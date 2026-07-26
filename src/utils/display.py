"""
display.py — 每日簡報產生器（human-in-the-loop 審核用）

產出 markdown 簡報：持倉部位 P&L、候選進場、推薦訂單（含 TP/SL + 理由）。
30-60 分鐘內可看完並決定是否照單下到 broker app。
"""
from __future__ import annotations
from datetime import datetime


def _money(x, cur="USD"):
    if x is None:
        return "—"
    return f"${x:,.2f}"


def render_briefing(state: dict, orders: list[dict], prices: dict,
                    as_of_date: str, cfg) -> str:
    """產出 markdown 簡報字串。"""
    lines = []
    lines.append(f"# Swing Trading 每日簡報 — {as_of_date}\n")
    lines.append(f"產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"本金：{cfg.starting_capital:,.0f} HKD ≈ {_money(cfg.capital_usd)} | "
                 f"現金：{_money(state['cash'])} | 權益：{_money(state['equity'])}\n")

    # ---- 持倉部位 ----
    lines.append("\n## 一、未平倉部位\n")
    if not state["open_positions"]:
        lines.append("（無持倉）\n")
    else:
        lines.append("| 標的 | 股數 | 進場 | 現價 | 停損 | 止盈 | 未實現P&L | 進場日 | 進場regime |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for p in state["open_positions"]:
            px = prices.get(p["ticker"], p["entry_price"])
            pnl = (px - p["entry_price"]) * p["shares"]
            pnl_pct = (px / p["entry_price"] - 1) * 100
            lines.append(f"| {p['ticker']} | {p['shares']} | {p['entry_price']:.2f} | "
                         f"{px:.2f} | {p['stop_price']:.2f} | {p['take_profit']:.2f} | "
                         f"{_money(pnl)} ({pnl_pct:+.1f}%) | {p['entry_date']} | {p['entry_regime']} |")
        lines.append("")

    # ---- 推薦訂單 ----
    lines.append("\n## 二、推薦訂單\n")
    buys = [o for o in orders if o["action"] == "BUY"]
    sells = [o for o in orders if o["action"] == "SELL"]
    holds = [o for o in orders if o["action"] == "HOLD"]

    if sells:
        lines.append("### 🔴 平倉（SELL）\n")
        for o in sells:
            lines.append(f"- **{o['ticker']}** 賣 {o['shares']} 股 @{o['price']}")
            lines.append(f"  - 觸發：{o.get('exit_reason','')} — {o.get('exit_detail','')}")
            lines.append(f"  - {o['reasoning']}\n")

    if buys:
        lines.append("### 🟢 買進（BUY）\n")
        for o in buys:
            lines.append(f"- **{o['ticker']}** 買 {o['shares']} 股 @{o['price']}")
            lines.append(f"  - 止損 SL = {o['stop']}　止盈 TP = {o['take_profit']}　R:R = {o['risk_reward']}")
            lines.append(f"  - 風險金額 {_money(o['risk_amount'])}　倉位價值 {_money(o['position_value'])}　net score = {o.get('net_score')}")
            lines.append(f"  - 進場邏輯：\n{o['reasoning']}\n")

    if holds:
        lines.append("### ⚪ 觀望（HOLD）\n")
        for o in holds:
            lines.append(f"- **{o['ticker']}**：{o['reasoning']}")
        lines.append("")

    # ---- 下單 checklist ----
    lines.append("\n## 三、broker app 下單 checklist\n")
    if not buys and not sells:
        lines.append("今日無需下單。\n")
    else:
        lines.append("請逐筆審核後，在 broker app 手動輸入：\n")
        for o in sells:
            lines.append(f"- [ ] SELL {o['shares']} {o['ticker']} @ 市價（觸發：{o.get('exit_reason')}）")
        for o in buys:
            lines.append(f"- [ ] BUY {o['shares']} {o['ticker']} @ 市價")
            lines.append(f"  - 設限價止損單：{o['stop']}")
            lines.append(f"  - 設限價止盈單：{o['take_profit']}")
        lines.append("")

    lines.append("\n---\n*本簡報由 WB swing bot 產生，僅為決策輔助；最終下單由你審核決定。*\n")
    return "\n".join(lines)
