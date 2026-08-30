"""
audit_distdays_thresholds.py — Reviewer Task 2 Part C: Threshold Sensitivity

Backtests the dynamic universe (2018-01 .. 2025-07, top 20) with the
Distribution Days cap threshold varied 4 / 5 / 6 / 7, plus the no-overlay
baseline (HMM+Breadth). Reports:
  trigger frequency (share of trading days the cap fires),
  CAGR / Sharpe / Sortino / MaxDD / Calmar / return,
  CAGR retention vs the no-overlay HMM+Breadth variant.

Selection rule: do NOT pick a threshold solely because it maximizes CAGR —
the audit statistics decide whether any threshold is an "extreme condition".
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
from regime_dual_engine.breadth_data import get_breadth_series
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data
from regime_dual_engine.audit_distribution_days import part_b_stats
from regime_dual_engine.daily_signals import build_daily_signals


def _sortino(eq_series: pd.Series):
    rets = eq_series.pct_change().dropna()
    if len(rets) < 2:
        return 0.0
    downside = rets[rets < 0]
    dd = downside.std() if len(downside) > 1 else 0.0
    return float(np.sqrt(252) * rets.mean() / dd) if dd > 0 else 0.0


def _full_metrics(summary, cfg, start, end):
    m = compute_metrics(summary, cfg)
    eq = pd.DataFrame(summary["equity_curve"])
    eq["date"] = pd.to_datetime(eq["date"])
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    cagr = ((summary["final_equity"] / summary["starting_equity"]) ** (1 / years) - 1) * 100 \
        if summary["final_equity"] > 0 else 0.0
    sortino = _sortino(eq.set_index("date")["equity"])
    calmar = cagr / abs(m["max_drawdown_pct"]) if m["max_drawdown_pct"] < 0 else 0.0
    return {"return_pct": m["total_return_pct"], "cagr_pct": round(cagr, 2),
            "sharpe": m["sharpe"], "sortino": round(sortino, 2),
            "max_dd_pct": m["max_drawdown_pct"], "calmar": round(calmar, 2),
            "exposure_pct": _exposure_pct(summary["equity_curve"]),
            "pf": m["profit_factor"], "trades": m["total_trades"]}


def main(breadth=None, breadth_label="current-constituent (get_breadth_series)",
         out_suffix=""):
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 72)
    print(f"  REVIEWER TASK 2C — DIST-DAY THRESHOLD SENSITIVITY (4/5/6/7)  "
          f"[breadth: {breadth_label}]")
    print("=" * 72)
    all_data = load_data(end=end)
    if breadth is None:
        breadth = get_breadth_series(end=end, verbose=False)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"  {len(all_data)} tickers, {len(buckets)} buckets, "
          f"breadth {len(breadth)} days")

    results = {}

    # ---- baseline C (no overlay) ----
    print("\n--- baseline C: HMM+Breadth (no dist overlay) ---", flush=True)
    dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                enable_dist_day_overlay=False)
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = ["breakout"]
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
    eng = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                             top_n=top_n, start=start, end=end,
                             no_regime=False, benchmark="SPY", progress=False)
    summary = eng.run()
    results["C_no_overlay"] = _full_metrics(summary, cfg, start, end)
    print(f"  Return={results['C_no_overlay']['return_pct']:+.2f}%  "
          f"CAGR={results['C_no_overlay']['cagr_pct']:.2f}%  "
          f"Sharpe={results['C_no_overlay']['sharpe']:.2f}  "
          f"MaxDD={results['C_no_overlay']['max_dd_pct']:.2f}%", flush=True)

    # ---- thresholds 4/5/6/7 ----
    for thr in [4, 5, 6, 7]:
        name = f"D_thr{thr}"
        print(f"\n--- {name}: dist cap at >= {thr} ---", flush=True)
        dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                    enable_dist_day_overlay=True,
                                    dist_day_cap_threshold=thr)
        cfg = Config()
        cfg.entry_mode = "setup"
        cfg.setup_enabled_types = ["breakout"]
        cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
        eng = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                                 top_n=top_n, start=start, end=end,
                                 no_regime=False, benchmark="SPY", progress=False)
        summary = eng.run()
        m = _full_metrics(summary, cfg, start, end)
        # trigger frequency from the actual backtest regime log
        veto = [r for r in summary.get("regime_log", [])]
        trig = sum(1 for r in veto if "DISTRIBUTION_DAY_CAP" in r.get("vetoes", []))
        m["trigger_freq_pct"] = round(trig / max(1, len(veto)) * 100, 2)
        m["cagr_retention"] = round(m["cagr_pct"] / results["C_no_overlay"]["cagr_pct"], 3) \
            if results["C_no_overlay"]["cagr_pct"] != 0 else None
        results[name] = m
        print(f"  Return={m['return_pct']:+.2f}%  CAGR={m['cagr_pct']:.2f}%  "
              f"Sharpe={m['sharpe']:.2f}  Sortino={m['sortino']:.2f}  "
              f"MaxDD={m['max_dd_pct']:.2f}%  Calmar={m['calmar']:.2f}  "
              f"trigger={m['trigger_freq_pct']}%  retention={m['cagr_retention']}", flush=True)

    # ---- table ----
    print("\n" + "=" * 72)
    print("  THRESHOLD SENSITIVITY TABLE")
    print("=" * 72)
    hdr = "".join(f"{k:>12}" for k in ["Return%", "CAGR%", "Sharpe", "Sortino",
                                       "MaxDD%", "Calmar", "Trigger%", "Retention"])
    print(f"{'Variant':<14}{hdr}")
    for name, m in results.items():
        row = (f"{m['return_pct']:>+12.2f}{m['cagr_pct']:>12.2f}{m['sharpe']:>12.2f}"
               f"{m['sortino']:>12.2f}{m['max_dd_pct']:>12.2f}{m['calmar']:>12.2f}")
        if "trigger_freq_pct" in m:
            row += f"{m['trigger_freq_pct']:>12.1f}"
            row += f"{str(m['cagr_retention']):>12}"
        else:
            row += f"{'-':>12}{'-':>12}"
        print(f"{name:<14}{row}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"dual_engine_distdays_thresholds{out_suffix}.json"), "w") as f:
        json.dump({"breadth_used": breadth_label, "results": results}, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_distdays_thresholds{out_suffix}.json")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "pit":
        from regime_dual_engine.pit_breadth_data import get_breadth
        pit = get_breadth("pit", rebuild=False)
        main(breadth=pit, breadth_label="PIT (point-in-time S&P500)", out_suffix="_pit")
    else:
        main()
