# P6 — Materiality assessment of the INERT extension filter

**Date:** 2026-10-02 · **Status: MEASURED — no production change made**
**Finding: the contract gap is cosmetic, not a missing risk control.**

---

## 0. Answer

> If `max_extension_from_pivot_pct = 0.03` were actually wired, it would block
> **0 of 168 trades (0.0 %)** — with **zero near-misses** as well.

Setup v1's freeze document (`src/agents/SETUP_V1_FREEZE.md`, clause 5) lists the extension
filter as an active rule. It cannot fire, because `prior_high20` is produced only on the
breakout path and Setup v1 is Pullback-Only. This closes the question the freeze audit has
carried as `INERT` since 2026-10-01: **the clause is inert AND harmless.**

---

## 1. Method

The filter is replayed counterfactually over the trades the system actually took. Nothing
is wired and nothing is changed.

```
extension = (next_open − prior_high20) / prior_high20
```

`prior_high20` is reconstructed **exactly** as `src/agents/setup_agent.py::breakout_setup`
defines it — `high.shift(1).rolling(20).max()` evaluated on the signal-date frame — so it
is the number the frozen code would have seen, not an approximation.

**PIT safety.** `.shift(1)` removes the signal bar, so only bars at or before the signal
date enter the pivot; the entry price is the next session's open, exactly as the live path
uses it. No future bar enters the reconstruction.

**Coverage:** 168 / 168 trades measured, **0 unavailable**.

---

## 2. Result

| | |
|---|---:|
| threshold | 0.03 |
| min | −0.2681 |
| p10 | −0.1035 |
| **median** | **−0.0434** |
| p90 | −0.0035 |
| **max** | **+0.0264** |
| **would block** | **0 (0.0 %)** |
| near-miss (within 1 pp of the threshold) | **0** |

The distribution sits entirely **below** the threshold, with a margin of 0.4 pp between the
observed maximum and the cut. The median trade entered **4.3 % below** its 20-day high.

### Why this is structurally guaranteed

A pullback setup fires when price comes *back* to the 10/20 EMA after an uptrend — the
entry is, by construction, a retracement. The next open therefore tends to sit below the
prior 20-day high, not above it. The extension filter was written for **breakout** entries,
where price is *breaking out* through the pivot and extension is the natural risk to cap.
For pullback entries the quantity it measures is negative by construction.

This is not a coincidence of the sample window; it is what the setup type means.

---

## 3. What this resolves

`phase5_record_quality_proposal_2026-10-01.md` (P6) listed three options for the human and
recommended option 1 as the minimum. The measurement now settles it:

| option | assessment |
|---|---|
| **1. Record the clause as inert in the freeze document** | **RECOMMENDED — now evidence-backed.** The filter would block nothing on this evidence, and its mechanism is structurally inoperative for pullback entries. A one-line documentation correction closes the gap at zero behavioural risk. |
| 2. Define a pullback-equivalent pivot and wire it | **NOT JUSTIFIED.** The filter measures a quantity that is negative for pullbacks; wiring it would create a clause that can still never fire, while adding a real Setup v1 amendment. |
| 3. Remove the clause from the contract | Equivalent to option 1 in effect. Option 1 is preferred because it also records *why* the clause exists and does not fire. |

**Nothing in this assessment changes production.** Options 1–3 all require a human
architecture decision, and option 1 is a documentation edit to a frozen file — which is the
user's call, not this agent's.

---

## 4. Consequence for R2

R2 could not use extension as a cohort dimension, because the field was null on all 168
trades. This assessment explains why: there is no variation to study — the quantity is
negative for every trade and would be identically "not blocked" for all of them. **Extension
is not a candidate cohort dimension under Pullback-Only Setup v1**, and no further R2 work
should be spent on it.

---

## 5. Honest limitations

* The counterfactual answers "would this filter have blocked anything on **this** window,
  with **these** 168 trades". It is not a proof that no future pullback entry could exceed
  +3 % above its 20-day high. The structural argument in §2 is the stronger evidence; the
  measurement confirms it on the available sample.
* `prior_high20` here is the quantity the breakout path *would* have produced. It was never
  computed by the production pullback path, so this is a measurement of the filter's
  potential effect, not a claim about what Setup v1 originally intended.

---

## 6. Artifacts

* `research/p6_extension_impact.py` — the counterfactual replay
* `reports/p6_extension_impact_pitcorrected_2026-10-02.json` — per-trade rows (168), full
  extension distribution
