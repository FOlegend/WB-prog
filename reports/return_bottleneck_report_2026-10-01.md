Approved. Proceed with the PIT correction and full research re-baseline, subject to the controls below.

## 1. PIT fix — APPROVED

Proceed with fixing the Regime v1 breadth point-in-time contract.

Preferred approach:

> **Fix the loader/data boundary so `get_breadth(end=as_of)` guarantees all returned observations satisfy `date <= as_of`.**

Prefer this over relying on individual callers to remember to slice the data.

Do not change:

- breadth calculation formula
- HMM calculation
- regime weighting
- regime thresholds
- regime labels
- divergence cap
- regime multipliers
- Setup rules
- Stop rules
- Exit rules
- Risk rules
- Portfolio rules

This is a **PIT/data-integrity correction**, not a strategy optimization.

### Required regression protection

Add tests proving:

1. different historical `as_of` dates do not silently use the same future tail;
2. no returned breadth observation is later than `as_of`;
3. cache path is PIT-safe;
4. rebuild path is PIT-safe;
5. exact-date observations are correctly selected;
6. missing/non-trading-day `as_of` follows the existing documented date semantics.

The test suite must explicitly protect against the discovered failure mode where historical requests all use the 2025-07-31 tail.

---

## 2. Full baseline re-run — APPROVED

After the PIT fix and regression tests pass:

Re-run the complete historical baseline for:

```text
2024-01-02 → 2025-07-31
```

using:

- unchanged universe
- unchanged data assumptions
- unchanged execution assumptions
- unchanged costs/slippage
- unchanged strategy parameters
- `exit_engine_mode = legacy`

Do not use the previous +7.99% as the current baseline.

Create a new corrected baseline and preserve the old result only as an audit artifact marked:

```text
SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE
```

---

## 3. Re-run all affected research — APPROVED

After the corrected baseline is established, regenerate:

- Phase 4 D1–D6
- V1–V4
- Phase 5 Step 0
- R1

Do not selectively preserve old findings.

For each previous conclusion, classify it as:

- CONFIRMED
- REJECTED
- CHANGED
- INCONCLUSIVE
- NOT MEASURABLE

Any conclusion that depends on the contaminated regime must be re-derived from the corrected data.

In particular, do NOT assume that the previous conclusions:

- “exposure dominates”
- “marginal capital has negative expectancy”
- “regime filter is protective”
- “stop is not the bottleneck”

remain quantitatively valid.

Re-test them.

---

## 4. R1 — WITHDRAW OLD RESULT

The previous R1 TP result is formally withdrawn.

Do NOT use the previous:

```text
2.5 → 3.0 → 3.5 → 4.0 → 4.5 → 5.0
```

performance progression as evidence.

After the PIT-correct baseline is available, rerun only the original pre-registered R1 grid first:

```text
take_profit_atr_mult:

2.0
2.5
3.0
3.5
4.0
```

Keep every other variable fixed.

Do NOT automatically extend to 4.5 or 5.0.

Do NOT call any TP value “optimal”.

The purpose is to determine whether the previous R1 signal survives under clean PIT data.

---

## 5. Live breadth staleness — APPROVED AS A SEPARATE DATA-INTEGRITY TASK

Yes, require the system to expose the actual breadth tail date.

The ledger/diagnostic record should capture at minimum:

```text
breadth_tail_date
```

alongside the existing breadth metadata.

The objective is to prevent a live run from silently appearing current while using stale breadth.

However:

> **Do not invent or silently introduce a new trading strategy freshness rule.**

First inspect the existing project/data contracts and determine the appropriate freshness behaviour.

If no existing threshold exists:

- document the issue;
- propose a threshold/behaviour;
- do not silently change live trading decisions without explicit approval.

The desired safety property is:

> stale breadth must become visible and must not silently masquerade as current data.

Whether the final behaviour is warning or fail-loud should be explicitly documented.

---

## 6. Regime v1 freeze contract — APPROVED FOR AUDIT

Inspect the Regime v1 freeze and determine whether it assumes PIT-correct inputs.

Please distinguish clearly between:

### Case A

The freeze explicitly assumes PIT data.

Then classify the current problem as:

> **implementation/data-adapter contract violation**

rather than a flaw in the Regime v1 decision mathematics.

### Case B

The freeze does not explicitly state the PIT requirement.

Then:

- do not silently change the strategy;
- identify this as a contract/documentation ambiguity;
- propose a documentation/contract clarification.

Do not rewrite frozen strategy mathematics merely because the data adapter was incorrect.

---

## 7. Production freeze status

After this task:

```text
production strategy parameters = unchanged
exit_engine_mode = legacy
Stop/Exit strategy contract = unchanged
Risk parameters = unchanged
Entry parameters = unchanged
Portfolio parameters = unchanged
```

The PIT correction is allowed because it repairs data integrity.

It must NOT be treated as approval to:

- increase risk
- increase exposure
- widen TP
- widen stops
- change regime multipliers
- change portfolio capacity
- enable the new Exit Engine

Those remain separate human-approved decisions.

---

## 8. Git / change management

Do NOT commit or push on my behalf.

Prepare the changes and reports, then stop for human review.

Before stopping, provide:

- `git status`
- changed files
- test results
- corrected baseline result
- affected reports
- registry status
- any new OPEN decisions
- any production-used files changed

Protected/frozen files must be explicitly checked.

---

## 9. Final report required

At completion, provide a concise decision report containing:

### A. PIT root cause

What was wrong and exactly why it created look-ahead.

### B. PIT fix

What was changed and why the chosen location is safest.

### C. Regression proof

Exact tests proving the problem cannot silently recur.

### D. Corrected baseline

Return, CAGR, Sharpe, Sortino, MaxDD, Calmar, PF, win rate, avg R, exposure, regime distribution.

### E. Corrected D1–D6

All previous performance-dependent bottleneck findings re-derived.

### F. Corrected Step 0

Accepted control + rejected/marginal cohorts.

### G. Corrected R1

Full pre-registered grid with subperiod results.

### H. Updated conclusion

Answer:

> **After correcting the PIT problem, what actually limits the strategy's return?**

Do not rank or optimize prematurely.

### I. Next research step

Choose the next research direction only from the corrected evidence.

---

## 10. Important research principle

Do not optimize the historical return.

The objective is:

> **Establish a clean point-in-time data foundation, reproduce the strategy honestly, identify the true return bottleneck, and only then test whether a specific change has robust evidence behind it.**

Proceed in this order:

```text
PIT Fix
   ↓
PIT Tests
   ↓
Clean Baseline
   ↓
D1–D6 / V1–V4
   ↓
Step 0
   ↓
R1
   ↓
Reassess Return Bottleneck
   ↓
Human Review
```

Do not proceed to new strategy optimization before this sequence is complete.














## 10. Observability limits (spec §10) — do not repeat these errors

Every item below is a place where a metric **cannot** be measured from current records. None is  
fabricated; each is either disclosed as a limitation or listed as the logging change that would fix it.

| # | Limit                                                                                                                                              | Effect on this analysis                                                                  | Fix (see `phase5_record_quality_proposal_2026-10-01.md`) |
| - | -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 1 | `extension_from_pivot_pct` is `null` for **all** 151 trades (the freeze-claimed `max_extension_from_pivot_pct` filter is **INERT** under Setup v1) | the extension dimension is **unmeasurable**                                              | P6/P7 (contract decision)                                |
| 2 | `components` is `{}` for all trades (pullback path never fills it)                                                                                 | component-level attribution unavailable                                                  | P7                                                       |
| 3 | `holding_days` is `null` for all trades (computed here from dates instead)                                                                         | was recomputed, not read                                                                 | P1                                                       |
| 4 | the risk gate persists only `allow`, not its `reasoning` string                                                                                    | rejection causes had to be **re-derived** by re-calling the engine (99.7 % verified, V1) | P2                                                       |
| 5 | `veto_flags` are not persisted in the trade log                                                                                                    | veto attribution had to come from the regime log                                         | P4                                                       |
| 6 | the equity/cash used by the *entries* phase is not persisted                                                                                       | reconstructed from the previous session's mark-to-market                                 | P3                                                       |
| 7 | **foregone setups are unobservable** when entries are blocked (the pipeline skips setup evaluation)                                                | bottleneck #4's magnitude is a **lower bound**                                           | requires a design decision                               |
| 8 | `r_multiple` is quantised (+1.667 / −1.0)                                                                                                          | R-distribution statistics are coarser than they look                                     | —                                                        |
| 9 | entry `signal_close` is not persisted in the trade log                                                                                             | the Step-0 harness fell back to `entry_price` for its (disabled) gap filter              | P3-adjacent                                              |

---


## 11. Prerequisite: is the research harness trustworthy? (spec §11) — **PASS**

Verified **before** running any parameter experiment:

| Check                                                    | Result                                                                                                                                                                        |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Direct Stop/Exit replay parity (`replay_exit_parity.py`) | **151 trades / 1073 bars / 0 divergences**; **0** adapter-vs-direct mismatches; 151/151 trade reconciliation; exit distribution identical (TP 60 / SL 75 / TIME 6 / TRAIL 10) |
| Step-0 control (accepted cohort re-measured)             | **148/148 comparable trades match in both exit reason and R (±0.05)**                                                                                                         |
| The 3 previously-unexplained "mismatches"                | **all classified**: `HARNESS_HORIZON_GUARD_ARTIFACT` — see below                                                                                                              |

**The 3 mismatches, classified.** All three are trades whose exit lands on the **first session at or  
after** the 30-calendar-day boundary, which fell on a weekend:

| Ticker | signal     | recorded                          | held | why the harness missed it |
| ------ | ---------- | --------------------------------- | ---: | ------------------------- |
| SYF    | 2024-01-31 | TIME_STOP 2024-03-04, R=1.173     |   32 | entry+30 = Sat 2024-03-02 |
| ROL    | 2025-05-08 | TRAILING_STOP 2025-06-09, R=0.109 |   31 | entry+30 = Sun 2025-06-08 |
| WRB    | 2025-05-08 | TIME_STOP 2025-06-09, R=0.054     |   31 | entry+30 = Sun 2025-06-08 |

The Step-0 simulator stops at `held > 30` calendar days **before** evaluating the exit on that bar, so  
it never sees the session that production's TIME_STOP (`held ≥ 30`) fires on. **Proof of cause:**  
re-simulating the same three trades with a larger horizon reproduces the recorded exit exactly  
(SYF TIME_STOP R=1.1727≈1.173; ROL TRAILING_STOP R=0.1088≈0.109; WRB TIME_STOP R=0.0541≈0.054) →  
`all_reproduce_with_larger_horizon = true`.

**Verdict:** the harness is **trustworthy**. The artifact is a simulator guard off-by-one, not an  
engine, data, or reference-price problem; it affects **3/151 (2.0 %)** of control trades and biases the  
Step-0 marginal-cohort figures only slightly (a handful of near-boundary TIME_STOP/TRAILING outcomes  
are excluded from its averages). It does **not** change the Step-0 verdict, whose margin is large  
(−0.270 R vs +0.182 R).

Artifacts: `reports/step0_harness_audit_2026-10-01.json`, `reports/research_replay_parity_2026-10-01.json`.

---

## 12. Limitations of this report

- **Single bull window.** 2024-01→2025-07 is a rising market; every "more exposure" statement is  
  mechanically flattering and must be judged risk-adjusted. The project's own 2018–2025 graduation  
  evidence reverses during bear phases.
- **Universe is not survivorship-free** (current index constituents used for historical screening —  
  already disclosed and accepted in the freeze contract).
- **The Step-0 marginal-cohort measurement is hypothetical** (it trades rejected setups with a  
  fixed next-open entry); it is a value estimate, not a portfolio backtest.
- **`max_open_positions` magnitude is unmeasurable** from current artifacts (§10 #7).
- **Small cohorts** are flagged: BULL entries n=24; entry-gap buckets 8–20 trades.

**No production change is implied by this report.** It is diagnosis only.
