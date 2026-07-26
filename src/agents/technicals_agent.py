"""
technicals_agent.py — 技術面 ensemble 訊號（趨勢/均值回歸/動量/波動，加權合併）

改寫自 ai-hedge-fund technicals.py，去掉 LLM，純指標函式 → 加權投票。
輸出 signal(bullish/bearish/neutral) + confidence + 詳細指標數值。
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

from src.indicators.technicals import (
    ema, sma, rsi, bollinger, macd, atr, adx, efficiency_ratio, hurst_exponent, safe_float
)


def _trend_signals(df, cfg) -> dict:
    ema_f = ema(df["close"], cfg.ema_fast)
    ema_m = ema(df["close"], cfg.ema_mid)
    ema_s = ema(df["close"], cfg.ema_slow)
    adx_df = adx(df, cfg.adx_period)
    adx_val = safe_float(adx_df["adx"].iloc[-1])
    trend_strength = adx_val / 100.0

    short_up = ema_f.iloc[-1] > ema_m.iloc[-1]
    mid_up = ema_m.iloc[-1] > ema_s.iloc[-1]
    if short_up and mid_up:
        signal, confidence = "bullish", trend_strength
    elif not short_up and not mid_up:
        signal, confidence = "bearish", trend_strength
    else:
        signal, confidence = "neutral", 0.5
    return {
        "signal": signal, "confidence": confidence,
        "metrics": {"adx": adx_val, "ema_fast": safe_float(ema_f.iloc[-1]),
                    "ema_mid": safe_float(ema_m.iloc[-1]), "ema_slow": safe_float(ema_s.iloc[-1])},
    }


def _mean_reversion_signals(df, cfg) -> dict:
    ma50 = sma(df["close"], 50)
    std50 = df["close"].rolling(50).std()
    z = (df["close"] - ma50) / std50.replace(0, np.nan)
    upper, mid, lower = bollinger(df["close"], cfg.bollinger_window)
    rsi14 = rsi(df["close"], cfg.rsi_period)
    z_now = safe_float(z.iloc[-1])
    bb_pos = safe_float((df["close"].iloc[-1] - lower.iloc[-1]) /
                        (upper.iloc[-1] - lower.iloc[-1])) if (upper.iloc[-1] - lower.iloc[-1]) > 0 else 0.5
    rsi_now = safe_float(rsi14.iloc[-1])

    if z_now < -2 and bb_pos < 0.2:
        signal, confidence = "bullish", min(abs(z_now) / 4, 1.0)
    elif z_now > 2 and bb_pos > 0.8:
        signal, confidence = "bearish", min(abs(z_now) / 4, 1.0)
    else:
        signal, confidence = "neutral", 0.5
    return {
        "signal": signal, "confidence": confidence,
        "metrics": {"z_score": z_now, "bb_position": bb_pos, "rsi14": rsi_now},
    }


def _momentum_signals(df, cfg) -> dict:
    returns = df["close"].pct_change()
    mom_1m = returns.rolling(21).sum()
    mom_3m = returns.rolling(63).sum()
    vol_ma = df["volume"].rolling(21).mean()
    vol_mom = df["volume"] / vol_ma.replace(0, np.nan)
    score = (0.4 * safe_float(mom_1m.iloc[-1]) + 0.3 * safe_float(mom_3m.iloc[-1]))
    vol_conf = safe_float(vol_mom.iloc[-1]) > 1.0
    if score > 0.05 and vol_conf:
        signal, confidence = "bullish", min(abs(score) * 5, 1.0)
    elif score < -0.05 and vol_conf:
        signal, confidence = "bearish", min(abs(score) * 5, 1.0)
    else:
        signal, confidence = "neutral", 0.5
    return {
        "signal": signal, "confidence": confidence,
        "metrics": {"momentum_1m": safe_float(mom_1m.iloc[-1]),
                    "momentum_3m": safe_float(mom_3m.iloc[-1]),
                    "volume_momentum": safe_float(vol_mom.iloc[-1])},
    }


def _volatility_signals(df, cfg) -> dict:
    returns = df["close"].pct_change()
    hist_vol = returns.rolling(21).std() * math.sqrt(252)
    vol_ma = hist_vol.rolling(63).mean()
    vol_regime = hist_vol / vol_ma.replace(0, np.nan)
    vol_z = (hist_vol - vol_ma) / hist_vol.rolling(63).std().replace(0, np.nan)
    cur = safe_float(vol_regime.iloc[-1])
    vz = safe_float(vol_z.iloc[-1])
    atr_val = safe_float(atr(df, cfg.atr_period).iloc[-1])
    if cur < 0.8 and vz < -1:
        signal, confidence = "bullish", min(abs(vz) / 3, 1.0)
    elif cur > 1.2 and vz > 1:
        signal, confidence = "bearish", min(abs(vz) / 3, 1.0)
    else:
        signal, confidence = "neutral", 0.5
    return {
        "signal": signal, "confidence": confidence,
        "metrics": {"historical_vol": safe_float(hist_vol.iloc[-1]),
                    "vol_regime": cur, "vol_z_score": vz, "atr": atr_val},
    }


def _weighted_combine(signals: dict, weights: dict) -> dict:
    vals = {"bullish": 1, "neutral": 0, "bearish": -1}
    wsum, wconf = 0.0, 0.0
    for k, s in signals.items():
        w = weights.get(k, 0)
        wsum += vals[s["signal"]] * w * s["confidence"]
        wconf += w * s["confidence"]
    final = wsum / wconf if wconf > 0 else 0.0
    if final > 0.2:
        signal = "bullish"
    elif final < -0.2:
        signal = "bearish"
    else:
        signal = "neutral"
    return {"signal": signal, "confidence": abs(final), "score": final}


def technicals_signal(df: pd.DataFrame, cfg) -> dict:
    """產出技術面 ensemble 訊號（agent signal 契約）。"""
    if len(df) < 70:
        return {"agent": "technicals_agent", "signal": "neutral", "confidence": 0.0,
                "score": 0.0, "reasoning": "資料不足（< 70 根），指標暖機不夠"}

    trend = _trend_signals(df, cfg)
    mr = _mean_reversion_signals(df, cfg)
    mom = _momentum_signals(df, cfg)
    vol = _volatility_signals(df, cfg)
    parts = {"trend": trend, "mean_reversion": mr, "momentum": mom, "volatility": vol}
    combined = _weighted_combine(parts, cfg.tech_weights)

    er = safe_float(efficiency_ratio(df["close"], cfg.er_lookback).iloc[-1])
    macd_line, signal_line, hist = macd(df["close"])
    atr_val = safe_float(atr(df, cfg.atr_period).iloc[-1])
    price = safe_float(df["close"].iloc[-1])

    return {
        "agent": "technicals_agent",
        "signal": combined["signal"],
        "confidence": round(combined["confidence"] * 100, 1),
        "score": round(combined["score"], 3),
        "er": round(er, 3),
        "atr": round(atr_val, 4),
        "price": round(price, 4),
        "macd_hist": round(safe_float(hist.iloc[-1]), 4),
        "reasoning": {
            "trend": trend, "mean_reversion": mr,
            "momentum": mom, "volatility": vol,
            "er": er, "combined_score": round(combined["score"], 3),
        },
    }
