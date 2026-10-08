"""
base.py — shared contract primitives (Phase 0 contracts)

Stdlib-only. No pydantic (architecture decision 5).

Conventions enforced here
------------------------
session_date : "YYYY-MM-DD" naive US trading-session date (never a timestamp)
prices       : USD / share
atr          : USD / share
multiple     : dimensionless
risk_per_share (1R) : USD / share
total risk   : USD
shares       : integer shares

Every contract carries a Provenance so a downstream reader can tell where the
numbers came from (which engine, which session, which source).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

CONTRACT_VERSION = "0.1.0"

SESSION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ContractError(ValueError):
    """Raised when a contract instance violates its own rules."""

    def __init__(self, contract: str, field_name: str, detail: str):
        self.contract = contract
        self.field_name = field_name
        self.detail = detail
        super().__init__(f"{contract}.{field_name}: {detail}")

    def as_dict(self) -> dict:
        return {"contract": self.contract, "field": self.field_name,
                "detail": self.detail}


# ---------------------------------------------------------------------------
# field validators (deterministic, no coercion)
# ---------------------------------------------------------------------------
def require_non_empty_str(value: Any, where: str, field_name: str,
                          contract: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(contract, field_name,
                            f"expected non-empty str, got {value!r}")
    return value


def require_bool(value: Any, where: str, field_name: str, contract: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(contract, field_name,
                            f"expected bool, got {type(value).__name__}")
    return value


def require_session_date(value: Any, where: str, field_name: str,
                         contract: str) -> str:
    if not isinstance(value, str) or not SESSION_DATE_RE.match(value):
        raise ContractError(
            contract, field_name,
            f"expected session date 'YYYY-MM-DD', got {value!r}")
    return value


def require_finite_number(value: Any, where: str, field_name: str,
                          contract: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(contract, field_name,
                            f"expected number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise ContractError(contract, field_name, f"expected finite, got {v!r}")
    return v


def require_positive(value: Any, where: str, field_name: str,
                     contract: str) -> float:
    v = require_finite_number(value, where, field_name, contract)
    if v <= 0:
        raise ContractError(contract, field_name, f"expected > 0, got {v}")
    return v


def require_non_negative(value: Any, where: str, field_name: str,
                         contract: str) -> float:
    v = require_finite_number(value, where, field_name, contract)
    if v < 0:
        raise ContractError(contract, field_name, f"expected >= 0, got {v}")
    return v


def require_int_shares(value: Any, where: str, field_name: str,
                       contract: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(contract, field_name,
                            f"expected int shares, got {type(value).__name__}")
    if value < 0:
        raise ContractError(contract, field_name,
                            f"expected >= 0 shares, got {value}")
    return value


def require_choice(value: Any, allowed: frozenset[str], where: str,
                   field_name: str, contract: str) -> str:
    if value not in allowed:
        raise ContractError(
            contract, field_name,
            f"expected one of {sorted(allowed)}, got {value!r}")
    return str(value)


def require_close(actual: float, expected: float, where: str, field_name: str,
                  contract: str, tol: float = 1e-6) -> None:
    if abs(actual - expected) > max(tol, abs(expected) * tol):
        raise ContractError(
            contract, field_name,
            f"inconsistent with derived value: {actual} vs {expected}")


def deterministic_id(prefix: str, payload: dict) -> str:
    """Stable id derived only from the payload (no clock, no randomness)."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Provenance:
    """Where a contract instance came from (auditability, not strategy)."""
    producer: str
    producer_version: str = CONTRACT_VERSION
    source: str = "unspecified"
    session_date: str | None = None
    details: dict = field(default_factory=dict)

    @classmethod
    def unknown(cls) -> "Provenance":
        return cls(producer="unspecified", source="unspecified")

    @classmethod
    def engine(cls, producer: str, source: str,
               session_date: str | None = None, **details) -> "Provenance":
        return cls(producer=producer, source=source,
                   session_date=session_date, details=dict(details))

    def validate(self, where: str, contract: str = "Provenance") -> None:
        require_non_empty_str(self.producer, where, "producer", contract)
        require_non_empty_str(self.producer_version, where,
                              "producer_version", contract)
        require_non_empty_str(self.source, where, "source", contract)
        if self.session_date is not None:
            require_session_date(self.session_date, where, "session_date",
                                 contract)
        if not isinstance(self.details, dict):
            raise ContractError(contract, "details", "expected dict")

    def as_dict(self) -> dict:
        return {"producer": self.producer,
                "producer_version": self.producer_version,
                "source": self.source,
                "session_date": self.session_date,
                "details": dict(self.details)}


# ---------------------------------------------------------------------------
# Execution window (shared by EntryDecision and OrderIntent)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExecutionWindow:
    """Session-date window in which an order may be executed.

    from_session / to_session are naive US trading-session dates
    ("YYYY-MM-DD"), NOT wall-clock timestamps.
    """
    from_session: str
    to_session: str

    def validate(self, where: str = "ExecutionWindow") -> None:
        require_session_date(self.from_session, where, "from_session",
                             "ExecutionWindow")
        require_session_date(self.to_session, where, "to_session",
                             "ExecutionWindow")
        if self.to_session < self.from_session:
            raise ContractError(
                "ExecutionWindow", "to_session",
                f"must be >= from_session ({self.from_session} > "
                f"{self.to_session})")

    def as_dict(self) -> dict:
        return {"from_session": self.from_session,
                "to_session": self.to_session}


# ---------------------------------------------------------------------------
# PriceBar — the OHLC bar an exit decision is evaluated against
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PriceBar:
    """One session's OHLC bar (USD/share).

    Note (documented deviation): the legacy `_exit_check` tolerated a missing
    `close` by silently falling back to the position entry price. This contract
    requires an explicit close so a bar can never be silently fabricated —
    invalid input fails loud instead.
    """
    open: float
    high: float
    low: float
    close: float

    def validate(self, where: str = "PriceBar") -> "PriceBar":
        for name in ("open", "high", "low", "close"):
            require_positive(getattr(self, name), where, name, "PriceBar")
        if self.high < self.low:
            raise ContractError("PriceBar", "high",
                                f"high < low ({self.high} < {self.low})")
        if not (self.low <= self.open <= self.high):
            raise ContractError("PriceBar", "open",
                                f"open {self.open} outside [low {self.low}, "
                                f"high {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ContractError("PriceBar", "close",
                                f"close {self.close} outside [low {self.low}, "
                                f"high {self.high}]")
        return self

    def as_dict(self) -> dict:
        return {"open": self.open, "high": self.high, "low": self.low,
                "close": self.close}
