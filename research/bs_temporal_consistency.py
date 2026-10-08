"""
research/bs_temporal_consistency.py — BS §12 diagnostic + §16 shadow-gate rule

Two deliverables, both read-only:

A. A temporal-consistency diagnostic that prints, for a given as_of, the exact
   triple the task asks for:

       as_of / SPY tail / breadth tail / SPY age / breadth age
       temporal_consistent = TRUE|FALSE

   plus the validity state under the PROPOSED (not yet approved) policy, so the
   human can see what the policy would do before approving it.

B. The shadow-qualification rule (task §16). A live shadow session may only count
   toward the 20-session gate if the regime input was valid. This module defines
   that predicate; it does not run the gate, it states the rule and reports the
   current count.

Nothing here changes production. The policy thresholds are passed in explicitly and
default to the PROPOSED values, clearly labelled as unapproved.
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

from production.agents.regime import load_breadth
from production.config import ProductionConfig
from production.data_validity import (FreshnessPolicy, VALIDITY_VALID, assess,
                                      human_report)
from production.datasource import build_cached_source

# PROPOSED thresholds (task §7 — awaiting human approval; NOT active in config)
PROPOSED_MAX_AGE_DAYS = 7
PROPOSED_TEMPORAL_SKEW_DAYS = 3
PROPOSED_ON_FAILURE = "RECORD_ONLY"

SHADOW_QUALIFYING_MIN_SESSIONS = 20


def temporal_row(cfg, as_of: str) -> dict:
    """One row of the §12 table: the effective dates and ages of both inputs."""
    src = build_cached_source(cfg)
    spy, _ = src.get_ohlcv(cfg.regime_market_index, as_of=as_of, min_bars=60)
    br = load_breadth(cfg, end=as_of)
    return {"as_of": as_of, "spy_df": spy, "breadth_df": br}


def shadow_qualifies(validity_state: str) -> bool:
    """Task §16: only a data-VALID session may count toward the shadow gate.

    Encoded as a single predicate so the gate and the diagnostic cannot drift
    apart. A session with STALE / TEMPORALLY_INCONSISTENT / MISSING / INVALID
    inputs is NON_QUALIFYING_DATA_INVALID.
    """
    return validity_state == VALIDITY_VALID


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=datetime.date.today().isoformat())
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"bs_temporal_consistency_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    cfg = ProductionConfig()
    policy = FreshnessPolicy(
        max_age_days=PROPOSED_MAX_AGE_DAYS,
        temporal_skew_days=PROPOSED_TEMPORAL_SKEW_DAYS,
        policy_id="bs5-proposed-2026-10-04",
        approved_by="PENDING_HUMAN_APPROVAL",
        on_failure=PROPOSED_ON_FAILURE,
        rationale="7 d clears a normal weekend/holiday cluster and one missed "
                  "manual refresh; 3 d allows the two caches to refresh "
                  "independently while still catching a real split")

    rows = []
    # a spread of dates: current, the two input tails, and historical dates
    for as_of in (args.as_of, "2026-08-06", "2025-07-31", "2024-06-14"):
        try:
            r = temporal_row(cfg, as_of)
        except Exception as exc:
            rows.append({"as_of": as_of, "error": str(exc)})
            continue
        v = assess(regime_as_of=as_of, spy_df=r["spy_df"],
                   breadth_df=r["breadth_df"], policy=policy)
        rows.append({
            "as_of": as_of,
            "spy_tail_date": v.spy.tail_date,
            "breadth_tail_date": v.breadth.tail_date,
            "spy_age_days": v.spy_age_days,
            "breadth_age_days": v.breadth_age_days,
            "spy_matches_as_of": v.spy_matches_as_of,
            "breadth_matches_as_of": v.breadth_matches_as_of,
            "temporal_consistent": v.regime_input_temporal_consistent,
            "freshness_valid": v.regime_input_freshness_valid,
            "state": v.state,
            "reasons": list(v.reasons),
            "decision_trustworthy": v.decision_trustworthy,
            "shadow_session_qualifies": shadow_qualifies(v.state),
        })

    payload = {
        "generated": datetime.date.today().isoformat(),
        "deliverable": "BS-§12 temporal-consistency diagnostic + §16 shadow rule",
        "no_production_change": True,
        "policy_used": policy.as_dict(),
        "policy_status": "PROPOSED — thresholds NOT active in ProductionConfig",
        "rows": rows,
        "shadow_gate": {
            "rule": "a live shadow session counts toward the 20-session gate only "
                    "if regime input validity == VALID",
            "predicate": "production.data_validity.state == 'VALID'",
            "non_qualifying_label": "NON_QUALIFYING_DATA_INVALID",
            "required_sessions": SHADOW_QUALIFYING_MIN_SESSIONS,
            "observed_qualifying_sessions": 0,
            "current_status": "PENDING — 0 sessions observed to date",
            "note": "the pre-existing 0-session pending state is UNAFFECTED: "
                    "with the proposed policy, every session recorded so far "
                    "would have been non-qualifying anyway, so this task does "
                    "not reset or advance the gate",
        },
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    print("=== BS-§12 temporal consistency (proposed policy, NOT active) ===")
    print(f"  policy: max_age={PROPOSED_MAX_AGE_DAYS}d  "
          f"skew={PROPOSED_TEMPORAL_SKEW_DAYS}d  "
          f"on_failure={PROPOSED_ON_FAILURE}")
    print()
    hdr = (f"  {'as_of':<12} {'SPY tail':<12} {'Br tail':<12} {'spyA':>5} "
           f"{'brA':>5} {'consist':>8} {'fresh':>7} {'state':>24} {'qualify':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in rows:
        if "error" in r:
            print(f"  {r['as_of']:<12} ERROR: {r['error'][:40]}")
            continue
        print(f"  {r['as_of']:<12} {str(r['spy_tail_date']):<12} "
              f"{str(r['breadth_tail_date']):<12} {str(r['spy_age_days']):>5} "
              f"{str(r['breadth_age_days']):>5} "
              f"{str(r['temporal_consistent']):>8} "
              f"{str(r['freshness_valid']):>7} {r['state']:>24} "
              f"{str(r['shadow_session_qualifies']):>8}")
    print()
    print("  §16 shadow gate: 0 qualifying sessions observed; the pending state "
          "is unchanged.")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
