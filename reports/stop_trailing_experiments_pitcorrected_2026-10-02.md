# S1–S3 — Stop / Trailing Experiments (PIT-corrected)

**Date:** 2026-10-02 · **Baseline:** the PIT-corrected one (commit `da9f5dc` + the PIT
fix, 2024-01-02 → 2025-07-31, 396 sessions) · **Pre-registered grids**, one variable per stage

> The purpose of S1–S3 is not to make the return larger. It is to determine whether the
> current stop/trailing architecture destroys an otherwise real edge. §13 applies: Stop
> Engine *parity* was already proven; that is a different question from stop *effectiveness*.

---

## A. Baseline — the corrected control

Re-run inside **every** stage as the historical control (§14). All three reproduced it exactly:

| Metric | Value |
|---|---:|
| Return | **+5.57 %** |
| CAGR | 3.51 % |
| Sharpe | **0.513** |
| Sortino | 0.557 |
| MaxDD | **−10.39 %** |
| Calmar | 0.338 |
| PF | **1.13** |
| avg R | **0.1878** |
| median R | −0.562 |
| Win rate | 47.6 % |
| Trades | **168** |
| Avg exposure | **23.39 %** |
| Avg holding (calendar d) | 9.78 |

Exit mix: `STOP_LOSS` 84 · `TAKE_PROFIT` 67 · `TIME_STOP` 9 · `TRAILING_STOP` 8.

---

## B. S1 — initial stop width (`stop_atr_mult`)

Pre-registered grid: baseline 1.5, candidates 1.2 / 1.8 / 2.0. TP, trailing, risk, entry,
portfolio, execution, costs, universe, data window all fixed.

| value | return % | MaxDD % | Sharpe | Sortino | Calmar | PF | avg R | median R | trades | expo % | hold d | stop-outs | overlap |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.2 | 5.38 | −14.78 | 0.419 | 0.468 | 0.229 | 1.10 | 0.138 | −1.000 | 201 | 26.60 | 7.65 | 111 | 0.34 |
| **1.5** | **5.57** | **−10.39** | **0.513** | **0.557** | **0.338** | **1.13** | **0.188** | **−0.562** | **168** | **23.39** | **9.78** | **84** | **1.00** |
| 1.8 | **11.09** | **−8.73** | **1.107** | **1.287** | **0.793** | **1.37** | **0.245** | **+0.462** | 145 | 20.37 | 12.02 | 61 | 0.39 |
| 2.0 | 6.53 | −7.64 | 0.722 | 0.791 | 0.538 | 1.26 | 0.155 | +0.295 | 128 | 20.08 | 13.85 | 54 | 0.30 |

### The central diagnostic (§6): are stop-outs terminating trades with subsequent potential?

Measured on the **next 20 sessions after the exit**, in R from the actual entry. This is a
description of recorded prices, not a simulated alternative trade.

| stop mult | stop-outs | avg MFE before stop | post-stop reached +1R | reached +2R | post-stop avg MFE |
|---:|---:|---:|---:|---:|---:|
| 1.2 | 111 | 0.560 | 52.3 % | 39.6 % | 1.938 |
| **1.5** | **84** | **0.483** | **46.4 %** | **25.0 %** | **1.035** |
| 1.8 | 61 | 0.461 | 39.3 % | 14.8 % | 0.790 |
| 2.0 | 54 | 0.465 | 27.8 % | 14.8 % | 0.569 |

**Reading.** On the frozen 1.5 stop, 46.4 % of stop-outs were followed by a +1R move
within 20 sessions. That is real foregone opportunity — but the same table shows it is
**not** what limits the strategy, because widening the stop shrinks the stop-out population
faster than it rescues those trades: at 1.8 the post-stop +1R cohort falls to 39.3 %, at
2.0 to 27.8 %. The stop is not the binding constraint on the winners.

### §12 robustness test — the shape of the response

```
1.2 = poor     (Sharpe 0.419)
1.5 = baseline (Sharpe 0.513)
1.8 = excellent(Sharpe 1.107)   <-- isolated peak
2.0 = good     (Sharpe 0.722)   <-- materially worse than its neighbour
```

This is the **anti-pattern the spec explicitly warns against** (§12): a lone spike whose
neighbour does not support it. By contrast S1's *decline* below 1.5 is smooth and
well-supported (1.2 is worse on Sharpe, MaxDD, PF and win rate simultaneously).

### §11 criteria

| | 1.2 | 1.8 | 2.0 |
|---|---|---|---|
| **C1** Sharpe > 0.513 | ✗ 0.419 | ✓ 1.107 | ✓ 0.722 |
| **C2** MaxDD not worse than −10.39 % | ✗ −14.78 | ✓ −8.73 | ✓ −7.64 |
| **C3** not one subperiod | ✗ see below | ✓ see below | ✗ see below |
| **C4** plausible mechanism | — | ✓ see §E | partial |
| **§12** neighbour support | — | **✗ isolated peak** | ✗ |

Sub-period returns (control → variant, pp):

| variant | 2024 | 2025 Jan–Mar | 2025 Apr–Jul |
|---|---:|---:|---:|
| 1.2 | 6.50 → 11.28 (**+4.78**) | −0.06 → −5.00 (**−4.94**) | −0.76 → −0.27 (+0.49) |
| 1.8 | 6.50 → 10.04 (**+3.54**) | −0.06 → **+1.84** (+1.90) | −0.76 → −0.81 (−0.05) |
| 2.0 | 6.50 → 5.69 (−0.81) | −0.06 → **+3.11** (+3.17) | −0.76 → −2.39 (−1.63) |

1.8 is the only variant that improves **two of three** sub-periods and is neutral in the
third — it passes C3. But 2.0 reverses (2024 negative, late 2025 negative), which is the
signature of an unstable optimum rather than a plateau.

---

## C. S2 — trailing stop width (`trailing_atr_mult`)

Pre-registered: baseline 1.5, candidates 2.0 / 2.5. Initial stop, TP, trigger, risk, entry,
portfolio, regime, execution all fixed. **Not combined with the TP experiment** (§15).

| value | return % | MaxDD % | Sharpe | Sortino | Calmar | PF | avg R | median R | trades | expo % | hold d | trail exits | overlap |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **1.5** | **5.57** | **−10.39** | **0.513** | **0.557** | **0.338** | **1.13** | **0.188** | **−0.562** | **168** | **23.39** | **9.78** | **8** | **1.00** |
| 2.0 | 3.12 | −10.72 | 0.306 | 0.331 | 0.185 | 1.07 | 0.130 | −1.000 | 162 | 23.76 | 10.15 | 4 | 0.90 |
| 2.5 | 3.66 | −10.40 | 0.354 | 0.390 | 0.223 | 1.08 | 0.133 | −1.000 | 160 | 23.82 | 10.33 | 2 | 0.89 |

**Every criterion fails for both candidates.** Sharpe falls, PF falls, median R falls to
−1.0, and the return loss is present in all three sub-periods for 2.0
(2024 −0.60, early 2025 −0.29, late 2025 −1.46 pp).

### §8 mechanism — premature truncation, or longer losing trades?

| variant | trail exits | trail avg R | trail avg hold | trail MFE | MFE capture (total) |
|---:|---:|---:|---:|---:|---:|
| 1.5 | 8 | **+0.123** | 12.8 d | 1.361 | **0.148** |
| 2.0 | 4 | **−0.200** | 16.0 d | 1.396 | 0.104 |
| 2.5 | 2 | **−0.357** | 20.5 d | 1.574 | 0.105 |

**This is the second failure mode the spec asked us to distinguish, and it is the one
that occurred.** The wider trailing stop did give winners more room (MFE rose 1.36 → 1.57),
but the trades it kept were converted into losses: average trailing R fell from **+0.123
to −0.357** while holding time rose 12.8 → 20.5 days. The frozen 1.5 trailing is not
truncating profitable continuation; it is **preventing long losers from becoming short
losers**.

---

## D. S3 — trailing activation (`trailing_trigger_r`)

Pre-registered: baseline 1.0, candidates 0.5 / 1.5. Everything else fixed.

| value | return % | MaxDD % | Sharpe | Sortino | Calmar | PF | avg R | median R | win % | trades | hold d | trail exits | overlap |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 3.13 | −9.51 | 0.315 | 0.336 | 0.208 | 1.07 | 0.116 | −0.298 | 41.1 | 185 | 8.42 | **35** | 0.67 |
| **1.0** | **5.57** | **−10.39** | **0.513** | **0.557** | **0.338** | **1.13** | **0.188** | **−0.562** | **47.6** | **168** | **9.78** | **8** | **1.00** |
| 1.5 | 5.18 | −10.31 | 0.480 | 0.533 | 0.316 | 1.13 | 0.168 | −1.000 | 45.7 | 162 | 10.27 | 6 | 0.86 |

**Both directions fail C1.** The response is single-peaked *at the control*: 0.5 is much
worse, 1.5 is slightly worse. That is the strongest possible form of the §12 argument —
the frozen value is not merely convenient, it is a local optimum with support on both sides.

### §10 mechanism — does early activation truncate the winner distribution?

| variant | trail exits | trail avg R | realised R median | % positive R | R std |
|---:|---:|---:|---:|---:|---:|
| 0.5 | **35** | **−0.172** | −0.298 | 41.6 % | 1.208 |
| **1.0** | **8** | **+0.123** | −0.562 | **48.2 %** | 1.279 |
| 1.5 | 6 | +0.128 | −1.000 | 45.7 % | 1.304 |

Arming the trailing stop at 0.5R **quadrupled** the trailing-exit population (8 → 35) and
turned that exit class from **+0.123R to −0.172R**. Positive-R share fell 48.2 % → 41.6 %.
This is precisely the failure the spec named: *activating the trailing stop too early
truncates the natural winner distribution*. The frozen 1.0R activation is protecting it.

---

## E. Mechanistic interpretation — what actually changed in the trade lifecycle

### E1. The finding that reframes all three stages

`stop_atr_mult` is **not** a single-variable lever in this architecture. Verified directly
against the code and the records:

1. **Position size.** `stop_distance = atr × stop_atr_mult` (`src/agents/risk_manager.py:41`)
   is the divisor in `shares = risk_budget ÷ stop_distance`. A wider stop mechanically buys
   fewer shares → exposure fell 23.39 % → 20.37 % at 1.8, and trades fell 168 → 145.
2. **The R unit itself.** `r_multiple = (exit − entry) / (entry − stop)`. Because the TP
   price is fixed at `entry + 2.5 × ATR`, the R-value of every take-profit is
   `2.5 / stop_atr_mult`. Measured: 2.083 / 1.667 / 1.389 / 1.250 against a theory of
   2.0833 / 1.6667 / 1.3889 / 1.2500 — an exact match. **The avg-R column is therefore not
   comparable across S1 rows**; it partly re-measures the denominator.
3. **The trailing trigger.** `r_dist = atr_at_entry × stop_atr_mult`
   (`src/portfolio/portfolio_manager.py:53`), so with `trailing_trigger_r = 1.0` the
   trailing stop arms at `entry + 1.0 × stop_atr_mult × ATR` — it moves *with* the initial
   stop. S1 therefore also perturbs S2/S3's own parameter.

**Consequence:** S1 cannot be read as "what happens if the stop is wider". It is "what
happens if the R unit, the position size, and the trailing arming threshold all widen
together". Any apparent improvement is a composite effect that this design cannot separate.
This is a design limitation of the experiment, not of the run — and it is the main reason
S1's isolated 1.8 peak cannot be attributed to stop width alone.

### E2. What the trailing experiments genuinely show

S2 and S3 do **not** have this problem: `trailing_atr_mult` and `trailing_trigger_r` do not
feed back into position size, the R unit, or the initial stop. Their trade overlap with the
control is 0.86–0.90, i.e. they are near-clean single-variable tests, and both fail
decisively. The mechanism in both cases is the same and is visible at trade level:

> The trailing stop's job in this system is **not** to extend winners. Winners already reach
> the 2.5×ATR take-profit (67 of 168 trades, 98.5 % of them followed by +1R). The trailing
> stop's measurable job is to **cap the tail of long losers** — and both S2 (wider) and S3
> (earlier) demonstrably degrade that job.

### E3. Where S1's 1.8 does have a real mechanism

Not the stop width. The 1.8 row's improvement is visible as a **distribution** change:
positive-R share 48.2 % → 55.2 %, median R −0.562 → **+0.462**, R std 1.279 → 1.151,
top-5-trade concentration 26.4 % → 19.6 %, MaxDD −10.39 % → −8.73 %. The stop-out
population fell 84 → 61 while take-profits held at 67. So the effect is "fewer
premature stop-outs, same number of winners, less downside tail" — a genuine and
plausible mechanism (C4 ✓) that is *consistent with* the hypothesis in §5. It is
undermined only by the §12 neighbour failure (2.0 gives back more than half the Sharpe
gain) and by the E1 coupling that prevents attribution.

---

## F. Robustness

| check | S1 | S2 | S3 |
|---|---|---|---|
| Smooth response | ✗ isolated peak at 1.8 | ✓ monotone decline | ✓ single peak at the control |
| Neighbour support | ✗ (2.0 far below 1.8) | ✓ (both candidates worse) | ✓ (both directions worse) |
| Stable sub-periods | partial (1.8: 2 of 3 improve) | ✗ (all 3 worse) | ✗ (0.5: all 3 worse) |
| Stable trade count | ✗ 201 / 168 / 145 / 128 | ✓ 160–168 | ~ 162–185 |
| Stable exposure | ✗ 20.1–26.6 % | ✓ 23.4–23.8 % | ~ 22.2–23.8 % |
| Reasonable drawdown | ✓ (except 1.2) | ✓ | ✓ |
| Trade overlap w/ control | 0.30–0.39 (low) | 0.89–0.90 (high) | 0.67–0.86 |

The low S1 overlap is itself a warning: at 1.8 only 87 of 145 trades are shared with the
control (Jaccard 0.385). A variant that shares barely half its trades with the baseline is
not a small perturbation of it, and its headline return is not evidence about a
*parameter* so much as about a *different portfolio path*.

---

## G. Limitations

1. **Sample.** 168 control trades over 19 months, ~30/month. S2's trailing class has only
   **8 trades** at the control; conclusions about trailing rest on single-digit counts and
   are correspondingly fragile.
2. **The S1 coupling (E1)** is the dominant limitation: position size, the R unit, and the
   trailing arming threshold all move together. Separating them requires a different
   experiment (e.g. fixing the R unit while varying only the fill size), which was **not**
   pre-registered and was not run.
3. **Overlapping variants.** S1 and S2/S3 interact; running S1's 1.8 together with any
   trailing change would be a combined search, which §15 forbids and which was not done.
4. **Shared trades.** S1 variants share 30–39 % of trades with the control, so variant
   differences are not independent observations of the same strategy.
5. **Window.** 2024–2025H1 only. The corrected baseline is already known to be a 2024
   effect (2024 +6.50 %, 2025 −0.88 %), which caps how much any single-window result can
   support.
6. **Post-stop counterfactual** looks 20 sessions ahead and applies no costs, fills or exit
   rules; it measures opportunity, not achievable profit.
7. **No TP × stop search was run** (§15) and none is recommended until S1's coupling is
   resolved.

---

## H. Research status

| Item | Verdict | Basis |
|---|---|---|
| **S1** `stop_atr_mult` 1.2 | **REJECTED** | Fails C1, C2, C3; worse on Sharpe, MaxDD, PF and win rate simultaneously |
| **S1** `stop_atr_mult` 1.8 | **INCONCLUSIVE** | Passes C1–C4 with a real mechanism, but fails §12 neighbour support (2.0 = 0.722) and cannot be attributed to stop width because of the E1 coupling. **Not a basis for changing production.** |
| **S1** `stop_atr_mult` 2.0 | **INCONCLUSIVE** | Improves C1/C2 but reverses across sub-periods; unsupported neighbour |
| **S2** `trailing_atr_mult` 2.0 | **REJECTED** | Fails C1/C2/C3; mechanism identified (long losers converted to −0.200R) |
| **S2** `trailing_atr_mult` 2.5 | **REJECTED** | Same, worse (trailing avg R −0.357) |
| **S3** `trailing_trigger_r` 0.5 | **REJECTED** | Fails C1/C3; quadruples trailing exits (8→35) and turns them negative |
| **S3** `trailing_trigger_r` 1.5 | **REJECTED** | Fails C1; slightly worse on every risk-adjusted metric |

**Overall answer to the question S1–S3 was designed to ask:**

> The trailing architecture is **not** destroying an edge — S2 and S3 are clean
> single-variable tests and both directions are worse than the frozen values, with the
> mechanism visible at trade level. The **initial** stop is the open question: 1.8 shows a
> real, mechanistically-explained improvement, but its neighbour does not support it and
> the parameter is entangled with position size, the R unit, and the trailing trigger, so
> the evidence does not yet support changing it.

**"optimal" is not claimed for any value.**

---

## I. Recommended next research direction

Based only on corrected, controlled evidence:

1. **Resolve the S1 coupling before revisiting `stop_atr_mult`.** The decisive question is
   whether 1.8's gain is *stop width* or simply *smaller positions*. A design that fixes the
   R unit and the trailing arming threshold while varying only the initial stop would answer
   it — but that requires an architecture change (decoupling `r_dist` from `stop_atr_mult`
   in `_exit_check`), which is a **frozen-contract amendment** and needs human approval
   first. It is a proposal, not a plan.
2. **Prefer 1.5/1.8 over 2.0 as the region of interest** for any future work, purely
   because 2.0 already gives back over half the gain.
3. **Do not extend the trailing grids.** Both were falsified with a clean mechanism; more
   values would be searching, not testing.
4. **R2 entry cohorts are now unblocked** by R7's `score_components`. The `reversal`
   component fires in only 7.1 % of trades while the three trend-structure components fire
   in >94 % — the pullback score behaves as a weighted sum, not the conjunction its freeze
   document describes. That is the natural next question, and it uses only fields R7
   already provides.
5. **P6 remains open** — `extension_from_pivot_pct` is still null and the frozen extension
   filter is still inert.

---

## J. Safety

```
git status / diff --stat   : see §20 of the delivery message
production parameters      : changed? NO
exit_engine_mode           : legacy
frozen contracts           : untouched (freeze audit 0 MISMATCH)
commits / pushes           : none
```

Raw artifacts, one per experiment:

* `reports/stop_trailing_s1_pitcorrected_2026-10-02.json`
* `reports/stop_trailing_s2_pitcorrected_2026-10-02.json`
* `reports/stop_trailing_s3_pitcorrected_2026-10-02.json`
