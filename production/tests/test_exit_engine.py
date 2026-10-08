"""
tests/test_exit_engine.py — Exit Engine parity + contract tests (Phase 2)

Plain-script runner (repository convention: no pytest).
Run:  python production/tests/test_exit_engine.py

The oracle is the untouched `src/portfolio/portfolio_manager.py::_exit_check`.
Every parity assertion compares FOUR fields, as required:

    should_exit · exit_reason_code · exit_price · fill_model

Required cases (17) + isolation / symmetry / contract guards.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.contracts.base import ContractError, PriceBar
from production.contracts.exit import ExitDecision
from production.contracts.position import PositionState
from production.contracts.reason_codes import (EXIT_REASON_CODES, FILL_MODELS,
                                               LONG, SHORT)
from production.exits import build_exit_context, evaluate_exit
from production.stops import initial_stop_plan, update_stop
from production.tests.exit_fixtures import (CASES, LegacyCfg, build_context,
                                            call_legacy, case_parts)

TOL = 1e-9


# ---------------------------------------------------------------------------
# parity helpers
# ---------------------------------------------------------------------------
def _parity(key: str, cfg=LegacyCfg, check_expected: bool = True):
    """Compare the new engine against the legacy oracle on FOUR fields.

    `expected` is only an independent sanity check for the default fixture cfg;
    with a different cfg a case may legitimately become a no-exit case, and the
    comparison is still legacy-vs-engine.
    """
    case = CASES[key]
    legacy = call_legacy(case, cfg)
    decision = evaluate_exit(build_context(case, cfg))
    exp = case["expected"]

    if legacy is None:
        assert decision.should_exit is False, \
            f"{key}: engine exited but legacy did not"
        assert decision.exit_reason_code is None, f"{key}: reason must be None"
        assert decision.exit_price is None, f"{key}: price must be None"
        assert decision.fill_model is None, f"{key}: fill_model must be None"
    else:
        assert decision.should_exit is True, f"{key}: legacy exited, engine did not"
        assert decision.exit_reason_code == legacy["reason"], \
            f"{key}: reason {decision.exit_reason_code} != {legacy['reason']}"
        assert abs(decision.exit_price - legacy["exit_price"]) <= TOL, \
            f"{key}: price {decision.exit_price} != {legacy['exit_price']}"
        assert decision.fill_model == legacy["fill_model"], \
            f"{key}: fill {decision.fill_model} != {legacy['fill_model']}"

    if check_expected and exp is not None:
        assert decision.exit_reason_code == exp["reason"], key
        assert abs(decision.exit_price - exp["exit_price"]) <= TOL, key
        assert decision.fill_model == exp["fill_model"], key
    return decision, legacy


# ---------------------------------------------------------------------------
# 1-17: required parity cases
# ---------------------------------------------------------------------------
def test_parity_01_stop_loss_by_open_gap():
    decision, _ = _parity("stop_loss_gap")
    assert decision.fill_model == "GAP"
    assert decision.stop_reference_price == 97.0


def test_parity_02_stop_loss_by_intraday_low():
    decision, _ = _parity("stop_loss_intraday")
    assert decision.fill_model == "STOP"


def test_parity_03_take_profit():
    decision, _ = _parity("take_profit")
    assert decision.target_reference_price == 110.0
    assert decision.fill_model == "TARGET"


def test_parity_04_trailing_stop_by_gap():
    decision, _ = _parity("trailing_gap")
    assert decision.fill_model == "GAP"
    assert decision.stop_reference_price == 108.4      # StopPlan.current_stop_price


def test_parity_05_trailing_stop_intraday():
    decision, _ = _parity("trailing_intraday")
    assert decision.fill_model == "TRAILING"
    assert abs(decision.exit_price - 108.4) <= TOL


def test_parity_06_time_stop():
    decision, _ = _parity("time_stop")
    assert decision.fill_model == "CLOSE"


def test_parity_07_signal_exit():
    decision, _ = _parity("signal_exit")
    assert decision.fill_model == "CLOSE"


def test_parity_08_no_exit():
    _parity("no_exit")


def test_parity_09_stop_and_target_same_bar_stop_wins():
    decision, _ = _parity("stop_and_target_same_bar")
    assert decision.exit_reason_code == "STOP_LOSS"
    assert decision.exit_price == 96.0


def test_parity_10_open_equals_stop():
    decision, _ = _parity("open_equals_stop")
    assert decision.fill_model == "GAP"


def test_parity_11_low_equals_stop():
    decision, _ = _parity("low_equals_stop")
    assert decision.fill_model == "STOP"


def test_parity_12_high_equals_target():
    decision, _ = _parity("high_equals_target")
    assert decision.fill_model == "TARGET"


def test_parity_13_trailing_armed_exactly():
    decision, _ = _parity("trailing_armed_exact")
    assert decision.exit_reason_code == "TRAILING_STOP"
    assert abs(decision.exit_price - 101.4) <= TOL


def test_parity_14_trailing_just_below_threshold():
    _parity("trailing_not_armed")


def test_parity_15_held_days_zero():
    _parity("held_days_zero")


def test_parity_16_held_days_max_minus_one():
    _parity("held_days_max_minus_one")


def test_parity_17_held_days_max():
    decision, _ = _parity("held_days_max")
    assert decision.exit_reason_code == "TIME_STOP"


def test_all_seventeen_cases_present():
    assert len(CASES) == 17, f"expected 17 fixtures, found {len(CASES)}"
    for key, case in CASES.items():
        assert "expected" in case, key
        assert set(case) >= {"pos", "bar", "tech_signal", "held_days"}, key


# ---------------------------------------------------------------------------
# production-config parity (real stop/trailing values: 1.5 / 1.5 / 1.0 / 30)
# ---------------------------------------------------------------------------
def test_parity_with_real_production_config():
    for key in CASES:
        _parity(key, cfg=ProductionConfig, check_expected=False)


# ---------------------------------------------------------------------------
# determinism / isolation / architecture guards
# ---------------------------------------------------------------------------
def test_deterministic_repeated_evaluation():
    def run():
        return [evaluate_exit(build_context(CASES[key])).as_dict()
                for key in CASES]
    assert run() == run() == run()


def test_no_trailing_math_in_exit_engine():
    """Hard rule: the Exit Engine may not compute trailing stops itself."""
    from production.exits import engine, rules
    for module in (rules, engine):
        src = inspect.getsource(module)
        for forbidden in ("atr_at_entry", "trailing_atr_multiple",
                          "stop_atr_multiple", "r_dist", "trail_distance"):
            assert forbidden not in src, (
                f"{module.__name__} contains '{forbidden}' — trailing/ATR "
                "arithmetic belongs to the Stop Engine")


def test_exit_engine_uses_stopplan_levels_only():
    """STOP_LOSS uses initial_stop_price; TRAILING uses current_stop_price."""
    ctx = build_context(CASES["trailing_intraday"])
    assert ctx.stop_plan.current_stop_price == 108.4
    assert ctx.stop_plan.initial_stop_price == 97.0
    assert ctx.trailing_armed is True
    d = evaluate_exit(ctx)
    assert d.exit_price == ctx.stop_plan.current_stop_price


def test_phase3_wiring_safety_import_graph():
    """Phase-3 wiring-safety (supersedes the Phase-2 isolation test).

    Phase 2 asserted "nothing imports the new engines". Phase 3 deliberately
    wires them, so the guarantee is upgraded to:

      1. the PROTECTED modules still import nothing new;
      2. `production/pipeline.py` is the only production module allowed to reach
         the new engines, and only through the Phase-3 adapter/shadow surface
         (never `production.exits.rules`);
      3. the new engines are NOT imported at pipeline module level, so the
         `legacy` import graph and behaviour are unchanged;
      4. the Exit Engine still holds no trailing arithmetic.
    """
    # 1. protected paths stay clean
    guarded = ["production/portfolio/portfolio.py", "production/risk/risk.py",
               "src/state/state.py", "src/agents/risk_manager.py",
               "src/portfolio/portfolio_manager.py"]
    for rel in guarded:
        with open(os.path.join(_REPO_ROOT, rel), encoding="utf-8") as f:
            src = f.read()
        for pkg in ("production.exits", "production.stops",
                    "production.contracts"):
            assert pkg not in src, (
                f"{rel} must not import {pkg} (Phase 3 wiring-safety)")

    # 2/3. pipeline: module level clean, function level adapter/shadow/engine only
    with open(os.path.join(_REPO_ROOT, "production/pipeline.py"),
              encoding="utf-8") as f:
        tree = ast.parse(f.read())

    def _direct_imports(body):
        """Imports that are DIRECT children of `body` (no recursion)."""
        out = set()
        for node in body:
            if isinstance(node, ast.ImportFrom) and node.module:
                out.add(node.module)
            elif isinstance(node, ast.Import):
                out.update(a.name for a in node.names)
        return out

    def _all_imports(node):
        out = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module:
                out.add(sub.module)
            elif isinstance(sub, ast.Import):
                out.update(a.name for a in sub.names)
        return out

    top_level = _direct_imports(tree.body)
    for mod in ("production.exits", "production.stops"):
        assert not any(n == mod or n.startswith(mod + ".") for n in top_level), \
            f"pipeline.py must not import {mod} at module level (legacy graph)"

    func_level = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_level |= _all_imports(node)
    new_engine_imports = {n for n in func_level
                          if n.startswith("production.exits")
                          or n.startswith("production.stops")}
    assert new_engine_imports, "expected the lazy Phase-3 imports"
    for n in sorted(new_engine_imports):
        assert not n.endswith(".rules"), (
            f"pipeline.py must not import {n} — the Exit Engine must be reached "
            "through the Phase-3 adapter/shadow surface")
    assert all(n.split(".")[-1] in ("adapter", "shadow", "engine")
               for n in new_engine_imports), sorted(new_engine_imports)


def test_technicals_agent_never_called_internally():
    """The technical signal is injected; the engine must never call the agent."""
    from production.exits import engine, rules
    for module in (rules, engine):
        src = inspect.getsource(module)
        assert "import technicals_agent" not in src
        assert "technicals_signal(" not in src, \
            f"{module.__name__} must not call technicals_signal"


# ---------------------------------------------------------------------------
# SHORT mirror (contract level only — no legacy oracle exists)
# ---------------------------------------------------------------------------
def _short_context(**over):
    base = dict(entry=100.0, atr=2.0, stop_mult=1.5, anchor=90.0,
                target=80.0, entry_session="2026-09-20",
                bar=dict(open=91.0, high=91.5, low=90.5, close=91.0),
                tech="neutral", session="2026-09-30", max_days=30)
    base.update(over)
    stop = base["entry"] + base["atr"] * base["stop_mult"]
    position = PositionState(
        ticker="S", direction=SHORT, shares=10,
        entry_fill_price=base["entry"], entry_session=base["entry_session"],
        atr_at_entry=base["atr"], initial_stop_price=stop,
        current_stop_price=stop, lowest_price_since_entry=base["anchor"],
        take_profit_price=base["target"]).validate()
    plan = initial_stop_plan(ticker="S", session_date=base["entry_session"],
                             reference_price=base["entry"],
                             reference_price_source="FILL",
                             atr_value=base["atr"],
                             atr_multiple=base["stop_mult"], direction=SHORT)
    upd = update_stop(plan, entry_fill_price=base["entry"],
                      anchor_price=base["anchor"],
                      trailing_atr_multiple=LegacyCfg.trailing_atr_mult,
                      trailing_trigger_r=LegacyCfg.trailing_trigger_r,
                      session_date=base["session"])
    return build_exit_context(
        position=position, stop_plan=upd.plan, bar=PriceBar(**base["bar"]),
        tech_signal=base["tech"], session_date=base["session"],
        max_holding_days=base["max_days"], trailing_armed=upd.evaluation.armed)


def test_short_stop_loss_gap_and_intraday():
    # SHORT stop = 103 ; open 104 gaps through it -> GAP at the open
    d = evaluate_exit(_short_context(bar=dict(open=104.0, high=104.5, low=103.5,
                                              close=104.0)))
    assert d.exit_reason_code == "STOP_LOSS" and d.fill_model == "GAP"
    assert d.exit_price == 104.0
    # open above stop, high breaches it -> STOP at the stop level
    d2 = evaluate_exit(_short_context(bar=dict(open=101.0, high=103.5, low=100.5,
                                               close=102.0)))
    assert d2.exit_reason_code == "STOP_LOSS" and d2.fill_model == "STOP"
    assert d2.exit_price == 103.0


def test_short_take_profit_fills_at_target():
    d = evaluate_exit(_short_context(bar=dict(open=85.0, high=86.0, low=79.0,
                                              close=80.0)))
    assert d.exit_reason_code == "TAKE_PROFIT"
    assert d.fill_model == "TARGET" and d.exit_price == 80.0


def test_short_trailing_mirror():
    # entry 100, atr 2, stop_mult 1.5 -> r_dist 3 ; trigger anchor = 100 - 3 = 97
    # anchor 90 -> armed ; trail = 90 + 0.8*2 = 91.6
    # intraday breach: open BELOW the trail, high reaches it -> TRAILING at 91.6
    d = evaluate_exit(_short_context(anchor=90.0,
                                     bar=dict(open=91.0, high=91.8, low=90.9,
                                              close=91.2)))
    assert d.exit_reason_code == "TRAILING_STOP"
    assert d.fill_model == "TRAILING" and abs(d.exit_price - 91.6) <= TOL
    # gap up through the trailing level -> GAP at the open
    d2 = evaluate_exit(_short_context(anchor=90.0,
                                      bar=dict(open=92.0, high=92.5, low=91.8,
                                               close=92.2)))
    assert d2.exit_reason_code == "TRAILING_STOP" and d2.fill_model == "GAP"
    assert d2.exit_price == 92.0


def test_short_time_and_signal_exit():
    # anchor 98 is NOT armed (trigger anchor = 97), so trailing is skipped and
    # the 30-calendar-day holding limit decides: TIME_STOP at the close.
    d = evaluate_exit(_short_context(anchor=98.0,
                                     bar=dict(open=100.0, high=100.5, low=99.5,
                                              close=100.2),
                                     session="2026-10-20", max_days=30))
    assert d.exit_reason_code == "TIME_STOP"
    assert d.fill_model == "CLOSE" and d.exit_price == 100.2
    d2 = evaluate_exit(_short_context(tech="bearish"))
    assert d2.exit_reason_code == "SIGNAL_EXIT" and d2.fill_model == "CLOSE"
    assert d2.exit_price == 91.0


# ---------------------------------------------------------------------------
# contract guards: EXACT fill semantics (no "reasonable range" rule)
# ---------------------------------------------------------------------------
def test_contract_rejects_inexact_fill_prices():
    bar = PriceBar(open=95.0, high=96.0, low=94.0, close=95.5)

    def mk(**over):
        base = dict(should_exit=True, ticker="T", direction=LONG,
                    session_date="2026-09-30", exit_reason_code="STOP_LOSS",
                    exit_price=95.0, fill_model="GAP",
                    stop_reference_price=97.0, bar=bar)
        base.update(over)
        return ExitDecision(**base)

    mk().validate()                                    # GAP at the open is exact
    # NOTE: {"fill_model": "STOP", "exit_price": 97.0} is VALID by contract —
    # a STOP fill must equal stop_reference_price (97.0). Whether a STOP fill is
    # the *right* rule for a given bar is the engine's decision (and is covered
    # by the parity cases), not a contract-level range check.
    for bad in (dict(exit_price=94.0),                  # != bar.open
                dict(fill_model="STOP", exit_price=96.9),   # != stop ref
                dict(fill_model="CLOSE", exit_price=95.5),  # CLOSE must equal bar.close
                dict(exit_reason_code="TAKE_PROFIT", fill_model="TARGET",
                     exit_price=110.0),                     # missing target ref
                dict(exit_reason_code="TIME_STOP", fill_model="GAP",
                     exit_price=95.0),                      # TIME must use CLOSE
                dict(fill_model="TARGET")):
        try:
            mk(**bad).validate()
        except ContractError:
            continue
        raise AssertionError(f"expected ContractError for {bad}")


def test_hold_decision_carries_no_execution_fields():
    d = evaluate_exit(build_context(CASES["no_exit"]))
    assert d.should_exit is False
    assert d.exit_price is None and d.fill_model is None
    assert d.exit_reason_code is None
    # audit context is still present
    assert d.stop_reference_price is not None
    assert "hold" in d.explain()


def test_reason_and_fill_vocabularies_are_legacy_exact():
    assert EXIT_REASON_CODES == frozenset({"STOP_LOSS", "TAKE_PROFIT",
                                           "TRAILING_STOP", "TIME_STOP",
                                           "SIGNAL_EXIT"})
    assert FILL_MODELS == frozenset({"STOP", "GAP", "TARGET", "TRAILING",
                                     "CLOSE"})


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
