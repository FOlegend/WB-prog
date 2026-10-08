"""
stop.py — StopPlan contract (Phase 0)

A StopPlan is the Stop Engine's complete answer for one position:

    "where is the stop, how was it derived, and why?"

Two stop prices are DISTINCT concepts (architecture decision 3)
--------------------------------------------------------------
initial_stop_price : fixed at entry. Defines 1R and therefore position sizing.
current_stop_price : the stop in force NOW (trailing updates move it).
                     Phase 1 computes and returns it but does NOT persist it
                     (src/state/state.py is untouched).

Semantics (architecture decision 10)
------------------------------------
reference_price        the price the stop was computed from
reference_price_source one of FILL | EXPECTED | SIGNAL — never conflate:
                       FILL     = actual fill (real initial risk)
                       EXPECTED = pre-trade expected execution price
                       SIGNAL   = signal-bar (D) close
risk_per_share         = |reference_price - initial_stop_price|  (1R, USD/share)

Not implemented in Phase 1: structure stops. `structure_level` is optional and
reserved for a future explicit architecture decision (see STOP_ENGINE_BACKLOG.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from production.contracts.base import (ContractError, Provenance, require_choice,
                                       require_close, require_non_empty_str,
                                       require_positive, require_session_date)
from production.contracts.reason_codes import (DIRECTIONS, INITIAL_ATR, LONG,
                                               REFERENCE_PRICE_SOURCES,
                                               STOP_METHOD_ATR, STOP_METHODS,
                                               STOP_REASON_CODES)


@dataclass(frozen=True)
class StopPlan:
    """Stop plan for one position on one session."""

    # ---- identity / time ----
    ticker: str
    direction: str
    session_date: str                 # session the plan was produced for

    # ---- price basis ----
    reference_price: float            # USD/share
    reference_price_source: str       # FILL | EXPECTED | SIGNAL

    # ---- stops ----
    initial_stop_price: float         # USD/share — defines 1R
    current_stop_price: float         # USD/share — in force now

    # ---- derivation ----
    stop_method: str = STOP_METHOD_ATR
    atr_value: float | None = None            # USD/share
    atr_multiple: float | None = None         # dimensionless
    structure_level: float | None = None      # OPTIONAL, reserved (unused in Phase 1)
    buffer: float | None = None               # OPTIONAL price buffer (legacy: none)
    risk_per_share: float | None = None       # USD/share — 1R (derived if None)

    # ---- explanation ----
    reason_code: str = INITIAL_ATR
    rationale: str = ""

    provenance: Provenance = field(default_factory=Provenance.unknown)
    contract_name: str = "StopPlan"

    # -----------------------------------------------------------------
    def validate(self) -> "StopPlan":
        c = self.contract_name
        require_non_empty_str(self.ticker, "validate", "ticker", c)
        require_choice(self.direction, DIRECTIONS, "validate", "direction", c)
        require_session_date(self.session_date, "validate", "session_date", c)

        require_positive(self.reference_price, "validate", "reference_price", c)
        require_choice(self.reference_price_source, REFERENCE_PRICE_SOURCES,
                       "validate", "reference_price_source", c)

        require_positive(self.initial_stop_price, "validate",
                         "initial_stop_price", c)
        require_positive(self.current_stop_price, "validate",
                         "current_stop_price", c)

        require_choice(self.stop_method, STOP_METHODS, "validate",
                       "stop_method", c)
        if self.stop_method == STOP_METHOD_ATR:
            if self.atr_value is None:
                raise ContractError(c, "atr_value",
                                    "required when stop_method='ATR'")
            if self.atr_multiple is None:
                raise ContractError(c, "atr_multiple",
                                    "required when stop_method='ATR'")
        if self.atr_value is not None:
            require_positive(self.atr_value, "validate", "atr_value", c)
        if self.atr_multiple is not None:
            require_positive(self.atr_multiple, "validate", "atr_multiple", c)
        if self.structure_level is not None:
            require_positive(self.structure_level, "validate",
                             "structure_level", c)
        if self.buffer is not None:
            require_positive(self.buffer, "validate", "buffer", c)

        # stop must sit strictly on the protected side of the reference price
        if self.direction == LONG:
            if not self.initial_stop_price < self.reference_price:
                raise ContractError(
                    c, "initial_stop_price",
                    f"LONG stop must be < reference_price "
                    f"({self.initial_stop_price} >= {self.reference_price})")
            # trailing may only tighten (move up for LONG)
            if self.current_stop_price < self.initial_stop_price:
                raise ContractError(
                    c, "current_stop_price",
                    f"LONG stop may not loosen: current "
                    f"{self.current_stop_price} < initial "
                    f"{self.initial_stop_price}")
        else:
            if not self.initial_stop_price > self.reference_price:
                raise ContractError(
                    c, "initial_stop_price",
                    f"SHORT stop must be > reference_price "
                    f"({self.initial_stop_price} <= {self.reference_price})")
            if self.current_stop_price > self.initial_stop_price:
                raise ContractError(
                    c, "current_stop_price",
                    f"SHORT stop may not loosen: current "
                    f"{self.current_stop_price} > initial "
                    f"{self.initial_stop_price}")

        derived = abs(self.reference_price - self.initial_stop_price)
        if self.risk_per_share is not None:
            require_positive(self.risk_per_share, "validate", "risk_per_share", c)
            require_close(self.risk_per_share, derived, "validate",
                          "risk_per_share", c)
        require_choice(self.reason_code, STOP_REASON_CODES, "validate",
                       "reason_code", c)
        self.provenance.validate("validate", c)
        return self

    # -----------------------------------------------------------------
    @property
    def timestamp(self) -> str:
        """Alias: the contract's timestamp IS the session date."""
        return self.session_date

    @property
    def risk_r(self) -> float:
        """1R in USD/share (initial risk per share)."""
        return abs(self.reference_price - self.initial_stop_price)

    @property
    def risk_per_share_value(self) -> float:
        """Explicit 1R accessor (field value if given, else derived)."""
        return (self.risk_per_share if self.risk_per_share is not None
                else self.risk_r)

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "session_date": self.session_date,
            "reference_price": self.reference_price,
            "reference_price_source": self.reference_price_source,
            "initial_stop_price": self.initial_stop_price,
            "current_stop_price": self.current_stop_price,
            "stop_method": self.stop_method,
            "atr_value": self.atr_value,
            "atr_multiple": self.atr_multiple,
            "structure_level": self.structure_level,
            "buffer": self.buffer,
            "risk_per_share": self.risk_per_share_value,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
            "provenance": self.provenance.as_dict(),
        }
