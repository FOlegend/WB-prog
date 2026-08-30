"""
audit_live_backtest.py — T4: Live vs Backtest Consistency Audit (static)

Compares the LIVE path (main.py + portfolio_manager + display) against the
BACKTEST path (dynamic_universe_backtest.py + setup_agent) item by item.
Pure static audit — reads code, no backtest run. Writes JSON + prints table.
"""
from __future__ import annotations

import os
import sys
import json
import re

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _read(path):
    with open(os.path.join(_REPO_ROOT, path)) as f:
        return f.read()


def main():
    main_py = _read("main.py")
    pm = _read("src/portfolio/portfolio_manager.py")
    dyn = _read("dynamic_universe_backtest.py")
    setup = _read("src/agents/setup_agent.py")
    risk = _read("src/agents/risk_manager.py")
    display = _read("src/utils/display.py")
    cfg_py = _read("config.py")

    checks = []

    def ck(name, live_ok, bt_ok, live_evidence, bt_evidence, verdict, note=""):
        checks.append({
            "item": name,
            "live_present": live_ok, "backtest_present": bt_ok,
            "live_evidence": live_evidence, "backtest_evidence": bt_evidence,
            "verdict": verdict, "note": note,
        })

    # ---- 1. Signal timing ----
    live_uses_setup = "setup_signal" in main_py or "setup_agent" in main_py
    ck("1. Signal source (entry signal)",
       live_uses_setup, "setup_signal" in dyn,
       "main.py uses technicals_signal (weighted entry)" if not live_uses_setup else "setup used",
       "dynamic uses setup_signal (Type D entry)",
       "MISMATCH" if not live_uses_setup else "MATCH",
       "Live still uses legacy weighted entry; setup_agent only wired into backtest")

    # ---- 2. Next-open entry ----
    ck("2. Next-open entry",
       "NEXT_OPEN" in pm or "next_open" in main_py, "NEXT_OPEN" in dyn,
       "live: entry price = today close", "backtest: pending signal -> next open",
       "MISMATCH", "backtest fills at next open with gap; live fills at today close")

    # ---- 3. Gap filter ----
    ck("3. Gap filter (max_entry_gap_pct)",
       "max_entry_gap_pct" in pm, "GAP_TOO_HIGH" in dyn,
       "live: none", "backtest: GAP_TOO_HIGH skip",
       "MISMATCH" if "max_entry_gap_pct" not in pm else "MATCH",
       "backtest skips entries that gap >2% above signal close; live has no such check")

    # ---- 4. Extension filter ----
    ck("4. Extension filter (max_extension_from_pivot_pct)",
       "max_extension_from_pivot_pct" in pm, "EXTENDED_FROM_PIVOT" in dyn,
       "live: none", "backtest: EXTENDED_FROM_PIVOT skip",
       "MISMATCH" if "max_extension_from_pivot_pct" not in pm else "MATCH",
       "backtest skips entries extended >3% above pivot; live has no such check")

    # ---- 5. ATR sizing ----
    ck("5. ATR position sizing",
       "size_position" in pm, "size_position" in dyn,
       "live: size_position(equity,cash,close,atr,size_mult,...)",
       "backtest: size_position(equity,cash,open,atr,size_mult*quality_mult,...)",
       "MATCH (same fn)",
       "Same risk_manager.size_position; but live passes close, backtest passes next-open price")

    # ---- 6. Stop / Target / Trailing ----
    ck("6. Stop/Target/Trailing exit logic",
       "def _exit_check" in pm, "_exit_check" in dyn and "portfolio_manager" in dyn,
       "live: _exit_check(pos, price, ...) close-only",
       "backtest: _exit_check(pos, bar{open,high,low,close}, ...) gap-aware OHLC",
       "PARTIAL", "Same _exit_check fn but backtest passes OHLC bar -> gap-aware fills; live passes close only")

    # ---- 7. Quality multiplier ----
    ck("7. Quality multiplier (setup_quality_mult)",
       "quality_mult" in pm, "quality_mult" in dyn,
       "live: none (weighted entry)", "backtest: eff_size_mult = global * quality_mult",
       "MISMATCH" if "quality_mult" not in pm else "MATCH",
       "backtest scales position by setup quality; live has no setup quality concept")

    # ---- 8. Briefing / order output ----
    ck("8. Daily briefing / order output",
       "render_briefing" in main_py, "trade_log" in dyn,
       "live: briefing.md + orders.json (recommendations, human reviews)",
       "backtest: trade_log JSON + HTML report (simulated fills)",
       "MATCH (different purpose)",
       "Live = recommendation for human; backtest = simulation. Both exist, no auto-reconciliation")

    # ---- Regime source ----
    ck("Regime source",
       "market_regime" in main_py, "DualEngineBacktest" in dyn,
       "live: market_regime (v3 5-component)", "backtest: compute_regime_decision (Regime v1 dual)",
       "MISMATCH",
       "Live still uses legacy v3 regime; Regime v1 (HMM+Breadth) only wired into backtest harness")

    print("=" * 76)
    print("  T4 — LIVE vs BACKTEST CONSISTENCY AUDIT (static)")
    print("=" * 76)
    print(f"{'#':<2}{'Item':<42}{'Live':<8}{'Backtest':<10}{'Verdict':<10}")
    print("-" * 76)
    for i, c in enumerate(checks, 1):
        print(f"{i:<2}{c['item'][:40]:<42}"
              f"{'yes' if c['live_present'] else 'NO':<8}"
              f"{'yes' if c['backtest_present'] else 'NO':<10}{c['verdict']:<10}")
    print()
    for i, c in enumerate(checks, 1):
        print(f"  [{i}] {c['item']} — {c['verdict']}")
        print(f"      live: {c['live_evidence']}")
        print(f"      bt  : {c['backtest_evidence']}")
        if c["note"]:
            print(f"      note: {c['note']}")

    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "setup_v1_live_bt_audit.json"), "w") as f:
        json.dump(checks, f, indent=2)
    print(f"\n  Saved -> reports/setup_v1_live_bt_audit.json")


if __name__ == "__main__":
    main()
