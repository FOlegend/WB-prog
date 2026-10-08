"""
entry.py — EntryDecision contract (Phase 0)

EntryDecision is the output of the (future) Entry Engine: "should we open a
position on this ticker, and with what intent?"

Hard boundary (Phase 0 / architecture decision 10)
--------------------------------------------------
signal_price   = the CLOSE of the signal bar (D session close)
expected_execution_price = pre-trade expected/reference execution price; it
                 MAY be None because the next-open price is not known when the
                 signal is produced
NEVER fixed here
    * no shares / quantity   -> sizing is the Risk Engine's job
    * no fill price          -> that exists only after execution
"""
from __future__ import annotations

from dataclasses import dataclass, field

from production.contracts.base import (ContractError, ExecutionWindow,
                                       Provenance, require_bool,
                                       require_choice, require_non_empty_str,
                                       require_positive, require_session_date,
                                       require_finite_number)
from production.contracts.reason_codes import DIRECTIONS, EXECUTION_TYPES, LONG


@dataclass(frozen=True)
class EntryDecision:
    """Entry intent for one ticker on one signal session."""

    # ---- required ----
    approved: bool
    ticker: str
    direction: str
    setup_type: str
    signal_date: str                  # session date of the signal bar (D)
    signal_price: float               # USD/share — D session close

    # ---- execution intent ----
    execution_type: str = "NEXT_OPEN"
    execution_window: ExecutionWindow | None = None
    expected_execution_price: float | None = None   # may be None (unknown yet)

    # ---- evidence ----
    entry_score: float | None = None                # 0..1 (dimensionless)
    entry_reason: str = ""
    vetoes: tuple[str, ...] = ()
    source_components: dict = field(default_factory=dict)

    provenance: Provenance = field(default_factory=Provenance.unknown)

    contract_name: str = "EntryDecision"

    # -----------------------------------------------------------------
    def validate(self) -> "EntryDecision":
        c = self.contract_name
        require_bool(self.approved, "validate", "approved", c)
        require_non_empty_str(self.ticker, "validate", "ticker", c)
        require_choice(self.direction, DIRECTIONS, "validate", "direction", c)
        require_non_empty_str(self.setup_type, "validate", "setup_type", c)
        require_session_date(self.signal_date, "validate", "signal_date", c)
        require_positive(self.signal_price, "validate", "signal_price", c)

        require_choice(self.execution_type, EXECUTION_TYPES, "validate",
                       "execution_type", c)
        if self.execution_window is not None:
            self.execution_window.validate("EntryDecision.execution_window")
            if self.execution_window.from_session < self.signal_date:
                raise ContractError(
                    c, "execution_window",
                    "from_session must be >= signal_date "
                    f"({self.execution_window.from_session} < "
                    f"{self.signal_date})")
        if self.expected_execution_price is not None:
            require_positive(self.expected_execution_price, "validate",
                             "expected_execution_price", c)

        if self.entry_score is not None:
            score = require_finite_number(self.entry_score, "validate",
                                          "entry_score", c)
            if not 0.0 <= score <= 1.0:
                raise ContractError(c, "entry_score",
                                    f"expected 0..1, got {score}")

        if not isinstance(self.vetoes, tuple):
            raise ContractError(c, "vetoes",
                                f"expected tuple[str, ...], got "
                                f"{type(self.vetoes).__name__}")
        for v in self.vetoes:
            require_non_empty_str(v, "validate", "vetoes[]", c)

        # deterministic decision semantics: a rejection must state a reason,
        # and an approval must not carry vetoes.
        if not self.approved and not self.vetoes:
            raise ContractError(c, "vetoes",
                                "approved=False requires at least one veto code")
        if self.approved and self.vetoes:
            raise ContractError(c, "vetoes",
                                "approved=True must not carry vetoes")

        if not isinstance(self.source_components, dict):
            raise ContractError(c, "source_components", "expected dict")
        self.provenance.validate("validate", c)
        return self

    # -----------------------------------------------------------------
    @property
    def timestamp(self) -> str:
        """Alias: the contract's timestamp IS the signal session date."""
        return self.signal_date

    def as_dict(self) -> dict:
        return {
            "approved": self.approved,
            "ticker": self.ticker,
            "direction": self.direction,
            "setup_type": self.setup_type,
            "signal_date": self.signal_date,
            "signal_price": self.signal_price,
            "execution_type": self.execution_type,
            "execution_window": (self.execution_window.as_dict()
                                 if self.execution_window else None),
            "expected_execution_price": self.expected_execution_price,
            "entry_score": self.entry_score,
            "entry_reason": self.entry_reason,
            "vetoes": list(self.vetoes),
            "source_components": dict(self.source_components),
            "provenance": self.provenance.as_dict(),
        }


def entry_decision(*, approved: bool, ticker: str, direction: str = LONG,
                   setup_type: str, signal_date: str, signal_price: float,
                   **kwargs) -> EntryDecision:
    """Build + validate an EntryDecision (fail loud on invalid input)."""
    return EntryDecision(approved=approved, ticker=ticker, direction=direction,
                         setup_type=setup_type, signal_date=signal_date,
                         signal_price=signal_price, **kwargs).validate()
