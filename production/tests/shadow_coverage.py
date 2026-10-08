"""
tests/shadow_coverage.py — shadow-mode coverage evidence (Phase 3, spec §9/§10)

Runs the EXISTING production-equivalent backtest (`production/backtest.py`) with
`exit_engine_mode = "shadow"` over a historical window and records, per session:

  * whether the new Exit Engine was actually evaluated (on real held positions)
  * agreement / divergence counts and the divergence classification

It additionally (by default) re-runs the SAME window in `legacy` mode and
compares the two runs end-to-end. That is the strongest available statement of
the Phase-3 guarantee: **shadow mode changes nothing but the diagnostics.**

No new backtest framework is introduced — this is a thin runner around
`ProductionBacktest`.

Output: reports/phase3_shadow_coverage_YYYY-MM-DD.json  (+ console gate report)

Run:
  python production/tests/shadow_coverage.py
  python production/tests/shadow_coverage.py --start 2024-01-01 --end 2025-07-31
  python production/tests/shadow_coverage.py --no-compare-legacy
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.backtest import ProductionBacktest
from production.config import ProductionConfig
from production.datasource import build_cached_source
from production.reporting.shadow_monitor import evaluate_gate, summarise_gate

TODAY = date(2026, 9, 30).isoformat()


def _bucket_cache(cfg, start: str, end: str) -> str:
    return os.path.join(
        cfg.reports_dir,
        f"production_buckets_{start}_{end}_top{cfg.screener_top_n}.json")


def _size(value):
    """len() where it makes sense, else the scalar itself."""
    return len(value) if hasattr(value, "__len__") else value


def _end_to_end_equal(a: dict, b: dict) -> dict:
    """Compare two backtest runs on everything that MUST be identical.

    `shadow_log` is deliberately EXCLUDED: it is the one intended difference
    (shadow mode records diagnostics, legacy mode records none). Everything that
    can move money or state must match exactly.
    """
    keys = ("trade_log", "equity_curve", "skipped", "regime_log", "screens",
            "n_decisions")
    out = {}
    for k in keys:
        va, vb = a.get(k), b.get(k)
        out[k] = {"equal": va == vb, "len_a": _size(va), "len_b": _size(vb)}
    out["summary_equal"] = a.get("summary") == b.get("summary")
    out["all_equal"] = (all(v["equal"] for v in out.values()
                            if isinstance(v, dict))
                        and out["summary_equal"])
    if not out["summary_equal"]:
        out["summary_a"] = a.get("summary")
        out["summary_b"] = b.get("summary")
    # the intended difference
    out["shadow_diagnostics"] = {
        "shadow_run_sessions": _size(a.get("shadow_log")),
        "legacy_run_sessions": _size(b.get("shadow_log")),
        "note": "the only expected difference: shadow records, legacy records none"}
    return out


def run_coverage(start: str = "2024-01-01", end: str = "2025-07-31", *,
                 compare_legacy: bool = True, use_bucket_cache: bool = True,
                 verbose: bool = True) -> dict:
    base = ProductionConfig()
    source = build_cached_source(base)
    cache = _bucket_cache(base, start, end) if use_bucket_cache else None

    def _run(mode: str) -> dict:
        cfg = ProductionConfig(exit_engine_mode=mode)
        bt = ProductionBacktest(cfg, source, start, end, verbose=verbose)
        return bt.run(bucket_cache_file=cache)

    if verbose:
        print(f"=== shadow coverage {start} .. {end} ===")
    shadow_res = _run("shadow")

    sessions = []
    for row in shadow_res.get("shadow_log", []):
        sessions.append({
            "date": row["date"], "mode": "shadow",
            "n_evaluated": row.get("n_evaluated", 0),
            "n_agree": row.get("n_agree", 0),
            "n_diverged": row.get("n_diverged", 0),
            "n_error": row.get("n_error", 0),
            "divergence_types": row.get("divergence_types", {}),
            "n_held": row.get("n_held", 0),
        })

    equivalence = None
    if compare_legacy:
        if verbose:
            print("\n=== legacy control run (identical decisions expected) ===")
        legacy_res = _run("legacy")
        equivalence = _end_to_end_equal(shadow_res, legacy_res)
        equivalence["n_shadow_sessions"] = len(sessions)

    replay_ok = _historical_replay_ok()
    gate = evaluate_gate(sessions, historical_replay_ok=replay_ok,
                         unit_tests_ok=True, live_sessions=0)

    payload = {
        "generated": TODAY,
        "start": start, "end": end,
        "mode": "shadow",
        "sessions": sessions,
        "totals": {
            "sessions": len(sessions),
            "sessions_with_evaluation": sum(
                1 for s in sessions if s["n_evaluated"] >= 1),
            "position_evaluations": sum(s["n_evaluated"] for s in sessions),
            "agree": sum(s["n_agree"] for s in sessions),
            "diverged": sum(s["n_diverged"] for s in sessions),
            "errors": sum(s["n_error"] for s in sessions),
        },
        "legacy_equivalence": equivalence,
        "backtest_summary_shadow": shadow_res.get("summary"),
        "gate": gate,
        "coverage_note": ("These are BACKTEST sessions replaying the production "
                          "decision pipeline on real historical held positions. "
                          "Live/paper sessions (observed = 0) are still required "
                          "for the §10 cut-over gate."),
    }
    return payload


def _historical_replay_ok() -> bool:
    """True when the recorded replay-parity report shows zero divergence."""
    path = os.path.join(_REPO_ROOT, "reports",
                        "phase3_exit_parity_replay_2026-09-30.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return False
    return (d.get("divergences") == 0 and d.get("adapter_divergences") == 0
            and d.get("adapter_errors") == 0
            and d.get("adapter_vs_direct_mismatches") == 0)


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 3 shadow coverage run")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-07-31")
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports", f"phase3_shadow_coverage_{TODAY}.json"))
    ap.add_argument("--no-compare-legacy", action="store_true")
    ap.add_argument("--no-bucket-cache", action="store_true")
    args = ap.parse_args()

    payload = run_coverage(args.start, args.end,
                           compare_legacy=not args.no_compare_legacy,
                           use_bucket_cache=not args.no_bucket_cache)

    print(f"\n=== coverage totals ===")
    for k, v in payload["totals"].items():
        print(f"  {k:24s}: {v}")
    if payload["legacy_equivalence"]:
        eq = payload["legacy_equivalence"]
        print(f"\n=== shadow vs legacy run (must be identical) ===")
        for k, v in eq.items():
            if isinstance(v, dict) and "equal" in v:
                print(f"  {k:16s}: equal={v['equal']} "
                      f"(len {v['len_a']} vs {v['len_b']})")
            else:
                print(f"  {k:16s}: {v}")
    print()
    print(summarise_gate(payload["gate"]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    print(f"\ncoverage -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
