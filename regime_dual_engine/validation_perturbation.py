"""
validation_perturbation.py — Parameter Perturbation

Regime thresholds:  60/40, 65/35 (baseline), 70/30
Breadth divergence lookback: 7, 10 (baseline), 13 days

9 backtests on variant C (HMM+Breadth, no dist overlay). Expected behavior:
  - no catastrophic collapse around baseline
  - smooth degradation
  - no isolated "magic parameter"
  - baseline competitive with neighbors
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

from config import Config
from dynamic_universe_backtest import build_rebalance_calendar
from src.backtest.engine import compute_metrics
from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.breadth_data import get_breadth_series
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data

warnings.filterwarnings("ignore")

THRESHOLDS = [(60.0, 40.0), (65.0, 35.0), (70.0, 30.0)]
LOOKBACKS = [7, 10, 13]


def main():
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 70)
    print("  PARAMETER PERTURBATION (variant C: HMM+Breadth)")
    print("=" * 70)
    all_data = load_data(end=end)
    breadth = get_breadth_series(end=end, verbose=False)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")

    results = {}
    for (bt, br) in THRESHOLDS:
        for lb in LOOKBACKS:
            name = f"thr{int(bt)}/{int(br)}_lb{lb}"
            dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                        enable_dist_day_overlay=False,
                                        bull_threshold=bt, bear_threshold=br,
                                        divergence_lookback=lb)
            cfg = Config()
            cfg.entry_mode = "setup"
            cfg.setup_enabled_types = ["breakout"]
            cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
            print(f"--- {name} ---", flush=True)
            eng = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                                     top_n=top_n, start=start, end=end,
                                     no_regime=False, benchmark="SPY", progress=False)
            summary = eng.run()
            m = compute_metrics(summary, cfg)
            results[name] = {
                "bull_threshold": bt, "bear_threshold": br, "lookback": lb,
                "return_pct": m["total_return_pct"], "sharpe": m["sharpe"],
                "max_dd_pct": m["max_drawdown_pct"], "pf": m["profit_factor"],
                "trades": m["total_trades"],
            }
            print(f"  Return={m['total_return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
                  f"MaxDD={m['max_drawdown_pct']:.2f}%  PF={m['profit_factor']}", flush=True)

    # surface table
    print("\n" + "=" * 70)
    print("  PERTURBATION SURFACE (Return% / Sharpe / MaxDD% / PF)")
    print("=" * 70)
    for lb in LOOKBACKS:
        row = f"lookback={lb:<3}  "
        for (bt, br) in THRESHOLDS:
            name = f"thr{int(bt)}/{int(br)}_lb{lb}"
            r = results[name]
            row += f"  {int(bt)}/{int(br)}: {r['return_pct']:+.1f}/{r['sharpe']:.2f}/{r['max_dd_pct']:.1f}  "
        print(row)

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_perturbation.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_perturbation.json")


if __name__ == "__main__":
    main()
