"""
validation_conditioning.py — Validation Test 3 (Regime Conditioning) + Test 5 (Walk-Forward)

Test 3: Unconditioned strategy (no regime filter) vs Dual-Engine conditioned.
  - CAGR / Sharpe / Sortino / MaxDD / Calmar / volatility / turnover / exposure / PF
  - regime-specific returns (BULL / SIDEWAYS / BEAR days)

Test 5: Walk-forward stability of the dual engine (HMM+Breadth). The dual engine
is point-in-time BY CONSTRUCTION (rolling breadth percentile + refit HMM, fixed
thresholds — no cross-sectional parameter fitting), so walk-forward reduces to:
  - 1-year out-of-sample segments must be consistently non-catastrophic
  - report yearly return / MaxDD / exposure
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
from dynamic_universe_backtest import DynamicBacktestEngine, build_rebalance_calendar, _exposure_pct
from src.backtest.engine import compute_metrics

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.breadth_data import get_breadth_series
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data

warnings.filterwarnings("ignore")


def _sortino(eq_series: pd.Series):
    rets = eq_series.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    downside = rets[rets < 0]
    dd = downside.std() if len(downside) > 1 else 0.0
    return float(np.sqrt(252) * rets.mean() / dd) if dd > 0 else 0.0


def _metrics(summary, cfg, start, end):
    m = compute_metrics(summary, cfg)
    eq = pd.DataFrame(summary["equity_curve"])
    eq["date"] = pd.to_datetime(eq["date"])
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    cagr = ((summary["final_equity"] / summary["starting_equity"]) ** (1 / years) - 1) * 100 \
        if summary["final_equity"] > 0 else 0.0
    sortino = _sortino(eq.set_index("date")["equity"])
    calmar = cagr / abs(m["max_drawdown_pct"]) if m["max_drawdown_pct"] < 0 else 0.0
    rets = eq["equity"].pct_change().dropna()
    ann_vol = float(rets.std() * np.sqrt(252) * 100)
    # turnover proxy: sum of |shares| traded relative to avg position
    trades = summary["trade_log"]
    dollar_traded = sum(t["shares"] * (t["entry_price"] + t["exit_price"]) / 2 for t in trades)
    avg_equity = eq["equity"].mean()
    turnover = dollar_traded / avg_equity if avg_equity > 0 else 0.0
    return {
        "return_pct": m["total_return_pct"], "cagr_pct": round(cagr, 2),
        "sharpe": m["sharpe"], "sortino": round(sortino, 2),
        "max_dd_pct": m["max_drawdown_pct"], "calmar": round(calmar, 2),
        "ann_vol_pct": round(ann_vol, 2), "turnover": round(turnover, 2),
        "exposure_pct": _exposure_pct(summary["equity_curve"]),
        "pf": m["profit_factor"], "win_rate": m["win_rate"],
        "trades": m["total_trades"],
    }


def _regime_specific_returns(summary):
    """Equity return on BULL/SIDEWAYS/BEAR days (from regime_log)."""
    rlog = {r["date"]: r["regime"] for r in summary.get("regime_log", [])}
    eq = pd.DataFrame(summary["equity_curve"])
    eq["date"] = pd.to_datetime(eq["date"]).dt.strftime("%Y-%m-%d")
    eq["ret"] = eq["equity"].pct_change()
    eq["regime"] = eq["date"].map(rlog)
    out = {}
    for reg in ["BULL", "SIDEWAYS", "BEAR"]:
        sub = eq[eq["regime"] == reg]["ret"].dropna()
        out[reg] = {"days": int(len(sub)),
                    "daily_mean_bps": round(float(sub.mean() * 10000), 2),
                    "cum_return_pct": round(float((1 + sub).prod() - 1) * 100, 2)}
    return out


def _yearly(oos_start, summary):
    """Yearly segments for walk-forward (1-year OOS windows from 2019)."""
    eq = pd.DataFrame(summary["equity_curve"])
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    years = sorted(eq["year"].unique())
    out = {}
    for y in years:
        sub = eq[eq["year"] == y]
        if len(sub) < 5:
            continue
        ret = (sub["equity"].iloc[-1] / sub["equity"].iloc[0] - 1) * 100
        dd = ((sub["equity"] / sub["equity"].cummax() - 1) * 100).min()
        out[int(y)] = {"return_pct": round(float(ret), 2), "max_dd_pct": round(float(dd), 2),
                       "days": int(len(sub))}
    return out


def main():
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 70)
    print("  TEST 3: REGIME CONDITIONING  +  TEST 5: WALK-FORWARD")
    print("=" * 70)
    all_data = load_data(end=end)
    breadth = get_breadth_series(end=end, verbose=False)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"  {len(all_data)} tickers, {len(buckets)} buckets")

    results = {}

    # ---- Unconditioned baseline (no regime) ----
    print("\n--- Unconditioned (no regime filter) ---")
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = ["breakout"]
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
    eng = DynamicBacktestEngine(cfg, all_data, all_rd, buckets, top_n=top_n,
                                start=start, end=end, no_regime=True,
                                benchmark="SPY", progress=False)
    summary_u = eng.run()
    results["unconditioned"] = _metrics(summary_u, cfg, start, end)
    print(f"  Return={results['unconditioned']['return_pct']:+.2f}%  "
          f"Sharpe={results['unconditioned']['sharpe']:.2f}  "
          f"MaxDD={results['unconditioned']['max_dd_pct']:.2f}%  "
          f"PF={results['unconditioned']['pf']}")

    # ---- Conditioned: HMM + Breadth (variant C) ----
    print("\n--- Conditioned: HMM+Breadth (variant C) ---")
    dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                enable_dist_day_overlay=False)
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = ["breakout"]
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
    eng_c = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                               top_n=top_n, start=start, end=end,
                               no_regime=False, benchmark="SPY", progress=False)
    summary_c = eng_c.run()
    results["conditioned_C"] = _metrics(summary_c, cfg, start, end)
    results["regime_specific"] = _regime_specific_returns(summary_c)
    results["walkforward_yearly"] = _yearly(start, summary_c)
    print(f"  Return={results['conditioned_C']['return_pct']:+.2f}%  "
          f"Sharpe={results['conditioned_C']['sharpe']:.2f}  "
          f"MaxDD={results['conditioned_C']['max_dd_pct']:.2f}%  "
          f"PF={results['conditioned_C']['pf']}")

    # ---- Comparison ----
    print("\n" + "=" * 70)
    print("  CONDITIONING COMPARISON")
    print("=" * 70)
    keys = [("Return%", "return_pct"), ("CAGR%", "cagr_pct"), ("Sharpe", "sharpe"),
            ("Sortino", "sortino"), ("MaxDD%", "max_dd_pct"), ("Calmar", "calmar"),
            ("Vol%", "ann_vol_pct"), ("Turnover", "turnover"), ("Exp%", "exposure_pct"),
            ("PF", "pf"), ("Win%", "win_rate"), ("Trades", "trades")]
    hdr = "".join(f"{n:>10}" for n, _ in keys)
    print(f"{'Variant':<18}{hdr}")
    print("-" * (18 + 10 * len(keys)))
    for name in ["unconditioned", "conditioned_C"]:
        m = results[name]
        row = ""
        for _, k in keys:
            v = m[k]
            if k == "win_rate":
                row += f"{v*100:>10.1f}"
            elif k in ("return_pct", "cagr_pct"):
                row += f"{v:>+10.2f}"
            elif k in ("sharpe", "sortino", "calmar", "turnover", "ann_vol_pct", "max_dd_pct", "exposure_pct"):
                row += f"{v:>10.2f}"
            else:
                row += f"{str(v):>10}"
        print(f"{name:<18}{row}")

    print("\n  Regime-specific returns (conditioned C):")
    for reg, d in results["regime_specific"].items():
        print(f"    {reg:<10} days={d['days']:>4}  daily_bps={d['daily_mean_bps']:+.2f}  "
              f"cum={d['cum_return_pct']:+.2f}%")

    print("\n  Walk-forward yearly segments (conditioned C):")
    for y, d in results["walkforward_yearly"].items():
        print(f"    {y}: return={d['return_pct']:+.2f}%  maxDD={d['max_dd_pct']:.2f}%  "
              f"days={d['days']}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_conditioning.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_conditioning.json")


if __name__ == "__main__":
    main()
