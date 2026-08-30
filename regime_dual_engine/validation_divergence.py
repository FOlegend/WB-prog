"""
validation_divergence.py — Validation Test 2: Divergence Lead Time

Evaluates whether the dual-engine gives EARLY warnings at major tops/bottoms:

  Events:
    - Q4 2018 top (2018-09-20) & bottom (2018-12-24)
    - March 2020 bottom (2020-03-23)
    - Q4 2021 top (SPY peak Dec 2021)

For each event, measure (in TRADING days):
  - breadth warning date   (BEARISH_BREADTH_DIVERGENCE fires near a top)
  - breadth thrust date    (BREADTH_THRUST fires near a bottom)
  - index peak/trough date
  - HMM regime-change date (first day HMM label flips to BULL after a bottom)

Parameters are NOT optimized on these events (spec §17).
"""
from __future__ import annotations

import os
import sys
import json

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine import hmm_engine
from regime_dual_engine.breadth_data import get_breadth_series
from regime_dual_engine.regime_dual import _breadth_thrust, _breadth_declining

CFG = DualEngineConfig()
REFIT = 20  # HMM refit every 20 trading days (matches backtest), forward-filled


def build_daily_signals():
    """Daily HMM label/prob (refit-every-20d) + breadth divergence/thrust flags."""
    spy = pd.read_csv(os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities", "SPY.csv"),
        parse_dates=["datetime"]).set_index("datetime")
    spy_close = spy["close"].astype(float)
    breadth = get_breadth_series(verbose=False)["pct_above_50dma"] / 100.0

    dates = spy_close.index
    hmm_label = {}
    hmm_prob = {}
    state = None
    for i, d in enumerate(dates):
        if state is None or i % REFIT == 0:
            state = hmm_engine.fit_state_labels(spy_close.iloc[:i + 1], CFG)
        if state is None:
            hmm_label[d] = "UNKNOWN"
            hmm_prob[d] = CFG.hmm_neutral_bull_prob
            continue
        b = hmm_engine.bull_probability_from_state(state, CFG)
        hmm_label[d] = b["hmm_regime_label"]
        hmm_prob[d] = b["hmm_bull_probability"]

    df = pd.DataFrame({"hmm_label": pd.Series(hmm_label), "hmm_prob": pd.Series(hmm_prob),
                       "breadth": breadth}).dropna()

    # divergence / thrust flags (point-in-time, 10-day lookback)
    df["breadth_10d_ago"] = df["breadth"].shift(CFG.divergence_lookback)
    df["divergence"] = df.apply(
        lambda r: (r["hmm_label"] == "BULL" and
                   not pd.isna(r["breadth_10d_ago"]) and
                   _breadth_declining(r["breadth"], r["breadth_10d_ago"])), axis=1)
    df["thrust"] = df.apply(
        lambda r: (not pd.isna(r["breadth_10d_ago"]) and
                   _breadth_thrust(r["breadth"], r["breadth_10d_ago"], CFG)), axis=1)
    # HMM turned BULL? (first day hmm_label becomes BULL after a prior non-BULL)
    df["hmm_bull_new"] = (df["hmm_label"] == "BULL") & (df["hmm_label"].shift(1) != "BULL")
    return df


def _trading_days_between(idx, d1, d2):
    if d1 not in idx or d2 not in idx:
        return None
    return int(idx.get_loc(d1) - idx.get_loc(d2))  # d1 later than d2 -> positive


def main():
    print("=" * 70)
    print("  VALIDATION TEST 2 — DIVERGENCE / THRUST LEAD TIME")
    print("=" * 70)
    df = build_daily_signals()
    print(f"  daily signals built: {len(df)} days "
          f"({df.index.min().date()} -> {df.index.max().date()})")
    spy = pd.read_csv(os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities", "SPY.csv"),
        parse_dates=["datetime"]).set_index("datetime")["close"].astype(float)
    spy = spy.reindex(df.index)

    # ---- Events ----
    # tops: (name, approx window start, window end)
    tops = [("2018-Q4 top", "2018-08-15", "2019-02-01"),
            ("2021-Q4 top", "2021-10-01", "2022-02-15")]
    bottoms = [("2018-Q4 bottom", "2018-11-01", "2019-04-01"),
               ("2020-COVID bottom", "2020-02-15", "2020-06-01")]

    results = {"tops": [], "bottoms": []}
    for name, w0, w1 in tops:
        win = df.loc[w0:w1]
        peak_date = spy.loc[w0:w1].idxmax()
        # first divergence warning within window BEFORE (or at) the peak
        div_dates = win.index[win["divergence"] & (win.index <= peak_date)]
        warn_date = div_dates[0] if len(div_dates) else None
        lead = _trading_days_between(df.index, peak_date, warn_date) if warn_date is not None else None
        results["tops"].append({"event": name, "peak_date": str(peak_date.date()),
                                "warning_date": str(warn_date.date()) if warn_date is not None else None,
                                "lead_trading_days": lead})
        print(f"\n  TOP {name}: peak={peak_date.date()}  "
              f"breadth warning={warn_date.date() if warn_date is not None else 'NONE'}  "
              f"lead={lead} trading days")

    for name, w0, w1 in bottoms:
        win = df.loc[w0:w1]
        trough_date = spy.loc[w0:w1].idxmin()
        # Thrust can fire before OR shortly after the trough (recovery confirmation).
        # Report offset in trading days: negative = before trough, positive = after.
        thrust_dates = win.index[win["thrust"]]
        thrust_date = thrust_dates[0] if len(thrust_dates) else None
        lead = _trading_days_between(df.index, trough_date, thrust_date) if thrust_date is not None else None
        if lead is not None:
            lead = -lead  # positive = days AFTER trough
        # HMM regime-change: first BULL flip AFTER the trough (genuine recovery,
        # not a stale pre-crash BULL)
        bull_dates = win.index[win["hmm_bull_new"] & (win.index > trough_date)]
        hmm_bull_date = bull_dates[0] if len(bull_dates) else None
        hmm_lead = (_trading_days_between(df.index, hmm_bull_date, trough_date)
                    if hmm_bull_date is not None else None)
        results["bottoms"].append({
            "event": name, "trough_date": str(trough_date.date()),
            "thrust_date": str(thrust_date.date()) if thrust_date is not None else None,
            "days_from_trough": lead,
            "hmm_bull_date": str(hmm_bull_date.date()) if hmm_bull_date is not None else None,
            "hmm_bull_days_after_trough": hmm_lead,
            "thrust_before_hmm": (thrust_date is not None and hmm_bull_date is not None
                                   and thrust_date < hmm_bull_date),
        })
        print(f"\n  BOTTOM {name}: trough={trough_date.date()}")
        print(f"    breadth thrust={thrust_date.date() if thrust_date is not None else 'NONE'}  "
              f"({lead} td from trough)")
        print(f"    HMM->BULL={hmm_bull_date.date() if hmm_bull_date is not None else 'NONE'}  "
              f"({hmm_lead} td after trough)  thrust_before_hmm={results['bottoms'][-1]['thrust_before_hmm']}")

    # summary stats
    top_leads = [r["lead_trading_days"] for r in results["tops"] if r["lead_trading_days"] is not None]
    bot_offsets = [r["days_from_trough"] for r in results["bottoms"] if r["days_from_trough"] is not None]
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Top warnings: {len(top_leads)}/{len(results['tops'])} fired, "
          f"lead days mean={np.mean(top_leads) if top_leads else 'N/A'}, "
          f"median={np.median(top_leads) if top_leads else 'N/A'}")
    print(f"  Bottom thrusts: {len(bot_offsets)}/{len(results['bottoms'])} fired, "
          f"days-from-trough mean={np.mean(bot_offsets) if bot_offsets else 'N/A'}, "
          f"median={np.median(bot_offsets) if bot_offsets else 'N/A'}")
    print(f"  Thrust before HMM-BULL at bottoms: "
          f"{sum(1 for r in results['bottoms'] if r.get('thrust_before_hmm'))}/"
          f"{len(results['bottoms'])}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_divergence.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Saved -> reports/dual_engine_divergence.json")


if __name__ == "__main__":
    main()
