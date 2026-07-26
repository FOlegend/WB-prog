"""
portfolio_manager.py — 決策整合層（無 LLM，純規則聚合 → 推薦訂單）

仿 ai-hedge-fund 的 portfolio_manager 角色，但用確定性規則取代 LLM：
  1. 加權聚合 regime + technicals 訊號 → net score ∈ [-1, 1]
  2. 對候選股：net > entry_threshold 且 regime 允許 → BUY（含 TP/SL/理由）
  3. 對未平倉部位：檢查 TP/SL/移動停利/時間停損/regime 翻空 → SELL
  4. 每筆決策附完整進場邏輯與 TP/SL 設定理由（human-in-the-loop 審核用）
"""
from __future__ import annotations
from datetime import datetime
from src.agents.risk_manager import size_position


def _exit_check(pos: dict, price: float, date_str: str, tech_signal: str, cfg) -> dict | None:
    """檢查未平倉部位是否觸發出場。回傳 {reason, exit_price} 或 None（續抱）。"""
    # 1. 止損
    if price <= pos["stop_price"]:
        return {"reason": "STOP_LOSS", "exit_price": price,
                "detail": f"收盤 {price:.2f} ≤ 止損 {pos['stop_price']:.2f}"}
    # 2. 止盈
    if price >= pos["take_profit"]:
        return {"reason": "TAKE_PROFIT", "exit_price": price,
                "detail": f"收盤 {price:.2f} ≥ 止盈 {pos['take_profit']:.2f}"}
    # 3. 移動停利：先看是否啟動（獲利達 1R）
    r_dist = pos["atr_at_entry"] * cfg.stop_atr_mult
    if pos["highest_since_entry"] >= pos["entry_price"] + cfg.trailing_trigger_r * r_dist:
        trail_stop = pos["highest_since_entry"] - cfg.trailing_atr_mult * pos["atr_at_entry"]
        if price <= trail_stop:
            return {"reason": "TRAILING_STOP", "exit_price": price,
                    "detail": f"從最高 {pos['highest_since_entry']:.2f} 回撤至 {price:.2f} ≤ 移動停利 {trail_stop:.2f}"}
    # 4. 時間停損
    try:
        held = (datetime.strptime(date_str, "%Y-%m-%d") -
                datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days
    except Exception:
        held = 0
    if held >= cfg.max_holding_days:
        return {"reason": "TIME_STOP", "exit_price": price,
                "detail": f"持有 {held} 天 ≥ 上限 {cfg.max_holding_days} 天"}
    # 5. 技術翻空
    if tech_signal == "bearish":
        return {"reason": "SIGNAL_EXIT", "exit_price": price,
                "detail": "technicals_agent 轉 bearish"}
    return None


def decide(state: dict, candidates: list[dict], prices: dict[str, float],
           cfg, as_of_date: str) -> dict:
    """
    candidates: [{ticker, regime_signal, technicals_signal, df, close}] 每檔含兩個 agent 訊號
    回傳 {orders: [...], briefing: {...}}
    每個 order: {ticker, action: BUY/SELL/HOLD, shares, price, stop, take_profit, reasoning, ...}
    """
    orders = []
    n_open = len(state["open_positions"])

    # ---- 先處理未平倉部位的出場 ----
    remaining = list(enumerate(state["open_positions"]))
    for idx, pos in remaining:
        ticker = pos["ticker"]
        price = prices.get(ticker)
        if price is None:
            continue
        # 找該標的的最新 technicals 訊號（候選清單裡有就用，否則 neutral）
        tech_sig = "neutral"
        for c in candidates:
            if c["ticker"] == ticker:
                tech_sig = c["technicals_signal"]["signal"]
                break
        ex = _exit_check(pos, price, as_of_date, tech_sig, cfg)
        if ex is not None:
            orders.append({
                "ticker": ticker, "action": "SELL", "shares": pos["shares"],
                "price": round(price, 4), "exit_reason": ex["reason"],
                "exit_detail": ex["detail"],
                "reasoning": (f"平倉 {ticker}：{ex['detail']}。"
                              f"進場 {pos['entry_price']:.2f} → 出場 {price:.2f}，"
                              f"持有自 {pos['entry_date']}，進場 regime={pos['entry_regime']}"),
            })

    # ---- 再處理候選股進場 ----
    # 統計已存在的持倉 ticker（避免重複買同一檔）
    held_tickers = {p["ticker"] for p in state["open_positions"]}
    # 注意：上面 SELL 決定後實際部位還沒移除（執行層才移），這裡進場用「扣掉 SELL 後」的預估持倉數
    pending_sells = sum(1 for o in orders if o["action"] == "SELL")
    effective_open = n_open - pending_sells

    for c in candidates:
        ticker = c["ticker"]
        if ticker in held_tickers:
            continue  # 已持有，不重複加倉
        regime = c["regime_signal"]
        tech = c["technicals_signal"]
        price = c.get("close") or prices.get(ticker)
        if price is None or price <= 0:
            continue

        # regime gate：BEAR / RANGE_BOUND 不開新多單
        if regime["regime"] in ("BEAR", "RANGE_BOUND") or not regime.get("trending", False):
            orders.append({
                "ticker": ticker, "action": "HOLD", "shares": 0, "price": round(price, 4),
                "reasoning": (f"不進場：regime={regime['regime']}（trending={regime.get('trending')}），"
                              f"swing bot 僅在 BULL 開新多單"),
            })
            continue

        # 加權 net score
        regime_score = regime.get("score", 0.0)
        tech_score = tech.get("score", 0.0)
        net = cfg.regime_weight * regime_score + cfg.technicals_weight * tech_score

        if net <= cfg.entry_threshold:
            orders.append({
                "ticker": ticker, "action": "HOLD", "shares": 0, "price": round(price, 4),
                "reasoning": (f"不進場：加權 net={net:.3f} ≤ 門檻 {cfg.entry_threshold} "
                              f"(regime {regime_score:.2f}×{cfg.regime_weight} + "
                              f"tech {tech_score:.2f}×{cfg.technicals_weight})"),
            })
            continue

        # 風控計算倉位
        atr_val = tech.get("atr") or c.get("atr") or 0.0
        sizing = size_position(state["equity"], state["cash"], price, atr_val,
                               regime["regime"], effective_open, cfg)
        if not sizing.get("allow"):
            orders.append({
                "ticker": ticker, "action": "HOLD", "shares": 0, "price": round(price, 4),
                "reasoning": f"風控否決：{sizing.get('reasoning')}",
            })
            continue

        orders.append({
            "ticker": ticker, "action": "BUY", "shares": sizing["shares"],
            "price": round(price, 4),
            "stop": sizing["stop_price"], "take_profit": sizing["take_profit"],
            "stop_distance": sizing["stop_distance"],
            "risk_amount": sizing["risk_amount"], "risk_reward": sizing["risk_reward"],
            "position_value": sizing["position_value"],
            "net_score": round(net, 3),
            "regime": regime["regime"], "atr": round(atr_val, 4),
            "reasoning": (
                f"買進 {ticker} {sizing['shares']} 股 @{price:.2f}。\n"
                f"  進場邏輯：regime={regime['regime']}(P={regime.get('latest_prob',0):.1%}) + "
                f"technicals={tech['signal']}(score={tech_score:.2f}) → net={net:.3f} > {cfg.entry_threshold}。\n"
                f"  TP/SL 設定：ATR={atr_val:.2f}，"
                f"SL={sizing['stop_price']:.2f}（entry − {cfg.stop_atr_mult}×ATR，保護下行），"
                f"TP={sizing['take_profit']:.2f}（entry + {cfg.take_profit_atr_mult}×ATR，R:R={sizing['risk_reward']:.2f}）。\n"
                f"  倉位理由：{sizing['reasoning']}"
            ),
        })
        effective_open += 1  # 佔一個持倉名額

    return {"orders": orders, "as_of_date": as_of_date}
