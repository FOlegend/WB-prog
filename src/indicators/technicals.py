"""
technicals.py — 技術指標函式庫（全部回傳 pd.Series，與價格對齊）

來源：ai-hedge-fund 的指標實作 + 自家 Kaufman ER，改寫為純函式、日線適用。
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def safe_float(value, default=0.0):
    try:
        if pd.isna(value) or np.isnan(value):
            return default
        return float(value)
    except (ValueError, TypeError, OverflowError):
        return default


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).fillna(0.0)
    loss = (-delta.where(delta < 0, 0.0)).fillna(0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std()
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    return upper, mid, lower


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"] - df["high"].shift()
    down = df["low"].shift() - df["low"]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = (pd.concat([df["high"] - df["low"],
                     (df["high"] - df["close"].shift()).abs(),
                     (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1))
    atr_ = tr.ewm(span=period).mean().replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(span=period).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(span=period).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(span=period).mean()
    return pd.DataFrame({"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di})


def efficiency_ratio(close: pd.Series, window: int = 30) -> pd.Series:
    """Kaufman ER = |淨變動| / Σ|每根變動|；越高越趨勢。"""
    close = close.astype(float)
    net = (close - close.shift(window)).abs()
    path = close.diff().abs().rolling(window).sum()
    er = net / path.replace(0, np.nan)
    return er.fillna(0.0)


def hurst_exponent(price: pd.Series, max_lag: int = 20) -> float:
    """H < 0.5 均值回歸；= 0.5 隨機走；> 0.5 趨勢。"""
    lags = range(2, max_lag)
    tau = [max(1e-8, np.sqrt(np.std(np.subtract(price.values[lag:], price.values[:-lag]))))
           for lag in lags]
    try:
        reg = np.polyfit(np.log(list(lags)), np.log(tau), 1)
        return float(reg[0])
    except (ValueError, RuntimeWarning):
        return 0.5
