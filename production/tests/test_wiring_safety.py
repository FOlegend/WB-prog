"""
tests/test_wiring_safety.py — Phase 3 wiring-safety suite (spec §16)

The Phase-3 claim under test is narrow and checkable:

    legacy mode  : behaviour byte-identical to pre-Phase-3 (and the new engine
                   packages are not even imported)
    shadow mode  : legacy still decides; the new engine is observed + recorded,
                   with zero side effects
    new mode     : the ExitDecision drives the action — implemented, but never
                   enabled by default

Required cases (15):
 1 default mode = legacy                       9 new mode: no independent trailing
 2 legacy mode output unchanged               10 StopPlan.current_stop_price used
 3 shadow does not alter the legacy decision  11 rollback to legacy works
 4 shadow records agreement                   12 shadow does not mutate state
 5 shadow records divergence                  13 shadow does not persist stops
 6 shadow engine exception -> legacy survives 14 config validation / safe fallback
 7 invalid shadow input -> legacy survives    15 freeze contract validation
 8 new mode consumes ExitDecision
Plus: 16 CLI mode access (`--exit-engine-mode`, approved sign-off item 5 option A).
"""
from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig, resolve_exit_engine_mode
from production.contracts.reason_codes import (DIVERGENCE_TYPES,
                                               DIV_ENGINE_EXCEPTION,
                                               DIV_FILL_MODEL_MISMATCH,
                                               DIV_INPUT_VALIDATION_MISMATCH,
                                               DIV_PRICE_MISMATCH,
                                               DIV_REASON_MISMATCH,
                                               DIV_SHOULD_EXIT_MISMATCH,
                                               DIV_STOP_REFERENCE_MISMATCH,
                                               EXIT_ENGINE_LEGACY,
                                               EXIT_ENGINE_MODES,
                                               EXIT_ENGINE_NEW,
                                               EXIT_ENGINE_SHADOW,
                                               SHADOW_AGREED, SHADOW_ERROR)
from production.datasource import build_cached_source
from production.exits import adapter as adapter_mod
from production.exits import shadow as shadow_mod
from production.exits.shadow import shadow_exit_check, summarise_shadow
from production.pipeline import run_daily
from src.state.state import default_position, default_state

AS_OF = "2025-07-31"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _source():
    return build_cached_source(ProductionConfig())


def _position(entry=180.0, stop=170.0, tp=200.0, atr=2.0, highest=None):
    p = default_position("AAPL", 5, entry, "2024-01-10", atr, stop, tp,
                         "BULL", "wiring-safety test")
    if highest is not None:
        p["highest_since_entry"] = float(highest)
    return p


def _state(pos=None):
    st = default_state(ProductionConfig().capital_usd)
    st["open_positions"].append(pos if pos is not None else _position())
    st["cash"] = 1000.0
    return st


def _run(mode, state=None, cfg=None, source=None):
    cfg = cfg or ProductionConfig(exit_engine_mode=mode)
    return run_daily(AS_OF, state if state is not None else _state(),
                     source or _source(), cfg, screen_mode="provided",
                     candidate_tickers=["MSFT"])


class _CfgView:
    """Duck-typed config (no exit_engine_mode at all) for fallback tests."""
    def __init__(self, base, mode_attr=True, value=None):
        for f in dataclasses.fields(base):
            setattr(self, f.name, getattr(base, f.name))
        if mode_attr:
            self.exit_engine_mode = value
        else:
            del self.exit_engine_mode


# ===========================================================================
# 1. default mode = legacy
# ===========================================================================
def test_1_default_mode_is_legacy():
    cfg = ProductionConfig()
    assert cfg.exit_engine_mode == EXIT_ENGINE_LEGACY
    assert EXIT_ENGINE_LEGACY in EXIT_ENGINE_MODES
    assert resolve_exit_engine_mode(cfg) == EXIT_ENGINE_LEGACY
    # the flag must be the ONLY new production knob: nothing else moved
    frozen = {"stop_atr_mult": 1.5, "take_profit_atr_mult": 2.5,
              "trailing_atr_mult": 1.5, "trailing_trigger_r": 1.0,
              "max_holding_days": 30, "risk_per_trade": 0.01,
              "max_open_positions": 5, "max_position_pct": 0.25,
              "setup_score_threshold": 0.5, "screener_top_n": 30}
    for k, v in frozen.items():
        assert getattr(cfg, k) == v, f"{k} changed ({getattr(cfg, k)} != {v})"


# ===========================================================================
# 2. legacy mode output unchanged (incl. "new engines not even imported")
# ===========================================================================
def test_2_legacy_mode_output_unchanged():
    rec = _run(EXIT_ENGINE_LEGACY)
    assert rec["exits"]["mode"] == EXIT_ENGINE_LEGACY
    assert rec["exits"]["shadow"] == []
    assert rec["exits"]["shadow_summary"] is None
    assert rec["pipeline_status"] == "OK"
    assert not [w for w in rec["warnings"] if "SHADOW" in w]
    # legacy decision still produced by the oracle
    assert rec["exits"]["proposed"], "expected the legacy oracle to propose an exit"


def test_2b_legacy_mode_imports_no_new_engine_module():
    """Strongest form of 'no behaviour change': a fresh process running a full
    legacy decision must never load the new engine packages."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, {_REPO_ROOT!r})\n"
        "from production.config import ProductionConfig\n"
        "from production.datasource import build_cached_source\n"
        "from production.pipeline import run_daily\n"
        "from src.state.state import default_state, default_position\n"
        "cfg = ProductionConfig()\n"
        "src = build_cached_source(cfg)\n"
        "st = default_state(cfg.capital_usd)\n"
        "st['open_positions'].append(default_position('AAPL', 5, 180.0, "
        "'2024-01-10', 2.0, 170.0, 200.0, 'BULL', 't'))\n"
        "rec = run_daily('2025-07-31', st, src, cfg, screen_mode='provided', "
        "candidate_tickers=['MSFT'])\n"
        "assert rec['exits']['mode'] == 'legacy'\n"
        "bad = sorted(m for m in sys.modules if m.startswith('production.exits') "
        "or m.startswith('production.stops'))\n"
        "print('IMPORTED[' + ','.join(bad) + ']')\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    marker = [ln for ln in out.stdout.splitlines() if ln.startswith("IMPORTED[")]
    assert marker, out.stdout[-2000:]
    assert marker[0] == "IMPORTED[]", (
        f"legacy mode loaded new engine modules: {marker[0]}")


# ===========================================================================
# 3. shadow mode does not alter the legacy decision
# ===========================================================================
def test_3_shadow_does_not_alter_legacy_decision():
    state = _state()
    legacy = _run(EXIT_ENGINE_LEGACY, state=_state())
    shadow = _run(EXIT_ENGINE_SHADOW, state=state)
    for section in ("proposed", "status", "warnings"):
        assert legacy["exits"][section] == shadow["exits"][section], section
    assert legacy["entries"]["proposed"] == shadow["entries"]["proposed"]
    assert legacy["positions"] == shadow["positions"]
    assert legacy["recommendations"] == shadow["recommendations"]
    assert legacy["pipeline_status"] == shadow["pipeline_status"]
    assert legacy["warnings"] == shadow["warnings"]
    assert shadow["exits"]["shadow"], "shadow mode must record something"


# ===========================================================================
# 4. shadow records agreement
# ===========================================================================
def test_4_shadow_records_agreement():
    rec = _run(EXIT_ENGINE_SHADOW)
    assert rec["exits"]["mode"] == EXIT_ENGINE_SHADOW
    assert len(rec["exits"]["shadow"]) == 1
    s = rec["exits"]["shadow"][0]
    assert s["ticker"] == "AAPL"
    assert s["evaluated"] is True
    assert s["status"] == SHADOW_AGREED
    assert s["agree"] is True
    assert s["divergence_types"] == []
    # both sides are recorded in the legacy-compatible vocabulary
    assert set(s["legacy"]) == {"reason", "exit_price", "fill_model"}
    assert s["legacy"]["reason"] == s["new"]["reason"]
    assert s["legacy"]["exit_price"] == s["new"]["exit_price"]
    assert s["legacy"]["fill_model"] == s["new"]["fill_model"]
    # the Stop Engine's levels travel with the record
    assert {"initial_stop_price", "current_stop_price", "armed",
            "reason_code"} <= set(s["stop_plan"])
    summary = rec["exits"]["shadow_summary"]
    assert summary == {"enabled": True, "n_evaluated": 1, "n_agree": 1,
                       "n_diverged": 0, "n_error": 0, "divergence_types": {}}
    assert json.loads(json.dumps(rec))["exits"]["shadow"] == [s]
    json.dumps(rec)


# ===========================================================================
# 5. shadow records divergence (classified, never auto-judged harmless)
# ===========================================================================
def test_5_shadow_records_divergence():
    # ---- (a) pipeline level: an injected HOLD engine diverges (legacy exits) ----
    from production.exits.engine import hold_decision
    original = shadow_mod.evaluate_exit
    shadow_mod.evaluate_exit = lambda ctx: hold_decision(ctx)
    try:
        rec = _run(EXIT_ENGINE_SHADOW)
    finally:
        shadow_mod.evaluate_exit = original

    s = rec["exits"]["shadow"][0]
    assert s["agree"] is False
    assert s["status"] != SHADOW_AGREED
    assert s["divergence_types"] == [DIV_SHOULD_EXIT_MISMATCH]
    assert set(s["divergence_types"]) <= DIVERGENCE_TYPES
    # ...and the legacy decision was untouched by the divergence
    legacy = _run(EXIT_ENGINE_LEGACY)
    assert rec["exits"]["proposed"] == legacy["exits"]["proposed"]
    assert rec["exits"]["shadow_summary"]["n_diverged"] == 1
    assert rec["exits"]["shadow_summary"]["divergence_types"] == {
        DIV_SHOULD_EXIT_MISMATCH: 1}

    # ---- (b) comparator level: field-by-field classification (both deterministic) ----
    pos = _position(entry=180.0, stop=170.0, tp=250.0, atr=2.0, highest=200.0)
    bar = {"open": 198.0, "high": 199.0, "low": 196.0, "close": 197.5}
    cfg = ProductionConfig(exit_engine_mode=EXIT_ENGINE_SHADOW)
    wrong_legacy = {"reason": "STOP_LOSS", "exit_price": 100.0,
                    "fill_model": "STOP"}
    r = shadow_exit_check(pos, bar, "neutral", AS_OF, wrong_legacy, cfg)
    assert r.new["reason"] == "TRAILING_STOP"          # the real engine
    assert set(r.divergence_types) == {
        DIV_REASON_MISMATCH, DIV_PRICE_MISMATCH, DIV_FILL_MODEL_MISMATCH,
        DIV_STOP_REFERENCE_MISMATCH}
    assert r.stop_plan["current_stop_price"] == 197.0


# ===========================================================================
# 6. shadow engine exception does not break legacy (fail-open)
# ===========================================================================
def test_6_shadow_engine_exception_does_not_break_legacy():
    original = shadow_mod.evaluate_exit
    shadow_mod.evaluate_exit = lambda ctx: (_ for _ in ()).throw(
        RuntimeError("boom"))
    try:
        rec = _run(EXIT_ENGINE_SHADOW)
    finally:
        shadow_mod.evaluate_exit = original

    legacy = _run(EXIT_ENGINE_LEGACY)
    assert rec["exits"]["proposed"] == legacy["exits"]["proposed"]
    assert rec["pipeline_status"] == "OK"
    s = rec["exits"]["shadow"][0]
    assert s["status"] == SHADOW_ERROR
    assert s["agree"] is None                        # unknown, not "agreed"
    assert DIV_ENGINE_EXCEPTION in s["divergence_types"]
    assert s["error_type"] == "RuntimeError" and "boom" in s["error"]
    assert rec["exits"]["shadow_summary"]["n_error"] == 1


# ===========================================================================
# 7. invalid shadow input does not break legacy
# ===========================================================================
def test_7_invalid_shadow_input_does_not_break_legacy():
    # a LONG position whose recorded stop sits ABOVE the entry is contract-invalid
    bad = _position(entry=180.0, stop=190.0, tp=200.0, atr=2.0)
    rec = _run(EXIT_ENGINE_SHADOW, state=_state(pos=bad))
    s = rec["exits"]["shadow"][0]
    assert s["status"] == SHADOW_ERROR
    assert DIV_INPUT_VALIDATION_MISMATCH in s["divergence_types"]
    assert s["agree"] is None
    # legacy still evaluated the position and still decides
    legacy = _run(EXIT_ENGINE_LEGACY, state=_state(pos=bad))
    assert rec["exits"]["proposed"] == legacy["exits"]["proposed"]
    assert rec["pipeline_status"] == legacy["pipeline_status"]


# ===========================================================================
# 8. new mode consumes the ExitDecision
# ===========================================================================
def test_8_new_mode_consumes_exit_decision():
    rec = _run(EXIT_ENGINE_NEW)
    assert rec["exits"]["mode"] == EXIT_ENGINE_NEW
    assert rec["exits"]["shadow"] == []              # shadow is shadow-only
    proposed = rec["exits"]["proposed"]
    assert proposed and proposed[0]["engine"] == "exit_engine_v1"
    assert "stop_plan" in proposed[0]

    # proof the decision really comes from the new engine: force it to HOLD and
    # the proposal must disappear even though legacy still triggers an exit
    imported = sys.modules["production.exits.engine"]
    original = imported.evaluate_exit
    hold = lambda ctx: imported.hold_decision(ctx)
    imported.evaluate_exit = hold
    try:
        rec_hold = _run(EXIT_ENGINE_NEW)
    finally:
        imported.evaluate_exit = original
    assert rec_hold["exits"]["proposed"] == []
    assert _run(EXIT_ENGINE_LEGACY)["exits"]["proposed"] != []


# ===========================================================================
# 9. new mode / adapter do not independently calculate trailing
# ===========================================================================
def test_9_no_independent_trailing_calculation():
    from production.exits import engine, rules
    for module in (rules, engine, shadow_mod, adapter_mod):
        src = inspect.getsource(module)
        for forbidden in ("trailing_atr_mult *", "r_dist", "trail_distance",
                          "* atr_at_entry", "atr_at_entry *"):
            assert forbidden not in src, (
                f"{module.__name__} computes trailing/ATR arithmetic "
                f"('{forbidden}') — the Stop Engine is the only owner")
    # the adapter must go THROUGH the Stop Engine
    src = inspect.getsource(adapter_mod)
    assert "plan_from_position(" in src and "update_stop(" in src


# ===========================================================================
# 10. StopPlan.current_stop_price is the level that is used
# ===========================================================================
def test_10_current_stop_price_is_used():
    pos = _position(entry=180.0, stop=170.0, tp=250.0, atr=2.0, highest=200.0)
    bar = {"open": 198.0, "high": 199.0, "low": 196.0, "close": 197.5}
    cfg = ProductionConfig(exit_engine_mode=EXIT_ENGINE_SHADOW)

    # expected level computed HERE (the test may know the formula; the engine
    # must not): armed anchor 200 - trailing 1.5 x atr 2.0
    expected = 200.0 - cfg.trailing_atr_mult * 2.0        # == 197.0

    legacy = {"reason": "TRAILING_STOP", "exit_price": expected,
              "fill_model": "TRAILING"}
    r = shadow_exit_check(pos, bar, "neutral", AS_OF, legacy, cfg)
    assert r.agree is True, r.as_dict()
    assert r.stop_plan["current_stop_price"] == expected
    assert r.stop_plan["initial_stop_price"] == 170.0
    assert r.stop_plan["armed"] is True
    assert r.new["stop_reference_price"] == expected

    # and the contract the engine saw carried that level (not a recomputation)
    adapted = adapter_mod.adapt_exit_inputs(pos, bar, "neutral", AS_OF, cfg)
    assert adapted.context.stop_plan.current_stop_price == expected
    assert adapted.context.trailing_armed is True
    assert adapted.context.stop_plan.initial_stop_price == 170.0


# ===========================================================================
# 11. rollback to legacy works
# ===========================================================================
def test_11_rollback_to_legacy_works():
    baseline = _run(EXIT_ENGINE_LEGACY)
    cfg = ProductionConfig(exit_engine_mode=EXIT_ENGINE_NEW)
    assert resolve_exit_engine_mode(cfg) == EXIT_ENGINE_NEW
    cfg.exit_engine_mode = EXIT_ENGINE_LEGACY           # flip the flag back
    rolled = _run("ignored", cfg=cfg)
    assert rolled["exits"]["mode"] == EXIT_ENGINE_LEGACY
    assert rolled["exits"]["proposed"] == baseline["exits"]["proposed"]
    assert rolled["exits"]["shadow"] == []
    # no code change, no revert needed — the flag alone restores behaviour
    assert rolled == baseline


# ===========================================================================
# 12. shadow does not mutate position / state
# ===========================================================================
def test_12_shadow_does_not_mutate_state():
    state = _state()
    before = copy.deepcopy(state)
    rec = _run(EXIT_ENGINE_SHADOW, state=state)
    assert state == before, "run_daily must never mutate the caller's state"
    assert "current_stop_price" not in state["open_positions"][0]
    assert rec["positions"]["items"][0]["stop_price"] == 170.0


def test_12b_adapter_does_not_mutate_its_inputs():
    pos = _position(highest=200.0)
    bar = {"open": 198.0, "high": 199.0, "low": 196.0, "close": 197.5}
    pos_before, bar_before = copy.deepcopy(pos), copy.deepcopy(bar)
    adapter_mod.adapt_exit_inputs(pos, bar, "neutral", AS_OF, ProductionConfig())
    assert pos == pos_before and bar == bar_before


# ===========================================================================
# 13. shadow does not persist current_stop_price
# ===========================================================================
def test_13_current_stop_price_not_persisted():
    rec = _run(EXIT_ENGINE_SHADOW)
    assert rec["exits"]["shadow"][0]["stop_plan"]["current_stop_price"] is not None
    # nothing anywhere in the record writes it back into a position
    for item in rec["positions"]["items"]:
        assert "current_stop_price" not in item
    # state schema untouched
    with open(os.path.join(_REPO_ROOT, "src/state/state.py"),
              encoding="utf-8") as f:
        src = f.read()
    assert "current_stop_price" not in src
    assert "stop_updated_session" not in src


# ===========================================================================
# 14. config validation / safe fallback
# ===========================================================================
def test_14_config_validation_and_safe_fallback():
    base = ProductionConfig()
    for bad in ("", "LEGACYISH", "new-ish", 123, None, ["legacy"], "legacyy"):
        assert resolve_exit_engine_mode(_CfgView(base, value=bad)) == \
            EXIT_ENGINE_LEGACY, bad
    # case/whitespace variants are normalised, not rejected
    for variant, expect in ((" SHADOW ", EXIT_ENGINE_SHADOW),
                            ("Legacy", EXIT_ENGINE_LEGACY),
                            ("NEW", EXIT_ENGINE_NEW)):
        assert resolve_exit_engine_mode(_CfgView(base, value=variant)) == expect
    # missing attribute entirely
    assert resolve_exit_engine_mode(_CfgView(base, mode_attr=False)) == \
        EXIT_ENGINE_LEGACY
    assert resolve_exit_engine_mode(object()) == EXIT_ENGINE_LEGACY

    invalid = ProductionConfig(exit_engine_mode="garbage")
    assert invalid.exit_engine_mode == EXIT_ENGINE_LEGACY
    assert invalid.config_warnings
    rec = _run("x", cfg=invalid)
    assert rec["exits"]["mode"] == EXIT_ENGINE_LEGACY
    assert any(w.startswith("CONFIG_WARNING:") for w in rec["warnings"])
    # a valid non-default mode is honoured (never forced)
    assert ProductionConfig(exit_engine_mode=EXIT_ENGINE_NEW).exit_engine_mode \
        == EXIT_ENGINE_NEW
    assert resolve_exit_engine_mode(_CfgView(base, value=EXIT_ENGINE_SHADOW)) \
        == EXIT_ENGINE_SHADOW


# ===========================================================================
# 16. CLI mode access (approved Phase-3 sign-off item 5, option A)
# ===========================================================================
def _cli_mode(argv: list[str]) -> dict:
    """Exercise production/main.py's argument parsing + cfg construction in a
    subprocess with the pipeline stubbed out (no run, no files written)."""
    code = (
        "import sys, json\n"
        f"sys.path.insert(0, {_REPO_ROOT!r})\n"
        "import production.main as m\n"
        "captured = {}\n"
        "def fake(cfg, **kw):\n"
        "    captured['mode'] = cfg.exit_engine_mode\n"
        "    return {}\n"
        "m.run_daily = fake\n"
        f"sys.argv = ['main.py'] + {argv!r}\n"
        "m.main()\n"
        "print('RESULT=' + json.dumps(captured))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, cwd=_REPO_ROOT)
    return {"rc": out.returncode, "stdout": out.stdout, "stderr": out.stderr}


def test_16_cli_exit_engine_mode_flag():
    # default (flag absent) -> legacy
    r = _cli_mode(["--cached"])
    assert r["rc"] == 0, r["stderr"][-800:]
    assert '"mode": "legacy"' in r["stdout"], r["stdout"]
    # every allowed value is honoured
    for mode in (EXIT_ENGINE_SHADOW, EXIT_ENGINE_NEW, EXIT_ENGINE_LEGACY):
        r = _cli_mode(["--cached", "--exit-engine-mode", mode])
        assert r["rc"] == 0, r["stderr"][-800:]
        assert f'"mode": "{mode}"' in r["stdout"], (mode, r["stdout"])
    # an unknown value is rejected at the CLI boundary (argparse -> exit 2)
    r = _cli_mode(["--cached", "--exit-engine-mode", "bogus"])
    assert r["rc"] == 2, r
    assert "invalid choice" in r["stderr"]
    # the flag is documented and offers exactly the three modes
    out = subprocess.run([sys.executable, "production/main.py", "--help"],
                         capture_output=True, text=True, timeout=120,
                         cwd=_REPO_ROOT)
    assert out.returncode == 0
    assert "--exit-engine-mode {legacy,new,shadow}" in out.stdout


# ===========================================================================
# 15. freeze contract validation
# ===========================================================================
def test_15_freeze_contract_document():
    path = os.path.join(_REPO_ROOT, "production/STOP_EXIT_V1_FREEZE.md")
    assert os.path.exists(path), "production/STOP_EXIT_V1_FREEZE.md missing"
    with open(path, encoding="utf-8") as f:
        doc = f.read()
    for required in ("Contracts", "Stop semantics", "Exit semantics",
                     "Production boundary", "Persistence boundary",
                     "STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "TIME_STOP",
                     "SIGNAL_EXIT", "initial_stop_price", "current_stop_price",
                     "calendar", "legacy"):
        assert required in doc, f"freeze document is missing '{required}'"
    assert "not persisted" in doc.lower()
    # the documented priority order must match the code
    from production.exits.rules import RULES
    order = [f.__name__ for f in RULES]
    assert order == ["rule_stop_loss", "rule_take_profit", "rule_trailing_stop",
                     "rule_time_stop", "rule_signal_exit"]
    # the freeze doc lists the same five reasons in the same order
    positions = [doc.index(code) for code in
                 ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "TIME_STOP",
                  "SIGNAL_EXIT")]
    assert positions == sorted(positions), "freeze doc priority order drifted"


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
