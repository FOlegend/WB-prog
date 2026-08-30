"""
breadth_data.py — Build Market Breadth Series from Cached OHLCV

Computes daily:
  - pct_above_50dma : % of universe stocks with close > 50-day SMA (0-100)
  - ad_line         : cumulative advance/decline line

Universe: current S&P 500 + NASDAQ 100 constituents from the historical cache.
KNOWN LIMITATION (spec §22): this uses CURRENT index constituents — survivorship
bias. Point-in-time constituents are not available in the cache. Flagged, not
silently ignored. Market-cap is not used (universe is already large-cap).

Output: regime_dual_engine/data/breadth_2016_2025.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_CACHE_DIR = os.path.join(os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
if not os.path.isdir(_CACHE_DIR):
    _CACHE_DIR = os.path.join(_REPO_ROOT, "data", "cache", "equities")

_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_OUT_PATH = os.path.join(_OUT_DIR, "breadth_2016_2025.csv")

EXCLUDE = {"SPY", "QQQ", "IWM"}  # benchmarks, not breadth constituents


def _load_closes(end: str = "2025-07-31") -> pd.DataFrame:
    """Load all ticker closes into a wide DataFrame (index=datetime, cols=tickers)."""
    frames = {}
    n_loaded = 0
    for f in sorted(os.listdir(_CACHE_DIR)):
        if not f.endswith(".csv"):
            continue
        t = f[:-4]
        if t in EXCLUDE:
            continue
        try:
            df = pd.read_csv(os.path.join(_CACHE_DIR, f), parse_dates=["datetime"])
            if len(df) < 60 or "close" not in df.columns:
                continue
            s = df.set_index("datetime")["close"].astype(float)
            s = s[~s.index.duplicated(keep="last")]
            frames[t] = s
            n_loaded += 1
        except Exception:
            continue
    panel = pd.DataFrame(frames).sort_index()
    if end:
        panel = panel[panel.index <= pd.Timestamp(end)]
    print(f"  loaded {n_loaded} tickers, {len(panel)} trading days, "
          f"{panel.index.min().date()} -> {panel.index.max().date()}")
    return panel


def build_breadth_series(sma_period: int = 50, end: str = "2025-07-31",
                         verbose: bool = True) -> pd.DataFrame:
    """Compute daily breadth series from cached OHLCV."""
    if verbose:
        print("Loading closes...")
    panel = _load_closes(end=end)
    if verbose:
        print("Computing 50DMA + A/D...")

    # % of stocks above SMA
    sma = panel.rolling(sma_period).mean()
    above = panel > sma
    n_valid = panel.notna().sum(axis=1).replace(0, np.nan)
    pct_above = (above.sum(axis=1) / n_valid * 100.0).fillna(50.0)

    # A/D line
    diff = panel.diff()
    adv = (diff > 0).fillna(False).astype(int)
    decl = (diff < 0).fillna(False).astype(int)
    net = (adv - decl).sum(axis=1)
    ad_line = net.cumsum()

    out = pd.DataFrame({
        "pct_above_50dma": pct_above.round(3),
        "ad_line": ad_line.round(3),
        "n_stocks": n_valid.astype(int),
    })
    return out


def get_breadth_series(rebuild: bool = False, end: str = "2025-07-31",
                       verbose: bool = True) -> pd.DataFrame:
    """Return the breadth series, building + caching if needed."""
    if os.path.exists(_OUT_PATH) and not rebuild:
        df = pd.read_csv(_OUT_PATH, parse_dates=["datetime"]).set_index("datetime")
        if verbose:
            print(f"  loaded cached breadth: {len(df)} days "
                  f"({df.index.min().date()} -> {df.index.max().date()})")
        return df

    os.makedirs(_OUT_DIR, exist_ok=True)
    df = build_breadth_series(end=end, verbose=verbose)
    df.reset_index().rename(columns={"index": "datetime"}).to_csv(_OUT_PATH, index=False)
    if verbose:
        print(f"  saved breadth -> {_OUT_PATH}")
    return df


if __name__ == "__main__":
    df = get_breadth_series(rebuild=True, verbose=True)
    print(df.tail(5))
