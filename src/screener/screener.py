"""
screener.py — Pre-Market Screening Pipeline for Daily Swing Trading

Screens ALL large-cap US stocks (S&P 500 + NASDAQ 100, ~550 tickers) against
6 institutional-grade criteria every morning:

  1. Market Cap          > $10 Billion USD
  2. 20-day Dollar Vol   > $50 Million/day   (Rolling_Mean(Close * Volume, 20))
  3. Close Price         > $10.00
  4. 14-day ATR%         > 2.0%             (ATR_14 / Close * 100)
  5. RS vs SPY           > 1.0              (multi-TF composite, date-aligned)
  6. 14-day ADX          > 20               (trend strength — NEW filter)

Output: cleaned ticker list + formatted metrics table
        (Price, Dollar Volume, ATR%, RS per timeframe, ADX, Market Cap).

Usage:
  python src/screener/screener.py                # standalone (直接跑檔案)
  python -m src.screener.screener                # standalone (module 模式)
  python main.py                                 # via daily briefing
  python main.py --tickers NVDA,AMD,META         # skip screener
"""
from __future__ import annotations

import os
import sys
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- repo-root bootstrap -----------------------------------------------------
# 讓這個檔案「直接執行」(python src/screener/screener.py 或 IDE 的 Run 按鈕)
# 時也能 import 到 src.* 套件；用 -m 或被 main.py import 時這段是 no-op。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# -----------------------------------------------------------------------------

import numpy as np
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from tabulate import tabulate

from src.indicators.technicals import atr as calc_atr, adx as calc_adx
from src.data.data_fetcher import fetch_batch


# ---------------------------------------------------------------------------
# Fallback universe (used if Wikipedia scraping fails)
# ---------------------------------------------------------------------------
FALLBACK_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "AMD",
    "NFLX", "CRM", "ADBE", "INTC", "QCOM", "TXN", "CSCO", "ACN", "ORCL",
    "JPM", "BAC", "WFC", "GS", "MS", "C", "USB", "PNC",
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT",
    "XOM", "CVX", "COP", "SLB", "EOG", "PSX",
    "COST", "WMT", "HD", "LOW", "TGT", "AMGN", "DHR",
    "DIS", "NKE", "MCD", "SBUX", "BA", "CAT", "GE", "HON",
    "NIO", "PLUG", "F", "T", "BBD", "VALE", "PBR",
]

# Backward-compat alias (main.py imports this as fallback)
UNIVERSE = FALLBACK_UNIVERSE


# ---------------------------------------------------------------------------
# Universe builders — scrape S&P 500 + NASDAQ 100 from Wikipedia (free, no key)
# ---------------------------------------------------------------------------

# Wikipedia blocks bare requests — use a browser User-Agent
_WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


def _scrape_sp500() -> list[str]:
    """Scrape current S&P 500 ticker list from Wikipedia.

    Dynamically searches all tables for one containing a 'Symbol' or 'Ticker'
    column, rather than hardcoding tables[0] — Wikipedia frequently reorders
    tables (e.g. adding a 'Selected changes' table above the main list).
    """
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    resp = requests.get(url, headers=_WIKI_HEADERS, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))

    for tbl in tables:
        for col in ("Symbol", "Ticker"):
            if col in tbl.columns:
                tickers = tbl[col].astype(str).str.strip().tolist()
                # yfinance uses "-" where Wikipedia uses "." (e.g. BRK.B -> BRK-B)
                return [t.replace(".", "-") for t in tickers if t and t != "nan"]

    raise ValueError("Could not find S&P 500 ticker table on Wikipedia")


def _scrape_nasdaq100() -> list[str]:
    """Scrape current NASDAQ 100 ticker list from Wikipedia.

    The components table lives on a separate page
    (https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies), not on the
    main Nasdaq-100 article.  Same dynamic table-search approach as
    _scrape_sp500() for robustness against layout changes.
    """
    url = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
    resp = requests.get(url, headers=_WIKI_HEADERS, timeout=15)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))

    for tbl in tables:
        for col in ("Ticker", "Symbol", "Ticker symbol"):
            if col in tbl.columns:
                tickers = tbl[col].astype(str).str.strip().tolist()
                return [t.replace(".", "-") for t in tickers if t and t != "nan"]

    raise ValueError("Could not find NASDAQ 100 ticker table on Wikipedia")


def get_universe() -> list[str]:
    """Union of S&P 500 + NASDAQ 100 (deduplicated, sorted)."""
    sp500 = _scrape_sp500()
    ndx100 = _scrape_nasdaq100()
    universe = sorted(set(sp500 + ndx100))
    if not universe:
        return FALLBACK_UNIVERSE
    return universe


# ---------------------------------------------------------------------------
# Batch download helper — chunk large ticker lists to avoid yfinance timeouts
# ---------------------------------------------------------------------------

def _download_chunked(tickers: list[str], period: str = "4mo",
                      chunk_size: int = 100) -> dict:
    """Download daily data in chunks and merge into a single dict."""
    merged: dict[str, pd.DataFrame] = {}
    n_chunks = (len(tickers) + chunk_size - 1) // chunk_size
    for i in range(n_chunks):
        chunk = tickers[i * chunk_size: (i + 1) * chunk_size]
        data = fetch_batch(chunk, period=period)
        merged.update(data)
        if n_chunks > 1:
            print(f"    chunk {i+1}/{n_chunks} done ({len(chunk)} tickers)")
    return merged


# ---------------------------------------------------------------------------
# Market cap fetch — parallelised with ThreadPoolExecutor
# ---------------------------------------------------------------------------

def _fetch_market_caps_parallel(tickers: list[str],
                                max_workers: int = 8) -> dict[str, float]:
    """Fetch market cap for multiple tickers concurrently.

    Replaces the old sequential loop that made N blocking HTTP calls one by
    one (10-25s for 80 survivors). With 8 workers the same batch finishes
    in ~2-3s.  yf.Ticker().fast_info is a lightweight REST call, so
    ThreadPoolExecutor is the correct concurrency model (I/O-bound).

    Note: We intentionally fetch market cap *after* the price/ATR/RS filters
    reduce the universe from ~500 to ~80, rather than fetching market cap
    for all 500 first.  This is because yf.download() batches price data
    efficiently (1 HTTP call per 100 tickers), whereas market cap has no
    batch endpoint — so the fewer calls, the better.
    """
    results: dict[str, float] = {}

    def _fetch_one(ticker: str) -> tuple[str, float | None]:
        try:
            mc = yf.Ticker(ticker).fast_info["market_cap"]
            return ticker, float(mc) if mc else None
        except Exception:
            return ticker, None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, mc = future.result()
            if mc is not None:
                results[ticker] = mc

    return results


# ---------------------------------------------------------------------------
# RS calculation — date-aligned, multi-timeframe composite
# ---------------------------------------------------------------------------

def _compute_rs_aligned(stock_close: pd.Series,
                        spy_close: pd.Series,
                        lookback: int) -> float | None:
    """Compute RS ratio with explicit date alignment.

    Aligns stock and SPY on common trading dates before computing returns.
    This prevents index-mismatch bugs when a stock has trading halts, IPO
    date offsets, or different listing calendars.

    RS = (1 + stock_return) / (1 + spy_return)
    — a value > 1.0 means the stock outperformed SPY over the lookback.
    Guards against SPY near-crash (return <= -99%) where the ratio would
    explode to infinity.
    """
    common_dates = stock_close.index.intersection(spy_close.index)
    if len(common_dates) < lookback + 1:
        return None

    stock_aligned = stock_close.loc[common_dates]
    spy_aligned = spy_close.loc[common_dates]

    stock_ret = stock_aligned.iloc[-1] / stock_aligned.iloc[-lookback] - 1.0
    spy_ret = spy_aligned.iloc[-1] / spy_aligned.iloc[-lookback] - 1.0

    # Guard: SPY lost >99% (extreme crash) — ratio meaningless
    if (1.0 + spy_ret) <= 0.01:
        return None

    rs_ratio = (1.0 + stock_ret) / (1.0 + spy_ret)
    val = float(rs_ratio)
    if np.isnan(val) or np.isinf(val) or val <= 0:
        return None
    return val


def _compute_rs_composite(stock_close: pd.Series,
                          spy_close: pd.Series,
                          lookbacks: list[int],
                          weights: list[float]) -> tuple[float | None, dict[int, float]]:
    """Compute weighted RS composite across multiple timeframes.

    Returns (composite_score, {lookback: rs_value}).
    Weights are normalised to the available timeframes — if a stock doesn't
    have enough history for RS_200 (e.g. recent IPO), the composite is
    computed from RS_50 and RS_100 only with re-normalised weights.
    """
    rs_values: dict[int, float] = {}
    for lb in lookbacks:
        rs = _compute_rs_aligned(stock_close, spy_close, lb)
        if rs is not None:
            rs_values[lb] = rs

    if not rs_values:
        return None, {}

    # Normalise weights to available timeframes
    active_weight = 0.0
    weighted_sum = 0.0
    for i, lb in enumerate(lookbacks):
        if lb in rs_values:
            w = weights[i] if i < len(weights) else 0.0
            weighted_sum += rs_values[lb] * w
            active_weight += w

    if active_weight == 0:
        # All weights were zero — fall back to simple average
        return float(np.mean(list(rs_values.values()))), rs_values

    composite = weighted_sum / active_weight
    return float(composite), rs_values


# ---------------------------------------------------------------------------
# Core screening pipeline
# ---------------------------------------------------------------------------

def screen(cfg=None, tickers=None, period: str | None = None,
           use_rs_top_pct: bool | None = None) -> pd.DataFrame:
    """
    Run the full 6-filter pre-market screening pipeline.

    Parameters
    ----------
    cfg : Config or None
        If provided, reads thresholds from cfg.screener_* fields.
        If None, uses module-level defaults.
    tickers : list[str] or None
        Override universe. If None, scrapes S&P 500 + NASDAQ 100.
    period : str or None
        yfinance period. If None, auto-computed from the longest RS lookback
        (e.g. RS_50 -> "4mo", RS_200 -> "11mo").
    use_rs_top_pct : bool or None
        If True, use top 20% RS filter instead of absolute > 1.0.
        If None, reads from cfg.screener_use_rs_top_pct (default False).

    Returns
    -------
    pd.DataFrame
        Columns: ticker, price, dollar_volume_m, atr_pct,
        rs_<lookback> (one per timeframe), rs_score (if multi-TF),
        adx, market_cap_b. Sorted by rs_score (or rs_50 if single-TF)
        descending.
    """
    # ---- Resolve thresholds ----
    if cfg is not None:
        min_mcap     = cfg.screener_min_market_cap
        min_dvol     = cfg.screener_min_dollar_vol
        min_price    = cfg.screener_min_price
        min_atr_pct  = cfg.screener_min_atr_pct
        min_adx      = getattr(cfg, "screener_min_adx", 0.0)
        min_rs       = cfg.screener_min_rs_ratio
        rs_lookbacks = getattr(cfg, "screener_rs_lookbacks", [50])
        rs_weights   = getattr(cfg, "screener_rs_weights", [1.0])
        rs_top_pct   = cfg.screener_rs_top_pct
        atr_period   = cfg.screener_atr_period
        adx_period   = cfg.screener_adx_period
        dvol_window  = cfg.screener_dvol_window
        top_n        = cfg.screener_top_n
        if use_rs_top_pct is None:
            use_rs_top_pct = cfg.screener_use_rs_top_pct
    else:
        min_mcap     = 10e9
        min_dvol     = 50e6
        min_price    = 10.0
        min_atr_pct  = 2.0
        min_adx      = 20.0
        min_rs       = 1.0
        rs_lookbacks = [50]
        rs_weights   = [1.0]
        rs_top_pct   = 0.20
        atr_period   = 14
        adx_period   = 14
        dvol_window  = 20
        top_n        = 30
        if use_rs_top_pct is None:
            use_rs_top_pct = False

    # Ensure lists
    if isinstance(rs_lookbacks, (int, float)):
        rs_lookbacks = [int(rs_lookbacks)]
    if isinstance(rs_weights, (int, float)):
        rs_weights = [float(rs_weights)]
    # Pad weights if shorter than lookbacks
    while len(rs_weights) < len(rs_lookbacks):
        rs_weights.append(0.0)

    multi_tf = len(rs_lookbacks) > 1
    max_rs_lb = max(rs_lookbacks) if rs_lookbacks else 50

    # Auto-compute period from longest RS lookback
    if period is None:
        needed_bars = max_rs_lb + 15  # buffer for rolling windows
        needed_months = max(4, math.ceil(needed_bars / 21))
        period = f"{needed_months}mo"

    # Minimum data length needed
    min_bars = max(max_rs_lb + 5, dvol_window + 5, atr_period * 3, adx_period * 3)

    # Filter count for progress display
    n_filters = 6 if min_adx > 0 else 5
    filter_label = "6-filter" if n_filters == 6 else "5-filter"

    # ---- Step 1: Build universe ----
    if tickers is None:
        print(f"  [1/{n_filters+1}] Building universe (S&P 500 + NASDAQ 100)...")
        try:
            universe = get_universe()
            print(f"        {len(universe)} tickers scraped")
        except Exception as exc:
            print(f"        Wikipedia scrape failed ({exc}), using fallback list")
            universe = FALLBACK_UNIVERSE
    else:
        universe = list(tickers)

    # Always include SPY for RS calculation
    all_tickers = sorted(set(universe + ["SPY"]))

    # ---- Step 2: Batch download ----
    print(f"  [2/{n_filters+1}] Downloading {len(all_tickers)} tickers ({period} daily)...")
    data = _download_chunked(all_tickers, period=period)

    # ---- Step 3: Compute metrics + apply filters 3-6 ----
    print(f"  [3/{n_filters+1}] Computing metrics (ATR%, Dollar Vol, RS, ADX)...")

    # SPY benchmark
    spy_df = data.get("SPY")
    if spy_df is None or len(spy_df) < max_rs_lb + 1:
        print("        SPY data insufficient — cannot compute RS. Aborting.")
        return pd.DataFrame()
    spy_close = spy_df["close"].astype(float)

    rows = []
    for t in universe:
        if t == "SPY":
            continue
        df = data.get(t)
        if df is None or len(df) < min_bars:
            continue

        try:
            close   = df["close"].astype(float)
            high    = df["high"].astype(float)
            low     = df["low"].astype(float)
            volume  = df["volume"].astype(float)

            price = float(close.iloc[-1])

            # Filter 3: Price > $10
            if np.isnan(price) or price <= min_price:
                continue

            # Filter 2: 20-day Avg Dollar Volume > $50M
            dollar_vol_series = (close * volume).rolling(dvol_window).mean()
            avg_dvol = float(dollar_vol_series.iloc[-1])
            if np.isnan(avg_dvol) or avg_dvol <= min_dvol:
                continue

            # Filter 4: 14-day ATR% > 2.0%
            atr_series = calc_atr(df, period=atr_period)
            atr_val = float(atr_series.iloc[-1])
            if np.isnan(atr_val) or atr_val <= 0:
                continue
            atr_pct = (atr_val / price) * 100.0
            if atr_pct <= min_atr_pct:
                continue

            # Filter 5: RS vs SPY (multi-timeframe, date-aligned)
            composite, rs_dict = _compute_rs_composite(
                close, spy_close, rs_lookbacks, rs_weights
            )
            if composite is None or np.isnan(composite):
                continue
            # RS absolute filter is applied later (after top-pct option)

            # Filter 6: ADX > 20 (trend strength) — NEW
            adx_df = calc_adx(df, period=adx_period)
            adx_val = float(adx_df["adx"].iloc[-1]) if not adx_df.empty else 0.0
            if np.isnan(adx_val):
                adx_val = 0.0
            if min_adx > 0 and adx_val < min_adx:
                continue

        except Exception:
            continue

        row = {
            "ticker": t,
            "price": round(price, 2),
            "dollar_volume_m": round(avg_dvol / 1e6, 1),
            "atr_pct": round(atr_pct, 2),
            "adx": round(adx_val, 1),
        }
        # Add per-timeframe RS columns
        for lb in rs_lookbacks:
            row[f"rs_{lb}"] = round(rs_dict.get(lb, 0.0), 4)
        # Add composite score if multi-timeframe
        if multi_tf:
            row["rs_score"] = round(composite, 4)

        rows.append(row)

    if not rows:
        print("        No tickers passed price/vol/ATR/RS/ADX filters.")
        return pd.DataFrame()

    df_results = pd.DataFrame(rows)

    # Determine which column to use for RS filtering/sorting
    rs_sort_col = "rs_score" if multi_tf else f"rs_{rs_lookbacks[0]}"

    # Apply RS filter (absolute > 1.0 OR top 20%)
    if use_rs_top_pct:
        cutoff = df_results[rs_sort_col].quantile(1 - rs_top_pct)
        df_results = df_results[df_results[rs_sort_col] >= cutoff].copy()
        print(f"        RS top {rs_top_pct:.0%} cutoff = {cutoff:.4f} (by {rs_sort_col})")
    else:
        df_results = df_results[df_results[rs_sort_col] > min_rs].copy()
        print(f"        RS > {min_rs} filter applied (by {rs_sort_col})")

    if df_results.empty:
        print("        No tickers passed RS filter.")
        return pd.DataFrame()

    # ---- Step 4: Market Cap filter (parallelised) ----
    survivors = df_results["ticker"].tolist()
    print(f"  [4/{n_filters+1}] Fetching Market Cap for {len(survivors)} survivors (parallel)...")
    mcap_dict = _fetch_market_caps_parallel(survivors, max_workers=8)
    print(f"        Got market cap for {len(mcap_dict)}/{len(survivors)} tickers")

    filtered = []
    for _, row in df_results.iterrows():
        t = row["ticker"]
        mc = mcap_dict.get(t)
        if mc is not None and mc > min_mcap:
            d = row.to_dict()
            d["market_cap_b"] = round(mc / 1e9, 1)
            filtered.append(d)

    if not filtered:
        print("        No tickers passed Market Cap filter.")
        return pd.DataFrame()

    df_final = pd.DataFrame(filtered)
    df_final = df_final.sort_values(rs_sort_col, ascending=False).reset_index(drop=True)

    # Cap at top_n
    if len(df_final) > top_n:
        df_final = df_final.head(top_n)

    # ---- Step 5: Print results ----
    print(f"  [5/{n_filters+1}] Results:")
    _print_table(df_final, len(universe), rs_lookbacks, multi_tf, rs_sort_col)

    return df_final


def _print_table(df: pd.DataFrame, universe_size: int,
                 rs_lookbacks: list[int], multi_tf: bool, rs_sort_col: str):
    """Print formatted metrics table to console."""
    print()
    print(f"  {'='*72}")
    print(f"  PRE-MARKET SCREEN RESULTS - {len(df)} passed / {universe_size} screened")
    print(f"  {'='*72}")
    print()

    # Build dynamic column list
    cols = ["ticker", "price", "dollar_volume_m", "atr_pct"]
    headers = ["Ticker", "Price ($)", "Dollar Vol ($M)", "ATR%"]
    fmts = ["", ".2f", ".1f", ".2f"]

    for lb in rs_lookbacks:
        col = f"rs_{lb}"
        if col in df.columns:
            cols.append(col)
            headers.append(f"RS ({lb}d)")
            fmts.append(".4f")

    if multi_tf and "rs_score" in df.columns:
        cols.append("rs_score")
        headers.append("RS Score")
        fmts.append(".4f")

    cols += ["adx", "market_cap_b"]
    headers += ["ADX", "Mkt Cap ($B)"]
    fmts += [".1f", ".1f"]

    # Filter to columns that exist in df
    valid = [(c, h, f) for c, h, f in zip(cols, headers, fmts) if c in df.columns]
    cols = [v[0] for v in valid]
    headers = [v[1] for v in valid]
    fmts = [v[2] for v in valid]

    table_data = df[cols].values.tolist()
    print(tabulate(table_data, headers=headers, tablefmt="grid",
                   floatfmt=fmts))
    print()

    tickers_str = ", ".join(df["ticker"].tolist())
    print(f"  Passed tickers: {tickers_str}")
    print()


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # sys.path 已在檔案頂部的 repo-root bootstrap 處理，
    # 所以 `python src/screener/screener.py` 與 `python -m src.screener.screener` 都可用。
    import argparse
    ap = argparse.ArgumentParser(description="Pre-market screening pipeline")
    ap.add_argument("--tickers", default="",
                    help="Override universe (comma-separated). Default: S&P 500 + NASDAQ 100")
    ap.add_argument("--period", default=None,
                    help="yfinance period (default: auto from RS lookback)")
    ap.add_argument("--top-pct", action="store_true",
                    help="Use top 20%% RS filter instead of absolute > 1.0")
    ap.add_argument("--multi-tf", action="store_true",
                    help="Enable multi-timeframe RS (50/100/200d composite)")
    ap.add_argument("--no-adx", action="store_true",
                    help="Disable ADX > 20 filter (display only)")
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None

    # Build config overrides
    from config import Config
    cfg = Config()

    if args.multi_tf:
        cfg.screener_rs_lookbacks = [50, 100, 200]
        cfg.screener_rs_weights = [0.5, 0.3, 0.2]

    if args.no_adx:
        cfg.screener_min_adx = 0.0

    print("=== Pre-Market Screening Pipeline ===")
    if args.multi_tf:
        print("    Multi-timeframe RS: [50d=0.5, 100d=0.3, 200d=0.2]")
    if not args.no_adx:
        print(f"    ADX filter: > {cfg.screener_min_adx}")
    print()

    df = screen(cfg=cfg, tickers=tickers, period=args.period,
                use_rs_top_pct=args.top_pct)

    if not df.empty:
        print(f"\nFinal ticker list ({len(df)}):")
        print(df["ticker"].tolist())
