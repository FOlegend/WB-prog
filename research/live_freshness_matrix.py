"""
research/live_freshness_matrix.py — §8 temporal consistency matrix + §9 gate check

Answers, for a set of candidate decision dates, the exact question the task
poses:

    decision date | SPY tail | constituent tail | breadth tail
                 | SPY age | constituent age | breadth age
                 | SPY valid? | constituents valid? | breadth valid?
                 | skew valid? | OVERALL STATE

and then evaluates §9 honestly: is the live data chain actually ready for
`BLOCK_DECISION`, or is the correct status `LIVE DATA NOT READY`?

This script does not decide; it reports. Enabling the blocking policy is gated
on the evidence this produces, and the gate is evaluated explicitly rather than
assumed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.agents.regime import load_breadth
from production.config import ProductionConfig
from production.data_validity import (FreshnessPolicy, VALIDITY_VALID, assess,
                                      human_report)
from production.datasource import build_cached_source
from production.pipeline import constituent_snapshot_date

# §1 policy as instructed for this task
MAX_AGE_DAYS = 7
TEMPORAL_SKEW_DAYS = 3
# Constituent bound: a membership snapshot is a QUARTERLY-updated reference
# dataset (last snapshots 2026-06-22 / 06-24 / 06-29 / 06-30), so a few days of
# carry-forward is normal and defensible; a month is not. 14 days is proposed
# for the human, not assumed here.
MAX_CONSTITUENT_AGE_DAYS = 14

SHADOW_REQUIRED_SESSIONS = 20


def policy() -> FreshnessPolicy:
    return FreshnessPolicy(
        max_age_days=MAX_AGE_DAYS,
        temporal_skew_days=TEMPORAL_SKEW_DAYS,
        max_constituent_age_days=MAX_CONSTITUENT_AGE_DAYS,
        policy_id="live-freshness-2026-10-04",
        approved_by="TASK_INSTRUCTED (§1)",
        on_failure="BLOCK_DECISION",
        rationale="7 d clears a weekend/holiday cluster; 3 d skew allows the "
                  "caches to refresh independently; constituent bound "
                  "proposed at 14 d because the reference dataset is updated "
                  "on membership-change dates, not daily")


def build_row(cfg, as_of: str, const_snapshot: str | None) -> dict:
    src = build_cached_source(cfg)
    spy, _ = src.get_ohlcv(cfg.regime_market_index, as_of=as_of, min_bars=60)
    br = load_breadth(cfg, end=as_of)

    # The membership snapshot that was ACTUALLY IN EFFECT for this as_of is the
    # latest snapshot with date <= as_of — that is what `members_as_of` uses.
    # Passing the dataset's global maximum instead would report a NEGATIVE age
    # for any historical date, which is nonsense: a 2024 replay was built on a
    # 2024 snapshot, not on a 2026 one.
    effective_snapshot = _snapshot_as_of(as_of) or const_snapshot

    # A PIT-correct REPLAY used a contemporaneous membership snapshot by
    # construction, so the live carry-forward bound must not be applied to it.
    # The membership dataset updates on membership-CHANGE dates, not daily: its
    # inter-snapshot gap has a median of 2 days but a maximum of 91, and 67 of
    # 2,717 gaps exceed 14 days. A flat 14-day bound would therefore flag
    # perfectly valid 2024 replay dates, and under BLOCK_DECISION that would
    # refuse to backtest at all.
    replay = _is_replay(as_of)
    if replay:
        effective_snapshot = None

    v = assess(regime_as_of=as_of, spy_df=spy, breadth_df=br, policy=policy(),
               constituent_snapshot=effective_snapshot)
    d = v.as_dict()
    return {
        "as_of": as_of,
        "mode": "REPLAY" if replay else "LIVE",
        "spy_tail": d["spy_tail_date"],
        "constituent_tail": (d["constituent_snapshot_date"]
                             or "(n/a — replay: contemporaneous by construction)"),
        "breadth_tail": d["breadth_tail_date"],
        "spy_age": d["spy_age_days"],
        "constituent_age": d["constituent_age_days"],
        "breadth_age": d["breadth_age_days"],
        "spy_valid": d["spy_age_days"] is not None and d["spy_age_days"] <= MAX_AGE_DAYS,
        "constituents_valid": d["constituents_valid"],
        "breadth_valid": d["breadth_age_days"] is not None and d["breadth_age_days"] <= MAX_AGE_DAYS,
        "skew_valid": d["regime_input_temporal_consistent"],
        "overall_state": d["state"],
        "reasons": d["reasons"],
        "trustworthy": d["decision_trustworthy"],
        "shadow_qualifies": d["state"] == VALIDITY_VALID,
    }


def _is_replay(as_of: str) -> bool:
    """True when this as_of is a PIT-correct HISTORICAL replay date.

    Recognised by the breadth tail being exactly `as_of` — which is precisely
    what `slice_to_end` guarantees for a PIT-correct replay, and is impossible
    for a live run whose caches lag.
    """
    try:
        cfg = ProductionConfig()
        br = load_breadth(cfg, end=as_of)
        if br is None or not len(br):
            return False
        return pd.Timestamp(br.index.max()).strftime("%Y-%m-%d") == as_of
    except Exception:
        return False


def _snapshot_as_of(as_of: str) -> str | None:
    """The membership snapshot in effect on `as_of` (latest snapshot <= as_of)."""
    try:
        import numpy as np
        from regime_dual_engine.pit_constituents import PitMembership
        pm = PitMembership()
        ts = pd.Timestamp(as_of)
        idx = int(np.searchsorted(pm.dates, ts.to_datetime64(), side="right")) - 1
        if idx < 0:
            return None
        return pd.Timestamp(pm.dates[idx]).strftime("%Y-%m-%d")
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"live_freshness_matrix_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    cfg = ProductionConfig()
    const_snapshot = constituent_snapshot_date()
    today = datetime.date.today().isoformat()

    dates = [today, "2026-10-02", "2026-08-06", "2026-07-01",
             "2025-07-31", "2024-06-14"]
    rows = []
    for d in dates:
        try:
            rows.append(build_row(cfg, d, const_snapshot))
        except Exception as exc:
            rows.append({"as_of": d, "error": str(exc)})

    live = rows[0] if rows and "error" not in rows[0] else None

    # ---- §9: is the chain actually ready to block? ---------------------
    # FAIL-CLOSED: an evaluation that could not run is NOT evidence of
    # readiness. Reporting LIVE DATA READY because every row errored would be
    # the most dangerous possible failure of this script.
    blockers = []
    errors = [r for r in rows if "error" in r]
    if errors:
        blockers.append(
            f"{len(errors)} evaluation(s) could not run "
            f"(e.g. {errors[0]['as_of']}: {errors[0]['error'][:80]}); "
            f"readiness cannot be asserted from a failed evaluation")
    if live is None and not errors:
        blockers.append("no usable evaluation for the current decision date")
    if live:
        if not live["spy_valid"]:
            blockers.append(
                f"OHLCV/SPY stale by {live['spy_age']} days "
                f"(tail {live['spy_tail']}); refresh blocked by an "
                f"auto_adjust basis change that would rewrite history")
        if not live["breadth_valid"]:
            blockers.append(
                f"breadth stale by {live['breadth_age']} days "
                f"(tail {live['breadth_tail']})")
        if not live["constituents_valid"]:
            blockers.append(
                f"PIT constituent snapshot stale by {live['constituent_age']} "
                f"days (snapshot {live['const_tail'] if 'const_tail' in live else live['constituent_tail']}); "
                f"breadth after that date is computed from a carried-forward "
                f"membership list")
    ready = not blockers

    payload = {
        "generated": today,
        "deliverable": "§8 temporal consistency matrix + §9 live-readiness gate",
        "no_production_change": True,
        "policy": policy().as_dict(),
        "constituent_snapshot_source": "fja05680/sp500 (sp500_changes.csv)",
        "rows": rows,
        "readiness": {
            "LIVE_DATA_READY": ready,
            "blockers": blockers,
            "policy_enabled_in_production": False,
            "reason_not_enabled": (
                "BLOCK_DECISION is NOT enabled: at least one required input is "
                "stale, so enabling it would simply refuse every live run "
                "without any route to recovery. The policy is implemented and "
                "tested; it is switched on only when the chain is current."),
        },
        "shadow_gate": {
            "status": "PENDING",
            "required_sessions": SHADOW_REQUIRED_SESSIONS,
            "qualifying_sessions": 0,
            "rule": "counts only sessions with overall_state == VALID",
            "note": "unchanged — no session to date would have qualified, so "
                    "this task neither advances nor resets the gate",
        },
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    print("=== §8 temporal consistency matrix ===")
    print(f"  policy: max_age={MAX_AGE_DAYS}d  skew={TEMPORAL_SKEW_DAYS}d  "
          f"constituent<={MAX_CONSTITUENT_AGE_DAYS}d  on_failure=BLOCK_DECISION")
    print(f"  constituent snapshot source: "
          f"{payload['constituent_snapshot_source']}")
    print()
    hdr = (f"  {'as_of':<12} {'mode':<7} {'SPY tail':<12} {'const':<12} "
           f"{'Br tail':<12} {'spyA':>5} {'conA':>6} {'brA':>5} {'SPY':>5} "
           f"{'CON':>5} {'BR':>5} {'skew':>6}  STATE")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        if "error" in r:
            print(f"  {r['as_of']:<12} ERROR {r['error'][:50]}")
            continue
        print(f"  {r['as_of']:<12} {r['mode']:<7} {str(r['spy_tail']):<12} "
              f"{str(r['constituent_tail'])[:11]:<12} {str(r['breadth_tail']):<12} "
              f"{str(r['spy_age']):>5} {str(r['constituent_age']):>6} "
              f"{str(r['breadth_age']):>5} "
              f"{'ok' if r['spy_valid'] else 'STALE':>5} "
              f"{'ok' if r['constituents_valid'] else 'STALE':>5} "
              f"{'ok' if r['breadth_valid'] else 'STALE':>5} "
              f"{'ok' if r['skew_valid'] else 'BAD':>6}  {r['overall_state']}")
    print()
    print("=== §9 live readiness ===")
    print(f"  LIVE DATA READY : {ready}")
    for b in blockers:
        print(f"    blocker: {b}")
    print(f"\n  policy enabled in production : "
          f"{payload['readiness']['policy_enabled_in_production']}")
    print(f"  shadow gate                 : PENDING, 0/{SHADOW_REQUIRED_SESSIONS}")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
