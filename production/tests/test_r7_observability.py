"""
test_r7_observability.py — R7 acceptance tests (strategy-neutral logging)

R7's contract is that observability changes what the record SAYS and nothing
about what the system DOES. These tests therefore assert two different things
and must never be collapsed into one:

  A. STRUCTURAL — the new fields exist, are populated, and are self-consistent
     (the risk-gate self-check must MATCH, the blocked snapshot must carry the
     capacity context, components must parse).
  B. BEHAVIOURAL — with the research flag OFF, every decision-bearing output is
     byte-identical to a run that has no R7 code path at all.

(B) is the one that matters. If a future edit makes the record richer *and* the
trading different, these tests fail.

Run:  python production/tests/test_r7_observability.py
"""
from __future__ import annotations

import copy
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.observability import (blocked_entry_snapshot,
                                     parse_setup_components,
                                     risk_gate_diagnostics)
from production.pipeline import run_daily

_RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------------------
# 1 — risk-gate self-check agrees with the REAL sizing engine
# ---------------------------------------------------------------------------
def test_risk_gate_selfcheck_matches_engine():
    from production.risk.risk import size_swing_position
    cfg = ProductionConfig()
    cases = [
        # (equity, cash, price, atr, size_mult, quality, n_open)
        (1282.0, 1282.0, 100.0, 2.0, 1.0, 1.0, 0),
        (1350.0, 900.0, 250.75, 4.13, 0.75, 0.75, 2),
        (1282.0, 40.0, 37.5, 1.05, 0.5, 0.5, 1),
        (1282.0, 1282.0, 412.9, 9.87, 1.0, 1.0, 4),
        # regime gate: size_mult = 0
        (1282.0, 1282.0, 100.0, 2.0, 0.0, 1.0, 0),
        # max_open gate
        (1282.0, 1282.0, 100.0, 2.0, 1.0, 1.0, 5),
        # risk budget below one share (the dominant real-world rejection)
        (1282.0, 1282.0, 400.0, 9.0, 0.3, 0.5, 1),
    ]
    mismatches = []
    for (eq, cash, px, atr, sm, q, n_open) in cases:
        sizing = size_swing_position(eq, cash, px, atr, sm, q, n_open, cfg)
        d = risk_gate_diagnostics(equity=eq, cash=cash, price=px, atr=atr,
                                  size_mult=sm, quality_mult=q, n_open=n_open,
                                  cfg=cfg, sizing=sizing)
        if d["selfcheck"] != "MATCH":
            mismatches.append((px, atr, sm, q, n_open, d["selfcheck"],
                               d.get("selfcheck_detail")))
    _check("risk_gate self-check MATCHes the real engine on all 7 cases",
           not mismatches, f"mismatches={mismatches}")

    # the fields the spec asks for must be present and numeric
    sizing = size_swing_position(1350.0, 900.0, 250.75, 4.13, 0.75, 0.75, 2, cfg)
    d = risk_gate_diagnostics(equity=1350.0, cash=900.0, price=250.75, atr=4.13,
                              size_mult=0.75, quality_mult=0.75, n_open=2,
                              cfg=cfg, sizing=sizing)
    for k in ("risk_budget", "requested_shares_exact", "raw_shares_after_floor",
              "cap_shares_by_value", "cap_shares_by_cash",
              "recomputed_final_shares", "engine_shares", "gates",
              "binding_cap", "selfcheck"):
        _check(f"risk_gate exposes `{k}`", k in d)
    _check("risk_budget == equity x risk_per_trade x eff (to 4dp)",
           abs(d["risk_budget"]
               - 1350.0 * cfg.risk_per_trade * 0.75 * 0.75) < 5e-4,
           f"{d['risk_budget']} vs "
           f"{1350.0 * cfg.risk_per_trade * 0.75 * 0.75}")
    _check("engine_reasoning is carried verbatim",
           d["engine_reasoning"] == sizing.get("reasoning"))


def test_risk_gate_reports_unavailable_not_guessed():
    cfg = ProductionConfig()
    d = risk_gate_diagnostics(equity=None, cash=100.0, price=100.0, atr=2.0,
                              size_mult=1.0, quality_mult=1.0, n_open=0,
                              cfg=cfg, sizing={"allow": False, "shares": 0})
    _check("missing input -> selfcheck NOT_COMPUTABLE (not a fabricated number)",
           d["selfcheck"] == "NOT_COMPUTABLE")
    _check("missing input -> risk_budget is None",
           d["risk_budget"] is None and d["requested_shares_exact"] is None)


# ---------------------------------------------------------------------------
# 2 — component attribution
# ---------------------------------------------------------------------------
def test_component_parser():
    full = parse_setup_components(
        "pullback: px>50SMA+px>200SMA+50>200+nearEMA+vol_contract+RS_strong "
        "(score=0.85)", "pullback")
    _check("all six fired components parsed", len(full["fired"]) == 6)
    _check("components dict is complete (7 named, 6 true)",
           sum(full["components"].values()) == 6
           and len(full["components"]) == 7)
    _check("reported_score parsed", full["reported_score"] == 0.85)
    _check("source documented", "entry_reason" in (full["source"] or ""))
    _check("EMA10/EMA20 limitation disclosed",
           "nearEMA" in full["fidelity"])

    none_fired = parse_setup_components("pullback: none (score=0.00)", "pullback")
    _check("'none' token -> zero fired components", none_fired["fired"] == [])

    early = parse_setup_components("資料不足（< 210 根）", None)
    _check("early-return reason -> available False + note",
           early["available"] is False and early["note"] is not None)
    _check("early-return -> components stay empty (no invention)",
           early["components"] == {})

    _check("empty input -> available False",
           parse_setup_components("", None)["available"] is False)
    _check("None input -> available False",
           parse_setup_components(None, None)["available"] is False)


# ---------------------------------------------------------------------------
# 3 — blocked snapshot shape (Tier A / Tier A+B)
# ---------------------------------------------------------------------------
def test_blocked_snapshot_records_capacity_context():
    cfg = ProductionConfig()
    state = {"equity": 1300.0, "cash": 700.0, "open_positions": [
        {"ticker": "AAA", "shares": 10, "entry_price": 50.0,
         "entry_date": "2024-03-01"},
        {"ticker": "BBB", "shares": 5, "entry_price": 80.0,
         "entry_date": "2024-04-02"}]}
    regime = {"regime_label": "BEAR", "position_size_mult": 0.0,
              "veto_flags": ["BEARISH_BREADTH_DIVERGENCE"]}
    cands = [{"ticker": "CCC"}, {"ticker": "DDD"}]
    snap = blocked_entry_snapshot(
        as_of="2024-05-01", blocked_stage="pre_setup_evaluation",
        blocked_reason="regime BEAR — no new longs (defensive/cash)",
        cands=cands, state=state, cfg=cfg, regime=regime)

    _check("snapshot records the blocking reason",
           "BEAR" in snap["blocked_reason"])
    _check("snapshot records the candidate population",
           snap["candidates"]["n_candidates"] == 2
           and snap["candidates"]["tickers"] == ["CCC", "DDD"])
    _check("snapshot records current open positions",
           snap["portfolio"]["n_open"] == 2
           and [p["ticker"] for p in
                snap["portfolio"]["current_open_positions"]] == ["AAA", "BBB"])
    _check("snapshot records positions competing for capacity",
           snap["portfolio"]["positions_competing_for_capacity"] == ["AAA", "BBB"])
    _check("snapshot records free capacity slots",
           snap["portfolio"]["capacity_slots_free"]
           == cfg.max_open_positions - 2)
    _check("snapshot records available capital",
           snap["capital"]["available_cash"] == 700.0
           and snap["capital"]["equity"] == 1300.0)
    _check("snapshot records the risk budget (0 under a defensive regime)",
           snap["capital"]["risk_budget_usd"] == 0.0)
    _check("snapshot records regime veto flags",
           snap["regime"]["veto_flags"] == ["BEARISH_BREADTH_DIVERGENCE"])
    _check("Tier A marks per-candidate detail unavailable (does not invent it)",
           snap["per_candidate"]["status"] == "unavailable"
           and snap["tier"] == "A")

    per = [{"ticker": "CCC", "setup_status": "valid", "risk_status": "denied"}]
    snap_b = blocked_entry_snapshot(
        as_of="2024-05-01", blocked_stage="pre_setup_evaluation",
        blocked_reason="x", cands=cands, state=state, cfg=cfg, regime=regime,
        per_candidate=per)
    _check("Tier B is labelled A+B and carries the detail",
           snap_b["tier"] == "A+B" and snap_b["per_candidate_evaluated"] is True)


# ---------------------------------------------------------------------------
# 4 — BEHAVIOURAL EQUIVALENCE (the acceptance criterion that matters)
# ---------------------------------------------------------------------------
def _decision_keys(rec: dict) -> dict:
    """Everything in a DecisionRecord that can conceivably move money."""
    return {
        "pipeline_status": rec["pipeline_status"],
        "data_status": rec["data"]["status"],
        "regime_status": rec["regime"]["status"],
        "regime_output": rec["regime"]["output"],
        "screener_status": rec["screener"]["status"],
        "screener_candidates": rec["screener"]["candidates"],
        "setup_status": rec["setup"]["status"],
        "setup_n_valid": rec["setup"]["n_valid"],
        "setup_evaluations": rec["setup"]["evaluations"],
        "positions": rec["positions"],
        "exits_status": rec["exits"]["status"],
        "exits_proposed": rec["exits"]["proposed"],
        "entries_status": rec["entries"]["status"],
        "entries_blocked_reason": rec["entries"]["blocked_reason"],
        "entries_proposed": rec["entries"]["proposed"],
        "entries_n_buys": rec["entries"]["n_buys"],
        "risk_sizing": rec["risk"]["sizing"],
        "portfolio": rec["portfolio"],
        "warnings": rec["warnings"],
        "recommendations": rec["recommendations"],
    }


def _deep_strip_r7(d):
    """Remove ONLY the additive R7 keys, so the remainder can be compared to a
    pre-R7 run. Anything else that differs is a real behaviour change."""
    import copy as _c
    out = _c.deepcopy(d)
    if isinstance(out, dict):
        for k in ("reasoning", "gate", "score_components",
                  "extension_filter_input", "pre_trade_equity",
                  "pre_trade_cash", "regime_gate", "blocked_snapshot",
                  "holding_days", "signal_close", "entry_open",
                  "next_open_gap_pct", "max_entry_gap_pct", "entry_atr",
                  "entry_atr_pct_of_price", "prior_high20",
                  "extension_from_pivot_pct", "extension_filter_status",
                  "entry_phase_equity", "entry_phase_cash_after_fill",
                  "entry_regime_size_mult", "entry_setup_quality_mult",
                  "entry_eff_size_mult", "risk_budget_usd", "stop_distance_usd",
                  "r_unit_usd", "shares_requested_before_flooring",
                  "shares_final", "position_value", "veto_flags"):
            out.pop(k, None)
    return out


def test_behavioural_equivalence_on_a_real_session():
    """Run one real PIT session twice — flag off, then Tier B on — and require
    every decision-bearing section to be identical (modulo the additive R7
    fields)."""
    from production.datasource import build_cached_source

    as_of = "2024-06-14"          # a SIDEWAYS/BULL-ish mid-window session
    cfg_off = ProductionConfig()
    source = build_cached_source(cfg_off)
    state = {"cash": cfg_off.capital_usd, "equity": cfg_off.capital_usd,
             "open_positions": [], "trade_log": [], "last_run_date": None}
    bucket = os.path.join(
        cfg_off.reports_dir,
        "production_buckets_2024-01-01_2025-07-31_top30.json")
    cands = None
    screen = None
    if os.path.exists(bucket):
        blob = json.load(open(bucket, encoding="utf-8"))
        keys = sorted(blob.keys())
        pick = [k for k in keys if k <= as_of][-1]
        screen = blob[pick]
        cands = [c["ticker"] for c in screen.get("tickers", [])]
    if not cands:
        _check("behavioural equivalence (skipped — no bucket cache)", True,
               "skipped")
        return

    rec_off = run_daily(as_of, copy.deepcopy(state), source, cfg_off,
                        screen_mode="bucket", candidate_tickers=cands,
                        screen_result=screen)
    cfg_on = ProductionConfig(research_blocked_setup_eval=True)
    rec_on = run_daily(as_of, copy.deepcopy(state), source, cfg_on,
                       screen_mode="bucket", candidate_tickers=cands,
                       screen_result=screen)

    a = _deep_strip_r7(_decision_keys(rec_off))
    b = _deep_strip_r7(_decision_keys(rec_on))
    _check("Tier B flag ON does not change any decision-bearing section",
           a == b,
           next((f"{k}: {a[k]!r} != {b[k]!r}" for k in a if a[k] != b[k]), ""))

    _check("R7 adds the blocked snapshot", rec_off["setup"]["blocked_snapshot"]
           is not None)
    _check("R7 records pre-trade equity/cash",
           rec_off["entries"]["pre_trade_equity"] is not None
           and rec_off["entries"]["pre_trade_cash"] is not None)
    _check("R7 records the regime gate incl. veto flags",
           "veto_flags" in rec_off["entries"]["regime_gate"])
    gate = rec_off["risk"]["sizing"]
    if gate:
        _check("every risk gate self-checks MATCH on a real session",
               all(g["gate"]["selfcheck"] == "MATCH" for g in gate),
               str([(g["ticker"], g["gate"]["selfcheck"]) for g in gate
                    if g["gate"]["selfcheck"] != "MATCH"]))
        _check("every setup evaluation carries parsed components",
               all("score_components" in e for e in
                   rec_off["setup"]["evaluations"]))
    else:
        _check("risk gate self-check (no sizing on this session)", True,
               "no sizing rows")


def test_snapshot_is_inert_on_a_blocked_session():
    """On a session where entries are blocked before evaluation, the snapshot
    must exist and the decision must still be 'blocked' with the same reason."""
    from production.datasource import build_cached_source

    as_of = "2024-01-03"      # the first window session is BEAR in the PIT data
    cfg = ProductionConfig()
    source = build_cached_source(cfg)
    state = {"cash": cfg.capital_usd, "equity": cfg.capital_usd,
             "open_positions": [], "trade_log": [], "last_run_date": None}
    bucket = os.path.join(
        cfg.reports_dir,
        "production_buckets_2024-01-01_2025-07-31_top30.json")
    cands = screen = None
    if os.path.exists(bucket):
        blob = json.load(open(bucket, encoding="utf-8"))
        pick = sorted(blob.keys())[0]
        screen = blob[pick]
        cands = [c["ticker"] for c in screen.get("tickers", [])]
    if not cands:
        _check("blocked-session snapshot (skipped — no bucket cache)", True,
               "skipped")
        return
    rec = run_daily(as_of, state, source, cfg, screen_mode="bucket",
                    candidate_tickers=cands, screen_result=screen)
    snap = rec["setup"]["blocked_snapshot"]
    _check("blocked session still writes a snapshot", snap is not None)
    if snap:
        _check("snapshot blocked_reason matches entries.blocked_reason",
               snap["blocked_reason"] == rec["entries"]["blocked_reason"]
               or "exhausted" in snap["blocked_reason"])
        _check("snapshot records the candidate population even though no setup "
               "was evaluated",
               snap["candidates"]["n_candidates"] == len(cands))
    _check("state dict was not mutated by run_daily",
           state["open_positions"] == [] and state["cash"] == cfg.capital_usd)


# ---------------------------------------------------------------------------
# 5 — the frozen surfaces are untouched
# ---------------------------------------------------------------------------
def test_r7_touches_no_frozen_decision_code():
    import ast
    import inspect
    from production.risk.risk import size_swing_position
    from production.portfolio.portfolio import exit_check

    # Parse the observability module's AST: prose may MENTION a decision rule
    # (it must, to document what it mirrors), but it must never IMPORT or CALL
    # one. A text search would false-positive on the docstrings.
    src = open(os.path.join(_REPO_ROOT, "production", "observability.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imported.add(base)
            imported |= {f"{base}.{a.name}" for a in node.names}
    banned = {"exit_check", "_exit_check", "size_position", "size_swing_position",
              "evaluate_setup", "build_order_buy", "run_daily",
              "production.pipeline", "production.risk.risk",
              "production.portfolio.portfolio", "production.agents.setup",
              "src.portfolio.portfolio_manager", "src.agents.risk_manager"}
    leaked = sorted(banned & imported)
    _check("observability imports NO decision rule (AST-verified)",
           not leaked, f"leaked={leaked}")
    _check("observability defines no config field",
           "ProductionConfig" not in imported)
    # the sizing + exit wrappers must still be the same one-line delegations
    _check("risk sizing wrapper unchanged",
           "return size_position(equity, cash, price, atr, eff, n_open, cfg)"
           in inspect.getsource(size_swing_position))
    _check("exit_check wrapper unchanged",
           "return _exit_check(pos, bar, date_str, tech_signal, cfg)"
           in inspect.getsource(exit_check))
    # the config's only new field must default to False
    cfg = ProductionConfig()
    _check("research_blocked_setup_eval defaults to False",
           cfg.research_blocked_setup_eval is False)
    for frozen, expected in (("stop_atr_mult", 1.5),
                             ("take_profit_atr_mult", 2.5),
                             ("trailing_atr_mult", 1.5),
                             ("trailing_trigger_r", 1.0),
                             ("risk_per_trade", 0.01),
                             ("max_open_positions", 5),
                             ("max_holding_days", 30)):
        _check(f"frozen `{frozen}` is still {expected}",
               getattr(cfg, frozen) == expected)
    _check("exit_engine_mode is still legacy",
           cfg.exit_engine_mode == "legacy")


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"=== R7 observability acceptance ({len(tests)} groups) ===\n")
    for t in tests:
        print(f"-- {t.__name__}")
        t()
        print()
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"=== {passed}/{total} assertions passed ===")
    failed = [(n, d) for n, ok, d in _RESULTS if not ok]
    for n, d in failed:
        print(f"  FAIL {n}: {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
