"""
setup_agent.py — Explicit Trade Setup Detection (Breakout + Pullback)

This is the missing piece that upgrades the backtest from Type C
(simplified swing engine) to Type D (full swing engine).

Two setup types:
  A. Breakout setup (VCP + pivot breakout):
     - Price above 50/150/200 SMA
     - 50SMA > 150SMA > 200SMA (MA alignment)
     - Close near 20D high (pivot breakout)
     - Volume expansion (vol > 1.5 × 20MA vol)
     - VCP: recent 10D ATR% < previous 30D ATR% (volatility contraction)
  B. Pullback setup (pullback to moving average):
     - Uptrend (Close > 50SMA & 200SMA)
     - Pullback to 10EMA/20EMA (±1%)
     - Volume contraction (vol < 20MA vol)
     - Reversal candle (hammer / bullish engulfing)
     - RS remains strong

Output contract:
  {
    "valid": bool,
    "setup_type": "breakout" | "pullback" | None,
    "setup_score": 0.0-1.0,
    "setup_quality_mult": 0.0 | 0.5-1.2,   # 0 = reject entry
    "entry": float,
    "stop": float,
    "target": float,
    "risk_reward": float,
    "entry_reason": str,
  }
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from src.indicators.technicals import sma, ema, atr, safe_float


def _ma_series(df: pd.DataFrame):
    """Compute the SMA/EMA series used by both setups."""
    close = df["close"].astype(float)
    return {
        "close": close,
        "sma50": sma(close, 50),
        "sma150": sma(close, 150),
        "sma200": sma(close, 200),
        "ema10": ema(close, 10),
        "ema20": ema(close, 20),
    }


def _vcp_ratio(df: pd.DataFrame, cfg) -> float | None:
    """VCP: recent 10D ATR% vs prior 30D ATR% ratio (< 1 = contraction)."""
    if len(df) < 45:
        return None
    a = atr(df, cfg.atr_period)
    close = df["close"].astype(float)
    atr_pct = a / close * 100.0
    recent = atr_pct.iloc[-10:].mean()
    prior = atr_pct.iloc[-40:-10].mean()
    if prior <= 0 or np.isnan(recent) or np.isnan(prior):
        return None
    return float(recent / prior)


def breakout_setup(df: pd.DataFrame, cfg) -> dict:
    """Detect a VCP + pivot breakout setup. Returns score dict."""
    if len(df) < 210:
        return {"valid": False, "setup_score": 0.0,
                "entry_reason": "資料不足（< 210 根）"}

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    m = _ma_series(df)
    price = safe_float(close.iloc[-1])
    s50 = safe_float(m["sma50"].iloc[-1])
    s150 = safe_float(m["sma150"].iloc[-1])
    s200 = safe_float(m["sma200"].iloc[-1])

    if price <= 0 or s50 <= 0 or s200 <= 0:
        return {"valid": False, "setup_score": 0.0,
                "entry_reason": "MA 未暖機"}

    score = 0.0
    reasons = []

    # 1. Price above MAs
    if price > s50:
        score += 0.10
        reasons.append("px>50SMA")
    if price > s150:
        score += 0.10
        reasons.append("px>150SMA")
    if price > s200:
        score += 0.10
        reasons.append("px>200SMA")

    # 2. MA alignment 50>150>200
    if s50 > s150:
        score += 0.10
        reasons.append("50>150")
    if s150 > s200:
        score += 0.10
        reasons.append("150>200")

    # 3. Close near 20D high (pivot breakout)
    high20 = safe_float(df["high"].rolling(20).max().iloc[-1])
    if high20 > 0:
        near_high = (high20 - price) / high20 <= cfg.setup_breakout_near_high_pct
        if near_high:
            score += 0.20
            reasons.append("near20Dhigh")

    # 4. Volume expansion
    vol_ma20 = safe_float(volume.rolling(20).mean().iloc[-1])
    vol_today = safe_float(volume.iloc[-1])
    if vol_ma20 > 0 and vol_today >= cfg.setup_breakout_vol_expand * vol_ma20:
        score += 0.15
        reasons.append("vol_expand")

    # 5. VCP (volatility contraction)
    vcp = _vcp_ratio(df, cfg)
    if vcp is not None and vcp < cfg.setup_breakout_vcp_ratio:
        score += 0.15
        reasons.append(f"VCP={vcp:.2f}")

    score = round(min(1.0, score), 3)
    return {"valid": score >= cfg.setup_score_threshold,
            "setup_score": score,
            "entry_reason": f"breakout: {'+'.join(reasons) if reasons else 'none'} (score={score:.2f})",
            "detail": {"s50": s50, "s150": s150, "s200": s200, "vcp": vcp}}


def _reversal_candle(df: pd.DataFrame) -> bool:
    """Detect hammer / bullish engulfing reversal candle."""
    if len(df) < 2:
        return False
    o = safe_float(df["open"].iloc[-1])
    c = safe_float(df["close"].iloc[-1])
    h = safe_float(df["high"].iloc[-1])
    l = safe_float(df["low"].iloc[-1])
    po = safe_float(df["open"].iloc[-2])
    pc = safe_float(df["close"].iloc[-2])
    if o <= 0 or c <= 0 or h <= 0 or l <= 0:
        return False

    body = abs(c - o)
    rng = max(h - l, 1e-9)
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)

    # Hammer: lower shadow >= 2x body, small upper shadow, bullish close
    hammer = (c > o) and (lower_shadow >= 2 * body) and (upper_shadow <= body)

    # Bullish engulfing
    engulf = (c > po) and (o < pc) and (c > pc) and (o < po)

    return hammer or engulf


def pullback_setup(df: pd.DataFrame, cfg) -> dict:
    """Detect a pullback-to-MA setup. Returns score dict."""
    if len(df) < 210:
        return {"valid": False, "setup_score": 0.0,
                "entry_reason": "資料不足（< 210 根）"}

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    m = _ma_series(df)
    price = safe_float(close.iloc[-1])
    s50 = safe_float(m["sma50"].iloc[-1])
    s200 = safe_float(m["sma200"].iloc[-1])
    e10 = safe_float(m["ema10"].iloc[-1])
    e20 = safe_float(m["ema20"].iloc[-1])

    if price <= 0 or s50 <= 0 or s200 <= 0:
        return {"valid": False, "setup_score": 0.0,
                "entry_reason": "MA 未暖機"}

    score = 0.0
    reasons = []

    # 1. Uptrend structure
    if price > s50:
        score += 0.10
        reasons.append("px>50SMA")
    if price > s200:
        score += 0.10
        reasons.append("px>200SMA")
    if s50 > s200:
        score += 0.10
        reasons.append("50>200")

    # 2. Pullback to 10EMA or 20EMA (±1%)
    near_ema10 = e10 > 0 and abs(price - e10) / e10 <= cfg.setup_pullback_ma_tol
    near_ema20 = e20 > 0 and abs(price - e20) / e20 <= cfg.setup_pullback_ma_tol
    if near_ema10 or near_ema20:
        score += 0.25
        reasons.append("nearEMA")

    # 3. Volume contraction
    vol_ma20 = safe_float(volume.rolling(20).mean().iloc[-1])
    vol_today = safe_float(volume.iloc[-1])
    if vol_ma20 > 0 and vol_today < vol_ma20:
        score += 0.20
        reasons.append("vol_contract")

    # 4. Reversal candle
    if _reversal_candle(df):
        score += 0.15
        reasons.append("reversal")

    # 5. RS remains strong: close above 50D high × 0.9
    high50 = safe_float(df["high"].rolling(50).max().iloc[-1])
    if high50 > 0 and price >= 0.9 * high50:
        score += 0.10
        reasons.append("RS_strong")

    score = round(min(1.0, score), 3)
    return {"valid": score >= cfg.setup_score_threshold,
            "setup_score": score,
            "entry_reason": f"pullback: {'+'.join(reasons) if reasons else 'none'} (score={score:.2f})",
            "detail": {"s50": s50, "s200": s200, "e10": e10, "e20": e20}}


def _quality_mult(score: float, cfg) -> float:
    """Map setup_score -> setup_quality_mult (0.5-1.2, 0 = reject)."""
    if score >= cfg.setup_quality_score_high:
        return cfg.setup_quality_mult_high       # 1.2 (perfect)
    if score >= cfg.setup_quality_score_mid:
        return cfg.setup_quality_mult_mid        # 1.0 (good)
    if score >= cfg.setup_score_threshold:
        return cfg.setup_quality_mult_low        # 0.5 (marginal)
    return 0.0                                  # reject


def setup_signal(df: pd.DataFrame, cfg) -> dict:
    """Evaluate both setups, return the better one with execution fields.

    This is the agent-signal entry point used by the backtest engine.
    """
    # Which setups to evaluate (cfg.setup_enabled_types)
    enabled = getattr(cfg, "setup_enabled_types", ["breakout", "pullback"])

    results = []
    if "breakout" in enabled:
        results.append(("breakout", breakout_setup(df, cfg)))
    if "pullback" in enabled:
        results.append(("pullback", pullback_setup(df, cfg)))

    if not results:
        return {"valid": False, "setup_type": None, "setup_score": 0.0,
                "setup_quality_mult": 0.0, "entry": None, "stop": None,
                "target": None, "risk_reward": None,
                "entry_reason": "no setup type enabled"}

    best_type, best = max(results, key=lambda x: x[1]["setup_score"])
    score = best["setup_score"]
    valid = score >= cfg.setup_score_threshold
    quality_mult = _quality_mult(score, cfg) if valid else 0.0

    # Execution fields (entry/stop/target consistent with risk engine)
    close = df["close"].astype(float)
    price = safe_float(close.iloc[-1])
    a = atr(df, cfg.atr_period)
    atr_val = safe_float(a.iloc[-1])
    stop = round(price - atr_val * cfg.stop_atr_mult, 4) if atr_val > 0 else None
    target = round(price + atr_val * cfg.take_profit_atr_mult, 4) if atr_val > 0 else None
    rr = round(cfg.take_profit_atr_mult / cfg.stop_atr_mult, 2) if cfg.stop_atr_mult > 0 else None

    return {
        "valid": valid,
        "setup_type": best_type if valid else None,
        "setup_score": score,
        "setup_quality_mult": quality_mult,
        "entry": price,
        "stop": stop,
        "target": target,
        "risk_reward": rr,
        "atr": atr_val,
        "entry_reason": best["entry_reason"],
    }
