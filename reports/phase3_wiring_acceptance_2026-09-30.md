# Phase 3 — Stop + Exit Shadow Wiring · Acceptance Report

**Date:** 2026-09-30 · **Repo:** `WB-prog` · **Base commit:** `da9f5dc` (+ Phase 1/2 work, uncommitted)
**Status:** implementation complete, **STOPPED for human review**. Production runs `exit_engine_mode = "legacy"`.

---

## A. Files created (9)

| File | Role |
|---|---|
| `production/exits/adapter.py` | Phase-3 adapter: production position dict → typed contracts → `PositionState` / `StopPlan` (supplied by the Stop Engine) / `ExitContext`. Translation + validation only. |
| `production/exits/shadow.py` | Shadow comparator: fail-open evaluation, legacy comparison, 8-way divergence classification, `summarise_shadow()`. |
| `production/reporting/shadow_monitor.py` | Shadow coverage monitor + cut-over gate (§10). Reads ledgers / coverage JSON; never decides. |
| `production/tests/test_wiring_safety.py` | 17 Phase-3 wiring-safety tests (spec §16). |
| `production/tests/shadow_coverage.py` | Coverage runner: replays the production-equivalent backtest in shadow mode + a legacy control run and compares them end-to-end. |
| `production/STOP_EXIT_V1_FREEZE.md` | The frozen contracts / stop semantics / exit semantics / production + persistence boundaries. |
| `reports/phase3_exit_parity_replay_2026-09-30.json` | Replay-parity evidence (both passes). |
| `reports/phase3_shadow_coverage_2026-09-30.json` | Shadow coverage + gate evidence. |
| `reports/phase3_wiring_acceptance_2026-09-30.md` | This report. |

## B. Files modified (12)

| File | Change |
|---|---|
| `production/pipeline.py` | Minimal wiring: `_skeleton` gains `exits.mode` / `exits.shadow` / `exits.shadow_summary`; mode resolution + config-failure warnings; mode-aware exit loop; `_exit_from_new_engine()` helper. **Lazy imports** — `legacy` mode never loads the new packages. |
| `production/config.py` | The ONE new flag `exit_engine_mode` (`legacy`/`shadow`/`new`, default `legacy`) + `config_warnings` + `resolve_exit_engine_mode()` (fails SAFE to legacy). No other parameter touched. |
| `production/backtest.py` | Additive only: collects per-session `exits.shadow_summary` into a returned `shadow_log`. No decision/execution path changed. |
| `production/contracts/reason_codes.py` | Added the mode vocabulary (`EXIT_ENGINE_*`) and the divergence vocabulary (`DIVERGENCE_TYPES`, `SHADOW_*`). |
| `production/tests/test_exit_engine.py` | `test_phase2_isolation_not_wired` → **replaced** by `test_phase3_wiring_safety_import_graph` (approved). Also fixed an `ast` bug that made "module-level import" detection recurse into function bodies. |
| `production/tests/test_stops.py` | `test_phase1_isolation_not_wired` → **replaced** by `test_phase3_wiring_safety_stop_imports` (same class of approved change; Phase 1's "nothing may import it" is superseded by Phase 3 wiring). |
| `production/tests/replay_exit_parity.py` | Added PASS B (adapter path) + `adapter_vs_direct` fidelity check; new Phase-3 report path. Existing Phase-2 evidence file left intact. |
| `control_center/registry_db.py` | New column `modules.production_decision_authority` (idempotent `ALTER TABLE` migration for existing DBs). |
| `control_center/classify.py` | Authority vocabulary + derivation; curated entries for the 5 new files; migrations updated (Stop/Exit → SHADOW WIRED) + 1 new row; +4 decisions; **removed the stale Phase-1/2 `production_used=0` overrides** for the modules Phase 3 genuinely imports. |
| `control_center/generate_docs.py` | Renders `production.exit_engine_mode`, the authority table (§4.1) and the authority breakdown. |
| `control_center/refresh_registry.py` | Writes `meta.production_exit_engine_mode` + the authority note. |
| `control_center/project_control.db`, `ARCHITECTURE.md`, `ARCHITECTURE.yaml` | Regenerated. |

## C. Files explicitly NOT modified

Verified clean by `git diff --quiet` (script run, all CLEAN):

`production/portfolio/portfolio.py` · `production/risk/risk.py` · `src/state/state.py` · `src/agents/risk_manager.py` · `src/portfolio/portfolio_manager.py` · `src/agents/setup_agent.py` · `config.py` · `production/main.py` · `production/ledger.py` · `production/datasource.py` · `production/screener/screener.py` · `production/agents/regime.py` · `production/agents/setup.py` · `regime_dual_engine/**` (all clean).

Legacy `_exit_check` was **not** modified and **not** converted into a delegate. Regime v1 and Setup v1 are untouched.

## D. Wiring architecture

```
                          production/pipeline.py::run_daily
                                       │
                 exit_engine_mode ─────┤ (resolve, fail-safe → legacy)
                                       │
   ┌───────────────────────────────────┴────────────────────────────────┐
   │ legacy (default)                 shadow                          new│
   │                                                                     │
   │ exit_check(pos,bar,date,sig,cfg) │  + shadow_exit_check(...)        │  ExitDecision
   │   → legacy oracle decides        │    → adapter → Stop Engine       │   decides
   │   → new pkgs NOT imported        │    → Exit Engine → compare       │
   │                                  │    → EXITS.SHADOW ONLY           │
   └─────────────────────────────────────────────────────────────────────┘

   ownership chain (unchanged, single owner):
     Stop Engine → StopPlan.current_stop_price → Exit Engine
```

* `production/exits/adapter.py` is the **only** production entry point to the Stop Engine; `production/exits/shadow.py` is the only comparator.
* `production/pipeline.py` is the only production module that touches them, and only via **lazy imports inside functions** — so `legacy` mode does not even load the modules (subprocess-tested).
* The Exit Engine still performs **no trailing arithmetic**; the adapter asks the Stop Engine (`plan_from_position` + `update_stop`) and copies the resulting level.

## E. Config mode

| Item | Value |
|---|---|
| flag | `production/config.py::ProductionConfig.exit_engine_mode` |
| allowed | `legacy` \| `shadow` \| `new` |
| **default** | **`legacy`** |
| fallback | missing attribute / `None` / non-string / unknown value → `legacy` + a `CONFIG_WARNING` in the DecisionRecord |
| normalisation | case/whitespace tolerated (`" SHADOW "` → `shadow`) |
| frozen params | `stop_atr_mult`, `take_profit_atr_mult`, `trailing_atr_mult`, `trailing_trigger_r`, `max_holding_days`, `risk_per_trade`, `max_open_positions`, `max_position_pct`, screener/setup/regime thresholds — **all unchanged** (asserted by test 1) |

## F. Historical parity (`reports/phase3_exit_parity_replay_2026-09-30.json`)

Real recorded trades (2024-01→2025-07), 151 trades, 1073 bars, real cached OHLCV:

| Pass | bars | legacy exit mix | new exit mix | divergences | errors |
|---|---|---|---|---|---|
| A — direct contracts (Phase-2 method) | 1073 | TP 60 / SL 75 / TIME 6 / TRAIL 10 | identical | **0** | — |
| B — **through the production adapter** | 1073 | — | identical | **0** | **0** |

* adapter decision **≠** direct decision: **0** occurrences.
* first-exit reconciliation vs the recorded trade log: **151 / 151**.
* `RESULT: PASS`.

## G. Shadow implementation

* `DecisionRecord.exits.mode` — the mode in force.
* `DecisionRecord.exits.shadow[]` — one block per held position per session: `ticker`, `session_date`, `enabled`, `evaluated`, `status`, `agree`, `legacy{reason,exit_price,fill_model}`, `new{should_exit,reason,exit_price,fill_model,stop_reference_price,target_reference_price,detail}`, `divergence_types[]`, `stop_plan{initial_stop_price,current_stop_price,armed,reason_code,changed,previous_stop,detail}`, `error`, `error_type`.
* `DecisionRecord.exits.shadow_summary` — `{enabled, n_evaluated, n_agree, n_diverged, n_error, divergence_types{}}`.
* Divergence vocabulary (never auto-judged harmless): `SHOULD_EXIT_MISMATCH`, `REASON_MISMATCH`, `PRICE_MISMATCH`, `FILL_MODEL_MISMATCH`, `STOP_REFERENCE_MISMATCH`, `TARGET_REFERENCE_MISMATCH`, `INPUT_VALIDATION_MISMATCH`, `ENGINE_EXCEPTION`.
* Fail-open: adapter/engine/validation failures produce an error record with `agree = None` (unknown, **not** "agreed") and legacy continues. Divergences/errors are confined to the shadow section — `warnings`, `pipeline_status`, `proposed`, state and cash are never touched.

## H. Unit tests (spec §16)

`test_wiring_safety.py` — **17/17**, covering all 15 required cases plus two extras:

default mode legacy · legacy output unchanged · **legacy does not import the new engines (fresh subprocess)** · shadow does not alter the legacy decision · records agreement · records divergence (pipeline level + comparator level) · engine exception survives · invalid input survives · new mode consumes ExitDecision (forced-HOLD proves it) · no independent trailing · `current_stop_price` is used · rollback by flag · no state mutation · adapter does not mutate inputs · no persistence · config validation/fallback · freeze-contract document.

## I. Integration / regression tests

| Suite | Result |
|---|---|
| `test_datasource` | 8/8 |
| `test_pipeline` | 9/9 |
| `test_backtest` | 5/5 |
| `test_production` | 10/10 |
| `test_contracts` | 18/18 |
| `test_stops` | 19/19 |
| `test_exit_engine` | 31/31 |
| `test_wiring_safety` | 17/17 |
| **Total** | **117/117** |

Intentional replacement of two tests is documented in §B. `test_production.py`'s e2e writes to `reports/`; those side effects were reverted (`git checkout -- reports/`) after the run.

## J. Side-effect verification

**Backtest-level, end-to-end** (`reports/phase3_shadow_coverage_2026-09-30.json`) — the same window (2024-01-01→2025-07-31) run twice, once in `shadow` and once in `legacy`:

| Artifact | shadow vs legacy |
|---|---|
| `trade_log` (151 entries) | **equal** |
| `equity_curve` (396) | **equal** |
| `skipped` (5) | **equal** |
| `regime_log` (396) | **equal** |
| `screens` (19 buckets) | **equal** |
| `n_decisions` (396) | **equal** |
| `summary` (all metrics) | **equal** |
| `shadow_log` | 396 vs 0 — *the only intended difference* |

`legacy_equivalence.all_equal = true`. Shadow coverage: **396 sessions, 280 with a real held-position evaluation, 1088 position evaluations, 1088 agree, 0 diverged, 0 errors**, longest qualifying streak **126**.

Unit-level: no mutation of the caller's `state` or the position dicts; `current_stop_price` appears nowhere in `src/state/state.py` or in the record's position items.

## K. Rollback verification

* `cfg.exit_engine_mode = "legacy"` restores behaviour with **no code revert**: test 11 flips a config from `new` back to `legacy` and asserts the produced record equals the pure-legacy baseline **exactly** (record equality, not just the exit list).
* Legacy is retained (no deletion); no automatic rollback logic exists — a human flips the flag (§12).

## L. Control Center updates

`python control_center/refresh_registry.py` run. modules **124 → 129**, deps **807 → 874**, migrations **6 → 7**, decisions **10 → 14** (12 OPEN), divergence 0, 1 human-edited row preserved.

**New: `production_decision_authority`** (the requested separation):

| authority | n | meaning |
|---|---|---|
| `PRIMARY` | 19 | its output decides production actions today (pipeline, main, config, screener, setup, regime, risk, portfolio, legacy oracle, state, technicals…) |
| `SHADOW_ONLY` | 20 | evaluated + recorded; would decide **only** in `exit_engine_mode='new'` (all of `production/exits/`, `production/stops/`, `production/contracts/`) |
| `NONE` | 90 | no production decision authority (tests, docs, research, tooling) |

`production_used` was **not** fudged: because Phase 3 really does import the Stop/Exit/Contract modules into the production graph, their `production_used` is now **1**, and the stale Phase-1/2 "isolated → 0" overrides were **removed** rather than kept as a false statement. The "does it decide?" question is answered by the new column. `meta.production_exit_engine_mode = legacy` records the mode actually in force. `ARCHITECTURE.md` §4.1 now renders the authority table and §5 the breakdown.

Migrations now read:
`Stop Engine — SHADOW WIRED — NOT DECIDING` · `Exit Engine — SHADOW WIRED — LEGACY STILL AUTHORITATIVE` · `Stop + Exit wiring (Phase 3) — SHADOW WIRED — LEGACY AUTHORITATIVE; mode='new' NOT ENABLED`.

## M. Current production mode

```
exit_engine_mode = "legacy"        # production
```
`shadow` is validated and ready to be switched on for observation. `new` is implemented and tested but **not enabled** and must not be without explicit human approval.

## N. Remaining risks

1. **Live 20-session gate is PENDING, not failed** (§10). Evidence exists only from the production-equivalent backtest (280 qualifying sessions, 0 divergence). No live/paper session has been observed, so the production shadow stream — integration, data translation, real state/timing — is **unevidenced in operation**.
2. **Shadow adds compute in shadow mode** (adapter + Stop Engine + Exit Engine per held position per session). Negligible at 1–5 positions; not benchmarked under load.
3. **`new` mode fail-loud policy**: an engine failure in `new` mode proposes **no** exit and marks the record `PARTIAL`. This is deliberate (no silent legacy fallback) but means a buggy `new` engine would block exits rather than degrade — a human must notice.
4. **Input tightening carried over from Phase 2**: a bar missing `close`, or an illegal `session_date`, now fails loud in the shadow path (recorded as `INPUT_VALIDATION_MISMATCH`) where legacy would have degraded quietly. Divergences of this kind are expected on dirty data and are **not** engine defects.
5. `production_used` for the four pre-Phase-1 test files still reads 1 (blunt `production/` path heuristic) — logged as an OPEN registry-accuracy decision, deliberately not changed mid-phase.
6. The freeze document is new; the two reference levels (`STOP_LOSS → initial_stop_price`, `TRAILING_STOP → current_stop_price`) are still the one semantically-reviewed mapping (Phase-2 decision §1.3) — unchanged, now frozen.

## O. NEEDS HUMAN REVIEW

1. **Freeze the three files** as the Stop/Exit freeze face: `production/STOP_EXIT_V1_FREEZE.md`, `production/exits/adapter.py`, `production/exits/shadow.py`.
2. **Approve `shadow` for live/paper operation** (the flag flip) and decide whether the daily briefing should run in `shadow` from now on; 20 qualifying sessions then close the gate.
3. **Confirm the `new`-mode fail-loud policy** (block the exit and flag, versus fall back to legacy).
4. **Confirm the registry semantics**: `production_used = imported by production` and `production_decision_authority = decides`. If you prefer `production_used` to mean "in the production import closure for every module type" (i.e. tests = 0), that is the OPEN LOW decision in the registry and a separate pass.
5. **Divergence escalation policy**: today every divergence is recorded and none is auto-judged harmless. Decide whether any class should raise a louder alarm (e.g. `SHOULD_EXIT_MISMATCH` in live shadow).
6. **No commit / no push performed** (AGENT WORKFLOW: agent prepares, human commits).

---

## §21 Acceptance gates

| Gate | Status | Evidence |
|---|---|---|
| Stop Engine still owns stop calculations | ✅ | adapter calls `plan_from_position`/`update_stop`; no ATR math in `exits/` (test 9) |
| Exit Engine consumes StopPlan | ✅ | `ExitContext.stop_plan`; test 10 |
| Exit Engine does not recalculate trailing | ✅ | `test_no_trailing_math_in_exit_engine` + test 9 |
| Legacy remains authoritative in shadow | ✅ | 1088/1088 legacy decisions unchanged (§J) |
| Default = legacy | ✅ | test 1 |
| Shadow failures fail-open | ✅ | tests 6, 7 |
| Shadow has no trading side effects | ✅ | §J `all_equal = true`; tests 12, 13 |
| Rollback works by config flag | ✅ | test 11 |
| Historical replay = 0 unexplained divergence | ✅ | 1073 bars ×2 passes, 0 divergences, 0 adapter-vs-direct mismatches |
| 20 qualifying sessions = 0 divergence | ⚠️ **PENDING** | backtest: 126-session streak, 0 divergence; **live: 0 sessions observed** |
| Relevant tests green | ✅ | 117/117 |
| Control Center updated | ✅ | §L (authority column, 7 migrations, 14 decisions) |
| Freeze document created | ✅ | `production/STOP_EXIT_V1_FREEZE.md` |
| No protected trading logic modified | ✅ | §C |
| No files deleted | ✅ | — |
| No commit/push made | ✅ | — |

**Stopped.** Risk Engine, Entry Engine, Portfolio Engine, execution redesign, legacy deletion, state persistence redesign and stop/exit parameter optimisation were **not** started, and `exit_engine_mode` remains `legacy`.
