"""
hostile_regime_test.py — Test regime engine during hostile market periods.

Reviewer: "Your engine earns its value during bad markets, not only during strong markets."

Tests 4 periods:
  1. COVID crash:      2020-02-01 ~ 2020-06-30 (V-shaped crash + recovery)
  2. 2022 bear market: 2022-01-01 ~ 2022-12-31 (gradual decline)
  3. 2018 Q4 selloff:  2018-10-01 ~ 2019-03-31 (sharp drop + recovery)
  4. Current bull:     2024-01-01 ~ latest     (for comparison)

Each period gets ~1.5 years of warmup data for HMM/MA indicators.
Results are filtered to only the target stress period.
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime
from config import Config
from src.backtest.engine import BacktestEngine, compute_metrics

# Define test periods with warmup
# (label, target_start, target_end, fetch_start, fetch_end)
PERIODS = [
    ("COVID Crash",     "2020-02-01", "2020-06-30", "2018-08-01", "2020-06-30"),
    ("2022 Bear Market","2022-01-01", "2022-12-31", "2020-07-01", "2022-12-31"),
    ("2018 Q4 Selloff", "2018-10-01", "2019-03-31", "2017-04-01", "2019-03-31"),
    ("Current Bull",    "2024-01-01", "",           "2024-01-01", ""),
]

UNIVERSE = ["MSFT", "AAPL", "NVDA", "GOOGL", "DELL", "BBY", "TGT", "JPM", "BAC"]


def run_period(label, target_start, target_end, fetch_start, fetch_end):
    """Run backtest for a specific period and filter results to target window."""
    cfg = Config()
    cfg.backtest_start = fetch_start
    cfg.backtest_end = fetch_end

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  Target: {target_start} ~ {target_end or 'latest'}")
    print(f"  Fetch:  {fetch_start} ~ {fetch_end or 'latest'} (warmup)")
    print(f"{'='*60}")

    eng = BacktestEngine(cfg, tickers=UNIVERSE, start=fetch_start, end=fetch_end)
    summary = eng.run()

    if not summary or not summary.get("equity_curve"):
        print("  ⚠️ No data or trades — skipping")
        return None

    # Filter equity curve to target period
    eq = summary["equity_curve"]
    if target_start:
        eq = [e for e in eq if e["date"] >= target_start]
    if target_end:
        eq = [e for e in eq if e["date"] <= target_end]

    # Filter trade log to target period
    trades = summary["trade_log"]
    if target_start:
        trades = [t for t in trades if t.get("entry_date", "") >= target_start]
    if target_end:
        trades = [t for t in trades if t.get("entry_date", "") <= target_end]

    # Filter regime log to target period
    regime_log = summary.get("regime_log", [])
    if target_start:
        regime_log = [r for r in regime_log if r["date"] >= target_start]
    if target_end:
        regime_log = [r for r in regime_log if r["date"] <= target_end]

    # Compute metrics from filtered equity curve
    if len(eq) < 2:
        print("  ⚠️ Insufficient equity curve data in target period")
        return {
            "label": label,
            "trades": len(trades),
            "regime_days": len(regime_log),
            "metrics": {},
            "regime_distribution": {},
        }

    eq_df = pd.DataFrame(eq)
    eq_df["ret"] = eq_df["equity"].pct_change()
    rets = eq_df["ret"].dropna()

    # Compute metrics
    starting_eq = eq_df["equity"].iloc[0]
    final_eq = eq_df["equity"].iloc[-1]
    total_return = (final_eq / starting_eq - 1) * 100

    sharpe = float(np.sqrt(252) * rets.mean() / rets.std()) if len(rets) > 1 and rets.std() > 0 else 0.0
    rolling_max = eq_df["equity"].cummax()
    dd = (eq_df["equity"] - rolling_max) / rolling_max
    max_dd = float(dd.min() * 100) if len(dd) > 0 else 0.0

    # Trade metrics
    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Regime distribution (how often was the engine in each state?)
    regime_dist = {}
    if regime_log:
        for r in regime_log:
            s = r.get("strategy", "unknown")
            regime_dist[s] = regime_dist.get(s, 0) + 1
        total_days = len(regime_log)
        regime_dist = {k: f"{v} ({v/total_days*100:.0f}%)" for k, v in
                       sorted(regime_dist.items(), key=lambda x: -x[1])}

    # SPY buy-and-hold for comparison
    spy_data = eng._market_regime_for  # just to reference
    # We need SPY data for buy-hold comparison
    from src.data.data_fetcher import fetch_daily
    spy_df = fetch_daily("SPY", start=fetch_start, end=fetch_end)
    if target_start:
        spy_df = spy_df[spy_df["datetime"] >= target_start]
    if target_end:
        spy_df = spy_df[spy_df["datetime"] <= target_end]
    if len(spy_df) >= 2:
        spy_return = (float(spy_df["close"].iloc[-1]) / float(spy_df["close"].iloc[0]) - 1) * 100
        spy_rolling_max = spy_df["close"].cummax()
        spy_dd = ((spy_df["close"] - spy_rolling_max) / spy_rolling_max * 100).min()
    else:
        spy_return = 0.0
        spy_dd = 0.0

    metrics = {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 3),
        "total_return_pct": round(total_return, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor": round(pf, 2) if pf != float("inf") else "inf",
        "spy_return_pct": round(spy_return, 2),
        "spy_max_dd_pct": round(float(spy_dd), 2),
    }

    # Print summary
    print(f"\n  --- {label} Results ---")
    print(f"  Trades: {metrics['total_trades']}, Win: {metrics['win_rate']*100:.0f}%")
    print(f"  Return: {metrics['total_return_pct']:+.2f}% | SPY B&H: {metrics['spy_return_pct']:+.2f}%")
    print(f"  Sharpe: {metrics['sharpe']:.2f} | MaxDD: {metrics['max_drawdown_pct']:.2f}%")
    print(f"  SPY MaxDD: {metrics['spy_max_dd_pct']:.2f}% | PF: {metrics['profit_factor']}")
    print(f"  Regime distribution: {regime_dist}")

    # Show regime score timeline during worst part of the period
    if regime_log:
        scores = [r["score"] for r in regime_log]
        min_score = min(scores)
        min_score_date = regime_log[scores.index(min_score)]["date"]
        print(f"  Min regime score: {min_score:.0f} on {min_score_date}")
        cash_days = sum(1 for r in regime_log if r["strategy"] == "cash")
        print(f"  Days in cash: {cash_days}/{len(regime_log)} ({cash_days/len(regime_log)*100:.0f}%)")

    return {
        "label": label,
        "metrics": metrics,
        "regime_distribution": regime_dist,
        "regime_log": regime_log,
    }


def main():
    print("=" * 60)
    print("  HOSTILE REGIME TESTING")
    print("  Testing if regime engine reduces exposure during stress periods")
    print("=" * 60)

    results = []
    for label, t_start, t_end, f_start, f_end in PERIODS:
        try:
            r = run_period(label, t_start, t_end, f_start, f_end)
            if r:
                results.append(r)
        except Exception as e:
            print(f"\n  ❌ Error testing {label}: {e}")
            import traceback
            traceback.print_exc()

    # Final comparison table
    print("\n" + "=" * 80)
    print("  COMPARISON TABLE")
    print("=" * 80)
    header = f"  {'Period':<20} | {'Trades':>6} {'Win%':>4} {'Return%':>8} {'SPY%':>7} {'Sharpe':>7} {'MaxDD%':>7} {'SPY_DD%':>8} {'PF':>5} {'Cash%':>6}"
    print(header)
    print("  " + "-" * 95)
    for r in results:
        m = r["metrics"]
        if not m:
            continue
        regime_log = r.get("regime_log", [])
        cash_pct = sum(1 for x in regime_log if x["strategy"] == "cash") / max(1, len(regime_log)) * 100
        print(f"  {r['label']:<20} | {m['total_trades']:>6} {m['win_rate']*100:>3.0f}% {m['total_return_pct']:>+7.2f}% {m['spy_return_pct']:>+6.2f}% {m['sharpe']:>7.2f} {m['max_drawdown_pct']:>+6.2f}% {m['spy_max_dd_pct']:>+7.2f}% {str(m['profit_factor']):>5} {cash_pct:>5.0f}%")

    print()
    print("  Key questions:")
    print("  1. Did the regime engine go to cash during crashes? (Cash% should be high during stress)")
    print("  2. Did MaxDD stay controlled? (Bot MaxDD should be << SPY MaxDD during stress)")
    print("  3. Did the bot avoid the worst part of the crash? (Return should beat SPY B&H during stress)")


if __name__ == "__main__":
    main()
