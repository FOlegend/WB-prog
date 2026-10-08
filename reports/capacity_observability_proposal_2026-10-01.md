# Proposal — make the foregone-setup volume observable (R7 part 2)

**Status: PROPOSED — NOT APPLIED.** Requires explicit human approval (it is a production-code change,
even though it changes no trading decision).
**Date:** 2026-10-01 · **Related:** `experiment_matrix_pitcorrected_2026-10-01.md` R3/R7,
`pit_correction_and_rebaseline_2026-10-01.md` §E/§I

---

## 1. The problem (measured, not assumed)

On the corrected baseline, the entries phase is **blocked outright** on a large number of sessions:

| Session-level block | Sessions (of 396) |
|---|---:|
| `regime_defensive` (position_size_mult = 0) | **136** |
| `max_open_positions reached` | **131** |

When either fires, `production/pipeline.py` **returns before evaluating any setup** — so the number and
quality of the setups that were skipped is **nowhere in the record**. Consequence: the magnitude of the
capacity constraint (research item **R3**) cannot be measured at all, which is why R3 has stayed "BLOCKED"
across every phase. The direct lever (L2, `max_open_positions` 5 → 6) can still be *tested*, but a lever
test does not tell us how much opportunity exists.

This is a **logging/observability gap, not a strategy defect.** Nothing here proposes to change entries.

## 2. The exact code path

`production/pipeline.py`, entries phase (≈ lines 386–404):

```python
if regime_failure:                       # -> BLOCKED
    ...
elif screen_blocked:                     # -> BLOCKED
    ...
elif not regime_allows:                  # -> ST_EMPTY, RETURNS WITHOUT EVALUATING
    entries["blocked_reason"] = f"regime {regime['regime_label']} — no new longs (defensive/cash)"
else:
    n_budget = max(0, cfg.max_open_positions - n_open)
    if n_budget <= 0:                    # -> ST_EMPTY, RETURNS WITHOUT EVALUATING
        entries["blocked_reason"] = "max_open_positions reached"
    else:
        for c in cands:                  # <-- the ONLY place setups are evaluated
            ... evaluate_setup(...)
```

## 3. Proposed change — two tiers, both decision-neutral

### Tier A — record the blocked screen population (no evaluation, negligible cost)

In the two `ST_EMPTY` branches, before returning, record what *was* available:

```python
rec["setup"]["blocked_snapshot"] = {
    "blocked_reason": entries["blocked_reason"],
    "n_candidates": len(cands),
    "tickers": [c["ticker"] for c in cands][:50],
}
```

Cost: one list comprehension. Answer "how often were candidates available but unusable?" — still not
*value*, but it establishes the exposure count from the record instead of from a reconstruction.

### Tier B — evaluate the blocked candidates (research-only, opt-in)

Add `research_blocked_setup_eval: bool = False` to `ProductionConfig` and, **only when true**, run the same
`evaluate_setup` loop over `cands` and store the results under
`rec["setup"]["blocked_snapshot"]["valid"]`. This makes the foregone cohort measurable with the **existing**
Step-0 machinery instead of a parallel harness.

Cost: `evaluate_setup` over ~30–40 candidates on ~267 blocked sessions ≈ a large runtime increase; hence
**off by default** and never enabled in live.

**Default-off matters:** it keeps the production path byte-identical and keeps the capability explicitly
research-scoped.

## 4. Why this does not change trading behaviour (verification plan)

1. **No branch condition changes.** The snapshot is written *after* the decision to block has been taken;
   `entries.status`, `blocked_reason`, `proposed`, `n_buys`, `exits`, `state`, `cash` and every order are
   untouched.
2. **Regression proof required before merge:** run the corrected baseline twice — flag off (unchanged) —
   and assert the **post-correction backtest summary is byte-identical** (`summary`, `trade_log`,
   `equity_curve`, `skipped`, `regime_log`, `screens`). Any difference other than the new
   `blocked_snapshot` blocks means the change is not decision-neutral and must be rejected.
3. **Test to add** (`production/tests/test_pit_breadth.py` or a new
   `test_blocked_observability.py`): with the flag off, the DecisionRecord for a blocked session has no
   `blocked_snapshot`; with it on, the snapshot is present **and** `entries`, `recommendations` and the
   trade log are unchanged for the same session.
4. **Frozen-surface check:** `freeze_conformance_audit.py` must still report 0 MISMATCH; no Setup / Stop /
   Exit / Risk / Portfolio rule is touched.

## 5. Explicitly NOT proposed

* Not changing `max_open_positions`, `risk_per_trade`, or any sizing rule (that is lever experiments L1/L2,
  which are separate and already running).
* Not adding a "reserve a slot" or ranking change.
* Not persisting `current_stop_price` or any state-schema change.
* Not enabling Tier B in live or in the default research path.

## 6. What it unblocks

| Research item | Blocked on | After Tier A | After Tier B |
|---|---|---|---|
| **R3** capacity magnitude | unobservable by design | exposure count known | foregone-cohort **value** measurable |
| **R2** entry quality | `extension_from_pivot_pct` null, `components` `{}` | no | partially (blocked cohort only) |
| Step-0-style analysis on blocked sessions | needs a bespoke harness | no | reuses the existing machinery |

**Decision requested:** approve Tier A only, Tier A + B, or neither.
