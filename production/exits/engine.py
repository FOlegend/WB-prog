"""
engine.py — Exit Engine facade (Phase 2, ISOLATED)

One deterministic entry point:

    decision = evaluate_exit(context)      # -> ExitDecision

It answers exactly four questions (Phase 2 responsibility list):
    1. should we exit?         -> should_exit
    2. why?                    -> exit_reason_code + detail
    3. at what modelled price? -> exit_price
    4. which fill model?       -> fill_model

It does NOT: size positions, calculate shares, send/execute orders, modify
portfolio state, call technicals_agent, or calculate a trailing stop.

The legacy implementation `src/portfolio/portfolio_manager.py::_exit_check` is
the oracle: every reason / price / fill model is reproduced exactly (priority
STOP_LOSS > TAKE_PROFIT > TRAILING_STOP > TIME_STOP > SIGNAL_EXIT).

Isolation: nothing in the production pipeline imports this package
(`test_phase2_isolation_not_wired` asserts it).
"""
from __future__ import annotations

from production.contracts.exit import ExitDecision
from production.exits.context import ExitContext
from production.exits.rules import RULES

ENGINE_NAME = "production/exits/engine.py"
COMPONENT_NAME = "exit_engine"


def hold_decision(ctx: ExitContext) -> ExitDecision:
    """Explicit 'no exit trigger' decision (never a bare None)."""
    return ExitDecision(
        should_exit=False, ticker=ctx.position.ticker,
        direction=ctx.position.direction, session_date=ctx.session_date,
        detail="無出場觸發（續抱）", bar=ctx.bar,
        stop_reference_price=ctx.stop_plan.current_stop_price,
        target_reference_price=ctx.target_reference_price,
        provenance=ctx.provenance).validate()


def evaluate_exit(ctx: ExitContext) -> ExitDecision:
    """Evaluate the five rules in legacy priority order.

    Deterministic: identical contexts always produce identical decisions.
    """
    ctx.validate()
    for rule in RULES:
        decision = rule(ctx)
        if decision is not None:
            return decision.validate()
    return hold_decision(ctx)
