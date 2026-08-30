"""
setup_v1_validation.py — Setup V1 Validation on frozen Regime v1 (HMM + PIT Breadth)

Validates the EXISTING setup_agent.py against Regime v1 (dual-engine, PIT
breadth, dist-day overlay OFF). Regime v1 / Screener / setup_agent are NOT
modified. Five tasks:

  T1  Setup x Regime v1 cross-table (entry_regime x setup_type)
  T2  Setup score threshold sensitivity (0.4 / 0.5 / 0.6 / 0.7) — breakout only
  T3  Quality multiplier effectiveness (current mult vs fixed 1.0)
  T5  Breakout Only vs Pullback Only vs Breakout+Pullback (Regime v1 + PIT)

T4 (live vs backtest consistency) is a static audit — see audit_live_backtest.py.

Execution note: all backtests use the SAME DynamicBacktestEngine mechanics
(monthly bucket, RS-sorted iteration, next-open entry, pending signals,
gap-aware OHLC exit, size_position with quality_mult, identical capital
allocation & priority rules). The ONLY difference between variants is
cfg.setup_enabled_types / setup_score_threshold / quality-mult mapping, so
capital-allocation bias is held constant (reviewer note).

Run:  python regime_dual_engine/setup_v1_validation.py   (background, ~15 min)
"""
from __future__ import annotations

import os
import sys
import json
import warnings
from collections import Counter

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
from regime_dual_engine.pit_breadth_data import get_breadth
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data

START, END, TOP_N = "2018-01-01", "2025-07-31", 20
YEARS = (pd.Timestamp(END) - pd.Timestamp(START)).days / 365.25


def _cagr(summary):
    if summary.get("final_equity", 0) > 0:
        return ((summary["final_equity"] / summary["starting_equity"]) ** (1 / YEARS) - 1) * 100
    return 0.0


def _avg_r(trades):
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    return round(float(np.mean(rs)), 3) if rs else None


def _pf(trades):
    wins = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    losses = abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0))
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return round(wins / losses, 3)


def _run(entry_types, threshold=0.5, mult_override=None, verbose=False):
    """One Regime-v1 backtest. Returns (summary, metrics, extra)."""
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = entry_types
    cfg.setup_score_threshold = threshold
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
    if mult_override is not None:  # e.g. dict(high=1.0, mid=1.0, low=1.0) = fixed 1.0
        cfg.setup_quality_mult_high = mult_override["high"]
        cfg.setup_quality_mult_mid = mult_override["mid"]
        cfg.setup_quality_mult_low = mult_override["low"]
    dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                enable_dist_day_overlay=False)  # Regime v1
    all_data = load_data(end=END)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{START}_{END}_top{TOP_N}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    breadth = get_breadth("pit", rebuild=False)
    eng = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                             top_n=TOP_N, start=START, end=END,
                             no_regime=False, benchmark="SPY", progress=False)
    summary = eng.run()
    m = compute_metrics(summary, cfg)
    extra = {
        "cagr_pct": round(_cagr(summary), 2),
        "exposure_pct": round(_exposure_pct(summary["equity_curve"]), 1),
        "avg_r": _avg_r(summary["trade_log"]),
    }
    return summary, m, extra


def _setup_regime_table(trades):
    """T1: entry_regime x setup_type cross table."""
    rows = []
    for reg in ["BULL", "SIDEWAYS", "BEAR"]:
        for st in ["breakout", "pullback"]:
            sub = [t for t in trades if t.get("entry_regime") == reg
                   and t.get("setup_type") == st]
            if not sub:
                continue
            rows.append({
                "regime": reg, "setup_type": st,
                "n": len(sub),
                "pf": _pf(sub),
                "win_pct": round(np.mean([t["net_pnl"] > 0 for t in sub]) * 100, 1),
                "avg_r": _avg_r(sub),
                "net_pnl_sum": round(sum(t["net_pnl"] for t in sub), 2),
                "contrib_pct": round(sum(t["net_pnl"] for t in sub) / max(1e-9, abs(sum(t["net_pnl"] for t in trades))) * 100, 1),
            })
    return rows


def main():
    print("=" * 78)
    print("  SETUP V1 VALIDATION — frozen Regime v1 (HMM + PIT Breadth)")
    print(f"  {START}..{END} | top{TOP_N} | monthly buckets | next-open | gap-aware exit")
    print("=" * 78, flush=True)

    all_data = load_data(end=END)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{START}_{END}_top{TOP_N}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    breadth = get_breadth("pit", rebuild=False)
    print(f"  {len(all_data)} tickers, {len(buckets)} buckets, "
          f"breadth {len(breadth)} days", flush=True)

    results = {}

    # ---- T5 core comparison (also feeds T1) ----
    print("\n[T5] Breakout Only / Pullback Only / Both (Regime v1 + PIT)", flush=True)
    t5 = {}
    for name, types in [("Breakout Only", ["breakout"]),
                        ("Pullback Only", ["pullback"]),
                        ("Breakout+Pullback", ["breakout", "pullback"])]:
        print(f"  --- {name} ---", flush=True)
        summary, m, ex = _run(types)
        t5[name] = {
            "return_pct": m["total_return_pct"], "cagr_pct": ex["cagr_pct"],
            "sharpe": m["sharpe"], "max_dd_pct": m["max_drawdown_pct"],
            "pf": m["profit_factor"], "win_pct": round(m["win_rate"] * 100, 1),
            "trades": m["total_trades"], "exposure_pct": ex["exposure_pct"],
            "avg_r": ex["avg_r"],
            "setup_dist": dict(Counter(t.get("setup_type") for t in summary["trade_log"])),
            "fill_dist": dict(Counter(t.get("exit_fill_model") for t in summary["trade_log"])),
            "_trades": summary["trade_log"],
        }
        print(f"  >> Ret={m['total_return_pct']:+.2f}%  CAGR={ex['cagr_pct']:.2f}%  "
              f"Sharpe={m['sharpe']:.2f}  MaxDD={m['max_drawdown_pct']:.2f}%  "
              f"PF={m['profit_factor']}  Trades={m['total_trades']}", flush=True)
    results["T5_core_comparison"] = {k: {kk: vv for kk, vv in v.items() if kk != "_trades"}
                                     for k, v in t5.items()}

    # ---- T1: setup x regime (from both single-setup runs for clean attribution) ----
    print("\n[T1] Setup x Regime v1 cross-table", flush=True)
    t1 = {"breakout": _setup_regime_table(t5["Breakout Only"]["_trades"]),
          "pullback": _setup_regime_table(t5["Pullback Only"]["_trades"])}
    results["T1_setup_x_regime"] = t1
    for st, rows in t1.items():
        print(f"  {st}:")
        for r in rows:
            print(f"    {r['regime']:<10} n={r['n']:>4}  PF={r['pf']}  "
                  f"Win={r['win_pct']}%  AvgR={r['avg_r']}  "
                  f"PnL=${r['net_pnl_sum']:+.2f}  contrib={r['contrib_pct']}%")

    # ---- T2: setup score threshold sensitivity (breakout only) ----
    print("\n[T2] Setup score threshold 0.4/0.5/0.6/0.7 (breakout only)", flush=True)
    t2 = {}
    for thr in [0.4, 0.5, 0.6, 0.7]:
        print(f"  --- threshold={thr} ---", flush=True)
        summary, m, ex = _run(["breakout"], threshold=thr)
        t2[str(thr)] = {
            "trades": m["total_trades"], "pf": m["profit_factor"],
            "win_pct": round(m["win_rate"] * 100, 1), "avg_r": ex["avg_r"],
            "cagr_pct": ex["cagr_pct"], "max_dd_pct": m["max_drawdown_pct"],
            "return_pct": m["total_return_pct"], "sharpe": m["sharpe"],
        }
        print(f"  >> Trades={m['total_trades']}  PF={m['profit_factor']}  "
              f"Win={m['win_rate']*100:.1f}%  AvgR={ex['avg_r']}  "
              f"CAGR={ex['cagr_pct']:.2f}%  MaxDD={m['max_drawdown_pct']:.2f}%", flush=True)
    results["T2_threshold_sensitivity"] = t2

    # ---- T3: quality multiplier current vs fixed 1.0 (breakout only) ----
    print("\n[T3] Quality multiplier: current vs fixed 1.0", flush=True)
    fixed = dict(high=1.0, mid=1.0, low=1.0)
    t3 = {}
    for label, mo in [("current", None), ("fixed_1.0", fixed)]:
        print(f"  --- {label} ---", flush=True)
        summary, m, ex = _run(["breakout"], mult_override=mo)
        t3[label] = {
            "cagr_pct": ex["cagr_pct"], "sharpe": m["sharpe"],
            "max_dd_pct": m["max_drawdown_pct"], "pf": m["profit_factor"],
            "exposure_pct": ex["exposure_pct"], "trades": m["total_trades"],
            "return_pct": m["total_return_pct"],
        }
        print(f"  >> CAGR={ex['cagr_pct']:.2f}%  Sharpe={m['sharpe']:.2f}  "
              f"MaxDD={m['max_drawdown_pct']:.2f}%  PF={m['profit_factor']}  "
              f"Exp={ex['exposure_pct']}%", flush=True)
    results["T3_quality_mult"] = t3

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "setup_v1_validation.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved -> reports/setup_v1_validation.json")


if __name__ == "__main__":
    main()
