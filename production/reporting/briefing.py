"""
reporting/briefing.py — Daily human-actionable briefing (Production V2)

Concise shortlist (3-10 candidates): Market Regime header + top setups with
entry/stop/target/R:R/reason. Human makes the final trade decision.
"""
from __future__ import annotations

import os


def render_briefing(regime: dict, candidates: list[dict], orders: list[dict],
                    as_of_date: str, cfg) -> str:
    lines = []
    lines.append(f"# Swing Trading Daily Briefing — {as_of_date}\n")
    lines.append(f"本金 {cfg.starting_capital:,.0f} HKD ≈ ${cfg.capital_usd:,.2f}\n")

    # ---- Market Regime ----
    lines.append("\n## Market Regime (Regime v1, FROZEN)")
    lines.append(f"- **{regime.get('regime_label', 'UNKNOWN')}**  "
                 f"Composite Score: **{regime.get('composite_score', 0):.0f}**  "
                 f"Strategy: {regime.get('strategy_mode', '?')}  "
                 f"Position-size context: **{regime.get('position_size_mult', 0):.2f}**")
    if regime.get("veto_flags"):
        lines.append(f"- Vetoes: {', '.join(regime['veto_flags'])}")
    diag = regime.get("_diag", {})
    if diag:
        lines.append(f"- HMM bull prob: {diag.get('hmm_bull_prob')}  "
                     f"Breadth percentile: {diag.get('breadth_percentile')}  "
                     f"Breadth now: {diag.get('breadth_now_pct')}%  "
                     f"Breadth 10d ago: {diag.get('breadth_10d_ago_pct')}%")

    # ---- Top Setups ----
    lines.append("\n## Top Setups")
    if regime.get("regime_label") == "BEAR":
        lines.append("\n> ⚠️ BEAR regime — **no new long setups.** Review exits only.")
    buys = [o for o in orders if o["action"] == "BUY"]
    sells = [o for o in orders if o["action"] == "SELL"]
    if sells:
        lines.append("\n### Exits")
        for o in sells[:10]:
            lines.append(f"- **{o['ticker']}** SELL {o.get('shares','')}  "
                         f"({o.get('exit_reason','')}) {o.get('exit_detail','')}")
    if not buys:
        lines.append("\n（今日無合格 pullback 進場候選）")
    for i, o in enumerate(buys[:10], 1):
        lines.append(f"\n{i}. **{o['ticker']}**")
        lines.append(f"   Setup: Pullback | Score: {o.get('setup_score'):.2f} | "
                     f"Quality: {o.get('setup_quality_mult'):.2f}")
        lines.append(f"   Entry: ${o.get('price'):.2f} (next open) | "
                     f"Stop: ${o.get('stop'):.2f} | Target: ${o.get('take_profit'):.2f} | "
                     f"R/R: {o.get('risk_reward'):.2f}")
        lines.append(f"   Regime fit: {o.get('regime_fit')}")
        lines.append(f"   Reason: {o.get('reason')}")

    # ---- Position summary ----
    lines.append("\n## Position Summary")
    lines.append(f"- Equity: ${candidates[0]['_equity']:.2f} | "
                 f"Cash: ${candidates[0]['_cash']:.2f} | "
                 f"Open: {candidates[0]['_n_open']} | "
                 f"Max: {cfg.max_open_positions}")

    lines.append("\n---")
    lines.append("> 人類審核：檢查 entry/stop/target 後手動下單。next-open 進場時須"
                 "再次確認 gap ≤ 2% 且 extension ≤ 3%（否則跳過）。")
    return "\n".join(lines)


def save_briefing(md: str, as_of_date: str, cfg):
    os.makedirs(cfg.reports_dir, exist_ok=True)
    path = os.path.join(cfg.reports_dir, f"briefing_{as_of_date}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path
