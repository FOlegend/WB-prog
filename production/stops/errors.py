"""
errors.py — Stop Engine errors

Engine inputs fail loud with a reason code (architecture decision 9), so a
caller can never mistake "no data" for "no stop needed".
"""
from __future__ import annotations

from production.contracts.reason_codes import STOP_ERROR_CODES


class StopEngineError(ValueError):
    """Raised when the Stop Engine cannot compute a valid stop."""

    def __init__(self, reason_code: str, detail: str, *,
                 field_name: str = "", ticker: str | None = None):
        if reason_code not in STOP_ERROR_CODES:
            raise ValueError(f"unknown stop error reason code: {reason_code!r}")
        self.reason_code = reason_code
        self.detail = detail
        self.field_name = field_name
        self.ticker = ticker
        parts = [p for p in (ticker, field_name) if p]
        prefix = f"{'/'.join(parts)}: " if parts else ""
        super().__init__(f"{reason_code}: {prefix}{detail}")

    def as_dict(self) -> dict:
        return {"reason_code": self.reason_code, "detail": self.detail,
                "field": self.field_name, "ticker": self.ticker}
