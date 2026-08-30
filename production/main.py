"""
main.py — Production V2 daily briefing runner (HUMAN-IN-THE-LOOP)

Pipeline (exact):
  Market Data → Regime v1 (HMM + Market Breadth) → Existing Screener
             → Setup v1 (Pullback Only) → Risk / Position Sizing
             → Daily Human-readable Briefing → Human decision

Frozen: Regime v1 (regime_dual_engine) + Setup v1 (src/agents/setup_agent,
Pullback Only). No v3 regime, no weighted entry, no Distribution Days.

Usage:
  python production/main.py                          # live (uses yfinance)
  python production/main.py --as-of 2025-07-31       # point-in-time demo
"""
from __future__ import annotations

import argparse
import json
import sys
import os
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.config import ProductionConfig
from production.agents.regime import compute_market_regime, load_breadth
from production.agents.setup import evaluate_setup
from production.screener.screener import run_screener
from production.risk.risk import size_swing_position
from production.portfolio.portfolio import (load_position_state, exit_check,
                                            build_order_buy)
from production.reporting.briefing import render_briefing, save_briefing
from src.state.state import mark_to_market
from src.agents.technicals_agent import technicals_signal  # exit signal only


def _fetch_daily(ticker: str, cfg=None, as_of: str | None = None):
    """Cache-first (offline, point-in-time) then yfinance fallback."""
    if cfg is not None and os.path.isdir(cfg.cache_dir):
        p = os.path.join(cfg.cache_dir, f"{ticker}.csv")
        if os.path.exists(p):
            try:
                df = pd.read_csv(p, parse_dates=["datetime"])
                if as_of and "datetime" in df.columns:
                    df = df[pd.to_datetime(df["datetime"]) <= pd.Timestamp(as_of)].reset_index(drop=True)
                if len(df) >= 60:
                    return df
            except Exception:
                pass
    from src.data.data_fetcher import fetch_daily
    df = fetch_daily(ticker, start="", period="2y")
    if as_of and "datetime" in df.columns:
        df = df[pd.to_datetime(df["datetime"]) <= pd.Timestamp(as_of)].reset_index(drop=True)
    return df


def _fetch_spy(cfg, as_of: str | None = None):
    return _fetch_daily(cfg.regime_market_index, cfg=cfg, as_of=as_of)


def run_daily(cfg: ProductionConfig, tickers=None, no_screen=False, as_of=None):
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    print(f"=== Production V2 — 每日簡報（{as_of}）===")

    state = load_position_state(cfg)
    held = [p["ticker"] for p in state["open_positions"]]

    # ---- 1. Market Regime v1 (frozen) ----
    print("\n[1/6] Regime v1（SPY）...")
    spy = _fetch_spy(cfg, as_of=as_of)
    if len(spy) < 60:
        print("  SPY 資料不足，中止"); return
    breadth = load_breadth(cfg, end=as_of)
    regime = compute_market_regime(spy, breadth, cfg)
    print(f"  regime={regime['regime_label']}  score={regime['composite_score']:.0f}  "
          f"size_mult={regime['position_size_mult']:.2f}  "
          f"vetoes={regime['veto_flags']}")

    # ---- 2. Screener (reuse) ----
    if no_screen:
        cand_tickers = held
    elif tickers:
        cand_tickers = list(set(tickers + held))
    else:
        print("\n[2/6] Screener（既有 6-filter）...")
        cand = run_screener(cfg)
        cand_tickers = list(set(cand["ticker"].tolist() + held))
        print(f"  篩出 {len(cand_tickers)} 檔候選")

    # ---- 3/4. Setup v1 (pullback) + Risk sizing ----
    print("\n[3-4/6] Setup v1（Pullback Only）+ Risk sizing...")
    orders = []
    n_open = len(state["open_positions"])
    held_set = set(held)
    candidates_ctx = []
    # exits first
    mark_to_market(state, {})
    for pos in state["open_positions"]:
        t = pos["ticker"]
        df = _fetch_daily(t, cfg=cfg, as_of=as_of)
        if df is None or len(df) < 60:
            continue
        sub = df
        bar = {"open": float(sub["open"].iloc[-1]), "high": float(sub["high"].iloc[-1]),
               "low": float(sub["low"].iloc[-1]), "close": float(sub["close"].iloc[-1])}
        try:
            tech_sig = technicals_signal(sub, cfg)["signal"]
        except Exception:
            tech_sig = "neutral"
        ex = exit_check(pos, bar, as_of, tech_sig, cfg)
        if ex is not None:
            orders.append({"ticker": t, "action": "SELL", "shares": pos["shares"],
                           "price": bar["close"], "exit_reason": ex["reason"],
                           "exit_detail": ex["detail"]})
    # entries: pullback only, gated by regime (BEAR → no long)
    regime_allows = regime["position_size_mult"] > 0
    for t in cand_tickers:
        if t in held_set:
            continue
        df = _fetch_daily(t, cfg=cfg, as_of=as_of)
        if df is None or len(df) < 60:
            continue
        rs_rank = cand_tickers.index(t) + 1 if t in cand_tickers else None
        setup = evaluate_setup(df, cfg, rs_rank=rs_rank)
        if not setup.get("valid"):
            continue
        price = float(df["close"].iloc[-1])
        atr = setup.get("atr") or 0.0
        if not regime_allows:
            orders.append({"ticker": t, "action": "HOLD", "price": price,
                           "reason": "BEAR regime — no new long"})
            continue
        sizing = size_swing_position(state["equity"], state["cash"], price, atr,
                                     regime["position_size_mult"],
                                     setup.get("setup_quality_mult", 1.0),
                                     n_open, cfg)
        if not sizing.get("allow"):
            continue
        orders.append(build_order_buy(t, price, sizing, setup, regime,
                                      reason=setup.get("entry_reason", "")))
        n_open += 1
    candidates_ctx = [{"_equity": state["equity"], "_cash": state["cash"],
                       "_n_open": len(state["open_positions"])}]

    # ---- 5/6. Briefing ----
    print("\n[5-6/6] 產出簡報...")
    md = render_briefing(regime, candidates_ctx, orders, as_of, cfg)
    path = save_briefing(md, as_of, cfg)
    print(md)
    print(f"\n📄 簡報：{path}")
    # machine-readable orders
    orders_path = os.path.join(cfg.reports_dir, f"orders_{as_of}.json")
    with open(orders_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2, ensure_ascii=False, default=str)
    print(f"📄 訂單：{orders_path}")
    print("\n👉 審核後手動下單；next-open 進場時確認 gap ≤2% 且 extension ≤3%。")


def main():
    ap = argparse.ArgumentParser(description="Production V2 daily briefing")
    ap.add_argument("--tickers", default="", help="指定標的（逗號分隔），跳過 screener")
    ap.add_argument("--no-screen", action="store_true", help="只檢查持倉出場")
    ap.add_argument("--as-of", default="", help="point-in-time 日期（demo）")
    args = ap.parse_args()
    cfg = ProductionConfig()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
    run_daily(cfg, tickers=tickers, no_screen=args.no_screen,
              as_of=args.as_of or None)


if __name__ == "__main__":
    main()
