"""
agents/regime.py — Production Regime v1 wrapper (FROZEN)

Calls regime_dual_engine.engine.compute_regime_decision (the frozen Regime v1:
HMM 50% + PIT Market Breadth 50% → composite → BULL/SIDEWAYS/BEAR + Thrust +
Divergence). Distribution Days are NOT computed (overlay off by default).

The legacy v3 regime (src.agents.regime_agent.market_regime / regime_score_engine)
is deliberately NOT imported here.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from regime_dual_engine.engine import compute_regime_decision  # Regime v1 (frozen)
from regime_dual_engine.breadth_data import get_breadth_series  # current-constituent fallback
from regime_dual_engine.pit_breadth_data import get_breadth    # PIT breadth (preferred)


def load_breadth(cfg, end: str = "2025-07-31") -> pd.DataFrame:
    """PIT breadth preferred; falls back to current-constituent series if the
    PIT cache is unavailable. Both are pre-computed research caches."""
    pit = get_breadth("pit", rebuild=False, end=end)
    if pit is not None and len(pit) > 0:
        return pit
    return get_breadth_series(end=end, verbose=False)


def compute_market_regime(spy_df: pd.DataFrame, breadth_df: pd.DataFrame,
                          cfg, hmm_cache: dict | None = None) -> dict:
    """Frozen Regime v1 decision on SPY.

    Returns the public schema:
      {regime_label, composite_score, position_size_mult, strategy_mode, veto_flags}
    plus a _diag payload (hmm/breadth/dist diagnostics).
    """
    return compute_regime_decision(spy_df, breadth_df, cfg.regime, hmm_cache=hmm_cache)


# ---- freeze guard: fail loudly if a legacy v3 regime symbol leaks in ----
_LEGACY_V3_SYMBOLS = ("market_regime", "regime_score_engine")


def assert_not_v3() -> None:
    """Test hook: verify the production regime module does not reference v3."""
    import inspect
    src = inspect.getsource(compute_market_regime)
    for sym in _LEGACY_V3_SYMBOLS:
        assert sym not in src, f"legacy v3 regime symbol leaked: {sym}"
