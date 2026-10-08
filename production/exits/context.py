"""
context.py — ExitContext (Phase 2)

The complete, deterministic input for one exit evaluation. The Exit Engine
consumes this and nothing else (no globals, no clock, no config object, no
market-data access).

Hard architecture rule (Phase 2)
--------------------------------
The Exit Engine never calculates a trailing stop. This module is the ONLY place
that talks to the Stop Engine, and it does so merely to read the **arming gate**:

    Stop Engine -> StopPlan.current_stop_price -> Exit Engine

`trailing_armed` is either supplied by the caller (who got it from the Stop
Engine) or computed by delegating to `production.stops.atr_trailing_stop`. The
candidate stop value returned by that call is deliberately DISCARDED — the
authoritative trailing level is `StopPlan.current_stop_price`.

Stop-level mapping used by the rules (both read from the single StopPlan):
    STOP_LOSS      -> stop_plan.initial_stop_price   (the static protective stop)
    TRAILING_STOP  -> stop_plan.current_stop_price   (the Stop Engine's live level)
"""
from __future__ import annotations

from dataclasses import dataclass

from production.contracts.base import (ContractError, PriceBar, Provenance,
                                       require_bool, require_choice,
                                       require_close, require_positive,
                                       require_session_date)
from production.contracts.position import PositionState
from production.contracts.reason_codes import TECH_SIGNALS
from production.contracts.stop import StopPlan

CONTEXT_NAME = "ExitContext"
ENGINE_VERSION = "phase2"


@dataclass(frozen=True)
class ExitConfigSnapshot:
    """The minimum configuration an exit decision depends on (decision 10).

    A snapshot (not a live config object) keeps the evaluation deterministic and
    auditable: only the parameters that actually affect exit logic are copied in.
    """
    max_holding_days: int

    def validate(self, where: str = CONTEXT_NAME) -> "ExitConfigSnapshot":
        if not isinstance(self.max_holding_days, int) or isinstance(self.max_holding_days, bool):
            raise ContractError(CONTEXT_NAME, "max_holding_days",
                                "expected int (calendar days)")
        if self.max_holding_days <= 0:
            raise ContractError(CONTEXT_NAME, "max_holding_days",
                                f"expected > 0, got {self.max_holding_days}")
        return self


@dataclass(frozen=True)
class ExitContext:
    """Everything the Exit Engine needs, explicitly."""
    position: PositionState
    stop_plan: StopPlan
    bar: PriceBar
    tech_signal: str                      # INJECTED by the caller (never computed here)
    session_date: str
    trailing_armed: bool                  # produced by the Stop Engine
    config: ExitConfigSnapshot
    target_reference_price: float | None = None
    provenance: Provenance | None = None

    # -----------------------------------------------------------------
    def validate(self) -> "ExitContext":
        if not isinstance(self.position, PositionState):
            raise ContractError(CONTEXT_NAME, "position",
                                "expected a validated PositionState")
        if not isinstance(self.stop_plan, StopPlan):
            raise ContractError(CONTEXT_NAME, "stop_plan", "expected a StopPlan")
        if not isinstance(self.bar, PriceBar):
            raise ContractError(CONTEXT_NAME, "bar", "expected a PriceBar")
        self.bar.validate("ExitContext.bar")
        self.config.validate()

        if self.stop_plan.ticker != self.position.ticker:
            raise ContractError(
                CONTEXT_NAME, "stop_plan",
                f"ticker mismatch ({self.stop_plan.ticker} != "
                f"{self.position.ticker})")
        if self.stop_plan.direction != self.position.direction:
            raise ContractError(
                CONTEXT_NAME, "stop_plan",
                f"direction mismatch ({self.stop_plan.direction} != "
                f"{self.position.direction})")

        require_session_date(self.session_date, "validate", "session_date",
                             CONTEXT_NAME)
        if self.session_date < self.position.entry_session:
            raise ContractError(
                CONTEXT_NAME, "session_date",
                f"must be >= entry_session ({self.session_date} < "
                f"{self.position.entry_session})")
        if self.stop_plan.session_date > self.session_date:
            raise ContractError(
                CONTEXT_NAME, "stop_plan",
                f"stop plan session {self.stop_plan.session_date} is in the "
                f"future relative to {self.session_date}")

        require_choice(self.tech_signal, TECH_SIGNALS, "validate", "tech_signal",
                       CONTEXT_NAME)
        require_bool(self.trailing_armed, "validate", "trailing_armed",
                     CONTEXT_NAME)

        if self.target_reference_price is not None:
            require_positive(self.target_reference_price, "validate",
                             "target_reference_price", CONTEXT_NAME)
            # the position's own target is authoritative; a mismatch is a bug
            if self.position.take_profit_price is not None:
                require_close(self.target_reference_price,
                              self.position.take_profit_price, "validate",
                              "target_reference_price", CONTEXT_NAME, tol=1e-9)

        # a context must always carry provenance so the resulting ExitDecision
        # can be audited (never None — fail loud instead)
        if self.provenance is None:
            raise ContractError(CONTEXT_NAME, "provenance",
                                "required (use build_exit_context to obtain the "
                                "engine provenance)")
        self.provenance.validate("validate", CONTEXT_NAME)
        return self

    def as_dict(self) -> dict:
        return {
            "position": self.position.as_dict(),
            "stop_plan": self.stop_plan.as_dict(),
            "bar": self.bar.as_dict(),
            "tech_signal": self.tech_signal,
            "session_date": self.session_date,
            "trailing_armed": self.trailing_armed,
            "target_reference_price": self.target_reference_price,
            "config": {"max_holding_days": self.config.max_holding_days},
            "provenance": (self.provenance.as_dict()
                           if self.provenance else None),
        }


def build_exit_context(*, position: PositionState, stop_plan: StopPlan,
                       bar: PriceBar, tech_signal: str, session_date: str,
                       max_holding_days: int,
                       target_reference_price: float | None = None,
                       trailing_armed: bool | None = None,
                       trailing_atr_multiple: float | None = None,
                       trailing_trigger_r: float | None = None,
                       provenance: Provenance | None = None) -> ExitContext:
    """Assemble an ExitContext.

    `trailing_armed` may be supplied directly (the normal case for a replay
    harness that already ran the Stop Engine). When it is omitted, it is obtained
    by DELEGATING to the Stop Engine — the single owner of trailing-stop
    semantics. No trailing arithmetic happens in this package.
    """
    if trailing_armed is None:
        if trailing_atr_multiple is None or trailing_trigger_r is None:
            raise ContractError(
                CONTEXT_NAME, "trailing_armed",
                "provide trailing_armed, or trailing_atr_multiple + "
                "trailing_trigger_r so the Stop Engine can supply the arming gate")
        from production.stops import atr_trailing_stop
        if stop_plan.atr_multiple is None:
            raise ContractError(CONTEXT_NAME, "stop_plan",
                                "atr_multiple is required to ask the Stop Engine "
                                "for the arming gate")
        evaluation = atr_trailing_stop(
            direction=position.direction, anchor_price=position.anchor_price,
            entry_fill_price=position.entry_fill_price,
            atr_at_entry=position.atr_at_entry,
            stop_atr_multiple=stop_plan.atr_multiple,
            trailing_atr_multiple=trailing_atr_multiple,
            trailing_trigger_r=trailing_trigger_r, ticker=position.ticker)
        # the candidate stop is intentionally ignored: current_stop_price is
        # authoritative (Stop Engine is the only owner of stop levels)
        trailing_armed = evaluation.armed

    if target_reference_price is None:
        target_reference_price = position.take_profit_price

    ctx = ExitContext(
        position=position, stop_plan=stop_plan, bar=bar,
        tech_signal=tech_signal, session_date=session_date,
        trailing_armed=bool(trailing_armed),
        config=ExitConfigSnapshot(max_holding_days=max_holding_days),
        target_reference_price=target_reference_price,
        provenance=(provenance if provenance is not None else
                    Provenance.engine("production/exits/context.py",
                                      "exit_context",
                                      session_date=session_date,
                                      ticker=position.ticker,
                                      trailing_armed=bool(trailing_armed))))
    return ctx.validate()
