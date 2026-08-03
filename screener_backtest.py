"""
screener_backtest.py — Run screener to get top 20, then backtest on that universe.

Two-phase pipeline:
  Phase 1: Screen S&P 500 + NASDAQ 100 (~518 tickers) → top 20 by RS
  Phase 2: Backtest v3 engine on those 20 tickers (2024-01 ~ latest)

Also runs the old 9-stock universe for side-by-side comparison.
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import sys, os, json, time
from pathlib import Path
from datetime import datetime

# Repo-root bootstrap
_REPO = os.path.dirname(os.path.abspath(__file__))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from config import Config
from src.screener.screener import screen
from src.backtest.engine import BacktestEngine, compute_metrics
from src.backtest.report import generate_html


def run_screener(top_n: int = 20) -> list[str]:
    """Phase 1: Run 6-filter screener, return top N tickers."""
    cfg = Config()
    cfg.screener_top_n = top_n

    print("=" * 72)
    print(f"  PHASE 1: PRE-MARKET SCREENER (top {top_n})")
    print("=" * 72)
    print()

    t0 = time.time()
    df = screen(cfg=cfg)
    elapsed = time.time() - t0

    if df.empty:
        print("  Screener returned no results. Aborting.")
        return []

    tickers = df["ticker"].tolist()[:top_n]
    print(f"\n  Screener completed in {elapsed:.0f}s")
    print(f"  Top {len(tickers)} tickers: {', '.join(tickers)}")

    # Save screener output for reference
    out_dir = Path(cfg.base_dir) / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "screener_top20.csv", index=False)
    print(f"  Saved to: {out_dir / 'screener_top20.csv'}")

    return tickers


def run_backtest(tickers: list[str], label: str, cfg: Config) -> dict:
    """Run backtest on given tickers, return metrics dict."""
    print()
    print("=" * 72)
    print(f"  PHASE 2: BACKTEST — {label} ({len(tickers)} tickers)")
    print("=" * 72)

    engine = BacktestEngine(cfg, tickers=tickers)
    summary = engine.run()
    if not summary:
        print(f"  Backtest failed for {label}.")
        return {}

    metrics = compute_metrics(summary, cfg)

    print(f"\n  --- {label} Results ---")
    for k, v in metrics.items():
        print(f"    {k}: {v}")

    return {"summary": summary, "metrics": metrics, "label": label, "tickers": tickers}


def main():
    # ---- Phase 1: Screener ----
    top20 = run_screener(top_n=20)
    if not top20:
        return

    # ---- Phase 2: Backtest on top 20 ----
    cfg = Config()
    cfg.screener_top_n = 20

    result_20 = run_backtest(top20, "Screener Top 20", cfg)

    # ---- Phase 2b: Backtest on old 9-stock for comparison ----
    old_9 = cfg.backtest_universe  # MSFT, AAPL, NVDA, GOOGL, DELL, BBY, TGT, JPM, BAC
    result_9 = run_backtest(old_9, "Hardcoded 9-stock", cfg)

    # ---- Comparison Table ----
    print()
    print("=" * 72)
    print("  COMPARISON: Screener Top 20 vs Hardcoded 9-stock")
    print("=" * 72)
    print()

    if result_20.get("metrics") and result_9.get("metrics"):
        m20 = result_20["metrics"]
        m9 = result_9["metrics"]

        headers = ["Metric", "Top 20", "9-stock", "Delta"]
        rows = [
            ["Tickers", str(len(result_20["tickers"])), str(len(result_9["tickers"])), ""],
            ["Trades", str(m20.get("total_trades", 0)), str(m9.get("total_trades", 0)), ""],
            ["Win Rate", f"{m20.get('win_rate', 0)*100:.1f}%", f"{m9.get('win_rate', 0)*100:.1f}%",
             f"{(m20.get('win_rate',0)-m9.get('win_rate',0))*100:+.1f}pp"],
            ["Total Return", f"{m20.get('total_return_pct', 0):+.2f}%", f"{m9.get('total_return_pct', 0):+.2f}%",
             f"{m20.get('total_return_pct',0)-m9.get('total_return_pct',0):+.2f}pp"],
            ["Sharpe", f"{m20.get('sharpe', 0):.2f}", f"{m9.get('sharpe', 0):.2f}",
             f"{m20.get('sharpe',0)-m9.get('sharpe',0):+.2f}"],
            ["Max DD", f"{m20.get('max_drawdown_pct', 0):+.2f}%", f"{m9.get('max_drawdown_pct', 0):+.2f}%",
             f"{m20.get('max_drawdown_pct',0)-m9.get('max_drawdown_pct',0):+.2f}pp"],
            ["PF", str(m20.get("profit_factor", 0)), str(m9.get("profit_factor", 0)), ""],
            ["Avg Win", f"${m20.get('avg_win', 0):.2f}", f"${m9.get('avg_win', 0):.2f}", ""],
            ["Avg Loss", f"${m20.get('avg_loss', 0):.2f}", f"${m9.get('avg_loss', 0):.2f}", ""],
            ["Expectancy", f"${m20.get('expectancy', 0):.2f}", f"${m9.get('expectancy', 0):.2f}", ""],
        ]

        # Print table
        col_widths = [16, 16, 16, 12]
        sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
        print(sep)
        print("|" + "|".join(f" {h:<{w}} " for h, w in zip(headers, col_widths)) + "|")
        print(sep)
        for row in rows:
            print("|" + "|".join(f" {v:<{w}} " for v, w in zip(row, col_widths)) + "|")
        print(sep)

    # ---- Save results ----
    reports_dir = Path(cfg.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Save comparison JSON
    comparison = {
        "screener_top20": {
            "tickers": top20,
            "metrics": result_20.get("metrics", {}),
        },
        "hardcoded_9stock": {
            "tickers": old_9,
            "metrics": result_9.get("metrics", {}),
        },
        "timestamp": datetime.now().isoformat(),
    }
    json_path = reports_dir / "universe_comparison.json"
    json_path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # Generate HTML report for top 20
    if result_20.get("summary"):
        html_path = reports_dir / "backtest_report_top20.html"
        generate_html(result_20["summary"], result_20["metrics"], cfg, str(html_path))
        print(f"\n  HTML report (top 20): {html_path}")

    if result_9.get("summary"):
        html_path_9 = reports_dir / "backtest_report_9stock.html"
        generate_html(result_9["summary"], result_9["metrics"], cfg, str(html_path_9))
        print(f"  HTML report (9-stock): {html_path_9}")

    print(f"  JSON comparison: {json_path}")
    print("\n  Done.")


if __name__ == "__main__":
    main()
