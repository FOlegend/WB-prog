"""
main.py — 每日簡報執行器（human-in-the-loop swing bot）

工作流程：
  1. 載入狀態（未平倉部位 + 現金）
  2. Screener 篩候選股
  3. 對每檔候選 + 每個持倉：跑 regime_agent(HMM) + technicals_agent
  4. risk_manager 計算倉位
  5. portfolio_manager 聚合 → 推薦訂單（含 TP/SL + 理由）
  6. 輸出 markdown 簡報 + recommended_orders.json（你審核後手動下到 broker app）

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
from src.agents.regime_agent import regime_signal
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
        print("\n[1/5] 篩選候選股...")
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

    # 2. 抓日線 + 跑兩個 agent
    print(f"\n[2/5] 抓 {len(cand_tickers)} 檔日線 + 跑 regime/technicals agent...")
    print("[3/5] HMM regime 分類 + 技術 ensemble...")
    prices = {}
    candidates = []
    for t in cand_tickers:
        df = fetch_daily(t, period="2y")
        if len(df) < 60:
            print(f"  ⚠️ {t} 資料不足（{len(df)} 根），跳過")
            continue
        close = df["close"]
        prices[t] = float(close.iloc[-1])
        rsig = regime_signal(close, cfg)
        tsig = technicals_signal(df, cfg)
        candidates.append({
            "ticker": t, "regime_signal": rsig, "technicals_signal": tsig,
            "df": df, "close": prices[t], "atr": tsig.get("atr"),
        })
        print(f"  {t}: regime={rsig['regime']}(P={rsig.get('latest_prob',0):.0%}) "
              f"tech={tsig['signal']}(score={tsig.get('score',0):.2f}) "
              f"price={prices[t]:.2f} atr={tsig.get('atr',0):.2f}")

    # 3. mark-to-market + 風控 + PM
    mark_to_market(state, prices)
    print(f"\n[4/5] 風控 + portfolio manager 決策...")
    result = decide(state, candidates, prices, cfg, as_of)
    orders = result["orders"]

    # 4. 輸出簡報
    print(f"\n[5/5] 產出簡報...")
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
