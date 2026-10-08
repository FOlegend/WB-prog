# Experiment Matrix (PIT-corrected) — Return Improvement Research

**Date:** 2026-10-02 (updated after R7 and S1–S3) · **Baseline:** the **corrected** one —
commit `da9f5dc` + the PIT fix, window 2024-01-02→2025-07-31 (396 sessions, **168 trades**,
**+5.57 %**, MaxDD **−10.39 %**, Sharpe **0.513**, Sortino 0.557, Calmar 0.338, PF **1.13**,
avg R **0.1878**, avg exposure **23.39 %**).

> This supersedes `experiment_matrix_pitcorrected_2026-10-01.md`, which was written before
> R7 and S1–S3 existed. Pre-PIT predecessor: `experiment_matrix_2026-10-01.md` (void).

**Design rules:** one variable at a time · frozen universe (PIT bucket cache reused) · frozen
data · frozen costs · frozen execution · no look-ahead · survivorship documented.
**Harness:** `research/harness.py` over the unchanged `ProductionBacktest`; overrides are
in-memory only (`ALLOWED_OVERRIDES` whitelist) — `production/config.py` is never edited.

**Pre-registered acceptance criteria (every lever):**

1. **C1** Sharpe must improve on 0.513
2. **C2** MaxDD must not worsen beyond −10.39 %
3. **C3** the improvement must hold across sub-periods (2024 / early 2025 / late 2025)
4. **C4** a plausible mechanism must be visible in trade-level data
5. **§12 robustness** smooth response + neighbour support + stable trade count / exposure —
   a lone spike is **not** evidence

---

## 1. The matrix

| # | Experiment | Variable | Baseline | Candidates | Status | Evidence |
|---|---|---|---|---|---|---|
| **R1** | Exit target | `take_profit_atr_mult` | 2.5 | 2.0 / 3.0 / 3.5 / 4.0 | **CLOSED — INCONCLUSIVE** | Zig-zag response; the winner (4.0) unsupported by neighbours; 2025 negative in 4 of 5 settings. No `STOP_EXIT_V1` revision justified. |
| **L1** | Risk sizing | `risk_per_trade` | 1.0 % | 1.25 %, 1.5 % | **CLOSED — REJECTED** | Return/Sharpe rose but MaxDD breached C2 (−14.15 %) and 2025 did not confirm. |
| **L2** | Portfolio capacity | `max_open_positions` | 5 | 6 | **CLOSED — REJECTED** | Return 0.63 %, Sharpe 0.090, PF 1.01, 2025 −4.62 %; all three criteria failed. |
| **R7** | Observability (strategy-neutral) | — none — | — | P1–P8 + capacity snapshot | **COMPLETE** | Backtest **byte-identical** (15 critical trade fields × 168 trades, 0 mismatches); 115/115 existing + 58/58 new tests; freeze audit 0 MISMATCH. `reports/r7_observability_2026-10-02.md` |
| **S1** | Initial stop width | `stop_atr_mult` | 1.5 | 1.2 / 1.8 / 2.0 | **1.2 REJECTED · 1.8 & 2.0 UNRESOLVED (no effect above noise)** | 1.8: Sharpe 1.107, MaxDD −8.73 %, but the paired block bootstrap gives ΔSharpe **+0.595 [−0.089, +1.503]** — **CI spans zero** (P(better)=0.952). S1-D attributes the gain to **trade selection**, not stop width: on shared trades 1.8 is **−0.009 R**, its 58 unique trades +0.229 R while 2.0's 60 unique trades are **−0.049 R** |
| **S1-D** | Decoupling analysis | (no parameter) | — | — | **COMPLETE** | Four couplings measured. **Largest is one R7 revealed**: a wider stop raises the risk budget needed to buy 1 share, so it silently acts as an **admission filter** (rejections 425 → 472 → 570; trades 168 → 145 → 128; 43 % of the book sits within 1.5 shares of the boundary). |
| **S1-P** | Architecture decision | — | — | — | **PROPOSED — awaiting human** | Whether to decouple the sizing divisor from the stop distance so stop width becomes testable. **Recommendation: NO, not now** — no evidence the 1.5 stop costs anything, and the one apparent gain fails the noise floor. `reports/s1_stop_width_decision_proposal_2026-10-02.md` |
| **S1-U** | Sampling uncertainty | (all S1–S3) | — | paired block bootstrap | **COMPLETE — 0 of 7 clear the noise floor** | 5,000 paired block-bootstrap draws (10-day blocks) on 395 sessions. **Every** ΔSharpe and ΔavgR 95 % CI spans zero. Strongest is S1 1.8 at P(better) = 0.952. `reports/bootstrap_uncertainty_2026-10-02.md` |
| **P6** | Inert extension filter | (no parameter) | — | counterfactual replay | **MEASURED — cosmetic, not a missing control** | Reconstructing `prior_high20` exactly as the breakout path defines it, the filter would block **0 of 168 trades (0.0 %)**, with **0 near-misses**. Max observed extension **+0.0264** vs the 0.03 threshold; median **−0.0434** — pullback entries are *below* the pivot by construction. `reports/p6_extension_impact_2026-10-02.md` |
| **⚠️ BS** | **Breadth staleness (live hazard)** | (no parameter) | — | staleness sweep | **OPEN — highest severity, awaiting human** | A live run today computes **50 % of the regime from a breadth series 428 days old** while the other 50 % comes from SPY 57 days old — the two halves are **371 days apart**. Moving only the breadth tail flips the regime **BEAR→BULL→BEAR** with `position_size_mult` 0.0→1.0→0.0. Mirror image of the PIT leak: `slice_to_end` constrains the future, **nothing constrains the past**. `reports/breadth_staleness_impact_2026-10-02.md` |
| **S2** | Trailing width | `trailing_atr_mult` | 1.5 | 2.0 / 2.5 | **CLOSED — REJECTED** | Both worse on every criterion. Mechanism: wider trailing turned long losers into short losers (trailing avg R +0.123 → −0.357). |
| **S3** | Trailing activation | `trailing_trigger_r` | 1.0 | 0.5 / 1.5 | **CLOSED — REJECTED** | Single peak **at the control**. 0.5 quadrupled trailing exits (8→35) and turned them negative (+0.123 → −0.172R). |
| **R2** | Entry quality (cohorts) | entry features | — | 21 pre-registered cohorts | **CLOSED — NO FINDING** | 0 survive Benjamini-Hochberg q ≤ 0.10 (smallest permutation p = 0.119, so nothing is significant even *before* correction). The top deltas are artefacts of near-universal components leaving 1–3 trades in the contrast group. `reports/r2_r3_cohorts_capacity_2026-10-02.md` |
| **R3** | Capacity magnitude | (measurement) | — | — | **CLOSED — MEASURED, constraint not costing value** | 11,280 blocked candidates examined under R7 Tier B. Foregone cohort avg R **−0.003** (regime-blocked) / **+0.046** (slot-exhausted) vs **+0.188** for trades actually taken. Independently explains the L2 rejection. |
| **R5** | Regime participation | `divergence_cap`, multipliers | cap 0.50 | — | **NO BASIS** | The fully-sized (mult 1.0) cohort underperformed the 0.5 cohort. |

---

## 2. Where the research now stands

```
R7 observability        COMPLETE   ──► unblocked R2, R3, S1-D, P6
S1 initial stop         UNRESOLVED (no effect above the noise floor)
S1-D decoupling         COMPLETE   ──► 1.8's gain is SELECTION, not stop width
S1-U uncertainty        COMPLETE   ──► 0 of 7 variants clear the noise floor
S1-P architecture       PROPOSED   ──► awaiting human decision (recommend: NO)
S2 trailing width       REJECTED   ── clean single-variable, mechanism identified
S3 trailing activation  REJECTED   ── clean single-variable, mechanism identified
R2 entry cohorts        NO FINDING ── 21 cohorts, 0 survive multiple comparison
R3 capacity magnitude   MEASURED   ── foregone cohort worth ~1/4 of taken trades
P6 extension filter     MEASURED   ── would block 0/168; cosmetic, not a control gap
BS breadth staleness    OPEN       ── ⚠️ LIVE HAZARD: regime halves 371 days apart
R1 / L1 / L2            CLOSED
```

**Every empirical research thread is closed, and none supports a production change.** Two
items remain open, and both are decisions rather than experiments:

* **S1-P** — whether to amend the frozen Stop/Exit contract so stop width becomes testable.
  **Recommended: no.** S1-D showed the 1.8 effect is not attributable to stop width, and
  S1-U showed it does not exceed the noise floor either.
* **⚠️ BS (breadth staleness)** — **higher severity than S1-P**, because it affects live
  operation rather than research quality. A live run today mixes a 2026-08 HMM reading
  with a 2025-07 breadth reading. The real blocker is not the policy but the absence of a
  breadth refresh path past 2025-07-31.

---

## 3. The S1 couplings (most important entry in this matrix)

`stop_atr_mult` is **not** a single-variable lever in this architecture. S1-D measured four
couplings, in descending order of impact (`reports/s1_decomposition_2026-10-02.md`):

| # | coupling | measured impact |
|---|---|---|
| 1 | **Admission filter** — `shares = floor(risk_budget / (atr × stop_mult))`, so a wider stop needs more budget to buy 1 share and rejects more candidates before they become trades | **largest**: rejections 425 → 472 → 570, trades 168 → 145 → 128. 43 % of the book sits within 1.5 shares of the affordability boundary, so the integer floor makes stop width a **minimum-volatility requirement** |
| 2 | **Trade selection** — which candidates pass | **carries the whole 1.8 effect**: shared trades **−0.009 R**, 1.8's 58 unique trades **+0.229 R**, 2.0's 60 unique trades **−0.049 R** |
| 3 | **The R unit** — `r_multiple` denominator and the trailing trigger both derive from the same distance | penalises 1.8 by −0.031 R; it does **not** explain the gain (control's own 168 trades score 0.1565 in 1.8's unit vs 0.1878 recorded) |
| 4 | **The trailing arm** — `r_dist = atr × stop_atr_mult` (`STOP_EXIT_V1_FREEZE.md:54-55`) | immaterial: +0.01 R on 3 % of trades |

Dollar risk per trade is **flat** across the grid ($5.34 → $5.57), so 1.8 is not de-risking;
only position value falls ($112.59 → $100.52). Its MaxDD improvement comes from smaller
positions, not better risk control.

**Consequence:** the 1.8 result is a **selection effect**, and its unstable response surface
is the mechanical consequence of a filter being tuned on one window. Resolving it would
require decoupling the sizing divisor from the stop distance — a frozen-contract amendment,
proposed (not implemented) in `reports/s1_stop_width_decision_proposal_2026-10-02.md`, with
the recommendation **not** to do it.

---

## 4. Honesty notes

* **Pre-registered before any run:** the S1 `{1.2, 1.5, 1.8, 2.0}`, S2 `{1.5, 2.0, 2.5}`
  and S3 `{0.5, 1.0, 1.5}` grids, and criteria C1–C4 + §12. No value outside a grid was
  tested; no combined search was run (§15).
* **The control was re-run inside every stage** and reproduced +5.57 % / Sharpe 0.513 /
  MaxDD −10.39 % / 168 trades exactly, three times out of three.
* **Not pre-registered:** the L1/L2 candidate values (disclosed 2026-10-01); both are now
  closed.
* **Small samples:** S2's trailing class has only **8 trades** at the control. Trailing
  conclusions rest on single-digit counts.
* **R2 cohort structure:** the seven components heavily co-fire (three in >94 % of trades)
  and 167 of 168 trades fired ≥4 components, so most cohort contrasts leave 1–3 trades in
  the comparison group. Cohort comparisons are **not independent**; BH controls the false
  discovery rate across the family but cannot manufacture independence.
* **R3 is an upper bound:** the foregone cohort would have competed for the same cash and
  slots as the trades actually taken, and that contention is not resolved (it is L2, which
  is rejected). Read it as "what was available", not "what would have been earned".
* **S1–S3 have no power at this sample size.** The paired block bootstrap
  (`reports/bootstrap_uncertainty_2026-10-02.md`) shows **0 of 7 variants** produce a
  Sharpe or avg-R CI excluding zero. Wide intervals reflect 168 trades over 19 months, not
  proof that the variants are equivalent — the correct word is *undetermined*.
* **Trade overlap:** S1 variants share only 30–39 % of trades with the control
  (Jaccard 0.30–0.39) — they are different portfolio paths, not small perturbations.
  S2/S3 overlap 0.86–0.90 and are near-clean.
* **The corrected baseline is a 2024 effect** (2024 +6.50 %, 2025 −0.88 %); C3 catches this.
* **No parameter outside the listed grids is searched.** Raw return alone is never
  sufficient, and "optimal" is claimed for nothing.

---

## 5. What was deliberately **not** done

* No leverage / exposure increase to close the benchmark gap (matching SPY would need
  k ≈ 6.5× with an implied MaxDD of −67.6 %).
* No combined multi-parameter search, and specifically **no TP × stop** search (§15) — R1
  was inconclusive, so combined optimisation would make attribution impossible.
* No change to `exit_engine_mode` (still `legacy`), no production strategy parameter
  changed, no frozen contract altered, no legacy deletion.
* No new backtest framework — every experiment reuses `ProductionBacktest`.
* **No commit, no push.**

---

## 6. Reports

| Document | Content |
|---|---|
| `reports/r7_observability_2026-10-02.md` | R7 acceptance, field-by-field provenance, equivalence proof |
| `reports/stop_trailing_experiments_pitcorrected_2026-10-02.md` | S1–S3 full results, mechanism, robustness, limitations |
| `reports/r2_r3_cohorts_capacity_2026-10-02.md` | R2 cohort attribution + R3 capacity magnitude |
| `reports/stop_trailing_s{1,2,3}_pitcorrected_2026-10-02.json` | raw machine-readable results |
| `reports/r2_entry_cohorts_pitcorrected_2026-10-02.json` | raw cohort table incl. per-trade rows |
| `reports/r3_capacity_magnitude_pitcorrected_2026-10-02.json` | raw foregone-cohort rows |
| `reports/r7_equivalence_2026-10-02.json` | behavioural-equivalence proof |
| `reports/pit_correction_and_rebaseline_2026-10-01.md` | the PIT fix and re-baseline |
| `reports/SUPERSEDED_PIT_ARTIFACTS.md` | pre-PIT artifacts, marked void |
