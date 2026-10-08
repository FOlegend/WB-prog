# SUPERSEDED ARTIFACTS — POINT-IN-TIME DATA INTEGRITY FAILURE

**Legend:** `SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE`
**Date of record:** 2026-10-01 · **Cause:** Regime v1's breadth engine was not point-in-time in
historical replay (finding: `pit_breadth_leak_2026-10-01.md`; fix + re-baseline:
`pit_correction_and_rebaseline_2026-10-01.md`)

Every artifact below was produced **before** the PIT correction. In all of them, Regime v1's breadth
component (50 % of the composite) was a **constant** equal to the 2025-07-31 cache tail, so:

* they are **not** a faithful replay of the frozen decision function;
* their performance figures are **leak-contaminated and must not be quoted**;
* they are retained **only as audit evidence** of what the leak looked like.

Each JSON artifact also carries a machine-readable marker in its root object:

```json
"_status": "SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE",
"_superseded_by": "<corrected artifact>",
"_superseded_reason": "..."
```

---

## 1. Machine-readable artifacts

| Superseded artifact | Corrected replacement |
|---|---|
| `production_bt_2024-01-01_2025-07-31.json` | `production_bt_pitcorrected_2024-01-01_2025-07-31.json` |
| `phase4_diag_data_2026-10-01.json` (D1–D6) | `phase4_diag_data_pitcorrected_2026-10-01.json` |
| `phase4_verify_2026-10-01.json` (V1–V4) | `phase4_verify_pitcorrected_2026-10-01.json` |
| `phase5_step0_marginal_cohort_2026-10-01.json` | `phase5_step0_marginal_cohort_pitcorrected_2026-10-01.json` |
| `phase5_step0b_lever_cohorts_2026-10-01.json` | `phase5_step0b_lever_cohorts_pitcorrected_2026-10-01.json` |
| `research_baseline_2026-10-01.json` | `research_baseline_pitcorrected_2026-10-01.json` |
| `research_tp_grid_2026-10-01.json` (R1) | `research_tp_grid_pitcorrected_2026-10-01.json` |
| `research_tp_grid_ext_2026-10-01.json` (R1b, TP 4.5/5.0) | **withdrawn, not regenerated** — the extension was a leak artefact and is excluded from the re-run by design |

## 2. Narrative artifacts

| Artifact | Status |
|---|---|
| `phase4_diagnostics_2026-10-01.md` | figures contaminated → superseded by the corrected D1–D6 |
| `phase4_review_sheet_2026-10-01.md` | figures contaminated |
| `phase5_step0_report_2026-10-01.md` | figures contaminated |
| `phase5_plan_2026-10-01.md` | its benchmark context was leak-based |
| `return_bottleneck_report_2026-10-01.md` | banner added; magnitudes re-derived in the corrected report |
| `return_improvement_research_2026-10-01.md` | banner added; **R1 recommendation WITHDRAWN** |
| `experiment_matrix_2026-10-01.md` | banner added; R1 status downgraded |
| `pit_breadth_audit_2026-10-01.json` / `pit_breadth_leak_2026-10-01.md` | **NOT superseded** — these are the *finding*, produced before the fix and still valid as the record of the defect |

## 3. What was NOT invalidated by the leak

| Item | Why it still stands |
|---|---|
| Stop/Exit parity (`phase2/3_exit_parity_replay_*.json`) | engine-vs-engine comparison on **identical entries**; the regime never enters it |
| Step-0 harness audit (`step0_harness_audit_2026-10-01.json`) | classifies a simulator guard defect; engine-vs-engine |
| Phase-3 shadow coverage (shadow vs legacy) | both arms consumed the **same** regime; the comparison itself is valid (only its performance context changes) |
| Freeze conformance audit (`freeze_conformance_audit_*.json`) | checks code-vs-contract, not performance |
