"""
audit_return_calculation.py — Backtest Return Calculation Audit

Traces how return% is computed and what trade rules actually produce it.
Uses the CURRENT production config (default Config: Current Weights, gate mode,
score_full=70) on the dynamic universe (top 20, monthly, 2018-2025).

Outputs:
  1. RETURN_CALCULATION_AUDIT summary (printed to stdout + saved to reports/)
  2. Trade-level audit log: first 20 / last 20 / top 20 winning / top 20 losing
     (saved to reports/audit_trade_log.json)
"""
from __future__ import annotations

import os
import sys
import json

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from dynamic_universe_backtest import DynamicBacktestEngine, build_rebalance_calendar
from src.backtest.engine import compute_metrics


def load_data_from_cache(end: str = "2025-07-31"):
    cache_dir = os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(cache_dir):
        cache_dir = os.path.join(_REPO_ROOT, "data", "cache", "equities")
    all_tickers = [f.replace(".csv", "") for f in os.listdir(cache_dir)
                   if f.endswith(".csv")]
    cutoff = pd.Timestamp(end)
    all_data = {}
    for t in sorted(all_tickers):
        p = os.path.join(cache_dir, f"{t}.csv")
        try:
            df = pd.read_csv(p, parse_dates=["datetime"])
            if len(df) > 0:
                df = df[df["datetime"] <= cutoff].reset_index(drop=True)
                all_data[t] = df
        except Exception:
            pass
    return all_data


def fmt_trade(t: dict) -> dict:
    """Round floats in a trade record for clean JSON output."""
    out = {}
    for k, v in t.items():
        if isinstance(v, float):
            out[k] = round(v, 3)
        else:
            out[k] = v
    return out


def print_trade_block(title: str, trades: list) -> None:
    print(f"\n{'='*80}")
    print(f"  {title} ({len(trades)} trades)")
    print(f"{'='*80}")
    cols = ["ticker", "entry_date", "entry_price", "exit_date", "exit_price",
            "exit_reason", "shares", "pnl", "r_multiple", "regime_score_at_entry",
            "size_mult_at_entry"]
    hdr = f"{'TICKER':<7}{'ENTRY':<12}{'ENT_PX':>9}{'EXIT':<12}{'EXIT_PX':>9}" \
          f"{'EXIT_REASON':<16}{'SHR':>5}{'PNL':>10}{'R':>7}{'REGIME':>8}{'MULT':>6}"
    print(hdr)
    print("-" * len(hdr))
    for t in trades:
        r = t.get("r_multiple")
        r_s = f"{r:.2f}" if r is not None else "-"
        rs = t.get("regime_score_at_entry")
        rs_s = f"{rs:.0f}" if rs is not None else "-"
        sm = t.get("market_size_mult_at_entry")
        sm_s = f"{sm:.2f}" if sm is not None else "-"
        print(f"{t['ticker']:<7}{str(t['entry_date']):<12}{t['entry_price']:>9.2f}"
              f"{str(t['exit_date']):<12}{t['exit_price']:>9.2f}"
              f"{str(t['exit_reason']):<16}{t['shares']:>5}{t['net_pnl']:>10.2f}"
              f"{r_s:>7}{rs_s:>8}{sm_s:>6}")


def main():
    start = "2018-01-01"
    end = "2025-07-31"
    top_n = 20

    print("=" * 80)
    print("  BACKTEST RETURN CALCULATION AUDIT")
    print("  Config: Current Weights (production default) | gate mode | score_full=70")
    print("  Universe: dynamic top 20 | monthly rebalance | 2018-2025")
    print("=" * 80)

    # Load data + buckets
    print("\n[1/3] Loading data...")
    all_data = load_data_from_cache(end=end)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"  {len(all_data)} tickers, {len(buckets)} buckets, "
          f"{len(all_rd)} rebalance dates")

    # Run backtest with default (production) config
    print("\n[2/3] Running backtest with production config...")
    cfg = Config()  # Current Weights (0.20/0.30/0.18/0.10/0.22), gate, score_full=70
    eng = DynamicBacktestEngine(
        cfg, all_data, all_rd, buckets, top_n=top_n,
        start=start, end=end, no_regime=False,
        benchmark="SPY", progress=False,
    )
    summary = eng.run()
    metrics = compute_metrics(summary, cfg)

    trades = summary["trade_log"]
    equity_curve = summary["equity_curve"]

    print(f"  {len(trades)} trades, final_equity=${summary['final_equity']:.2f}")

    # Sort trades
    trades_sorted_by_pnl = sorted(trades, key=lambda t: t["net_pnl"], reverse=True)

    print("\n[3/3] Trade-level audit log...")
    print_trade_block("FIRST 20 TRADES (chronological)", trades[:20])
    print_trade_block("LAST 20 TRADES (chronological)", trades[-20:])
    print_trade_block("TOP 20 WINNING TRADES (by net_pnl)", trades_sorted_by_pnl[:20])
    print_trade_block("TOP 20 LOSING TRADES (by net_pnl)",
                      sorted(trades, key=lambda t: t["net_pnl"])[:20])

    # Exit reason distribution
    from collections import Counter
    exit_dist = Counter(t["exit_reason"] for t in trades)
    print(f"\nExit reason distribution:")
    for reason, count in exit_dist.most_common():
        print(f"  {reason:<16} {count:>5} trades")

    # Save audit log
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    audit_log = {
        "meta": {
            "config": "Current Weights (production default)",
            "weights": cfg.regime_score_weights,
            "exposure_mode": cfg.regime_exposure_mode,
            "score_full": cfg.regime_score_full,
            "start": start, "end": end, "top_n": top_n,
            "total_trades": len(trades),
        },
        "metrics": metrics,
        "first_20": [fmt_trade(t) for t in trades[:20]],
        "last_20": [fmt_trade(t) for t in trades[-20:]],
        "top_20_winning": [fmt_trade(t) for t in trades_sorted_by_pnl[:20]],
        "top_20_losing": [fmt_trade(t) for t in sorted(trades, key=lambda x: x["net_pnl"])[:20]],
        "exit_reason_distribution": dict(exit_dist),
        "all_trades": [fmt_trade(t) for t in trades],
    }
    log_path = os.path.join(out_dir, "audit_trade_log.json")
    with open(log_path, "w") as f:
        json.dump(audit_log, f, indent=2, default=str)
    print(f"\n  Audit log saved to: {log_path}")

    # =====================================================================
    # RETURN_CALCULATION_AUDIT summary
    # =====================================================================
    print("\n" + "=" * 80)
    print("  RETURN_CALCULATION_AUDIT")
    print("=" * 80)

    print("\n1. Return% formula:")
    print(f"   initial_equity = starting_equity = cfg.capital_usd = "
          f"{summary['starting_equity']:.2f} USD")
    print(f"   (= starting_capital {cfg.starting_capital:.0f} HKD x fx_to_usd {cfg.fx_to_usd})")
    print(f"   final_equity = state['equity'] at end = {summary['final_equity']:.2f} USD")
    print(f"   formula = (final_equity / initial_equity - 1) * 100")
    print(f"   = ({summary['final_equity']:.2f} / {summary['starting_equity']:.2f} - 1) * 100")
    print(f"   = {metrics['total_return_pct']:.2f}%")

    print("\n2. Equity curve update method:")
    print("   mark_to_market(state, prices) each trading day:")
    print("   equity = cash + sum(shares x close_price for open positions)")
    print("   - open_position() deducts cash on buy (notional + slippage)")
    print("   - close_position() adds cash on sell (proceeds - fees)")

    print("\n3. Entry rule:")
    print("   Function: DynamicBacktestEngine.run() entry loop")
    print("   Conditions (ALL must be true):")
    print("     a. global_size_mult > 0  (regime not in cash)")
    print("     b. ticker in monthly screened bucket (top 20 by RS)")
    print("     c. ticker not already held")
    print("     d. net > entry_threshold (0.25), where:")
    print("        net = 0.35*regime_s + 0.65*tech_score")
    print("        (regime_s = (regime_score-50)/50, tech_score = technicals ensemble)")
    print("     e. size_position() returns allow=True (risk budget > 1 share)")

    print("\n4. Exit rule:")
    print("   Function: _exit_check() in portfolio_manager.py")
    print("   Conditions (checked in order):")
    print("     1. STOP_LOSS: price <= entry - 1.5*ATR")
    print("     2. TAKE_PROFIT: price >= entry + 2.5*ATR")
    print("     3. TRAILING_STOP: after +1R, exit if price <= highest - 1.5*ATR")
    print("     4. TIME_STOP: held >= 30 days")
    print("     5. SIGNAL_EXIT: technicals_agent == bearish")
    print("     6. BACKTEST_END: forced close at end of backtest")

    print("\n5. Position sizing rule:")
    print("   Function: size_position() in risk_manager.py")
    print("   risk_amount = equity * risk_per_trade(1%) * size_mult")
    print("   stop_distance = ATR * stop_atr_mult(1.5)")
    print("   shares = floor(risk_amount / stop_distance)")
    print("   capped by: 25% equity, available cash, max 5 positions")

    print("\n6. Are technicals used?")
    print("   YES — technicals_agent ensemble (EMA trend + mean reversion + momentum")
    print("   + volatility) produces tech_score used in net score for entry, and")
    print("   tech signal used for SIGNAL_EXIT.")

    print("\n7. Is ATR used?")
    print("   YES — ATR(14) used in: stop distance (1.5x), take profit (2.5x),")
    print("   trailing stop (1.5x), and risk-based share sizing.")

    print("\n8. Backtest classification:")
    print("   Type C — Simplified Swing Engine")
    print("   (has entry/exit + ATR stop + ATR target + risk sizing, but NO")
    print("   explicit setup agent: no pivot breakout, no pullback setup, no VCP,")
    print("   no setup_score. Entry is a weighted score threshold, not a setup signal.)")

    print("\n9. Key limitation:")
    print("   Entry is NOT setup-based. A ticker enters merely because:")
    print("   (regime allows) AND (in bucket) AND (weighted score > 0.25).")
    print("   There is no explicit breakout/pullback/pivot/VCP trigger. The")
    print("   'technicals ensemble' is a directional vote, not a trade setup.")
    print("   => return% is a RESEARCH PROXY, not live swing trading performance.")

    print("\n10. Recommended next engineering step:")
    print("   Build setup_agent.py (breakout + pullback setups with setup_score),")
    print("   then wire setup_score into the entry gate. Only after that does the")
    print("   backtest become a full swing engine (Type D) worth walking forward.")


if __name__ == "__main__":
    main()
