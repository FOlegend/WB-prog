"""
initial.py — ATR initial stop (Phase 1)

Re-implements the EXISTING baseline formula, unchanged:

    LONG  : initial_stop = reference_price - ATR x atr_multiple
    SHORT : initial_stop = reference_price + ATR x atr_multiple   (mirror)

Legacy source of truth (NOT modified, only reproduced):
    src/agents/risk_manager.py::size_position
        stop_distance = atr_val * cfg.stop_atr_mult
        stop_price    = price - stop_distance          # LONG
        (the same stop_distance is the 1R used for sizing)

`buffer` is optional and defaults to None, matching the legacy behaviour where
no extra buffer is applied. When provided it is added beyond the ATR distance
in the protective direction — a NEW capability, so parity tests only cover
buffer=None.
"""
from __future__ import annotations

from dataclasses import dataclass

from production.contracts.reason_codes import (DIRECTIONS, INITIAL_ATR, LONG,
                                               direction_sign)
from production.stops.errors import StopEngineError


@dataclass(frozen=True)
class StopCalculation:
    """A raw stop price plus the numbers that produced it."""
    stop_price: float
    reason_code: str
    detail: str
    direction: str
    reference_price: float
    atr_value: float
    atr_multiple: float
    buffer: float | None = None
    distance: float = 0.0          # USD/share between reference and stop

    def as_dict(self) -> dict:
        return {"stop_price": self.stop_price, "reason_code": self.reason_code,
                "detail": self.detail, "direction": self.direction,
                "reference_price": self.reference_price,
                "atr_value": self.atr_value,
                "atr_multiple": self.atr_multiple, "buffer": self.buffer,
                "distance": self.distance}


def _check_direction(direction: str, ticker: str | None) -> None:
    if direction not in DIRECTIONS:
        raise StopEngineError(
            "INVALID_DIRECTION",
            f"direction must be one of {sorted(DIRECTIONS)}, got {direction!r}",
            field_name="direction", ticker=ticker)


def _check_positive(value, code: str, field_name: str, ticker: str | None) -> float:
    if value is None:
        raise StopEngineError("ATR_UNAVAILABLE" if field_name == "atr_value"
                              else code,
                              f"{field_name} is required but was None",
                              field_name=field_name, ticker=ticker)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StopEngineError(code, f"{field_name} must be a number, got "
                                    f"{type(value).__name__}",
                              field_name=field_name, ticker=ticker)
    if not value > 0:
        raise StopEngineError(code, f"{field_name} must be > 0, got {value}",
                              field_name=field_name, ticker=ticker)
    return float(value)


def atr_initial_stop(*, reference_price: float, atr_value: float | None,
                     atr_multiple: float | None, direction: str = LONG,
                     buffer: float | None = None,
                     ticker: str | None = None) -> StopCalculation:
    """Compute the initial ATR stop.

    reference_price : price the stop is measured from (USD/share). Use the
                      actual FILL for real initial risk (see StopPlan.
                      reference_price_source); EXPECTED/SIGNAL are pre-trade.
    atr_value       : ATR at entry (USD/share)
    atr_multiple    : dimensionless ATR multiple (legacy: cfg.stop_atr_mult)
    """
    _check_direction(direction, ticker)
    ref = _check_positive(reference_price, "INVALID_PRICE", "reference_price",
                          ticker)
    atr = _check_positive(atr_value, "INVALID_ATR", "atr_value", ticker)
    mult = _check_positive(atr_multiple, "INVALID_MULTIPLE", "atr_multiple",
                           ticker)
    if buffer is not None:
        if isinstance(buffer, bool) or not isinstance(buffer, (int, float)):
            raise StopEngineError("INVALID_PRICE",
                                  f"buffer must be a number, got "
                                  f"{type(buffer).__name__}",
                                  field_name="buffer", ticker=ticker)
        if buffer < 0:
            raise StopEngineError("INVALID_PRICE",
                                  f"buffer must be >= 0, got {buffer}",
                                  field_name="buffer", ticker=ticker)

    distance = atr * mult + (float(buffer) if buffer is not None else 0.0)
    stop = ref - distance if direction == LONG else ref + distance
    if stop <= 0:
        raise StopEngineError(
            "INVALID_PRICE",
            f"computed stop is not a valid price ({stop}); "
            f"reference={ref} distance={distance}",
            field_name="stop_price", ticker=ticker)

    buffer_note = f" + buffer {buffer}" if buffer is not None else ""
    return StopCalculation(
        stop_price=stop, reason_code=INITIAL_ATR, direction=direction,
        reference_price=ref, atr_value=atr, atr_multiple=mult,
        buffer=None if buffer is None else float(buffer), distance=distance,
        detail=(f"initial ATR stop = {ref} - ATR {atr} x {mult}{buffer_note} "
                f"= {stop}" if direction == LONG else
                f"initial ATR stop = {ref} + ATR {atr} x {mult}{buffer_note} "
                f"= {stop}"))
