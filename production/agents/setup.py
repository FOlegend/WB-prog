"""
agents/setup.py — Production Setup v1 wrapper (FROZEN: Pullback Only)

Uses src.agents.setup_agent.setup_signal as the source implementation with
setup_enabled_types = ["pullback"] (frozen). No Breakout / VCP / new types.
Regime context is consumed ONLY as a gate (BEAR → no long) — Setup does not
recompute market regime.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.agents.setup_agent import setup_signal  # frozen Setup v1 source


def evaluate_setup(df: pd.DataFrame, cfg, rs_rank: int | None = None) -> dict:
    """Pullback-only setup evaluation.

    df: ticker OHLCV (point-in-time slice). rs_rank: 1-based RS rank from the
    screener bucket (1 = strongest). Returns the setup_signal contract:
      {valid, setup_type, setup_score, setup_quality_mult, signal_close, atr,
       prior_high20, components, entry_reason}
    """
    # freeze guard: production must never enable breakout
    enabled = list(getattr(cfg, "setup_enabled_types", ["pullback"]))
    assert enabled == ["pullback"], (
        f"Setup v1 is frozen to Pullback Only, got {enabled}")
    return setup_signal(df, cfg, rs_rank=rs_rank)
