"""
ablation_pit.py — Reviewer Task 4: 4-Way Ablation with PIT Breadth

Same 4 variants as backtest_harness.py but with the POINT-IN-TIME breadth:
  A. HMM only              (hmm_weight=1.0, breadth_weight=0.0)
  B. Breadth only          (hmm_weight=0.0, breadth_weight=1.0)
  C. HMM + Breadth         (0.5 / 0.5, overlay off)
  D. HMM + Breadth + Dist  (0.5 / 0.5, overlay on)

Regime layer evaluated as a RISK overlay (exposure, MaxDD, regime dist) AND
for return generation. Results are compared to the current-constituent
ablation (reports/dual_engine_ablation.json).
"""
from __future__ import annotations

import os
import sys
import json
import warnings

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

warnings.filterwarnings("ignore")

from config import Config
from dynamic_universe_backtest import build_rebalance_calendar, _exposure_pct
from src.backtest.engine import compute_metrics

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.backtest_harness import (DualEngineBacktest, load_data,
                                                 _regime_distribution, _veto_distribution)
from regime_dual_engine.pit_breadth_data import get_breadth as get_pit_breadth

ABLATION = {
    "A. HMM only":   dict(hmm_weight=1.0, breadth_weight=0.0, enable_dist_day_overlay=False),
    "B. Breadth only": dict(hmm_weight=0.0, breadth_weight=1.0, enable_dist_day_overlay=False),
    "C. HMM+Breadth": dict(hmm_weight=0.5, breadth_weight=0.5, enable_dist_day_overlay=False),
    "D. +DistDays":  dict(hmm_weight=0.5, breadth_weight=0.5, enable_dist_day_overlay=True),
}


def run_ablation(breadth, start="2018-01-01", end="2025-07-31", top_n=20, verbose=True):
    all_data = load_data(end=end)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"  {len(all_data)} tickers, {len(buckets)} buckets, "
          f"breadth {len(breadth)} days")

    results = {}
    for name, kw in ABLATION.items():
        if verbose:
            print(f"\n--- {name} ---", flush=True)
        dual_cfg = DualEngineConfig(**kw)
        cfg = Config()
        cfg.entry_mode = "setup"
        cfg.setup_enabled_types = ["breakout"]
        cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
        eng = DualEngineBacktest(cfg, all_data, all_rd, buckets,
                                 dual_cfg, breadth, top_n=top_n,
                                 start=start, end=end, no_regime=False,
                                 benchmark="SPY", progress=verbose)
        summary = eng.run()
        metrics = compute_metrics(summary, cfg)
        metrics["exposure_pct"] = _exposure_pct(summary["equity_curve"])
        metrics["regime_dist"] = _regime_distribution(summary)
        metrics["veto_dist"] = _veto_distribution(summary)
        results[name] = {"metrics": metrics}
        m = metrics
        if verbose:
            print(f"  >> Return={m['total_return_pct']:+.2f}%  "
                  f"Sharpe={m['sharpe']:.2f}  MaxDD={m['max_drawdown_pct']:.2f}%  "
                  f"PF={m['profit_factor']}  Trades={m['total_trades']}  "
                  f"Exp={metrics['exposure_pct']}%", flush=True)
    return results


def compare_with_current(pit_results):
    cur_path = os.path.join(_REPO_ROOT, "reports", "dual_engine_ablation.json")
    if not os.path.exists(cur_path):
        print("  (no current-breadth ablation file to compare)")
        return {}
    with open(cur_path) as f:
        cur = json.load(f)
    out = {}
    for name in ABLATION:
        if name not in cur or name not in pit_results:
            continue
        cm = cur[name]["metrics"]
        pm = pit_results[name]["metrics"]
        out[name] = {
            "current": {"return_pct": cm["total_return_pct"], "sharpe": cm["sharpe"],
                        "max_dd_pct": cm["max_drawdown_pct"], "pf": cm["profit_factor"],
                        "exposure_pct": cm["exposure_pct"]},
            "pit": {"return_pct": pm["total_return_pct"], "sharpe": pm["sharpe"],
                    "max_dd_pct": pm["max_drawdown_pct"], "pf": pm["profit_factor"],
                    "exposure_pct": pm["exposure_pct"]},
            "delta_return_pp": round(pm["total_return_pct"] - cm["total_return_pct"], 2),
            "delta_sharpe": round(pm["sharpe"] - cm["sharpe"], 3),
            "delta_maxdd_pp": round(pm["max_drawdown_pct"] - cm["max_drawdown_pct"], 2),
        }
    return out


def print_table(results, title):
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)
    keys = [("Return%", "total_return_pct", "+.2f"),
            ("Sharpe", "sharpe", ".2f"),
            ("MaxDD%", "max_drawdown_pct", ".2f"),
            ("PF", "profit_factor", ""),
            ("Win%", "win_rate", ""),
            ("Trades", "total_trades", "d"),
            ("Exp%", "exposure_pct", ".1f")]
    hdr = "".join(f"{n:>10}" for n, _, _ in keys)
    print(f"{'Variant':<16}{hdr}")
    print("-" * (16 + 10 * len(keys)))
    for name, res in results.items():
        m = res["metrics"]
        row = ""
        for _, k, fmt in keys:
            v = m[k]
            if k == "win_rate":
                row += f"{v*100:>10.1f}"
            elif k == "profit_factor":
                row += f"{str(v):>10}"
            elif fmt == "+.2f":
                row += f"{v:>+10.2f}"
            elif fmt == ".2f":
                row += f"{v:>10.2f}"
            elif fmt == "d":
                row += f"{v:>10d}"
            else:
                row += f"{v:>10.1f}"
        print(f"{name:<16}{row}")
    print("\n  Regime distribution:")
    for name, res in results.items():
        print(f"    {name:<16} {res['metrics']['regime_dist']}")
    print("\n  Veto distribution:")
    for name, res in results.items():
        print(f"    {name:<16} {res['metrics']['veto_dist']}")


def main():
    print("Building breadth series...", flush=True)
    from regime_dual_engine.pit_breadth_data import get_breadth
    pit = get_breadth("pit", rebuild=False)
    cur = get_breadth("current", rebuild=False)
    print(f"  PIT breadth: {len(pit)} days "
          f"({pit.index.min().date()} -> {pit.index.max().date()})")
    print(f"  Current-constituent breadth: {len(cur)} days "
          f"({cur.index.min().date()} -> {cur.index.max().date()})")

    results_pit = run_ablation(pit)
    print_table(results_pit, "ABLATION TABLE — PIT breadth (dynamic universe top20, 2018-2025)")
    results_cur = run_ablation(cur)
    print_table(results_cur, "ABLATION TABLE — Current-constituent breadth (same construction)")

    # clean apples-to-apples comparison (same construction, only membership differs)
    cmp = {}
    for name in ABLATION:
        if name not in results_cur or name not in results_pit:
            continue
        cm = results_cur[name]["metrics"]
        pm = results_pit[name]["metrics"]
        cmp[name] = {
            "current": {"return_pct": cm["total_return_pct"], "sharpe": cm["sharpe"],
                        "max_dd_pct": cm["max_drawdown_pct"], "pf": cm["profit_factor"],
                        "exposure_pct": cm["exposure_pct"]},
            "pit": {"return_pct": pm["total_return_pct"], "sharpe": pm["sharpe"],
                    "max_dd_pct": pm["max_drawdown_pct"], "pf": pm["profit_factor"],
                    "exposure_pct": pm["exposure_pct"]},
            "delta_return_pp": round(pm["total_return_pct"] - cm["total_return_pct"], 2),
            "delta_sharpe": round(pm["sharpe"] - cm["sharpe"], 3),
            "delta_maxdd_pp": round(pm["max_drawdown_pct"] - cm["max_drawdown_pct"], 2),
        }
    print("\n  CURRENT vs PIT breadth (Δ return pp / Δ Sharpe / Δ MaxDD pp):")
    for name, d in cmp.items():
        print(f"    {name:<16} {d['delta_return_pp']:+.2f} / "
              f"{d['delta_sharpe']:+.3f} / {d['delta_maxdd_pp']:+.2f}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_ablation_pit.json"), "w") as f:
        json.dump({"results": results_pit, "results_current": results_cur,
                   "vs_current": cmp}, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_ablation_pit.json")


if __name__ == "__main__":
    main()
