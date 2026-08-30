"""
risk/risk.py — Production risk & position sizing (reuse validated logic)

Calls src.agents.risk_manager.size_position — the SAME ATR-based sizing used by
the validated dynamic backtest. Regime v1 provides position_size_mult; Setup v1
provides setup_quality_mult; effective multiplier = global × quality.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.agents.risk_manager import size_position  # same fn as backtest


def size_swing_position(equity: float, cash: float, price: float, atr: float,
                        size_mult: float, quality_mult: float, n_open: int,
                        cfg) -> dict:
    """Position sizing with effective multiplier = size_mult × quality_mult.

    size_mult   : from Regime v1 position_size_mult (0-1)
    quality_mult: from Setup v1 setup_quality_mult (0, 0.5, 0.75, 1.0)
    Returns size_position contract: {allow, shares, stop_price, take_profit,
    risk_reward, ...}
    """
    eff = size_mult * quality_mult
    return size_position(equity, cash, price, atr, eff, n_open, cfg)
