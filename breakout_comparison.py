"""
breakout_comparison.py — Breakout Only: score_full=70 vs 60 (controlled)

v3.2.3 setup agent: Breakout Only, gap-aware OHLC exit, next-open entry,
deterministic ordering, real RS rank, quality_mult capped at 1.0.

Reported metrics:
  Return%, CAGR%, Sharpe, MaxDD%, MaxDD duration, PF, Win%, Trades,
  Avg win, Avg loss, Avg R, Worst R, Gap fill count, 2020 DD, 2022 DD,
  2023 return/exposure, setup_score bucket diagnostics.

Run:
  python breakout_comparison.py
"""
from __future__ import annotations

import os
import sys
import json
import time
import warnings
from collections import Counter

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from dynamic_universe_backtest import DynamicBacktestEngine, build_rebalance_calendar
from src.backtest.engine import compute_metrics

warnings.filterwarnings("ignore")


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


def _phase_maxdd(equity_curve, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    eq = [x for x in equity_curve if s <= pd.Timestamp(x["date"]) <= e]
    if len(eq) < 2:
        return 0.0
    vals = pd.Series([x["equity"] for x in eq])
    dd = (vals / vals.cummax() - 1).min() * 100
    return round(float(dd), 2)


def _phase_return_exposure(summary, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    eq = [x for x in summary["equity_curve"] if s <= pd.Timestamp(x["date"]) <= e]
    if len(eq) < 2:
        return 0.0, 0.0
    ret = (eq[-1]["equity"] / eq[0]["equity"] - 1) * 100
    exp = sum(1 for x in eq if x["n_positions"] > 0) / len(eq) * 100
    return round(ret, 2), round(exp, 1)


def _maxdd_duration_days(equity_curve):
    """Longest drawdown duration (peak-to-peak) in trading days."""
    if not equity_curve:
        return 0
    vals = [x["equity"] for x in equity_curve]
    peak = vals[0]
    peak_idx = 0
    longest = 0
    for i, v in enumerate(vals):
        if v >= peak:
            peak = v
            peak_idx = i
        else:
            longest = max(longest, i - peak_idx)
    return longest


def _cagr(summary, start, end):
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    if years <= 0:
        return 0.0
    ratio = summary["final_equity"] / summary["starting_equity"]
    if ratio <= 0:
        return 0.0
    return round((ratio ** (1 / years) - 1) * 100, 2)


def _setup_buckets(trades):
    """Performance buckets by setup_score: 0.70-0.75, 0.75-0.80, 0.80-0.85, 0.85-1.00."""
    buckets = [("0.70-0.75", 0.70, 0.75),
               ("0.75-0.80", 0.75, 0.80),
               ("0.80-0.85", 0.80, 0.85),
               ("0.85-1.00", 0.85, 1.01)]
    out = []
    for label, lo, hi in buckets:
        sel = [t for t in trades
               if t.get("setup_score") is not None and lo <= t["setup_score"] < hi]
        if not sel:
            out.append({"bucket": label, "trade_count": 0, "return_contribution": 0.0,
                        "pf": 0.0, "win_rate": 0.0, "avg_r": None, "worst_r": None,
                        "gap_fill_count": 0})
            continue
        pnls = [t["net_pnl"] for t in sel]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gw, gl = sum(wins), abs(sum(losses))
        pf = round(gw / gl, 2) if gl > 0 else float("inf")
        rs = [t.get("r_multiple") for t in sel if t.get("r_multiple") is not None]
        out.append({
            "bucket": label,
            "trade_count": len(sel),
            "return_contribution": round(sum(pnls), 2),
            "pf": pf,
            "win_rate": round(len(wins) / len(sel), 3),
            "avg_r": round(float(np.mean(rs)), 3) if rs else None,
            "worst_r": round(float(min(rs)), 3) if rs else None,
            "gap_fill_count": sum(1 for t in sel if t.get("exit_fill_model") == "GAP"),
        })
    return out


def detailed_metrics(summary, cfg, start, end):
    m = compute_metrics(summary, cfg)
    trades = summary["trade_log"]
    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]

    out = {
        "return_pct": m["total_return_pct"],
        "cagr_pct": _cagr(summary, start, end),
        "sharpe": m["sharpe"],
        "max_dd_pct": m["max_drawdown_pct"],
        "max_dd_duration_days": _maxdd_duration_days(summary["equity_curve"]),
        "pf": m["profit_factor"],
        "win_rate": m["win_rate"],
        "trades": m["total_trades"],
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0.0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0.0,
        "avg_r": round(float(np.mean(rs)), 3) if rs else None,
        "worst_r": round(float(min(rs)), 3) if rs else None,
        "gap_fill_count": sum(1 for t in trades if t.get("exit_fill_model") == "GAP"),
        "dd_2020": _phase_maxdd(summary["equity_curve"], "2020-02-19", "2020-04-30"),
        "dd_2022": _phase_maxdd(summary["equity_curve"], "2022-01-01", "2022-10-12"),
    }
    ret_2023, exp_2023 = _phase_return_exposure(summary, "2023-01-01", "2023-12-31")
    out["ret_2023_pct"] = ret_2023
    out["exposure_2023_pct"] = exp_2023
    out["setup_buckets"] = _setup_buckets(trades)
    return out


def run_one(score_full, all_data, all_rd, buckets, start, end):
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = ["breakout"]
    cfg.regime_score_weights = {"hmm": 0.20, "ma": 0.30, "ker": 0.18,
                                 "adx": 0.10, "dist": 0.22}
    cfg.regime_exposure_mode = "gate"
    cfg.regime_score_full = score_full
    eng = DynamicBacktestEngine(cfg, all_data, all_rd, buckets, top_n=20,
                                start=start, end=end, no_regime=False,
                                benchmark="SPY", progress=True)
    summary = eng.run()
    return summary, cfg


def main():
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 70)
    print("  BREAKOUT ONLY COMPARISON: score_full=70 vs 60")
    print("  gap-aware OHLC | next-open entry | deterministic | RS rank")
    print("=" * 70)

    all_data = load_data_from_cache(end=end)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"\n  {len(all_data)} tickers, {len(buckets)} buckets, {len(all_rd)} dates")

    results = {}
    for label, sf in [("score_full=70", 70.0), ("score_full=60", 60.0)]:
        print(f"\n{'='*60}\n  {label}\n{'='*60}")
        summary, cfg = run_one(sf, all_data, all_rd, buckets, start, end)
        m = detailed_metrics(summary, cfg, start, end)
        results[label] = m
        print(f"\n  >> Return={m['return_pct']:+.2f}%  CAGR={m['cagr_pct']:+.2f}%  "
              f"Sharpe={m['sharpe']:.2f}  MaxDD={m['max_dd_pct']:.2f}%  "
              f"PF={m['pf']}  Trades={m['trades']}")

    # Save
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out = {"meta": {"start": start, "end": end, "top_n": top_n,
                    "setup": "Breakout Only", "entry": "next-open",
                    "exit": "gap-aware OHLC", "weights": "Current"},
           "variants": results}
    with open(os.path.join(out_dir, "breakout_comparison.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)

    # Summary table
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    keys = [
        ("Return%", "return_pct", "+.2f"),
        ("CAGR%", "cagr_pct", "+.2f"),
        ("Sharpe", "sharpe", ".2f"),
        ("MaxDD%", "max_dd_pct", ".2f"),
        ("DDdur", "max_dd_duration_days", "d"),
        ("PF", "pf", ""),
        ("Win%", "win_rate", ""),
        ("Trades", "trades", "d"),
        ("AvgWin", "avg_win", ".2f"),
        ("AvgLoss", "avg_loss", ".2f"),
        ("AvgR", "avg_r", ""),
        ("WorstR", "worst_r", ""),
        ("GapFill", "gap_fill_count", "d"),
        ("2020DD%", "dd_2020", ".2f"),
        ("2022DD%", "dd_2022", ".2f"),
        ("2023Ret%", "ret_2023_pct", "+.2f"),
        ("2023Exp%", "exposure_2023_pct", ".1f"),
    ]
    hdr = "".join(f"{name:>12}" for name, _, _ in keys)
    print(f"{'':<18}{hdr}")
    print("-" * (18 + 12 * len(keys)))
    for label in results:
        row = "".join(f"{results[label][k]:>12}" if not isinstance(results[label][k], str)
                      else f"{results[label][k]:>12}"
                      for _, k, _ in keys)
        # format win_rate as %
        print(f"{label:<18}{row}")

    # Simpler explicit table
    print("\n  Detailed table:")
    for label in results:
        m = results[label]
        wr = f"{m['win_rate']*100:.1f}%"
        print(f"\n  {label}:")
        print(f"    Return={m['return_pct']:+.2f}%  CAGR={m['cagr_pct']:+.2f}%  "
              f"Sharpe={m['sharpe']:.2f}  MaxDD={m['max_dd_pct']:.2f}%  "
              f"DDdur={m['max_dd_duration_days']}d  PF={m['pf']}")
        print(f"    Win={wr}  Trades={m['trades']}  AvgWin={m['avg_win']:.2f}  "
              f"AvgLoss={m['avg_loss']:.2f}  AvgR={m['avg_r']}  WorstR={m['worst_r']}  "
              f"GapFill={m['gap_fill_count']}")
        print(f"    2020DD={m['dd_2020']:.2f}%  2022DD={m['dd_2022']:.2f}%  "
              f"2023Ret={m['ret_2023_pct']:+.2f}%  2023Exp={m['exposure_2023_pct']:.1f}%")
        print(f"    setup_score buckets:")
        for b in m["setup_buckets"]:
            print(f"      {b['bucket']}: n={b['trade_count']}  "
                  f"contrib={b['return_contribution']:+.2f}  PF={b['pf']}  "
                  f"win={b['win_rate']*100:.0f}%  avgR={b['avg_r']}  "
                  f"worstR={b['worst_r']}  gap={b['gap_fill_count']}")


if __name__ == "__main__":
    main()
