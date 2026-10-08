"""
rules.py — the five exit rules (Phase 2), legacy-exact

Each rule is a pure function of ExitContext returning an ExitDecision (or None).
They are evaluated in the LEGACY priority order by engine.evaluate_exit:

    1 STOP_LOSS   2 TAKE_PROFIT   3 TRAILING_STOP   4 TIME_STOP   5 SIGNAL_EXIT

so "stop wins over target on the same bar" falls out of the ordering — there is
deliberately NO intrabar path inference.

No trailing-stop arithmetic and no ATR arithmetic may appear in this file: the
levels come from `StopPlan` (initial_stop_price / current_stop_price) and the
arming gate comes from the Stop Engine via ExitContext.

LONG is the legacy behaviour; SHORT is its exact mirror (contract-level only —
production remains LONG-only).
"""
from __future__ import annotations

from datetime import datetime

from production.contracts.exit import ExitDecision
from production.contracts.reason_codes import (EXIT_SIGNAL_EXIT, EXIT_STOP_LOSS,
                                               EXIT_TAKE_PROFIT,
                                               EXIT_TIME_STOP,
                                               EXIT_TRAILING_STOP, FILL_CLOSE,
                                               FILL_GAP, FILL_STOP, FILL_TARGET,
                                               FILL_TRAILING, LONG,
                                               TECH_SIGNAL_BEARISH)
from production.exits.context import ExitContext


def _held_days(ctx: ExitContext) -> int:
    """Legacy semantics: CALENDAR days between entry and the decision session.

    The legacy implementation wrapped this in try/except and fell back to 0.
    Here both dates are validated contract inputs, so a malformed date fails
    loud at construction instead of silently becoming 0.
    """
    return (datetime.strptime(ctx.session_date, "%Y-%m-%d")
            - datetime.strptime(ctx.position.entry_session, "%Y-%m-%d")).days


def _decision(ctx: ExitContext, *, reason: str, price: float, fill_model: str,
              detail: str, stop_ref: float | None = None,
              target_ref: float | None = None) -> ExitDecision:
    return ExitDecision(
        should_exit=True, ticker=ctx.position.ticker,
        direction=ctx.position.direction, session_date=ctx.session_date,
        exit_reason_code=reason, exit_price=price, fill_model=fill_model,
        stop_reference_price=stop_ref, target_reference_price=target_ref,
        detail=detail, bar=ctx.bar, provenance=ctx.provenance)


# ---------------------------------------------------------------------------
# 1. STOP_LOSS — the STATIC protective stop (StopPlan.initial_stop_price)
#    legacy: pos["stop_price"]; gap-through fills at the open, intraday at stop
# ---------------------------------------------------------------------------
def rule_stop_loss(ctx: ExitContext) -> ExitDecision | None:
    stop = ctx.stop_plan.initial_stop_price
    bar = ctx.bar
    long_side = ctx.position.direction == LONG

    if long_side and bar.open <= stop:
        return _decision(ctx, reason=EXIT_STOP_LOSS, price=bar.open,
                         fill_model=FILL_GAP, stop_ref=stop,
                         detail=f"開盤 {bar.open:.2f} ≤ 止損 {stop:.2f}（跳空越過）")
    if not long_side and bar.open >= stop:
        return _decision(ctx, reason=EXIT_STOP_LOSS, price=bar.open,
                         fill_model=FILL_GAP, stop_ref=stop,
                         detail=f"開盤 {bar.open:.2f} ≥ 止損 {stop:.2f}（跳空越過）")

    if long_side and bar.low <= stop:
        return _decision(ctx, reason=EXIT_STOP_LOSS, price=stop,
                         fill_model=FILL_STOP, stop_ref=stop,
                         detail=f"盤中低點 {bar.low:.2f} ≤ 止損 {stop:.2f}")
    if not long_side and bar.high >= stop:
        return _decision(ctx, reason=EXIT_STOP_LOSS, price=stop,
                         fill_model=FILL_STOP, stop_ref=stop,
                         detail=f"盤中高點 {bar.high:.2f} ≥ 止損 {stop:.2f}")
    return None


# ---------------------------------------------------------------------------
# 2. TAKE_PROFIT — fills exactly at the target (legacy: pos["take_profit"])
# ---------------------------------------------------------------------------
def rule_take_profit(ctx: ExitContext) -> ExitDecision | None:
    target = ctx.target_reference_price
    if target is None:                       # legacy guard: no target -> skip
        return None
    bar = ctx.bar
    if ctx.position.direction == LONG:
        if bar.high >= target:
            return _decision(ctx, reason=EXIT_TAKE_PROFIT, price=target,
                             fill_model=FILL_TARGET, target_ref=target,
                             detail=f"盤中高點 {bar.high:.2f} ≥ 止盈 {target:.2f}")
        return None
    if bar.low <= target:
        return _decision(ctx, reason=EXIT_TAKE_PROFIT, price=target,
                         fill_model=FILL_TARGET, target_ref=target,
                         detail=f"盤中低點 {bar.low:.2f} ≤ 止盈 {target:.2f}")
    return None


# ---------------------------------------------------------------------------
# 3. TRAILING_STOP — gated by the Stop Engine's arming state, level taken from
#    StopPlan.current_stop_price (never recalculated here)
# ---------------------------------------------------------------------------
def rule_trailing_stop(ctx: ExitContext) -> ExitDecision | None:
    if not ctx.trailing_armed:               # legacy: arming checked before use
        return None
    trail = ctx.stop_plan.current_stop_price
    bar = ctx.bar
    anchor = ctx.position.anchor_price

    if ctx.position.direction == LONG:
        if bar.open <= trail:
            return _decision(ctx, reason=EXIT_TRAILING_STOP, price=bar.open,
                             fill_model=FILL_GAP, stop_ref=trail,
                             detail=f"開盤 {bar.open:.2f} ≤ 移動停利 {trail:.2f}")
        if bar.low <= trail:
            return _decision(ctx, reason=EXIT_TRAILING_STOP, price=trail,
                             fill_model=FILL_TRAILING, stop_ref=trail,
                             detail=(f"從最高 {anchor:.2f} 回撤至 {bar.low:.2f} ≤ "
                                     f"移動停利 {trail:.2f}"))
        return None

    if bar.open >= trail:
        return _decision(ctx, reason=EXIT_TRAILING_STOP, price=bar.open,
                         fill_model=FILL_GAP, stop_ref=trail,
                         detail=f"開盤 {bar.open:.2f} ≥ 移動停利 {trail:.2f}")
    if bar.high >= trail:
        return _decision(ctx, reason=EXIT_TRAILING_STOP, price=trail,
                         fill_model=FILL_TRAILING, stop_ref=trail,
                         detail=(f"從最低 {anchor:.2f} 回升至 {bar.high:.2f} ≥ "
                                 f"移動停利 {trail:.2f}"))
    return None


# ---------------------------------------------------------------------------
# 4. TIME_STOP — calendar-day holding limit, filled at the close
# ---------------------------------------------------------------------------
def rule_time_stop(ctx: ExitContext) -> ExitDecision | None:
    held = _held_days(ctx)
    limit = ctx.config.max_holding_days
    if held >= limit:
        return _decision(ctx, reason=EXIT_TIME_STOP, price=ctx.bar.close,
                         fill_model=FILL_CLOSE,
                         detail=f"持有 {held} 天 ≥ 上限 {limit} 天")
    return None


# ---------------------------------------------------------------------------
# 5. SIGNAL_EXIT — injected bearish technical signal, filled at the close
# ---------------------------------------------------------------------------
def rule_signal_exit(ctx: ExitContext) -> ExitDecision | None:
    if ctx.tech_signal == TECH_SIGNAL_BEARISH:
        return _decision(ctx, reason=EXIT_SIGNAL_EXIT, price=ctx.bar.close,
                         fill_model=FILL_CLOSE,
                         detail="technicals_agent 轉 bearish")
    return None


# legacy priority order (do not reorder — STOP wins over TARGET on one bar)
RULES = (rule_stop_loss, rule_take_profit, rule_trailing_stop, rule_time_stop,
         rule_signal_exit)
