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


# ---------------------------------------------------------------------------
# PIT guard (data-integrity contract — see reports/pit_breadth_leak_2026-10-01.md)
# ---------------------------------------------------------------------------
def slice_to_end(df: pd.DataFrame, end: str | None, *,
                 name: str = "breadth") -> pd.DataFrame:
    """Keep only observations with date <= `end`. THE PIT CONTRACT.

    Any loader asked for `end=as_of` must never hand back an observation later
    than `as_of`. Historically the cached-CSV path ignored `end` entirely, so a
    historical replay silently read the 2025-07-31 tail (percentile 41.746) for
    every as-of date — a look-ahead in Regime v1's breadth engine. This helper
    is the single place that guarantees the restriction; it is applied to the
    cache path AND the rebuild path, in both `get_breadth` (PIT series) and
    `get_breadth_series` (current-constituent fallback).

    Date semantics (unchanged from the existing convention): `end` is a US
    trading-session date `YYYY-MM-DD`. A non-trading `end` resolves to the most
    recent observation at or before it. An `end` before the series start yields
    an EMPTY frame on purpose — callers must treat that as a data failure, never
    as "breadth is neutral".

    Raises TypeError rather than returning future data if the frame carries
    neither a DatetimeIndex nor a `datetime` column (fail loud, never leak).
    """
    if df is None or len(df) == 0 or end is None:
        return df
    ts = pd.Timestamp(end)
    if isinstance(df.index, pd.DatetimeIndex):
        return df[df.index <= ts]
    if "datetime" in df.columns:
        mask = pd.to_datetime(df["datetime"]) <= ts
        return df[mask]
    raise TypeError(
        f"{name}: cannot apply the point-in-time slice — the frame has neither "
        f"a DatetimeIndex nor a 'datetime' column")


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
    """Return the breadth series, building + caching if needed.

    PIT contract: the returned frame never contains an observation after `end`
    (see `slice_to_end`). `end=None` returns the full cached series.
    """
    if os.path.exists(_OUT_PATH) and not rebuild:
        df = slice_to_end(
            pd.read_csv(_OUT_PATH, parse_dates=["datetime"]).set_index("datetime"),
            end, name="get_breadth_series(cache)")
        if verbose:
            span = (f"{df.index.min().date()} -> {df.index.max().date()}"
                    if len(df) else "EMPTY")
            print(f"  loaded cached breadth: {len(df)} days ({span}) "
                  f"[PIT end={end}]")
        return df

    os.makedirs(_OUT_DIR, exist_ok=True)
    df = build_breadth_series(end=end, verbose=verbose)
    # write the FULL built frame to the cache, but return only up to `end`
    df.reset_index().rename(columns={"index": "datetime"}).to_csv(_OUT_PATH, index=False)
    if verbose:
        print(f"  saved breadth -> {_OUT_PATH}")
    return slice_to_end(df, end, name="get_breadth_series(rebuild)")


if __name__ == "__main__":
    df = get_breadth_series(rebuild=True, verbose=True)
    print(df.tail(5))
