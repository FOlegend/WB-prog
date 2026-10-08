"""
position.py — PositionState contract (Phase 0)

PositionState is the Stop Engine's INPUT view of an open position.

Phase 1 boundary (architecture decision 3)
------------------------------------------
`current_stop_price` is a FIRST-CLASS concept in this contract, but Phase 1
does NOT persist it: `src/state/state.py` and the production position schema
are untouched. The production path keeps deriving its trailing stop on the fly
(`src/portfolio/portfolio_manager.py::_exit_check`), and the isolated Stop
Engine computes/returns the same numbers without writing them anywhere.

Anchors (trailing basis)
------------------------
LONG  : highest_price_since_entry  (legacy `highest_since_entry` in state.py)
SHORT : lowest_price_since_entry   (mirror; no legacy counterpart until SHORT
        is enabled — see STOP_ENGINE_BACKLOG.md)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from production.contracts.base import (ContractError, Provenance, require_choice,
                                       require_int_shares, require_non_empty_str,
                                       require_positive, require_session_date)
from production.contracts.reason_codes import (DIRECTIONS, INITIAL_ATR, LONG,
                                               STOP_REASON_CODES)


@dataclass(frozen=True)
class PositionState:
    """Snapshot of one open position (entry facts + stop facts)."""

    # ---- identity ----
    ticker: str
    direction: str
    shares: int                                  # integer shares, > 0

    # ---- entry facts ----
    entry_fill_price: float                      # USD/share (actual fill)
    entry_session: str                           # session date of the fill
    atr_at_entry: float                          # USD/share

    # ---- stop facts ----
    initial_stop_price: float                    # USD/share — defines 1R
    current_stop_price: float                    # USD/share — in force now

    # ---- anchors (trailing basis) ----
    highest_price_since_entry: float | None = None   # LONG anchor
    lowest_price_since_entry: float | None = None    # SHORT anchor

    # ---- context ----
    take_profit_price: float | None = None
    entry_regime: str | None = None
    entry_reason: str | None = None
    stop_reason_code: str = INITIAL_ATR
    stop_updated_session: str | None = None

    provenance: Provenance = field(default_factory=Provenance.unknown)
    contract_name: str = "PositionState"

    # -----------------------------------------------------------------
    def validate(self) -> "PositionState":
        c = self.contract_name
        require_non_empty_str(self.ticker, "validate", "ticker", c)
        require_choice(self.direction, DIRECTIONS, "validate", "direction", c)
        require_int_shares(self.shares, "validate", "shares", c)
        if self.shares <= 0:
            raise ContractError(c, "shares",
                                "an open position requires shares > 0")

        require_positive(self.entry_fill_price, "validate", "entry_fill_price", c)
        require_session_date(self.entry_session, "validate", "entry_session", c)
        require_positive(self.atr_at_entry, "validate", "atr_at_entry", c)
        require_positive(self.initial_stop_price, "validate",
                         "initial_stop_price", c)
        require_positive(self.current_stop_price, "validate",
                         "current_stop_price", c)

        # ---- stop placement vs entry fill ----
        if self.direction == LONG:
            if not self.initial_stop_price < self.entry_fill_price:
                raise ContractError(
                    c, "initial_stop_price",
                    f"LONG stop must be < entry_fill_price "
                    f"({self.initial_stop_price} >= {self.entry_fill_price})")
            if self.current_stop_price < self.initial_stop_price:
                raise ContractError(c, "current_stop_price",
                                    "LONG stop may not loosen")
            if (self.highest_price_since_entry is not None
                    and self.highest_price_since_entry < self.entry_fill_price):
                raise ContractError(
                    c, "highest_price_since_entry",
                    "LONG anchor must be >= entry_fill_price "
                    f"({self.highest_price_since_entry} < {self.entry_fill_price})")
            if (self.take_profit_price is not None
                    and self.take_profit_price <= self.entry_fill_price):
                raise ContractError(c, "take_profit_price",
                                    "LONG take profit must be > entry_fill_price")
        else:
            if not self.initial_stop_price > self.entry_fill_price:
                raise ContractError(
                    c, "initial_stop_price",
                    f"SHORT stop must be > entry_fill_price "
                    f"({self.initial_stop_price} <= {self.entry_fill_price})")
            if self.current_stop_price > self.initial_stop_price:
                raise ContractError(c, "current_stop_price",
                                    "SHORT stop may not loosen")
            if (self.lowest_price_since_entry is not None
                    and self.lowest_price_since_entry > self.entry_fill_price):
                raise ContractError(
                    c, "lowest_price_since_entry",
                    "SHORT anchor must be <= entry_fill_price "
                    f"({self.lowest_price_since_entry} > {self.entry_fill_price})")
            if (self.take_profit_price is not None
                    and self.take_profit_price >= self.entry_fill_price):
                raise ContractError(c, "take_profit_price",
                                    "SHORT take profit must be < entry_fill_price")

        if self.take_profit_price is not None:
            require_positive(self.take_profit_price, "validate",
                             "take_profit_price", c)
        if self.highest_price_since_entry is not None:
            require_positive(self.highest_price_since_entry, "validate",
                             "highest_price_since_entry", c)
        if self.lowest_price_since_entry is not None:
            require_positive(self.lowest_price_since_entry, "validate",
                             "lowest_price_since_entry", c)

        require_choice(self.stop_reason_code, STOP_REASON_CODES, "validate",
                       "stop_reason_code", c)
        if self.stop_updated_session is not None:
            require_session_date(self.stop_updated_session, "validate",
                                 "stop_updated_session", c)
            if self.stop_updated_session < self.entry_session:
                raise ContractError(
                    c, "stop_updated_session",
                    "stop_updated_session must be >= entry_session")
        self.provenance.validate("validate", c)
        return self

    # -----------------------------------------------------------------
    @property
    def risk_per_share(self) -> float:
        """Initial 1R in USD/share (entry fill vs initial stop)."""
        return abs(self.entry_fill_price - self.initial_stop_price)

    @property
    def anchor_price(self) -> float | None:
        """Trailing anchor for this direction (None if not supplied)."""
        return (self.highest_price_since_entry if self.direction == LONG
                else self.lowest_price_since_entry)

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "shares": self.shares,
            "entry_fill_price": self.entry_fill_price,
            "entry_session": self.entry_session,
            "atr_at_entry": self.atr_at_entry,
            "initial_stop_price": self.initial_stop_price,
            "current_stop_price": self.current_stop_price,
            "highest_price_since_entry": self.highest_price_since_entry,
            "lowest_price_since_entry": self.lowest_price_since_entry,
            "take_profit_price": self.take_profit_price,
            "entry_regime": self.entry_regime,
            "entry_reason": self.entry_reason,
            "stop_reason_code": self.stop_reason_code,
            "stop_updated_session": self.stop_updated_session,
            "risk_per_share": self.risk_per_share,
            "provenance": self.provenance.as_dict(),
        }
