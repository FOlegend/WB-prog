# Experiment Matrix (PIT-corrected) — Return Improvement Research

**Date:** 2026-10-01 · **Baseline:** the **corrected** one —
commit `da9f5dc` + the PIT fix, window 2024-01-02→2025-07-31 (396 sessions, **168 trades**,
**+5.57 %**, MaxDD **−10.39 %**, Sharpe **0.513**, Sortino 0.557, Calmar 0.338, PF **1.13**,
avg R **0.1878**, avg exposure **23.39 %**).

> This replaces `experiment_matrix_2026-10-01.md`, whose statuses and baseline were built on the
> pre-PIT (leak-contaminated) numbers. Superseded legend: `SUPERSEDED_PIT_ARTIFACTS.md`.

**Design rules:** one variable at a time · frozen universe (PIT bucket cache reused) · frozen data ·
frozen costs · frozen execution · no look-ahead · survivorship documented.
**Harness:** `research/harness.py` over the unchanged `ProductionBacktest`; overrides are in-memory only
(`ALLOWED_OVERRIDES` whitelist) — `production/config.py` is never edited.

**Pre-registered acceptance criteria for every lever:**

1. **Sharpe must improve** on the corrected baseline (0.513);
2. **MaxDD must not worsen beyond −10.39 %**;
3. the improvement must hold in **both** sub-periods (2024 and 2025).

---

## 1. The matrix

| # | Experiment | Hypothesis (one sentence) | Variable | Baseline | Candidate(s) | Primary metric | Status |
|---|---|---|---|---|---|---|---|
| **R1** | Exit target | A wider take-profit captures more upside | `take_profit_atr_mult` | 2.5 | 2.0 / 3.0 / 3.5 / 4.0 (pre-registered grid) | Sharpe, MaxDD, avg R + sub-period stability | **CLOSED — INCONCLUSIVE.** Zig-zag response (2.5 good → 3.0/3.5 negative → 4.0 best), winner unsupported by neighbours, 2025 negative in 4 of 5 settings. **No `STOP_EXIT_V1` revision justified.** `reports/pit_correction_and_rebaseline_2026-10-01.md` §G |
| **L1** | Risk sizing / deployment | The "protective cap" reading is rejected, and the capital a modest sizing lever would admit measured **positive** (+0.349 R at 1.5 %), so a small increase should improve return without proportional drawdown | `risk_per_trade` | 1.0 % | **1.25 %, 1.5 %** | Sharpe, MaxDD, realised risk %, sub-periods | **RUNNING** — `research/run_lever_grid.py` |
| **L2** | Portfolio capacity (direct) | `max_open_positions` blocks 131 sessions; a 6th slot may admit profitable trades | `max_open_positions` | 5 | **6** | trade count, return, Sharpe, MaxDD | **RUNNING** — same runner |
| **S1** | Initial stop width | The stop is *mostly* not cutting winners (11/84 stop-outs ever reached +1 R), so widening it should not help — worth falsifying cheaply | `stop_atr_mult` | 1.5 | 1.2, 1.8, 2.0 | stop-out count, % stop-outs that would have won, MaxDD | DESIGNED — not run |
| **S2** | Trailing width | R1's gain arrived *through* the trailing exit, so trailing width is the natural follow-up | `trailing_atr_mult` | 1.5 | 2.0, 2.5 | MFE capture, avg R, MaxDD | DESIGNED — must not be combined with R1 |
| **S3** | Trailing activation | Arming later may let trades breathe | `trailing_trigger_r` | 1.0 | 0.5, 1.5 | avg R distribution | DESIGNED |
| **R2** | Entry quality (cohorts) | Some cohorts carry the edge and others destroy it | entry features | — | cohort partitions | avg R / PF per cohort | **BLOCKED** — the entry-gap signal is refuted (V4, still non-monotone on corrected data); `extension_from_pivot_pct` is null for **all 168** trades and `components` is `{}` → needs **R7** |
| **R3** | Capacity *magnitude* | How much was foregone when entries were blocked? | (measurement, not a lever) | — | — | foregone-setup count/value | **BLOCKED** — unobservable by design (setup evaluation is skipped when entries are blocked). Needs **R7**; L2 only tests the *lever*, not the magnitude |
| **R5** | Regime participation | The cap / multipliers suppress positive-expectancy opportunities | `divergence_cap`, base multipliers | cap 0.50 / BULL 1.0 | cap → 1.0; SIDEWAYS 0.5 → 0.75 | newly-admitted cohort avg R | **NO BASIS** — the corrected regime already reaches mult 1.0 on 61 sessions; the fully-sized (1.0) cohort **underperformed** the 0.5 cohort (+0.067 R vs +0.227 R, n = 41 vs 127) |
| **R7** | Logging fix (strategy-neutral) | Several metrics stay unmeasurable because fields are not persisted | (no strategy change) | — | P1–P8 (+ blocked-entry snapshot) | fields populated; **backtest summary must stay byte-identical** | DESIGNED — not applied |

---

## 2. Priority order and why

| Order | Direction | Reason |
|---|---|---|
| 1 | **L1 + L2** (running) | The Step-0b reversal made deployment the only lever with *positive* supporting evidence. Both are single-variable and directly measurable |
| 2 | **R7** logging | Prerequisite for R2 and R3 — without it those questions cannot be answered at all |
| 3 | **S1–S3** | The exit path is not closed; only the *target* half is (§G). Stops/trailing have never been tested on corrected data |
| — | **Closed / no basis** | **R1** (inconclusive), **R5** (no basis) |

**Deviation from the heuristic R1→R2→R3→R4→R5→R6 order is data-driven and documented**, per the spec's own
escape clause: R1 is closed, R2/R3 are blocked on logging, and the strongest corrected evidence now points
at sizing/capacity (R4/R3).

---

## 3. Honesty notes

* **Pre-registered:** the R1 grid `{2.0, 2.5, 3.0, 3.5, 4.0}` and the three acceptance criteria above.
  The R1 extension to 4.5/5.0 is **withdrawn** and excluded from the corrected run.
* **Not pre-registered:** the L1/L2 candidate values were chosen *after* the Step-0b reversal, though before
  running — disclosed as such.
* **Watch for:** the corrected baseline's return is a **2024 effect** (2024 +6.50 %, 2025 −0.88 %); any lever
  that only helps in 2024 fails criterion 3.
* **No parameter outside the listed grids is searched.** Raw return alone is never sufficient.

---

## 4. What was deliberately **not** done

* No leverage / exposure increase to close the benchmark gap (matching SPY would need k ≈ 6.5× with an
  implied MaxDD of −67.6 %).
* No combined multi-parameter search.
* No change to `exit_engine_mode` (still `legacy`), no production parameter changed, no frozen contract
  altered, no legacy deletion.
* No new backtest framework — every experiment reuses `ProductionBacktest`.
