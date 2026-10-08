"""
exit_fixtures.py — deterministic fixtures for Exit Engine parity (Phase 2)

Each case stores enough input state to reproduce a legacy `_exit_check` result
with NO randomness and NO environment dependency:

    pos          legacy position dict (the oracle's input shape)
    bar          OHLC dict
    session_date decision session
    tech_signal  injected signal
    stop_mult    ATR multiple that yields the case's static stop (so the
                 StopPlan is produced by the Stop Engine's own formula)

`build_context(case)` runs the real Stop Engine (Phase 1) over the same inputs
to obtain the authoritative StopPlan + arming gate, exactly as a live caller
would. `call_legacy(case)` calls the untouched legacy oracle.

The discriminating config (`trailing_atr_mult=0.8` vs `stop_atr_mult=1.5`)
mirrors the Phase 1 approach: mixing up the trigger multiple with the trail
multiple fails the parity assertions.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.contracts.base import PriceBar
from production.contracts.position import PositionState
from production.exits.context import build_exit_context
from production.stops import initial_stop_plan, update_stop


class LegacyCfg:
    """Duck-typed config for the legacy oracle AND the new engine."""
    stop_atr_mult = 1.5
    trailing_atr_mult = 0.8
    trailing_trigger_r = 1.0
    max_holding_days = 30
    # unused by _exit_check, present for completeness
    risk_per_trade = 0.01
    take_profit_atr_mult = 2.5
    max_open_positions = 5
    max_position_pct = 0.25


def _entry(session: str, days: int) -> str:
    d = datetime.strptime(session, "%Y-%m-%d") - timedelta(days=days)
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Case table
# ---------------------------------------------------------------------------
# common: entry price 100, ATR 2, trail multiple 0.8 -> trail = anchor - 1.6
# static stop from stop_mult:  stop = 100 - 2 * stop_mult
_SESSION = "2026-09-30"
_ENTRY_SESSION = _entry(_SESSION, 10)          # 10 calendar days held by default

CASES: dict[str, dict] = {
    # 1 STOP_LOSS by open/gap -------------------------------------------------
    "stop_loss_gap": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,        # stop 97
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=95.0, high=96.0, low=94.0, close=95.5),
        tech_signal="neutral", held_days=10,
        expected=dict(reason="STOP_LOSS", exit_price=95.0, fill_model="GAP")),

    # 2 STOP_LOSS by intraday low --------------------------------------------
    "stop_loss_intraday": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=99.0, high=99.5, low=96.0, close=98.0),
        tech_signal="neutral", held_days=10,
        expected=dict(reason="STOP_LOSS", exit_price=97.0, fill_model="STOP")),

    # 3 TAKE_PROFIT ----------------------------------------------------------
    "take_profit": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=110.0, highest_since_entry=105.0),
        bar=dict(open=105.0, high=111.0, low=104.0, close=109.0),
        tech_signal="neutral", held_days=5,
        expected=dict(reason="TAKE_PROFIT", exit_price=110.0, fill_model="TARGET")),

    # 4 TRAILING_STOP by gap --------------------------------------------------
    #    highest 110 -> armed, trail = 110 - 1.6 = 108.4
    "trailing_gap": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=110.0),
        bar=dict(open=108.0, high=108.5, low=107.0, close=107.5),
        tech_signal="neutral", held_days=10,
        expected=dict(reason="TRAILING_STOP", exit_price=108.0, fill_model="GAP")),

    # 5 TRAILING_STOP intraday ------------------------------------------------
    "trailing_intraday": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=110.0),
        bar=dict(open=109.5, high=109.8, low=108.0, close=108.2),
        tech_signal="neutral", held_days=10,
        expected=dict(reason="TRAILING_STOP", exit_price=108.4,
                      fill_model="TRAILING")),

    # 6 TIME_STOP (stop parked far away via a large ATR multiple) -------------
    "time_stop": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=25.0,      # stop 50
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=101.0, high=102.0, low=99.0, close=101.5),
        tech_signal="neutral", held_days=30,
        expected=dict(reason="TIME_STOP", exit_price=101.5, fill_model="CLOSE")),

    # 7 SIGNAL_EXIT -----------------------------------------------------------
    "signal_exit": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=25.0,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=101.0, high=102.0, low=99.0, close=100.75),
        tech_signal="bearish", held_days=5,
        expected=dict(reason="SIGNAL_EXIT", exit_price=100.75, fill_model="CLOSE")),

    # 8 no exit ---------------------------------------------------------------
    "no_exit": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=101.0),
        bar=dict(open=100.5, high=101.2, low=99.8, close=100.9),
        tech_signal="neutral", held_days=5,
        expected=None),

    # 9 stop + target on the same bar -> STOP_LOSS wins -----------------------
    "stop_and_target_same_bar": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,        # stop 97
                 take_profit=110.0, highest_since_entry=100.0),
        bar=dict(open=96.0, high=115.0, low=95.0, close=110.0),
        tech_signal="neutral", held_days=5,
        expected=dict(reason="STOP_LOSS", exit_price=96.0, fill_model="GAP")),

    # 10 open == stop ---------------------------------------------------------
    "open_equals_stop": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=97.0, high=99.0, low=96.5, close=98.0),
        tech_signal="neutral", held_days=5,
        expected=dict(reason="STOP_LOSS", exit_price=97.0, fill_model="GAP")),

    # 11 low == stop ----------------------------------------------------------
    "low_equals_stop": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=99.0, high=99.5, low=97.0, close=98.5),
        tech_signal="neutral", held_days=5,
        expected=dict(reason="STOP_LOSS", exit_price=97.0, fill_model="STOP")),

    # 12 high == target -------------------------------------------------------
    "high_equals_target": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=110.0, highest_since_entry=104.0),
        bar=dict(open=104.0, high=110.0, low=103.5, close=109.5),
        tech_signal="neutral", held_days=5,
        expected=dict(reason="TAKE_PROFIT", exit_price=110.0, fill_model="TARGET")),

    # 13 trailing trigger EXACTLY armed --------------------------------------
    #    r_dist = 2*1.5 = 3 ; trigger anchor = 103 ; trail = 103 - 1.6 = 101.4
    "trailing_armed_exact": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=103.0),
        bar=dict(open=102.5, high=102.8, low=101.0, close=101.2),
        tech_signal="neutral", held_days=5,
        expected=dict(reason="TRAILING_STOP", exit_price=101.4,
                      fill_model="TRAILING")),

    # 14 trailing trigger just BELOW threshold -> not armed -> no exit --------
    "trailing_not_armed": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=1.5,
                 take_profit=200.0, highest_since_entry=102.999999),
        bar=dict(open=102.5, high=102.8, low=101.0, close=101.2),
        tech_signal="neutral", held_days=5,
        expected=None),

    # 15 held_days == 0 -------------------------------------------------------
    "held_days_zero": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=25.0,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=101.0, high=102.0, low=99.0, close=101.0),
        tech_signal="neutral", held_days=0,
        expected=None),

    # 16 held_days == max - 1 -------------------------------------------------
    "held_days_max_minus_one": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=25.0,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=101.0, high=102.0, low=99.0, close=101.0),
        tech_signal="neutral", held_days=29,
        expected=None),

    # 17 held_days == max -----------------------------------------------------
    "held_days_max": dict(
        pos=dict(entry_price=100.0, atr_at_entry=2.0, stop_mult=25.0,
                 take_profit=200.0, highest_since_entry=100.0),
        bar=dict(open=101.0, high=102.0, low=99.0, close=101.0),
        tech_signal="neutral", held_days=30,
        expected=dict(reason="TIME_STOP", exit_price=101.0, fill_model="CLOSE")),
}


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
def case_parts(case: dict) -> dict:
    """Materialise session dates + the legacy position dict for one case."""
    session = _SESSION
    entry_session = _entry(session, case["held_days"])
    p = case["pos"]
    stop = p["entry_price"] - p["atr_at_entry"] * p["stop_mult"]
    legacy_pos = {
        "ticker": "TEST", "direction": "LONG", "shares": 10,
        "entry_price": p["entry_price"], "entry_date": entry_session,
        "atr_at_entry": p["atr_at_entry"], "stop_price": stop,
        "take_profit": p["take_profit"],
        "highest_since_entry": p["highest_since_entry"],
        "entry_regime": "BULL", "entry_reasoning": "fixture",
    }
    return {"session": session, "entry_session": entry_session,
            "legacy_pos": legacy_pos, "stop": stop,
            "bar": dict(case["bar"]), "tech_signal": case["tech_signal"]}


def call_legacy(case: dict, cfg=LegacyCfg) -> dict | None:
    """Run the untouched legacy oracle for this case."""
    from src.portfolio.portfolio_manager import _exit_check
    parts = case_parts(case)
    return _exit_check(parts["legacy_pos"], parts["bar"], parts["session"],
                       parts["tech_signal"], cfg)


def build_context(case: dict, cfg=LegacyCfg):
    """Produce the ExitContext via the REAL Stop Engine (Phase 1)."""
    parts = case_parts(case)
    p = case["pos"]
    position = PositionState(
        ticker="TEST", direction="LONG", shares=10,
        entry_fill_price=p["entry_price"], entry_session=parts["entry_session"],
        atr_at_entry=p["atr_at_entry"], initial_stop_price=parts["stop"],
        current_stop_price=parts["stop"],
        highest_price_since_entry=p["highest_since_entry"],
        take_profit_price=p["take_profit"], entry_regime="BULL",
        entry_reason="fixture").validate()

    plan = initial_stop_plan(
        ticker="TEST", session_date=parts["entry_session"],
        reference_price=p["entry_price"], reference_price_source="FILL",
        atr_value=p["atr_at_entry"], atr_multiple=p["stop_mult"])
    update = update_stop(
        plan, entry_fill_price=p["entry_price"],
        anchor_price=p["highest_since_entry"],
        trailing_atr_multiple=cfg.trailing_atr_mult,
        trailing_trigger_r=cfg.trailing_trigger_r,
        session_date=parts["session"])

    bar = PriceBar(**parts["bar"]).validate()
    return build_exit_context(
        position=position, stop_plan=update.plan, bar=bar,
        tech_signal=parts["tech_signal"], session_date=parts["session"],
        max_holding_days=cfg.max_holding_days,
        trailing_armed=update.evaluation.armed)
