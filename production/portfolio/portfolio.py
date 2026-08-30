"""
portfolio/portfolio.py — Production portfolio decisions

Reuses the validated exit logic (_exit_check from src.portfolio) and the
state persistence (src.state). No legacy weighted entry (0.35×regime+0.65×tech
is NOT used here — entries are Setup-v1 gated only).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.portfolio.portfolio_manager import _exit_check  # same exit rules as backtest
from src.state.state import (load_state, save_state, mark_to_market,
                             open_position, close_position)


def load_position_state(cfg):
    os.makedirs(os.path.dirname(cfg.state_file), exist_ok=True)
    return load_state(cfg.state_file, cfg.capital_usd)


def exit_check(pos: dict, bar: dict, date_str: str, tech_signal: str, cfg) -> dict | None:
    """Gap-aware OHLC exit check (STOP/TARGET/TRAILING/TIME/SIGNAL)."""
    return _exit_check(pos, bar, date_str, tech_signal, cfg)


def build_order_buy(ticker: str, price: float, sizing: dict, setup: dict,
                    regime: dict, reason: str) -> dict:
    """Human-actionable BUY recommendation (no auto-execution)."""
    return {
        "ticker": ticker, "action": "BUY",
        "shares": sizing["shares"],
        "price": round(price, 4),                      # signal close (next-open entry)
        "entry_model": "NEXT_OPEN",
        "stop": sizing["stop_price"], "take_profit": sizing["take_profit"],
        "risk_reward": sizing["risk_reward"],
        "setup_type": setup.get("setup_type"),
        "setup_score": setup.get("setup_score"),
        "setup_quality_mult": setup.get("setup_quality_mult"),
        "regime": regime.get("regime_label"),
        "composite_score": regime.get("composite_score"),
        "regime_fit": "BULL/SIDEWAYS OK" if regime.get("regime_label") in ("BULL", "SIDEWAYS") else "BEAR-blocked",
        "reason": reason,
    }
