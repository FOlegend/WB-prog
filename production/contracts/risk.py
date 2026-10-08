"""
risk.py — RiskDecision contract (Phase 0)

The Risk Engine (a later phase) answers: "how much of this trade may we take?"

Three-valued status — deliberately NOT a boolean (architecture decision: the
caller must be able to distinguish approval from resize from rejection):

    APPROVE : full requested size approved
    RESIZE  : a smaller size approved (approved_shares < requested_shares)
    REJECT  : nothing approved (approved_shares == 0, approved_risk == 0)

Units: prices/ATR USD per share · risk amounts USD ·
projected_heat dimensionless (fraction of equity) · shares integer.

Phase 0 defines the contract only — no risk logic is implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from production.contracts.base import (ContractError, Provenance, deterministic_id,
                                       require_choice, require_close,
                                       require_finite_number,
                                       require_int_shares,
                                       require_non_empty_str,
                                       require_non_negative, require_positive,
                                       require_session_date)
from production.contracts.reason_codes import (DIRECTIONS, RISK_APPROVED,
                                               RISK_REASON_CODES, RISK_REJECTED,
                                               RISK_RESIZED, RISK_STATUS_APPROVE,
                                               RISK_STATUS_REJECT,
                                               RISK_STATUS_RESIZE, RISK_STATUSES,
                                               RISK_UNSPECIFIED)


@dataclass(frozen=True)
class RiskDecision:
    """Sizing decision for one candidate trade."""

    # ---- required ----
    status: str                        # APPROVE | RESIZE | REJECT
    ticker: str
    direction: str
    approved_shares: int
    approved_risk: float               # USD
    risk_per_share: float              # USD/share (1R)

    # ---- requested (pre-risk) ----
    requested_shares: int | None = None
    requested_risk: float | None = None

    # ---- portfolio context ----
    current_portfolio_risk: float | None = None      # USD
    projected_portfolio_risk: float | None = None    # USD
    projected_heat: float | None = None              # dimensionless (risk/equity)
    gross_exposure: float | None = None              # USD
    net_exposure: float | None = None                # USD
    remaining_capacity: float | None = None          # USD
    constraints_triggered: tuple[str, ...] = ()

    # ---- explanation / identity ----
    reason_code: str = RISK_UNSPECIFIED
    session_date: str | None = None
    decision_id: str | None = None

    provenance: Provenance = field(default_factory=Provenance.unknown)
    contract_name: str = "RiskDecision"

    def __post_init__(self) -> None:
        if self.decision_id is None:
            object.__setattr__(self, "decision_id", deterministic_id(
                "risk",
                {"status": self.status, "ticker": self.ticker,
                 "direction": self.direction,
                 "approved_shares": self.approved_shares,
                 "approved_risk": self.approved_risk,
                 "risk_per_share": self.risk_per_share,
                 "session_date": self.session_date}))

    # -----------------------------------------------------------------
    def validate(self) -> "RiskDecision":
        c = self.contract_name
        require_choice(self.status, RISK_STATUSES, "validate", "status", c)
        require_non_empty_str(self.ticker, "validate", "ticker", c)
        require_choice(self.direction, DIRECTIONS, "validate", "direction", c)
        require_int_shares(self.approved_shares, "validate", "approved_shares", c)
        require_non_negative(self.approved_risk, "validate", "approved_risk", c)
        require_positive(self.risk_per_share, "validate", "risk_per_share", c)

        if self.requested_shares is not None:
            require_int_shares(self.requested_shares, "validate",
                               "requested_shares", c)
        if self.requested_risk is not None:
            require_non_negative(self.requested_risk, "validate",
                                 "requested_risk", c)

        # ---- status semantics ----
        if self.status == RISK_STATUS_APPROVE:
            if self.approved_shares <= 0:
                raise ContractError(c, "approved_shares",
                                    "APPROVE requires approved_shares > 0")
            if (self.requested_shares is not None
                    and self.approved_shares != self.requested_shares):
                raise ContractError(
                    c, "approved_shares",
                    "APPROVE must equal requested_shares "
                    f"({self.approved_shares} != {self.requested_shares})")
        elif self.status == RISK_STATUS_RESIZE:
            if self.requested_shares is None:
                raise ContractError(c, "requested_shares",
                                    "RESIZE requires requested_shares")
            if not 0 < self.approved_shares < self.requested_shares:
                raise ContractError(
                    c, "approved_shares",
                    "RESIZE requires 0 < approved_shares < requested_shares "
                    f"({self.approved_shares} vs {self.requested_shares})")
        else:  # REJECT
            if self.approved_shares != 0 or self.approved_risk != 0:
                raise ContractError(
                    c, "approved_shares",
                    "REJECT requires approved_shares == 0 and approved_risk == 0")

        # ---- risk consistency ----
        if (self.projected_portfolio_risk is not None
                and self.current_portfolio_risk is not None):
            require_finite_number(self.projected_portfolio_risk, "validate",
                                  "projected_portfolio_risk", c)
            require_finite_number(self.current_portfolio_risk, "validate",
                                  "current_portfolio_risk", c)
            if self.projected_portfolio_risk < self.current_portfolio_risk - 1e-9:
                raise ContractError(
                    c, "projected_portfolio_risk",
                    "must be >= current_portfolio_risk "
                    f"({self.projected_portfolio_risk} < "
                    f"{self.current_portfolio_risk})")
            require_close(self.projected_portfolio_risk,
                          self.current_portfolio_risk + self.approved_risk,
                          "validate", "projected_portfolio_risk", c, tol=1e-6)

        if self.projected_heat is not None:
            heat = require_non_negative(self.projected_heat, "validate",
                                        "projected_heat", c)
            if heat > 1.0:
                raise ContractError(c, "projected_heat",
                                    f"expected fraction of equity (0..1), got {heat}")

        for name in ("gross_exposure", "net_exposure", "remaining_capacity"):
            value = getattr(self, name)
            if value is not None:
                require_finite_number(value, "validate", name, c)
        if self.remaining_capacity is not None and self.remaining_capacity < 0:
            raise ContractError(c, "remaining_capacity",
                                f"expected >= 0, got {self.remaining_capacity}")

        if not isinstance(self.constraints_triggered, tuple):
            raise ContractError(c, "constraints_triggered",
                                "expected tuple[str, ...]")
        for item in self.constraints_triggered:
            require_non_empty_str(item, "validate", "constraints_triggered[]", c)

        # ---- reason code / status coherence ----
        require_choice(self.reason_code, RISK_REASON_CODES, "validate",
                       "reason_code", c)
        expected = {RISK_STATUS_APPROVE: RISK_APPROVED,
                    RISK_STATUS_RESIZE: RISK_RESIZED,
                    RISK_STATUS_REJECT: RISK_REJECTED}[self.status]
        if self.reason_code not in (expected, RISK_UNSPECIFIED):
            raise ContractError(
                c, "reason_code",
                f"status {self.status} expects {expected} or UNSPECIFIED, "
                f"got {self.reason_code}")

        if self.session_date is not None:
            require_session_date(self.session_date, "validate", "session_date", c)
        require_non_empty_str(self.decision_id, "validate", "decision_id", c)
        self.provenance.validate("validate", c)
        return self

    def as_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "status": self.status,
            "ticker": self.ticker,
            "direction": self.direction,
            "requested_shares": self.requested_shares,
            "approved_shares": self.approved_shares,
            "requested_risk": self.requested_risk,
            "approved_risk": self.approved_risk,
            "risk_per_share": self.risk_per_share,
            "current_portfolio_risk": self.current_portfolio_risk,
            "projected_portfolio_risk": self.projected_portfolio_risk,
            "projected_heat": self.projected_heat,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "remaining_capacity": self.remaining_capacity,
            "constraints_triggered": list(self.constraints_triggered),
            "reason_code": self.reason_code,
            "session_date": self.session_date,
            "provenance": self.provenance.as_dict(),
        }
