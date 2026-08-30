"""
validation_divergence_review.py — Reviewer Task 3: Breadth Divergence Re-Check

Previous finding: top warnings fired ~21-30 trading days before sampled tops.
The reviewer asks: is earlier ALWAYS better? Measure the opportunity cost.

For each sampled top:
  * warning date            (first BEARISH_BREADTH_DIVERGENCE before the peak)
  * actual index peak date
  * lead time (trading days)
  * index return from warning to peak        -> opportunity cost of de-risking
  * index return from warning to 20d later   -> was the warning justified?

For each sampled bottom:
  * breadth thrust date     (first BREADTH_THRUST near the trough)
  * HMM regime-shift date   (first day HMM label flips to BULL after trough)
  * verify thrust occurs BEFORE the HMM regime shift

Point-in-time everywhere; parameters are NOT tuned on these events.
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
from regime_dual_engine.daily_signals import build_daily_signals


def _trading_days(idx, later, earlier):
    if later is None or earlier is None:
        return None
    return int(idx.get_loc(later) - idx.get_loc(earlier))


def main(breadth=None, breadth_name="current", out_suffix=""):
    print("=" * 74)
    print(f"  REVIEWER TASK 3 — DIVERGENCE / THRUST BEHAVIOR  ({breadth_name} breadth)")
    print("=" * 74)
    if breadth is None:
        from regime_dual_engine.breadth_data import get_breadth_series
        breadth = get_breadth_series(verbose=False)
    sig = build_daily_signals(breadth)

    from regime_dual_engine.daily_signals import load_spy_close
    spy_close, _ = load_spy_close()
    spy_close = spy_close.reindex(sig.index)

    results = {"tops": [], "bottoms": []}

    # ---- Tops: lead time + opportunity cost ----
    tops = [("2018-Q4 top", "2018-08-01", "2019-02-15"),
            ("2021-Q4 top", "2021-10-01", "2022-03-01"),
            ("2024-Q3 top", "2024-06-01", "2024-11-01")]
    for name, w0, w1 in tops:
        win = sig.loc[w0:w1]
        px = spy_close.loc[w0:w1]
        peak_date = px.idxmax()
        div_dates = win.index[(win["veto_flags"].apply(
            lambda v: "BEARISH_BREADTH_DIVERGENCE" in v)) & (win.index <= peak_date)]
        warn_date = div_dates[0] if len(div_dates) else None
        lead = _trading_days(sig.index, peak_date, warn_date) if warn_date is not None else None
        # index return from warning close to peak close (opportunity cost)
        if warn_date is not None:
            r_to_peak = spy_close[peak_date] / spy_close[warn_date] - 1.0
            r_to_20d = spy_close.iloc[min(len(spy_close) - 1,
                spy_close.index.get_loc(warn_date) + 20)] / spy_close[warn_date] - 1.0
            r_peak_to_60d = (spy_close.iloc[min(len(spy_close) - 1,
                spy_close.index.get_loc(peak_date) + 60)] / spy_close[peak_date] - 1.0) \
                if spy_close.index.get_loc(peak_date) + 60 < len(spy_close) else None
        else:
            r_to_peak = r_to_20d = r_peak_to_60d = None
        results["tops"].append({
            "event": name, "peak_date": str(peak_date.date()),
            "warning_date": str(warn_date.date()) if warn_date is not None else None,
            "lead_trading_days": lead,
            "index_ret_warning_to_peak_pct": round(r_to_peak * 100, 2) if r_to_peak is not None else None,
            "index_ret_warning_to_20d_pct": round(r_to_20d * 100, 2) if r_to_20d is not None else None,
            "index_ret_peak_to_60d_pct": round(r_peak_to_60d * 100, 2) if r_peak_to_60d is not None else None,
        })
        print(f"\n  TOP {name}: peak={peak_date.date()}")
        print(f"    warning={warn_date.date() if warn_date is not None else 'NONE'}  "
              f"lead={lead} td")
        print(f"    SPY warning->peak: {r_to_peak*100:+.2f}%  "
              f"warning->20d: {r_to_20d*100:+.2f}%  peak->60d: "
              f"{r_peak_to_60d*100:+.2f}%" if r_to_peak is not None else "")

    # ---- Bottoms: thrust vs HMM regime shift ----
    bottoms = [("2018-Q4 bottom", "2018-11-01", "2019-04-30"),
               ("2020-COVID bottom", "2020-02-15", "2020-06-30"),
               ("2022-Q4 bottom", "2022-09-01", "2023-03-31")]
    for name, w0, w1 in bottoms:
        win = sig.loc[w0:w1]
        px = spy_close.loc[w0:w1]
        trough_date = px.idxmin()
        thrust_dates = win.index[win["veto_flags"].apply(
            lambda v: "BREADTH_THRUST" in v)]
        thrust_date = thrust_dates[0] if len(thrust_dates) else None
        thrust_off = _trading_days(sig.index, thrust_date, trough_date) if thrust_date is not None else None
        if thrust_off is not None:
            thrust_off = -thrust_off  # + = after trough
        # first BULL flip AFTER the trough
        hmm_bull = win.index[(win["hmm_label"] == "BULL") &
                             (win["hmm_label"].shift(1) != "BULL") &
                             (win.index > trough_date)]
        hmm_date = hmm_bull[0] if len(hmm_bull) else None
        hmm_off = _trading_days(sig.index, hmm_date, trough_date) if hmm_date is not None else None
        thrust_before_hmm = (thrust_date is not None and hmm_date is not None
                             and thrust_date < hmm_date)
        results["bottoms"].append({
            "event": name, "trough_date": str(trough_date.date()),
            "thrust_date": str(thrust_date.date()) if thrust_date is not None else None,
            "thrust_days_from_trough": thrust_off,
            "hmm_bull_date": str(hmm_date.date()) if hmm_date is not None else None,
            "hmm_bull_days_from_trough": hmm_off,
            "thrust_before_hmm": thrust_before_hmm,
        })
        print(f"\n  BOTTOM {name}: trough={trough_date.date()}")
        print(f"    thrust={thrust_date.date() if thrust_date is not None else 'NONE'} "
              f"({thrust_off} td)")
        print(f"    HMM->BULL={hmm_date.date() if hmm_date is not None else 'NONE'} "
              f"({hmm_off} td)  thrust_before_hmm={thrust_before_hmm}")

    # summary
    top_leads = [r["lead_trading_days"] for r in results["tops"] if r["lead_trading_days"] is not None]
    costs = [r["index_ret_warning_to_peak_pct"] for r in results["tops"] if r["index_ret_warning_to_peak_pct"] is not None]
    print("\n" + "=" * 74)
    print("  SUMMARY")
    print("=" * 74)
    print(f"  Top warnings fired: {len(top_leads)}/{len(results['tops'])}")
    print(f"  Lead (td): mean={np.mean(top_leads) if top_leads else 'N/A'}, "
          f"median={np.median(top_leads) if top_leads else 'N/A'}")
    print(f"  Opportunity cost (SPY warning->peak): "
          f"mean={np.mean(costs):+.2f}%" if costs else "")
    for r in results["tops"]:
        print(f"    {r['event']}: lead={r['lead_trading_days']}td  "
              f"cost={r['index_ret_warning_to_peak_pct']}%  "
              f"peak->60d={r['index_ret_peak_to_60d_pct']}%")
    print(f"  Thrust before HMM-BULL at bottoms: "
          f"{sum(1 for r in results['bottoms'] if r['thrust_before_hmm'])}/"
          f"{len(results['bottoms'])}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"dual_engine_divergence_review{out_suffix}.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved -> {path}")


if __name__ == "__main__":
    main()
