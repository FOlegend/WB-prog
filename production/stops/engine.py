"""
engine.py — Stop Engine facade (Phase 1, ISOLATED)

The Stop Engine answers exactly one question:

    "Where is the stop for this position now, and why?"

It computes the INITIAL stop and the CURRENT (trailing) stop, enforces that a
stop can never loosen, and explains every change with a reason code. It does
NOT decide whether to exit — that belongs to the (future) Exit Engine
(architecture: Stop Engine = levels, Exit Engine = decision).

Phase 1 boundaries
------------------
* Nothing here is wired into `production/pipeline.py` or any production path.
* `src/agents/risk_manager.py` and `src/portfolio/portfolio_manager.py` are
  NOT modified and NOT delegated to — the parity tests prove the numbers match.
* `current_stop_price` is returned, never persisted (state.py untouched).

No-loosening rule
-----------------
LONG  : updated_stop >= previous_stop
SHORT : updated_stop <= previous_stop
A candidate that would loosen the stop is rejected and reported as
STOP_UNCHANGED ("no valid improvement"), never silently applied.
"""
from __future__ import annotations

from dataclasses import dataclass

from production.contracts.base import Provenance
from production.contracts.position import PositionState
from production.contracts.reason_codes import (DIRECTIONS, INITIAL_ATR, LONG,
                                               NEW_HIGH, REFERENCE_PRICE_SOURCES,
                                               STOP_METHOD_ATR, STOP_UNCHANGED,
                                               TRAILING_ATR, direction_sign)
from production.contracts.stop import StopPlan
from production.stops import trailing as _trailing
from production.stops.errors import StopEngineError
from production.stops.initial import atr_initial_stop
ENGINE_NAME = "production/stops/engine.py"
ENGINE_COMPONENT = "stop_engine"


# ---------------------------------------------------------------------------
# StopUpdate
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class StopUpdate:
    """Outcome of one stop update attempt (levels only, no exit decision)."""
    ticker: str
    direction: str
    session_date: str
    previous_stop: float
    updated_stop: float
    changed: bool
    reason_code: str
    detail: str
    plan: StopPlan
    evaluation: _trailing.TrailingEvaluation | None = None

    def explain(self) -> str:
        """Human-readable answer to 'why did the stop go from X to Y?'."""
        if not self.changed:
            return (f"{self.direction} stop unchanged at {self.previous_stop} "
                    f"on {self.session_date} ({self.reason_code}): {self.detail}")
        return (f"{self.direction} stop {self.previous_stop} -> "
                f"{self.updated_stop} on {self.session_date} "
                f"({self.reason_code}): {self.detail}")

    def as_dict(self) -> dict:
        return {"ticker": self.ticker, "direction": self.direction,
                "session_date": self.session_date,
                "previous_stop": self.previous_stop,
                "updated_stop": self.updated_stop, "changed": self.changed,
                "reason_code": self.reason_code, "detail": self.detail,
                "plan": self.plan.as_dict(),
                "evaluation": (self.evaluation.as_dict()
                               if self.evaluation else None)}


# ---------------------------------------------------------------------------
# Initial stop plan
# ---------------------------------------------------------------------------
def initial_stop_plan(*, ticker: str, session_date: str, reference_price: float,
                      reference_price_source: str, atr_value: float | None,
                      atr_multiple: float | None, direction: str = LONG,
                      buffer: float | None = None,
                      provenance: Provenance | None = None) -> StopPlan:
    """Build the initial StopPlan (initial_stop_price == current_stop_price).

    reference_price_source states WHAT the stop was measured from:
      FILL     -> the actual fill (real initial risk / 1R for sizing)
      EXPECTED -> pre-trade expected execution price
      SIGNAL   -> signal-bar (D) close
    """
    if direction not in DIRECTIONS:
        raise StopEngineError(
            "INVALID_DIRECTION",
            f"direction must be one of {sorted(DIRECTIONS)}, got {direction!r}",
            field_name="direction", ticker=ticker)
    if reference_price_source not in REFERENCE_PRICE_SOURCES:
        raise StopEngineError(
            "INVALID_PRICE",
            f"reference_price_source must be one of "
            f"{sorted(REFERENCE_PRICE_SOURCES)}, got {reference_price_source!r}",
            field_name="reference_price_source", ticker=ticker)

    calc = atr_initial_stop(reference_price=reference_price,
                            atr_value=atr_value, atr_multiple=atr_multiple,
                            direction=direction, buffer=buffer, ticker=ticker)
    return StopPlan(
        ticker=ticker, direction=direction, session_date=session_date,
        reference_price=float(reference_price),
        reference_price_source=reference_price_source,
        initial_stop_price=calc.stop_price,
        current_stop_price=calc.stop_price,
        stop_method=STOP_METHOD_ATR, atr_value=calc.atr_value,
        atr_multiple=calc.atr_multiple, structure_level=None,
        buffer=calc.buffer, risk_per_share=calc.distance,
        reason_code=INITIAL_ATR, rationale=calc.detail,
        provenance=provenance or Provenance.engine(
            ENGINE_NAME, "initial_stop", session_date=session_date,
            ticker=ticker)).validate()


def plan_from_position(position: PositionState, *,
                       atr_multiple: float | None = None,
                       session_date: str | None = None,
                       provenance: Provenance | None = None) -> StopPlan:
    """Read-only bridge: PositionState -> StopPlan (no state mutation).

    `atr_multiple` defaults to the position's implied ATR multiple
    (risk_per_share / atr_at_entry), which is exact while the initial stop came
    from an ATR stop. Pass it explicitly if that is not the case.
    """
    if position.direction not in DIRECTIONS:
        raise StopEngineError("INVALID_DIRECTION",
                              f"invalid direction {position.direction!r}",
                              field_name="direction", ticker=position.ticker)
    if atr_multiple is None:
        atr_multiple = position.risk_per_share / position.atr_at_entry
    return StopPlan(
        ticker=position.ticker, direction=position.direction,
        session_date=session_date or position.entry_session,
        reference_price=position.entry_fill_price,
        reference_price_source="FILL",
        initial_stop_price=position.initial_stop_price,
        current_stop_price=position.current_stop_price,
        stop_method=STOP_METHOD_ATR, atr_value=position.atr_at_entry,
        atr_multiple=float(atr_multiple), structure_level=None, buffer=None,
        risk_per_share=position.risk_per_share,
        reason_code=position.stop_reason_code,
        rationale=("derived from PositionState (read-only); "
                   "no persistence"),
        provenance=provenance or Provenance.engine(
            ENGINE_NAME, "plan_from_position",
            session_date=position.entry_session, ticker=position.ticker,
            derived_multiple=atr_multiple)).validate()


# ---------------------------------------------------------------------------
# Stop update (trailing) — levels only
# ---------------------------------------------------------------------------
def update_stop(plan: StopPlan, *, entry_fill_price: float,
                anchor_price: float | None,
                trailing_atr_multiple: float, trailing_trigger_r: float,
                previous_anchor_price: float | None = None,
                session_date: str | None = None,
                provenance: Provenance | None = None) -> StopUpdate:
    """Evaluate the trailing candidate for `anchor_price` and apply it only
    when it tightens the stop.

    previous_anchor_price lets the engine distinguish NEW_HIGH (the anchor
    itself advanced) from TRAILING_ATR (stop improved without a new anchor
    extreme) — the reason code answers *why the decision happened*.
    """
    if plan.direction not in DIRECTIONS:
        raise StopEngineError("INVALID_DIRECTION",
                              f"invalid direction {plan.direction!r}",
                              field_name="direction", ticker=plan.ticker)
    if plan.atr_multiple is None:
        raise StopEngineError("INVALID_MULTIPLE",
                              "StopPlan.atr_multiple is required to evaluate "
                              "the trailing trigger distance",
                              field_name="atr_multiple", ticker=plan.ticker)
    atr_at_entry = plan.atr_value
    session = session_date or plan.session_date

    evaluation = _trailing.atr_trailing_stop(
        direction=plan.direction, anchor_price=anchor_price,
        entry_fill_price=entry_fill_price, atr_at_entry=atr_at_entry,
        stop_atr_multiple=plan.atr_multiple,
        trailing_atr_multiple=trailing_atr_multiple,
        trailing_trigger_r=trailing_trigger_r, ticker=plan.ticker)

    previous_stop = plan.current_stop_price
    sign = direction_sign(plan.direction)

    # ---- not armed -> nothing to do ----
    if not evaluation.armed or evaluation.candidate_stop is None:
        new_plan = _replace_plan(plan, current_stop=previous_stop,
                                 reason_code=evaluation.reason_code,
                                 rationale=evaluation.detail,
                                 session_date=session, provenance=provenance)
        return StopUpdate(
            ticker=plan.ticker, direction=plan.direction, session_date=session,
            previous_stop=previous_stop, updated_stop=previous_stop,
            changed=False, reason_code=evaluation.reason_code,
            detail=evaluation.detail, plan=new_plan, evaluation=evaluation)

    candidate = evaluation.candidate_stop

    # ---- no-loosening enforcement ----
    improves = (candidate * sign) > (previous_stop * sign)
    if not improves:
        detail = (f"candidate {candidate} does not improve the current stop "
                  f"{previous_stop} ({plan.direction} stops may not loosen)")
        new_plan = _replace_plan(plan, current_stop=previous_stop,
                                 reason_code=STOP_UNCHANGED, rationale=detail,
                                 session_date=session, provenance=provenance)
        return StopUpdate(
            ticker=plan.ticker, direction=plan.direction, session_date=session,
            previous_stop=previous_stop, updated_stop=previous_stop,
            changed=False, reason_code=STOP_UNCHANGED, detail=detail,
            plan=new_plan, evaluation=evaluation)

    # ---- reason: NEW_HIGH vs TRAILING_ATR ----
    anchor_advanced = False
    if previous_anchor_price is not None:
        anchor_advanced = ((anchor_price - previous_anchor_price) * sign) > 0
    reason = NEW_HIGH if anchor_advanced else TRAILING_ATR
    detail = evaluation.detail + (f"; anchor advanced from "
                                  f"{previous_anchor_price} to {anchor_price}"
                                  if anchor_advanced else "")
    new_plan = _replace_plan(plan, current_stop=candidate, reason_code=reason,
                             rationale=detail, session_date=session,
                             provenance=provenance)
    return StopUpdate(
        ticker=plan.ticker, direction=plan.direction, session_date=session,
        previous_stop=previous_stop, updated_stop=candidate, changed=True,
        reason_code=reason, detail=detail, plan=new_plan, evaluation=evaluation)


def _replace_plan(plan: StopPlan, *, current_stop: float, reason_code: str,
                  rationale: str, session_date: str,
                  provenance: Provenance | None) -> StopPlan:
    """Return a new validated StopPlan (initial stop and reference preserved)."""
    return StopPlan(
        ticker=plan.ticker, direction=plan.direction,
        session_date=session_date, reference_price=plan.reference_price,
        reference_price_source=plan.reference_price_source,
        initial_stop_price=plan.initial_stop_price,
        current_stop_price=current_stop, stop_method=plan.stop_method,
        atr_value=plan.atr_value, atr_multiple=plan.atr_multiple,
        structure_level=plan.structure_level, buffer=plan.buffer,
        risk_per_share=plan.risk_per_share, reason_code=reason_code,
        rationale=rationale,
        provenance=provenance or Provenance.engine(
            ENGINE_NAME, "update_stop", session_date=session_date,
            ticker=plan.ticker, previous_reason=plan.reason_code)).validate()
