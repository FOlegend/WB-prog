"""
ablation_test.py — 消融實驗（Ablation Testing）

測試每個 regime component 的邊際貢獻：
  1. MA Structure Only（基準線）
  2. MA + HMM
  3. MA + HMM + Distribution Days
  4. Full Model (MA + HMM + KER + ADX + Dist Days)
  5. Full Model — No Vetoes（測量 veto 機制的貢獻）

目標：加入每個 component 後，Max DD 應遞減或 Sharpe 應提升。
若某 component 加入後淨提升為負，應調低其權重。
"""
from __future__ import annotations
import warnings
import sys
import os
import copy
from config import Config
from src.backtest.engine import BacktestEngine, compute_metrics

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def make_config(weights: dict, disable_vetoes: bool = False) -> Config:
    """Create a Config with specified component weights and optional veto disabling."""
    cfg = Config()
    cfg.regime_score_weights = weights
    if disable_vetoes:
        cfg.regime_veto_hmm_bear_prob = 1.01   # never triggers
        cfg.regime_veto_below_sma_cap = 1.0     # no cap
        cfg.regime_veto_dist_days = 999          # never triggers
    return cfg


# Original weights: hmm=0.20, ma=0.30, ker=0.18, adx=0.10, dist=0.22
# The regime_score_engine auto-normalizes, so we just set excluded to 0.

configs = [
    # (label, weights, disable_vetoes)
    ("1. MA Only", {
        "ma": 1.0, "hmm": 0.0, "ker": 0.0, "adx": 0.0, "dist": 0.0,
    }, False),
    ("2. MA + HMM", {
        "ma": 0.30, "hmm": 0.20, "ker": 0.0, "adx": 0.0, "dist": 0.0,
    }, False),
    ("3. MA + HMM + Dist", {
        "ma": 0.30, "hmm": 0.20, "ker": 0.0, "adx": 0.0, "dist": 0.22,
    }, False),
    ("4. Full Model", {
        "ma": 0.30, "hmm": 0.20, "ker": 0.18, "adx": 0.10, "dist": 0.22,
    }, False),
    ("5. Full — No Vetoes", {
        "ma": 0.30, "hmm": 0.20, "ker": 0.18, "adx": 0.10, "dist": 0.22,
    }, True),
]

# Use 5-stock fast mode for speed
tickers_override = ["MSFT", "AAPL", "NVDA", "GOOGL", "DELL"]

print("=" * 100)
print("消融實驗（Ablation Testing）— 5 檔 fast mode（MSFT/AAPL/NVDA/GOOGL/DELL）")
print("=" * 100)
print(f"\n{'Config':<22} | {'Trades':>6} {'Win%':>5} {'Return%':>8} {'Sharpe':>7} "
      f"{'MaxDD%':>7} {'PF':>5} {'AvgWin':>7} {'AvgLoss':>8} {'Final$':>8}")
print("-" * 100)

results = []
for label, weights, no_vetoes in configs:
    cfg = make_config(weights, disable_vetoes=no_vetoes)
    engine = BacktestEngine(cfg, tickers=tickers_override)
    summary = engine.run()
    if not summary:
        print(f"{label:<22} | FAILED")
        continue
    metrics = compute_metrics(summary, cfg)
    results.append((label, metrics))
    print(f"{label:<22} | {metrics['total_trades']:>6} {metrics['win_rate']*100:>4.0f}% "
          f"{metrics['total_return_pct']:>+7.2f}% {metrics['sharpe']:>7.2f} "
          f"{metrics['max_drawdown_pct']:>+6.2f}% {str(metrics['profit_factor']):>5} "
          f"${metrics['avg_win']:>6.0f} ${metrics['avg_loss']:>+7.0f} "
          f"${metrics['final_equity']:>7.0f}")

# Summary analysis
print("\n" + "=" * 100)
print("邊際貢獻分析（vs 前一配置）：")
print("-" * 100)
if len(results) >= 4:
    for i in range(1, min(4, len(results))):
        prev = results[i-1][1]
        curr = results[i][1]
        d_sharpe = curr["sharpe"] - prev["sharpe"]
        d_dd = curr["max_drawdown_pct"] - prev["max_drawdown_pct"]  # negative = improvement
        d_ret = curr["total_return_pct"] - prev["total_return_pct"]
        component = results[i][0].split("+")[-1].strip() if "+" in results[i][0] else "KER+ADX"
        print(f"  +{component:<15}: Sharpe {'+' if d_sharpe>=0 else ''}{d_sharpe:+.2f}, "
              f"MaxDD {d_dd:+.2f}% ({'改善' if d_dd > 0 else '惡化'}), "
              f"Return {d_ret:+.2f}%")

if len(results) >= 5:
    full = results[3][1]
    no_veto = results[4][1]
    d_sharpe = full["sharpe"] - no_veto["sharpe"]
    d_dd = full["max_drawdown_pct"] - no_veto["max_drawdown_pct"]
    d_ret = full["total_return_pct"] - no_veto["total_return_pct"]
    print(f"\n  Veto 機制貢獻:     Sharpe {'+' if d_sharpe>=0 else ''}{d_sharpe:+.2f}, "
          f"MaxDD {d_dd:+.2f}% ({'改善' if d_dd > 0 else '惡化'}), "
          f"Return {d_ret:+.2f}%")
