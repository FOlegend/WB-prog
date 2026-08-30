"""
distribution_days.py — Tactical Risk Overlay (circuit breaker, NOT a regime engine)

Distribution Days are strictly a tactical risk overlay:
  - rolling 25-trading-day count of IBD distribution days
  - count >= 5 -> hard position-size cap (0.50)
  - NEVER participates in composite_score or regime_label

Reuses the validated O'Neil logic (close drops > 0.2% on higher volume, with
5% rally reset voiding a distribution day).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def count_distribution_days(df: pd.DataFrame, cfg) -> int:
    """Count IBD distribution days in the last `lookback` trading days.

    A distribution day = close drops > dist_day_drop (default -0.2%) on volume
    higher than the previous day. A 5% rally above a distribution day's close
    voids it (O'Neil rule).
    """
    lookback = cfg.dist_day_lookback
    drop_thr = cfg.dist_day_drop
    rally_reset = cfg.dist_day_rally_reset
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    if len(close) < lookback + 1 or len(volume) < lookback + 1:
        return 0

    recent_close = close.iloc[-lookback:]
    recent_vol = volume.iloc[-lookback:]
    current_price = float(close.iloc[-1])

    count = 0
    for i in range(1, len(recent_close)):
        daily_ret = recent_close.iloc[i] / recent_close.iloc[i - 1] - 1.0
        if daily_ret < drop_thr and recent_vol.iloc[i] > recent_vol.iloc[i - 1]:
            dist_day_close = float(recent_close.iloc[i])
            if current_price >= dist_day_close * (1.0 + rally_reset):
                continue  # voided by rally
            count += 1
    return count


def distribution_day_cap(n_dist: int, cfg, current_mult: float) -> tuple[float, list]:
    """Apply the hard cap if count >= threshold.

    Returns (capped_mult, veto_flags).
    """
    if n_dist >= cfg.dist_day_cap_threshold:
        capped = min(current_mult, cfg.dist_day_cap)
        return capped, ["DISTRIBUTION_DAY_CAP"]
    return current_mult, []
