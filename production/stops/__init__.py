"""
production.stops — Stop Engine (Phase 1, isolated)

Responsibility split (architecture decision, Phase 1 spec):

    Stop Engine : computes / updates the STOP LEVEL, with a reason code
    Exit Engine : decides WHETHER to exit (future phase — not here)

Public surface
--------------
    initial_stop_plan(...)   -> StopPlan            (initial ATR stop)
    update_stop(plan, ...)   -> StopUpdate          (trailing, no loosening)
    plan_from_position(...)  -> StopPlan            (read-only bridge)
    atr_initial_stop(...)    -> StopCalculation     (raw formula, legacy-parity)
    atr_trailing_stop(...)   -> TrailingEvaluation  (raw formula, legacy-parity)
    StopPlan                                        (re-exported contract)

Phase 1 guarantees
------------------
* Deterministic: no clock, no randomness, no global state.
* Explainable: every result carries a reason code + human detail.
* No loosening: LONG updated >= previous, SHORT updated <= previous.
* Isolated: nothing here is imported by the production pipeline, and
  `src/agents/risk_manager.py` / `src/portfolio/portfolio_manager.py` are
  untouched (parity is proven by tests, not by refactoring).
* Structure stops are NOT implemented (optional contract field only) —
  see STOP_ENGINE_BACKLOG.md.
"""
from __future__ import annotations

# import order matters for the circular-safe relative imports in engine.py
from production.stops.errors import StopEngineError
from production.stops.initial import StopCalculation, atr_initial_stop
from production.stops.trailing import TrailingEvaluation, atr_trailing_stop
from production.stops.engine import (ENGINE_NAME, StopUpdate, initial_stop_plan,
                                     plan_from_position, update_stop)

# re-exported contract (architecture decision 6 — contracts hold the models)
from production.contracts.stop import StopPlan

__all__ = [
    "StopPlan",
    "StopCalculation",
    "TrailingEvaluation",
    "StopUpdate",
    "StopEngineError",
    "atr_initial_stop",
    "atr_trailing_stop",
    "initial_stop_plan",
    "plan_from_position",
    "update_stop",
    "ENGINE_NAME",
]
