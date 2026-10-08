# Lever experiments on the PIT-corrected baseline — results

**Date:** 2026-10-01 · **Baseline:** corrected (PIT) — 396 sessions / 168 trades / **+5.57 %** /
MaxDD **−10.39 %** / Sharpe **0.513** / PF 1.13 / avg R 0.1878 / avg exposure 23.39 %
**Harness:** `research/run_lever_grid.py` over the unchanged `ProductionBacktest`; one in-memory override
per run (`production/config.py` never edited); PIT bucket cache reused (frozen universe).
**Runner:** `python research/run_lever_grid.py --param <name> --grid <v1,v2> --baseline <v0>`

**Pre-registered acceptance criteria (all three required):**

1. **Sharpe must improve** on 0.513.
2. **MaxDD must not worsen beyond −10.39 %.**
3. The improvement must hold in **both** sub-periods (2024 and 2025).

---

## 0. Why these two levers, and not the rest

The Step-0b reversal (`pit_correction_and_rebaseline_2026-10-01.md` §F) showed that **every** sizing/capacity
lever now admits a **positive**-expectancy cohort (+0.13 … +0.35 R), which removed the basis for the earlier
"the cap is protective, do not loosen it" conclusion. That made deployment the only lever with positive
supporting evidence, so it was tested properly — at the **portfolio** level, not by cohort simulation.

## 1. Lever 1 — `risk_per_trade`

| `risk_per_trade` | Return % | **MaxDD %** | **Sharpe** | Sortino | Calmar | avg R | PF | Trades | Avg expo % | Realised risk (mean/max) % |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **0.010 (baseline)** | 5.57 | **−10.39** | **0.513** | 0.557 | 0.338 | 0.1878 | 1.13 | 168 | 23.39 | 0.399 / 0.997 |
| 0.0125 | **11.43** | **−14.15** | **0.818** | 0.927 | 0.504 | 0.2027 | 1.23 | 175 | 27.35 | 0.485 / 1.168 |
| 0.0150 | **12.00** | **−13.39** | 0.774 | 0.889 | 0.559 | 0.1746 | 1.19 | 176 | 31.86 | 0.545 / 1.148 |

**Sub-periods**

| | 2024 return | 2024 Sharpe | 2024 avg R | 2025 return | 2025 Sharpe | 2025 avg R |
|---|---:|---:|---:|---:|---:|---:|
| 0.010 | 6.50 % | 1.091 | 0.2277 | −0.88 % | −0.124 | 0.1245 |
| 0.0125 | **12.32 %** | 1.532 | 0.2666 | −0.79 % | −0.078 | 0.1045 |
| 0.0150 | **15.14 %** | 1.609 | 0.2492 | **−2.72 %** | −0.367 | 0.0615 |

**Verdict — FAILS 2 of 3 criteria.**

* **C1 Sharpe: PASS** (0.818 / 0.774 > 0.513).
* **C2 MaxDD: FAIL** — −14.15 % / −13.39 % both breach the −10.39 % limit (2.9–3.8 pp worse).
* **C3 both sub-periods: FAIL** — the gain is **entirely a 2024 effect** (+6.50 → +12.32 / +15.14 %);
  **2025 does not improve** (−0.88 → −0.79 / −2.72 %) and at 1.5 % it is markedly worse.

Realised risk rises 0.399 → 0.485 → 0.545 % (still below the 1 % intent), realised **max** risk crosses 1 %
(1.168 %). Exit mix barely moves — the extra return comes from larger positions, not from better trades.

## 2. Lever 2 — `max_open_positions`

| `max_open_positions` | Return % | MaxDD % | Sharpe | Sortino | Calmar | avg R | PF | Trades | Avg positions |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **5 (baseline)** | **5.57** | **−10.39** | **0.513** | 0.557 | 0.338 | 0.1878 | **1.13** | 168 | 2.823 |
| 6 | **0.63** | **−12.93** | **0.090** | 0.101 | 0.031 | 0.0649 | **1.01** | 197 | 3.356 |

**Sub-periods**

| | 2024 return | 2024 Sharpe | 2024 avg R | 2025 return | 2025 Sharpe | 2025 avg R |
|---|---:|---:|---:|---:|---:|---:|
| 5 | 6.50 % | 1.091 | 0.2277 | −0.88 % | −0.124 | 0.1245 |
| 6 | 5.50 % | 0.833 | 0.1460 | **−4.62 %** | **−0.757** | **−0.0588** |

Exit mix: STOP_LOSS **84 → 105** (+21), TAKE_PROFIT 67 → 69, TRAILING 8 → 14.

**Verdict — FAILS all 3 criteria.** Return collapses 5.57 → **0.63 %**, Sharpe 0.513 → **0.09**,
PF 1.13 → **1.01** (edge gone), MaxDD worsens to −12.93 %, and **both** sub-periods degrade.
The 6th slot is filled mostly with **stop-outs** — the capacity-freeing lever is destructive.

## 3. Combined reading

| Lever | ΔReturn | ΔSharpe | ΔMaxDD | 2024 | 2025 | Verdict |
|---|---:|---:|---:|---|---|---|
| `risk_per_trade` 1 % → 1.25 % | +5.86 pp | +0.305 | **−3.76 pp** | better | ≈flat | **FAIL** |
| `risk_per_trade` 1 % → 1.5 % | +6.43 pp | +0.261 | **−3.00 pp** | better | worse | **FAIL** |
| `max_open_positions` 5 → 6 | −4.94 pp | −0.423 | **−2.54 pp** | worse | worse | **FAIL** |

**Headline: the deployment thread is closed for this window — and it closes in the direction of the
constraints being *protective after all*, but now on PIT-clean portfolio-level evidence rather than a leaky
cohort proxy.**

The important methodological point: **a positive marginal-cohort simulation did not survive contact with a
portfolio backtest.** Step-0b's "+0.349 R for the newly admitted setups" is a *standalone trade* estimate;
once those trades are actually taken alongside the existing book, they displace capital, occupy slots,
extend the holding pattern and (for the capacity lever) mostly hit stops. That is exactly why the earlier
reports insisted a cohort estimate is not a portfolio result — this experiment is the proof.

## 4. What this means for the bottleneck question

Re-answering "what limits return?" with these results folded in:

* The **constraints are not the limiter**: every attempt to relax them (more risk per trade — C1 passes but
  C2/C3 fail; more slots — fails outright) buys return **only in the period that was already good** and
  costs drawdown that exceeds the pre-registered limit.
* The **exit-target half is closed** (R1 inconclusive, no revision justified).
* What remains is the **edge itself** — but only the parts not yet tested: the **stop/trailing** half of the
  exit path (S1–S3), and the **entry cohorts** (R2) which are **blocked on logging** (R7) because
  `extension_from_pivot_pct` is null for all 168 trades and `components` is `{}`.

## 5. Limitations

* Single bull-dominated window; the corrected baseline's return is **a 2024 effect** (2024 +6.50 %,
  2025 −0.88 %), so C3 is the discriminating criterion and it is the one that fails.
* Two candidate values per lever — a controlled test, not a sweep. No value outside the stated grids was run.
* No statistical significance test; variants share entries, so samples are overlapping and non-independent.
* The "L2 adds mostly stop-outs" reading is an end-to-end attribution; adding a slot changes the whole
  position path, so per-trade attribution is approximate.
* All figures are on the **corrected (PIT)** regime. Nothing here changes production: `exit_engine_mode`
  stays `legacy`, all parameters stay frozen, `production/config.py` is unmodified.

## 6. Recommendation

**Do not change `risk_per_trade` or `max_open_positions`.** Both levers were tested under pre-registered
criteria and both failed; the failure mode (return bought with drawdown, concentrated in 2024) is consistent
across the two independent levers, which makes it more credible than a single negative result.

Next, in order: **(1)** the logging change (R7 + the blocked-entry snapshot, proposal:
`capacity_observability_proposal_2026-10-01.md`) so entry/blocked cohorts become measurable; **(2)** the
**stop/trailing** half of the exit path (S1–S3) on corrected data — the only exit dimension never tested;
**(3)** only then revisit entry cohorts (R2).
