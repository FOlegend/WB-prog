"""
robustness_pit.py — Reviewer Task 5: Robustness on PIT breadth

  * Walk-forward OOS: yearly segments of the conditioned strategy (variant C
    on PIT breadth) — consistency of return / MaxDD / exposure per year.
  * Threshold perturbation: 60/40, 65/35, 70/30 (variant C, PIT breadth).
  * Breadth lookback perturbation: divergence_lookback 7 / 10 / 13 days.

All calculations point-in-time (rolling breadth percentile + refit HMM +
fixed thresholds). No parameter is tuned on the final OOS set — the grid is
pre-registered exactly as the reviewer specified.
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
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data
from regime_dual_engine.pit_breadth_data import get_breadth as get_pit_breadth


def _metrics(summary, cfg):
    m = compute_metrics(summary, cfg)
    return {"return_pct": m["total_return_pct"], "sharpe": m["sharpe"],
            "max_dd_pct": m["max_drawdown_pct"], "pf": m["profit_factor"],
            "trades": m["total_trades"], "exposure_pct": _exposure_pct(summary["equity_curve"])}


def _run(dual_cfg, all_data, all_rd, buckets, breadth, start, end, top_n=20):
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = ["breakout"]
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
    eng = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                             top_n=top_n, start=start, end=end,
                             no_regime=False, benchmark="SPY", progress=False)
    summary = eng.run()
    return summary, _metrics(summary, cfg)


def _yearly(summary):
    eq = pd.DataFrame(summary["equity_curve"])
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    out = {}
    for y, sub in eq.groupby("year"):
        if len(sub) < 5:
            continue
        ret = (sub["equity"].iloc[-1] / sub["equity"].iloc[0] - 1) * 100
        dd = ((sub["equity"] / sub["equity"].cummax() - 1) * 100).min()
        out[int(y)] = {"return_pct": round(float(ret), 2), "max_dd_pct": round(float(dd), 2)}
    return out


def main():
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 72)
    print("  REVIEWER TASK 5 — ROBUSTNESS (PIT breadth)")
    print("=" * 72)
    breadth = get_pit_breadth("pit", rebuild=False)
    all_data = load_data(end=end)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")

    results = {"walkforward_yearly": {}, "threshold_perturbation": {},
               "breadth_lookback_perturbation": {}}

    # ---- baseline variant C (PIT) ----
    print("\n--- baseline C: HMM+Breadth (PIT) ---", flush=True)
    base_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                enable_dist_day_overlay=False)
    summary, m = _run(base_cfg, all_data, all_rd, buckets, breadth, start, end)
    results["baseline_C"] = m
    results["walkforward_yearly"] = _yearly(summary)
    print(f"  Return={m['return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
          f"MaxDD={m['max_dd_pct']:.2f}%  PF={m['pf']}", flush=True)

    # ---- walk-forward yearly (OOS segments) ----
    print("\n  Walk-forward yearly (variant C, PIT):")
    for y, d in results["walkforward_yearly"].items():
        print(f"    {y}: return={d['return_pct']:+.2f}%  maxDD={d['max_dd_pct']:.2f}%")

    # ---- threshold perturbation 60/40 65/35 70/30 ----
    for bt, br in [(60.0, 40.0), (65.0, 35.0), (70.0, 30.0)]:
        name = f"thr{int(bt)}/{int(br)}"
        dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                    enable_dist_day_overlay=False,
                                    bull_threshold=bt, bear_threshold=br)
        print(f"\n--- {name} ---", flush=True)
        summary, m = _run(dual_cfg, all_data, all_rd, buckets, breadth, start, end)
        results["threshold_perturbation"][name] = m
        print(f"  Return={m['return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
              f"MaxDD={m['max_dd_pct']:.2f}%  PF={m['pf']}", flush=True)

    # ---- breadth lookback 7 / 10 / 13 ----
    for lb in [7, 10, 13]:
        name = f"lookback{lb}"
        dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                    enable_dist_day_overlay=False,
                                    divergence_lookback=lb)
        print(f"\n--- {name} ---", flush=True)
        summary, m = _run(dual_cfg, all_data, all_rd, buckets, breadth, start, end)
        results["breadth_lookback_perturbation"][name] = m
        print(f"  Return={m['return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
              f"MaxDD={m['max_dd_pct']:.2f}%  PF={m['pf']}", flush=True)

    # ---- summary tables ----
    print("\n" + "=" * 72)
    print("  THRESHOLD PERTURBATION (variant C, PIT breadth)")
    print("=" * 72)
    print(f"{'Threshold':<12}{'Return%':>10}{'Sharpe':>10}{'MaxDD%':>10}{'PF':>10}{'Exp%':>10}")
    for name, m in results["threshold_perturbation"].items():
        print(f"{name:<12}{m['return_pct']:>+10.2f}{m['sharpe']:>10.2f}"
              f"{m['max_dd_pct']:>10.2f}{m['pf']:>10}{m['exposure_pct']:>10.1f}")
    print("\n  BREADTH LOOKBACK PERTURBATION")
    print("=" * 72)
    print(f"{'Lookback':<12}{'Return%':>10}{'Sharpe':>10}{'MaxDD%':>10}{'PF':>10}{'Exp%':>10}")
    for name, m in results["breadth_lookback_perturbation"].items():
        print(f"{name:<12}{m['return_pct']:>+10.2f}{m['sharpe']:>10.2f}"
              f"{m['max_dd_pct']:>10.2f}{m['pf']:>10}{m['exposure_pct']:>10.1f}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_robustness_pit.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_robustness_pit.json")


if __name__ == "__main__":
    main()
