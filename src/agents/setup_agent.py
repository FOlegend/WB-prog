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


def breakout_setup(df: pd.DataFrame, cfg, rs_rank: int | None = None) -> dict:
    """Detect a strict VCP + pivot breakout setup. Returns score dict.

    v3.2.4 changes (reviewer):
      - STRICT only: price > prior_20d_high is REQUIRED for valid entry.
        near_prior_20d_pivot is watchlist-only, never an entry.
      - component ablation via cfg.setup_breakout_components
      - outputs boolean component flags + prior_high20 + signal_close
    """
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

    # ---- Compute boolean components (for attribution, not all scored) ----
    prior_high20 = safe_float(df["high"].shift(1).rolling(20).max().iloc[-1])
    vol_ma20 = safe_float(volume.rolling(20).mean().iloc[-1])
    vol_today = safe_float(volume.iloc[-1])
    vcp = _vcp_ratio(df, cfg)

    strict_breakout = prior_high20 > 0 and price > prior_high20
    near_pivot = (not strict_breakout) and prior_high20 > 0 and \
        (prior_high20 - price) / prior_high20 <= cfg.setup_breakout_near_high_pct
    volume_expansion = vol_ma20 > 0 and vol_today >= cfg.setup_breakout_vol_expand * vol_ma20
    vcp_contraction = vcp is not None and vcp < cfg.setup_breakout_vcp_ratio
    rs_rank_pass = rs_rank is not None and rs_rank <= cfg.setup_min_rs_rank

    comps = {
        "above_50": price > s50,
        "above_150": price > s150,
        "above_200": price > s200,
        "ma_aligned": (s50 > s150) and (s150 > s200),
        "strict_breakout": strict_breakout,
        "near_pivot": near_pivot,
        "volume_expansion": volume_expansion,
        "vcp_contraction": vcp_contraction,
        "rs_rank_pass": rs_rank_pass,
    }

    # ---- Score from ENABLED components (ablation) ----
    enabled = set(getattr(cfg, "setup_breakout_components", ["ma", "rs_rank", "volume", "vcp"]))
    score = 0.0
    reasons = []

    # strict breakout is always scored (0.20) — core gate
    if strict_breakout:
        score += 0.20
        reasons.append("breakout_above_prior_20d_high")

    if "ma" in enabled:
        if comps["above_50"]:
            score += 0.10
            reasons.append("px>50SMA")
        if comps["above_150"]:
            score += 0.10
            reasons.append("px>150SMA")
        if comps["above_200"]:
            score += 0.10
            reasons.append("px>200SMA")
        if s50 > s150:
            score += 0.10
            reasons.append("50>150")
        if s150 > s200:
            score += 0.10
            reasons.append("150>200")

    if "volume" in enabled and volume_expansion:
        score += 0.15
        reasons.append("vol_expand")

    if "vcp" in enabled and vcp_contraction:
        score += 0.15
        reasons.append(f"VCP={vcp:.2f}")

    if "rs_rank" in enabled and rs_rank_pass:
        score += 0.10
        reasons.append(f"RS_rank={rs_rank}")

    score = round(min(1.0, score), 3)
    # STRICT: valid requires strict_breakout (near-pivot never valid)
    valid = strict_breakout and score >= cfg.setup_score_threshold

    return {
        "valid": valid,
        "setup_score": score,
        "strict_breakout": strict_breakout,
        "prior_high20": prior_high20,
        "signal_close": price,
        "components": comps,
        "entry_reason": f"breakout: {'+'.join(reasons) if reasons else 'none'} (score={score:.2f})",
        "detail": {"s50": s50, "s150": s150, "s200": s200, "vcp": vcp,
                   "prior_high20": prior_high20, "rs_rank": rs_rank},
    }


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

    # Bullish engulfing (v3.2.3 fix: previous candle must be bearish pc < po)
    engulf = (pc < po) and (c > po) and (o < pc)

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
    """Map setup_score -> setup_quality_mult (0.5-1.0, 0 = reject).

    v3.2.3: cap at 1.0 (was 1.2). No >1.0 risk scaling until robustness passes.
    """
    if score >= cfg.setup_quality_score_high:
        return cfg.setup_quality_mult_high       # 1.0 (perfect)
    if score >= cfg.setup_quality_score_mid:
        return cfg.setup_quality_mult_mid        # 0.75 (good)
    if score >= cfg.setup_score_threshold:
        return cfg.setup_quality_mult_low        # 0.5 (marginal)
    return 0.0                                  # reject


def setup_signal(df: pd.DataFrame, cfg, rs_rank: int | None = None) -> dict:
    """Evaluate both setups, return the better one with execution fields.

    This is the agent-signal entry point used by the backtest engine.
    rs_rank: 1-based rank in the RS-sorted screener bucket (1 = strongest).

    v3.2.4: outputs signal_close / atr / prior_high20 / components instead of
    stop/target anchored to signal close. Stop/target are recomputed at the
    actual next-open entry price by the engine (size_position).
    """
    enabled = getattr(cfg, "setup_enabled_types", ["breakout", "pullback"])

    results = []
    if "breakout" in enabled:
        results.append(("breakout", breakout_setup(df, cfg, rs_rank=rs_rank)))
    if "pullback" in enabled:
        results.append(("pullback", pullback_setup(df, cfg)))

    if not results:
        return {"valid": False, "setup_type": None, "setup_score": 0.0,
                "setup_quality_mult": 0.0, "signal_close": None, "atr": None,
                "prior_high20": None, "components": {},
                "entry_reason": "no setup type enabled"}

    best_type, best = max(results, key=lambda x: x[1]["setup_score"])
    score = best["setup_score"]
    valid = best.get("valid", score >= cfg.setup_score_threshold)
    quality_mult = _quality_mult(score, cfg) if valid else 0.0

    close = df["close"].astype(float)
    price = safe_float(close.iloc[-1])
    a = atr(df, cfg.atr_period)
    atr_val = safe_float(a.iloc[-1])

    return {
        "valid": valid,
        "setup_type": best_type if valid else None,
        "setup_score": score,
        "setup_quality_mult": quality_mult,
        "signal_close": price,
        "atr": atr_val,
        "prior_high20": best.get("prior_high20"),
        "components": best.get("components", {}),
        "entry_reason": best["entry_reason"],
    }
