# R2 + R3 — Entry Cohorts and Capacity Magnitude (PIT-corrected)

**Date:** 2026-10-02 · **Baseline:** the PIT-corrected one (396 sessions, 168 trades,
+5.57 %, Sharpe 0.513, MaxDD −10.39 %, PF 1.13, avg R 0.1878, exposure 23.39 %)

> R2 and R3 were both marked **BLOCKED** until R7 landed, because `components` was
> permanently `{}` and the blocked candidate population existed nowhere in the record.
> This is the first round of research they unblocks.

---

## 0. Summary of both verdicts

| | Verdict | One line |
|---|---|---|
| **R2** entry/setup cohorts | **NO FINDING** | 21 pre-registered cohorts, **0 survive** multiple-comparison control. The entry features the records can supply do not separate edge from drag. |
| **R3** capacity magnitude | **MEASURED — the constraint is not costing anything** | 11,280 blocked candidates examined. The foregone cohort is worth **avg R −0.0029** (PF 0.995) where the regime blocked it and **+0.046** (PF 1.08) where the slot budget ran out — against **+0.188** for the trades actually taken. |

Read together, these close the two questions the experiment matrix had been unable to
answer for several phases, and both answers are negative. That is a result, not a gap.

---

# R2 — Entry / setup cohorts

## R2.1 What became answerable

R7's `score_components` (parsed from the frozen engine's own `entry_reason`) is populated
on **168/168** trades, so component attribution no longer requires reconstructing anything.

Component frequency across the baseline trades:

| Component | fires | share | weight in score |
|---|---:|---:|---:|
| `px>200SMA` | 165 | 98.2 % | 0.10 |
| `px>50SMA` | 160 | 95.2 % | 0.10 |
| `50>200` | 158 | 94.0 % | 0.10 |
| `vol_contract` | 153 | 91.1 % | 0.20 |
| `RS_strong` | 149 | 88.7 % | 0.10 |
| `nearEMA` | 79 | 47.0 % | 0.25 |
| **`reversal`** | **12** | **7.1 %** | 0.15 |

**The score in practice is a trend-structure filter.** Five of the seven components fire
in >88 % of trades, so they cannot discriminate; the score is effectively carried by
`px>50SMA + px>200SMA + 50>200 + vol_contract` (the 0.10/0.10/0.10/0.20 terms), and
`reversal` — one of the four conditions the Setup v1 freeze document names — is nearly
absent. The number of components fired is tightly clustered: 5 components in 100 trades,
6 in 51, 4 in 15, and **0 trades fired fewer than 3**.

This confirms, now with outcome data, the freeze audit's standing `CLARITY` verdict: the
pullback is a weighted **sum**, not the **conjunction** its contract describes.

## R2.2 Cohorts tested (pre-registered before outcomes were inspected)

21 cohorts in six families: each of the 7 components (present vs absent); component-count
bands; the freeze document's named conjunction; setup-score bands; the entry-quality fields
R7 added (gap, ATR%); and regime × setup interactions.

Guards applied: a cohort needs **n ≥ 20** to be eligible, a **|Δ avg R| ≥ 0.10** effect,
a **permutation p** (5,000 relabellings), and a **Benjamini-Hochberg q ≤ 0.10** across the
family. Sub-period stability (2024 / early 2025 / late 2025) is reported for every cohort,
matching the C3 criterion used in S1–S3.

## R2.3 Results — ranked by effect size

| cohort | n | avg R (flagged) | avg R (rest) | Δ | BH q | stable? |
|---|---:|---:|---:|---:|---:|---|
| `n_components>=4` | 167 | 0.195 | −1.000 | +1.195 | — | n/a (167/168) |
| `component:px>200SMA` | 165 | 0.198 | −0.348 | +0.546 | 0.79 | yes |
| `regime:BULL + score>=0.75` | 18 | −0.271 | 0.243 | −0.513 | 0.79 | yes |
| `component:50>200` | 158 | 0.211 | −0.184 | +0.395 | 0.79 | yes |
| `component:reversal` | 12 | 0.461 | 0.167 | +0.294 | 0.79 | no |
| `conjunction:nearEMA+vol+RS` | 50 | −0.009 | 0.271 | −0.280 | 0.79 | yes |
| `component:nearEMA` | 79 | 0.064 | 0.298 | −0.233 | 0.79 | no |
| `regime:BULL` | 48 | 0.033 | 0.250 | −0.216 | 0.79 | no |
| `regime:SIDEWAYS` | 120 | 0.250 | 0.033 | +0.216 | 0.79 | no |
| `component:px>50SMA` | 160 | 0.197 | 0.000 | +0.197 | 0.81 | no |
| `component:vol_contract` | 153 | 0.173 | 0.342 | −0.169 | 0.79 | no |
| `score>=0.75` | 64 | 0.117 | 0.231 | −0.114 | 0.79 | no |
| `atr_pct>=3` | 97 | 0.233 | 0.126 | +0.107 | 0.79 | no |
| `gap>0` | 82 | 0.142 | 0.232 | −0.090 | 0.79 | no |
| `component:RS_strong` | 149 | 0.178 | 0.261 | −0.083 | 0.83 | no |

**Surviving findings: 0.**

The smallest permutation p-value across all 21 cohorts is **0.119** — so nothing is
significant even *before* the multiple-comparison correction, let alone after it.

## R2.4 Why the large-looking deltas are artefacts, not edges

Three of the top rows look dramatic and are not:

* **`n_components>=4` (Δ +1.195)** — this is 167 of 168 trades. The single trade that fired
  ≤3 components happened to lose. A one-observation comparison is not a cohort.
* **`component:px>200SMA` (Δ +0.546)** — 165 of 168. Same structure: the "unflagged" group
  is 3 trades. The engine's trend filter is near-universal, so *any* component that
  correlates with it inherits a meaningless contrast.
* **`regime:BULL + score>=0.75` (Δ −0.513)** — n = 18, below the n ≥ 20 threshold. It is
  reported because it is superficially the most actionable-looking row, and it is exactly
  the kind of small-n, large-effect result that must not drive a decision.

Note also that the two largest *eligible* cohorts pull in **opposite directions**
(`conjunction:nearEMA+vol+RS` −0.280 vs `component:nearEMA` −0.233 with
`conjunction:all_four` at n = 2), and the regime rows are mirror images of each other by
construction (BULL vs SIDEWAYS partition the same trades). The family is not producing
independent evidence.

## R2.5 Verdict

**NO FINDING.** On the corrected baseline, with the observability R7 added, **no entry or
setup cohort the records can supply separates winners from losers at any conventional
significance level.** The apparent effects are explained by (a) near-universal components
leaving 1–3 trades in the contrast group, and (b) sub-threshold sample sizes.

This is a genuine negative result and it is informative: the entry side is not where the
remaining return problem lives. It is also consistent with the earlier V4 finding that the
entry-gap signal is non-monotone even on corrected data — and, per §17, that signal was
explicitly **not** revived here; it was re-tested only as one of 21 pre-registered cohorts
and duly failed.

---

# R3 — Capacity / entry-block magnitude

## R3.1 What was measured

With R7 Tier B enabled, the **unmodified** production `evaluate_setup` +
`size_swing_position` were run over the candidates on every session where entries were
blocked, and every valid setup was pushed through the already-validated Step-0 simulator
(`simulate_trade`: next-open entry, frozen gap filter, frozen Stop/Exit Engine). The
cohort is measured **in the exact portfolio state in which it was blocked** — the baseline
state machine was replayed, so the cash, the open positions and the slot count are the real
ones.

No constraint was changed. `max_open_positions` = 5, `risk_per_trade` = 1 %, and every
other parameter remained at the frozen baseline (asserted in code before the run).

## R3.2 Results

| blocked stage | sessions | candidates examined | setup valid | risk allowed | risk denied | simulated | avg R | PF | % positive |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **pre-setup** (regime defensive / slots full) | 267 | 8,010 | 4,724 | 0 | **4,724** | 4,376 | **−0.0029** | 0.995 | 45.2 % |
| **post-setup** (session slot budget exhausted) | 129 | 3,270 | 610 | 0 | 0 | 563 | **+0.0459** | 1.08 | 45.1 % |
| *contrast: trades actually taken* | — | — | — | — | — | **168** | **+0.1878** | **1.13** | 47.6 % |

Sub-period split of the foregone cohort:

| sub-period | n | avg R | sum R | % positive |
|---|---:|---:|---:|---:|
| 2024 | 3,130 | +0.078 | +242.97 | 47.6 % |
| 2025 Jan–Mar | 706 | **−0.343** | −241.98 | 30.5 % |
| 2025 Apr–Jul | 1,103 | +0.011 | +12.37 | 47.7 % |

Simulation skips: 240 `GAP_TOO_HIGH` (the frozen 2 % filter did its job), 155
`no_exit_within_horizon`.

## R3.3 Reading

**The capacity constraint is not costing anything.** The foregone cohort is worth
**−0.003 R on average where the regime blocked it** and **+0.046 R where the slot budget
ran out** — against **+0.188 R for the trades the system actually took**. A candidate
admitted from the blocked pool would have been, on average, roughly **one quarter as good
as the trades already being taken**, and in the regime-blocked case indistinguishable from
zero.

This is the direct, measured answer to the question the capacity proposal was written to
ask, and it **independently explains the L2 rejection**. L2 tested `max_open_positions`
5 → 6 as a lever and found return 0.63 %, Sharpe 0.090, PF 1.01. R3 now supplies the
mechanism: the sixth slot would have been filled from a pool averaging +0.046 R and
PF 1.08, which is enough to dilute a PF-1.13 portfolio and not enough to lift it. The lever
failed because the opportunity behind it was not there — not because the test was wrong.

The 2025 Jan–Mar figure (−0.343 R, 30.5 % positive) is worth flagging separately: the
foregone pool was actively harmful in that window, so the constraint was *protecting* the
portfolio then. This is the corrected-data counterpart to the earlier "risk cap is
protective" claim that the PIT leak had falsified — it now holds in a narrower, properly
measured form, for the **entry-block** constraint rather than for the risk cap.

## R3.4 Important caveat

This is an **upper bound** on recoverable value, not a return forecast. The foregone trades
would have competed for the same cash and the same slots as the trades that were taken,
and that contention is **not** resolved here — resolving it is a lever experiment, and the
one lever that was run (L2) is rejected on its own evidence. Read the table as "what was
available", not "what would have been earned".

---

# Combined status

| Item | Verdict | Basis |
|---|---|---|
| **R2** entry/setup cohorts | **NO FINDING** | 21 pre-registered cohorts, 0 survive BH q ≤ 0.10; smallest permutation p = 0.119 |
| **R3** capacity magnitude | **MEASURED — constraint is not binding on value** | 11,280 blocked candidates; foregone avg R −0.003 / +0.046 vs +0.188 taken |

**What this closes.** R2 and R3 were the two items the matrix had carried as BLOCKED. Both
are now answered, and both answers are negative: the entry features the records can supply
do not separate edge from drag, and the capacity constraint is not suppressing a better
cohort. Neither result justifies any production change.

**What remains open.**

1. **S1 / the initial stop** is still the only live question. Its 1.8 candidate remains
   INCONCLUSIVE — an isolated peak (§12 fails) that cannot be attributed to stop width
   because `stop_atr_mult` simultaneously moves position size, the R unit and the trailing
   arming threshold. Resolving that needs the `_exit_check` decoupling, which is a
   **frozen-contract amendment** requiring human approval.
2. **P6** — `extension_from_pivot_pct` is still null and the frozen extension filter is
   still inert. R2 could not use extension as a cohort dimension for exactly this reason.
3. The R2 negative result is bounded by what the records contain. A genuinely predictive
   entry feature may exist and simply not be reconstructable from daily OHLCV plus the
   frozen setup score.

---

# Safety

```
production strategy parameters changed = NO
exit_engine_mode                        = legacy
frozen contracts                        = untouched
commits / pushes                        : none
```

New in this round (research only, no production import):

* `research/r2_entry_cohorts.py`
* `research/r3_capacity_magnitude.py`
* `reports/r2_entry_cohorts_pitcorrected_2026-10-02.json`
* `reports/r3_capacity_magnitude_pitcorrected_2026-10-02.json`

One implementation defect was found and fixed during this round: the Benjamini-Hochberg
routine initially sorted a `NaN` permutation p-value (from cohorts with fewer than 2
members) into the family, corrupting the monotone step-up. Cohorts too small to relabel are
now excluded from the family count and report `q = null`. The corrected run was used for
every number above; the verdict is unchanged either way, because the smallest p-value
(0.119) is above 0.05 even before any correction.
