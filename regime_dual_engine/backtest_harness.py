"""
backtest_harness.py — 4-Way Ablation on the Dynamic Universe

Runs the dual-engine regime as the global exposure gate on the existing
dynamic universe (top 20, monthly rebalance, gap-aware OHLC exit, setup entry).

Variants (spec §15):
  A. HMM only              (hmm_weight=1.0, breadth_weight=0.0)
  B. Breadth only          (hmm_weight=0.0, breadth_weight=1.0)
  C. HMM + Breadth         (0.5 / 0.5, overlay off)
  D. HMM + Breadth + Dist  (0.5 / 0.5, overlay on)

The engine subclasses DynamicBacktestEngine and overrides only the regime
computation — every other rule (entry setup, gap-aware exit, sizing, fees)
is identical across variants, so the comparison isolates regime value.
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
# NOTE: do NOT add this package's dir to sys.path — it would shadow the repo
# root `config.py` (name collision). regime_dual_engine resolves as a subpackage.

from config import Config
from dynamic_universe_backtest import DynamicBacktestEngine, build_rebalance_calendar
from src.backtest.engine import compute_metrics

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.engine import compute_regime_decision
from regime_dual_engine.breadth_data import get_breadth_series

warnings.filterwarnings("ignore")


ABLATION = {
    "A. HMM only":   dict(hmm_weight=1.0, breadth_weight=0.0, enable_dist_day_overlay=False),
    "B. Breadth only": dict(hmm_weight=0.0, breadth_weight=1.0, enable_dist_day_overlay=False),
    "C. HMM+Breadth": dict(hmm_weight=0.5, breadth_weight=0.5, enable_dist_day_overlay=False),
    "D. +DistDays":  dict(hmm_weight=0.5, breadth_weight=0.5, enable_dist_day_overlay=True),
}


class DualEngineBacktest(DynamicBacktestEngine):
    """DynamicBacktestEngine with the regime computation swapped to the dual engine."""

    def __init__(self, cfg, all_data, rebalance_dates, bucket_selections,
                 dual_cfg, breadth_df, **kwargs):
        super().__init__(cfg, all_data, rebalance_dates, bucket_selections, **kwargs)
        self.dual_cfg = dual_cfg
        self.breadth_df = breadth_df
        self._dual_hmm_cache: dict = {}

    def _market_regime_for(self, spy_df, date_idx, cache, cfg):
        date = spy_df["datetime"].iloc[-1]
        b_slice = self.breadth_df[self.breadth_df.index <= date]
        decision = compute_regime_decision(spy_df, b_slice, self.dual_cfg,
                                           hmm_cache=self._dual_hmm_cache)
        diag = decision["_diag"]
        return {
            "regime": decision["regime_label"],
            "trending": decision["regime_label"] != "BEAR",
            "latest_prob": diag["hmm_bull_prob"],
            "regime_score": decision["composite_score"],
            "score": round((decision["composite_score"] - 50.0) / 50.0, 3),
            "position_size_mult": decision["position_size_mult"],
            "strategy": decision["strategy_mode"],
            "components": {"hmm": diag["hmm_bull_prob"],
                           "breadth": diag["breadth_percentile"],
                           "dist_days": diag["distribution_days"]},
            "vetoes": decision["veto_flags"],
        }


def load_data(end="2025-07-31"):
    cache_dir = os.path.join(os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(cache_dir):
        cache_dir = os.path.join(_REPO_ROOT, "data", "cache", "equities")
    tickers = [f[:-4] for f in os.listdir(cache_dir) if f.endswith(".csv")]
    cutoff = pd.Timestamp(end)
    data = {}
    for t in sorted(tickers):
        try:
            df = pd.read_csv(os.path.join(cache_dir, f"{t}.csv"), parse_dates=["datetime"])
            df = df[df["datetime"] <= cutoff].reset_index(drop=True)
            if len(df) >= 60:
                data[t] = df
        except Exception:
            pass
    return data


def run_ablation(start="2018-01-01", end="2025-07-31", top_n=20, verbose=True):
    print("Loading data + breadth...")
    all_data = load_data(end=end)
    breadth = get_breadth_series(end=end, verbose=False)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        import json as _json
        buckets = _json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"  {len(all_data)} tickers, {len(buckets)} buckets, "
          f"breadth {len(breadth)} days")

    results = {}
    for name, kw in ABLATION.items():
        if verbose:
            print(f"\n--- {name} ---")
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
        from dynamic_universe_backtest import _exposure_pct
        metrics["exposure_pct"] = _exposure_pct(summary["equity_curve"])
        metrics["regime_dist"] = _regime_distribution(summary)
        metrics["veto_dist"] = _veto_distribution(summary)
        results[name] = {"summary": summary, "metrics": metrics}
        m = metrics
        if verbose:
            print(f"  >> Return={m['total_return_pct']:+.2f}%  "
                  f"Sharpe={m['sharpe']:.2f}  MaxDD={m['max_drawdown_pct']:.2f}%  "
                  f"PF={m['profit_factor']}  Trades={m['total_trades']}  "
                  f"Exp={metrics['exposure_pct']}%")
    return results


def _regime_distribution(summary):
    from collections import Counter
    return dict(Counter(r["regime"] for r in summary.get("regime_log", [])))


def _veto_distribution(summary):
    from collections import Counter
    c = Counter()
    for r in summary.get("regime_log", []):
        for v in r.get("vetoes", []):
            c[v] += 1
    return dict(c)


def print_ablation_table(results):
    print("\n" + "=" * 78)
    print("  ABLATION TABLE (dual-engine regime on dynamic universe)")
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
    print("\n  Regime distribution per variant:")
    for name, res in results.items():
        print(f"    {name:<16} {res['metrics']['regime_dist']}")
    print("\n  Veto distribution per variant:")
    for name, res in results.items():
        print(f"    {name:<16} {res['metrics']['veto_dist']}")


if __name__ == "__main__":
    results = run_ablation(verbose=True)
    print_ablation_table(results)
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out = {name: {"metrics": res["metrics"]} for name, res in results.items()}
    with open(os.path.join(out_dir, "dual_engine_ablation.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_ablation.json")
