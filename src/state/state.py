"""
state.py — 跨日狀態持久化（swing bot 必須「記得」昨天買了什麼）

用 JSON 存：現金、權益、未平倉部位、已平倉交易紀錄、最後執行日。
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from copy import deepcopy


def default_state(capital_usd: float) -> dict:
    return {
        "cash": float(capital_usd),
        "equity": float(capital_usd),
        "open_positions": [],   # list of position dicts
        "trade_log": [],        # list of closed-trade dicts
        "last_run_date": None,
    }


def default_position(ticker: str, shares: int, entry_price: float, entry_date: str,
                     atr_at_entry: float, stop_price: float, take_profit: float,
                     regime: str, reasoning: str) -> dict:
    return {
        "ticker": ticker,
        "direction": "LONG",
        "shares": int(shares),
        "entry_price": float(entry_price),
        "entry_date": entry_date,
        "atr_at_entry": float(atr_at_entry),
        "stop_price": float(stop_price),
        "take_profit": float(take_profit),
        "highest_since_entry": float(entry_price),
        "entry_regime": regime,
        "entry_reasoning": reasoning,
    }


def load_state(path: str, capital_usd: float) -> dict:
    p = Path(path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return default_state(capital_usd)


def save_state(state: dict, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def mark_to_market(state: dict, prices: dict[str, float]) -> float:
    """用最新收盤價更新 equity（cash + 未平倉市值）。回傳 equity。"""
    pos_value = 0.0
    for pos in state["open_positions"]:
        px = prices.get(pos["ticker"], pos["entry_price"])
        pos_value += pos["shares"] * px
        # 更新最高價（給移動停利用）
        if px > pos["highest_since_entry"]:
            pos["highest_since_entry"] = px
    state["equity"] = state["cash"] + pos_value
    return state["equity"]


def close_position(state: dict, idx: int, exit_price: float, exit_date: str,
                   exit_reason: str, prices: dict, fill_model: str | None = None) -> dict:
    """平倉第 idx 個部位，寫進 trade_log，更新 cash。回傳該筆交易紀錄。"""
    pos = state["open_positions"].pop(idx)
    gross_pnl = (exit_price - pos["entry_price"]) * pos["shares"]
    # 費用：買進時已扣（這裡補估），賣出費用
    buy_cost = pos["shares"] * pos["entry_price"] * 0.0005  # 估買進滑點
    sell_notional = pos["shares"] * exit_price
    sell_cost = sell_notional * 0.0005 + sell_notional * 0.0000206  # 滑點 + SEC
    sell_cost += min(pos["shares"] * 0.000195, 9.79)  # FINRA TAF
    net_pnl = gross_pnl - buy_cost - sell_cost
    state["cash"] += pos["shares"] * exit_price - sell_cost  # 賣出回收（扣賣方費用）

    # R-multiple: 報酬 / 初始風險（entry − stop）
    stop_price = pos.get("stop_price")
    r_multiple = None
    if stop_price is not None and stop_price > 0:
        risk_per_share = float(pos["entry_price"]) - float(stop_price)
        if risk_per_share > 0:
            r_multiple = round((exit_price - pos["entry_price"]) / risk_per_share, 3)

    pct = round((exit_price / pos["entry_price"] - 1) * 100, 3)
    trade = {
        "ticker": pos["ticker"],
        "direction": pos["direction"],
        "shares": pos["shares"],
        "entry_price": pos["entry_price"],
        "exit_price": exit_price,
        "entry_date": pos["entry_date"],
        "exit_date": exit_date,
        "holding_days": None,  # 由呼叫端填
        "gross_pnl": round(gross_pnl, 4),
        "net_pnl": round(net_pnl, 4),
        "return_pct": pct,
        "pnl_pct": pct,
        "exit_reason": exit_reason,
        "exit_fill_model": fill_model if fill_model is not None else "CLOSE",
        "entry_regime": pos["entry_regime"],
        # ---- v3.2.2 audit fields (from position dict, not inferred) ----
        "entry_reason": pos.get("entry_reasoning", "UNKNOWN"),
        "setup_type": pos.get("setup_type"),
        "setup_score": pos.get("setup_score"),
        "stop_price": stop_price,
        "target_price": pos.get("take_profit"),
        "r_multiple": r_multiple,
        "regime_score_at_entry": pos.get("entry_regime_score"),
        "market_size_mult_at_entry": pos.get("entry_size_mult"),
        "atr_at_entry": pos.get("atr_at_entry"),
        "bucket_date": pos.get("bucket_date"),
        "position_value": round(pos["shares"] * pos["entry_price"], 2),
    }
    state["trade_log"].append(trade)
    return trade


def open_position(state: dict, pos: dict, price: float, cfg) -> None:
    """新增部位，扣現金（含買進費用）。"""
    buy_notional = pos["shares"] * price
    buy_cost = buy_notional * cfg.slippage_pct  # 買進滑點
    state["cash"] -= (buy_notional + buy_cost)
    pos["entry_price"] = float(price)
    pos["highest_since_entry"] = float(price)
    state["open_positions"].append(pos)
