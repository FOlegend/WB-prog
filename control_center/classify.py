"""classify.py — turn raw scan output into registry records.

Classification policy (IMPORTANT)
---------------------------------
* Nothing here guesses silently. Every non-obvious call carries an ``evidence``
  string pointing at the artefact that justifies it (an import edge, a freeze
  contract, a HANDOFF note, or a mirror-comparison).
* When evidence is insufficient the row is left ``status=UNKNOWN`` /
  ``confidence=LOW`` / ``needs_review=1`` so the UI shows NEED HUMAN REVIEW.
* ``canonical`` is only set to 1 when a freeze contract, a production import
  edge, or an explicit handoff statement identifies the module as such.
"""
from __future__ import annotations

import ast
import os
import re
from collections import defaultdict

# --------------------------------------------------------------------------
# Entry points — roots for reachability (usage) tracing
# --------------------------------------------------------------------------
PRODUCTION_ENTRIES = ["production/main.py", "production/pipeline.py"]
RESEARCH_ENTRIES = [
    "dynamic_universe_backtest.py", "screen_as_of.py", "screener_backtest.py",
    "backtest.py", "v32_experiments.py", "v32_robustness.py",
    "setup_comparison.py", "breakout_comparison.py", "breakout_ablation.py",
    "ablation_test.py", "hmm_diagnostic.py", "hmm_backtest_compare.py",
    "hmm_vw_compare.py", "hostile_regime_test.py", "audit_return_calculation.py",
    "historical_cache.py", "main.py",
    "regime_dual_engine/validation_conditioning.py",
    "regime_dual_engine/validation_divergence.py",
    "regime_dual_engine/validation_orthogonality.py",
    "regime_dual_engine/validation_perturbation.py",
    "regime_dual_engine/validation_state_alignment.py",
    "regime_dual_engine/ablation_pit.py",
    "regime_dual_engine/robustness_pit.py",
    "regime_dual_engine/audit_distribution_days.py",
    "regime_dual_engine/audit_distdays_thresholds.py",
    "regime_dual_engine/audit_live_backtest.py",
    "regime_dual_engine/setup_v1_validation.py",
    "regime_dual_engine/setup_v1_root_cause.py",
    "regime_dual_engine/setup_v1_selection_sim.py",
    "regime_dual_engine/setup_v1_regime_policy_test.py",
    "regime_dual_engine/backtest_harness.py",
    "regime_dual_engine/daily_signals.py",
    "regime_dual_engine/build_review_report.py",
    "regime_dual_engine/download_missing_ohlcv.py",
]
BACKTEST_ENTRIES = [
    "dynamic_universe_backtest.py", "screener_backtest.py", "backtest.py",
    "v32_experiments.py", "v32_robustness.py", "production/backtest.py",
    "regime_dual_engine/backtest_harness.py", "ablation_test.py",
    "breakout_ablation.py", "setup_comparison.py",
]

# --------------------------------------------------------------------------
# Architecture layers
# --------------------------------------------------------------------------
LAYER_RULES = [
    (r"config", "CONFIG"),
    (r"contracts/", "CONTRACTS"),
    (r"stops/|stop_engine", "STOP"),
    (r"exits/|exit_engine", "EXIT_ENGINE"),
    (r"datasource|data_fetcher|historical_cache|breadth_data|pit_breadth_data|download_missing", "DATA"),
    (r"screener|screen_as_of|universe|constituents", "UNIVERSE_SCREENER"),
    (r"setup|signal", "SETUP_SIGNAL"),
    (r"regime|hmm|breadth|dist(ribution)?_days", "MARKET_REGIME"),
    (r"risk", "RISK_SIZING"),
    (r"portfolio", "PORTFOLIO"),
    (r"state|ledger", "STATE"),
    (r"indicator|technicals", "INDICATORS"),
    (r"exit", "EXIT"),
    (r"report|briefing|display|build_review", "REPORTING"),
    (r"fees|utils", "UTIL"),
]


def architecture_layer(path: str) -> str:
    p = path.lower()
    base = os.path.basename(p)
    if p.startswith("control_center/"):
        return "CONTROL_CENTER"
    if p.startswith("research/") or p.startswith("experiments/"):
        return "RESEARCH"
    if base == "__init__.py":
        return "PACKAGE"
    if base.startswith("test_") or "/tests/" in p:
        return "TESTS"
    if base.startswith("config"):
        return "CONFIG"
    for pat, layer in LAYER_RULES:
        if re.search(pat, p):
            return layer
    return "INFRA"


SUBSYSTEM_BY_LAYER = {
    "DATA": "Data",
    "UNIVERSE_SCREENER": "Screener / Universe",
    "MARKET_REGIME": "Market Regime",
    "SETUP_SIGNAL": "Setup / Signal",
    "RISK_SIZING": "Risk / Sizing",
    "PORTFOLIO": "Portfolio",
    "STATE": "State",
    "INDICATORS": "Indicators / Technicals",
    "EXIT": "Exit",
    "STOP": "Stop Engine",
    "EXIT_ENGINE": "Exit Engine",
    "CONTRACTS": "Contracts",
    "REPORTING": "Reporting",
    "CONFIG": "Config",
    "UTIL": "Utility",
    "INFRA": "Infrastructure",
    "RESEARCH": "Return Research",
}


# --------------------------------------------------------------------------
# Production decision authority (Phase 3)
# --------------------------------------------------------------------------
# `production_used` answers "does the production pipeline import/use this?".
# That is NOT the same question as "does this module's output decide what
# production actually does?". Phase 3 wires the Stop/Exit engines into
# production in SHADOW mode, which makes them genuinely *used* while the legacy
# oracle remains the only decision source. The registry must be able to state
# both facts instead of fudging one to fake the other.
DECISION_AUTHORITY_PRIMARY = "PRIMARY"       # its output decides production actions today
DECISION_AUTHORITY_SHADOW = "SHADOW_ONLY"    # computed + recorded; would decide only in mode="new"
DECISION_AUTHORITY_NONE = "NONE"             # no production decision authority

# modules whose output currently drives production decisions (legacy path)
PRIMARY_DECISION_AUTHORITY = {
    "production/pipeline.py", "production/main.py", "production/config.py",
    "production/backtest.py", "production/datasource.py",
    "production/ledger.py", "production/screener/screener.py",
    "production/agents/regime.py", "production/agents/setup.py",
    "production/portfolio/portfolio.py", "production/risk/risk.py",
    "production/reporting/briefing.py",
    "src/portfolio/portfolio_manager.py", "src/agents/risk_manager.py",
    "src/agents/setup_agent.py", "src/agents/regime_agent.py",
    "src/agents/technicals_agent.py", "src/screener/screener.py",
    "src/state/state.py",
}

# modules that are evaluated and recorded but cannot decide (unless mode="new")
SHADOW_AUTHORITY_PREFIXES = ("production/exits/", "production/stops/",
                             "production/contracts/")

# The mode production actually runs today. Human-controlled — never inferred
# from code, and never changed by an agent (Phase 3 spec §22).
CURRENT_EXIT_ENGINE_MODE = "legacy"

# Human approvals, recorded verbatim so the registry never has to guess.
STOP_EXIT_FREEZE_APPROVED = "2026-10-01"          # sign-off §1 (freeze face)
# sign-off §8: allowed labels are IMPLEMENTED / HISTORICALLY VALIDATED /
# LIVE VALIDATION PENDING — never "FULLY LIVE VALIDATED" before the gate closes.
PHASE3_STATUS = ("IMPLEMENTED / HISTORICALLY VALIDATED / LIVE VALIDATION PENDING")


def decision_authority(path: str, curated: dict) -> str:
    """Classify a module's production decision authority."""
    if "production_decision_authority" in curated:
        return curated["production_decision_authority"]
    if path in PRIMARY_DECISION_AUTHORITY:
        return DECISION_AUTHORITY_PRIMARY
    if path.endswith(".py") and path.startswith(SHADOW_AUTHORITY_PREFIXES):
        return DECISION_AUTHORITY_SHADOW
    return DECISION_AUTHORITY_NONE


# --------------------------------------------------------------------------
# Curated evidence — sourced from freeze contracts + HANDOFFs (NOT guessed)
# --------------------------------------------------------------------------
CURATED: dict[str, dict] = {
    # ---- Market Regime ----
    "regime_dual_engine/regime_dual.py": dict(
        type="core", status="VALIDATED", canonical=1, subsystem="Market Regime",
        confidence="HIGH", purpose="Regime v1 dual-engine composite (HMM 50% + Market Breadth 50%).",
        evidence="doc:regime_dual_engine/REGIME_V1_FREEZE.md (FROZEN 2026-08-30)"),
    "regime_dual_engine/engine.py": dict(
        type="core", status="VALIDATED", canonical=1, subsystem="Market Regime",
        confidence="HIGH", purpose="Regime v1 engine (public 5-field schema).",
        evidence="doc:regime_dual_engine/REGIME_V1_FREEZE.md"),
    "regime_dual_engine/hmm_engine.py": dict(
        type="core", status="VALIDATED", canonical=1, subsystem="Market Regime",
        confidence="HIGH", purpose="HMM engine half of Regime v1.",
        evidence="doc:regime_dual_engine/REGIME_V1_FREEZE.md"),
    "regime_dual_engine/breadth_engine.py": dict(
        type="core", status="VALIDATED", canonical=1, subsystem="Market Regime",
        confidence="HIGH", purpose="Market Breadth engine half of Regime v1 (252d percentile, thrust, divergence).",
        evidence="doc:regime_dual_engine/REGIME_V1_FREEZE.md"),
    "regime_dual_engine/config.py": dict(
        type="config", status="VALIDATED", canonical=1, subsystem="Market Regime",
        confidence="HIGH", purpose="Frozen DualEngineConfig (Regime v1 parameters).",
        evidence="doc:regime_dual_engine/REGIME_V1_FREEZE.md; import:production/config.py"),
    "production/agents/regime.py": dict(
        type="production", status="ACTIVE", canonical=1, subsystem="Market Regime",
        confidence="HIGH", purpose="Production facade over Regime v1 (compute_market_regime).",
        evidence="import:production/pipeline.py"),
    "src/agents/regime_agent.py": dict(
        type="core", status="LEGACY", canonical=0, legacy=1, replacement_module="regime_dual_engine/",
        subsystem="Market Regime", confidence="HIGH",
        purpose="HYBRID — do NOT delete: Regime v1 (regime_dual_engine/hmm_engine.py) still imports "
                "its HMM primitives (fit_hmm / compute_features / decode_and_label). Only the v3 "
                "composite (regime_score_engine / market_regime) is legacy.",
        evidence="import:regime_dual_engine/hmm_engine.py (production path); "
                 "doc:regime_dual_engine/REGIME_V1_FREEZE.md §2 (KER/ADX/DistDays banned)"),
    "hmm_diagnostic.py": dict(type="research", status="RESEARCH", subsystem="Market Regime",
        confidence="HIGH", purpose="HMM diagnostic (v3 era).", evidence="filename+root-research-script"),
    "hmm_backtest_compare.py": dict(type="research", status="RESEARCH", subsystem="Market Regime",
        confidence="HIGH", purpose="HMM volatility-window backtest comparison.", evidence="filename+root-research-script"),
    "hmm_vw_compare.py": dict(type="research", status="RESEARCH", subsystem="Market Regime",
        confidence="HIGH", purpose="HMM volatility-window comparison.", evidence="filename+root-research-script"),
    "v32_experiments.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Market Regime",
        confidence="HIGH", purpose="v3.2 4-variant regime experiment driver.", evidence="doc:HANDOFF_2026-08-30.md"),
    "v32_robustness.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Market Regime",
        confidence="HIGH", purpose="v3.2 robustness stress tests.", evidence="filename+root-research-script"),
    "hostile_regime_test.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Market Regime",
        confidence="HIGH", purpose="Hostile-regime (COVID/2022) behaviour test.", evidence="doc:HANDOFF_2026-08-30.md"),

    # ---- Setup / Signal ----
    "src/agents/setup_agent.py": dict(
        type="core", status="VALIDATED", canonical=1, subsystem="Setup / Signal",
        confidence="HIGH", purpose="Setup v1 implementation (Pullback Only frozen). Breakout code dormant.",
        evidence="doc:src/agents/SETUP_V1_FREEZE.md (FROZEN 2026-08-30)"),
    "production/agents/setup.py": dict(
        type="production", status="ACTIVE", canonical=1, subsystem="Setup / Signal",
        confidence="HIGH", purpose="Production wrapper over Setup v1 (evaluate_setup + freeze guard).",
        evidence="import:production/pipeline.py"),
    "setup_comparison.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Setup / Signal",
        confidence="HIGH", purpose="Legacy vs Breakout vs Pullback setup comparison (v3 engine).",
        evidence="reports/setup_comparison.json"),
    "breakout_comparison.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Setup / Signal",
        confidence="HIGH", purpose="Breakout-only comparison under v3.2.4 engine.",
        evidence="reports/breakout_comparison.json"),
    "breakout_ablation.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Setup / Signal",
        confidence="HIGH", purpose="Breakout component ablation.",
        evidence="reports/breakout_ablation.json"),
    "ablation_test.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Setup / Signal",
        confidence="MEDIUM", purpose="Generic ablation test.", evidence="filename+root-research-script"),

    # ---- Screener / Universe ----
    "src/screener/screener.py": dict(
        type="core", status="ACTIVE", canonical=1, subsystem="Screener / Universe",
        confidence="HIGH", purpose="6-filter screener + RS helpers (shared by live + PIT + production).",
        evidence="import:production/screener/screener.py; import:screen_as_of.py; import:main.py"),
    "production/screener/screener.py": dict(
        type="production", status="ACTIVE", canonical=1, subsystem="Screener / Universe",
        confidence="HIGH", purpose="Production screener wrapper (screen_from_source, no silent fallback).",
        evidence="import:production/pipeline.py"),
    "screen_as_of.py": dict(
        type="backtest", status="ACTIVE", canonical=1, subsystem="Screener / Universe",
        confidence="HIGH", purpose="Point-in-time screener (>= as_of only) for research backtests.",
        evidence="import:dynamic_universe_backtest.py; doc:HANDOFF_2026-08-30.md"),
    "screener_backtest.py": dict(type="experiment", status="EXPERIMENTAL", subsystem="Screener / Universe",
        confidence="HIGH", purpose="Screen-today-then-backtest (look-ahead demo; superseded).",
        evidence="doc:HANDOFF_2026-08-30.md (static screen pitfall)"),

    # ---- Data ----
    "src/data/data_fetcher.py": dict(type="core", status="ACTIVE", canonical=1, subsystem="Data",
        confidence="HIGH", purpose="yfinance fetch layer (fetch_daily / fetch_batch).",
        evidence="import:historical_cache.py; import:src/screener/screener.py"),
    "production/datasource.py": dict(type="production", status="ACTIVE", canonical=1, subsystem="Data",
        confidence="HIGH", purpose="DataSource ABC + Cached/YFinance/Stooq + Provenance (PIT slicing).",
        evidence="import:production/pipeline.py; doc:reports/production_pipeline_unified_2026-09-04.md"),
    "historical_cache.py": dict(type="backtest", status="ACTIVE", canonical=1, subsystem="Data",
        confidence="HIGH", purpose="OHLCV cache builder for research (Layer 1).",
        evidence="import:dynamic_universe_backtest.py; doc:HANDOFF_2026-08-30.md §3"),

    # ---- Risk / Sizing ----
    "production/risk/risk.py": dict(type="production", status="ACTIVE", canonical=1, subsystem="Risk / Sizing",
        confidence="HIGH", purpose="Production ATR position sizing (size_swing_position).",
        evidence="import:production/pipeline.py"),
    "src/agents/risk_manager.py": dict(type="core", status="LEGACY", legacy=1, canonical=0,
        replacement_module="production/risk/risk.py", subsystem="Risk / Sizing", confidence="HIGH",
        purpose="Legacy risk sizing — do NOT delete: production/risk/risk.py reuses size_position.",
        evidence="import:production/risk/risk.py; import:dynamic_universe_backtest.py"),

    # ---- Portfolio ----
    "production/portfolio/portfolio.py": dict(type="production", status="ACTIVE", canonical=1,
        subsystem="Portfolio", confidence="HIGH",
        purpose="Production portfolio constraints + exit_check + build_order_buy.",
        evidence="import:production/pipeline.py"),
    "src/portfolio/portfolio_manager.py": dict(type="core", status="LEGACY", legacy=1, canonical=0,
        replacement_module="production/portfolio/portfolio.py", subsystem="Portfolio", confidence="HIGH",
        purpose="Legacy portfolio manager — do NOT delete: production/portfolio/portfolio.py reuses _exit_check.",
        evidence="import:production/portfolio/portfolio.py; import:main.py"),

    # ---- State ----
    "production/ledger.py": dict(type="production", status="ACTIVE", canonical=1, subsystem="State",
        confidence="HIGH", purpose="DecisionRecord ledger writer (reports/decision_YYYY-MM-DD.json).",
        evidence="doc:reports/production_pipeline_unified_2026-09-04.md §1"),
    "src/state/state.py": dict(type="core", status="LEGACY", legacy=1, canonical=0,
        subsystem="State", confidence="HIGH",
        purpose="State helpers — do NOT delete: production/pipeline.py + portfolio + production/backtest.py "
                "reuse mark_to_market / load_state / open_position / close_position.",
        evidence="import:production/pipeline.py; import:production/portfolio/portfolio.py"),

    # ---- Reporting ----
    "production/reporting/briefing.py": dict(type="production", status="ACTIVE", canonical=1,
        subsystem="Reporting", confidence="HIGH", purpose="Production human briefing renderer.",
        evidence="doc:reports/production_pipeline_unified_2026-09-04.md §2"),
    "src/utils/display.py": dict(type="core", status="LEGACY", legacy=1, canonical=0,
        replacement_module="production/reporting/briefing.py", subsystem="Reporting", confidence="MEDIUM",
        purpose="Legacy markdown briefing renderer (v3).", evidence="import:main.py"),
    "src/backtest/report.py": dict(type="core", status="ACTIVE", canonical=0, subsystem="Reporting",
        confidence="MEDIUM", purpose="HTML backtest report generator (research).",
        evidence="import:screener_backtest.py"),

    # ---- Backtest engines ----
    "production/backtest.py": dict(type="backtest", status="VALIDATED", canonical=1, subsystem="Backtest",
        confidence="HIGH", purpose="Production-equivalent backtest (replays run_daily).",
        evidence="doc:reports/production_pipeline_unified_2026-09-04.md §7"),
    "dynamic_universe_backtest.py": dict(type="backtest", status="RESEARCH", canonical=0, subsystem="Backtest",
        confidence="HIGH", purpose="Legacy dynamic-screening research engine (Layer 4+5, 4 variants).",
        evidence="doc:reports/production_pipeline_unified_2026-09-04.md (retained as legacy research)"),
    "src/backtest/engine.py": dict(type="core", status="ACTIVE", canonical=1, subsystem="Backtest",
        confidence="HIGH", purpose="compute_metrics + legacy BacktestEngine used by research.",
        evidence="import:dynamic_universe_backtest.py; import:screener_backtest.py"),
    "backtest.py": dict(type="backtest", status="LEGACY", legacy=1, canonical=0, subsystem="Backtest",
        confidence="MEDIUM", purpose="Early static backtest entry (v3 era).",
        evidence="doc:HANDOFF_2026-08-30.md §3 (legacy)"),

    # ---- Config ----
    "production/config.py": dict(type="config", status="VALIDATED", canonical=1, subsystem="Config",
        confidence="HIGH", purpose="ProductionConfig — FROZEN production parameters.",
        evidence="doc:src/agents/SETUP_V1_FREEZE.md; doc:regime_dual_engine/REGIME_V1_FREEZE.md"),
    "config.py": dict(type="config", status="LEGACY", legacy=1, canonical=0, subsystem="Config",
        confidence="HIGH", purpose="Legacy global Config (v3 regime + setup breakout params).",
        evidence="doc:HANDOFF_2026-08-30.md §3"),

    # ---- Entry points ----
    "production/main.py": dict(type="production", status="ACTIVE", canonical=1, subsystem="Entrypoint",
        confidence="HIGH", purpose="PRODUCTION entrypoint (thin CLI over pipeline.run_daily).",
        evidence="doc:HANDOFF_2026-09-04.md §1"),
    "main.py": dict(type="production", status="LEGACY", legacy=1, canonical=0, subsystem="Entrypoint",
        replacement_module="production/main.py", confidence="HIGH",
        purpose="LEGACY live entrypoint (still uses v3 regime; do NOT confuse with production/).",
        evidence="doc:HANDOFF_2026-08-30.md §3 + §5 (live entry still legacy)"),

    # ---- Technicals ----
    "src/agents/technicals_agent.py": dict(type="core", status="ACTIVE", canonical=1,
        subsystem="Indicators / Technicals", confidence="HIGH",
        purpose="Technicals ensemble signal (used by production for exit timing).",
        evidence="import:production/pipeline.py; import:dynamic_universe_backtest.py"),
    "src/indicators/technicals.py": dict(type="core", status="ACTIVE", canonical=1, subsystem="Indicators / Technicals",
        confidence="HIGH", purpose="Indicator library (sma/ema/atr/adx/safe_float).",
        evidence="import:src/agents/setup_agent.py; import:src/screener/screener.py"),
    "src/utils/fees.py": dict(type="core", status="UNKNOWN", canonical=0, subsystem="Utility",
        confidence="LOW", purpose="Fee model helper (usage not proven).", evidence="no importer found"),

    # ---- Control Center (this tooling) ----
    "control_center/registry_db.py": dict(type="utility", status="ACTIVE", subsystem="Control Center",
        confidence="HIGH", purpose="Registry SQLite schema + human-decision preservation.", evidence="self"),
    "control_center/scanner.py": dict(type="utility", status="ACTIVE", subsystem="Control Center",
        confidence="HIGH", purpose="READ-ONLY AST repo scanner (imports/calls/assets/git).", evidence="self"),
    "control_center/classify.py": dict(type="utility", status="ACTIVE", subsystem="Control Center",
        confidence="HIGH", purpose="Classification rules + curated evidence + pipelines.", evidence="self"),
    "control_center/generate_docs.py": dict(type="utility", status="ACTIVE", subsystem="Control Center",
        confidence="HIGH", purpose="Generates ARCHITECTURE.md / ARCHITECTURE.yaml from the DB.", evidence="self"),
    "control_center/refresh_registry.py": dict(type="utility", status="ACTIVE", subsystem="Control Center",
        confidence="HIGH", purpose="Rescan + rebuild registry DB + docs.", evidence="self"),

    # ---- Untracked legacy artefact from the 2026-08-14 session ----
    "unified_backtest.py": dict(type="backtest", status="LEGACY", legacy=1, canonical=0,
        replacement_module="production/backtest.py", subsystem="Backtest", confidence="HIGH",
        purpose="Ad-hoc unified backtest written in the 2026-08-14 session (untracked). "
                "Superseded by production/backtest.py + this registry.",
        evidence="git status (untracked); doc:HANDOFF_2026-09-04.md §1 (production/backtest.py)"),
    "regime_dual_engine/validation_divergence_review.py": dict(
        type="audit", status="ACTIVE", subsystem="Market Regime", confidence="HIGH",
        purpose="Standalone review generator for the divergence validation (not imported by design).",
        evidence="reports/dual_engine_divergence_review.json"),

    # ---- Phase 0-1: contracts + Stop Engine (2026-09-28, ISOLATED) ----
    # production_used=0 is an explicit override: these files live under
    # production/ but are NOT imported by the production pipeline
    # (asserted by production/tests/test_stops.py::test_phase1_isolation_not_wired).
    "production/contracts/__init__.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="Cross-engine contract package (Phase 0): re-exports the five models.",
        evidence="doc:production/stops/DESIGN_NOTE.md §2"),
    "production/contracts/base.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="Contract primitives: ContractError, Provenance, ExecutionWindow, validators, deterministic ids.",
        evidence="doc:production/stops/DESIGN_NOTE.md §2 (units/session semantics)"),
    "production/contracts/reason_codes.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="Closed reason-code vocabulary (stop / risk / order / execution / reference-price sources).",
        evidence="doc:production/stops/DESIGN_NOTE.md §3.4"),
    "production/contracts/entry.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="EntryDecision contract (signal semantics; deliberately carries no share count).",
        evidence="doc:production/stops/DESIGN_NOTE.md §2"),
    "production/contracts/stop.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="StopPlan contract (initial vs current stop; optional structure_level reserved).",
        evidence="doc:production/stops/DESIGN_NOTE.md §2/§3.1"),
    "production/contracts/risk.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="RiskDecision contract — 3-valued status APPROVE/RESIZE/REJECT (contract only; engine is a later phase).",
        evidence="doc:production/stops/DESIGN_NOTE.md §2"),
    "production/contracts/order.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="OrderIntent contract keeping signal / execution / risk explicitly separate.",
        evidence="doc:production/stops/DESIGN_NOTE.md §2"),
    "production/contracts/position.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="PositionState view incl. the current_stop_price CONCEPT (not persisted in Phase 1).",
        evidence="doc:production/stops/DESIGN_NOTE.md §2; STOP_ENGINE_BACKLOG.md B2"),
    "production/stops/__init__.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Stop Engine", confidence="HIGH",
        purpose="Stop Engine public surface (initial / trailing / update + StopPlan re-export).",
        evidence="doc:production/stops/DESIGN_NOTE.md §3"),
    "production/stops/errors.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Stop Engine", confidence="HIGH",
        purpose="StopEngineError with reason codes (fail-loud, no silent fallback).",
        evidence="doc:production/stops/DESIGN_NOTE.md §3.4"),
    "production/stops/initial.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Stop Engine", confidence="HIGH",
        purpose="ATR initial stop — reproduces src/agents/risk_manager.size_position formula "
                "(parity-tested, legacy untouched).",
        evidence="test:production/tests/test_stops.py::test_legacy_parity_initial_stop"),
    "production/stops/trailing.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Stop Engine", confidence="HIGH",
        purpose="ATR trailing stop + trigger gate — reproduces portfolio_manager._exit_check trailing "
                "block (parity-tested, legacy untouched); SHORT is the mirror.",
        evidence="test:production/tests/test_stops.py::test_legacy_parity_trailing"),
    "production/stops/engine.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Stop Engine", confidence="HIGH",
        purpose="Stop Engine facade: initial_stop_plan / update_stop (no-loosening) / "
                "plan_from_position / StopUpdate.explain(). Levels only — no exit decision.",
        evidence="doc:production/stops/DESIGN_NOTE.md §3.3/§3.4"),
    "production/tests/test_contracts.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        subsystem="Contracts", confidence="HIGH",
        purpose="18 Phase-0 contract validation tests (plain-script runner).",
        evidence="doc:production/stops/DESIGN_NOTE.md §7"),
    "production/tests/test_stops.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        subsystem="Stop Engine", confidence="HIGH",
        purpose="19 Stop Engine tests incl. legacy parity + Phase-3 wiring-safety guard "
                "(plain-script runner).",
        evidence="doc:production/stops/DESIGN_NOTE.md §4/§7"),

    # ---- Phase 2: Exit Engine (2026-09-29, ISOLATED) ----
    "production/contracts/exit.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Contracts", confidence="HIGH",
        purpose="ExitDecision contract — legacy-compatible reason codes and fill "
                "models; EXACT fill-price rules (no range heuristics).",
        evidence="doc:production/exits/DESIGN_NOTE.md §2/§5"),
    "production/exits/__init__.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Exit Engine public surface (context / rules / engine).",
        evidence="doc:production/exits/DESIGN_NOTE.md §3"),
    "production/exits/context.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="ExitContext — explicit deterministic inputs; delegates the "
                "trailing ARMING GATE to the Stop Engine (no trailing math here).",
        evidence="doc:production/exits/DESIGN_NOTE.md §3"),
    "production/exits/rules.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="The five legacy exit rules (STOP_LOSS / TAKE_PROFIT / "
                "TRAILING_STOP / TIME_STOP / SIGNAL_EXIT) in legacy priority order.",
        evidence="test:production/tests/test_exit_engine.py (17 parity cases)"),
    "production/exits/engine.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Exit Engine facade: evaluate_exit(context) -> ExitDecision; "
                "consumes StopPlan levels only.",
        evidence="doc:production/exits/DESIGN_NOTE.md §4/§5"),
    "production/tests/test_exit_engine.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="31 Exit Engine tests: 17 required parity cases vs _exit_check, "
                "production-config parity, SHORT mirror, isolation + no-trailing-math guards.",
        evidence="doc:reports/phase2_exit_engine_2026-09-29.md §F"),
    "production/tests/exit_fixtures.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Deterministic fixtures for every legacy exit case (no randomness; "
                "reproducible input state).",
        evidence="doc:reports/phase2_exit_engine_2026-09-29.md §F"),
    "production/tests/replay_exit_parity.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Trade-level replay parity: real historical bars/trades, legacy "
                "_exit_check vs new Exit Engine, every divergence reported.",
        evidence="doc:reports/phase2_exit_parity_replay_2026-09-29.json"),
    # ---- Phase 3: shadow wiring (Stop + Exit engines into the pipeline) ----
    "production/exits/adapter.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Phase-3 adapter: production position dict -> typed contracts -> PositionState / "
                "StopPlan (supplied by the Stop Engine) / ExitContext. Translation + validation "
                "only — no strategy, no stop arithmetic.",
        evidence="doc:production/STOP_EXIT_V1_FREEZE.md §4"),
    "production/exits/shadow.py": dict(
        type="core", status="ACTIVE", canonical=1,
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Phase-3 shadow comparison: fail-open evaluation of the new engine, legacy stays "
                "authoritative, 8-way divergence classification recorded in "
                "DecisionRecord.exits.shadow.",
        evidence="doc:production/STOP_EXIT_V1_FREEZE.md §4"),
    "production/reporting/shadow_monitor.py": dict(
        type="utility", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Reporting", confidence="HIGH",
        purpose="Phase-3 shadow coverage monitor + cut-over gate (20 consecutive qualifying "
                "sessions, 0 divergence). Reads decision ledgers / coverage JSON only.",
        evidence="doc:reports/phase3_wiring_acceptance_2026-09-30.md §J"),
    "production/tests/test_wiring_safety.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        production_decision_authority="NONE",
        subsystem="Exit Engine", confidence="HIGH",
        purpose="18 Phase-3 wiring-safety tests (default legacy, shadow side-effect-free, "
                "fail-open, rollback, no persistence, freeze contract, CLI --exit-engine-mode).",
        evidence="doc:reports/phase3_wiring_acceptance_2026-09-30.md §H"),
    "production/tests/shadow_coverage.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        production_decision_authority="NONE",
        subsystem="Exit Engine", confidence="HIGH",
        purpose="Phase-3 shadow coverage runner: replays the production-equivalent backtest in "
                "shadow mode and compares it end-to-end with a legacy control run.",
        evidence="doc:reports/phase3_shadow_coverage_2026-09-30.json"),
    "production/tests/phase4_diagnostics.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Backtest", confidence="HIGH",
        purpose="Phase-4 D1-D6 read-only diagnostics: runs the EXISTING "
                "production/backtest.py unchanged and captures the DecisionRecords it discards "
                "(in-process wrapper) to attribute the benchmark gap. Changes no parameter and no "
                "production path.",
        evidence="doc:reports/phase4_diagnostics_2026-10-01.md; "
                 "data:reports/phase4_diag_data_2026-10-01.json"),
    "production/tests/phase4_verify.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Backtest", confidence="HIGH",
        purpose="Phase-4 independent verification (V1-V4): replays every sizing attempt through the "
                "real production risk engine (99.7% agreement), recomputes the benchmark "
                "decomposition across five windows, runs the sizing counterfactual, and tests "
                "sub-period stability. V4 REFUTED the entry-gap monotonicity claim.",
        evidence="doc:reports/phase4_diagnostics_2026-10-01.md §V; "
                 "data:reports/phase4_verify_2026-10-01.json"),
    "production/tests/phase5_step0_marginal_cohort.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Backtest", confidence="HIGH",
        purpose="Phase-5 Step 0 value measurement: trades the risk-rejected valid setups "
                "hypothetically with next-open entry and the FROZEN Exit Engine, after validating "
                "the machinery against the realised trades (98.0%). Verdict: marginal expectancy "
                "-0.270R -> the budget cap is protective.",
        evidence="doc:reports/phase5_step0_report_2026-10-01.md; "
                 "data:reports/phase5_step0_marginal_cohort_2026-10-01.json"),
    "production/tests/phase5_step0b_lever_cohorts.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Backtest", confidence="HIGH",
        purpose="Step-0 addendum (read-only): re-scores each candidate lever over the 458 simulated "
                "rejections to test whether a lever admits a different cohort than the all-rejected "
                "average (it does not — every lever's newly-admitted cohort is negative), and builds "
                "the leverage decision table including SPY's own drawdown/Sharpe for the same window.",
        evidence="doc:reports/phase5_step0_report_2026-10-01.md §6; "
                 "data:reports/phase5_step0b_lever_cohorts_2026-10-01.json"),
    "production/tests/freeze_conformance_audit.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Backtest", confidence="HIGH",
        purpose="Systematic frozen-contract conformance audit (re-runnable): checks every clause "
                "of SETUP_V1 / REGIME_V1 / STOP_EXIT_V1 against the code and the records. Result: "
                "13 OPERATIVE, 9 TEST-VERIFIED, 1 INERT (the Setup v1 extension filter), 2 CLARITY, "
                "0 MISMATCH.",
        evidence="doc:reports/freeze_conformance_audit_2026-10-01.md; "
                 "data:reports/freeze_conformance_audit_2026-10-01.json"),
    "research/harness.py": dict(
        type="research", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Isolated research harness: runs the EXISTING production-equivalent backtest unchanged "
                "and computes the standardised performance dashboard, optionally with exactly ONE "
                "config parameter overridden in memory (production/config.py is never edited).",
        evidence="doc:reports/return_improvement_research_2026-10-01.md"),
    "research/run_baseline.py": dict(
        type="research", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Deliverable 2 runner: reproduces the frozen baseline (396 sessions / 151 trades) and "
                "records commit + config snapshot + data fingerprint; asserts the 7 frozen reference "
                "values.",
        evidence="data:reports/research_baseline_2026-10-01.json"),
    "research/run_tp_grid.py": dict(
        type="experiment", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="R1 controlled experiment: pre-registered take-profit grid {2.0, 2.5, 3.0, 3.5, 4.0} "
                "ATR, one variable at a time, with subperiod split, exit distribution and MFE capture.",
        evidence="data:reports/research_tp_grid_2026-10-01.json"),
    "research/step0_harness_audit.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Spec §11 gate: re-verifies the Step-0 research harness and classifies its 3 unexplained "
                "control mismatches. All 3 = HARNESS_HORIZON_GUARD_ARTIFACT (simulator stops at "
                "held > 30 days before evaluating the exit when the boundary is a weekend); proven by "
                "re-simulating with a larger horizon, which reproduces each recorded exit exactly.",
        evidence="data:reports/step0_harness_audit_2026-10-01.json; "
                 "doc:reports/return_bottleneck_report_2026-10-01.md §11"),
    "research/pit_breadth_audit.py": dict(
        type="audit", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Point-in-time audit of Regime v1's breadth engine. Finds that "
                "regime_dual_engine/pit_breadth_data.get_breadth() ignores its `end` argument when the "
                "cache exists, so compute_regime_decision() always reads the cache TAIL (2025-07-31): "
                "Engine B is a CONSTANT (percentile 41.746, breadth_now 57.23) for every as-of date in "
                "historical replay, while the PIT-correct value ranges 11.8-56.0 and the regime label "
                "flips at some dates. Measures the end-to-end impact by patching "
                "production.pipeline.load_breadth IN-PROCESS ONLY (no file modified).",
        evidence="data:reports/pit_breadth_audit_2026-10-01.json; "
                 "doc:reports/pit_breadth_leak_2026-10-01.md"),
    "research/rerun_corrected_research.py": dict(
        type="research", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Re-derives the Phase-4/5 research on the PIT-corrected regime by calling the "
                "EXISTING diagnostics/verify/Step-0 scripts unchanged and only redirecting their "
                "output paths, so the contaminated artifacts stay on disk as audit evidence beside "
                "the corrected ones.",
        evidence="doc:reports/pit_correction_and_rebaseline_2026-10-01.md"),
    "research/run_lever_grid.py": dict(
        type="experiment", status="ACTIVE", canonical=1, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Generic single-variable lever experiment: runs the unchanged ProductionBacktest with "
                "EXACTLY ONE in-memory config override (whitelist enforced) and reports the §15 dashboard, "
                "the sub-period split and the realised-risk %, so a lever can be judged against the "
                "pre-registered criteria (Sharpe up, MaxDD not worse than -10.39%, holds in 2024 AND 2025).",
        evidence="data:reports/research_lever_risk_per_trade_pitcorrected_2026-10-01.json; "
                 "data:reports/research_lever_max_open_positions_pitcorrected_2026-10-01.json; "
                 "doc:reports/experiment_matrix_pitcorrected_2026-10-01.md"),
    "production/tests/test_pit_breadth.py": dict(
        type="test", status="ACTIVE", canonical=0, production_used=0,
        production_decision_authority="NONE",
        subsystem="Return Research", confidence="HIGH",
        purpose="Regression protection for the point-in-time breadth contract (7 tests): different "
                "as_of dates must not share a future tail; no observation after as_of; cache path "
                "PIT-safe and non-destructive; rebuild path PIT-safe (full frame cached, sliced "
                "frame returned); exact-date selection; non-trading-day semantics; and the ledger "
                "must expose breadth tail_date.",
        evidence="doc:reports/pit_correction_and_rebaseline_2026-10-01.md; "
                 "doc:reports/pit_breadth_leak_2026-10-01.md"),
}

# Subsystem canonical map (for the UI's canonical view)
SUBSYSTEM_CANONICAL = {
    "Market Regime": {"canonical": ["regime_dual_engine/", "production/agents/regime.py"],
                      "legacy": ["src/agents/regime_agent.py"]},
    "Setup / Signal": {"canonical": ["src/agents/setup_agent.py", "production/agents/setup.py"]},
    "Screener / Universe": {"canonical": ["src/screener/screener.py", "production/screener/screener.py", "screen_as_of.py"]},
    "Backtest": {"canonical": ["production/backtest.py"], "legacy": ["dynamic_universe_backtest.py"]},
    "Config": {"canonical": ["production/config.py", "regime_dual_engine/config.py"], "legacy": ["config.py"]},
    "Entrypoint": {"canonical": ["production/main.py"], "legacy": ["main.py"]},
    "Risk / Sizing": {"canonical": ["production/risk/risk.py"], "legacy": ["src/agents/risk_manager.py"]},
    "Portfolio": {"canonical": ["production/portfolio/portfolio.py"], "legacy": ["src/portfolio/portfolio_manager.py"]},
    "Stop Engine": {"canonical": ["production/stops/"]},
    "Exit Engine": {"canonical": ["production/exits/"]},
    "Contracts": {"canonical": ["production/contracts/"]},
    "Reporting": {"canonical": ["production/reporting/briefing.py"], "legacy": ["src/utils/display.py"]},
}

# --------------------------------------------------------------------------
# Migrations (curated from HANDOFF docs)
# --------------------------------------------------------------------------
MIGRATIONS = [
    dict(subsystem="Market Regime", old_module="src/agents/regime_agent.py",
         new_module="regime_dual_engine/", status="VALIDATED — WIRED",
         next_action="Reviewer sign-off on the Regime v1 freeze face",
         blocker="human review", evidence="doc:regime_dual_engine/REGIME_V1_FREEZE.md; production/agents/regime.py"),
    dict(subsystem="Live Entrypoint", old_module="main.py",
         new_module="production/main.py + production/pipeline.run_daily", status="VALIDATED — NOT YET CUT OVER",
         next_action="Switch live main fully to screen_from_source (retire legacy main.py path)",
         blocker="human review", evidence="doc:HANDOFF_2026-09-04.md §6 (item 2)"),
    dict(subsystem="Backtest", old_module="dynamic_universe_backtest.py",
         new_module="production/backtest.py", status="AVAILABLE — LEGACY RETAINED",
         next_action="Adopt production-equivalent bt as the research baseline",
         blocker="—", evidence="doc:reports/production_pipeline_unified_2026-09-04.md §7"),
    dict(subsystem="Setup / Signal", old_module="config.py: setup_enabled_types=['breakout']",
         new_module="src/agents/setup_agent.py (Pullback Only, Setup v1)", status="VALIDATED — FROZEN",
         next_action="None (frozen); further ideas go to SETUP_V2_BACKLOG.md",
         blocker="—", evidence="doc:src/agents/SETUP_V1_FREEZE.md §1/§6"),
    dict(subsystem="Stop Engine",
         old_module="derived inline: src/agents/risk_manager.py (initial stop) + "
                    "src/portfolio/portfolio_manager.py::_exit_check (trailing)",
         new_module="production/stops/ + production/contracts/ (Phase 1; reached from production "
                    "through production/exits/adapter.py)",
         status="SHADOW WIRED — NOT DECIDING (parity verified)",
         next_action="No action until the live shadow gate closes; stop levels are consumed by the "
                     "Exit Engine in shadow only",
         blocker="mode != 'new': the legacy oracle still owns every stop decision",
         evidence="doc:production/STOP_EXIT_V1_FREEZE.md §2/§4; "
                  "test:production/tests/test_stops.py (19 tests, legacy parity)"),
    dict(subsystem="Exit Engine",
         old_module="src/portfolio/portfolio_manager.py::_exit_check (decision + fill model inline)",
         new_module="production/exits/ + production/contracts/exit.py (Phase 2; wired in shadow by "
                    "Phase 3 behind cfg.exit_engine_mode)",
         status="SHADOW WIRED — LEGACY STILL AUTHORITATIVE (parity verified, 0 divergences)",
         next_action="Observe shadow mode on live/paper sessions, then decide any cut-over",
         blocker="human review; live shadow coverage PENDING (0 sessions observed)",
         evidence="doc:reports/phase2_exit_engine_2026-09-29.md; "
                  "test:production/tests/test_exit_engine.py (31 tests, 17 parity cases); "
                  "replay:reports/phase2_exit_parity_replay_2026-09-29.json (1073 bars, 0 divergences)"),
    dict(subsystem="Stop + Exit wiring (Phase 3)",
         old_module="src/portfolio/portfolio_manager.py::_exit_check — the single decision source for "
                    "stop/target/trailing/time/signal exits",
         new_module="production/exits/adapter.py + production/exits/shadow.py behind "
                    "production/config.py::exit_engine_mode (legacy | shadow | new; default legacy), "
                    "selectable at the live entrypoint via production/main.py "
                    "--exit-engine-mode",
         status="IMPLEMENTED — HISTORICALLY VALIDATED — LIVE VALIDATION PENDING",
         next_action="Accumulate 20 consecutive qualifying live/paper shadow sessions (0 divergence, "
                     "0 errors, >= 1 real held-position evaluation per session); then a human cut-over "
                     "decision. NOT to be marked FULLY LIVE VALIDATED until then",
         blocker="live shadow validation PENDING (0 sessions observed); mode='new' NOT ENABLED; "
                 "production cut-over NOT APPROVED",
         evidence="human sign-off 2026-10-01 (freeze face approved: "
                  "production/STOP_EXIT_V1_FREEZE.md + production/exits/adapter.py + "
                  "production/exits/shadow.py); doc:reports/phase3_wiring_acceptance_2026-09-30.md; "
                  "test:production/tests/test_wiring_safety.py (18 tests); "
                  "replay:reports/phase3_exit_parity_replay_2026-09-30.json (1073 bars, adapter path, "
                  "0 divergences, 0 adapter-vs-direct mismatches); "
                  "coverage:reports/phase3_shadow_coverage_2026-09-30.json"),
    dict(subsystem="Regime v1 breadth — point-in-time contract (data integrity)",
         old_module="regime_dual_engine/pit_breadth_data.py::get_breadth — returned the WHOLE cached "
                    "CSV whenever the cache existed (the `end` argument was honoured only on rebuild), "
                    "and regime_dual_engine/breadth_data.py::get_breadth_series had the same defect; "
                    "compute_regime_decision then read `pct_above_50dma.iloc[-1]`, i.e. the "
                    "2025-07-31 tail, for every historical as-of date",
         new_module="regime_dual_engine/breadth_data.py::slice_to_end — ONE point-in-time guard applied "
                    "on both the cache and rebuild paths of both loaders, so any `end=as_of` request is "
                    "guaranteed `date <= as_of`",
         status="FIXED — FULLY RE-BASELINED (data-integrity repair; no strategy change)",
         next_action="Human review of the corrected baseline and the re-derived Phase-4/5/R1 findings",
         blocker="—",
         evidence="human approval 2026-10-01 (PIT correction + full re-baseline); "
                  "doc:reports/pit_breadth_leak_2026-10-01.md (finding); "
                  "doc:reports/pit_correction_and_rebaseline_2026-10-01.md (fix + re-baseline); "
                  "test:production/tests/test_pit_breadth.py (7 tests pinning the failure mode); "
                  "allowed by REGIME_V1_FREEZE.md §3 ('可證明的 correctness/bug 修復' + "
                  "'data-pipeline 修正') — no unfreeze required; the regime decision MATHS is untouched"),
]

# --------------------------------------------------------------------------
# Decisions (architecture-level, human-in-the-loop)
# --------------------------------------------------------------------------
DECISIONS = [
    dict(issue="Two repo copies exist with the same commit: 'WB prog' vs 'WB-prog'",
         decision_needed="Pick the single canonical working copy; archive or delete the other",
         current_state="RESOLVED 2026-09-28 — the human declared 'WB-prog' (hyphen) the single authoritative "
                       "working copy; 'WB prog' (space) is archived/read-only and no longer exists on this "
                       "machine, so the divergence comparison now reports 0 by design",
         proposed_action="All development happens in 'WB-prog'; no code is added to the retired copy",
         affected_modules="(repo-level)", status="RESOLVED", priority="HIGH",
         evidence="human decision 2026-09-28 (Phase 0-1 kickoff, item 1); modules: 116, divergence: 0"),
    dict(issue="Legacy live entrypoint (main.py) still present alongside production/main.py",
         decision_needed="Cut live over to production/main.py + screen_from_source",
         current_state="Both exist; production path validated but not cut over",
         proposed_action="After reviewer sign-off, retire legacy main.py path",
         affected_modules="main.py; production/main.py; src/portfolio/portfolio_manager.py",
         status="OPEN", priority="HIGH", evidence="doc:HANDOFF_2026-09-04.md §6"),
    dict(issue="Untracked legacy artefacts from an earlier session (unified_backtest.py + 2 reports)",
         decision_needed="Keep, archive under legacy/, or delete",
         current_state="Untracked in 'WB-prog' only",
         proposed_action="Human decides; no automatic deletion",
         affected_modules="unified_backtest.py; reports/unified_backtest_*",
         status="OPEN", priority="MEDIUM", evidence="git status; copy_divergence table"),
    dict(issue="Unused/dead-code candidates are not proven safe to delete",
         decision_needed="Human review of low-confidence modules (imported by nothing)",
         current_state="Registry flags them; delete-safety answered per-module in the UI",
         proposed_action="Review Module Registry -> 'Unreferenced' filter",
         affected_modules="see modules where production_used=0 AND research_used=0 AND backtest_used=0",
         status="OPEN", priority="MEDIUM", evidence="dependency closure (this registry)"),
    dict(issue="Legacy src/ modules are still imported by the production path (deletion unsafe)",
         decision_needed="Decide whether to migrate production onto production/ equivalents, or accept the shared dependency",
         current_state="regime_dual_engine/hmm_engine.py imports HMM primitives from src/agents/regime_agent.py; "
                       "production/risk, production/portfolio and production/pipeline reuse "
                       "src/agents/risk_manager.py, src/portfolio/portfolio_manager.py, src/state/state.py",
         proposed_action="Either (a) keep them as a shared low-level layer (update labels), or "
                         "(b) plan a migration before any cleanup",
         affected_modules="src/agents/regime_agent.py; src/agents/risk_manager.py; "
                          "src/portfolio/portfolio_manager.py; src/state/state.py",
         status="OPEN", priority="HIGH",
         evidence="dependencies table (production import edges)"),

    # ---- Phase 0-1 (2026-09-28): Stop Engine / contracts ----
    dict(issue="Stop Engine is implemented but NOT wired (Phase 1 isolation)",
         decision_needed="SUPERSEDED — Phase 3 wired the Stop Engine into the production graph in shadow mode",
         current_state="RESOLVED 2026-09-30 by Phase 3. production/stops/ is now reached from "
                       "production/exits/adapter.py (imported lazily by production/pipeline.py), so "
                       "production_used=1; its decision authority is SHADOW_ONLY, i.e. it computes the "
                       "level that the Exit Engine consumes while the LEGACY oracle still decides",
         proposed_action="No further action; the remaining question is the live shadow gate (see the "
                         "'Live 20-session shadow gate' decision)",
         affected_modules="production/stops/; production/contracts/; production/exits/adapter.py",
         status="RESOLVED", priority="HIGH",
         evidence="doc:production/STOP_EXIT_V1_FREEZE.md §2/§4; "
                  "migrations row 'Stop Engine' (SHADOW WIRED)"),
    dict(issue="Structure-based stop definition is undefined (deferred in Phase 1)",
         decision_needed="Define the structure level rule (definition, lookback, buffer, and its interaction "
                         "with the ATR stop: tighter-of / wider-of / switch-over)",
         current_state="No reliable canonical structure-stop implementation exists in the repo; "
                       "StopPlan.structure_level is reserved but unused and no structure stop is computed",
         proposed_action="Take this as a separate architecture decision BEFORE implementation "
                         "(do not invent prior-swing-low / 20D-pivot rules ad hoc)",
         affected_modules="production/stops/",
         status="OPEN", priority="MEDIUM",
         evidence="doc:production/stops/STOP_ENGINE_BACKLOG.md B1"),
    dict(issue="current_stop_price is computed but never persisted",
         decision_needed="Decide the Position / Ledger / State redesign that persists the current stop",
         current_state="The Stop Engine returns current_stop_price but production keeps re-deriving the trailing "
                       "stop from highest_since_entry in _exit_check; src/state/state.py is untouched",
         proposed_action="Decide whether current_stop_price + stop_reason_code + stop_updated_session become "
                         "part of the persisted position (and how existing state files migrate)",
         affected_modules="production/stops/; production/contracts/position.py; src/state/state.py",
         status="OPEN", priority="MEDIUM",
         evidence="doc:production/stops/STOP_ENGINE_BACKLOG.md B2"),

    # ---- Phase 2 (2026-09-29): Exit Engine ----
    dict(issue="Exit Engine is implemented but NOT wired (Phase 2 isolation)",
         decision_needed="SUPERSEDED — Phase 3 wired Stop + Exit into the production graph in shadow mode",
         current_state="RESOLVED 2026-09-30 by Phase 3. production/exits/ is imported by "
                       "production/pipeline.py through the lazy Phase-3 adapter/shadow surface "
                       "(production_used=1, production_decision_authority=SHADOW_ONLY). Legacy "
                       "src/portfolio/portfolio_manager.py::_exit_check remains the decision source "
                       "while exit_engine_mode='legacy'; shadow reproduced 1088/1088 legacy decisions "
                       "with 0 divergences",
         proposed_action="No further action; the remaining question is the live shadow gate and whether "
                         "mode='new' is ever enabled",
         affected_modules="production/exits/; production/contracts/exit.py; production/pipeline.py",
         status="RESOLVED", priority="HIGH",
         evidence="doc:reports/phase3_wiring_acceptance_2026-09-30.md; "
                  "migrations row 'Exit Engine' (SHADOW WIRED — LEGACY STILL AUTHORITATIVE)"),
    dict(issue="Legacy position has TWO stop levels; mapping into StopPlan must be confirmed",
         decision_needed="Confirm that STOP_LOSS reads StopPlan.initial_stop_price while TRAILING_STOP reads "
                         "StopPlan.current_stop_price",
         current_state="The legacy oracle uses pos['stop_price'] (static, never updated) for STOP_LOSS and a "
                       "separately derived trail level for TRAILING_STOP. Phase 2 maps these to the single "
                       "StopPlan's initial_stop_price / current_stop_price, so the Exit Engine recalculates "
                       "nothing (one owner) yet still reproduces both legacy levels exactly",
         proposed_action="Human sign-off of this interpretation; if rejected, decide how a single StopPlan should "
                         "express an updateable stop for both rules",
         affected_modules="production/exits/rules.py; production/contracts/stop.py",
         status="OPEN", priority="MEDIUM",
         evidence="doc:reports/phase2_exit_engine_2026-09-29.md §D/§K; "
                  "doc:production/exits/DESIGN_NOTE.md §6"),
    dict(issue="Live 20-session shadow gate is not yet closed (backtest coverage only)",
         decision_needed="Accumulate qualifying live/paper sessions; do NOT claim live validation",
         current_state="PENDING, not failed. Human-approved gate definition (2026-10-01 §6): "
                       "20 consecutive qualifying trading sessions AND 0 divergence AND 0 engine errors "
                       "AND actual held-position evaluations > 0. An idle session (process ran, nothing "
                       "held/evaluated) does NOT count. Historical parity PASS; historical coverage "
                       "PASS; live shadow validation PENDING; production cut-over NOT APPROVED",
         proposed_action="Run `python production/main.py --exit-engine-mode shadow` daily and let "
                         "production/reporting/shadow_monitor.py track the streak; a session qualifies "
                         "only when the new engine evaluated >= 1 held position",
         affected_modules="production/main.py; production/reporting/shadow_monitor.py",
         status="OPEN", priority="HIGH",
         evidence="human sign-off 2026-10-01 §6/§8; "
                  "reports/phase3_shadow_coverage_2026-09-30.json (gate.status = PENDING)"),
    dict(issue="exit_engine_mode='new' is implemented but must not be enabled without approval",
         decision_needed="Explicit human approval to flip production to mode='new' (and the rollback "
                         "runbook)",
         current_state="mode='new' is implemented and unit-tested (the ExitDecision drives the "
                       "proposal) but production runs mode='legacy'; nothing enables it automatically",
         proposed_action="Keep 'legacy' until the shadow gate closes AND a human approves; rollback is "
                         "the single config flag back to 'legacy' (no code revert)",
         affected_modules="production/config.py; production/pipeline.py",
         status="OPEN", priority="HIGH",
         evidence="doc:production/STOP_EXIT_V1_FREEZE.md §4; "
                  "test:production/tests/test_wiring_safety.py (rollback test)"),
    dict(issue="production_used vs production_decision_authority were conflated",
         decision_needed="Keep the two questions separate in the registry",
         current_state="RESOLVED 2026-09-30 — `modules.production_decision_authority` was added "
                       "(PRIMARY / SHADOW_ONLY / NONE). Phase 3 genuinely imports the Stop/Exit/Contract "
                       "modules into the production graph, so their `production_used` is now 1 (the "
                       "Phase-1/2 'isolated -> 0' overrides were removed rather than kept as a false "
                       "statement), while their authority is SHADOW_ONLY because the legacy oracle still "
                       "decides in legacy/shadow mode",
         proposed_action="Query authority, not usage, when asking 'what decides?'. "
                         "meta.production_exit_engine_mode records the mode actually in force",
         affected_modules="control_center/registry_db.py; control_center/classify.py",
         status="RESOLVED", priority="HIGH",
         evidence="modules.production_decision_authority; migrate via "
                  "registry_db.ADDED_COLUMNS (idempotent ALTER TABLE)"),
    dict(issue="`production_used` for test files under production/ relies on a path heuristic",
         decision_needed="Decide whether production_used should mean 'in the production import closure' "
                         "for every module type",
         current_state="classify_modules() marks anything under production/ as production_used=1 unless a "
                       "curated override says otherwise. The Phase-1/2 test entries (and the Phase-3 ones) "
                       "are overridden to 0, but the four pre-Phase-1 production tests still show 1",
         proposed_action="A future registry-accuracy pass could drop the blanket `production/` prefix rule "
                         "in favour of the dependency closure + curation; NOT done now to avoid "
                         "destabilising 100+ unrelated rows mid-phase",
         affected_modules="control_center/classify.py (classify_modules)",
         status="OPEN", priority="LOW",
         evidence="modules rows for production/tests/test_{datasource,pipeline,backtest,production}.py"),
    dict(issue="BLOCKER: no executable path can run production in shadow mode, so the §10 live gate "
               "cannot start accumulating",
         decision_needed="Choose how the exit-engine mode is selected at the live entrypoint",
         current_state="RESOLVED 2026-10-01 — human approved OPTION A. `production/main.py` gained "
                       "`--exit-engine-mode {legacy,shadow,new}` (default legacy; argparse rejects "
                       "unknown values; no env-var-only mechanism; no second operator entrypoint). "
                       "`python production/main.py --exit-engine-mode shadow` can now accumulate the "
                       "live/paper shadow sessions",
         proposed_action="Run the daily briefing with --exit-engine-mode shadow until the gate closes; "
                         "do NOT enable 'new'",
         affected_modules="production/main.py; production/config.py",
         status="RESOLVED", priority="HIGH",
         evidence="human approval 2026-10-01 (sign-off §5, option A); "
                  "test:production/tests/test_wiring_safety.py::test_16_cli_exit_engine_mode_flag"),
    dict(issue="Phase 4 D1-D6 diagnostics complete — the next implementation target must be chosen "
               "from evidence, not intuition",
         decision_needed="Approve or reject the recommended target, and pick which lever is in scope",
         current_state="RANKED (rule fixed in advance: attributable controllable gap -> interface "
                       "cost -> falsifiability). #1 Capital deployment mechanics — 506/669 valid "
                       "setups never sized, 413 (81.6%) because the risk budget floors to zero shares "
                       "(mean budget $4.95), realised risk 0.33% vs the 1% target, average exposure "
                       "17.16%; the -28.27pp benchmark gap is explained by exposure (SPY at the "
                       "system's own daily exposure returns +7.37% vs the system's +7.99%). "
                       "#2 Entry gap quality (monotone: gap<0% -> +0.399R; the allowed 0.5-2% cohort "
                       "loses). #3 Exit target efficiency (TP 1.667R vs 2.25R MFE; upper bound only). "
                       "#4 max_open_positions (magnitude unobservable). #5 Regime calibration (NOT "
                       "proposed: BULL-regime entries averaged -0.321R, so the divergence cap looks "
                       "protective)",
         proposed_action="Decide which lever is in scope for target #1 — (a) sizing arithmetic/"
                         "flooring, (b) risk_per_trade, (c) regime base multipliers or the divergence "
                         "cap (FROZEN), (d) max_open_positions, (e) the capital base itself (~$1,282). "
                         "Also decide whether the frozen Setup v1 `max_entry_gap_pct` experiment "
                         "(target #2) may proceed",
         affected_modules="production/risk/risk.py; src/agents/risk_manager.py; "
                          "regime_dual_engine/config.py; production/config.py; src/agents/setup_agent.py",
         status="OPEN", priority="HIGH",
         evidence="doc:reports/phase4_diagnostics_2026-10-01.md; "
                  "data:reports/phase4_diag_data_2026-10-01.json"),
    dict(issue="Phase-4 verification REFUTED one diagnostic claim (entry-gap monotonicity)",
         decision_needed="Accept the correction and park the entry-gap lever until it has a "
                         "stability result",
         current_state="RESOLVED AS A CORRECTION 2026-10-01 — `production/tests/phase4_verify.py` "
                       "(V4) shows the full-window entry-gap monotonicity is a 2024 effect: in 2025 "
                       "the ordering reverses (the currently-allowed 1-2% cohort averaged +0.618R), "
                       "with only 8-20 trades per cell. V1 also replaced the hand-rolled rejection "
                       "labels with the real risk engine's own verdict (669/669 attempts, 99.7% "
                       "agreement; cause = '風險預算不足' for 505/506 rejections). V2 showed the "
                       "exposure-vs-selection decomposition is robust across five windows "
                       "(selection -1.69 to +1.93pp vs raw gaps of -5 to -22pp)",
         proposed_action="Entry-gap threshold (candidate #2) is downgraded to 'measure stability "
                         "first'; it must not be used to justify a frozen-Setup-v1 change yet",
         affected_modules="src/agents/setup_agent.py (untouched); production/tests/phase4_verify.py",
         status="RESOLVED", priority="HIGH",
         evidence="doc:reports/phase4_diagnostics_2026-10-01.md §V4 + §D3; "
                  "data:reports/phase4_verify_2026-10-01.json"),
    dict(issue="Diagnostic record-quality gaps limit future analysis",
         decision_needed="Decide whether to persist the missing fields (a small, strategy-neutral "
                         "task) — a concrete patch proposal now exists",
         current_state="Observed in the Phase-4 run: `holding_days` is null for all 151 trades; "
                       "`components` is empty; `extension_from_pivot_pct` is null for all 151 trades; "
                       "the risk gate stores only `allow` (not the engine's `reasoning`); "
                       "`veto_flags` and the equity/cash used by the entries phase are not persisted, "
                       "so diagnostics must reconstruct them. A proposal with exact (unapplied) "
                       "patches exists: reports/phase5_record_quality_proposal_2026-10-01.md",
         proposed_action="Approve the proposal (P1 holding_days, P2 risk reasoning, P3 entries-phase "
                         "equity/cash, P4 veto_flags) — all additive, touching only "
                         "production/backtest.py and production/pipeline.py, with no strategy "
                         "semantics change. Verify by confirming the backtest summary block stays "
                         "byte-identical afterwards",
         affected_modules="production/backtest.py; production/pipeline.py",
         status="OPEN", priority="LOW",
         evidence="doc:reports/phase5_record_quality_proposal_2026-10-01.md"),
    dict(issue="CONTRACT MISMATCH: the frozen Setup v1 extension filter cannot fire in production",
         decision_needed="Decide how to handle a documented frozen clause that is inert",
         current_state="`max_extension_from_pivot_pct = 0.03` is documented in SETUP_V1_FREEZE.md and "
                       "in the backtest's execution conventions, but its input `prior_high20` is "
                       "produced ONLY by the breakout path (src/agents/setup_agent.py:94, surfaced at "
                       ":165/:321); Setup v1 enables only `pullback`, so the dispatcher always "
                       "returns None -> production/backtest.py computes ext=None -> the guard never "
                       "fires. Verified live (evaluate_setup returns prior_high20=None for valid "
                       "pullback setups). Same root cause makes the `components` attribution field "
                       "permanently {} for v1 trades. NO behaviour change and no trade was affected: "
                       "the clause simply cannot block",
         proposed_action="Choose one: (a) record the clause as INERT in the freeze document (no code "
                         "change, minimum), (b) define a pullback-equivalent pivot (e.g. the 20-day "
                         "high without the breakout requirement) and wire it — a real Setup v1 "
                         "amendment, or (c) remove the clause from the documented contract. Any of "
                         "(b)/(c) is an architecture decision: src/agents/setup_agent.py is the "
                         "canonical frozen Setup v1 implementation",
         affected_modules="src/agents/setup_agent.py (FROZEN); src/agents/SETUP_V1_FREEZE.md; "
                          "production/backtest.py; production/pipeline.py",
         status="OPEN", priority="HIGH",
         evidence="doc:reports/phase4_diagnostics_2026-10-01.md §D3a; "
                  "doc:reports/phase5_record_quality_proposal_2026-10-01.md P6/P7; "
                  "doc:reports/freeze_conformance_audit_2026-10-01.md (full audit: 13 OPERATIVE / "
                  "9 TEST-VERIFIED / 1 INERT / 2 CLARITY / 0 MISMATCH; the extension filter is the "
                  "ONLY clause that cannot fire)"),
    dict(issue="Two freeze clauses are accurate only under one reading (documentation precision)",
         decision_needed="Decide whether to tighten the two freeze documents (documentation-only "
                         "edits, permitted without unfreezing)",
         current_state="C1 SETUP_V1 clause 8 is written as a conjunction of four conditions "
                       "(EMA pullback + volume contraction + reversal candle + RS strength) but the "
                       "implementation is a weighted SUM with threshold 0.50 — structure 0.30 + "
                       "nearEMA 0.25 = 0.55 is valid with neither volume contraction nor a reversal "
                       "candle; and 'RS 強勢' is a 50-day-high proximity proxy (price >= 0.9 x "
                       "high50), not a cross-sectional RS-vs-SPY test (that lives in the screener). "
                       "C2 REGIME_V1 clause 2 states a fixed 'x50 / x0.5' formula while the code is "
                       "a generalised weighted mean (identical while the weights stay 0.5/0.5). "
                       "NEITHER is a behaviour defect",
         proposed_action="Edit the two freeze documents to state the implemented semantics (a "
                         "docs-only change, explicitly allowed by both freeze contracts §3), so a "
                         "future reader cannot mis-infer 'all four required' or a fixed formula",
         affected_modules="src/agents/SETUP_V1_FREEZE.md; regime_dual_engine/REGIME_V1_FREEZE.md",
         status="OPEN", priority="LOW",
         evidence="doc:reports/freeze_conformance_audit_2026-10-01.md §4"),
    dict(issue="Step 0 executed: the risk-budget cap is PROTECTIVE, so the primary research target "
               "moves from deployment to exit efficiency",
         decision_needed="Accept the Step-0 verdict and the re-ranking; decide the deployment "
                         "question as a preference rather than a research item",
         current_state="RESOLVED AS A FINDING 2026-10-01 — the mechanism was validated first (the "
                       "same machinery reproduced 148/151 realised trades, 98.0%, on both exit "
                       "reason and R). The 505 budget-rejected valid setups were then traded "
                       "hypothetically with next-open entry and the FROZEN Exit Engine: avg R "
                       "-0.270 (median -1.0, 64.6% hit -1R, 65% stopped out) vs the accepted "
                       "cohort's +0.182; de-duplicated (n=169) still -0.091. Rejection is "
                       "systematically biased to the high-ATR/high-price band (price $150-400: "
                       "-0.363R over 241 rejections), i.e. the integer-share floor has been acting "
                       "as an accidental quality filter. Pre-registered rule (written before the "
                       "run): marginal expectancy <= 0 -> STOP",
         proposed_action="(1) Do NOT loosen the regime multipliers or risk_per_trade to admit more "
                         "setups. (2) Reclassify the residual under-deployment (0.33% realised risk "
                         "vs 1% intent, 17% exposure) as a LEVERAGE PREFERENCE: constant scaling "
                         "moves return and drawdown proportionally but not Sharpe, so it is not an "
                         "alpha lever. (3) New primary candidate = Exit target efficiency "
                         "(take_profit_atr_mult 2.5 -> 3.5) — needs an architecture decision "
                         "because Stop/Exit v1 is frozen. (4) NOTE the strategic finding: in this "
                         "window SPY had the higher Sharpe (1.218 vs 0.935) and a -18.76% MaxDD, and "
                         "matching SPY's return by leverage needs ~4.54x exposure implying ~-27% "
                         "MaxDD — so the system's demonstrated proposition is DRAWDOWN CONTROL, not "
                         "risk-adjusted outperformance",
         affected_modules="src/agents/risk_manager.py; regime_dual_engine/config.py (both UNTOUCHED); "
                          "production/tests/phase5_step0_marginal_cohort.py; "
                          "production/tests/phase5_step0b_lever_cohorts.py",
         status="RESOLVED", priority="HIGH",
         evidence="**SUPERSEDED 2026-10-01 by the PIT correction — the conclusion REVERSED.** Those "
                  "numbers were computed on a leak-contaminated regime (every lever's newly-admitted "
                  "cohort is now POSITIVE: (b) 1.5% +0.349, (b2) 2% +0.133, (c) cap removed +0.146, "
                  "(c2) SIDEWAYS 0.75 +0.326, (e) capital x2 +0.160). The aggregate marginal cohort "
                  "also collapsed from -0.270R to -0.0069R (de-duplicated +0.086R), so the "
                  "pre-registered STOP verdict is now marginal. Do NOT cite the 'protective cap' "
                  "reading. Corrected evidence: "
                  "doc:reports/pit_correction_and_rebaseline_2026-10-01.md §F; "
                  "data:reports/phase5_step0_marginal_cohort_pitcorrected_2026-10-01.json; "
                  "data:reports/phase5_step0b_lever_cohorts_pitcorrected_2026-10-01.json; "
                  "ORIGINAL (leaky): doc:reports/phase5_step0_report_2026-10-01.md"),
    dict(issue="Research §11 gate: is the Stop/Exit research harness trustworthy before parameter "
               "experiments?",
         decision_needed="Confirm the harness may be used for controlled exit/stop research",
         current_state="PASS 2026-10-01. Direct replay parity re-verified (151 trades / 1073 bars / 0 "
                       "divergences; 0 adapter-vs-direct mismatches; 151/151 reconciliation). The "
                       "Step-0 control reproduced 148/148 comparable trades on both exit reason and R "
                       "(±0.05). The 3 previously-unexplained mismatches are ALL classified as "
                       "HARNESS_HORIZON_GUARD_ARTIFACT: the Step-0 simulator breaks at `held > 30` "
                       "calendar days BEFORE evaluating the exit, so when the 30-day boundary falls "
                       "on a weekend (SYF, ROL, WRB) it never sees the session where production's "
                       "TIME_STOP (held >= 30) fires at held=31/32. Proven by re-simulating with a "
                       "larger horizon, which reproduces each recorded exit exactly (SYF 1.1727 vs "
                       "1.173; ROL 0.1088 vs 0.109; WRB 0.0541 vs 0.054). Impact = 3/151 (2.0%) of "
                       "control trades; it does not change the Step-0 verdict (-0.270R vs +0.182R)",
         proposed_action="None — the artifact is a simulator guard, not an engine/data/reference-"
                         "price problem. If the Step-0 numbers are ever re-used, widen the simulator "
                         "horizon or evaluate the exit before the break",
         affected_modules="research/step0_harness_audit.py; production/tests/phase5_step0_marginal_cohort.py",
         status="RESOLVED", priority="HIGH",
         evidence="data:reports/step0_harness_audit_2026-10-01.json; "
                  "data:reports/research_replay_parity_2026-10-01.json; "
                  "doc:reports/return_bottleneck_report_2026-10-01.md §11"),
    dict(issue="R1 executed: the frozen 2.5 ATR take-profit appears TOO TIGHT — a research finding, "
               "NOT a production change",
         decision_needed="Decide whether to (a) replicate on an independent window, (b) unfreeze "
                         "STOP_EXIT_V1 for a take_profit_atr_mult change, or (c) park the finding",
         current_state="OPEN 2026-10-01. Controlled single-variable sweep of take_profit_atr_mult "
                       "{2.0,2.5,3.0,3.5,4.0} + a response-shape diagnostic {4.5,5.0} through the "
                       "unchanged ProductionBacktest (mode=legacy, universe/data/costs/execution "
                       "frozen). Result: a smooth, BRACKETED plateau — return peaks at 4.5 (14.39% vs "
                       "baseline 7.99%), Sharpe/PF/avgR/Calmar peak at 4.0 (Sharpe 1.468, MaxDD "
                       "-4.50%, PF 1.63), and it declines at 5.0. Every one of the four sub-periods "
                       "shows TP >= 3.5 beating 2.5 (12/12 cells) and avg R agrees in 4/4 windows — "
                       "unlike the entry-gap signal that V4 refuted. Mechanism is mechanical: wider "
                       "target converts TAKE_PROFIT hits into TRAILING_STOP exits at higher R (TP "
                       "fills 1.333R -> 3.333R, TP count 78 -> 22). Exposure barely moves "
                       "(17.16% -> 18.63%) so it is not an exposure artefact, and avg R rises in R "
                       "units so it is not leverage. CAVEATS: no genuine out-of-sample (both "
                       "sub-periods informed the conclusion); one bull regime only; no significance "
                       "test; turnover -15% (151 -> 128); `take_profit_atr_mult` is INSIDE the frozen "
                       "STOP_EXIT_V1 contract, so a production change needs an architecture revision. "
                       "⚠️ WITHDRAWN 2026-10-01 pending re-derivation: the point-in-time audit "
                       "(reports/pit_breadth_leak_2026-10-01.md) found Regime v1's breadth engine is "
                       "not PIT in replay, and with a PIT-correct regime the same grid becomes a "
                       "non-monotone zig-zag (TP 2.5/3.0/3.5/4.0 -> +5.57/-1.43/-0.41/+7.38%), so the "
                       "monotone improvement reported here was an artefact of that leak. R1 must be "
                       "re-run on a corrected regime before any conclusion",
         proposed_action="(1) Replicate R1 on an independent window (e.g. 2018-2023) through the "
                         "same harness — this is the single action that would make the evidence "
                         "usable. (2) If the direction survives, raise an explicit STOP_EXIT_V1 "
                         "revision decision. (3) Do NOT combine with a trailing (S2) or capacity "
                         "(R3) change in one step; R1's gain ARRIVES through the trailing exit and "
                         "raises the sessions-at-capacity share from 17.2% to ~22%",
         affected_modules="research/run_tp_grid.py; research/harness.py; production/config.py "
                          "(take_profit_atr_mult — UNTOUCHED at 2.5)",
         status="RESOLVED", priority="HIGH",
         evidence="**RESOLVED 2026-10-01: re-run on the PIT-corrected regime -> INCONCLUSIVE, no revision "
                  "justified.** Corrected pre-registered grid (take_profit_atr_mult 2.0/2.5/3.0/3.5/4.0): "
                  "return -3.32 / +5.57 / -1.43 / -0.41 / +7.38 %; Sharpe -0.264 / 0.513 / -0.084 / 0.005 / "
                  "0.612; MaxDD -11.24 / -10.39 / -11.29 / -10.78 / -10.70 %; trades 175/168/156/151/141. "
                  "The response is a ZIG-ZAG (2.5 good, 3.0/3.5 negative, 4.0 best) with a valley between "
                  "two separated good points, the winner is NOT supported by its neighbours, and 2025 is "
                  "negative for 4 of 5 settings -> the isolated-peak pattern the spec §14 warns against. "
                  "The earlier monotone 'wider is better' was the leak artefact; the frozen 2.5 ATR target "
                  "is NOT demonstrated to be wrong, and the exit-path question moves to the untested "
                  "stop/trailing half. "
                  "doc:reports/pit_correction_and_rebaseline_2026-10-01.md §G; "
                  "data:reports/research_tp_grid_pitcorrected_2026-10-01.json"),
    dict(issue="LOOK-AHEAD: Regime v1 Engine B (breadth) is NOT point-in-time in historical replay "
               "— it always evaluates the cache tail",
         decision_needed="Decide how to repair the PIT contract for the breadth engine, and how to "
                         "re-baseline the historical results that depended on it",
         current_state="FOUND 2026-10-01 (read-only audit, no file changed). "
                       "regime_dual_engine/pit_breadth_data.get_breadth() returns the WHOLE cached CSV "
                       "whenever the cache exists (`if os.path.exists(path) and not rebuild: return "
                       "pd.read_csv(path, ...)`) — the `end` argument only matters when rebuilding. "
                       "production/pipeline.py::run_daily calls load_breadth(cfg, end=as_of) and then "
                       "compute_market_regime(spy_df, breadth_df, cfg); "
                       "regime_dual_engine/engine.py::compute_regime_decision reads "
                       "breadth_df['pct_above_50dma'].iloc[-1] and the trailing percentile window, i.e. "
                       "the TAIL of the 2016..2025 cache. Measured: the shipped breadth diagnostics are "
                       "IDENTICAL for as_of = 2018-06-29, 2020-03-31, 2022-06-30, 2024-01-31, "
                       "2024-06-28, 2025-01-31, 2025-07-31 (percentile 41.746, breadth_now 57.23), "
                       "whereas the PIT-correct values range 11.79..55.99 and the regime label differs "
                       "at some dates (2022-06-30: shipped BEAR vs PIT BULL). At 2025-07-31 the two "
                       "coincide — confirming the mechanism. Engine A (HMM) is unaffected: it consumes "
                       "the PIT SPY slice. NET EFFECT in replay: 50% of the frozen Regime v1 composite "
                       "is a CONSTANT, so the backtest effectively runs an HMM-only regime with a fixed "
                       "breadth offset and a fixed divergence-flag state",
         proposed_action="(1) Treat this as a PIT-contract defect of the frozen Regime v1 ADAPTER "
                         "layer, not of the frozen decision maths: the decision function is correct "
                         "given a PIT series; the loader is what fails to slice. (2) Human decision on "
                         "the repair (slice in load_breadth, or slice in pipeline before the call). "
                         "(3) Re-baseline every historical result that consumed the leaky regime "
                         "(Phase 3 shadow coverage, Phase 4 D1-D6, Phase 5 Step 0, R1). (4) Check the "
                         "LIVE path too: with the cache ending 2025-07-31, a live run on any later date "
                         "silently uses a stale tail — it records only `breadth_rows`, not its date, so "
                         "staleness is invisible in the ledger",
         affected_modules="regime_dual_engine/pit_breadth_data.py (get_breadth); "
                          "production/pipeline.py (run_daily breadth load); "
                          "regime_dual_engine/engine.py (consumer — correct given a PIT series); "
                          "NOT changed: any file",
         status="RESOLVED", priority="HIGH",
         evidence="FIXED 2026-10-01 (human-approved, human-reviewed). Repair: a single "
                  "point-in-time guard `breadth_data.slice_to_end(df, end)` applied on BOTH the "
                  "cache and rebuild paths of BOTH loaders (`get_breadth`, `get_breadth_series`), "
                  "so any `end=as_of` request is guaranteed date <= as_of. No formula, weight, "
                  "threshold, label, divergence rule, multiplier or strategy rule was touched. "
                  "Regression protection: production/tests/test_pit_breadth.py (7 tests) pins the "
                  "exact discovered failure mode. RE-BASELINE: the corrected 2024-01-02..2025-07-31 "
                  "baseline is +5.57% / Sharpe 0.513 / MaxDD -10.39% / PF 1.13 / 168 trades / 23.39% "
                  "exposure (vs the leaky +7.99% / 0.935 / -5.98%); the former reference values are "
                  "retained only as an audit record marked SUPERSEDED. All contaminated artifacts "
                  "carry `_status: SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE`. "
                  "data:reports/pit_breadth_audit_2026-10-01.json; "
                  "doc:reports/pit_breadth_leak_2026-10-01.md; "
                  "test:production/tests/test_pit_breadth.py"),
    dict(issue="Live breadth staleness: the freshness POLICY is undefined (exposure now added, "
               "threshold not yet decided)",
         decision_needed="Decide whether stale breadth should warn, block sizing, or fail loud — "
                         "and against which tolerance",
         current_state="The ledger now records `data.breadth.tail_date` and `tail_matches_as_of` "
                       "(approved data-integrity diagnostic, no decision impact). No freshness "
                       "threshold exists anywhere in the project's data contracts; the only "
                       "analogous concept is `datasource.Provenance.stale`, which is informational "
                       "and does NOT block. So a stale breadth series is now VISIBLE but not yet "
                       "acted upon. With the PIT fix in place the series is correctly sliced up to "
                       "the cache tail; if that tail is older than the as-of session the run is "
                       "using genuinely stale market breadth",
         proposed_action="Human decision. Options: (a) warn only (current behaviour, policy "
                         "defaults to 'visible'); (b) record a decision-affecting flag but keep "
                         "sizing; (c) fail loud beyond N sessions. Do NOT implement (b)/(c) without "
                         "explicit approval — they change live decision behaviour. A tolerance "
                         "proposal is documented in reports/pit_breadth_leak_2026-10-01.md §5",
         affected_modules="production/pipeline.py (data.breadth diagnostics — added); "
                          "no trading rule changed",
         status="OPEN", priority="MEDIUM",
         evidence="doc:reports/pit_breadth_leak_2026-10-01.md §5; "
                  "doc:reports/pit_correction_and_rebaseline_2026-10-01.md §5"),
    dict(issue="Corrected research re-baseline complete — the next implementation target must be chosen "
               "on PIT-clean evidence",
         decision_needed="Approve (or reject) the recommended next step: a controlled single-variable "
                         "deployment/sizing experiment, then the capacity-observability logging change",
         current_state="OPEN 2026-10-01. The PIT fix invalidated the previous bottleneck conclusions: "
                       "the 'protective cap / negative-expectancy marginal capital' reading is REJECTED "
                       "(Step 0b: every lever now admits a POSITIVE cohort, best = risk_per_trade "
                       "1.0%->1.5% at +0.349R on 101 setups), and R1 is INCONCLUSIVE (no robust "
                       "take-profit improvement; the frozen 2.5 ATR target is not demonstrated wrong). "
                       "What remains CONFIRMED: the benchmark gap is exposure (selection gap -0.18pp on "
                       "a raw -30.69pp), the edge is thin (+0.188R, PF 1.13, median trade -0.562R), the "
                       "account size floors ~31% of valid setups even at full multiplier, exit-target "
                       "give-back is real but not actionable, and the whole corrected return is a 2024 "
                       "effect (2024 +6.50%, 2025 -0.88%)",
         proposed_action="(1) DEFERRED, now executed and FAILED — see the lever experiments below. "
                         "(2) Approve the logging change (R7 + the blocked-entry snapshot, proposal: "
                         "reports/capacity_observability_proposal_2026-10-01.md) so entry and "
                         "blocked cohorts become measurable. (3) Then test the STOP/TRAILING half of "
                         "the exit path (S1-S3) on corrected data — the only exit dimension never "
                         "tested. Do NOT loosen regime multipliers, extend the TP grid, or add "
                         "leverage",
         affected_modules="production/config.py (risk_per_trade / max_open_positions — BOTH "
                          "UNCHANGED, tested in memory only); "
                          "research/run_lever_grid.py; production/pipeline.py (logging only, if approved)",
         status="OPEN", priority="HIGH",
         evidence="**Deployment levers TESTED 2026-10-01 and BOTH FAILED their pre-registered criteria** "
                  "(doc:reports/lever_experiments_pitcorrected_2026-10-01.md). "
                  "risk_per_trade 1% -> 1.25% / 1.5%: return +11.43% / +12.00% and Sharpe 0.818 / 0.774 "
                  "(C1 PASS) BUT MaxDD -14.15% / -13.39% breaches the -10.39% limit (C2 FAIL) and the "
                  "gain is entirely a 2024 effect while 2025 does not improve (C3 FAIL). "
                  "max_open_positions 5 -> 6: return 5.57% -> 0.63%, Sharpe 0.513 -> 0.090, PF 1.13 -> "
                  "1.01, MaxDD -12.93%, both sub-periods worse (FAILS all three). "
                  "Methodological result: a positive marginal-cohort simulation (Step-0b) did NOT survive "
                  "contact with a portfolio backtest. "
                  "doc:reports/pit_correction_and_rebaseline_2026-10-01.md §H/§I; "
                  "data:reports/research_lever_risk_per_trade_pitcorrected_2026-10-01.json; "
                  "data:reports/research_lever_max_open_positions_pitcorrected_2026-10-01.json; "
                  "doc:reports/experiment_matrix_pitcorrected_2026-10-01.md"),
]


# --------------------------------------------------------------------------
# Reachability (usage) tracing
# --------------------------------------------------------------------------
def _adjacency(deps: list[dict]) -> dict:
    adj = defaultdict(set)
    for d in deps:
        adj[d["source_module"]].add(d["target_module"])
    return adj


def _closure(entries: list[str], adj: dict) -> set:
    seen, stack = set(), list(entries)
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nxt in adj.get(cur, ()):
            if nxt not in seen:
                stack.append(nxt)
    return seen


# --------------------------------------------------------------------------
# Per-module classification
# --------------------------------------------------------------------------
def _type_from_path(path: str) -> str:
    p = path.lower()
    base = os.path.basename(p)
    if p.startswith("control_center/"):
        return "utility"
    if base.startswith("test_") or "/tests/" in p:
        return "test"
    if base == "__init__.py":
        return "utility"
    if base == "config.py" or p.endswith("/config.py"):
        return "config"
    if "production/" in p:
        return "production"
    if p.startswith("regime_dual_engine/"):
        if re.search(r"validation_|audit_|robustness_|ablation_|build_review", base):
            return "audit"
        return "core"
    if re.search(r"backtest|_bt", base):
        return "backtest"
    if re.search(r"v32_|experiment", base):
        return "experiment"
    if re.search(r"hmm_|hostile_|comparison|ablation_test|diagnostic", base):
        return "research"
    if p.startswith("src/"):
        return "core"
    if base.endswith(".md"):
        return "documentation"
    return "UNKNOWN"


def _status_from_path(path: str, t: str) -> str:
    p = path.lower()
    if t == "test":
        return "ACTIVE"
    if p.startswith("control_center/"):
        return "ACTIVE"
    if p.startswith("production/"):
        return "ACTIVE"
    if t == "config":
        return "UNKNOWN"
    if t == "production":
        return "ACTIVE"
    if t == "audit":
        return "ACTIVE"
    if t == "experiment":
        return "EXPERIMENTAL"
    if t == "research":
        return "RESEARCH"
    if t == "backtest":
        return "RESEARCH"
    if t == "core":
        return "UNKNOWN"  # refined later by usage evidence
    return "UNKNOWN"


def classify_modules(scan: dict) -> list[dict]:
    repo = scan["label"]
    deps = scan["deps"]
    adj = _adjacency(deps)
    prod_used = _closure(PRODUCTION_ENTRIES, adj)
    res_used = _closure(RESEARCH_ENTRIES, adj)
    bt_used = _closure(BACKTEST_ENTRIES, adj)
    importers = defaultdict(set)
    for d in deps:
        importers[d["target_module"]].add(d["source_module"])

    rows = []
    for m in scan["modules"]:
        path = m["path"]
        t = _type_from_path(path)
        status = _status_from_path(path, t)
        cur = CURATED.get(path, {})
        if cur.get("type"):
            t = cur["type"]
        if cur.get("status"):
            status = cur["status"]

        prod = int(path in prod_used or path.startswith("production/"))
        res = int(path in res_used)
        bt = int(path in bt_used)
        if path in (PRODUCTION_ENTRIES):
            prod = 1
        # explicit curated override: a file may live under production/ while the
        # production pipeline does NOT import it (isolated Phase 1 modules such as
        # production/stops/ and production/contracts/).
        if "production_used" in cur:
            prod = int(bool(cur["production_used"]))
        # core modules reachable only from research -> legacy-ish, keep computed
        if t == "core" and not prod and res and path.startswith("src/"):
            if status == "UNKNOWN":
                status = "LEGACY" if path in CURATED and CURATED[path].get("legacy") else "ACTIVE"
        if t == "core" and prod and status == "UNKNOWN":
            status = "ACTIVE"

        canonical = int(cur.get("canonical", 0))
        legacy = int(cur.get("legacy", 0))
        experimental = int(t == "experiment")
        replacement = cur.get("replacement_module")
        confidence = cur.get("confidence")
        evidence = cur.get("evidence")
        purpose = cur.get("purpose")

        # evidence from usage
        ev_parts = []
        if evidence:
            ev_parts.append(evidence)
        if importers.get(path):
            ev_parts.append("imported_by:" + ",".join(sorted(importers[path])[:4]))
        if not importers.get(path) and t not in ("test", "utility", "documentation"):
            ev_parts.append("no_importer_found")

        if not confidence:
            confidence = "MEDIUM" if (prod or res) else "LOW"
        if not purpose:
            first = (m.get("docstring") or "").splitlines()
            purpose = first[0][:160] if first and first[0] else "UNKNOWN (no docstring)"
        if status == "UNKNOWN" and path.startswith("src/"):
            status = "LEGACY" if not prod else "ACTIVE"

        # Flag for human review only when the registry genuinely lacks evidence.
        needs_review = int(status == "UNKNOWN" or t == "UNKNOWN" or confidence == "LOW")

        rows.append({
            "repo": repo, "path": path, "filename": m["filename"],
            "module_name": m["module_name"], "ext": m["ext"],
            "type": t, "status": status,
            "architecture_layer": architecture_layer(path),
            "subsystem": cur.get("subsystem") or SUBSYSTEM_BY_LAYER.get(architecture_layer(path), "Infrastructure"),
            "purpose": purpose,
            "description": (m.get("docstring") or "").strip()[:400],
            "canonical": canonical, "production_used": prod, "research_used": res,
            "backtest_used": bt, "legacy": legacy, "experimental": experimental,
            "production_decision_authority": decision_authority(path, cur),
            "replacement_module": replacement,
            "confidence": confidence, "evidence": " ; ".join(ev_parts),
            "needs_review": needs_review,
            "owner": "FOlegend",
            "created_date": None, "last_modified": m["last_modified"],
            "size_bytes": m["size_bytes"], "lines": m["lines"],
            "content_hash": m["content_hash"],
            "has_main_guard": m["has_main_guard"], "is_cli_entry": m["is_cli_entry"],
            "docstring": (m.get("docstring") or "")[:600],
            "human_type": None, "human_status": None, "human_canonical": None,
            "human_note": None, "human_locked": 0,
            "notes": m.get("parse_error") and f"PARSE ERROR: {m['parse_error']}" or None,
        })
    return rows


# --------------------------------------------------------------------------
# Config extraction
# --------------------------------------------------------------------------
def extract_configs(root: str, scan: dict) -> list[dict]:
    """Parse dataclass fields in *config*.py files (static, no execution)."""
    deps = scan["deps"]
    importers = defaultdict(set)
    for d in deps:
        importers[d["target_module"]].add(d["source_module"])

    rows = []
    for m in scan["modules"]:
        path = m["path"]
        if not (os.path.basename(path) == "config.py" or path.endswith("/config.py")):
            continue
        abspath = os.path.join(root, path)
        try:
            with open(abspath, "r", encoding="utf-8", errors="replace") as f:
                src = f.read()
            tree = ast.parse(src)
        except Exception:
            continue
        lines = src.splitlines()
        used_by = sorted(importers.get(path, []))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                name = default = None
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    default = stmt.value
                elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    name = stmt.targets[0].id
                    default = stmt.value
                if not name or name.startswith("_") or name in ("base_dir", "state_file", "reports_dir", "cache_dir"):
                    continue
                try:
                    val = ast.unparse(default) if default is not None else ""
                except Exception:
                    val = "?"
                comment = ""
                if 0 < stmt.lineno <= len(lines):
                    line = lines[stmt.lineno - 1]
                    if "#" in line:
                        comment = line.split("#", 1)[1].strip()
                rows.append({
                    "parameter_name": name, "file": path,
                    "default_value": val[:200], "current_value": val[:200],
                    "used_by": ",".join(used_by[:6]),
                    "purpose": comment[:200],
                    "subsystem": _config_subsystem(name),
                    "experimental": int("backtest" in name or "experiment" in comment.lower()),
                    "deprecated": 0, "duplicate_count": 0,
                    "human_note": None, "human_locked": 0,
                })
    return rows


def _config_subsystem(name: str) -> str:
    n = name.lower()
    if n.startswith("screener_"):
        return "Screener"
    if n.startswith(("regime_", "hmm_")) or "dist_day" in n:
        return "Regime"
    if n.startswith("setup_") or n in ("entry_mode", "entry_threshold", "max_entry_gap_pct", "max_extension_from_pivot_pct"):
        return "Setup"
    if n.startswith(("stop_", "take_", "trailing_", "max_holding")):
        return "Exit"
    if n.startswith(("risk_", "max_position", "max_open", "slippage", "commission", "sec_fee", "finra")):
        return "Risk/Execution"
    if n.startswith(("ema_", "rsi_", "bollinger", "atr_", "adx_", "er_", "tech_")):
        return "Indicators"
    if n.startswith(("starting_capital", "fx_", "capital_")):
        return "Capital"
    if n.startswith("backtest_"):
        return "Backtest"
    return "Other"


# --------------------------------------------------------------------------
# Pipelines (curated + evidence)
# --------------------------------------------------------------------------
def production_pipeline_rows(repo: str) -> list[dict]:
    steps = [
        ("1. DATA", "production/datasource.py", "CachedSource / YFinanceSource",
         "as_of, universe, cache", "(df, Provenance) per ticker", "2. REGIME",
         "OK", "doc:reports/production_pipeline_unified_2026-09-04.md §2/§4"),
        ("2. REGIME", "production/agents/regime.py", "compute_market_regime",
         "SPY df + PIT breadth", "regime_label, composite_score, position_size_mult, strategy_mode, veto_flags", "3. SCREENER",
         "OK", "doc:regime_dual_engine/REGIME_V1_FREEZE.md (public 5-field schema)"),
        ("3. SCREENER", "production/screener/screener.py", "screen_from_source",
         "DataSource, top_n=30", "candidates (rs_rank), status OK/EMPTY/FAILURE", "4. SETUP",
         "OK", "import:production/pipeline.py"),
        ("4. SETUP", "production/agents/setup.py", "evaluate_setup",
         "candidate df + rs_rank", "valid, setup_type, score, quality_mult, atr", "5. RISK",
         "OK", "doc:src/agents/SETUP_V1_FREEZE.md (Pullback Only)"),
        ("5. RISK", "production/risk/risk.py", "size_swing_position",
         "equity, cash, entry_px, atr, eff_size_mult", "shares, stop_price, take_profit", "6. ENTRY/EXIT",
         "OK", "import:production/pipeline.py"),
        ("6. EXIT", "production/portfolio/portfolio.py", "exit_check",
         "position, OHLC bar, tech_signal", "proposed SELL (gap-aware fill)", "7. ENTRY",
         "OK", "doc:src/agents/SETUP_V1_FREEZE.md §1 (gap-aware OHLC exit)"),
        ("7. ENTRY", "production/portfolio/portfolio.py", "build_order_buy",
         "setup signal, sizing", "proposed BUY (next-open execution)", "8. PORTFOLIO",
         "OK", "doc:reports/production_pipeline_unified_2026-09-04.md §2"),
        ("8. PORTFOLIO", "production/portfolio/portfolio.py", "constraints/mark-to-market",
         "state, orders", "updated constraints + warnings", "9. LEDGER",
         "OK", "import:production/pipeline.py"),
        ("9. STATE/LEDGER", "production/ledger.py", "write DecisionRecord",
         "DecisionRecord", "reports/decision_YYYY-MM-DD.json", "10. REPORTING",
         "OK", "doc:reports/production_pipeline_unified_2026-09-04.md §1/§6"),
        ("10. REPORTING", "production/reporting/briefing.py", "render briefing",
         "DecisionRecord", "human briefing (markdown) + orders json", "—",
         "OK", "doc:HANDOFF_2026-08-30.md §3"),
    ]
    out = []
    for i, s in enumerate(steps, start=1):
        out.append({"repo": repo, "step_order": i, "stage": s[0], "module": s[1],
                    "function": s[2], "inputs": s[3], "outputs": s[4],
                    "next_stage": s[5], "status": s[6], "evidence": s[7]})
    return out


def research_pipeline_rows(repo: str) -> list[dict]:
    steps = [
        ("1. CACHE", "historical_cache.py", "get_all_data", "universe, start/end",
         "OHLCV cache (data/cache/equities)", "2. PIT SCREEN", "OK",
         "doc:HANDOFF_2026-08-30.md §3"),
        ("2. PIT SCREEN", "screen_as_of.py", "screen_all_buckets", "all_data, rebalance dates",
         "top-N buckets per month (PIT)", "3. RESEARCH ENGINE", "OK",
         "doc:HANDOFF_2026-08-30.md §3"),
        ("3. RESEARCH ENGINE", "dynamic_universe_backtest.py", "DynamicBacktestEngine.run",
         "buckets + all_data", "equity curves + trade logs", "4. SETUP", "OK",
         "doc:reports/production_pipeline_unified_2026-09-04.md (retained as legacy research)"),
        ("4. SETUP", "src/agents/setup_agent.py", "setup_signal", "sliced df + rs_rank",
         "setup signal", "5. METRICS", "OK", "doc:src/agents/SETUP_V1_FREEZE.md"),
        ("5. METRICS", "src/backtest/engine.py", "compute_metrics", "summary",
         "Return/Sharpe/MaxDD/PF/Win", "6. REPORT", "OK", "import:dynamic_universe_backtest.py"),
        ("6. REPORT", "src/backtest/report.py", "generate_html", "summary + metrics",
         "HTML report", "—", "OK", "import:screener_backtest.py"),
        ("ALT. PROD-EQUIV", "production/backtest.py", "ProductionBacktest.run",
         "monthly PIT bucket + run_daily replay", "production-equivalent metrics", "—", "OK",
         "doc:reports/production_pipeline_unified_2026-09-04.md §7"),
    ]
    out = []
    for i, s in enumerate(steps, start=1):
        out.append({"repo": repo, "step_order": i, "stage": s[0], "module": s[1],
                    "function": s[2], "inputs": s[3], "outputs": s[4],
                    "next_stage": s[5], "status": s[6], "evidence": s[7]})
    return out


# --------------------------------------------------------------------------
# Duplicate / near-duplicate detection (never auto-deletes)
# --------------------------------------------------------------------------
GENERIC_FN = {"main", "run", "norm", "setup", "sig", "parse", "load", "helper",
              "test", "render", "build", "get", "make", "compute", "fmt"}


def detect_duplicates(scan: dict) -> list[dict]:
    repo = scan["label"]
    rows = []
    # (a) same filename in more than one location
    by_name = defaultdict(list)
    for m in scan["modules"]:
        if m["filename"] == "__init__.py":
            continue
        by_name[m["filename"]].append(m["path"])
    for fn, paths in by_name.items():
        if len(paths) > 1:
            group = f"same_filename::{fn}"
            for p in sorted(paths):
                rows.append({
                    "repo": repo, "group_name": group, "member_path": p,
                    "evidence": "identical basename in multiple locations",
                    "similarity": "name-only", "possible_canonical": "",
                    "human_decision": None, "human_note": None,
                })
    # (b) same function name defined in >=2 modules
    by_fn = defaultdict(set)
    for s in scan["symbols"]:
        if s["kind"] == "function" and s["is_public"]:
            by_fn[s["name"]].add(s["module_path"])
    for name, mods in by_fn.items():
        if len(mods) >= 2 and len(name) > 3 and name not in GENERIC_FN:
            group = f"same_function::{name}"
            for p in sorted(mods):
                rows.append({
                    "repo": repo, "group_name": group, "member_path": p,
                    "evidence": f"function '{name}' defined in {len(mods)} modules",
                    "similarity": "symbol-name", "possible_canonical": "",
                    "human_decision": None, "human_note": None,
                })
    return rows


def mark_config_duplicates(config_rows: list[dict]) -> None:
    counts = defaultdict(int)
    for r in config_rows:
        counts[r["parameter_name"]] += 1
    for r in config_rows:
        r["duplicate_count"] = counts[r["parameter_name"]] - 1
