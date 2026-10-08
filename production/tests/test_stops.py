"""
tests/test_stops.py — Stop Engine unit tests (Phase 1)

Plain-script runner (repository convention: no pytest — architecture decision 8).
Run:  python production/tests/test_stops.py

Covers the required cases:
  1  ATR initial stop LONG
  2  ATR initial stop SHORT
  3  trailing LONG
  4  trailing SHORT
  5  trigger threshold (exact / just below)
  6  LONG stop cannot loosen
  7  SHORT stop cannot loosen
  8  invalid price
  9  invalid ATR
 10  invalid multiple
 11  invalid direction
 12  deterministic repeated calculation
 13  parity against the existing legacy formulas (risk_manager + portfolio_manager)

Extra guards
  * Phase 1 isolation: the production pipeline must NOT import production.stops
  * NEW_HIGH vs TRAILING_ATR reason semantics
  * plan_from_position does not mutate / persist anything
"""
from __future__ import annotations

import inspect
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.contracts.position import PositionState
from production.contracts.reason_codes import LONG, NEW_HIGH, SHORT, \
    STOP_UNCHANGED, TRAILING_ATR, TRAILING_NOT_ARMED
from production.stops import (StopEngineError, atr_initial_stop,
                              atr_trailing_stop, initial_stop_plan,
                              plan_from_position, update_stop)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _LegacyCfg:
    """Duck-typed config for the legacy functions, with DELIBERATELY different
    stop/trailing multiples so a wrong variable is caught by the parity tests.

    stop_atr_mult    = 1.5  (also the 1R / trigger multiple in the baseline)
    trailing_atr_mult= 0.8  (trail distance)
    trailing_trigger_r=1.0
    """
    stop_atr_mult = 1.5
    trailing_atr_mult = 0.8
    trailing_trigger_r = 1.0
    max_holding_days = 30
    # size_position needs these too (not used by _exit_check)
    risk_per_trade = 0.01
    take_profit_atr_mult = 2.5
    max_open_positions = 5
    max_position_pct = 0.25


def _legacy_trailing_stop(entry, atr, anchor, cfg=_LegacyCfg):
    """The legacy trailing formula, written out for documentation/parity."""
    r_dist = atr * cfg.stop_atr_mult
    armed = anchor >= entry + cfg.trailing_trigger_r * r_dist
    trail = anchor - cfg.trailing_atr_mult * atr
    return armed, trail


def _pos_for_legacy(ticker, entry, atr, anchor, cfg=_LegacyCfg):
    """Position dict in the shape the legacy _exit_check expects.

    stop_price is placed far below and take_profit far above so ONLY the
    trailing block can fire.
    """
    return {
        "ticker": ticker, "direction": "LONG", "shares": 10,
        "entry_price": float(entry), "entry_date": "2026-09-01",
        "atr_at_entry": float(atr), "stop_price": float(entry) - 100.0,
        "take_profit": float(entry) + 1000.0,
        "highest_since_entry": float(anchor), "entry_regime": "BULL",
        "entry_reasoning": "parity-test",
    }


def _plan(ticker="TEST", entry=100.0, atr=2.0, multiple=1.5, direction=LONG):
    return initial_stop_plan(
        ticker=ticker, session_date="2026-09-01", reference_price=entry,
        reference_price_source="FILL", atr_value=atr, atr_multiple=multiple,
        direction=direction)


# ---------------------------------------------------------------------------
# 1 / 2 — initial stop LONG + SHORT
# ---------------------------------------------------------------------------
def test_atr_initial_stop_long():
    calc = atr_initial_stop(reference_price=100.0, atr_value=2.0,
                            atr_multiple=1.5, direction=LONG)
    assert abs(calc.stop_price - 97.0) < 1e-12, calc.stop_price
    assert abs(calc.distance - 3.0) < 1e-12
    assert calc.reason_code == "INITIAL_ATR"
    assert calc.stop_price < calc.reference_price


def test_atr_initial_stop_short():
    calc = atr_initial_stop(reference_price=100.0, atr_value=2.0,
                            atr_multiple=1.5, direction=SHORT)
    assert abs(calc.stop_price - 103.0) < 1e-12, calc.stop_price
    assert abs(calc.distance - 3.0) < 1e-12
    assert calc.stop_price > calc.reference_price


# ---------------------------------------------------------------------------
# 3 / 4 — trailing LONG + SHORT
# ---------------------------------------------------------------------------
def test_trailing_long():
    # entry 100, ATR 2, stop mult 1.5 -> r_dist 3.0 ; trigger at 103.0
    ev = atr_trailing_stop(direction=LONG, anchor_price=110.0,
                           entry_fill_price=100.0, atr_at_entry=2.0,
                           stop_atr_multiple=1.5, trailing_atr_multiple=0.8,
                           trailing_trigger_r=1.0)
    assert ev.armed is True
    assert abs(ev.candidate_stop - (110.0 - 1.6)) < 1e-12, ev.candidate_stop
    assert abs(ev.trail_distance - 1.6) < 1e-12
    assert abs(ev.trigger_distance - 3.0) < 1e-12


def test_trailing_short():
    ev = atr_trailing_stop(direction=SHORT, anchor_price=90.0,
                           entry_fill_price=100.0, atr_at_entry=2.0,
                           stop_atr_multiple=1.5, trailing_atr_multiple=0.8,
                           trailing_trigger_r=1.0)
    assert ev.armed is True
    assert abs(ev.candidate_stop - (90.0 + 1.6)) < 1e-12, ev.candidate_stop
    # SHORT stop sits ABOVE the anchor (mirror of LONG)
    assert ev.candidate_stop > ev.anchor_price


# ---------------------------------------------------------------------------
# 5 — trigger threshold
# ---------------------------------------------------------------------------
def test_trigger_threshold():
    entry, atr = 100.0, 2.0
    trigger_anchor = entry + 1.0 * (atr * 1.5)      # == 103.0
    armed = atr_trailing_stop(direction=LONG, anchor_price=trigger_anchor,
                              entry_fill_price=entry, atr_at_entry=atr,
                              stop_atr_multiple=1.5, trailing_atr_multiple=0.8,
                              trailing_trigger_r=1.0)
    assert armed.armed is True, "trigger distance reached -> must be armed"

    below = atr_trailing_stop(direction=LONG,
                              anchor_price=trigger_anchor - 1e-9,
                              entry_fill_price=entry, atr_at_entry=atr,
                              stop_atr_multiple=1.5, trailing_atr_multiple=0.8,
                              trailing_trigger_r=1.0)
    assert below.armed is False
    assert below.candidate_stop is None
    assert below.reason_code == TRAILING_NOT_ARMED

    # SHORT mirror
    s_armed = atr_trailing_stop(direction=SHORT,
                                anchor_price=entry - 3.0,
                                entry_fill_price=entry, atr_at_entry=atr,
                                stop_atr_multiple=1.5,
                                trailing_atr_multiple=0.8,
                                trailing_trigger_r=1.0)
    s_below = atr_trailing_stop(direction=SHORT,
                                anchor_price=entry - 3.0 + 1e-9,
                                entry_fill_price=entry, atr_at_entry=atr,
                                stop_atr_multiple=1.5,
                                trailing_atr_multiple=0.8,
                                trailing_trigger_r=1.0)
    assert s_armed.armed is True and s_below.armed is False


# ---------------------------------------------------------------------------
# 6 / 7 — stop cannot loosen
# ---------------------------------------------------------------------------
def test_stop_cannot_loosen_long():
    plan = _plan(entry=100.0, atr=2.0)
    # first tightening move
    up = update_stop(plan, entry_fill_price=100.0, anchor_price=110.0,
                     trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                     previous_anchor_price=103.0)
    assert up.changed and abs(up.updated_stop - 108.4) < 1e-12, up.updated_stop
    # then a lower anchor that would loosen -> must be refused
    down = update_stop(up.plan, entry_fill_price=100.0, anchor_price=105.0,
                       trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                       previous_anchor_price=110.0)
    assert down.changed is False
    assert down.reason_code == STOP_UNCHANGED
    assert abs(down.updated_stop - up.updated_stop) < 1e-12, \
        "LONG stop must never move down"
    assert down.updated_stop >= down.previous_stop


def test_stop_cannot_loosen_short():
    plan = _plan(entry=100.0, atr=2.0, direction=SHORT)
    down = update_stop(plan, entry_fill_price=100.0, anchor_price=90.0,
                       trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                       previous_anchor_price=97.0)
    assert down.changed and abs(down.updated_stop - 91.6) < 1e-12, down.updated_stop
    # a higher anchor would loosen a SHORT stop -> refused
    up = update_stop(down.plan, entry_fill_price=100.0, anchor_price=95.0,
                     trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                     previous_anchor_price=90.0)
    assert up.changed is False
    assert up.reason_code == STOP_UNCHANGED
    assert up.updated_stop <= up.previous_stop, "SHORT stop must never move up"


# ---------------------------------------------------------------------------
# 8 / 9 / 10 / 11 — invalid inputs fail loud with reason codes
# ---------------------------------------------------------------------------
def _expect_error(fn, code, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except StopEngineError as exc:
        assert exc.reason_code == code, f"expected {code}, got {exc.reason_code}"
        return exc
    raise AssertionError(f"expected StopEngineError({code}) — none raised")


def test_invalid_price():
    _expect_error(atr_initial_stop, "INVALID_PRICE",
                  reference_price=0.0, atr_value=2.0, atr_multiple=1.5)
    _expect_error(atr_initial_stop, "INVALID_PRICE",
                  reference_price=-5.0, atr_value=2.0, atr_multiple=1.5)
    _expect_error(atr_trailing_stop, "INVALID_PRICE",
                  direction=LONG, anchor_price=None, entry_fill_price=100.0,
                  atr_at_entry=2.0, stop_atr_multiple=1.5,
                  trailing_atr_multiple=1.0, trailing_trigger_r=1.0)


def test_invalid_atr():
    _expect_error(atr_initial_stop, "ATR_UNAVAILABLE",
                  reference_price=100.0, atr_value=None, atr_multiple=1.5)
    _expect_error(atr_initial_stop, "INVALID_ATR",
                  reference_price=100.0, atr_value=0.0, atr_multiple=1.5)
    _expect_error(atr_initial_stop, "INVALID_ATR",
                  reference_price=100.0, atr_value=-2.0, atr_multiple=1.5)


def test_invalid_multiple():
    _expect_error(atr_initial_stop, "INVALID_MULTIPLE",
                  reference_price=100.0, atr_value=2.0, atr_multiple=0.0)
    _expect_error(atr_initial_stop, "INVALID_MULTIPLE",
                  reference_price=100.0, atr_value=2.0, atr_multiple=None)
    _expect_error(atr_trailing_stop, "INVALID_MULTIPLE",
                  direction=LONG, anchor_price=110.0, entry_fill_price=100.0,
                  atr_at_entry=2.0, stop_atr_multiple=1.5,
                  trailing_atr_multiple=0.0, trailing_trigger_r=1.0)


def test_invalid_direction():
    _expect_error(atr_initial_stop, "INVALID_DIRECTION",
                  reference_price=100.0, atr_value=2.0, atr_multiple=1.5,
                  direction="SIDEWAYS")
    _expect_error(atr_trailing_stop, "INVALID_DIRECTION",
                  direction="BOTH", anchor_price=110.0,
                  entry_fill_price=100.0, atr_at_entry=2.0,
                  stop_atr_multiple=1.5, trailing_atr_multiple=1.0,
                  trailing_trigger_r=1.0)


# ---------------------------------------------------------------------------
# 12 — deterministic repeated calculation
# ---------------------------------------------------------------------------
def test_deterministic_repeated_calculation():
    def run():
        plan = _plan(entry=187.35, atr=4.13)
        upd = update_stop(plan, entry_fill_price=187.35, anchor_price=205.9,
                          trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                          previous_anchor_price=196.2)
        return (plan.as_dict(), upd.as_dict(), upd.explain())

    first, second, third = run(), run(), run()
    assert first == second == third


# ---------------------------------------------------------------------------
# 13 — parity against the legacy implementations
# ---------------------------------------------------------------------------
def test_legacy_parity_initial_stop():
    """New initial stop == legacy risk_manager.size_position stop_price."""
    from src.agents.risk_manager import size_position
    cfg = ProductionConfig()
    cases = [(100.0, 2.0), (250.75, 4.13), (37.5, 1.05), (412.9, 9.87)]
    for price, atr in cases:
        legacy = size_position(10000.0, 10000.0, price, atr, 1.0, 0, cfg)
        assert legacy["allow"], f"legacy sizing denied for {price}/{atr}"
        ours = atr_initial_stop(reference_price=price, atr_value=atr,
                                atr_multiple=cfg.stop_atr_mult)
        assert abs(ours.stop_price - legacy["stop_price"]) < 1e-9, (
            price, atr, ours.stop_price, legacy["stop_price"])
        # 1R parity: legacy stop_distance == our distance
        assert abs(ours.distance - legacy["stop_distance"]) < 1e-9


def test_legacy_parity_trailing():
    """New trailing candidate == legacy _exit_check TRAILING fill price,
    and the 'not armed' gate matches exactly.

    The duck-typed cfg uses stop_atr_mult=1.5 but trailing_atr_mult=0.8, so a
    mix-up between the trigger multiple and the trail multiple is caught.
    """
    from src.portfolio.portfolio_manager import _exit_check
    cfg = _LegacyCfg()

    for entry, atr, anchor in [(100.0, 2.0, 110.0), (100.0, 2.0, 104.0),
                               (250.75, 4.13, 300.0), (50.0, 3.7, 62.0)]:
        armed, legacy_trail = _legacy_trailing_stop(entry, atr, anchor, cfg)
        assert armed, f"case should be armed: {entry}/{atr}/{anchor}"

        # legacy: put low just below the trail so the TRAILING fill fires
        pos = _pos_for_legacy("TEST", entry, atr, anchor, cfg)
        bar = {"open": legacy_trail + 1.0, "high": legacy_trail + 2.0,
               "low": legacy_trail - 0.5, "close": legacy_trail + 0.25}
        legacy_exit = _exit_check(pos, bar, "2026-09-10", "neutral", cfg)
        assert legacy_exit is not None, "legacy should have exited on trailing"
        assert legacy_exit["reason"] == "TRAILING_STOP"
        assert legacy_exit["fill_model"] == "TRAILING"

        ours = atr_trailing_stop(
            direction=LONG, anchor_price=anchor, entry_fill_price=entry,
            atr_at_entry=atr, stop_atr_multiple=cfg.stop_atr_mult,
            trailing_atr_multiple=cfg.trailing_atr_mult,
            trailing_trigger_r=cfg.trailing_trigger_r)
        assert ours.armed is True
        assert abs(ours.candidate_stop - legacy_exit["exit_price"]) < 1e-9, (
            entry, atr, anchor, ours.candidate_stop, legacy_exit["exit_price"])

    # ---- not-armed gate parity (GAP: gap-through stop takes precedence) ----
    entry, atr = 100.0, 2.0
    anchor_below = entry + 1.0 * (atr * cfg.stop_atr_mult) - 1e-6
    pos = _pos_for_legacy("TEST", entry, atr, anchor_below, cfg)
    # a bar that would trigger if the trailing block were evaluated
    bar = {"open": entry, "high": entry + 1.0, "low": entry - 20.0,
           "close": entry - 5.0}
    assert _exit_check(pos, bar, "2026-09-10", "neutral", cfg) is None, \
        "legacy must not exit while the trailing stop is not armed"
    ours = atr_trailing_stop(
        direction=LONG, anchor_price=anchor_below, entry_fill_price=entry,
        atr_at_entry=atr, stop_atr_multiple=cfg.stop_atr_mult,
        trailing_atr_multiple=cfg.trailing_atr_mult,
        trailing_trigger_r=cfg.trailing_trigger_r)
    assert ours.armed is False
    assert ours.reason_code == TRAILING_NOT_ARMED


def test_legacy_parity_engine_end_to_end():
    """Engine-level parity: plan + update reproduce the legacy numbers."""
    from src.agents.risk_manager import size_position
    from src.portfolio.portfolio_manager import _exit_check
    cfg = _LegacyCfg()

    legacy_at_entry = size_position(10000.0, 10000.0, 100.0, 2.0, 1.0, 0,
                                    ProductionConfig())
    plan = initial_stop_plan(ticker="TEST", session_date="2026-09-01",
                             reference_price=100.0,
                             reference_price_source="FILL", atr_value=2.0,
                             atr_multiple=cfg.stop_atr_mult)
    assert abs(plan.initial_stop_price - legacy_at_entry["stop_price"]) < 1e-9

    anchor = 110.0
    pos = _pos_for_legacy("TEST", 100.0, 2.0, anchor, cfg)
    _armed, legacy_trail = _legacy_trailing_stop(100.0, 2.0, anchor, cfg)
    bar = {"open": legacy_trail + 1.0, "high": legacy_trail + 2.0,
           "low": legacy_trail - 0.5, "close": legacy_trail + 0.5}
    legacy_exit = _exit_check(pos, bar, "2026-09-10", "neutral", cfg)

    upd = update_stop(plan, entry_fill_price=100.0, anchor_price=anchor,
                      trailing_atr_multiple=cfg.trailing_atr_mult,
                      trailing_trigger_r=cfg.trailing_trigger_r,
                      previous_anchor_price=105.0)
    assert upd.changed and upd.reason_code == NEW_HIGH
    assert abs(upd.updated_stop - legacy_exit["exit_price"]) < 1e-9
    # initial stop is preserved (1R unchanged) while current moved up
    assert abs(upd.plan.initial_stop_price - plan.initial_stop_price) < 1e-12
    assert upd.plan.current_stop_price > upd.plan.initial_stop_price


# ---------------------------------------------------------------------------
# extra guards
# ---------------------------------------------------------------------------
def test_new_high_vs_trailing_atr_reason():
    plan = _plan(entry=100.0, atr=2.0)
    # anchor advances -> NEW_HIGH
    a = update_stop(plan, entry_fill_price=100.0, anchor_price=110.0,
                    trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                    previous_anchor_price=105.0)
    assert a.reason_code == NEW_HIGH and a.changed
    # anchor unchanged but stop improves -> TRAILING_ATR
    b = update_stop(plan, entry_fill_price=100.0, anchor_price=110.0,
                    trailing_atr_multiple=0.8, trailing_trigger_r=1.0,
                    previous_anchor_price=110.0)
    assert b.reason_code == TRAILING_ATR and b.changed
    # not armed -> TRAILING_NOT_ARMED, unchanged
    c = update_stop(plan, entry_fill_price=100.0, anchor_price=101.0,
                    trailing_atr_multiple=0.8, trailing_trigger_r=1.0)
    assert c.reason_code == TRAILING_NOT_ARMED and not c.changed


def test_plan_from_position_is_read_only():
    pos = PositionState(
        ticker="AAPL", direction=LONG, shares=5, entry_fill_price=180.0,
        entry_session="2026-09-01", atr_at_entry=3.0,
        initial_stop_price=175.5, current_stop_price=184.0,
        highest_price_since_entry=190.0, take_profit_price=200.0,
        entry_regime="BULL", entry_reason="test").validate()
    before = pos.as_dict()
    plan = plan_from_position(pos)
    assert plan.initial_stop_price == 175.5
    assert plan.current_stop_price == 184.0
    assert abs(plan.risk_per_share_value - 4.5) < 1e-12
    assert abs(plan.atr_multiple - 1.5) < 1e-12     # 4.5 / 3.0
    assert pos.as_dict() == before, "PositionState must not be mutated"


def test_stop_plan_validation_short_and_loosening():
    from production.contracts.base import ContractError
    # SHORT plan: current may not exceed initial (loosen)
    try:
        initial_stop_plan(ticker="T", session_date="2026-09-01",
                          reference_price=100.0, reference_price_source="SIGNAL",
                          atr_value=2.0, atr_multiple=1.5, direction=SHORT)
    except ContractError as exc:  # pragma: no cover - sanity
        raise AssertionError(f"unexpected validation error: {exc}")
    from production.contracts.stop import StopPlan
    try:
        StopPlan(ticker="T", direction=SHORT, session_date="2026-09-01",
                 reference_price=100.0, reference_price_source="FILL",
                 initial_stop_price=103.0, current_stop_price=104.0,
                 atr_value=2.0, atr_multiple=1.5).validate()
    except ContractError as exc:
        assert exc.field_name == "current_stop_price"
    else:
        raise AssertionError("SHORT loosening must be rejected")


def test_phase3_wiring_safety_stop_imports():
    """Phase-3 wiring-safety (supersedes the Phase-1 isolation test).

    Phase 1 asserted "nothing imports the Stop Engine". Phase 3 wires it in
    shadow, so the guarantee is upgraded to: the PROTECTED production modules
    still import nothing new, and the Stop Engine is reachable from production
    only through `production/exits/adapter.py`, imported lazily inside a
    pipeline function (never at module level, so `legacy` mode does not even
    load it).
    """
    import ast

    guarded = ["production/portfolio/portfolio.py", "production/risk/risk.py",
               "src/state/state.py", "src/agents/risk_manager.py",
               "src/portfolio/portfolio_manager.py"]
    for rel in guarded:
        path = os.path.join(_REPO_ROOT, rel)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for pkg in ("production.stops", "production.exits",
                    "production.contracts"):
            assert pkg not in src, (
                f"{rel} must not import {pkg} (Phase 3 wiring-safety)")

    # pipeline.py: no module-level import of the engines (lazy only)
    with open(os.path.join(_REPO_ROOT, "production/pipeline.py"),
              encoding="utf-8") as f:
        tree = ast.parse(f.read())
    top: set[str] = set()
    for node in tree.body:                       # direct children ONLY
        if isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module)
        elif isinstance(node, ast.Import):
            top.update(a.name for a in node.names)
    for mod in ("production.stops", "production.exits"):
        assert not any(n == mod or n.startswith(mod + ".") for n in top), \
            f"pipeline.py must not import {mod} at module level (legacy graph)"

    # the adapter is the single production entry to the Stop Engine
    with open(os.path.join(_REPO_ROOT, "production/exits/adapter.py"),
              encoding="utf-8") as f:
        adapter_src = f.read()
    assert "from production.stops import" in adapter_src
    assert "update_stop(" in adapter_src and "plan_from_position(" in adapter_src


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
