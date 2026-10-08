"""
order.py — OrderIntent contract (Phase 0)

An OrderIntent is what the human (or an executor) would actually act on. It
keeps three things EXPLICITLY separate:

    signal    : signal_price (D session close — where the idea came from)
    execution : expected_execution_type / expected_execution_price / window
    risk      : approved_shares + risk_decision_id (what the Risk Engine allowed)

No execution implementation lives here, and no strategy decision is re-made
here. Phase 1 does not emit OrderIntents from production — the contract exists
so later phases can.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from production.contracts.base import (ContractError, ExecutionWindow, Provenance,
                                       deterministic_id, require_choice,
                                       require_int_shares, require_non_empty_str,
                                       require_positive, require_session_date)
from production.contracts.reason_codes import (DIRECTIONS, EXECUTION_TYPES,
                                               LONG, ORDER_REASON_CODES,
                                               ORDER_UNSPECIFIED)


@dataclass(frozen=True)
class OrderIntent:
    """Execution intent for one approved trade."""

    # ---- identity ----
    ticker: str
    direction: str

    # ---- size (from the Risk Engine) ----
    requested_shares: int
    approved_shares: int

    # ---- price semantics (three separate concepts) ----
    signal_price: float                        # USD/share — D session close
    stop_price: float                          # USD/share — stop to attach

    # ---- explanation / linkage ----
    order_reason: str
    risk_decision_id: str

    # ---- execution intent ----
    expected_execution_type: str = "NEXT_OPEN"
    expected_execution_price: float | None = None   # may be None pre-trade
    execution_window: ExecutionWindow | None = None

    reason_code: str = ORDER_UNSPECIFIED
    session_date: str | None = None
    order_id: str | None = None

    provenance: Provenance = field(default_factory=Provenance.unknown)
    contract_name: str = "OrderIntent"

    def __post_init__(self) -> None:
        if self.order_id is None:
            object.__setattr__(self, "order_id", deterministic_id(
                "order",
                {"ticker": self.ticker, "direction": self.direction,
                 "requested_shares": self.requested_shares,
                 "approved_shares": self.approved_shares,
                 "signal_price": self.signal_price,
                 "stop_price": self.stop_price,
                 "risk_decision_id": self.risk_decision_id,
                 "session_date": self.session_date}))

    # -----------------------------------------------------------------
    def validate(self) -> "OrderIntent":
        c = self.contract_name
        require_non_empty_str(self.ticker, "validate", "ticker", c)
        require_choice(self.direction, DIRECTIONS, "validate", "direction", c)

        require_int_shares(self.requested_shares, "validate", "requested_shares", c)
        require_int_shares(self.approved_shares, "validate", "approved_shares", c)
        if self.approved_shares <= 0:
            raise ContractError(c, "approved_shares",
                                "an OrderIntent requires approved_shares > 0")
        if self.approved_shares > self.requested_shares:
            raise ContractError(
                c, "approved_shares",
                "approved_shares must be <= requested_shares "
                f"({self.approved_shares} > {self.requested_shares})")

        require_positive(self.signal_price, "validate", "signal_price", c)
        require_positive(self.stop_price, "validate", "stop_price", c)

        require_choice(self.expected_execution_type, EXECUTION_TYPES, "validate",
                       "expected_execution_type", c)
        if self.expected_execution_price is not None:
            require_positive(self.expected_execution_price, "validate",
                             "expected_execution_price", c)
        if self.execution_window is not None:
            self.execution_window.validate("OrderIntent.execution_window")
            if (self.session_date is not None
                    and self.execution_window.from_session < self.session_date):
                raise ContractError(
                    c, "execution_window",
                    "from_session must be >= session_date "
                    f"({self.execution_window.from_session} < "
                    f"{self.session_date})")

        # stop must protect the price we expect to actually pay
        basis = (self.expected_execution_price
                 if self.expected_execution_price is not None
                 else self.signal_price)
        if self.direction == LONG and not self.stop_price < basis:
            raise ContractError(
                c, "stop_price",
                f"LONG stop must be < execution basis ({self.stop_price} >= "
                f"{basis}); basis="
                f"{'expected_execution_price' if self.expected_execution_price is not None else 'signal_price'}")
        if self.direction != LONG and not self.stop_price > basis:
            raise ContractError(c, "stop_price",
                                f"SHORT stop must be > execution basis "
                                f"({self.stop_price} <= {basis})")

        require_non_empty_str(self.order_reason, "validate", "order_reason", c)
        require_non_empty_str(self.risk_decision_id, "validate",
                              "risk_decision_id", c)
        require_choice(self.reason_code, ORDER_REASON_CODES, "validate",
                       "reason_code", c)
        if self.session_date is not None:
            require_session_date(self.session_date, "validate", "session_date", c)
        require_non_empty_str(self.order_id, "validate", "order_id", c)
        self.provenance.validate("validate", c)
        return self

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "direction": self.direction,
            "requested_shares": self.requested_shares,
            "approved_shares": self.approved_shares,
            "signal_price": self.signal_price,
            "expected_execution_type": self.expected_execution_type,
            "expected_execution_price": self.expected_execution_price,
            "execution_window": (self.execution_window.as_dict()
                                 if self.execution_window else None),
            "stop_price": self.stop_price,
            "order_reason": self.order_reason,
            "risk_decision_id": self.risk_decision_id,
            "reason_code": self.reason_code,
            "session_date": self.session_date,
            "provenance": self.provenance.as_dict(),
        }
