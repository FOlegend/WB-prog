"""
download_missing_ohlcv.py — Download OHLCV for historical S&P 500 members missing from cache

Goal: reduce the residual survivorship bias of the market-breadth universe.
The existing cache only holds CURRENT S&P 500 + NASDAQ 100 members; historical
members (delisted / acquired / renamed) are absent (~257 tickers). This script
attempts to fetch their history from yfinance and stores it in the same cache
format, CLIPPED to the ticker's point-in-time membership window from
fja05680/sp500 (sp500_ticker_start_end.csv) to guard against ticker-reuse
(wrong-data) hazards.

Download window: 2016-01-01 .. 2026-06-30 (breadth needs >= 50DMA warmup for
the 2018+ backtest).

Run once; results are merged into the cache dir as <TICKER>.csv (normalized:
dots -> dashes, matching yfinance naming used by the rest of the repo).
"""
from __future__ import annotations

import os
import sys
import time
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
if not os.path.isdir(_CACHE_DIR):
    _CACHE_DIR = os.path.join(_REPO_ROOT, "data", "cache", "equities")
_CONST_DIR = os.path.join(_REPO_ROOT, "data", "constituents")

DOWNLOAD_START = "2016-01-01"
DOWNLOAD_END = "2026-06-30"


def norm(t: str) -> str:
    return t.replace(".", "-").replace("^", "").strip()


def load_missing_list() -> tuple[list[str], pd.DataFrame]:
    """Tickers ever in S&P 500 (2015-06..2026-06) but absent from the cache."""
    ch = pd.read_csv(os.path.join(_CONST_DIR, "sp500_changes.csv"))
    ch["date"] = pd.to_datetime(ch["date"])
    members: set[str] = set()
    for _, row in ch.iterrows():
        if pd.Timestamp("2015-06-01") <= row["date"] <= pd.Timestamp("2026-06-30"):
            members |= set(row["tickers"].split(","))
    cache = {norm(f[:-4]) for f in os.listdir(_CACHE_DIR) if f.endswith(".csv") and "-K1" not in f}
    missing = sorted(m for m in members if norm(m) not in cache)

    # membership windows (for clipping); multiple rows = re-entry (keep all)
    te = pd.read_csv(os.path.join(_CONST_DIR, "sp500_ticker_start_end.csv"))
    te["start_date"] = pd.to_datetime(te["start_date"])
    te["end_date"] = pd.to_datetime(te["end_date"])
    return missing, te


def clip_to_windows(df: pd.DataFrame, te: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Clip OHLCV to the union of the ticker's membership windows (re-entry safe)."""
    rows = te[te["ticker"] == ticker]
    if rows.empty:
        return df
    keep = pd.Series(False, index=df.index)
    for _, r in rows.iterrows():
        s = r["start_date"]
        e = r["end_date"] if not pd.isna(r["end_date"]) else pd.Timestamp(DOWNLOAD_END)
        keep |= (df.index >= s) & (df.index <= e)
    return df[keep]


def download_one(t: str, te: pd.DataFrame) -> pd.DataFrame | None:
    """Fetch + clip + format OHLCV for one ticker. Returns None on failure/empty."""
    import yfinance as yf
    tk = yf.Ticker(norm(t))
    raw = tk.history(period="max", auto_adjust=True)
    if raw is None or len(raw) < 10:
        return None
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()
    if getattr(raw.index, "tz", None) is not None:
        raw.index = raw.index.tz_localize(None)  # yfinance returns tz-aware; drop to naive
    # sanity: drops any reused-ticker garbage outside the membership window
    raw = clip_to_windows(raw, te, t)
    raw = raw[(raw.index >= pd.Timestamp(DOWNLOAD_START)) &
              (raw.index <= pd.Timestamp(DOWNLOAD_END))]
    if len(raw) < 60:
        return None
    out = pd.DataFrame({
        "datetime": raw.index,
        "open": raw["Open"].astype(float),
        "high": raw["High"].astype(float),
        "low": raw["Low"].astype(float),
        "close": raw["Close"].astype(float),
        "volume": raw["Volume"].astype(float),
    })
    return out


def main():
    missing, te = load_missing_list()
    print(f"missing tickers to try: {len(missing)}", flush=True)
    ok, empty = [], []
    for i, t in enumerate(missing):
        try:
            df = download_one(t, te)
        except Exception as e:
            df = None
            print(f"  {t}: ERR {str(e)[:60]}", flush=True)
        if df is not None:
            out_path = os.path.join(_CACHE_DIR, f"{norm(t)}.csv")
            df.to_csv(out_path, index=False)
            ok.append(t)
        else:
            empty.append(t)
        if (i + 1) % 25 == 0:
            print(f"  progress {i+1}/{len(missing)} ok={len(ok)}", flush=True)
        time.sleep(0.25)
    print(f"\nDONE: ok={len(ok)} empty/failed={len(empty)}")
    with open(os.path.join(_CONST_DIR, "download_missing_report.txt"), "w") as f:
        f.write(f"ok={len(ok)} empty/failed={len(empty)}\n")
        f.write("OK:\n" + "\n".join(ok) + "\n\nEMPTY/FAILED:\n" + "\n".join(empty) + "\n")


if __name__ == "__main__":
    main()
