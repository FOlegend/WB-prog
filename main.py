"""
main.py — 每日簡報執行器（human-in-the-loop swing bot）

v3 結構解耦：
  - Regime 只在 SPY 上計算一次 → 全局 position_size_mult
  - 個股只跑 technicals（進出場時機）

工作流程：
  1. 載入狀態（未平倉部位 + 現金）
  2. Screener 篩候選股
  3. 在 SPY 上跑 regime_agent → 全局 size_mult + strategy
  4. 對每檔候選 + 每個持倉：只跑 technicals_agent
  5. risk_manager 計算倉位（用全局 size_mult）
  6. portfolio_manager 聚合 → 推薦訂單（含 TP/SL + 理由）
  7. 輸出 markdown 簡報 + recommended_orders.json（你審核後手動下到 broker app）

用法：
  python main.py                       # 跑今天
  python main.py --tickers NIO,PLUG    # 指定標的（跳過 screener）
  python main.py --no-screen           # 只跑現有持倉的出場檢查
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path

from config import Config
from src.data.data_fetcher import fetch_daily, fetch_batch
from src.screener.screener import screen
from src.agents.regime_agent import market_regime
from src.agents.technicals_agent import technicals_signal
from src.portfolio.portfolio_manager import decide
from src.state.state import load_state, save_state, mark_to_market
from src.utils.display import render_briefing


def run_daily(cfg: Config, tickers=None, no_screen=False):
    print(f"=== Swing Bot 每日簡報 ===")
    print(f"本金 {cfg.starting_capital:,.0f} HKD ≈ ${cfg.capital_usd:,.2f}")

    state = load_state(cfg.state_file, cfg.capital_usd)
    as_of = datetime.now().strftime("%Y-%m-%d")

    # 1. 決定要分析的標的 = screener 候選 ∪ 現有持倉
    held = [p["ticker"] for p in state["open_positions"]]
    if no_screen:
        cand_tickers = held
    elif tickers:
        cand_tickers = list(set(tickers + held))
    else:
        print("\n[1/6] 篩選候選股...")
        cand_df = screen(cfg)
        if cand_df.empty:
            print("  ⚠️ 篩選無結果，改用預設 universe")
            from src.screener.screener import UNIVERSE
            cand_tickers = list(set(UNIVERSE[:10] + held))
        else:
            cand_tickers = list(set(cand_df["ticker"].tolist() + held))
            print(f"  篩出 {len(cand_df)} 檔：{', '.join(cand_df['ticker'].tolist()[:8])}...")

    if not cand_tickers:
        print("無標的可分析。")
        return

    # 2. v3: 在市場指數（SPY）上計算 regime → 全局 size_mult
    market_idx = getattr(cfg, "regime_market_index", "SPY")
    print(f"\n[2/6] 計算市場 regime（{market_idx}）...")
    spy_df = fetch_daily(market_idx, period="2y")
    if len(spy_df) < 60:
        print(f"  ⚠️ {market_idx} 資料不足（{len(spy_df)} 根），無法計算 regime")
        return
    market_sig = market_regime(spy_df, cfg)
    global_size_mult = market_sig["position_size_mult"]
    global_strategy = market_sig["strategy"]
    global_score = market_sig["regime_score"]
    print(f"  {market_idx} regime: score={global_score:.0f}/100 "
          f"({global_strategy}, size_mult={global_size_mult:.2f}) "
          f"regime={market_sig['regime']}(P={market_sig.get('latest_prob',0):.0%})")
    comp_str = " / ".join(f"{k}={v:.0f}" for k, v in market_sig.get("components", {}).items())
    print(f"  Components: {comp_str}")
    if market_sig.get("vetoes"):
        for v in market_sig["vetoes"]:
            print(f"  ⚠️ Veto: {v}")

    if global_size_mult <= 0:
        print(f"\n  ⚠️ 市場 regime = {global_strategy}（size_mult={global_size_mult:.2f}）")
        print(f"  → 不開新多單。仍會檢查現有持倉的出場條件。")

    # 3. 抓個股日線 + 只跑 technicals
    print(f"\n[3/6] 抓 {len(cand_tickers)} 檔日線 + 跑 technicals agent...")
    print("[4/6] 技術 ensemble...")
    prices = {}
    candidates = []
    for t in cand_tickers:
        df = fetch_daily(t, period="2y")
        if len(df) < 60:
            print(f"  ⚠️ {t} 資料不足（{len(df)} 根），跳過")
            continue
        close = df["close"]
        prices[t] = float(close.iloc[-1])
        # v3: 個股只跑 technicals，regime 使用全局 SPY 結果
        tsig = technicals_signal(df, cfg)
        candidates.append({
            "ticker": t,
            "regime_signal": market_sig,  # 全局 SPY regime（所有個股共用）
            "technicals_signal": tsig,
            "df": df, "close": prices[t], "atr": tsig.get("atr"),
        })
        print(f"  {t}: tech={tsig['signal']}(score={tsig.get('score',0):.2f}) "
              f"price={prices[t]:.2f} atr={tsig.get('atr',0):.2f}")

    # 5. mark-to-market + 風控 + PM
    mark_to_market(state, prices)
    print(f"\n[5/6] 風控 + portfolio manager 決策...")
    result = decide(state, candidates, prices, cfg, as_of)
    orders = result["orders"]

    # 6. 輸出簡報
    print(f"\n[6/6] 產出簡報...")
    briefing_md = render_briefing(state, orders, prices, as_of, cfg)
    Path(cfg.reports_dir).mkdir(parents=True, exist_ok=True)
    md_path = Path(cfg.reports_dir) / f"briefing_{as_of}.md"
    md_path.write_text(briefing_md, encoding="utf-8")
    orders_path = Path(cfg.reports_dir) / f"orders_{as_of}.json"
    orders_path.write_text(json.dumps(orders, indent=2, ensure_ascii=False, default=str),
                           encoding="utf-8")

    # 印摘要到 console
    buys = [o for o in orders if o["action"] == "BUY"]
    sells = [o for o in orders if o["action"] == "SELL"]
    print(f"\n=== 決策摘要（{as_of}）===")
    print(f"  市場 regime：{market_sig['regime']} score={global_score:.0f}/100 "
          f"({global_strategy}, size_mult={global_size_mult:.2f})")
    print(f"  權益：${state['equity']:,.2f}  現金：${state['cash']:,.2f}  持倉：{len(state['open_positions'])}")
    print(f"  🟢 BUY {len(buys)} 筆  🔴 SELL {len(sells)} 筆")
    for o in orders:
        if o["action"] in ("BUY", "SELL"):
            print(f"    {o['action']} {o['shares']} {o['ticker']} @{o['price']}", end="")
            if o["action"] == "BUY":
                print(f"  SL={o['stop']} TP={o['take_profit']} R:R={o['risk_reward']}")
            else:
                print(f"  ({o.get('exit_reason','')})")
    print(f"\n📄 簡報：{md_path}")
    print(f"📄 訂單：{orders_path}")
    print(f"\n👉 審核簡報後，在 broker app 手動輸入 BUY/SELL 訂單。")
    print(f"👉 執行後用 backtest.py 的 --record-trade 或直接編輯 data/state.json 更新持倉。")
    print(f"\n注意：本工具不會自動下單。下單後請更新 data/state.json 反映實際成交。")


def main():
    ap = argparse.ArgumentParser(description="Swing bot 每日簡報")
    ap.add_argument("--tickers", default="", help="指定標的（逗號分隔），跳過 screener")
    ap.add_argument("--no-screen", action="store_true", help="只檢查現有持倉出場")
    args = ap.parse_args()
    cfg = Config()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    run_daily(cfg, tickers=tickers, no_screen=args.no_screen)


if __name__ == "__main__":
    main()
