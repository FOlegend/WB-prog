"""
trailing.py — ATR trailing stop (Phase 1)

Re-implements the EXISTING baseline trailing behaviour exactly.

Legacy source of truth (NOT modified, only reproduced):
    src/portfolio/portfolio_manager.py::_exit_check  (trailing block)

        r_dist      = atr_at_entry * cfg.stop_atr_mult        <- note: stop_atr_mult
        armed       = highest_since_entry >= entry_price + cfg.trailing_trigger_r * r_dist
        trail_stop  = highest_since_entry - cfg.trailing_atr_mult * atr_at_entry
        (exit when open <= trail_stop -> GAP fill, or low <= trail_stop -> TRAILING fill)

SHORT is the true mirror (architecture decision 4 — not enabled in production):

        armed       = lowest_since_entry <= entry_price - trailing_trigger_r * r_dist
        trail_stop  = lowest_since_entry + trailing_atr_mult * atr_at_entry

The trigger distance deliberately uses `stop_atr_multiple` (the same multiple
that defines 1R) rather than the trailing multiple, because that is what the
existing baseline does; changing it would alter behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass

from production.contracts.reason_codes import (DIRECTIONS, LONG,
                                               TRAILING_NOT_ARMED)
from production.stops.errors import StopEngineError


@dataclass(frozen=True)
class TrailingEvaluation:
    """Result of one trailing evaluation (candidate stop, or not armed yet)."""
    armed: bool
    candidate_stop: float | None
    reason_code: str
    detail: str
    direction: str
    anchor_price: float
    entry_fill_price: float
    atr_at_entry: float
    trigger_distance: float          # USD/share — 1R x trailing_trigger_r
    trail_distance: float            # USD/share — ATR x trailing_atr_multiple

    def as_dict(self) -> dict:
        return {"armed": self.armed, "candidate_stop": self.candidate_stop,
                "reason_code": self.reason_code, "detail": self.detail,
                "direction": self.direction, "anchor_price": self.anchor_price,
                "entry_fill_price": self.entry_fill_price,
                "atr_at_entry": self.atr_at_entry,
                "trigger_distance": self.trigger_distance,
                "trail_distance": self.trail_distance}


def _num(value, code: str, field_name: str, *, allow_zero: bool = False):
    if value is None:
        raise StopEngineError("ATR_UNAVAILABLE" if field_name == "atr_at_entry"
                              else code,
                              f"{field_name} is required but was None",
                              field_name=field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StopEngineError(code, f"{field_name} must be a number, got "
                                    f"{type(value).__name__}",
                              field_name=field_name)
    if allow_zero:
        if value < 0:
            raise StopEngineError(code, f"{field_name} must be >= 0, got {value}",
                                  field_name=field_name)
    elif not value > 0:
        raise StopEngineError(code, f"{field_name} must be > 0, got {value}",
                              field_name=field_name)
    return float(value)


def atr_trailing_stop(*, direction: str = LONG, anchor_price: float | None,
                      entry_fill_price: float,
                      atr_at_entry: float | None,
                      stop_atr_multiple: float,
                      trailing_atr_multiple: float,
                      trailing_trigger_r: float,
                      ticker: str | None = None) -> TrailingEvaluation:
    """Evaluate the ATR trailing candidate for the current anchor.

    anchor_price : highest price since entry (LONG) / lowest (SHORT)
    Returns TrailingEvaluation; `armed=False` (TRAILING_NOT_ARMED) means the
    trigger distance has not been reached, so no candidate stop exists.
    """
    if direction not in DIRECTIONS:
        raise StopEngineError(
            "INVALID_DIRECTION",
            f"direction must be one of {sorted(DIRECTIONS)}, got {direction!r}",
            field_name="direction", ticker=ticker)
    if anchor_price is None:
        raise StopEngineError("INVALID_PRICE", "anchor price unavailable",
                              field_name="anchor_price", ticker=ticker)
    anchor = _num(anchor_price, "INVALID_PRICE", "anchor_price")
    entry = _num(entry_fill_price, "INVALID_PRICE", "entry_fill_price")
    atr = _num(atr_at_entry, "INVALID_ATR", "atr_at_entry")
    stop_mult = _num(stop_atr_multiple, "INVALID_MULTIPLE", "stop_atr_multiple")
    trail_mult = _num(trailing_atr_multiple, "INVALID_MULTIPLE",
                      "trailing_atr_multiple")
    trigger_r = _num(trailing_trigger_r, "INVALID_MULTIPLE",
                     "trailing_trigger_r", allow_zero=True)

    r_dist = atr * stop_mult                       # 1R per share (legacy r_dist)
    trigger_distance = trigger_r * r_dist
    trail_distance = trail_mult * atr

    if direction == LONG:
        armed = anchor >= entry + trigger_distance
        candidate = anchor - trail_distance
        armed_detail = (f"{anchor} >= entry {entry} + trigger "
                        f"{trigger_distance}")
    else:
        armed = anchor <= entry - trigger_distance
        candidate = anchor + trail_distance
        armed_detail = (f"{anchor} <= entry {entry} - trigger "
                        f"{trigger_distance}")

    if not armed:
        return TrailingEvaluation(
            armed=False, candidate_stop=None, reason_code=TRAILING_NOT_ARMED,
            direction=direction, anchor_price=anchor,
            entry_fill_price=entry, atr_at_entry=atr,
            trigger_distance=trigger_distance, trail_distance=trail_distance,
            detail=f"trailing not armed: need {armed_detail}")

    if candidate <= 0:
        raise StopEngineError(
            "INVALID_PRICE",
            f"computed trailing stop is not a valid price ({candidate})",
            field_name="candidate_stop", ticker=ticker)

    op = "-" if direction == LONG else "+"
    return TrailingEvaluation(
        armed=True, candidate_stop=candidate, reason_code="TRAILING_ATR",
        direction=direction, anchor_price=anchor, entry_fill_price=entry,
        atr_at_entry=atr, trigger_distance=trigger_distance,
        trail_distance=trail_distance,
        detail=(f"armed ({armed_detail}); candidate = anchor {anchor} {op} "
                f"ATR {atr} x {trail_mult} = {candidate}"))
