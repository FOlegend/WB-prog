"""
data_fetcher.py — 日線資料抓取（yfinance，免 API key）

swing trading 用日線，所以這裡只抓 1d interval。
"""
from __future__ import annotations
import pandas as pd
import yfinance as yf


def _flatten_cols(df: pd.DataFrame) -> pd.DataFrame:
    """把 yfinance 的 MultiIndex 欄位名壓成純字串。

    yfinance 即使只下載單一標的也會回傳 MultiIndex 欄位，
    例如 ('Close', 'NIO')。這裡只保留第一層（欄位名）。
    """
    if isinstance(df.columns, pd.MultiIndex):
        new_cols = []
        for c in df.columns:
            if isinstance(c, tuple):
                new_cols.append(c[0])
            else:
                new_cols.append(c)
        df.columns = new_cols
    return df


def fetch_daily(ticker: str, start: str = "", end: str = "",
                period: str = "2y") -> pd.DataFrame:
    """抓單一標的日線，回傳 DataFrame 含 datetime/open/high/low/close/volume。

    start/end 為空時用 period（預設 2 年，足夠 HMM 與指標暖機）。
    """
    kwargs = dict(interval="1d", auto_adjust=True, progress=False)
    if start or end:
        kwargs["start"] = start or None
        kwargs["end"] = end or None
    else:
        kwargs["period"] = period

    df = yf.download(ticker, **kwargs)
    if df is None or len(df) == 0:
        return pd.DataFrame()

    # 壓平 MultiIndex 欄位 → 純字串
    df = _flatten_cols(df)

    # reset_index 把 Date 從索引變成欄位
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df = df.rename(columns={"date": "datetime"})
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    # 確保欄位齊全
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = float("nan")
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def fetch_batch(tickers: list[str], start: str = "", end: str = "",
                period: str = "2y") -> dict[str, pd.DataFrame]:
    """批量下載多標的日線，回傳 {ticker: DataFrame}。"""
    if not tickers:
        return {}
    out = {}
    kwargs = dict(interval="1d", auto_adjust=True, progress=False, group_by="ticker")
    if start or end:
        kwargs["start"] = start or None
        kwargs["end"] = end or None
    else:
        kwargs["period"] = period

    raw = yf.download(tickers, **kwargs)
    if raw is None or len(raw) == 0:
        return {t: pd.DataFrame() for t in tickers}

    for t in tickers:
        if isinstance(raw.columns, pd.MultiIndex):
            if t not in raw.columns.get_level_values(0):
                out[t] = pd.DataFrame()
                continue
            sub = raw[t].copy()
        else:
            sub = raw.copy()

        sub = sub.reset_index()
        sub.columns = [str(c).lower() for c in sub.columns]
        sub = sub.rename(columns={"date": "datetime"})
        sub["datetime"] = pd.to_datetime(sub["datetime"])
        sub = sub.sort_values("datetime").reset_index(drop=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in sub.columns:
                sub[col] = float("nan")
        out[t] = sub[["datetime", "open", "high", "low", "close", "volume"]]
    return out
