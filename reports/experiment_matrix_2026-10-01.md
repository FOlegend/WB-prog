# Experiment Matrix — Return Improvement Research

> ## ⚠️ R1 STATUS DOWNGRADED — see `pit_breadth_leak_2026-10-01.md`
> A point-in-time audit found Regime v1's **breadth engine is not point-in-time in historical replay**
> (constant 2025-07-31 tail), so R1's monotone result was an artefact. With a PIT-correct regime R1 becomes a
> non-monotone zig-zag (`+5.57 / −1.43 / −0.41 / +7.38 %` for TP 2.5/3.0/3.5/4.0) → **R1 is now
> INCONCLUSIVE-ON-LEAKED-DATA and must be re-run on a corrected regime before any conclusion.** The rest of
> the matrix (S1–S3, R2–R5, R7) is unaffected as a *plan*.

**Date:** 2026-10-01 · **Baseline:** commit `da9f5dc`, window 2024-01-02→2025-07-31
**Design rules (spec §12):** one variable at a time · frozen universe · frozen data · frozen costs · frozen
execution · no look-ahead · survivorship documented.
**Harness:** `research/harness.py` runs the existing `production/backtest.py::ProductionBacktest` unchanged
with at most one in-memory config override (production config file never edited).

---

## 1. The matrix

| # | Experiment | Hypothesis (one sentence) | Variable | Baseline | Candidate(s) | Primary metric | Status |
|---|---|---|---|---|---|---|---|
| **R1** | Exit target | A wider take-profit lets winners run, raising captured upside without degrading risk-adjusted performance | `take_profit_atr_mult` | 2.5 | **2.0, 3.0, 3.5, 4.0** (pre-registered grid) | Sharpe, MaxDD, avg R, PF + subperiod stability | **COMPLETE** |
| **R1b** | Exit target — response shape | If the R1 gain is real the response should turn over inside a bracketable range rather than keep rising to the grid edge | `take_profit_atr_mult` | 2.5 | 4.5, 5.0 (shape diagnosis only, **not** candidate selection) | Return / Sharpe / MaxDD trend; subperiod | **COMPLETE** |
| **S1** | Initial stop width | The stop is not cutting good trades (only 3/75 stop-outs ever reached +1 R), so widening it is unlikely to help — worth falsifying cheaply | `stop_atr_mult` | 1.5 | 1.2, 1.8, 2.0 | stop-out count, avg R of stop-outs, % stop-outs that would have won, MaxDD | DESIGNED — not run |
| **S2** | Trailing width | R1 shifted exits from TAKE_PROFIT to TRAILING_STOP; a wider trail may capture more of the post-TP drift | `trailing_atr_mult` | 1.5 | 2.0, 2.5 | MFE capture, avg R, MaxDD, Sharpe | DESIGNED — not run (must not be combined with R1) |
| **S3** | Trailing activation | Whether arming later (letting the trade breathe) improves the R distribution | `trailing_trigger_r` | 1.0 | 0.5, 1.5 | avg R, trade-level R distribution | DESIGNED — not run |
| **R2** | Entry quality (cohorts) | Some setup/regime cohorts carry positive expectancy and others negative; the entry signal can be improved | entry features (setup score, gap, distance-from-high, vol regime) | — | cohort partitions | avg R, PF per cohort | **PARTIAL** — D3 cohorts exist; the entry-gap signal was **refuted** by V4 (sub-period instability). Full R2 needs the logging fix (`signal_close`, `components`, `extension`) |
| **R3** | Portfolio capacity | `max_open_positions = 5` blocks otherwise-qualified trades | `max_open_positions` | 5 | 6, 7 | trade count, return, MaxDD | **BLOCKED** — magnitude is **unmeasurable** from current records (the pipeline skips setup evaluation when entries are blocked; D5's 68 sessions at-cap is a lower bound) |
| **R4** | Risk sizing | The risk budget floors to zero shares for 505/506 rejected setups, so loosening it admits more trades | `risk_per_trade` | 1.0 % | 1.5 %, 2.0 % | newly-admitted cohort avg R, return | **DEPRIORITISED** — Step 0 measured the newly admitted cohort as **negative expectancy** (−0.138 / −0.171 R) |
| **R5** | Regime participation | The regime multiplier/cap suppresses positive-expectancy opportunities | `divergence_cap`, base multipliers | cap 0.50 / BULL 1.0 | cap → 1.0; SIDEWAYS 0.5 → 0.75 | newly-admitted cohort avg R | **COUNTER-INDICATED** — BULL entries are the worst cohort (−0.321 R, n=24); removing the cap sizes up the losing cohort |
| **R7** | Logging fix | Several metrics are unmeasurable because fields are not persisted | (no strategy change) | — | P1–P8 in `phase5_record_quality_proposal_2026-10-01.md` | fields populated; backtest summary must stay byte-identical | DESIGNED — not applied (strategy-neutral prerequisite) |

---

## 2. Priority order and why it was (or was not) followed

The spec prescribes **R1 → R2 → R3 → R4 → R5 → R6**, changeable only if the data shows a larger
bottleneck elsewhere.

| Spec order | Actual | Reason |
|---|---|---|
| R1 exit target | **first (done)** | The bottleneck report ranks exit-target truncation as the highest **risk-free** lever: the stop is not cutting winners, and the target leaves an upper-bound 47 R on the table. Followed as prescribed. |
| R2 entry quality | deferred | Cannot be done properly from current artifacts: the one candidate entry signal (entry gap) **failed** the sub-period stability test (V4), and the extension/`components` fields are null (freeze audit: `INERT` / `{}`). Depends on **R7**. |
| R3 portfolio deployment | blocked | The dominant *gap* contributor, but (a) its magnitude is unobservable (§D5 note) and (b) Step 0 shows the newly admitted capital is negative-expectancy, i.e. it is a risk preference, not alpha. Running it would produce a number that is not actionable. |
| R4 risk sizing | deprioritised | Step 0 + lever-cohort analysis already measured this: every loosening lever admits a negative-expectancy cohort. Running a full portfolio experiment would re-confirm a known result at high cost. |
| R5 regime | counter-indicated | The BULL cohort is the worst performer; relaxing the cap would size it up. |
| R6 stop parameters | not first | The stop is not the bottleneck (3/75). Listed as S1–S3, ready to run if R1's result makes the exit path the focus. |

**Deviation from the literal order is data-driven and documented**, per the spec's own escape clause.

---

## 3. Pre-registration status (honesty note)

* **Pre-registered before running:** the R1 search space `{2.0, 2.5, 3.0, 3.5, 4.0}` (written into
  `research/run_tp_grid.py` before execution) and the primary success criteria — *"Sharpe must not fall
  and MaxDD must not worsen beyond −5.98 %"* (stated in `reports/phase5_plan_2026-10-01.md` before R1 ran).
* **Not pre-registered:** the R1b extension `{4.5, 5.0}`. It is explicitly labelled a **response-shape
  diagnostic**, not a candidate search, and its results are reported as such.
* **No parameter outside the listed grids was searched.**

---

## 4. Guardrails applied to every experiment (spec §16)

A candidate is only reported as promising if it improves **return *and* at least one of
Sharpe / Sortino / Calmar / MaxDD**, and if the improvement is **not concentrated in a single short
sub-period**. Raw return alone is never sufficient. Turnover (trade count) and exposure are reported
alongside so a "fewer, bigger trades" artefact is visible.

---

## 5. What was deliberately **not** done

* No risk increase was used to close the benchmark gap (spec §18).
* No combined multi-parameter search.
* No change to `exit_engine_mode` (still `legacy`), no production risk/stop/entry parameter changed, no
  frozen contract altered, no legacy deletion.
* No new backtest framework: every experiment reuses `ProductionBacktest`.
