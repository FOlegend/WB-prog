# ARCHITECTURE AUDIT REPORT — WB-prog Trading Bot

**Phase 1 · READ-ONLY REPOSITORY ARCHAEOLOGY**
Generated 2026-09-24 · tool `control_center` v1.0.0 · commit `da9f5dc`

> **No trading logic, indicator, entry rule or risk logic was added, modified or deleted.**
> The only files created are inside `control_center/` (this registry) — nothing else in the repo changed.

---

## 0. Scope & method

- **Scanned**: the whole repo — `*.py`, `*.md`, `*.json/.yaml/.toml/.txt/.csv`, `reports/`, `data/`.
- **Not copied per-file**: `data/cache/` (520 OHLCV CSVs) and `data/constituents/` — indexed as an aggregate summary only.
- **How classification was derived**: Python `ast` (imports, symbols, `__main__` guards, call-level edges),
  then reachability closure from the production / research / backtest entry points, then a small
  **curated evidence layer** sourced from the freeze contracts and HANDOFF docs.
  Where evidence was insufficient the row stays `UNKNOWN` + **NEED HUMAN REVIEW**.
- **Two copies scanned**: `WB-prog` (primary, this repo) and `WB prog` (sibling copy).

### Headline numbers

| Metric | Value |
|---|---|
| Python modules indexed | **101** |
| Dependency edges | **612** |
| Config parameters indexed | **169** |
| Potential duplicate groups | **14** (33 member rows) |
| Modules produced/used by production | **44** |
| Legacy modules | **9** flagged (`legacy=1`) / 18 with `status=LEGACY` |
| Experimental modules | **8** |
| Needs human review | **21** |
| Unreferenced (no importer) | **2** |
| Copy divergence | **0 real** (146 line-ending-only, 14 new files) |
| Open migrations | **4** |
| Open decisions | **5** |

---

## 1. What is actually production?

**Entrypoint `production/main.py` → single decision function `production/pipeline.py::run_daily`.**
This is the *only* decision path — live and backtest share it.

| Stage | Module | Function |
|---|---|---|
| 1 DATA | `production/datasource.py` | CachedSource / YFinanceSource (+ Provenance) |
| 2 REGIME | `production/agents/regime.py` | `compute_market_regime` → Regime **v1** (frozen) |
| 3 SCREENER | `production/screener/screener.py` | `screen_from_source` (6 filters, top-30) |
| 4 SETUP | `production/agents/setup.py` | `evaluate_setup` → Setup **v1 = Pullback Only** (frozen) |
| 5 RISK | `production/risk/risk.py` | `size_swing_position` |
| 6 EXIT | `production/portfolio/portfolio.py` | `exit_check` (gap-aware) |
| 7 ENTRY | `production/portfolio/portfolio.py` | `build_order_buy` (next-open) |
| 8 PORTFOLIO | `production/portfolio/portfolio.py` | constraints / mark-to-market |
| 9 STATE/LEDGER | `production/ledger.py` | DecisionRecord → `reports/decision_*.json` |
| 10 REPORTING | `production/reporting/briefing.py` | human briefing |

Frozen contracts: `regime_dual_engine/REGIME_V1_FREEZE.md`, `src/agents/SETUP_V1_FREEZE.md`.

**⚠️ Important nuance:** production is **not** self-contained inside `production/`. It reuses 9 modules
from the legacy `src/` tree (see §7). Do not assume `src/` is dead code.

---

## 2. What is research only?

38 modules are reachable only from research/backtest entry points (never from production). Groups:

- **Root research/experiment scripts**: `dynamic_universe_backtest.py`, `screener_backtest.py`,
  `backtest.py`, `v32_experiments.py`, `v32_robustness.py`, `setup_comparison.py`,
  `breakout_comparison.py`, `breakout_ablation.py`, `ablation_test.py`, `hmm_diagnostic.py`,
  `hmm_backtest_compare.py`, `hmm_vw_compare.py`, `hostile_regime_test.py`,
  `audit_return_calculation.py`
- **Research data layer**: `historical_cache.py` (Layer 1 cache), `screen_as_of.py` (PIT screener)
- **`regime_dual_engine/` validation & audit tooling**: `validation_*` (5), `audit_*` (3),
  `ablation_pit.py`, `robustness_pit.py`, `setup_v1_*` (4), `backtest_harness.py`,
  `daily_signals.py`, `build_review_report.py`, `download_missing_ohlcv.py`
- **Legacy research support**: `src/backtest/engine.py` (`compute_metrics`), `src/backtest/report.py`,
  `src/utils/display.py`, `src/utils/fees.py`
- **Legacy live entrypoint**: `main.py`

The **production-equivalent** replacement for the big research engine is `production/backtest.py`.

---

## 3. What is legacy?

| Module | Replacement | Safe to delete? |
|---|---|---|
| `main.py` | `production/main.py` | ❌ no (legacy live path still present) |
| `config.py` | `production/config.py` + `regime_dual_engine/config.py` | ❌ no (legacy research still imports it) |
| `src/agents/regime_agent.py` | `regime_dual_engine/` | ❌ **NO — production imports its HMM primitives** |
| `src/agents/risk_manager.py` | `production/risk/risk.py` | ❌ **NO — production/risk reuses `size_position`** |
| `src/portfolio/portfolio_manager.py` | `production/portfolio/portfolio.py` | ❌ **NO — production reuses `_exit_check`** |
| `src/state/state.py` | — | ❌ **NO — production/pipeline reuses `mark_to_market`** |
| `src/utils/display.py` | `production/reporting/briefing.py` | ⚠️ only `main.py` uses it (legacy) |
| `backtest.py` | `production/backtest.py` | ⚠️ legacy research only |
| `unified_backtest.py` | `production/backtest.py` | ⚠️ untracked ad-hoc artefact (2026-08-14) |

> **The single most important finding of this audit:** four modules labelled *legacy* are still on the
> **production import path**. Deleting `src/` "because it's old" would break production.
> The registry marks these explicitly (`production_used=1 AND legacy=1`).

---

## 4. What is experimental?

8 modules: `ablation_test.py`, `breakout_ablation.py`, `breakout_comparison.py`,
`hostile_regime_test.py`, `screener_backtest.py`, `setup_comparison.py`,
`v32_experiments.py`, `v32_robustness.py`.

These are single-purpose experiment drivers. Several produced the reports that led to the
Regime v1 / Setup v1 freeze decisions — **keep for reproducibility**, but they are not production.

---

## 5. Which modules are duplicated?

14 potential-duplicate groups (**nothing auto-deleted**; each needs a human decision):

| Group | Members | Note |
|---|---|---|
| `same_function::load_data_from_cache` | 5 research scripts | copy-pasted cache loader |
| `same_filename::config.py` | `config.py`, `production/config.py`, `regime_dual_engine/config.py` | 3 config sources |
| `same_function::run_daily` | `main.py`, `production/main.py`, `production/pipeline.py` | legacy vs production |
| `same_filename::backtest.py` | `backtest.py`, `production/backtest.py` | legacy vs production |
| `same_filename::engine.py` | `regime_dual_engine/engine.py`, `src/backtest/engine.py` | different subsystems — name collision only |
| `same_filename::main.py` | `main.py`, `production/main.py` | legacy vs production |
| `same_filename::screener.py` | `production/screener/screener.py`, `src/screener/screener.py` | wrapper vs shared impl |
| `same_function::render_briefing` | `production/reporting/briefing.py`, `src/utils/display.py` | legacy vs production |
| `same_function::run_screener` | `production/screener/screener.py`, `screener_backtest.py` | |
| `same_function::segmented_breakdown` | `dynamic_universe_backtest.py`, `v32_experiments.py` | |
| `same_function::detailed_metrics` | `breakout_ablation.py`, `breakout_comparison.py` | |
| `same_function::run_variant` | `breakout_ablation.py`, `setup_comparison.py` | |
| `same_function::run_ablation` | `regime_dual_engine/ablation_pit.py`, `backtest_harness.py` | |
| `same_function::build_daily_signals` | `regime_dual_engine/daily_signals.py`, `validation_divergence.py` | |

### Config drift (the most actionable)

**49 parameters are defined in more than one config file.** Every duplicated `screener_*`,
`setup_*`, `stop_*/take_profit_*/trailing_*` and `hmm_*` parameter appears in at least 2 of
`config.py` / `production/config.py` / `regime_dual_engine/config.py`.

The sharpest example — a genuine behavioural divergence:

| Parameter | `config.py` (legacy) | `production/config.py` (frozen) |
|---|---|---|
| `setup_enabled_types` | `["breakout"]` | **`["pullback"]`** |

That single line is the whole v3→v1 setup migration, and it is exactly the kind of drift this
registry exists to surface.

---

## 6. Which modules have unclear ownership?

**21 modules flagged `needs_review=1`** — insufficient evidence to classify confidently:

- 8 modules with `status=UNKNOWN`: `audit_return_calculation.py`, `regime_dual_engine/backtest_harness.py`,
  `download_missing_ohlcv.py`, `setup_v1_regime_policy_test.py`, `setup_v1_root_cause.py`,
  `setup_v1_selection_sim.py`, `setup_v1_validation.py`
- 11 package markers `__init__.py` + `control_center/control_center.py`, `control_center/tests/…`
- `src/utils/fees.py` — **no importer found**; purpose unproven.

These appear in the UI with **NEED HUMAN REVIEW**. The registry deliberately does not guess.

---

## 7. Which modules are imported by production?

**44 modules are on the production import closure.** Grouped:

- **`production/` package (13)** — the pipeline itself (§1).
- **`regime_dual_engine/` core (9)** — `regime_dual.py`, `engine.py`, `hmm_engine.py`,
  `breadth_engine.py`, `config.py`, `breadth_data.py`, `pit_breadth_data.py`, `pit_constituents.py`,
  `distribution_days.py`.
- **`src/` shared layer (9)** — this is the surprise:
  - `src/agents/regime_agent.py` ← imported by `regime_dual_engine/hmm_engine.py`
    (uses `fit_hmm` / `compute_features` / `decode_and_label`)
  - `src/agents/risk_manager.py` ← imported by `production/risk/risk.py`
  - `src/portfolio/portfolio_manager.py` ← imported by `production/portfolio/portfolio.py`
  - `src/state/state.py` ← imported by `production/pipeline.py` + `portfolio.py` + `backtest.py`
  - `src/agents/setup_agent.py` ← imported by `production/agents/setup.py`
  - `src/agents/technicals_agent.py` ← imported by `production/pipeline.py`
  - `src/screener/screener.py` ← imported by `production/screener/screener.py` + `screen_as_of.py`
  - `src/data/data_fetcher.py`, `src/indicators/technicals.py`
- **`config.py` (root)** — reachable via a config import edge.

Consequence: **the "legacy `src/`" tree is actually a shared low-level layer**. It is not dead.

---

## 8. Which scripts are not used anywhere?

Only **2 modules have no importer at all** (and neither is auto-deletable):

| Module | Type | Verdict |
|---|---|---|
| `unified_backtest.py` | legacy artefact | Untracked ad-hoc script from the 2026-08-14 session; superseded by `production/backtest.py`. **Human decision required.** |
| `regime_dual_engine/validation_divergence_review.py` | audit | Standalone review generator (invoked manually by design), not imported. Keep. |

Note: CLI entry points (`main.py`, `production/main.py`, `dynamic_universe_backtest.py`, …) are
never *imported* either, but they are **entry points** — the registry distinguishes the two.

---

## 9. Which migrations are incomplete?

| Subsystem | Old → New | Status | Blocker |
|---|---|---|---|
| Market Regime | `src/agents/regime_agent.py` → `regime_dual_engine/` | VALIDATED — WIRED | human review (freeze-face sign-off) |
| Live Entrypoint | `main.py` → `production/main.py` + `run_daily` | **VALIDATED — NOT YET CUT OVER** | human review |
| Backtest | `dynamic_universe_backtest.py` → `production/backtest.py` | AVAILABLE — LEGACY RETAINED | — |
| Setup / Signal | `config.py: ["breakout"]` → Setup v1 Pullback Only | VALIDATED — FROZEN | — |

**Two migrations are blocked on a human decision**, exactly as the HANDOFF docs state.

---

## 10. Current canonical implementation per subsystem

| Subsystem | Canonical | Legacy / superseded |
|---|---|---|
| **Market Regime** | `regime_dual_engine/` (Regime v1, frozen) via `production/agents/regime.py` | `src/agents/regime_agent.py` (v3 composite) |
| **Setup / Signal** | `src/agents/setup_agent.py` (Setup v1, Pullback Only) + `production/agents/setup.py` | breakout scoring (dormant in-file) |
| **Screener / Universe** | `src/screener/screener.py` (+ `production/screener/screener.py`, `screen_as_of.py` for PIT) | `screener_backtest.py` |
| **Data** | `production/datasource.py`; `src/data/data_fetcher.py`; `historical_cache.py` (research cache) | — |
| **Risk / Sizing** | `production/risk/risk.py` | `src/agents/risk_manager.py` (still imported) |
| **Portfolio** | `production/portfolio/portfolio.py` | `src/portfolio/portfolio_manager.py` (still imported) |
| **State** | `production/ledger.py` + `state.json`; `src/state/state.py` (helpers, still imported) | — |
| **Reporting** | `production/reporting/briefing.py` | `src/utils/display.py` |
| **Backtest** | `production/backtest.py` (production-equivalent) | `dynamic_universe_backtest.py` (research), `backtest.py` |
| **Config** | `production/config.py` + `regime_dual_engine/config.py` | `config.py` |
| **Entrypoint** | `production/main.py` | `main.py` |

---

## 11. Non-obvious findings (worth your attention)

1. **`src/` is a live shared layer, not dead code.** Four "legacy" modules are on the production import
   path (regime_agent HMM primitives, risk_manager, portfolio_manager, state). The registry flags them
   `production_used=1 AND legacy=1`. **Any cleanup plan must treat them as production.**
2. **The two repo copies have ZERO substantive drift.** All 146 apparent differences were CRLF-vs-LF
   line endings; 14 files exist only in the primary copy (the new `control_center/` + one untracked
   legacy artefact). The earlier "153 files differ" reading was a false alarm — now corrected and
   marked `line_ending_only`.
3. **49 duplicated config parameters** across 3 config files — the primary architecture-drift vector.
4. **The breakout→pullback flip** lives in exactly one parameter (`setup_enabled_types`), and the two
   config files disagree — a silent trap for anyone reading only one config.
5. **`src/utils/fees.py` has no importer** — the only genuine "possibly dead" module, and even that is
   marked UNKNOWN rather than assumed dead.

---

## 12. Deliverables (all inside `WB-prog/control_center/`)

```
control_center/
├── control_center.py            # Streamlit UI (10 pages)
├── refresh_registry.py          # rescan + rebuild db + docs (READ-ONLY on trading code)
├── project_control.db           # SQLite single source of truth
├── ARCHITECTURE.md              # human-readable map   (GENERATED)
├── ARCHITECTURE.yaml            # machine-readable map (GENERATED)
├── AGENT_WORKFLOW.md            # mandatory before/after workflow for AI agents
├── ARCHITECTURE_AUDIT_REPORT.md # this report
├── requirements-control-center.txt
├── README.md
├── registry_db.py               # schema + human-decision preservation
├── scanner.py                   # AST scanner
├── classify.py                  # rules + curated evidence + pipelines
├── generate_docs.py             # doc generator
└── tests/test_control_center.py # headless smoke test (AppTest)
```

### Success-criteria checks (verified)

| You ask | The system answers |
|---|---|
| "What does `regime_agent.py` do?" | Module Registry → detail (HYBRID: HMM primitives used by production; v3 composite legacy) |
| "Who uses `regime_agent.py`?" | Dependency Explorer → 6 importers listed |
| "Which regime engine does production use?" | Production Architecture → `regime_dual_engine/` via `production/agents/regime.py` |
| "Can I delete this script?" | Delete-safety panel: production dep YES/NO, research dep YES/NO, replacement, last modified, confidence, review required |

---

## 13. What Phase 1 did NOT do

- ❌ No trading logic / indicator / entry rule / risk logic added or changed.
- ❌ No strategy refactor, no file deleted, no script merged.
- ❌ No backtest result changed; no production behaviour changed.
- ❌ Nothing committed or pushed.

## 14. Suggested Phase 2 (awaiting your instruction)

1. **Resolve the two HIGH-priority decisions**: pick the canonical repo copy; plan the legacy
   `main.py` cut-over.
2. **Config de-duplication plan** (49 duplicated params) — a freeze-safe consolidation proposal,
   for your approval, no code change yet.
3. **Review the 21 NEED-HUMAN-REVIEW modules** and record decisions in the UI (they persist).
4. **Review the 14 duplicate groups** and mark KEEP / KEEP_AS_LEGACY / MERGE_INTO.
5. Only then consider any actual cleanup — driven by registry evidence, not by guesswork.
