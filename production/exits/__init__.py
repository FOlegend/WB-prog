"""
production.exits — Exit Engine (Phase 2, isolated)

Responsibility split (unchanged from the architecture decision):

    Stop Engine : computes / updates the STOP LEVEL (with a reason code)
    Exit Engine : decides WHETHER to exit, why, at what modelled price

Public surface
--------------
    build_exit_context(...)  -> ExitContext     (deterministic input assembly)
    evaluate_exit(ctx)       -> ExitDecision    (the five legacy rules, in order)
    hold_decision(ctx)       -> ExitDecision    (explicit no-exit decision)
    ExitContext, ExitConfigSnapshot, RULES
    ExitDecision                                (re-exported contract)

Phase 2 guarantees
------------------
* Parity-first: `src/portfolio/portfolio_manager.py::_exit_check` is the oracle
  and its reason codes / fill models / prices are reproduced exactly. Legacy
  code is NOT modified and NOT delegated to.
* Legacy-compatible vocabulary: STOP_LOSS / TAKE_PROFIT / TRAILING_STOP /
  TIME_STOP / SIGNAL_EXIT and GAP / STOP / TARGET / TRAILING / CLOSE.
* No trailing-stop arithmetic: trailing uses `StopPlan.current_stop_price` plus
  the arming gate supplied by the Stop Engine.
* tech_signal is injected by the caller — `technicals_agent` is never called here.
* Isolated: no production path imports this package; `production_used = 0`.
* LONG is production behaviour; SHORT is a tested contract-level mirror only.
"""
from __future__ import annotations

# import order mirrors the internal dependency direction (context -> rules -> engine)
from production.exits.context import (CONTEXT_NAME, ENGINE_VERSION,
                                      ExitConfigSnapshot, ExitContext,
                                      build_exit_context)
from production.exits.rules import (RULES, rule_signal_exit, rule_stop_loss,
                                    rule_take_profit, rule_time_stop,
                                    rule_trailing_stop)
from production.exits.engine import (COMPONENT_NAME, ENGINE_NAME,
                                     evaluate_exit, hold_decision)

# re-exported contract
from production.contracts.exit import ExitDecision

__all__ = [
    "ExitDecision",
    "ExitContext",
    "ExitConfigSnapshot",
    "build_exit_context",
    "evaluate_exit",
    "hold_decision",
    "RULES",
    "rule_stop_loss",
    "rule_take_profit",
    "rule_trailing_stop",
    "rule_time_stop",
    "rule_signal_exit",
    "ENGINE_NAME",
    "COMPONENT_NAME",
    "CONTEXT_NAME",
    "ENGINE_VERSION",
]
