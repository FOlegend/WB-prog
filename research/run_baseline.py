"""
research/run_baseline.py — Deliverable 2: the reproducible baseline.

Re-runs the frozen end-to-end system over the known window and writes a
machine-readable baseline, together with everything needed to reproduce it:
the git commit, the full production config snapshot, the data window and a
content fingerprint of the OHLCV cache, and the exact command.

It also asserts the baseline still matches the frozen reference numbers; if it
does not, the task must stop and diagnose reproducibility first (spec §1).

Output: reports/research_baseline_2026-10-01.json

Run:
  python research/run_baseline.py
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import harness as H

OUT = os.path.join(_REPO_ROOT, "reports", "research_baseline_2026-10-01.json")

# Post-PIT baseline (2026-10-01). The pre-fix reference set
# (396 / 151 / 7.99 / 0.935 / -5.98 / 1.29 / 50.3) was leak-contaminated by the
# breadth look-ahead — see reports/pit_breadth_leak_2026-10-01.md — and is kept
# below only as an audit record.
REFERENCE = {
    "n_days": 396, "n_trades": 168, "return_pct": 5.57,
    "sharpe": 0.513, "max_dd_pct": -10.39, "profit_factor": 1.13,
    "win_rate_pct": 47.6,
}
SUPERSEDED_PRE_PIT_REFERENCE = {
    "n_days": 396, "n_trades": 151, "return_pct": 7.99,
    "sharpe": 0.935, "max_dd_pct": -5.98, "profit_factor": 1.29,
    "win_rate_pct": 50.3,
    "_status": "SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE",
}


def config_snapshot(cfg) -> dict:
    snap = {}
    for f in dataclasses.fields(cfg):
        v = getattr(cfg, f.name)
        if isinstance(v, (int, float, str, bool)) or v is None:
            snap[f.name] = v
    return snap


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--raw-out", default="",
                    help="also save the FULL backtest result (trade_log etc.) for "
                         "replays; e.g. reports/production_bt_pitcorrected_...json")
    ap.add_argument("--label", default="D2 — reproducible baseline")
    args = ap.parse_args()
    out_path = args.out or OUT

    cfg = H.load_config()
    print(f"=== Reproducible baseline {H.START} .. {H.END} ===")
    print(f"  git commit : {H.git_commit()['head_short']}")
    print(f"  cache dir  : {cfg.cache_dir}")

    fp = H.data_fingerprint(cfg)
    print(f"  data       : {fp['n_ticker_files']} ticker files; SPY "
          f"{fp['spy']['first']}..{fp['spy']['last']} rows={fp['spy']['rows']} "
          f"sha256={fp['spy']['sha256_16']}")

    res = H.run_backtest(cfg)
    result = res["result"]
    if "error" in result:
        print("ERROR:", result["error"])
        return 1
    dash = H.dashboard(result, cfg)
    if args.raw_out:
        H.save_json(args.raw_out, result)
        print(f"  raw backtest -> {args.raw_out}")

    merged = {**result["summary"],
              **{k: v for k, v in dash.items() if v is not None}}
    checks = {k: {"reference": v, "actual": merged.get(k), "ok": None}
              for k, v in REFERENCE.items()}
    for k, c in checks.items():
        actual = c["actual"]
        c["ok"] = (actual is not None and abs(float(actual) - c["reference"]) <= 0.02)

    payload = {
        "generated": "2026-10-01",
        "deliverable": args.label,
        "command": "python research/run_baseline.py",
        "harness": "production/backtest.py::ProductionBacktest (unchanged), "
                   "mode=legacy, run through research/harness.py",
        "pit_status": "point-in-time breadth contract ACTIVE "
                      "(regime_dual_engine/breadth_data.slice_to_end)",
        "window": {"start_arg": H.START, "end_arg": H.END,
                   "actual_start": result["summary"]["start"],
                   "actual_end": result["summary"]["end"],
                   "n_sessions": result["summary"]["n_days"]},
        "git": H.git_commit(),
        "data": fp,
        "config_snapshot": config_snapshot(cfg),
        "summary": result["summary"],
        "dashboard": dash,
        "reference_checks": checks,
        "all_reference_checks_ok": all(c["ok"] for c in checks.values()),
        "outputs": {"baseline_json": out_path, "raw_backtest": args.raw_out or None},
    }
    H.save_json(out_path, payload)

    print("\n=== frozen reference checks ===")
    for k, c in checks.items():
        print(f"  [{'OK ' if c['ok'] else 'NO '}] {k:16s} ref={c['reference']} "
              f"actual={c['actual']}")
    print(f"\n  all reference checks ok: {payload['all_reference_checks_ok']}")
    print(f"  return={dash['return_pct']}% cagr={dash['cagr_pct']}% "
          f"maxDD={dash['max_dd_pct']}% sharpe={dash['sharpe']} "
          f"sortino={dash['sortino']} calmar={dash['calmar']}")
    print(f"  avgR={dash['avg_r']} PF={dash['profit_factor']} "
          f"trades={dash['n_trades']} avg_exposure={dash['avg_exposure_pct']}%")
    print(f"\nreport -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
