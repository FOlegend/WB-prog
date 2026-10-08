# ARCHITECTURE — WB Swing Trading Bot

> **GENERATED FILE** — produced by `control_center/generate_docs.py`. Do not hand-edit; run `python control_center/refresh_registry.py` instead.
> Generated 2026-10-07T06:44:29 · tool v1.0.0 · commit `da9f5dcfad`

## 1. What the system does

A **human-in-the-loop US-equity swing trading bot**. It screens large-cap stocks each day, computes a market regime, detects a trade setup, sizes the position, and emits a briefing the human reviews before placing orders at a broker. Capital is locked at 10,000 HKD (~1,282 USD).

Two execution paths share the *same* decision function:
- **Production (live)**: `production/main.py` → `production/pipeline.py::run_daily`
- **Research (backtest)**: `dynamic_universe_backtest.py` (legacy engine) and `production/backtest.py` (production-equivalent replay)

## 2. Production pipeline

```
production/main.py  (thin CLI)
  └─ 1. DATA          production/datasource.py  ::  CachedSource / YFinanceSource
  └─ 2. REGIME        production/agents/regime.py  ::  compute_market_regime
  └─ 3. SCREENER      production/screener/screener.py  ::  screen_from_source
  └─ 4. SETUP         production/agents/setup.py  ::  evaluate_setup
  └─ 5. RISK          production/risk/risk.py  ::  size_swing_position
  └─ 6. EXIT          production/portfolio/portfolio.py  ::  exit_check
  └─ 7. ENTRY         production/portfolio/portfolio.py  ::  build_order_buy
  └─ 8. PORTFOLIO     production/portfolio/portfolio.py  ::  constraints/mark-to-market
  └─ 9. STATE/LEDGER  production/ledger.py  ::  write DecisionRecord
  └─ 10. REPORTING    production/reporting/briefing.py  ::  render briefing
     └─ production/ledger.py → reports/decision_YYYY-MM-DD.json
     └─ production/reporting/briefing.py → human briefing
```

Frozen contracts: regime_dual_engine/REGIME_V1_FREEZE.md (Regime v1); src/agents/SETUP_V1_FREEZE.md (Setup v1 = Pullback Only); production/STOP_EXIT_V1_FREEZE.md (Stop + Exit v1, Phase-3 wiring)

| # | Stage | Module | Function | Next | Status |
|---|---|---|---|---|---|
| 1 | 1. DATA | `production/datasource.py` | `CachedSource / YFinanceSource` | 2. REGIME | OK |
| 2 | 2. REGIME | `production/agents/regime.py` | `compute_market_regime` | 3. SCREENER | OK |
| 3 | 3. SCREENER | `production/screener/screener.py` | `screen_from_source` | 4. SETUP | OK |
| 4 | 4. SETUP | `production/agents/setup.py` | `evaluate_setup` | 5. RISK | OK |
| 5 | 5. RISK | `production/risk/risk.py` | `size_swing_position` | 6. ENTRY/EXIT | OK |
| 6 | 6. EXIT | `production/portfolio/portfolio.py` | `exit_check` | 7. ENTRY | OK |
| 7 | 7. ENTRY | `production/portfolio/portfolio.py` | `build_order_buy` | 8. PORTFOLIO | OK |
| 8 | 8. PORTFOLIO | `production/portfolio/portfolio.py` | `constraints/mark-to-market` | 9. LEDGER | OK |
| 9 | 9. STATE/LEDGER | `production/ledger.py` | `write DecisionRecord` | 10. REPORTING | OK |
| 10 | 10. REPORTING | `production/reporting/briefing.py` | `render briefing` | — | OK |

## 3. Research pipeline

| # | Stage | Module | Function | Status |
|---|---|---|---|---|
| 1 | 1. CACHE | `historical_cache.py` | `get_all_data` | OK |
| 2 | 2. PIT SCREEN | `screen_as_of.py` | `screen_all_buckets` | OK |
| 3 | 3. RESEARCH ENGINE | `dynamic_universe_backtest.py` | `DynamicBacktestEngine.run` | OK |
| 4 | 4. SETUP | `src/agents/setup_agent.py` | `setup_signal` | OK |
| 5 | 5. METRICS | `src/backtest/engine.py` | `compute_metrics` | OK |
| 6 | 6. REPORT | `src/backtest/report.py` | `generate_html` | OK |
| 7 | ALT. PROD-EQUIV | `production/backtest.py` | `ProductionBacktest.run` | OK |

## 4. Canonical / legacy / research by subsystem

| Subsystem | Canonical | Legacy | Research / Experiment |
|---|---|---|---|
| Backtest | `production/backtest.py`<br>`production/tests/freeze_conformance_audit.py`<br>`production/tests/phase4_diagnostics.py`<br>`production/tests/phase4_verify.py`<br>`production/tests/phase5_step0b_lever_cohorts.py`<br>`production/tests/phase5_step0_marginal_cohort.py`<br>`src/backtest/engine.py` | `backtest.py`<br>`unified_backtest.py` | — |
| Config | `production/config.py` | `config.py` | — |
| Contracts | `production/contracts/base.py`<br>`production/contracts/entry.py`<br>`production/contracts/exit.py`<br>`production/contracts/order.py`<br>`production/contracts/position.py`<br>`production/contracts/reason_codes.py`<br>`production/contracts/risk.py`<br>`production/contracts/stop.py`<br>`production/contracts/__init__.py` | — | — |
| Control Center | — | — | — |
| Data | `historical_cache.py`<br>`production/datasource.py`<br>`src/data/data_fetcher.py` | — | — |
| Entrypoint | `production/main.py` | `main.py` | — |
| Exit Engine | `production/exits/adapter.py`<br>`production/exits/context.py`<br>`production/exits/engine.py`<br>`production/exits/rules.py`<br>`production/exits/shadow.py`<br>`production/exits/__init__.py` | — | — |
| Indicators / Technicals | `src/agents/technicals_agent.py`<br>`src/indicators/technicals.py` | — | — |
| Infrastructure | — | — | — |
| Market Regime | `production/agents/regime.py`<br>`regime_dual_engine/breadth_engine.py`<br>`regime_dual_engine/config.py`<br>`regime_dual_engine/engine.py`<br>`regime_dual_engine/hmm_engine.py`<br>`regime_dual_engine/regime_dual.py` | `src/agents/regime_agent.py` | `hmm_backtest_compare.py`<br>`hmm_diagnostic.py`<br>`hmm_vw_compare.py`<br>`hostile_regime_test.py`<br>`v32_experiments.py`<br>`v32_robustness.py` |
| Portfolio | `production/portfolio/portfolio.py` | `src/portfolio/portfolio_manager.py` | — |
| Reporting | `production/reporting/briefing.py`<br>`production/reporting/shadow_monitor.py` | `src/utils/display.py` | — |
| Return Research | `research/harness.py`<br>`research/pit_breadth_audit.py`<br>`research/rerun_corrected_research.py`<br>`research/run_baseline.py`<br>`research/run_lever_grid.py`<br>`research/run_tp_grid.py`<br>`research/step0_harness_audit.py` | — | `research/harness.py`<br>`research/rerun_corrected_research.py`<br>`research/run_baseline.py`<br>`research/run_lever_grid.py`<br>`research/run_stop_trailing_experiments.py`<br>`research/run_tp_grid.py` |
| Risk / Sizing | `production/risk/risk.py` | `src/agents/risk_manager.py` | — |
| Screener / Universe | `screen_as_of.py`<br>`production/screener/screener.py`<br>`src/screener/screener.py` | — | `screener_backtest.py` |
| Setup / Signal | `production/agents/setup.py`<br>`src/agents/setup_agent.py` | — | `ablation_test.py`<br>`breakout_ablation.py`<br>`breakout_comparison.py`<br>`setup_comparison.py` |
| State | `production/ledger.py` | `src/state/state.py` | — |
| Stop Engine | `production/stops/engine.py`<br>`production/stops/errors.py`<br>`production/stops/initial.py`<br>`production/stops/trailing.py`<br>`production/stops/__init__.py` | — | — |
| Utility | — | — | — |

### 4.1 Production decision authority

- `production.exit_engine_mode` in force: **legacy**
- `production_used` says *the production pipeline imports this*;
  `production_decision_authority` says *this decides production actions*:
  **PRIMARY** = decides today · **SHADOW_ONLY** = evaluated/recorded, would decide only in `exit_engine_mode='new'` · **NONE** = no production decision authority.

| Module | Subsystem | production_used | decision authority |
|---|---|---|---|
| `production/agents/regime.py` | Market Regime | 1 | **PRIMARY** |
| `production/agents/setup.py` | Setup / Signal | 1 | **PRIMARY** |
| `production/backtest.py` | Backtest | 1 | **PRIMARY** |
| `production/config.py` | Config | 1 | **PRIMARY** |
| `production/datasource.py` | Data | 1 | **PRIMARY** |
| `production/ledger.py` | State | 1 | **PRIMARY** |
| `production/main.py` | Entrypoint | 1 | **PRIMARY** |
| `production/pipeline.py` | Infrastructure | 1 | **PRIMARY** |
| `production/portfolio/portfolio.py` | Portfolio | 1 | **PRIMARY** |
| `production/reporting/briefing.py` | Reporting | 1 | **PRIMARY** |
| `production/risk/risk.py` | Risk / Sizing | 1 | **PRIMARY** |
| `production/screener/screener.py` | Screener / Universe | 1 | **PRIMARY** |
| `src/agents/regime_agent.py` | Market Regime | 1 | **PRIMARY** |
| `src/agents/risk_manager.py` | Risk / Sizing | 1 | **PRIMARY** |
| `src/agents/setup_agent.py` | Setup / Signal | 1 | **PRIMARY** |
| `src/agents/technicals_agent.py` | Indicators / Technicals | 1 | **PRIMARY** |
| `src/portfolio/portfolio_manager.py` | Portfolio | 1 | **PRIMARY** |
| `src/screener/screener.py` | Screener / Universe | 1 | **PRIMARY** |
| `src/state/state.py` | State | 1 | **PRIMARY** |
| `production/contracts/__init__.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/base.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/entry.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/exit.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/order.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/position.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/reason_codes.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/risk.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/contracts/stop.py` | Contracts | 1 | **SHADOW_ONLY** |
| `production/exits/__init__.py` | Exit Engine | 1 | **SHADOW_ONLY** |
| `production/exits/adapter.py` | Exit Engine | 1 | **SHADOW_ONLY** |
| `production/exits/context.py` | Exit Engine | 1 | **SHADOW_ONLY** |
| `production/exits/engine.py` | Exit Engine | 1 | **SHADOW_ONLY** |
| `production/exits/rules.py` | Exit Engine | 1 | **SHADOW_ONLY** |
| `production/exits/shadow.py` | Exit Engine | 1 | **SHADOW_ONLY** |
| `production/stops/__init__.py` | Stop Engine | 1 | **SHADOW_ONLY** |
| `production/stops/engine.py` | Stop Engine | 1 | **SHADOW_ONLY** |
| `production/stops/errors.py` | Stop Engine | 1 | **SHADOW_ONLY** |
| `production/stops/initial.py` | Stop Engine | 1 | **SHADOW_ONLY** |
| `production/stops/trailing.py` | Stop Engine | 1 | **SHADOW_ONLY** |

## 5. Module inventory

- Total modules indexed: **165**
- By type: UNKNOWN=17, audit=19, backtest=6, config=3, core=49, experiment=11, production=13, research=6, test=16, utility=25
- By status: ACTIVE=100, EXPERIMENTAL=9, LEGACY=18, RESEARCH=4, UNKNOWN=26, VALIDATED=8
- By layer: CONFIG=3, CONTRACTS=8, CONTROL_CENTER=7, DATA=6, EXIT_ENGINE=5, INDICATORS=2, INFRA=15, MARKET_REGIME=25, PACKAGE=20, PORTFOLIO=2, REPORTING=4, RESEARCH=25, RISK_SIZING=2, SETUP_SIGNAL=8, STATE=2, STOP=4, TESTS=20, UNIVERSE_SCREENER=6, UTIL=1
- By production decision authority: NONE=126, PRIMARY=19, SHADOW_ONLY=20

## 6. Modules with unclear ownership / needing human review

40 module(s) flagged (`needs_review=1`):

- `audit_return_calculation.py`
- `control_center/control_center.py`
- `control_center/tests/test_control_center.py`
- `regime_dual_engine/__init__.py`
- `regime_dual_engine/backtest_harness.py`
- `regime_dual_engine/breadth_refresh.py`
- `regime_dual_engine/download_missing_ohlcv.py`
- `regime_dual_engine/setup_v1_regime_policy_test.py`
- `regime_dual_engine/setup_v1_root_cause.py`
- `regime_dual_engine/setup_v1_selection_sim.py`
- `regime_dual_engine/setup_v1_validation.py`
- `regime_dual_engine/tests/test_regime_dual.py`
- `research/__init__.py`
- `research/bootstrap_uncertainty.py`
- `research/breadth_lineage_impact.py`
- `research/breadth_staleness_impact.py`
- `research/bs_temporal_consistency.py`
- `research/constituent_official_crosscheck.py`
- `research/constituent_source_audit.py`
- `research/constituent_staleness_impact.py`
- `research/live_breadth_build.py`
- `research/live_breadth_crosscheck.py`
- `research/live_freshness_matrix.py`
- `research/ohlcv_lineage_audit.py`
- `research/p6_extension_impact.py`
- `research/r2_entry_cohorts.py`
- `research/r3_capacity_magnitude.py`
- `research/run_stop_trailing_experiments.py`
- `research/s1_decomposition.py`
- `research/verify_r7_equivalence.py`
- `src/__init__.py`
- `src/agents/__init__.py`
- `src/backtest/__init__.py`
- `src/data/__init__.py`
- `src/indicators/__init__.py`
- `src/portfolio/__init__.py`
- `src/screener/__init__.py`
- `src/state/__init__.py`
- `src/utils/__init__.py`
- `src/utils/fees.py`

## 7. Unreferenced modules (no production/research/backtest importer)

- `production/tests/freeze_conformance_audit.py`
- `production/tests/phase4_diagnostics.py`
- `production/tests/phase4_verify.py`
- `production/tests/phase5_step0_marginal_cohort.py`
- `production/tests/phase5_step0b_lever_cohorts.py`
- `regime_dual_engine/breadth_refresh.py`
- `regime_dual_engine/validation_divergence_review.py`
- `research/bootstrap_uncertainty.py`
- `research/breadth_lineage_impact.py`
- `research/breadth_staleness_impact.py`
- `research/bs_temporal_consistency.py`
- `research/constituent_official_crosscheck.py`
- `research/constituent_source_audit.py`
- `research/constituent_staleness_impact.py`
- `research/harness.py`
- `research/live_breadth_build.py`
- `research/live_breadth_crosscheck.py`
- `research/live_freshness_matrix.py`
- `research/ohlcv_lineage_audit.py`
- `research/p6_extension_impact.py`
- `research/pit_breadth_audit.py`
- `research/r2_entry_cohorts.py`
- `research/r3_capacity_magnitude.py`
- `research/rerun_corrected_research.py`
- `research/run_baseline.py`
- `research/run_lever_grid.py`
- `research/run_stop_trailing_experiments.py`
- `research/run_tp_grid.py`
- `research/s1_decomposition.py`
- `research/step0_harness_audit.py`
- `research/verify_r7_equivalence.py`
- `unified_backtest.py`

## 8. Current migrations

| Subsystem | Old | New | Status | Blocker |
|---|---|---|---|---|
| Market Regime | `src/agents/regime_agent.py` | `regime_dual_engine/` | VALIDATED — WIRED | human review |
| Live Entrypoint | `main.py` | `production/main.py + production/pipeline.run_daily` | VALIDATED — NOT YET CUT OVER | human review |
| Backtest | `dynamic_universe_backtest.py` | `production/backtest.py` | AVAILABLE — LEGACY RETAINED | — |
| Setup / Signal | `config.py: setup_enabled_types=['breakout']` | `src/agents/setup_agent.py (Pullback Only, Setup v1)` | VALIDATED — FROZEN | — |
| Stop Engine | `derived inline: src/agents/risk_manager.py (initial stop) + src/portfolio/portfolio_manager.py::_exit_check (trailing)` | `production/stops/ + production/contracts/ (Phase 1; reached from production through production/exits/adapter.py)` | SHADOW WIRED — NOT DECIDING (parity verified) | mode != 'new': the legacy oracle still owns every stop decision |
| Exit Engine | `src/portfolio/portfolio_manager.py::_exit_check (decision + fill model inline)` | `production/exits/ + production/contracts/exit.py (Phase 2; wired in shadow by Phase 3 behind cfg.exit_engine_mode)` | SHADOW WIRED — LEGACY STILL AUTHORITATIVE (parity verified, 0 divergences) | human review; live shadow coverage PENDING (0 sessions observed) |
| Stop + Exit wiring (Phase 3) | `src/portfolio/portfolio_manager.py::_exit_check — the single decision source for stop/target/trailing/time/signal exits` | `production/exits/adapter.py + production/exits/shadow.py behind production/config.py::exit_engine_mode (legacy | shadow | new; default legacy), selectable at the live entrypoint via production/main.py --exit-engine-mode` | IMPLEMENTED — HISTORICALLY VALIDATED — LIVE VALIDATION PENDING | live shadow validation PENDING (0 sessions observed); mode='new' NOT ENABLED; production cut-over NOT APPROVED |
| Regime v1 breadth — point-in-time contract (data integrity) | `regime_dual_engine/pit_breadth_data.py::get_breadth — returned the WHOLE cached CSV whenever the cache existed (the `end` argument was honoured only on rebuild), and regime_dual_engine/breadth_data.py::get_breadth_series had the same defect; compute_regime_decision then read `pct_above_50dma.iloc[-1]`, i.e. the 2025-07-31 tail, for every historical as-of date` | `regime_dual_engine/breadth_data.py::slice_to_end — ONE point-in-time guard applied on both the cache and rebuild paths of both loaders, so any `end=as_of` request is guaranteed `date <= as_of`` | FIXED — FULLY RE-BASELINED (data-integrity repair; no strategy change) | — |

## 9. Known technical debt / duplicate candidates

- Duplicate groups detected: **19** (45 member rows). Never auto-deleted — see the Duplicate Audit page.
- Config parameters defined in more than one config file: see `config_registry.duplicate_count` (e.g. `setup_enabled_types`).
- **Repo copy divergence**: 319 file(s) differ between the two working copies:
  - `[content_differs]` production/backtest.py
  - `[content_differs]` production/config.py
  - `[content_differs]` production/main.py
  - `[content_differs]` production/pipeline.py
  - `[content_differs]` regime_dual_engine/breadth_data.py
  - `[content_differs]` regime_dual_engine/pit_breadth_data.py
  - `[content_differs]` reports/production_bt_2024-01-01_2025-07-31.json
  - `[line_ending_only]` HANDOFF_2026-08-30.md
  - `[line_ending_only]` HANDOFF_2026-09-04.md
  - `[line_ending_only]` README.md
  - `[line_ending_only]` ablation_test.py
  - `[line_ending_only]` audit_return_calculation.py
  - `[line_ending_only]` backtest.py
  - `[line_ending_only]` breakout_ablation.py
  - `[line_ending_only]` breakout_comparison.py

## 10. Open architecture decisions (human-in-the-loop)

- **[HIGH]** Two repo copies exist with the same commit: 'WB prog' vs 'WB-prog' — RESOLVED
- **[HIGH]** Legacy live entrypoint (main.py) still present alongside production/main.py — OPEN
- **[MEDIUM]** Untracked legacy artefacts from an earlier session (unified_backtest.py + 2 reports) — OPEN
- **[MEDIUM]** Unused/dead-code candidates are not proven safe to delete — OPEN
- **[HIGH]** Legacy src/ modules are still imported by the production path (deletion unsafe) — OPEN
- **[HIGH]** Stop Engine is implemented but NOT wired (Phase 1 isolation) — RESOLVED
- **[MEDIUM]** Structure-based stop definition is undefined (deferred in Phase 1) — OPEN
- **[MEDIUM]** current_stop_price is computed but never persisted — OPEN
- **[HIGH]** Exit Engine is implemented but NOT wired (Phase 2 isolation) — RESOLVED
- **[MEDIUM]** Legacy position has TWO stop levels; mapping into StopPlan must be confirmed — OPEN
- **[HIGH]** Live 20-session shadow gate is not yet closed (backtest coverage only) — OPEN
- **[HIGH]** exit_engine_mode='new' is implemented but must not be enabled without approval — OPEN
- **[HIGH]** production_used vs production_decision_authority were conflated — RESOLVED
- **[LOW]** `production_used` for test files under production/ relies on a path heuristic — OPEN
- **[HIGH]** BLOCKER: no executable path can run production in shadow mode, so the §10 live gate cannot start accumulating — RESOLVED
- **[HIGH]** Phase 4 D1-D6 diagnostics complete — the next implementation target must be chosen from evidence, not intuition — OPEN
- **[HIGH]** Phase-4 verification REFUTED one diagnostic claim (entry-gap monotonicity) — RESOLVED
- **[LOW]** Diagnostic record-quality gaps limit future analysis — OPEN
- **[HIGH]** CONTRACT MISMATCH: the frozen Setup v1 extension filter cannot fire in production — OPEN
- **[LOW]** Two freeze clauses are accurate only under one reading (documentation precision) — OPEN
- **[HIGH]** Step 0 executed: the risk-budget cap is PROTECTIVE, so the primary research target moves from deployment to exit efficiency — RESOLVED
- **[HIGH]** Research §11 gate: is the Stop/Exit research harness trustworthy before parameter experiments? — RESOLVED
- **[HIGH]** R1 executed: the frozen 2.5 ATR take-profit appears TOO TIGHT — a research finding, NOT a production change — RESOLVED
- **[HIGH]** LOOK-AHEAD: Regime v1 Engine B (breadth) is NOT point-in-time in historical replay — it always evaluates the cache tail — RESOLVED
- **[MEDIUM]** Live breadth staleness: the freshness POLICY is undefined (exposure now added, threshold not yet decided) — OPEN
- **[HIGH]** Corrected research re-baseline complete — the next implementation target must be chosen on PIT-clean evidence — OPEN

## 11. How to maintain this file

```bash
python control_center/refresh_registry.py     # rescan + rebuild db + docs
streamlit run control_center/control_center.py
```
