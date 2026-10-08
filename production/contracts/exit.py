"""
exit.py — ExitDecision contract (Phase 2)

The Exit Engine's answer for one position on one session:

    1. should we exit?         -> should_exit
    2. why?                    -> exit_reason_code (LEGACY vocab) + detail
    3. at what modelled price? -> exit_price
    4. which fill model?       -> fill_model (LEGACY vocab)

Parity discipline (Phase 2 decision 1 + the validation correction)
-----------------------------------------------------------------
`src/portfolio/portfolio_manager.py::_exit_check` is the ORACLE. Every fill
price must reproduce the legacy semantics EXACTLY — there is deliberately no
"reasonable OHLC range" rule anywhere in this contract:

    GAP      -> exit_price == bar.open      (gap through the level)
    STOP     -> exit_price == stop_reference_price
    TRAILING -> exit_price == stop_reference_price
    TARGET   -> exit_price == target_reference_price
    CLOSE    -> exit_price == bar.close

Vocabulary is legacy-compatible by decision (2) and (4) — no renamed codes, so
ledger/backtest reconciliation needs no mapping table.

Not in scope here: shares, sizing, order sending/execution, portfolio state,
or any trailing-stop calculation (the Stop Engine owns stop levels).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from production.contracts.base import (ContractError, PriceBar, Provenance,
                                       require_choice, require_close,
                                       require_non_empty_str, require_positive,
                                       require_session_date)
from production.contracts.reason_codes import (DIRECTIONS, EXIT_REASON_CODES,
                                               EXIT_REASON_FILL_MODELS,
                                               EXIT_STOP_LOSS,
                                               EXIT_TAKE_PROFIT,
                                               EXIT_TRAILING_STOP, FILL_CLOSE,
                                               FILL_GAP, FILL_MODELS, FILL_STOP,
                                               FILL_TARGET, FILL_TRAILING)


@dataclass(frozen=True)
class ExitDecision:
    """Exit decision for one open position on one session."""

    # ---- identity / time ----
    should_exit: bool
    ticker: str
    direction: str
    session_date: str                       # naive US trading session

    # ---- decision (required when should_exit) ----
    exit_reason_code: str | None = None     # STOP_LOSS | TAKE_PROFIT | TRAILING_STOP | TIME_STOP | SIGNAL_EXIT
    exit_price: float | None = None         # USD/share
    fill_model: str | None = None           # STOP | GAP | TARGET | TRAILING | CLOSE

    # ---- levels that caused the decision (auditability, decision 3) ----
    stop_reference_price: float | None = None    # authoritative StopPlan level used
    target_reference_price: float | None = None

    # ---- audit ----
    detail: str = ""
    bar: PriceBar | None = None
    provenance: Provenance = field(default_factory=Provenance.unknown)
    contract_name: str = "ExitDecision"
    # -----------------------------------------------------------------
    def validate(self) -> "ExitDecision":
        c = self.contract_name
        require_non_empty_str(self.ticker, "validate", "ticker", c)
        require_choice(self.direction, DIRECTIONS, "validate", "direction", c)
        require_session_date(self.session_date, "validate", "session_date", c)

        if self.stop_reference_price is not None:
            require_positive(self.stop_reference_price, "validate",
                             "stop_reference_price", c)
        if self.target_reference_price is not None:
            require_positive(self.target_reference_price, "validate",
                             "target_reference_price", c)

        if not self.should_exit:
            # a "hold" decision must not carry execution fields
            for name in ("exit_reason_code", "exit_price", "fill_model"):
                if getattr(self, name) is not None:
                    raise ContractError(
                        c, name,
                        "must be None when should_exit=False")
            self.provenance.validate("validate", c)
            return self

        # ---- exit decision ----
        require_choice(self.exit_reason_code, EXIT_REASON_CODES, "validate",
                       "exit_reason_code", c)
        require_choice(self.fill_model, FILL_MODELS, "validate", "fill_model", c)
        require_positive(self.exit_price, "validate", "exit_price", c)

        allowed_fills = EXIT_REASON_FILL_MODELS[self.exit_reason_code]
        if self.fill_model not in allowed_fills:
            raise ContractError(
                c, "fill_model",
                f"{self.exit_reason_code} may only use "
                f"{sorted(allowed_fills)}, got {self.fill_model}")

        # ---- references required for the level-driven reasons ----
        if self.exit_reason_code in (EXIT_STOP_LOSS, EXIT_TRAILING_STOP):
            if self.stop_reference_price is None:
                raise ContractError(
                    c, "stop_reference_price",
                    f"required for {self.exit_reason_code} (which stop level "
                    "caused the exit must be auditable)")
        if self.exit_reason_code == EXIT_TAKE_PROFIT:
            if self.target_reference_price is None:
                raise ContractError(c, "target_reference_price",
                                    "required for TAKE_PROFIT")

        # ---- EXACT legacy fill semantics (no range heuristics) ----
        if self.fill_model == FILL_GAP:
            if self.bar is None:
                raise ContractError(c, "bar",
                                    "required for a GAP fill (exit_price must "
                                    "equal the bar open)")
            require_close(self.exit_price, self.bar.open, "validate",
                          "exit_price", c)
        elif self.fill_model in (FILL_STOP, FILL_TRAILING):
            if self.stop_reference_price is None:
                raise ContractError(c, "stop_reference_price",
                                    f"required for a {self.fill_model} fill")
            require_close(self.exit_price, self.stop_reference_price,
                          "validate", "exit_price", c)
        elif self.fill_model == FILL_TARGET:
            if self.target_reference_price is None:
                raise ContractError(c, "target_reference_price",
                                    "required for a TARGET fill")
            require_close(self.exit_price, self.target_reference_price,
                          "validate", "exit_price", c)
        elif self.fill_model == FILL_CLOSE:
            if self.bar is None:
                raise ContractError(c, "bar",
                                    "required for a CLOSE fill (exit_price "
                                    "must equal the bar close)")
            require_close(self.exit_price, self.bar.close, "validate",
                          "exit_price", c)

        self.provenance.validate("validate", c)
        return self

    # -----------------------------------------------------------------
    def explain(self) -> str:
        """One-line answer to 'why (not) exit, and at what price?'."""
        if not self.should_exit:
            return (f"{self.ticker} {self.direction}: hold on "
                    f"{self.session_date} — {self.detail or 'no exit trigger'}")
        return (f"{self.ticker} {self.direction}: EXIT "
                f"{self.exit_reason_code} at {self.exit_price} "
                f"({self.fill_model}) on {self.session_date}"
                + (f" — {self.detail}" if self.detail else ""))

    def as_dict(self) -> dict:
        return {
            "should_exit": self.should_exit,
            "ticker": self.ticker,
            "direction": self.direction,
            "session_date": self.session_date,
            "exit_reason_code": self.exit_reason_code,
            "exit_price": self.exit_price,
            "fill_model": self.fill_model,
            "stop_reference_price": self.stop_reference_price,
            "target_reference_price": self.target_reference_price,
            "detail": self.detail,
            "bar": self.bar.as_dict() if self.bar else None,
            "provenance": self.provenance.as_dict(),
        }
