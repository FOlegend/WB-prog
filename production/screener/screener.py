"""
screener/screener.py — Production Screener wrapper (REUSE, do not rebuild)

Calls the existing 6-filter pipeline (src.screener.screener.screen) unchanged:
Market Cap / Dollar Volume / Price / ATR% / RS vs SPY / ADX. Output feeds
Setup v1 (the RS-sorted list provides rs_rank per candidate).
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.screener.screener import screen, UNIVERSE  # reuse existing (do not rebuild)


def run_screener(cfg) -> pd.DataFrame:
    """Return the screened candidates DataFrame (ticker + metrics), RS-sorted.

    Falls back to a default universe slice if the live screen returns empty
    (same behavior as main.py).
    """
    cand = screen(cfg)
    if cand is None or cand.empty:
        return pd.DataFrame({"ticker": UNIVERSE[:10]})
    return cand
