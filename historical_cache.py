"""
historical_cache.py — Layer 1: Historical Data Cache (Dynamic Screening Backtest)

Downloads and caches OHLCV data for the full candidate universe
(S&P 500 + NASDAQ 100) + benchmarks (SPY / QQQ) to local parquet files,
so the dynamic backtest does NOT re-download 500+ tickers on every run.

Design notes / known biases (v1, per reviewer feedback):
  * SURVIVORSHIP BIAS: universe = CURRENT S&P 500 + NASDAQ 100. Only companies
    that survived to be in the index *today* are tested. Stocks that were
    delisted / acquired / dropped are absent. This biases results optimistic.
    Accepted for v1 — flag in all reports.
  * MARKET-CAP APPROXIMATION: we do NOT fetch historical market caps. The
    universe is already large-cap by construction, so screen_as_of() skips the
    market-cap filter. Using CURRENT market caps for historical dates would be
    a worse bias (pitfall #3), so we simply treat the index membership as the
    large-cap proxy.
  * NO LOOK-AHEAD in caching itself: we store full history; point-in-time
    slicing happens later in screen_as_of() via `df[df.datetime <= as_of_date]`.

Usage:
  from historical_cache import get_all_data
  all_data = get_all_data()   # builds cache if missing, returns {ticker: DataFrame}
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from src.data.data_fetcher import fetch_batch
from src.screener.screener import get_universe

warnings.filterwarnings("ignore")

CACHE_DIR = os.path.join(_REPO_ROOT, "data", "cache", "equities")
DEFAULT_START = "2018-01-01"
BENCHMARKS = ["SPY", "QQQ"]


# ---------------------------------------------------------------------------
# Cache directory helpers
# ---------------------------------------------------------------------------
def ensure_cache_dir() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return CACHE_DIR


def _ticker_path(ticker: str) -> str:
    # yfinance uses "-" for "." (e.g. BRK-B). Cache key matches ticker string.
    # CSV (not parquet) to avoid a pyarrow dependency for this workload.
    return os.path.join(CACHE_DIR, f"{ticker}.csv")


def ticker_cached(ticker: str) -> bool:
    return os.path.exists(_ticker_path(ticker))


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------
def build_universe(include_benchmarks: bool = True) -> list[str]:
    """Union of S&P 500 + NASDAQ 100 (current). Optionally add benchmarks."""
    universe = get_universe()
    if include_benchmarks:
        universe = sorted(set(universe + BENCHMARKS))
    return universe


# ---------------------------------------------------------------------------
# Download + cache
# ---------------------------------------------------------------------------
def download_and_cache(tickers: list[str], start: str = DEFAULT_START,
                       chunk_size: int = 80, verbose: bool = True,
                       max_retries: int = 2) -> dict:
    """Download tickers and save each to data/cache/equities/<TICKER>.parquet.

    Returns {ticker: n_rows} for successfully cached tickers.
    Downloads in chunks to avoid yfinance timeouts; retries each failed chunk.
    """
    ensure_cache_dir()
    cached: dict[str, int] = {}
    n = len(tickers)
    n_chunks = max(1, (n + chunk_size - 1) // chunk_size)

    for i in range(n_chunks):
        chunk = tickers[i * chunk_size:(i + 1) * chunk_size]
        data: dict = {}
        for attempt in range(max_retries + 1):
            try:
                data = fetch_batch(chunk, start=start, end="")
                break
            except Exception as exc:  # network / throttle
                if verbose:
                    print(f"    chunk {i + 1} attempt {attempt + 1} failed: {exc}")
                time.sleep(2.0 * (attempt + 1))
                data = {}

        for t, df in data.items():
            if df is None or len(df) < 5:
                continue
            try:
                df.to_csv(_ticker_path(t), index=False)
                cached[t] = len(df)
            except Exception:
                pass

        if verbose:
            done = min((i + 1) * chunk_size, n)
            print(f"  chunk {i + 1}/{n_chunks}: cached {len(cached)}/{done} so far")
        # polite pause to reduce yfinance throttling
        if i < n_chunks - 1:
            time.sleep(1.0)

    if verbose:
        print(f"  cached {len(cached)}/{n} tickers -> {CACHE_DIR}")
    return cached


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_cache(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Load cached parquet files. Returns {ticker: DataFrame}. Skips misses."""
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        p = _ticker_path(t)
        if os.path.exists(p):
            try:
                df = pd.read_csv(p, parse_dates=["datetime"])
                if len(df) > 0:
                    out[t] = df
            except Exception:
                pass
    return out


def get_all_data(start: str = DEFAULT_START, end: str = "",
                 force_refresh: bool = False, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """Build universe; download if cache incomplete; load all cached data.

    Returns full in-memory {ticker: DataFrame} (columns: datetime/open/high/
    low/close/volume). Optionally trims to `end` (inclusive).
    """
    universe = build_universe()

    if force_refresh:
        if verbose:
            print("Force refresh — re-downloading full universe...")
        download_and_cache(universe, start=start)
    else:
        existing = [t for t in universe if ticker_cached(t)]
        if len(existing) < len(universe) * 0.8:
            if verbose:
                print(f"Cache incomplete ({len(existing)}/{len(universe)}). "
                      f"Downloading missing tickers...")
            missing = [t for t in universe if not ticker_cached(t)]
            download_and_cache(missing, start=start)
        elif verbose:
            print(f"Cache complete ({len(existing)}/{len(universe)} tickers).")

    data = load_cache(universe)
    if end:
        cutoff = pd.Timestamp(end)
        data = {t: df[df["datetime"] <= cutoff].reset_index(drop=True)
                for t, df in data.items()}
    if verbose:
        print(f"Loaded {len(data)} tickers into memory.")
    return data


if __name__ == "__main__":
    print("=== Building historical cache (Layer 1) ===")
    t0 = time.time()
    data = get_all_data()
    print(f"Done in {time.time() - t0:.1f}s. {len(data)} tickers loaded.")
