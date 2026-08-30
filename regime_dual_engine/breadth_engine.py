"""
breadth_engine.py — Engine B: Market Breadth Health Engine (50% weight)

Primary inputs (from breadth_data.py daily series):
  - pct_above_50dma : % of stocks with close > 50-day SMA (0-100)
  - ad_line         : cumulative advance/decline line

The breadth percentile score (0-100) combines:
  - percentile rank of current % above 50DMA within trailing window (0-100)
  - percentile rank of A/D line 10-day momentum within trailing window (0-100)

Architectural rule: breadth represents market INTERNAL health. No index-level
MA trend / KER / ADX components are introduced here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _percentile_rank(series: pd.Series, current: float) -> float:
    """Percentile rank of `current` within `series` (0-100)."""
    if len(series) == 0:
        return 50.0
    return float((series < current).mean() * 100.0)


def ad_momentum(ad_line: pd.Series, lookback: int = 10) -> float | None:
    """A/D line momentum = relative ROC over `lookback` days.

    Returns None if insufficient data (caller falls back to neutral).
    """
    if ad_line is None or len(ad_line) < lookback + 1:
        return None
    prev = float(ad_line.iloc[-1 - lookback])
    cur = float(ad_line.iloc[-1])
    if abs(prev) < 1e-12:
        # flat base: use absolute change normalized by series scale
        scale = float(ad_line.abs().mean()) + 1e-12
        return (cur - prev) / scale
    return (cur - prev) / abs(prev)


def breadth_percentile_score(pct_above: pd.Series, ad_line: pd.Series | None,
                             cfg) -> float:
    """Compute Engine B score (0-100) from the daily breadth series.

    Parameters
    ----------
    pct_above : pd.Series
        Daily % of stocks above 50DMA (index = datetime).
    ad_line : pd.Series | None
        Daily cumulative A/D line (index = datetime). None -> A/D weight 0.
    cfg : DualEngineConfig
    """
    if pct_above is None or len(pct_above) < 2:
        return 50.0  # neutral

    cur_pct = float(pct_above.iloc[-1])
    window = pct_above.iloc[-cfg.breadth_percentile_lookback:]
    pct_above_percentile = _percentile_rank(window, cur_pct)

    if ad_line is not None and cfg.breadth_ad_momentum_weight > 0:
        ad_window = ad_line.iloc[-cfg.breadth_percentile_lookback:]
        mom = ad_momentum(ad_line, cfg.breadth_ad_momentum_lookback)
        if mom is None:
            ad_percentile = 50.0
        else:
            # percentile of current 10d momentum within the trailing window of
            # 10d momentums
            moments = ad_line.diff(cfg.breadth_ad_momentum_lookback) \
                             .dropna().iloc[-cfg.breadth_percentile_lookback:]
            ad_percentile = _percentile_rank(moments, mom)
        score = (cfg.breadth_pct_above_weight * pct_above_percentile +
                 cfg.breadth_ad_momentum_weight * ad_percentile)
    else:
        score = pct_above_percentile

    return round(max(0.0, min(100.0, score)), 3)
