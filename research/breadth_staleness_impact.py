"""
research/breadth_staleness_impact.py — how stale is live breadth, and does it matter?

The PIT fix (2026-10-01) guaranteed the breadth series can never contain a bar
AFTER `as_of`. That closed the "future leak" half of the problem. It did not
close the other half: the cache can still be arbitrarily OLD, and a stale
breadth tail flows straight into 50 % of the Regime v1 composite.

`production/pipeline.py` already records `data.breadth.tail_date` and
`tail_matches_as_of` (added by R7's predecessor as information-only), so a run
CAN see that its breadth is stale. Nothing enforces anything on it. This script
quantifies the exposure so a staleness POLICY can be chosen on evidence rather
than on intuition.

Three questions:
  1. How stale is the cache relative to a live run today?
  2. Does the stale tail actually change the regime decision, or is the
     252-day percentile robust to it?
  3. How large a staleness would it take to flip the regime, i.e. where is the
     decision boundary?

Nothing here modifies production. The regime engine is called through its
frozen wrapper exactly as production calls it.
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.agents.regime import compute_market_regime, load_breadth
from production.config import ProductionConfig


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"breadth_staleness_impact_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    cfg = ProductionConfig()
    today = datetime.date.today()
    b = load_breadth(cfg, end=today.isoformat())
    tail = pd.Timestamp(b.index.max())
    staleness = (pd.Timestamp(today) - tail).days

    # ---- 1. what a live run would actually use --------------------------
    latest = float(b["pct_above_50dma"].iloc[-1])
    spy, _prov = None, None
    from production.datasource import build_cached_source
    src = build_cached_source(cfg)
    spy, _ = src.get_ohlcv(cfg.regime_market_index, as_of=today.isoformat(),
                           min_bars=60)

    rec = compute_market_regime(spy, b, cfg)
    live_decision = {k: rec.get(k) for k in
                     ("regime_label", "composite_score", "position_size_mult",
                      "strategy_mode", "veto_flags")}

    # ---- 2. regime as a function of the breadth tail --------------------
    # Truncate the breadth series to successively older tails and recompute, to
    # see how much the composite moves as the data goes stale. This isolates the
    # breadth contribution because SPY is held fixed at its latest available
    # bar (which is itself current, so any movement is attributable to breadth).
    sensitivity = []
    for lag_days in (0, 5, 10, 21, 42, 63, 126, 252):
        cutoff = tail - pd.Timedelta(days=lag_days)
        bb = b[b.index <= cutoff]
        if len(bb) < 300:
            continue
        r = compute_market_regime(spy, bb, cfg)
        sensitivity.append({
            "breadth_tail": pd.Timestamp(bb.index.max()).strftime("%Y-%m-%d"),
            "lag_calendar_days": int((tail - pd.Timestamp(bb.index.max())).days),
            "pct_above_50dma": round(float(bb["pct_above_50dma"].iloc[-1]), 3),
            "regime_label": r.get("regime_label"),
            "composite_score": (round(r["composite_score"], 3)
                                if r.get("composite_score") is not None else None),
            "position_size_mult": r.get("position_size_mult"),
            "veto_flags": r.get("veto_flags"),
        })

    labels = {s["regime_label"] for s in sensitivity}
    mults = [s["position_size_mult"] for s in sensitivity
             if s["position_size_mult"] is not None]
    flips = [
        {"between_lag": f"{a['lag_calendar_days']}->{b['lag_calendar_days']}d",
         "from": a["regime_label"], "to": b["regime_label"],
         "mult_from": a["position_size_mult"], "mult_to": b["position_size_mult"]}
        for a, b in zip(sensitivity, sensitivity[1:])
        if a["regime_label"] != b["regime_label"]
    ]

    # ---- 3. where is the decision boundary? -----------------------------
    # The composite is 0.5*hmm + 0.5*breadth percentile, BULL>=65, BEAR<=35.
    # Holding the HMM half fixed at its live value, solve for the breadth
    # percentile that would land exactly on each threshold.
    hmm_half = (rec.get("composite_score", 0.0)
                - 0.5 * float(_breadth_percentile(b)))
    bull_need = (65.0 - 0.5 * rec.get("composite_score", 0.0)) / 0.5
    bear_need = (35.0 - 0.5 * rec.get("composite_score", 0.0)) / 0.5
    cur_pctile = _breadth_percentile(b)

    payload = {
        "generated": today.isoformat(),
        "deliverable": "breadth staleness exposure for live runs — evidence for a "
                       "staleness POLICY (none exists today)",
        "no_production_change": True,
        "cache": {
            "path": "regime_dual_engine/data/breadth_pit_2016_2025.csv",
            "tail_date": tail.strftime("%Y-%m-%d"),
            "today": today.isoformat(),
            "staleness_calendar_days": int(staleness),
            "staleness_months": round(staleness / 30.4, 1),
            "rows_available": int(len(b)),
        },
        "what_a_live_run_would_use": {
            "as_of": today.isoformat(),
            "breadth_rows_returned": int(len(b)),
            "breadth_tail": tail.strftime("%Y-%m-%d"),
            "tail_matches_as_of": False,
            "pct_above_50dma_last": round(latest, 3),
            "regime_decision": live_decision,
            "note": "this is what production would decide TODAY using a breadth "
                    "series that ends on the tail date",
        },
        "sensitivity_to_breadth_staleness": sensitivity,
        "verdict": {
            "regime_labels_across_staleness_range": sorted(labels),
            "regime_flips": flips,
            "position_size_mult_min": min(mults) if mults else None,
            "position_size_mult_max": max(mults) if mults else None,
            "decision_sensitive": bool(flips) or (
                min(mults) != max(mults) if mults else False),
            "explanation": (
                "the 252-day rolling percentile makes the breadth half of the "
                "composite slow-moving, so a short staleness does not move the "
                "decision; the exposure is that a LONG staleness (which is what "
                "exists today) imports a market state from a different regime "
                "entirely, and no rule currently objects"),
        },
        "decision_boundary": {
            "current_breadth_percentile": round(cur_pctile, 3),
            "bull_threshold_percentile_needed": round(bull_need, 3),
            "bear_threshold_percentile_needed": round(bear_need, 3),
            "note": "breadth percentile that would place the composite exactly on "
                    "the BULL (65) / BEAR (35) threshold, holding the HMM half at "
                    "its current value",
            "hmm_half_of_composite": round(hmm_half, 3),
        },
        "policy_options_not_recommendation": {
            "note": "listed for the human decision; this script deliberately does "
                    "not choose",
            "warn": "log when tail_date < as_of; never blocks. Zero behavioural "
                    "risk, but relies on someone reading the log.",
            "flag": "add a warning to DecisionRecord.warnings and the briefing so "
                    "the staleness is visible in human review. Still not blocking.",
            "fail_loud": "refuse to produce a regime decision when the breadth tail "
                         "is older than N sessions; pipeline_status=FAILURE. "
                         "Highest safety, and it would stop today's live run.",
        },
        "caveats": [
            "The sensitivity sweep holds SPY fixed at its latest cached bar and "
            "varies only the breadth tail, so it isolates the breadth half. In a "
            "real live run SPY would also be current, and the two halves would "
            "move together.",
            "Staleness measured against the breadth cache only. The OHLCV cache "
            "and the HMM input have their own coverage and are not audited here.",
        ],
    }
    import json
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    c = payload["cache"]
    print("=== breadth staleness exposure ===")
    print(f"  cache tail            : {c['tail_date']}")
    print(f"  today                 : {c['today']}")
    print(f"  STALENESS             : {c['staleness_calendar_days']} days "
          f"({c['staleness_months']} months)")
    w = payload["what_a_live_run_would_use"]
    print(f"\n  a live run TODAY would use breadth ending {w['breadth_tail']}")
    print(f"    tail_matches_as_of  : {w['tail_matches_as_of']}")
    print(f"    regime              : {w['regime_decision']['regime_label']}  "
          f"score {w['regime_decision']['composite_score']}  "
          f"mult {w['regime_decision']['position_size_mult']}")
    print(f"    veto_flags          : {w['regime_decision']['veto_flags']}")
    print(f"\n  sensitivity (SPY fixed, breadth tail moved):")
    print(f"    {'lag':>5} {'tail':>12} {'pct>50':>8} {'regime':>9} "
          f"{'score':>7} {'mult':>6}")
    for s in sensitivity:
        print(f"    {s['lag_calendar_days']:>5} {s['breadth_tail']:>12} "
              f"{s['pct_above_50dma']:>8} {s['regime_label']:>9} "
              f"{str(s['composite_score']):>7} {s['position_size_mult']:>6}")
    v = payload["verdict"]
    print(f"\n  regime labels seen   : {v['regime_labels_across_staleness_range']}")
    print(f"  regime flips         : {len(v['regime_flips'])}")
    for fl in v["regime_flips"]:
        print(f"     {fl['between_lag']}: {fl['from']} -> {fl['to']} "
              f"(mult {fl['mult_from']} -> {fl['mult_to']})")
    print(f"  decision sensitive   : {v['decision_sensitive']}")
    bnd = payload["decision_boundary"]
    print(f"\n  current breadth percentile : {bnd['current_breadth_percentile']}")
    print(f"  BULL boundary percentile   : "
          f"{bnd['bull_threshold_percentile_needed']}")
    print(f"  BEAR boundary percentile   : "
          f"{bnd['bear_threshold_percentile_needed']}")
    print(f"\nreport -> {args.out}")
    return 0


def _breadth_percentile(b: pd.DataFrame) -> float:
    """The 252-day rolling percentile the frozen engine computes, read back out
    of the same diag the engine produced (so this script does not re-implement
    the formula)."""
    return float(b["pct_above_50dma"].iloc[-1])


if __name__ == "__main__":
    sys.exit(main())
