"""
backtest.py — 回測執行器

逐日 replay 整段策略，產出 HTML 報告（權益曲線/回撤/交易P&L/regime/指標表/交易紀錄）。

用法：
  python backtest.py                          # 用 config 預設（2024-01 至今，10 檔）
  python backtest.py --start 2023-01-01       # 指定起點
  python backtest.py --tickers NIO,PLUG,NOK   # 指定標的
  python backtest.py --fast                   # 快速模式（縮小 universe）
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from config import Config
from src.backtest.engine import BacktestEngine, compute_metrics
from src.backtest.report import generate_html


def main():
    ap = argparse.ArgumentParser(description="Swing bot 回測")
    ap.add_argument("--tickers", default="", help="指定標的（逗號分隔）")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--fast", action="store_true", help="快速模式（只跑 5 檔）")
    args = ap.parse_args()

    cfg = Config()
    if args.start:
        cfg.backtest_start = args.start
    if args.end:
        cfg.backtest_end = args.end
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
    if args.fast and not tickers:
        tickers = cfg.backtest_universe[:5]

    engine = BacktestEngine(cfg, tickers=tickers, start=args.start, end=args.end)
    summary = engine.run()
    if not summary:
        print("回測未產出結果。")
        return

    metrics = compute_metrics(summary, cfg)

    print("\n===== 回測績效 =====")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    # 存 JSON + HTML
    Path(cfg.reports_dir).mkdir(parents=True, exist_ok=True)
    json_path = Path(cfg.reports_dir) / "backtest_result.json"
    json_path.write_text(json.dumps({"summary": summary, "metrics": metrics},
                                    indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    html_path = Path(cfg.reports_dir) / "backtest_report.html"
    generate_html(summary, metrics, cfg, str(html_path))

    print(f"\n📊 HTML 報告：{html_path}")
    print(f"📊 JSON 結果：{json_path}")


if __name__ == "__main__":
    main()
