# Phase 5 — Step 0: Marginal-Cohort Value Measurement

**Date:** 2026-10-01 · **Status:** EXECUTED (read-only). **This changes the Phase-4 recommendation.**
Runner: `production/tests/phase5_step0_marginal_cohort.py` · Data:
`reports/phase5_step0_marginal_cohort_2026-10-01.json`

---

## 0. What was asked and what was done

Phase-4 left one question blocking a target choice: *the 505 valid setups the risk gate rejected for
"risk budget insufficient for one share" — had the budget allowed them, would they have made money?*

**Method (read-only; no parameter, config or production path changed; no alternative strategy
implemented):** each rejected setup is traded hypothetically with **next-open entry** and the
**frozen, parity-verified Exit Engine** (`production/exits`, reached through the same contracts the
Phase-3 adapter produces) — i.e. the exit decision is made by the same code production would run,
including trailing, the stop-before-target priority and the calendar-day time stop.

**Internal control first.** The *same machinery* was re-run on the 151 **accepted** trades and compared
with their realised records:

| control | result |
|---|---|
| exit reason reproduced | **148 / 151 (98.0 %)** |
| realised R reproduced within 0.05 | **148 / 151 (98.0 %)** |
| **verdict** | **machinery validated** — the marginal-cohort numbers are credible |

---

## 1. Result

| cohort | n | avg R | median R | sum R | win % | ≥ +1R | ≤ −1R |
|---|---|---|---|---|---|---|---|
| **accepted** (control / benchmark) | 151 | **+0.182** | +0.021 | +27.5 | 50.3 | 41.1 % | 49.7 % |
| **marginal** (budget-rejected, all simulated) | 458 | **−0.270** | **−1.0** | **−123.6** | 34.3 | 25.3 % | **64.6 %** |
| marginal, de-duplicated (first per ticker-month) | 169 | **−0.091** | −1.0 | −15.4 | 40.2 | 28.4 % | 58.0 % |

506 rejected setups → 458 simulated (45 were skipped by the frozen gap filter — they would have
gapped >2 % — and 3 never exited within the horizon).

Exit mix of the marginal cohort: `STOP_LOSS 296`, `TAKE_PROFIT 115`, `TRAILING_STOP 37`, `TIME_STOP 10`
— i.e. **65 % of them would have been stopped out.**

By regime and price band (the floor rejects *systematically*, not randomly):

| split | n | avg R | win % |
|---|---|---|---|
| BULL at signal | 91 | **−0.614** | 19.8 |
| SIDEWAYS at signal | 367 | −0.184 | 37.9 |
| price $50–150 | 158 | −0.166 | 37.3 |
| **price $150–400** | **241** | **−0.363** | 31.5 |
| price > $400 | 59 | −0.166 | 37.3 |

**Pre-registered decision rule** (written before the run): *marginal expectancy ≤ 0 → STOP; the risk
budget cap is protective.*

> **VERDICT: STOP.** accepted avg R **+0.182** vs marginal avg R **−0.270**.
> The rejected setups were not good opportunities the account was too small to take — they were bad
> trades. **The integer-share floor has been acting as an accidental quality filter.**

---

## 2. What this changes

### 2.1 Candidate #1 loses its "admit more setups" component

The Phase-4 report ranked *capital deployment mechanics* first because the arithmetic (V3) showed a
large lever: 164 → 485 sizeable setups. Step 0 now measures the **value** of the setups that lever
would admit, and it is negative. **Loosening the budget would degrade the mix, not improve it.**

The rejection is also *systematically* biased: the floor binds when `risk_amount < stop_distance`,
i.e. on high-ATR / high-priced names — and that is exactly the band that performed worst
($150–400: −0.363 R over 241 rejections). So the floor is not a random loss of opportunity.

### 2.2 The remaining deployment question is a **leverage preference**, not an alpha lever

What still holds from Phase 4 is that the *accepted* trades ran at **0.33 % realised risk against a
1 % configured intent**, and average exposure was 17 %. But that is a **near-constant scaling** of
positions, and under constant scaling the risk-adjusted profile does not improve: return, volatility
and drawdown percentage all scale together, so **Sharpe is essentially unchanged** while MaxDD grows in
absolute terms (−5.98 % at 17 % exposure implies roughly −35 % at full exposure).

Therefore: *under-deployment explains the benchmark gap (Phase 4 §D6) but it is not a source of
risk-adjusted improvement. Closing that gap means taking proportionally more risk, not trading
better.* That is a preference decision for the human, and it should be recorded as such rather than
researched as an alpha target.

### 2.3 Revised candidate ranking

| rank | candidate | why | status |
|---|---|---|---|
| **1** | **Exit target efficiency** | the only measured, *controllable* inefficiency left: targets fill at 1.667 R while those trades averaged **2.25 R MFE** (0.585 R given back per winner). Only **3 of 75** stop-outs had ever been ≥ +1 R, so the stop distance is not the problem | **new primary candidate** (candidate #3 in Phase 4) |
| 2 | **Entry gap quality** | potentially −22.5 USD in the allowed ≥0.5 % cohort, but §V4 showed the effect is a 2024-only artefact with 8–20 trades per 2025 cell | blocked pending a stability result |
| 3 | **Make #4 observable** (record-quality fix) | the 99 budget-blocked sessions were never setup-evaluated, so a whole class of opportunity is invisible; persisting the fields is strategy-neutral | cheap prerequisite |
| 4 | ~~Capital deployment~~ | reclassified as a **leverage/preference decision** (§2.2), not a research target | reclassified |
| 5 | Regime calibration | BULL entries were the worst cohort (−0.321 R accepted, −0.614 R marginal) → the divergence cap looks protective | not proposed |

---

## 3. Proposed next experiment (needs approval; not run)

**Exit target efficiency**, single variable, pre-registered:

> *Hypothesis: the fixed take-profit at `take_profit_atr_mult = 2.5` (1.667 R) caps winners
> systematically below their favourable excursion, so raising the target or letting the frozen
> trailing stop own the exit improves expectancy without degrading drawdown.*

* **Exact single change:** `take_profit_atr_mult: 2.5 → 3.5` (the exit geometry; **Stop/Exit v1 is
  frozen, so this needs an explicit architecture decision first**). A trailing-only variant
  (`take_profit_atr_mult → ∞`, pure trailing) is the natural second arm but must be a *separate*
  experiment.
* **Success criterion (risk-adjusted):** expectancy and PF improve while Sharpe does not fall and
  MaxDD does not worsen beyond the current −5.98 %.
* **Falsification:** if the give-back is not recoverable — i.e. expectancy does not improve — the
  "target too tight" hypothesis is dead and the exit face should be left frozen.
* **Cost:** one production-equivalent backtest run per arm + out-of-window (2018–2025) confirmation.

**Why not the deployment lever:** Step 0 measured it and it is negative; and its residual component is
a leverage choice, not alpha.

---

## 4. Caveats (must accompany the Step-0 result)

1. The hypothetical trades use the **frozen** exit geometry; a different exit rule would change both
   cohorts, so this measures the marginal cohort *under the current system*.
2. 45 of 506 rejected setups were dropped by the gap filter (>2 % gap) — they may have been better or
   worse; they are **not** in the −0.270 R figure.
3. The marginal cohort is **not a random sample** — it is the high-ATR/high-price band by
   construction (that is what makes the floor bind). The result is therefore "the floor rejects a
   systematically weaker band", not "all rejected setups are bad".
4. Repeats: the same ticker can be rejected on consecutive sessions while it stays in the bucket.
   The de-duplicated view (n=169, −0.091 R) is still negative, so the conclusion does not depend on
   the repeat treatment, but the two numbers must be quoted together.
5. This is still **one window** (2024-01→2025-07, a bull market). The pre-registered
   out-of-window requirement applies to any follow-up experiment.
6. No production path, parameter, config or freeze contract was modified to produce this result.

---

## 5. NEEDS HUMAN REVIEW

1. **Accept the Step-0 verdict** (`STOP` — the budget cap is protective) and the resulting
   re-ranking: **Exit target efficiency becomes the primary candidate**.
2. **Decide the deployment question as a preference, not a research item**: does the project want
   ~17 % average exposure (MaxDD −5.98 %) or a higher deployment (proportionally more return *and*
   drawdown)? Record the answer; do not optimise it. **See the addendum below for the numbers you
   need to make that call — including SPY's own drawdown and Sharpe in the same window.**
3. **Approve or reject the Exit experiment** (`take_profit_atr_mult 2.5 → 3.5`), which requires an
   architecture decision because Stop/Exit v1 is frozen.
4. Confirm the **record-quality fix** (persist `holding_days`, `components`, risk `reasoning`,
   entries-phase equity/cash, `veto_flags`) as a small strategy-neutral task — needed to measure
   candidate #4 at all.

---

## 6. Addendum — lever-specific cohort check + leverage decision table

Runner: `production/tests/phase5_step0b_lever_cohorts.py` · Data:
`reports/phase5_step0b_lever_cohorts_2026-10-01.json`. Read-only; reuses the Step-0 simulations.

### 6.1 Verification: §1 traded *all* rejected setups — does a *lever* admit a different subset?

Each lever was re-scored over the 458 simulated rejections; "newly admitted" = sizeable under the
lever but not as recorded:

| lever | newly admitted | their avg R | win % | mean size | mean risk |
|---|---|---|---|---|---|
| (b) `risk_per_trade` 1.0 % → 1.5 % | 120 | **−0.138** | 39.2 | 1.0 sh | $6.52 |
| (b2) `risk_per_trade` 1.0 % → 2.0 % | 220 | **−0.171** | 39.6 | 1.0 sh | $7.49 |
| (c) divergence cap removed (multiplier → 1.0) | 220 | **−0.171** | 39.6 | 1.0 sh | $7.49 |
| (c2) SIDEWAYS base 0.5 → 0.75 | 97 | **−0.070** | 42.3 | 1.0 sh | $6.55 |
| (e) capital base doubled | 221 | **−0.169** | 39.8 | 1.0 sh | $7.51 |

**The verdict stands and is strengthened: every lever's newly-admitted cohort is negative
expectancy**, so the "marginal cohort is bad" finding was not an artefact of averaging over setups no
lever would actually take.

Two useful consistency signals: (c) and (b2) produce **identical** cohorts (+220, same avg R) —
removing the divergence cap doubles the multiplier, which is arithmetically the same as doubling
`risk_per_trade`; and every newly admitted trade is **1 share at ~$6.5–7.5 risk**, i.e. the minimum
viable size.

**Dollar-weighted reading (rough estimate, assumptions stated).** Each newly admitted trade risks
≈ $7 at ≈ −0.14 R → ≈ **−$0.9 expected** per trade; admitting 120 of them ≈ **−$108**, while scaling
the accepted cohort's size by 1.5× adds only ≈ **+$51** (0.5 × the realised +$102 net P&L). Net ≈
**−$56** for lever (b). The mildest lever (c2) is closer to a **wash** (≈ −$44 admitted vs ≈ +$43
scaled). This is arithmetic on existing records, not a backtest; the integer-share floor makes the
size-up *sub*-linear, so the estimate is optimistic **toward** the lever.

### 6.2 The leverage decision — with a comparison that matters

| | return | MaxDD | Sharpe | avg exposure |
|---|---|---|---|---|
| Frozen system (realised) | +7.99 % | **−5.98 %** | **0.935** | 17.16 % |
| **SPY, same window** | **+36.26 %** | **−18.76 %** | **1.218** | 100 % |
| System scaled to match SPY's return (k ≈ 4.54×) | ≈ +36 % | ≈ **−27.1 %** | ≈ 0.935 | ≈ 78 % |

* **Matching SPY's return by leverage would require ≈ 4.5× exposure and imply ≈ −27 % drawdown —
  worse than simply holding SPY (−18.76 %).** Leverage is therefore not a route to the benchmark.
* **In this window SPY also had the higher Sharpe (1.218 vs 0.935).** So the system's demonstrated
  value proposition is **drawdown control**, not risk-adjusted outperformance. That is an honest and
  strategically important statement for the project, and it is one more reason the "deploy more"
  lever is a *preference* (how much drawdown do you accept for how much return), not research.
* Linear scaling is an approximation (compounding, cash drag and the share floor all move with size).
  It is a decision aid for a preference, not a performance estimate.
* Caveat: one window, a bull market. The 2018–2025 graduation study in the project's history found
  the opposite pattern in bear periods (regime protection reduced MaxDD sharply), so this comparison
  must not be generalised from 2024–2025 alone.
