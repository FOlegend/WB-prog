"""
main.py — Production V2 daily briefing runner (HUMAN-IN-THE-LOOP)

Thin CLI over the canonical pipeline (production.pipeline.run_daily):

  python production/main.py                          # live (yfinance refresh)
  python production/main.py --as-of 2025-07-31       # point-in-time (cache)
  python production/main.py --tickers NVDA,AMD       # skip screener (provided)
  python production/main.py --no-screen              # exits only

Every run writes:
  reports/decision_<as_of>.json    machine-readable DecisionRecord (ledger)
  reports/briefing_<as_of>.md      human-readable briefing
  reports/orders_<as_of>.json      proposed orders (kept for compatibility)

Frozen: Regime v1 + Setup v1. No v3 regime, no weighted entry, no DistDays.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.datasource import build_live_source, build_cached_source
from production.pipeline import run_daily as pipeline_run_daily
from production.ledger import save_ledger
from production.reporting.briefing import render_briefing, save_briefing


def run_daily(cfg: ProductionConfig, tickers=None, no_screen=False,
              as_of=None, use_live_source: bool = True):
    """Backward-compatible entry used by tests and old callers.

    Delegates to the canonical pipeline (production.pipeline.run_daily).
    Returns the DecisionRecord dict (v2 behaviour) instead of None.
    """
    as_of = as_of or datetime.now().strftime("%Y-%m-%d")
    source = (build_live_source(cfg) if use_live_source
              else build_cached_source(cfg))
    if no_screen:
        mode, cands = "held_only", None
    elif tickers:
        mode, cands = "provided", list(tickers)
    else:
        mode, cands = "live", None

    rec = pipeline_run_daily(as_of, _load_state(cfg), source, cfg,
                             screen_mode=mode, candidate_tickers=cands)
    _emit(rec, cfg)
    return rec


def _load_state(cfg) -> dict:
    from production.portfolio.portfolio import load_position_state
    return load_position_state(cfg)


def _emit(rec: dict, cfg: ProductionConfig) -> None:
    as_of = rec["as_of"]
    print(f"=== Production V2 — 每日決策（{as_of}） pipeline={rec['pipeline_status']} ===")
    print(f"  regime   : {rec['regime']['output']['regime_label'] if rec['regime']['output'] else 'FAIL'}"
          f"  (score={rec['regime']['output']['composite_score'] if rec['regime']['output'] else '-'})")
    print(f"  screener : {rec['screener']['status']}  {rec['screener']['n_candidates']} candidates")
    print(f"  exits    : {len(rec['exits']['proposed'])} proposed | "
          f"entries: {rec['entries']['n_buys']} proposed BUY")
    for w in rec["warnings"]:
        print(f"  ⚠️ {w}")

    # ledger (machine-readable)
    ledger_path = save_ledger(rec, cfg)
    print(f"📄 決策記錄：{ledger_path}")

    # human briefing (render from the record)
    regime_out = rec["regime"]["output"] or {}
    reg_for_brief = {**regime_out,
                     "_diag": {"hmm_bull_prob": None, "breadth_percentile": None}}
    buys = [o for o in rec["entries"]["proposed"]]
    sells = rec["exits"]["proposed"]
    orders = (buys + [{"ticker": s["ticker"], "action": "SELL",
                       "shares": s["shares"], "price": s["exit_price"],
                       "exit_reason": s["reason"], "exit_detail": s["detail"]}
                      for s in sells])
    ctx = [{"_equity": rec["positions"]["equity"],
            "_cash": rec["positions"]["cash"],
            "_n_open": rec["positions"]["n_open"]}]
    md = render_briefing(reg_for_brief, ctx, orders, as_of, cfg)
    path = save_briefing(md, as_of, cfg)
    print(md)
    print(f"\n📄 簡報：{path}")

    orders_path = os.path.join(cfg.reports_dir, f"orders_{as_of}.json")
    with open(orders_path, "w", encoding="utf-8") as f:
        json.dump(orders, f, indent=2, ensure_ascii=False, default=str)
    print(f"📄 訂單：{orders_path}")
    print("\n👉 審核後手動下單；next-open 進場時確認 gap ≤2% 且 extension ≤3%。")


def main():
    ap = argparse.ArgumentParser(description="Production V2 daily decision")
    ap.add_argument("--tickers", default="", help="指定標的（逗號分隔），跳過 screener")
    ap.add_argument("--no-screen", action="store_true", help="只檢查持倉出場")
    ap.add_argument("--as-of", default="", help="point-in-time 日期")
    ap.add_argument("--cached", action="store_true",
                    help="用 CachedSource（離線，不做網路更新）")
    args = ap.parse_args()
    cfg = ProductionConfig()
    tickers = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
               if args.tickers else None)
    run_daily(cfg, tickers=tickers, no_screen=args.no_screen,
              as_of=args.as_of or None,
              use_live_source=not args.cached)


if __name__ == "__main__":
    main()
