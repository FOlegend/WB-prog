"""
daily_signals.py — Shared daily-signal builder for the dual engine

Builds a daily DataFrame (point-in-time) with:
  hmm_label / hmm_prob          (HMM refit every REFIT trading days, forward-filled)
  breadth_pct / breadth_score   (from any breadth series: current or PIT)
  dist_count                   (rolling IBD distribution-day count on SPY)
  composite / regime / strategy / size_mult / veto_flags (dual-engine decision)

Used by: audit_distribution_days.py (by-regime trigger stats),
         validation_divergence.py (lead-time), and report scripts.
The HMM and breadth inputs are strictly point-in-time (refit-once-per-window,
rolling percentile within trailing window).
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine import hmm_engine
from regime_dual_engine.breadth_engine import breadth_percentile_score
from regime_dual_engine.distribution_days import count_distribution_days
from regime_dual_engine.regime_dual import dual_engine_regime

REFIT = 20  # HMM refit interval in trading days (matches backtest harness)


def load_spy_close():
    cache = os.path.join(os.path.dirname(_REPO_ROOT), "data", "cache", "equities", "SPY.csv")
    if not os.path.exists(cache):
        cache = os.path.join(_REPO_ROOT, "data", "cache", "equities", "SPY.csv")
    df = pd.read_csv(cache, parse_dates=["datetime"]).set_index("datetime")
    return df["close"].astype(float), df


def build_daily_signals(breadth: pd.DataFrame, cfg: DualEngineConfig = None,
                        end: str = "2025-07-31") -> pd.DataFrame:
    """Daily signal frame from a breadth series (index=datetime).

    breadth must have columns: pct_above_50dma (0-100), ad_line.
    """
    cfg = cfg or DualEngineConfig()
    spy_close, spy_df = load_spy_close()
    spy_close = spy_close[spy_close.index <= pd.Timestamp(end)]
    dates = spy_close.index

    # ---- HMM (refit every REFIT days, forward-filled) ----
    hmm_label, hmm_prob = {}, {}
    state = None
    for i, d in enumerate(dates):
        if state is None or i % REFIT == 0:
            state = hmm_engine.fit_state_labels(spy_close.iloc[:i + 1], cfg)
        if state is None:
            hmm_label[d], hmm_prob[d] = "UNKNOWN", cfg.hmm_neutral_bull_prob
            continue
        b = hmm_engine.bull_probability_from_state(state, cfg)
        hmm_label[d], hmm_prob[d] = b["hmm_regime_label"], b["hmm_bull_probability"]

    bdf = breadth[breadth.index <= pd.Timestamp(end)]
    df = pd.DataFrame({
        "hmm_label": pd.Series(hmm_label),
        "hmm_prob": pd.Series(hmm_prob),
        "breadth_pct": bdf["pct_above_50dma"],
        "ad_line": bdf["ad_line"] if "ad_line" in bdf.columns else np.nan,
    }).dropna(subset=["breadth_pct"])

    # ---- breadth percentile score (rolling 252d percentile, point-in-time) ----
    scores = []
    for i in range(len(df)):
        sub = df.iloc[:i + 1]
        ad = sub["ad_line"].dropna() if sub["ad_line"].notna().any() else None
        s = breadth_percentile_score(sub["breadth_pct"], ad, cfg)
        scores.append(s)
    df["breadth_score"] = scores

    # ---- distribution-day count on SPY (rolling 25d, point-in-time) ----
    dist = []
    for i in range(len(df)):
        d0 = df.index[i]
        sl = spy_df[spy_df.index <= d0]
        dist.append(count_distribution_days(sl, cfg))
    df["dist_count"] = dist

    # ---- dual-engine decision ----
    rows = []
    for d in df.index:
        i = df.index.get_loc(d)
        b10 = df["breadth_pct"].iloc[i - cfg.divergence_lookback] if i >= cfg.divergence_lookback else np.nan
        dec = dual_engine_regime(
            hmm_bull_prob=float(df["hmm_prob"].iloc[i]),
            hmm_regime_label=str(df["hmm_label"].iloc[i]),
            breadth_percentile=float(df["breadth_score"].iloc[i]),
            breadth_now=float(df["breadth_pct"].iloc[i]) / 100.0,
            breadth_10d_ago=float(b10) / 100.0 if not pd.isna(b10) else None,
            n_dist_days=int(df["dist_count"].iloc[i]),
            cfg=cfg,
        )
        rows.append({**dec, "date": d})
    dec_df = pd.DataFrame(rows).set_index("date")
    out = df.join(dec_df[["regime_label", "composite_score", "position_size_mult",
                          "strategy_mode", "veto_flags"]])
    out["veto_flags"] = out["veto_flags"].apply(lambda v: sorted(v))
    return out


if __name__ == "__main__":
    from regime_dual_engine.breadth_data import get_breadth_series
    b = get_breadth_series(verbose=False)
    sig = build_daily_signals(b)
    print(f"daily signals: {len(sig)} days "
          f"({sig.index.min().date()} -> {sig.index.max().date()})")
    print(sig[["hmm_label", "breadth_pct", "breadth_score", "dist_count",
               "regime_label", "position_size_mult", "veto_flags"]].tail(3).to_string())
