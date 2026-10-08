"""
research/run_tp_grid.py — R1: exit-target (take-profit) controlled experiment.

Hypothesis (spec §17 R1): the frozen take-profit (2.5 ATR) closes winners early
— realised TP fills at ~1.667R while the winners' mean MFE is ~2.25R — so a wider
target may capture more upside.

Design (spec §12-14)
--------------------
* ONE variable changes: `take_profit_atr_mult`. Every other parameter, the
  universe, the data, the costs and the execution semantics are frozen.
* Pre-registered grid: {2.0, 2.5, 3.0, 3.5, 4.0} ATR. 2.5 is the baseline; the
  neighbours exist to distinguish a SMOOTH response from a sharp optimisation peak
  (spec §14). Nothing outside this grid is searched.
* No look-ahead: the backtest replays `run_daily` day by day on point-in-time
  data; only the target geometry changes.
* Reported: the full §15 dashboard, subperiod split, exit-reason distribution,
  MFE captured, and a trade-count / turnover check.

Output: reports/research_tp_grid_2026-10-01.json

Run:
  python research/run_tp_grid.py
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import harness as H

OUT = os.path.join(_REPO_ROOT, "reports", "research_tp_grid_2026-10-01.json")
GRID = [2.0, 2.5, 3.0, 3.5, 4.0]
BASELINE_TP = 2.5
SUBPERIODS = {
    "2024_full": ("2024-01-01", "2024-12-31"),
    "2025_to_Jul": ("2025-01-01", "2025-07-31"),
    "2024_H2": ("2024-07-01", "2024-12-31"),
    "2025_H1": ("2025-01-01", "2025-06-30"),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default="", help="comma list, e.g. 4.5,5.0")
    ap.add_argument("--out", default="")
    ap.add_argument("--label", default="D4 — R1 exit-target experiment")
    args = ap.parse_args()
    grid = ([float(x) for x in args.grid.split(",") if x.strip()]
            if args.grid else GRID)
    out_path = args.out or OUT

    variants = {}
    for tp in grid:
        print(f"=== TP = {tp} ATR "
              f"({'BASELINE' if tp == BASELINE_TP else 'candidate'}) ===")
        cfg = H.load_config(take_profit_atr_mult=tp)
        res = H.run_backtest(cfg)
        result = res["result"]
        if "error" in result:
            print("ERROR:", result["error"])
            return 1
        dash = H.dashboard(result, cfg)
        sub = H.subperiods(result, SUBPERIODS)
        variants[str(tp)] = {"take_profit_atr_mult": tp, "dashboard": dash,
                             "subperiods": sub}
        print(f"  return={dash['return_pct']}% maxDD={dash['max_dd_pct']}% "
              f"sharpe={dash['sharpe']} sortino={dash['sortino']} "
              f"calmar={dash['calmar']} avgR={dash['avg_r']} PF={dash['profit_factor']} "
              f"trades={dash['n_trades']} exposure={dash['avg_exposure_pct']}%")

    base_dash = variants.get(str(BASELINE_TP), {}).get("dashboard")
    if base_dash is None and os.path.exists(OUT):
        import json
        with open(OUT, encoding="utf-8") as fh:
            base_dash = (json.load(fh)["variants"].get(str(BASELINE_TP), {})
                         .get("dashboard"))
    for k, v in variants.items():
        if base_dash:
            v["vs_baseline"] = H.compare_to_baseline(v["dashboard"], base_dash)

    # robustness read: is the response smooth or a single lucky peak?
    ret = [variants[str(tp)]["dashboard"]["return_pct"] for tp in grid]
    sharpe = [variants[str(tp)]["dashboard"]["sharpe"] for tp in grid]
    robust = {
        "grid": grid,
        "return_pct_by_tp": dict(zip(map(str, grid), ret)),
        "sharpe_by_tp": dict(zip(map(str, grid), sharpe)),
        "note": ("a smooth, single-peaked response that is not dominated by one "
                 "subperiod is weak evidence of a real effect; a sharp isolated "
                 "spike is likelier to be noise/overfitting"),
    }

    payload = {
        "generated": "2026-10-01",
        "deliverable": args.label,
        "hypothesis": ("frozen take-profit (2.5 ATR) truncates winners; a wider "
                       "target raises captured upside without harming risk-adjusted "
                       "performance"),
        "changing_variable": "take_profit_atr_mult",
        "unchanged": "everything else (universe, data, costs, execution, all other "
                     "parameters); bucket screen reused from the PIT cache",
        "baseline_tp": BASELINE_TP,
        "grid": grid,
        "git": H.git_commit(),
        "variants": variants,
        "robustness": robust,
    }
    H.save_json(out_path, payload)

    print("\n=== comparison vs baseline (TP 2.5) ===")
    hdr = f"{'TP':>4} {'ret%':>7} {'maxDD%':>7} {'sharpe':>7} {'sortino':>8} " \
          f"{'calmar':>7} {'avgR':>7} {'PF':>5} {'trades':>6} {'expo%':>6}"
    print(hdr)
    for tp in grid:
        d = variants[str(tp)]["dashboard"]
        print(f"{tp:>4} {d['return_pct']:>7} {d['max_dd_pct']:>7} {d['sharpe']:>7} "
              f"{str(d['sortino']):>8} {str(d['calmar']):>7} {str(d['avg_r']):>7} "
              f"{str(d['profit_factor']):>5} {d['n_trades']:>6} "
              f"{str(d['avg_exposure_pct']):>6}")
    print(f"\nreport -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
