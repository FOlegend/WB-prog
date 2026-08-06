"""
screen_as_of.py — Layer 3: Point-in-Time Screener (Dynamic Screening Backtest)

Runs the 6-filter screening pipeline AS OF a specific historical date, using
ONLY data available on or before that date (strictly no look-ahead).

This is the core of "dynamic screening": at each rebalance date we pretend we
are that day, screen with only the data we would have had, and trade the
resulting universe in the following window.

Compared to src/screener/screener.screen() (the live daily screener):
  * This function takes an in-memory `all_data` dict (from historical_cache)
    instead of hitting the network.
  * It slices every ticker to `datetime <= as_of_date` BEFORE computing any
    indicator, so a 2024-03-31 screen literally cannot see an April print.
  * Market-cap filter is SKIPPED: the universe is already large-cap by
    construction (current S&P 500 + NASDAQ 100). We deliberately avoid pulling
    CURRENT market caps for historical dates (pitfall #3 — worse bias than the
    approximation we already accept via membership).

Filters applied (point-in-time):
  2. 20-day Avg Dollar Volume > $50M   (close * volume, rolled on <=as_of)
  3. Close Price > $10
  4. 14-day ATR% > 2.0%
  5. RS vs SPY > 1.0  (date-aligned, multi-TF composite)
  6. 14-day ADX > 20  (trend strength)

Returns the top_n tickers sorted by RS descending.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from src.indicators.technicals import atr as calc_atr, adx as calc_adx
# Reuse the exact RS alignment logic from the live screener to stay consistent.
from src.screener.screener import _compute_rs_composite

warnings.filterwarnings("ignore")


def screen_as_of(all_data: dict[str, pd.DataFrame], as_of_date: str,
                 cfg: Config | None = None, benchmark: str = "SPY",
                 top_n: int = 20, verbose: bool = False) -> list[str]:
    """Point-in-time screening. Returns list of top_n tickers as of as_of_date.

    Parameters
    ----------
    all_data : dict
        {ticker: full OHLCV DataFrame} (from historical_cache.get_all_data).
    as_of_date : str
        Rebalance date. ONLY data with datetime <= as_of_date is used.
    cfg : Config or None
        Reads screener_* thresholds. None -> Config().
    benchmark : str
        Benchmark ticker for RS (default SPY).
    top_n : int
        Number of candidates to return.

    Returns
    -------
    list[str]  — top_n tickers by RS (empty if insufficient data).
    """
    if cfg is None:
        cfg = Config()

    # Resolve thresholds (mirror screener.screen() resolution)
    min_dvol    = cfg.screener_min_dollar_vol
    min_price   = cfg.screener_min_price
    min_atr_pct = cfg.screener_min_atr_pct
    min_adx     = getattr(cfg, "screener_min_adx", 0.0)
    min_rs      = cfg.screener_min_rs_ratio
    rs_lookbacks = list(getattr(cfg, "screener_rs_lookbacks", [50]))
    rs_weights   = list(getattr(cfg, "screener_rs_weights", [1.0]))
    dvol_window = cfg.screener_dvol_window
    atr_period  = cfg.screener_atr_period
    adx_period  = cfg.screener_adx_period

    while len(rs_weights) < len(rs_lookbacks):
        rs_weights.append(0.0)
    max_rs_lb = max(rs_lookbacks) if rs_lookbacks else 50
    min_bars = max(max_rs_lb + 5, dvol_window + 5, atr_period * 3, adx_period * 3)

    as_of = pd.Timestamp(as_of_date)
    spy_df = all_data.get(benchmark)
    if spy_df is None or len(spy_df) == 0:
        if verbose:
            print(f"    [screen_as_of {as_of_date}] no {benchmark} data")
        return []
    spy_close = spy_df[spy_df["datetime"] <= as_of]["close"].astype(float)
    if len(spy_close) < max_rs_lb + 1:
        if verbose:
            print(f"    [screen_as_of {as_of_date}] {benchmark} history too short "
                  f"({len(spy_close)} < {max_rs_lb + 1})")
        return []

    rows = []
    for t, df in all_data.items():
        if t == benchmark:
            continue
        # ---- POINT-IN-TIME SLICE: this is the whole point ----
        sub = df[df["datetime"] <= as_of]
        if len(sub) < min_bars:
            continue
        try:
            close  = sub["close"].astype(float)
            high   = sub["high"].astype(float)
            low    = sub["low"].astype(float)
            volume = sub["volume"].astype(float)

            price = float(close.iloc[-1])

            # Filter 3: Price > $10
            if np.isnan(price) or price <= min_price:
                continue

            # Filter 2: 20-day Avg Dollar Volume > $50M
            dvol_series = (close * volume).rolling(dvol_window).mean()
            avg_dvol = float(dvol_series.iloc[-1])
            if np.isnan(avg_dvol) or avg_dvol <= min_dvol:
                continue

            # Filter 4: 14-day ATR% > 2.0%
            atr_series = calc_atr(sub, period=atr_period)
            atr_val = float(atr_series.iloc[-1]) if len(atr_series) else float("nan")
            if np.isnan(atr_val) or atr_val <= 0:
                continue
            atr_pct = (atr_val / price) * 100.0
            if atr_pct <= min_atr_pct:
                continue

            # Filter 5: RS vs SPY > 1.0 (date-aligned, multi-TF composite)
            composite, _ = _compute_rs_composite(close, spy_close, rs_lookbacks, rs_weights)
            if composite is None or np.isnan(composite) or composite <= min_rs:
                continue

            # Filter 6: ADX > 20 (trend strength)
            adx_df = calc_adx(sub, period=adx_period)
            adx_val = float(adx_df["adx"].iloc[-1]) if (not adx_df.empty and "adx" in adx_df) else 0.0
            if np.isnan(adx_val):
                adx_val = 0.0
            if min_adx > 0 and adx_val < min_adx:
                continue

        except Exception:
            continue

        rows.append({
            "ticker": t,
            "rs": float(composite),
            "price": round(price, 2),
            "atr_pct": round(atr_pct, 2),
            "adx": round(adx_val, 1),
            "dvol_m": round(avg_dvol / 1e6, 1),
        })

    if not rows:
        if verbose:
            print(f"    [screen_as_of {as_of_date}] no tickers passed filters")
        return []

    df_rows = pd.DataFrame(rows)
    df_rows = df_rows.sort_values("rs", ascending=False).reset_index(drop=True)
    if len(df_rows) > top_n:
        df_rows = df_rows.head(top_n)

    if verbose:
        print(f"    [screen_as_of {as_of_date}] {len(df_rows)} candidates "
              f"(top RS={df_rows['rs'].iloc[0]:.3f})")
    return df_rows["ticker"].tolist()


def screen_all_buckets(all_data: dict[str, pd.DataFrame],
                        rebalance_dates: list, cfg: Config | None = None,
                        top_n: int = 20, benchmark: str = "SPY",
                        verbose: bool = True) -> dict:
    """Screen every rebalance date once. Returns {rebalance_date: [tickers]}."""
    if cfg is None:
        cfg = Config()
    out: dict = {}
    for i, rd in enumerate(rebalance_dates):
        rd_str = pd.Timestamp(rd).strftime("%Y-%m-%d")
        sel = screen_as_of(all_data, rd_str, cfg, benchmark=benchmark, top_n=top_n)
        out[rd_str] = sel
        if verbose:
            print(f"  bucket {i + 1}/{len(rebalance_dates)} {rd_str}: "
                  f"{len(sel)} selected")
    return out


if __name__ == "__main__":
    # Quick self-test: screen as of a fixed historical date.
    print("=== screen_as_of self-test ===")
    from historical_cache import get_all_data
    data = get_all_data(end="2024-04-01")
    sel = screen_as_of(data, "2024-03-31", top_n=20)
    print("Top 20 as of 2024-03-31:")
    print(sel)
