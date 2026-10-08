"""
adapter.py — production ⇄ typed-contract adapter (Phase 3)

Sole responsibility: **data translation and validation**.

    existing production position dict
                 ↓
            typed contracts
                 ↓
            PositionState
            StopPlan  (via the Stop Engine — the only stop owner)
            ExitContext

It contains NO trading strategy. It never decides an exit, never computes a
stop, never computes ATR arithmetic, never sizes, and never writes state.

The one thing it *does* orchestrate is the frozen ownership chain:

    Stop Engine → StopPlan.current_stop_price → Exit Engine

`current_stop_price` is obtained by asking the Stop Engine
(`plan_from_position` + `update_stop`); the adapter merely copies the result
into the contracts. Nothing in `production/exits/` recomputes it.

Phase-3 boundary
----------------
* Read-only with respect to `pos` / `state`: the input dicts are never mutated.
* No persistence: `current_stop_price` is returned in-contract only; it is NOT
  written back to `src/state/state.py` (Phase-3 decision 6 / §13 freeze).
* `tech_signal` is supplied by the caller (the pipeline), never computed here.
* Fail loud: malformed production input raises a contract error instead of
  silently degrading (the caller decides whether that is fatal — in shadow mode
  it is recorded and ignored, see `production/exits/shadow.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from production.contracts.base import (ContractError, PriceBar, Provenance,
                                       require_non_empty_str)
from production.contracts.position import PositionState
from production.contracts.reason_codes import LONG
from production.contracts.stop import StopPlan
from production.stops import StopUpdate, plan_from_position, update_stop
from production.exits.context import ExitContext, build_exit_context

ADAPTER_NAME = "production/exits/adapter.py"

# the production position dict is the frozen `state.py` schema
_REQUIRED_POSITION_FIELDS = ("ticker", "direction", "shares", "entry_price",
                             "entry_date", "atr_at_entry", "stop_price")
_REQUIRED_BAR_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class AdaptedExitInput:
    """The complete, validated input set for one exit evaluation."""
    position: PositionState
    stop_plan: StopPlan
    stop_update: StopUpdate
    context: ExitContext

    def stop_plan_snapshot(self) -> dict:
        """Compact audit view used by the shadow record (spec §7)."""
        evaluation = self.stop_update.evaluation
        return {
            "initial_stop_price": self.stop_plan.initial_stop_price,
            "current_stop_price": self.stop_plan.current_stop_price,
            "armed": bool(evaluation.armed) if evaluation is not None else False,
            "reason_code": self.stop_update.reason_code,
            "changed": bool(self.stop_update.changed),
            "previous_stop": self.stop_update.previous_stop,
            "detail": self.stop_update.detail,
        }


def _require_fields(d: dict, fields, what: str) -> None:
    if not isinstance(d, dict):
        raise ContractError(what, "input",
                            f"expected a dict, got {type(d).__name__}")
    missing = [f for f in fields if d.get(f) is None]
    if missing:
        raise ContractError(what, missing[0],
                            f"{what} is missing required field(s): "
                            f"{', '.join(missing)}")


def position_state_from_dict(pos: dict, *,
                             provenance: Provenance | None = None
                             ) -> PositionState:
    """Translate one production position dict into a PositionState.

    Mapping (frozen Phase-3 decision 3):

        pos["stop_price"]           -> initial_stop_price   (the STOP_LOSS level)
        pos["highest_since_entry"]  -> LONG anchor          (trailing basis)
        pos["take_profit"]          -> take_profit_price

    `current_stop_price` is seeded with the initial stop here and REPLACED by
    the Stop Engine's level in `adapt_exit_inputs` — the adapter never derives it.
    """
    _require_fields(pos, _REQUIRED_POSITION_FIELDS, "PositionState")
    require_non_empty_str(pos["ticker"], "adapter", "ticker", ADAPTER_NAME)

    take_profit = pos.get("take_profit")
    return PositionState(
        ticker=pos["ticker"],
        direction=pos["direction"] or LONG,
        shares=int(pos["shares"]),
        entry_fill_price=float(pos["entry_price"]),
        entry_session=pos["entry_date"],
        atr_at_entry=float(pos["atr_at_entry"]),
        initial_stop_price=float(pos["stop_price"]),
        current_stop_price=float(pos["stop_price"]),   # provisional; see below
        highest_price_since_entry=(float(pos["highest_since_entry"])
                                   if pos.get("highest_since_entry") is not None
                                   else None),
        take_profit_price=(float(take_profit)
                           if take_profit is not None else None),
        entry_regime=pos.get("entry_regime"),
        entry_reason=pos.get("entry_reasoning"),
        provenance=(provenance if provenance is not None else
                    Provenance.engine(ADAPTER_NAME, "position_state_from_dict",
                                      session_date=pos.get("entry_date"),
                                      ticker=pos.get("ticker"))),
    ).validate()


def price_bar_from_dict(bar: dict) -> PriceBar:
    """Translate the pipeline's OHLC dict into a validated PriceBar."""
    _require_fields(bar, _REQUIRED_BAR_FIELDS, "PriceBar")
    return PriceBar(open=float(bar["open"]), high=float(bar["high"]),
                    low=float(bar["low"]), close=float(bar["close"])).validate()


@dataclass(frozen=True)
class StopResolution:
    """The Stop Engine's answer for one session (levels + why)."""
    plan: StopPlan           # updated plan — current_stop_price is authoritative
    initial_plan: StopPlan   # the initial ATR stop (1R basis), before trailing
    update: StopUpdate       # reason code + arming state + detail

    @property
    def armed(self) -> bool:
        evaluation = self.update.evaluation
        return bool(evaluation.armed) if evaluation is not None else False


def stop_plan_for(position: PositionState, *, cfg, session_date: str,
                  provenance: Provenance | None = None) -> StopResolution:
    """Ask the Stop Engine for the plan and today's stop level.

    Returns the *updated* plan (its `current_stop_price` is authoritative) plus
    the StopUpdate that explains the level (reason code + arming state).
    """
    if position.initial_stop_price >= position.entry_fill_price:
        # PositionState.validate already enforces this for LONG; kept explicit
        # so the failure message names the adapter boundary.
        raise ContractError(ADAPTER_NAME, "initial_stop_price",
                            "initial stop must sit below the entry fill for LONG")
    initial_plan = plan_from_position(
        position, atr_multiple=float(cfg.stop_atr_mult),
        session_date=position.entry_session, provenance=provenance)
    update = update_stop(
        initial_plan,
        entry_fill_price=position.entry_fill_price,
        anchor_price=position.anchor_price,
        trailing_atr_multiple=float(cfg.trailing_atr_mult),
        trailing_trigger_r=float(cfg.trailing_trigger_r),
        session_date=session_date,
        provenance=provenance)
    return StopResolution(plan=update.plan, initial_plan=initial_plan,
                          update=update)


def adapt_exit_inputs(pos: dict, bar: dict, tech_signal: str,
                      session_date: str, cfg, *,
                      provenance: Provenance | None = None) -> AdaptedExitInput:
    """Full translation: production dicts -> AdaptedExitInput.

    Ownership chain enforced here: the stop level comes from the Stop Engine,
    and the Exit Engine receives it through `StopPlan`.
    """
    seeded = position_state_from_dict(pos, provenance=provenance)
    price_bar = price_bar_from_dict(bar)
    resolved = stop_plan_for(seeded, cfg=cfg, session_date=session_date,
                             provenance=provenance)

    # the Stop Engine's level replaces the provisional seed (no recomputation)
    position = replace(seeded,
                       current_stop_price=resolved.plan.current_stop_price,
                       stop_reason_code=resolved.update.reason_code,
                       stop_updated_session=session_date).validate()

    ctx = build_exit_context(
        position=position, stop_plan=resolved.plan, bar=price_bar,
        tech_signal=tech_signal, session_date=session_date,
        max_holding_days=int(cfg.max_holding_days),
        target_reference_price=position.take_profit_price,
        trailing_armed=resolved.armed,
        provenance=provenance)

    return AdaptedExitInput(position=position, stop_plan=resolved.plan,
                            stop_update=resolved.update, context=ctx)
