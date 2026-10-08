"""
shadow.py — shadow comparison of the legacy exit oracle vs the Exit Engine
(Phase 3)

Contract (Phase-3 spec §1.2–§1.4, §7, §8):

    legacy decision  -> always wins, always continues unchanged
    new engine       -> evaluated, compared, RECORDED, never acted on
    any failure      -> fail-open: record the error, legacy continues

This module is the ONLY place that knows how to compare the two engines. It
holds no trading logic of its own: it asks the adapter for validated inputs,
asks the Exit Engine for an ExitDecision, and classifies the difference.

Divergences are classified (never auto-judged "harmless"):

    SHOULD_EXIT_MISMATCH · REASON_MISMATCH · PRICE_MISMATCH ·
    FILL_MODEL_MISMATCH · STOP_REFERENCE_MISMATCH · TARGET_REFERENCE_MISMATCH ·
    INPUT_VALIDATION_MISMATCH · ENGINE_EXCEPTION

The reference-price checks infer the level the LEGACY fill implies (a STOP /
TRAILING fill is by definition the stop level, a TARGET fill the target level)
so that no trailing arithmetic is performed anywhere in this package.
"""
from __future__ import annotations

from dataclasses import dataclass

from production.contracts.base import ContractError
from production.contracts.reason_codes import (DIV_ENGINE_EXCEPTION,
                                               DIV_FILL_MODEL_MISMATCH,
                                               DIV_INPUT_VALIDATION_MISMATCH,
                                               DIV_PRICE_MISMATCH,
                                               DIV_REASON_MISMATCH,
                                               DIV_SHOULD_EXIT_MISMATCH,
                                               DIV_STOP_REFERENCE_MISMATCH,
                                               DIV_TARGET_REFERENCE_MISMATCH,
                                               FILL_STOP, FILL_TARGET,
                                               FILL_TRAILING, SHADOW_AGREED,
                                               SHADOW_DIVERGED, SHADOW_ERROR)
from production.exits.adapter import adapt_exit_inputs
from production.exits.engine import evaluate_exit
from production.stops.errors import StopEngineError

SHADOW_NAME = "production/exits/shadow.py"
TOL = 1e-9

# errors raised for *malformed input* (as opposed to an engine malfunction)
INPUT_ERROR_TYPES = (ContractError, StopEngineError, ValueError, KeyError,
                     TypeError)


@dataclass(frozen=True)
class ShadowExitResult:
    """One position's shadow comparison for one session (JSON-serialisable)."""
    ticker: str
    session_date: str
    enabled: bool = True
    evaluated: bool = False
    status: str = SHADOW_ERROR
    agree: bool | None = None
    legacy: dict | None = None
    new: dict | None = None
    divergence_types: tuple = ()
    stop_plan: dict | None = None
    error: str | None = None
    error_type: str | None = None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "session_date": self.session_date,
            "enabled": self.enabled,
            "evaluated": self.evaluated,
            "status": self.status,
            "agree": self.agree,
            "legacy": self.legacy,
            "new": self.new,
            "divergence_types": list(self.divergence_types),
            "stop_plan": self.stop_plan,
            "error": self.error,
            "error_type": self.error_type,
        }


def _legacy_view(legacy_result: dict | None) -> dict | None:
    if legacy_result is None:
        return None
    return {
        "reason": legacy_result.get("reason"),
        "exit_price": (float(legacy_result["exit_price"])
                       if legacy_result.get("exit_price") is not None else None),
        "fill_model": legacy_result.get("fill_model"),
    }


def _new_view(decision) -> dict:
    return {
        "should_exit": bool(decision.should_exit),
        "reason": decision.exit_reason_code,
        "exit_price": (float(decision.exit_price)
                       if decision.exit_price is not None else None),
        "fill_model": decision.fill_model,
        "stop_reference_price": decision.stop_reference_price,
        "target_reference_price": decision.target_reference_price,
        "detail": decision.detail,
    }


def _classify(legacy: dict | None, new: dict, decision) -> list[str]:
    """Return every divergence type found (empty list == full agreement)."""
    out: list[str] = []
    legacy_exits = legacy is not None
    new_exits = bool(new["should_exit"])

    if legacy_exits != new_exits:
        out.append(DIV_SHOULD_EXIT_MISMATCH)

    if legacy_exits and new_exits:
        if legacy["reason"] != new["reason"]:
            out.append(DIV_REASON_MISMATCH)
        lp, np_ = legacy["exit_price"], new["exit_price"]
        if lp is None or np_ is None or abs(lp - np_) > TOL:
            out.append(DIV_PRICE_MISMATCH)
        if legacy["fill_model"] != new["fill_model"]:
            out.append(DIV_FILL_MODEL_MISMATCH)

        # the legacy fill model identifies the level it filled at
        fill = legacy["fill_model"]
        if fill in (FILL_STOP, FILL_TRAILING):
            # a STOP/TRAILING fill IS the stop level -> compare to the level the
            # Stop Engine handed the Exit Engine through StopPlan
            ref = decision.stop_reference_price
            if (ref is None or lp is None or abs(float(ref) - float(lp)) > TOL):
                out.append(DIV_STOP_REFERENCE_MISMATCH)
        elif fill == FILL_TARGET:
            ref = decision.target_reference_price
            if (ref is None or lp is None or abs(float(ref) - float(lp)) > TOL):
                out.append(DIV_TARGET_REFERENCE_MISMATCH)
    return out


def shadow_exit_check(pos: dict, bar: dict, tech_signal: str, session_date: str,
                      legacy_result: dict | None, cfg) -> ShadowExitResult:
    """Evaluate the new engine in shadow and compare it with the legacy oracle.

    NEVER raises: any failure is captured as a divergence/error record and the
    caller's legacy decision is unaffected (fail-open).
    """
    ticker = str(pos.get("ticker", "?"))
    try:
        adapted = adapt_exit_inputs(pos, bar, tech_signal, session_date, cfg)
    except INPUT_ERROR_TYPES as exc:
        return ShadowExitResult(
            ticker=ticker, session_date=session_date, status=SHADOW_ERROR,
            agree=None, legacy=_legacy_view(legacy_result), new=None,
            divergence_types=(DIV_INPUT_VALIDATION_MISMATCH,),
            error=str(exc), error_type=type(exc).__name__)
    except Exception as exc:                       # pragma: no cover - defensive
        return ShadowExitResult(
            ticker=ticker, session_date=session_date, status=SHADOW_ERROR,
            agree=None, legacy=_legacy_view(legacy_result), new=None,
            divergence_types=(DIV_ENGINE_EXCEPTION,),
            error=f"adapter failed: {exc}", error_type=type(exc).__name__)

    snapshot = adapted.stop_plan_snapshot()

    try:
        decision = evaluate_exit(adapted.context)
    except Exception as exc:
        return ShadowExitResult(
            ticker=ticker, session_date=session_date, evaluated=True,
            status=SHADOW_ERROR, agree=None,
            legacy=_legacy_view(legacy_result), new=None,
            divergence_types=(DIV_ENGINE_EXCEPTION,),
            stop_plan=snapshot, error=f"exit engine failed: {exc}",
            error_type=type(exc).__name__)

    legacy = _legacy_view(legacy_result)
    new = _new_view(decision)
    divergences = _classify(legacy, new, decision)
    return ShadowExitResult(
        ticker=ticker, session_date=session_date, evaluated=True,
        status=(SHADOW_AGREED if not divergences else SHADOW_DIVERGED),
        agree=not divergences, legacy=legacy, new=new,
        divergence_types=tuple(divergences), stop_plan=snapshot,
        error=None, error_type=None)


def summarise_shadow(results: list[ShadowExitResult]) -> dict:
    """Aggregate view for the DecisionRecord / monitoring (never a judgement)."""
    by_type: dict[str, int] = {}
    for r in results:
        for t in r.divergence_types or ():
            by_type[t] = by_type.get(t, 0) + 1
    return {
        "enabled": bool(results),
        "n_evaluated": len(results),
        "n_agree": sum(1 for r in results if r.agree is True),
        "n_diverged": sum(1 for r in results if r.agree is False),
        "n_error": sum(1 for r in results if r.status == SHADOW_ERROR),
        "divergence_types": by_type,
    }
