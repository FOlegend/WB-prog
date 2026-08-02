"""
hmm_backtest_compare.py — 比較不同 HMM 參數的回測績效
"""
from __future__ import annotations
import warnings
import sys
import os
from config import Config
from src.backtest.engine import BacktestEngine, compute_metrics

warnings.filterwarnings("ignore")

# Ensure src module path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

configs = [
    # (label, vol_window, covariance_type, min_bull_ret, min_bear_ret, min_spread)
    ("VW10-full (current)",  10, "full",      0.03, -0.02, 0.05),
    ("VW5-full",              5, "full",      0.03, -0.02, 0.05),
    ("VW10-spherical",       10, "spherical", 0.03, -0.02, 0.05),
    ("VW5-spherical",         5, "spherical", 0.03, -0.02, 0.05),
    ("VW15-full",            15, "full",      0.03, -0.02, 0.05),
    ("VW10-diag",            10, "diag",      0.03, -0.02, 0.05),
]

print(f"{'Config':<25} | {'Trades':>6} {'Win%':>5} {'Return%':>8} {'Sharpe':>7} "
      f"{'MaxDD%':>7} {'PF':>5} {'Final$':>8}")
print("-" * 85)

for label, vw, cov, mbr, mber, ms in configs:
    cfg = Config()
    cfg.hmm_vol_window = vw
    cfg.hmm_covariance_type = cov
    cfg.min_bull_ret = mbr
    cfg.min_bear_ret = mber
    cfg.min_regime_spread = ms

    engine = BacktestEngine(cfg, tickers=cfg.backtest_universe[:5])  # fast mode: 5 stocks
    summary = engine.run()
    if not summary:
        print(f"{label:<25} | FAILED")
        continue
    metrics = compute_metrics(summary, cfg)
    print(f"{label:<25} | {metrics['total_trades']:>6} {metrics['win_rate']*100:>4.0f}% "
          f"{metrics['total_return_pct']:>+7.2f}% {metrics['sharpe']:>7.2f} "
          f"{metrics['max_drawdown_pct']:>+6.2f}% {str(metrics['profit_factor']):>5} "
          f"${metrics['final_equity']:>7.0f}")
