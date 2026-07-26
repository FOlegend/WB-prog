"""
screener.py — 標的篩選器（swing 版，日線驅動）

從候選池篩出「便宜 + 流動 + 會趨勢 + 波幅夠」的標的，給 regime/technicals agent 跑。
評分 = 0.45×ER + 0.30×波幅 + 0.25×流動性，取前 N 名。
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd
import yfinance as yf

from src.indicators.technicals import efficiency_ratio


UNIVERSE = [
    "NIO", "RIVN", "LCID", "XPEV", "PLUG", "FUBO", "WULF",
    "NOK", "SOFI", "INTC", "SNDL", "CLOV",
    "F", "T", "BAC",
    "MARA", "RIOT", "NXE", "CDE", "HL", "PAAS",
    "VALE", "PBR", "BBD", "ITUB",
    "AMC", "GME", "NCLH", "KWEB",
]

_HERE = os.path.dirname(os.path.abspath(__file__))
_TXT = os.path.join(_HERE, "universe.txt")
if os.path.exists(_TXT):
    with open(_TXT) as fh:
        UNIVERSE = [l.strip().upper() for l in fh if l.strip()]


def _safe_sub(df, ticker):
    if df is None or len(df) == 0:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        if ticker not in df.columns.get_level_values(0):
            return None
        return df[ticker]
    return df


def screen(cfg, tickers=None, period="3mo") -> pd.DataFrame:
    """跑篩選，回傳前 N 名 DataFrame（ticker/price/avg_dvol/range%/er/score）。"""
    pool = tickers or UNIVERSE
    daily = yf.download(pool, period=period, interval="1d",
                        group_by="ticker", auto_adjust=True, progress=False)
    rows = []
    for t in pool:
        d = _safe_sub(daily, t)
        if d is None or len(d) < 20:
            continue
        try:
            close = d["Close"].astype(float)
            price = float(close.iloc[-1])
            avg_dvol = float((close * d["Volume"].astype(float)).iloc[-20:].mean())
            rng = float(((d["High"].astype(float) - d["Low"].astype(float)) / close).iloc[-20:].mean())
            er = efficiency_ratio(close, window=cfg.er_lookback)
            er_mean = float(np.nanmean(er)) if not np.all(np.isnan(er)) else np.nan
        except Exception:
            continue
        rows.append({"ticker": t, "price": price, "avg_dvol": avg_dvol,
                     "daily_range_pct": rng, "er": er_mean})

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    f = ((df.price >= cfg.price_min) & (df.price <= cfg.price_max)
         & (df.avg_dvol >= cfg.min_dollar_vol)
         & (df.daily_range_pct >= cfg.min_daily_range)
         & (df.er >= cfg.min_er))
    cand = df[f].copy()
    if cand.empty:
        return df

    def _norm(s):
        lo, hi = s.min(), s.max()
        return (s - lo) / (hi - lo) if hi > lo else s * 0.0
    w = {"er": 0.45, "range": 0.30, "liq": 0.25}
    cand["score"] = (w["er"] * _norm(cand.er) + w["range"] * _norm(cand.daily_range_pct)
                     + w["liq"] * _norm(cand.avg_dvol))
    cand = cand.sort_values("score", ascending=False).head(cfg.screener_top_n)
    return cand.reset_index(drop=True)
