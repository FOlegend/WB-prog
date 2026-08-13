"""
setup_comparison.py — Setup Agent Backtest Comparison

Compares the new setup-based entry against the legacy weighted-score entry:
  1. Current Simplified (legacy weighted score)
  2. Setup: Breakout only
  3. Setup: Pullback only
  4. Setup: Breakout + Pullback

All use Current Weights (default), gate mode, score_full=70, and the gap-aware
OHLC exit model. Also runs a determinism test (3 identical runs of variant 4).

Run:
  python setup_comparison.py
"""
from __future__ import annotations

import os
import sys
import json
import time
import warnings
import base64
import io

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from dynamic_universe_backtest import DynamicBacktestEngine, build_rebalance_calendar
from src.backtest.engine import compute_metrics
from src.backtest.engine import compute_metrics as _cm

warnings.filterwarnings("ignore")


VARIANTS = {
    "Current Simplified": {
        "entry_mode": "weighted",
        "setup_types": None,
        "desc": "Legacy weighted-score entry (0.35*regime + 0.65*tech > 0.25)",
    },
    "Breakout Only": {
        "entry_mode": "setup",
        "setup_types": ["breakout"],
        "desc": "Setup agent: breakout setup only",
    },
    "Pullback Only": {
        "entry_mode": "setup",
        "setup_types": ["pullback"],
        "desc": "Setup agent: pullback setup only",
    },
    "Breakout + Pullback": {
        "entry_mode": "setup",
        "setup_types": ["breakout", "pullback"],
        "desc": "Setup agent: breakout + pullback",
    },
}

VARIANT_COLORS = {
    "Current Simplified": "#888888",
    "Breakout Only": "#1f77b4",
    "Pullback Only": "#2ca02c",
    "Breakout + Pullback": "#d62728",
}


def load_data_from_cache(end: str = "2025-07-31"):
    cache_dir = os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(cache_dir):
        cache_dir = os.path.join(_REPO_ROOT, "data", "cache", "equities")
    all_tickers = [f.replace(".csv", "") for f in os.listdir(cache_dir)
                   if f.endswith(".csv")]
    cutoff = pd.Timestamp(end)
    all_data = {}
    for t in sorted(all_tickers):
        p = os.path.join(cache_dir, f"{t}.csv")
        try:
            df = pd.read_csv(p, parse_dates=["datetime"])
            if len(df) > 0:
                df = df[df["datetime"] <= cutoff].reset_index(drop=True)
                all_data[t] = df
        except Exception:
            pass
    return all_data


def run_variant(name, spec, all_data, all_rd, buckets, start, end, verbose=True):
    cfg = Config()
    cfg.entry_mode = spec["entry_mode"]
    if spec["setup_types"] is not None:
        cfg.setup_enabled_types = spec["setup_types"]
    # Current Weights (production default)
    cfg.regime_score_weights = {"hmm": 0.20, "ma": 0.30, "ker": 0.18,
                                 "adx": 0.10, "dist": 0.22}
    cfg.regime_exposure_mode = "gate"
    cfg.regime_score_full = 70.0

    if verbose:
        print(f"\n{'='*60}\n  {name}\n  {spec['desc']}\n{'='*60}")

    eng = DynamicBacktestEngine(
        cfg, all_data, all_rd, buckets, top_n=20,
        start=start, end=end, no_regime=False, benchmark="SPY", progress=verbose,
    )
    summary = eng.run()
    metrics = compute_metrics(summary, cfg)
    return summary, metrics, cfg


def _setup_type_distribution(trades):
    from collections import Counter
    return dict(Counter(t.get("setup_type") for t in trades))


def _fill_model_distribution(trades):
    from collections import Counter
    return dict(Counter(t.get("exit_fill_model") for t in trades))


def main():
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 70)
    print("  SETUP AGENT BACKTEST COMPARISON")
    print("  Gap-aware OHLC exit | Current Weights | gate | score_full=70")
    print("=" * 70)

    all_data = load_data_from_cache(end=end)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"\n  {len(all_data)} tickers, {len(buckets)} buckets, {len(all_rd)} rebalance dates")

    results = {}
    for name, spec in VARIANTS.items():
        summary, metrics, cfg = run_variant(name, spec, all_data, all_rd,
                                            buckets, start, end, verbose=True)
        trades = summary["trade_log"]
        m = metrics
        print(f"\n  >> Return={m['total_return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
              f"MaxDD={m['max_drawdown_pct']:.2f}%  PF={m['profit_factor']}  "
              f"Win={m['win_rate']*100:.1f}%  Trades={m['total_trades']}")
        results[name] = {
            "summary": summary, "metrics": metrics,
            "setup_dist": _setup_type_distribution(trades),
            "fill_dist": _fill_model_distribution(trades),
        }

    # ---- Determinism test (variant 4, 3 runs) ----
    print("\n" + "=" * 70)
    print("  DETERMINISM TEST (Breakout + Pullback, 3 runs)")
    print("=" * 70)
    det_results = []
    spec = VARIANTS["Breakout + Pullback"]
    for run in range(3):
        cfg = Config()
        cfg.entry_mode = spec["entry_mode"]
        cfg.setup_enabled_types = spec["setup_types"]
        cfg.regime_score_weights = {"hmm": 0.20, "ma": 0.30, "ker": 0.18,
                                     "adx": 0.10, "dist": 0.22}
        cfg.regime_exposure_mode = "gate"
        cfg.regime_score_full = 70.0
        eng = DynamicBacktestEngine(cfg, all_data, all_rd, buckets, top_n=20,
                                    start=start, end=end, no_regime=False,
                                    benchmark="SPY", progress=False)
        summary = eng.run()
        metrics = compute_metrics(summary, cfg)
        key = (metrics["total_return_pct"], metrics["sharpe"],
               metrics["max_drawdown_pct"], metrics["profit_factor"],
               metrics["total_trades"])
        det_results.append(key)
        print(f"  Run {run+1}: Return={key[0]:+.2f}%  Sharpe={key[1]:.2f}  "
              f"MaxDD={key[2]:.2f}%  PF={key[3]}  Trades={key[4]}")
    deterministic = len(set(det_results)) == 1
    print(f"\n  Deterministic: {'YES (3 identical runs)' if deterministic else 'NO (results differ)'}")

    # ---- Save results ----
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out = {
        "meta": {"start": start, "end": end, "top_n": top_n,
                 "config": "Current Weights | gate | score_full=70 | gap-aware OHLC exit"},
        "variants": {
            name: {"metrics": results[name]["metrics"],
                   "setup_distribution": results[name]["setup_dist"],
                   "fill_model_distribution": results[name]["fill_dist"]}
            for name in VARIANTS
        },
        "determinism_test": {
            "deterministic": deterministic,
            "runs": [{"return": r[0], "sharpe": r[1], "max_dd": r[2],
                      "pf": r[3], "trades": r[4]} for r in det_results],
        },
    }
    out_path = os.path.join(out_dir, "setup_comparison.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  Results saved to: {out_path}")

    # ---- Summary table ----
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"{'Variant':<22}{'Return%':>10}{'Sharpe':>9}{'MaxDD%':>10}{'PF':>8}{'Win%':>8}{'Trades':>9}")
    print("-" * 76)
    for name in VARIANTS:
        m = results[name]["metrics"]
        pf = m["profit_factor"]
        pf_s = "inf" if pf == "inf" else f"{pf:.2f}"
        print(f"{name:<22}{m['total_return_pct']:>+10.2f}{m['sharpe']:>9.2f}"
              f"{m['max_drawdown_pct']:>10.2f}{pf_s:>8}{m['win_rate']*100:>8.1f}"
              f"{m['total_trades']:>9}")

    print("\n  Setup type distribution per variant:")
    for name in VARIANTS:
        print(f"    {name}: {results[name]['setup_dist']}")
    print("\n  Exit fill model distribution per variant:")
    for name in VARIANTS:
        print(f"    {name}: {results[name]['fill_dist']}")


if __name__ == "__main__":
    main()
