"""
risk_manager.py — 風控與倉位計算（無 LLM，純規則）

依 regime score 的 position_size_mult × 個股 ATR 計算合適倉位：
  risk_amount = equity × risk_per_trade × size_mult
  stop_distance = ATR × stop_atr_mult
  shares = floor(risk_amount / stop_distance)
  再用 max_position_pct / max_open_positions / 可用現金 三道上限夾住。

size_mult 來自 RegimeScoreEngine（0.0-1.0），取代舊的 regime_size_mult dict。
這實現了 progressive exposure：score 70+ → 1.0, 50-69 → 0.3-1.0, <50 → 0.0。
"""
from __future__ import annotations
import math
from src.indicators.technicals import atr


def size_position(equity: float, cash: float, price: float, atr_val: float,
                  size_mult: float, n_open: int, cfg) -> dict:
    """計算單一標的建議倉位。回傳 dict（含 shares / stop / tp / 各項上限理由）。

    Parameters
    ----------
    size_mult : float
        Position size multiplier from RegimeScoreEngine (0.0-1.0).
        0.0 = no trade (cash strategy), 1.0 = full size (trend_following).
    """
    if price <= 0 or atr_val <= 0 or math.isnan(atr_val):
        return _no_trade("價格或 ATR 無效")

    # regime gate: size_mult <= 0 means cash strategy — no new longs
    if size_mult <= 0:
        return _no_trade(f"size_mult={size_mult:.2f}，禁止開新多單（cash strategy）")

    # 投組層：已達最大持倉數
    if n_open >= cfg.max_open_positions:
        return _no_trade(f"已達最大持倉數 {cfg.max_open_positions}")

    # 風險預算（progressive exposure via size_mult）
    risk_amount = equity * cfg.risk_per_trade * size_mult
    stop_distance = atr_val * cfg.stop_atr_mult
    raw_shares = int(risk_amount // stop_distance) if stop_distance > 0 else 0
    if raw_shares <= 0:
        return _no_trade(f"風險預算 ${risk_amount:.2f} 不足以買 1 股（stop_dist={stop_distance:.2f}）")

    # 上限 1：單一持倉 ≤ max_position_pct × equity
    max_value = equity * cfg.max_position_pct
    max_shares_by_value = int(max_value // price)
    # 上限 2：可用現金
    max_shares_by_cash = int(cash // price) if cash > 0 else 0

    shares = max(0, min(raw_shares, max_shares_by_value, max_shares_by_cash))
    if shares <= 0:
        return _no_trade(
            f"上限夾到 0（raw={raw_shares}, by_value={max_shares_by_value}, "
            f"by_cash={max_shares_by_cash}）"
        )

    stop_price = price - stop_distance
    take_profit = price + atr_val * cfg.take_profit_atr_mult
    risk_usd = shares * stop_distance
    reward_usd = shares * (take_profit - price)
    rr = reward_usd / risk_usd if risk_usd > 0 else 0.0

    return {
        "allow": True,
        "shares": shares,
        "stop_price": round(stop_price, 4),
        "take_profit": round(take_profit, 4),
        "stop_distance": round(stop_distance, 4),
        "risk_amount": round(risk_usd, 2),
        "reward_amount": round(reward_usd, 2),
        "risk_reward": round(rr, 2),
        "position_value": round(shares * price, 2),
        "size_mult": round(size_mult, 3),
        "reasoning": (
            f"倉位={shares} 股（風險預算 ${risk_amount:.0f} × size_mult {size_mult:.2f} / "
            f"stop_dist ${stop_distance:.2f}）。上限：value {max_shares_by_value}、"
            f"cash {max_shares_by_cash}。SL={stop_price:.2f} TP={take_profit:.2f} R:R={rr:.2f}"
        ),
    }


def _no_trade(reason: str) -> dict:
    return {"allow": False, "shares": 0, "reasoning": reason,
            "stop_price": None, "take_profit": None, "risk_reward": None}
