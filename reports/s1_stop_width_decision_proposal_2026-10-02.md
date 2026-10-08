# Decision Proposal — making the initial stop width a testable variable

**Date:** 2026-10-02 · **Status: PROPOSAL ONLY — NOTHING IMPLEMENTED**
**Requires: an explicit human architecture decision.**

This document follows §16 of the research spec: research evidence → human review → a
*separate* production-change proposal. No code in this proposal has been written. The
repository is exactly as it was before this document.

---

## 1. The decision being requested

> **Should the project amend the frozen Stop/Exit contract to decouple the stop distance
> from the position-sizing divisor, so that "initial stop width" becomes a variable that
> can be tested on its own?**

**Recommendation: NO — not now.** The evidence says the current stop width is not the
problem, and the amendment would touch a frozen contract to chase an effect that the
decomposition attributes to something else. The reasoning is in §5; the options are in §3.

---

## 2. The evidence that prompts the question

`stop_atr_mult` cannot be varied as a single variable in this architecture. S1-D measured
four couplings (`reports/s1_decomposition_2026-10-02.md`):

| # | coupling | measured impact | where it lives |
|---|---|---|---|
| 1 | **Admission filter** — a wider stop raises the risk budget needed to buy 1 share, so more candidates are rejected before they ever become trades | **largest**; rejections 425 → 472 → 570, trades 168 → 145 → 128 | `src/agents/risk_manager.py:42-44` |
| 2 | **Trade selection** — which candidates survive that filter | **carries the entire 1.8 effect**; on shared trades 1.8 is −0.009 R, its 58 unique trades are +0.229 R | emergent |
| 3 | **The R unit** — `r_multiple` denominator and the trailing trigger both derive from the same distance | penalises 1.8 by −0.031 R; it does **not** explain the gain | `state.py:81-87`, `portfolio_manager.py:53` |
| 4 | **Trailing arm** — `r_dist = atr × stop_atr_mult` | immaterial (+0.01 R on 3 % of trades) | `STOP_EXIT_V1_FREEZE.md:54-55` |

Coupling 1 is the one that was not obvious before R7. Because 43 % of the baseline book
sits within 1.5 shares of the affordability boundary, the integer share floor turns a stop
parameter into a **minimum-volatility admission requirement**.

---

## 3. Options

### Option A — Do nothing. Keep the freeze as it is. *(recommended)*

**What changes:** nothing.

**Rationale.** S1's 1.8 result is a selection effect, not a stop-width effect, and its
neighbour fails the robustness test. There is no evidence that the 1.5 stop is leaving
money on the table. Every other lever investigated in this research window (R1 TP, L1
sizing, L2 capacity, S2/S3 trailing, R2 cohorts, R3 capacity magnitude) has been closed
without finding a production change worth making. The consistent reading across all of
them is that **the constraint set is not where the remaining return problem lives.**

**Cost:** the initial stop width stays permanently confounded, so it can never be cleanly
tested with the current architecture. That is a real limitation and it is the price.

**When this option should be revisited:** if a future line of evidence points at the stop
specifically — for example, a longer and independent sample, or a strategy variant where
the entry and the stop can be varied independently by construction.

---

### Option B — Decouple the sizing divisor only

**What would change.** Introduce a separate `sizing_atr_mult` (or equivalent) so that
`shares = risk_budget / (atr × sizing_atr_mult)` while the stop distance continues to use
`stop_atr_mult`. Setting both to 1.5 reproduces today's behaviour exactly.

**What it buys.** Coupling 1 — the admission filter — is removed. A subsequent S1 would
then vary stop width while every candidate remains affordable on the same terms, and the
trade set would stop moving for a mechanical reason.

**What it costs.**
- `ProductionConfig` gains a parameter that is a **second** answer to "how wide is a stop",
  which is a genuine design smell.
- It amends `production/STOP_EXIT_V1_FREEZE.md` (the sizing formula is inside the frozen
  risk surface) and `src/agents/risk_manager.py`.
- It does **not** fix coupling 3: the R unit and the trailing arm would still track the
  stop distance. Stop width would be *less* confounded, not clean.
- The amendment must preserve today's numbers exactly, which means a parity test proving
  `sizing_atr_mult = stop_atr_mult = 1.5` reproduces the current 168-trade baseline
  byte-for-byte. That test is the real cost, and it is mandatory.

**Evidence that would justify it:** a direct measurement that the 1.5 stop is costing
return. **There is none.** S1-D attributes the apparent gain elsewhere.

---

### Option C — Decouple the trailing trigger only

**What would change.** Give the trailing arm its own ATR distance, so `r_dist` no longer
derives from `stop_atr_mult`.

**What it buys.** Removes coupling 4 and lets the trailing arm be tested independently of
the initial stop.

**What it costs.** Amends an explicitly documented freeze clause
(`STOP_EXIT_V1_FREEZE.md:54-55`) to fix a coupling that S1-D measured as **immaterial**
(+0.01 R on 3 % of trades), while S2 and S3 have already independently confirmed the
frozen trailing values are better than both alternatives tested.

**Verdict: not justified by any current evidence.**

---

### Option D — Decouple everything (a new risk/stop contract)

**What would change.** Separate `risk_unit_atr` (defines 1R, drives sizing, the R multiple
and the trailing arm) from `initial_stop_atr` (defines where the protective stop sits).

**What it buys.** Stop width, position size, the R multiple and the trailing arm would each
become independently testable. This is the architecturally correct answer.

**What it costs.** This is a substantial redesign of the frozen Stop/Exit surface: the
contracts, the parity suite, the shadow wiring, the freeze document, and the whole
regression corpus. It would need its own staged plan, its own parity evidence, and a
re-baseline.

**Verdict: disproportionate.** It is the right shape eventually and clearly wrong as a
response to a single inconclusive experiment. Doing it now would be optimising the
toolchain rather than the strategy.

---

## 4. Comparison

| | A: do nothing *(recommended)* | B: decouple sizing | C: decouple trailing | D: decouple all |
|---|---|---|---|---|
| Amends a frozen contract | no | yes (risk surface) | yes (documented clause) | yes (whole surface) |
| Fixes the **largest** confound (admission filter) | no | **yes** | no | yes |
| Fixes the R-unit confound | no | no | no | yes |
| Justified by current evidence | n/a | **no** | **no** | **no** |
| Risk of changing live behaviour | none | moderate | moderate | high |
| Reversible if it does not help | n/a | yes | yes | poorly |

---

## 5. Reasoning for the recommendation

1. **The premise failed.** S1 set out to test "the initial stop may be unnecessarily tight".
   S1-D shows the 1.8 improvement is **not** attributable to stop width — on shared trades
   it is slightly negative (−0.009 R). There is no stop problem on the evidence.

2. **The neighbours disagree for a mechanical reason.** 2.0's unique trades average
   **−0.049 R**. A parameter whose neighbouring values admit such different populations is
   acting as a filter, not as a stop. Tuning a filter on one window, with one neighbour
   supporting and one contradicting, is how a backtest gets overfitted.

3. **The rest of the research points the same way.** R1, L1, L2, S2, S3, R2 and R3 are all
   closed with no production change justified. R3 in particular found the foregone cohort
   is worth roughly a quarter of the trades actually taken. The system is not obviously
   leaving value on the table at any of the levers examined.

4. **Option B would let the next S1 be cleaner, but "cleaner test of a parameter with no
   evidence of a problem" is not a reason to amend a freeze.** If the project wants the
   stop width to remain testable in future, B is the right shape and the parity test above
   is the right gate — but it should be chosen deliberately, on architectural grounds, and
   not as a follow-up to a result that does not support it.

---

## 6. If the human chooses Option B

The minimum safe sequence would be:

1. Add `sizing_atr_mult` to `ProductionConfig`, defaulting to **1.5**, documented as
   research-facing and default-equal to the stop multiple.
2. Change **only** the divisor in `src/agents/risk_manager.py::size_position`.
3. Add a parity test asserting `sizing_atr_mult == stop_atr_mult == 1.5` reproduces the
   current baseline **exactly**: 168 trades, +5.57 %, Sharpe 0.513, MaxDD −10.39 %, PF 1.13.
4. Re-run the full regression suite and the freeze audit; both must stay green.
5. Update `STOP_EXIT_V1_FREEZE.md` with an explicit amendment note recording what changed
   and why.
6. Only then re-run S1 as a **new** pre-registered experiment, and treat the old S1 numbers
   as superseded.

Steps 3 and 4 are the gate. If either fails, the amendment is reverted.

---

## 7. What is explicitly not being asked for

* No change to `stop_atr_mult`'s frozen value of 1.5.
* No change to any other production parameter.
* No change to `exit_engine_mode` (still `legacy`).
* No implementation of any option in this document.
* No commit, no push.

---

## 8. Status

**AWAITING HUMAN DECISION.** The research thread is otherwise complete: R7, S1, S2, S3,
R2 and R3 are all closed, and **none of them justifies a production change.** This
document is the only open item, and it is an architecture question rather than an
empirical one.
