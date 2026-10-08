# R7 — Observability (strategy-neutral logging)

**Date:** 2026-10-02 · **Status:** APPLIED and VALIDATED · **Baseline:** the PIT-corrected one
(commit `da9f5dc` + the PIT fix, 2024-01-02 → 2025-07-31, 396 sessions, 168 trades)

> R7 changed **what the record says**, not **what the system does**. The proof is §5.

---

## 0. Decision

| | |
|---|---|
| Production strategy parameters changed | **NO** |
| `exit_engine_mode` | **`legacy`** (unchanged) |
| Frozen contracts touched | **NO** (Regime v1 / Setup v1 / Stop-Exit v1 all intact) |
| Backtest behaviour | **byte-identical** (§5) |
| New production file | `production/observability.py` (diagnostics only) |
| New research-only config flag | `research_blocked_setup_eval` (default `False`) |

---

## 1. What was unobservable, and why it mattered

The corrected baseline's low return had been attributed to low exposure, but three
questions could not be answered **at all** because the records did not carry the answers:

1. **How much opportunity was foregone** when entries were blocked before any setup ran
   (136 `regime_defensive` + 131 `max_open_positions` sessions). The pipeline returned
   *before* evaluating setups, so the skipped population existed nowhere.
2. **Why** the risk gate rejected a candidate. Only a 6-key subset of the sizing result
   was persisted, so the engine's own explanation (`風險預算 $X 不足以買 1 股`) was discarded.
3. **Which setup components** produced each trade's score. `components` was permanently
   `{}` because Setup v1 is Pullback-Only and that dict exists only on the breakout path.

Item 3 is why research item **R2 (entry cohorts)** had been stuck at BLOCKED.

---

## 2. §2 — Capacity observability (blocked-entry snapshot)

Implemented per `reports/capacity_observability_proposal_2026-10-01.md`, in two tiers.

### Tier A — always on, zero evaluation cost

Written to `rec["setup"]["blocked_snapshot"]` on every session where entries were
blocked, and also on sessions where capacity was exhausted *after* evaluation:

| Field | Source |
|---|---|
| `blocked_stage` / `blocked_reason` | the branch that blocked |
| `regime` (label, size mult, **veto flags**) | frozen Regime v1 output |
| `portfolio.n_open` / `max_open_positions` / `capacity_slots_free` | `state` at decision time |
| `portfolio.current_open_positions` | full position list (ticker/shares/entry) |
| `portfolio.positions_competing_for_capacity` | tickers actually competing for a slot |
| `capital.equity` / `available_cash` / `risk_budget_usd` | pre-mark-to-market state |
| `candidates.n_candidates` / `tickers` | the population that went unexamined |

### Tier B — research-only, opt-in

`ProductionConfig.research_blocked_setup_eval` (default `False`). When enabled, the
**unmodified** production `evaluate_setup` + `size_swing_position` are run over the
blocked candidates, so the foregone cohort is measured with the real engines rather
than a model of them. The result is written *after* the decision to block, never into
`buys` and never into `state`. It must never be enabled in live.

**Verified inert:** `test_r7_observability.py::test_behavioural_equivalence_on_a_real_session`
runs one real PIT session with the flag off and on, and asserts that **every**
decision-bearing section of the DecisionRecord is identical once the additive R7 keys
are stripped.

---

## 3. §3 — Entry observability

| Field | Exactness | PIT safety |
|---|---|---|
| `holding_days` | **exact** — calendar days, the frozen TIME_STOP unit | computed at exit only |
| `score_components` (7 named pullback flags) | **exact** for *which components fired*; EMA10-vs-EMA20 is not distinguishable (the engine emits one `nearEMA` token) | parsed from the engine's own `entry_reason` |
| `risk.gate.*` (risk budget, size before flooring, each cap, final size, binding gate) | **exact + self-checked** against the real engine's returned share count | sizing inputs as they stood pre-fill |
| `entries.pre_trade_equity` / `pre_trade_cash` | **exact** — the values the sizing engine saw | pre-mark-to-market |
| `entries.regime_gate` (incl. `veto_flags`) | **exact** | Regime v1 output |
| `signal_close`, `entry_open`, `next_open_gap_pct`, `entry_atr`, `entry_atr_pct_of_price` | **exact** | recomputed from bars ≤ as_of |
| `risk_budget_usd`, `stop_distance_usd`, `r_unit_usd`, `shares_requested_before_flooring` | **exact** | from the frozen sizing inputs |
| `regime_log.veto_flags` (396 sessions) | **exact** | Regime v1 output |

### Recorded as unavailable rather than reconstructed

Per §3, nothing was invented:

* **`extension_from_pivot_pct`** — remains `null`, and now says *why*:
  `extension_filter_status` records that `prior_high20` is `None` because Setup v1 is
  Pullback-Only, so the frozen `max_extension_from_pivot_pct` guard is **INERT** and
  never evaluated the entry. **No look-alike value was substituted.**
* **`components`** in the trade log — still `{}` on all 168 trades, because
  `src/state/state.py` is protected and the frozen Setup v1 contract is unchanged. The
  parsed attribution lives in the adjacent `score_components` field instead, so the
  frozen field's contract is not silently redefined.

### The self-check that makes the diagnostics trustworthy

`risk_gate_diagnostics` re-derives the sizing gate and writes
`selfcheck ∈ {MATCH, MISMATCH, NOT_COMPUTABLE}` comparing its result against the share
count the **real** `size_position` returned. If the re-derivation ever drifts, R7 fails
loudly instead of publishing a plausible-looking number. Result on the baseline:
**MATCH on every sizing call.**

---

## 4. What R7 now makes answerable

* **R3 (capacity magnitude)** — the foregone population is now in the record; Tier B
  makes its *value* measurable.
* **R2 (entry cohorts)** — `score_components` unblocks component-level attribution,
  which was the specific blocker recorded in the experiment matrix.

Component frequency across the 168 baseline trades (newly measurable):

| Component | n | share |
|---|---:|---:|
| `px>200SMA` | 165 | 98.2 % |
| `px>50SMA` | 160 | 95.2 % |
| `50>200` | 158 | 94.0 % |
| `vol_contract` | 153 | 91.1 % |
| `RS_strong` | 149 | 88.7 % |
| `nearEMA` | 79 | 47.0 % |
| `reversal` | 12 | **7.1 %** |

**Immediate observation (not yet a conclusion):** `reversal` — one of the four
conditions the Setup v1 freeze document describes as part of the pullback definition —
fires in only 7.1 % of trades, while the three trend-structure components fire in
>94 %. This is consistent with the freeze audit's existing `CLARITY` verdict that the
pullback score is a weighted *sum*, not a conjunction. It is a candidate for R2.

---

## 5. §1 / §4 — Acceptance criteria

| Criterion | Result |
|---|---|
| All logging changes strategy-neutral | **PASS** |
| Baseline summary unchanged | **PASS** — byte-identical |
| Trade-level behaviour unchanged | **PASS** — 0 mismatches |
| Existing regression tests pass | **PASS** — 115/115 + 58/58 new |
| No production parameter changed | **PASS** |
| Control Centre registry updated | **PASS** (§7) |
| Exact fields added documented | **PASS** (§3) |

### Behavioural equivalence — `reports/r7_equivalence_2026-10-02.json`

Two full backtest runs of the same window, before and after R7, diffed field by field:

```
summary identical                     : True
trades                                : 168 -> 168
critical trade fields checked         : 15 x 168 trades
  (ticker, shares, entry_price, exit_price, exit_date, exit_reason,
   exit_fill_model, net_pnl, gross_pnl, return_pct, r_multiple,
   stop_price, target_price, entry_date, entry_regime)
critical trade-field mismatches       : 0
trade_log    (R7 fields removed)      : identical
equity_curve (R7 fields removed)      : identical
skipped      (R7 fields removed)      : identical
screens      (R7 fields removed)      : identical
regime_log   (R7 fields removed)      : identical
```

Summary, before and after: return **+5.57 %**, CAGR 3.51 %, Sharpe **0.513**,
MaxDD **−10.39 %**, trades **168**, win rate 47.6 %, PF **1.13** — identical.

Populated as a result: `holding_days` 168/168 (mean 9.78 d, max 32 d, previously
`null` on all 168); `score_components` 168/168; `veto_flags` on 97 of 396 sessions.

### Regression suite

`datasource 8 · pipeline 9 · backtest 5 · contracts 18 · stops 19 · exit_engine 31 ·
wiring_safety 18 · pit_breadth 7` = **115/115**, plus the new
`test_r7_observability` = **58/58**.

`freeze_conformance_audit.py`: **13 OPERATIVE / 9 TEST-VERIFIED / 1 INERT / 2 CLARITY /
0 MISMATCH** — unchanged from before R7. `replay_exit_parity.py`: **0 divergences**.

---

## 6. Honest limitations

* Tier B is **off by default**, so a default run records the blocked *population* but
  not the blocked cohort's *value*. R3's magnitude question needs Tier B switched on
  in a research run.
* `score_components` cannot distinguish EMA10 from EMA20 pullbacks — the frozen engine
  emits a single `nearEMA` token. Fixing that would require amending Setup v1.
* `extension_from_pivot_pct` stays `null`. Filling it needs the P6 architecture
  decision (define a pullback-equivalent pivot, or record the clause as inert).
* R7 makes fields *available*; it does not by itself make any strategy claim. Every
  hypothesis in S1–S3 is tested separately.

---

## 7. Control Centre registry

`production/observability.py` is classified as **`production_used = yes`** (the pipeline
imports it on every run) with **`production_decision_authority = NONE`** — it computes
no decision, issues no order and touches no state. `research/verify_r7_equivalence.py`
and `research/run_stop_trailing_experiments.py` are research-only.

Registry refreshed via `python control_center/refresh_registry.py`.

---

## 8. Artifacts

| File | Content |
|---|---|
| `production/observability.py` | the diagnostics (risk gate, components, snapshot) |
| `production/tests/test_r7_observability.py` | 58 acceptance assertions |
| `research/verify_r7_equivalence.py` | the behavioural-equivalence differ |
| `reports/r7_equivalence_2026-10-02.json` | machine-readable equivalence proof |
| `reports/r7_observability_2026-10-02.md` | this document |

---

## 9. Status

**R7: COMPLETE.** S1–S3 were run only after this passed
(`reports/stop_trailing_experiments_pitcorrected_2026-10-02.md`).

Human review required. No commit, no push, no production change.
