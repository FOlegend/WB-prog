# S1–S3 — Sampling Uncertainty (block bootstrap)

**Date:** 2026-10-02 · **Baseline:** the PIT-corrected one (168 trades, Sharpe 0.513)

> S1–S3 reported point estimates with no confidence intervals. This adds them, and the
> result changes how strongly any of those experiments should be read.

---

## 0. The finding

**Not one of the seven variants tested in S1–S3 produces a Sharpe or average-R difference
whose 95 % interval excludes zero.** Every 95 % CI spans zero.

| stage | variant | ΔSharpe | 95 % CI | P(variant better) | verdict |
|---|---|---:|---|---:|---|
| S1 | 1.2 | −0.094 | [−1.006, +0.831] | 0.407 | noise |
| S1 | **1.8** | **+0.595** | **[−0.089, +1.503]** | **0.952** | **noise (marginal)** |
| S1 | 2.0 | +0.209 | [−0.657, +1.244] | 0.717 | noise |
| S2 | 2.0 | −0.207 | [−0.502, +0.035] | 0.047 | noise |
| S2 | 2.5 | −0.159 | [−0.459, +0.080] | 0.099 | noise |
| S3 | 0.5 | −0.198 | [−0.602, +0.315] | 0.293 | noise |
| S3 | 1.5 | −0.033 | [−0.258, +0.205] | 0.460 | noise |

Average R behaves identically: all seven CIs span zero.

**The 1.8 result is the marginal case.** Its lower bound is −0.089 — barely crossing zero —
and P(variant better) = **0.952**. That is the strongest single result in the entire
research programme, and it is still not distinguishable from noise at 95 %.

---

## 1. Method

**Block bootstrap, paired across variants, 5,000 draws, 395 daily observations.**

* **Paired.** All variants run over the same dates against the same market, so their daily
  returns are strongly correlated. Resampling each curve independently would destroy the
  pairing and inflate the intervals; the same block indices are applied to every variant.
* **Blocks of 10 trading days**, not i.i.d. days. Positions are held ~10 days on average,
  so daily returns are autocorrelated; an i.i.d. bootstrap would understate the variance.
  The block length is matched to the average holding period.
* **Same metric conventions** as `production/backtest.py::_summary`
  (Sharpe = mean/std × √252 on daily equity returns; drawdown on the cumulative path), so
  the interval is on the *reported* metrics, not a proxy.

Average R is bootstrapped separately over trades (5,000 resamples of the trade sets),
because its sampling unit is the trade, not the day.

---

## 2. What this does and does not mean

**It does mean:** at this sample size — 168 trades over 19 months — the experiment cannot
distinguish any S1–S3 variant from the frozen baseline. The measured differences are
consistent with ordinary sampling variation.

**It does not mean** the variants are equivalent in truth, and the S2/S3 direction is worth
noting: both show `P(variant better) ≈ 0.05–0.10` for Sharpe, i.e. the data mildly
*favours the control*. That is consistent with the mechanisms identified in the S1–S3
report (wider/earlier trailing converts long losers into short losers), but the interval
does not exclude zero, so the mechanism is the stronger evidence here, not the statistics.

**The honest reading of 1.8:** a 0.95 probability of beating the control on Sharpe is *not*
the same as a demonstrated improvement. Combined with S1-D's finding that the gain is a
trade-selection artefact rather than a stop-width effect, the case for changing
`stop_atr_mult` is now weaker on two independent grounds:

1. the effect does not exceed the noise floor, and
2. where it comes from, it is not the stop.

---

## 3. Effect on the research verdicts

| item | previous verdict | revised verdict | reason |
|---|---|---|---|
| **S1** 1.2 | REJECTED | **REJECTED** (unchanged, stronger) | worse and not significant |
| **S1** 1.8 | INCONCLUSIVE | **UNRESOLVED — no evidence of an effect** | CI spans zero; gain is selection, not stop width |
| **S1** 2.0 | INCONCLUSIVE | **UNRESOLVED** | CI spans zero; unique cohort is negative |
| **S2** 2.0 / 2.5 | REJECTED | **REJECTED** (unchanged) | mechanism identified; statistics consistent |
| **S3** 0.5 / 1.5 | REJECTED | **REJECTED** (unchanged) | mechanism identified; statistics consistent |
| **S1-P** decision | recommended "do nothing" | **recommended "do nothing", on stronger grounds** | no evidence the stop width matters at all |

The §12 neighbour test and the C1–C4 criteria remain valid; the bootstrap is an additional,
independent check that none of the candidates clears the noise floor.

---

## 4. Honest limitations

* **Power, not merit.** A wide CI reflects a small sample, not a bad strategy. 168 trades is
  simply not enough to resolve a Sharpe difference of this size. This should be read as
  "undetermined", not as "the variants are equivalent".
* **The bootstrap cannot correct for the selection channel.** S1-D established that a
  variant's trade *set* differs from the control's; these intervals resample the realised
  path and do not model that mechanism. They bound sampling variation in the equity path
  only.
* **Block length 10 is a judgement call.** Shorter blocks understate variance; longer blocks
  shrink the effective sample size. 10 was matched to the ~10-day average holding period.
* **One window, one market.** 2024-01→2025-07, one regime history, no out-of-sample
  replication.

---

## 5. Artifacts

* `research/bootstrap_uncertainty.py` — the paired block bootstrap
* `reports/bootstrap_uncertainty_pitcorrected_2026-10-02.json` — full intervals
