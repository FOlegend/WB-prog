"""
production.contracts — cross-engine data contracts (Phase 0)

Shared, dependency-free models used by the Entry / Stop / Risk engines:

    EntryDecision   entry intent (signal semantics, no sizing)
    StopPlan        stop levels + derivation (initial vs current)
    RiskDecision    three-valued sizing decision (APPROVE / RESIZE / REJECT)
    OrderIntent     execution intent (signal / execution / risk kept separate)
    PositionState   open-position view (incl. current_stop_price concept)

Conventions (see each module's docstring for the full rule set)
    session_date : "YYYY-MM-DD" naive US trading-session date
    prices / ATR : USD per share
    multiple     : dimensionless
    risk amounts : USD
    shares       : integer

Every model exposes `validate()` and `as_dict()`; validation is deterministic
and raises `ContractError` with (contract, field, detail).

Phase 0 scope: these contracts are NOT wired into the production pipeline.
"""
from __future__ import annotations

from production.contracts.base import (CONTRACT_VERSION, ContractError,
                                      ExecutionWindow, PriceBar, Provenance,
                                      deterministic_id)
from production.contracts.entry import EntryDecision, entry_decision
from production.contracts.exit import ExitDecision
from production.contracts.order import OrderIntent
from production.contracts.position import PositionState
from production.contracts.risk import RiskDecision
from production.contracts.stop import StopPlan

__all__ = [
    "CONTRACT_VERSION", "ContractError", "ExecutionWindow", "PriceBar",
    "Provenance", "deterministic_id",
    "EntryDecision", "entry_decision",
    "StopPlan",
    "ExitDecision",
    "RiskDecision",
    "OrderIntent",
    "PositionState",
]
