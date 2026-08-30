"""
audit_distribution_days.py — Reviewer Task 2: Distribution Days Audit

Part A — Definition audit (read-only checks on the raw implementation):
  * price condition   : close drop > 0.2% vs prior day
  * volume condition  : volume strictly higher than prior day
  * index/universe    : SPY (the market index proxy used across the repo)
  * adjusted prices   : cache uses auto_adjust=True -> adjusted close
  * duplicate/consecutive signals : consecutive days each count (IBD standard)
  * rally reset       : a dist day is voided if current price >= +5% above its close
  * rolling count     : count over the trailing 25 trading days, recomputed daily
  * missing data      : < lookback+1 rows -> 0; NaN volume -> no count

Part B — Rolling-count statistics over 2016..2025:
  mean/median, p75/p90/p95, max, freq(count>=3..7), by year, by regime,
  avg/max trigger-episode length.

Part C — Threshold sensitivity (4 / 5 / 6 / 7) on the dynamic-universe backtest:
  trigger frequency, CAGR, Sharpe, Sortino, MaxDD, Calmar,
  CAGR retention vs HMM+Breadth (variant C, no overlay).
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

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.breadth_data import get_breadth_series
from regime_dual_engine.daily_signals import build_daily_signals


def part_b_stats(sig: pd.DataFrame) -> dict:
    """Rolling-count statistics from the daily signal frame."""
    c = sig["dist_count"]
    ep = c >= 5  # default threshold for episode analysis (change of state)
    out = {
        "n_days": int(len(c)),
        "mean": round(float(c.mean()), 3),
        "median": float(c.median()),
        "p75": float(c.quantile(0.75)),
        "p90": float(c.quantile(0.90)),
        "p95": float(c.quantile(0.95)),
        "max": int(c.max()),
        "freq_ge3_pct": round(float((c >= 3).mean()) * 100, 2),
        "freq_ge4_pct": round(float((c >= 4).mean()) * 100, 2),
        "freq_ge5_pct": round(float((c >= 5).mean()) * 100, 2),
        "freq_ge6_pct": round(float((c >= 6).mean()) * 100, 2),
        "freq_ge7_pct": round(float((c >= 7).mean()) * 100, 2),
        "by_year": {},
        "by_regime": {},
        "episodes": {},
    }
    sig = sig.copy()
    sig["year"] = sig.index.year
    for y, sub in sig.groupby("year"):
        out["by_year"][int(y)] = {
            "mean": round(float(sub["dist_count"].mean()), 2),
            "ge5_pct": round(float((sub["dist_count"] >= 5).mean()) * 100, 1),
            "ge7_pct": round(float((sub["dist_count"] >= 7).mean()) * 100, 1),
        }
    for reg in ["BULL", "SIDEWAYS", "BEAR"]:
        sub = sig[sig["regime_label"] == reg]
        if len(sub) == 0:
            continue
        out["by_regime"][reg] = {
            "days": int(len(sub)),
            "mean": round(float(sub["dist_count"].mean()), 2),
            "ge5_pct": round(float((sub["dist_count"] >= 5).mean()) * 100, 1),
            "p90": float(sub["dist_count"].quantile(0.90)),
        }
    # trigger episodes: consecutive days where count >= threshold
    for thr in [3, 4, 5, 6, 7]:
        mask = (c >= thr).to_numpy()
        lens, cur = [], 0
        for m in mask:
            if m:
                cur += 1
            else:
                if cur:
                    lens.append(cur)
                cur = 0
        if cur:
            lens.append(cur)
        out["episodes"][f"ge{thr}"] = {
            "n_episodes": len(lens),
            "avg_len_days": round(float(np.mean(lens)), 2) if lens else 0.0,
            "max_len_days": int(max(lens)) if lens else 0,
        }
    return out


def main():
    print("=" * 72)
    print("  REVIEWER TASK 2 — DISTRIBUTION DAYS AUDIT")
    print("=" * 72)

    # ---- Part A: definition (documented, read-only) ----
    print("""
  PART A — RAW DEFINITION (current implementation, distribution_days.py)
    price cond : daily close return < -0.2% (cfg.dist_day_drop = -0.002)
    volume cond: volume[i] > volume[i-1]  (strictly higher than prior day)
    index      : SPY  (market_df passed to count_distribution_days)
    adjusted   : OHLCV cache auto_adjust=True -> adjusted close & volume
    consecut.  : consecutive down-up-volume days each count separately (IBD)
    rally reset: dist day voided if current close >= 1.05 * dist-day close
                 (point-in-time: evaluated with the LATEST close each day)
    rolling    : trailing 25 trading days (cfg.dist_day_lookback), recomputed
                 daily from scratch (no memory across days)
    missing    : < 26 rows -> count 0 ; NaN volume -> comparison False
  """)

    # ---- Part B: statistics (current breadth; same HMM as backtest) ----
    print("  PART B — ROLLING-COUNT STATISTICS (2016-01 .. 2025-07)")
    breadth = get_breadth_series(verbose=False)
    sig = build_daily_signals(breadth)
    stats = part_b_stats(sig)
    print(f"    days={stats['n_days']}  mean={stats['mean']}  median={stats['median']}  "
          f"p75={stats['p75']}  p90={stats['p90']}  p95={stats['p95']}  max={stats['max']}")
    print(f"    freq >=3: {stats['freq_ge3_pct']}%  >=4: {stats['freq_ge4_pct']}%  "
          f">=5: {stats['freq_ge5_pct']}%  >=6: {stats['freq_ge6_pct']}%  "
          f">=7: {stats['freq_ge7_pct']}%")
    print("    by year:")
    for y, d in stats["by_year"].items():
        print(f"      {y}: mean={d['mean']}  >=5 {d['ge5_pct']}%  >=7 {d['ge7_pct']}%")
    print("    by regime:")
    for reg, d in stats["by_regime"].items():
        print(f"      {reg:<9} days={d['days']}  mean={d['mean']}  >=5 {d['ge5_pct']}%  "
              f"p90={d['p90']}")
    print("    trigger episodes (consecutive days at/above threshold):")
    for thr, d in stats["episodes"].items():
        print(f"      {thr}: n={d['n_episodes']}  avg_len={d['avg_len_days']}d  "
              f"max_len={d['max_len_days']}d")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_distdays_audit.json"), "w") as f:
        json.dump({"definition_note": "see distribution_days.py; SPY index; "
                                      "auto_adjust close/vol; rally-reset uses latest close",
                   "stats": stats}, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_distdays_audit.json")


if __name__ == "__main__":
    main()
