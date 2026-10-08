# Frozen-Contract Conformance Audit

**Date:** 2026-10-01 · **Status:** read-only audit, complete. **Nothing modified.**
Runner: `production/tests/freeze_conformance_audit.py` (re-runnable after any change) ·
Data: `reports/freeze_conformance_audit_2026-10-01.json`

---

## 1. Why

A freeze contract is only as good as the match between what it *claims* and what the code *does*.
The Phase-4 diagnostics found one clause that is documented but cannot fire (P6). This audit applies
the same method **systematically** to every clause of all three freeze contracts.

**Verdict vocabulary**

| verdict | meaning |
|---|---|
| **OPERATIVE** | the clause exists in code *and* its inputs exist at runtime |
| **INERT** | the clause exists in code but its input never exists at runtime, so it cannot fire |
| **MISMATCH** | the documented value/rule differs from the code |
| **CLARITY** | the code matches only under one reading of the document |
| **TEST-VERIFIED** | proven by an existing unit/parity test rather than by this audit |

## 2. Result

**13 OPERATIVE · 9 TEST-VERIFIED · 1 INERT · 2 CLARITY · 0 MISMATCH**

| contract | clauses | outcome |
|---|---|---|
| `src/agents/SETUP_V1_FREEZE.md` | 8 | 5 OPERATIVE · 1 TEST-VERIFIED · **1 INERT** · 1 CLARITY |
| `regime_dual_engine/REGIME_V1_FREEZE.md` | 8 | 6 OPERATIVE · 1 TEST-VERIFIED · 1 CLARITY |
| `production/STOP_EXIT_V1_FREEZE.md` | 7 | 7 TEST-VERIFIED |

No frozen value was found to disagree with the code: `setup_enabled_types=["pullback"]`,
`setup_score_threshold=0.5`, the quality mapping (1.0/0.75/0.5), `max_entry_gap_pct=0.02`,
`hmm_weight`/`breadth_weight`=0.5/0.5, `bull_threshold`/`bear_threshold`=65/35,
`divergence_cap=0.50`, `enable_dist_day_overlay=False`.

## 3. The single functional gap — INERT (this is P6)

**Setup v1 clause 5: `max_extension_from_pivot_pct = 0.03`.**

| evidence | |
|---|---|
| `prior_high20` computed inside `breakout_setup`: | **True** |
| `prior_high20` computed inside `pullback_setup`: | **False** |
| Setup v1 enabled types | `["pullback"]` only |
| dispatcher return | `best.get("prior_high20")` → always `None` |
| `production/backtest.py:274` | `ext = ... if prior_high20 else None` → always `None` |
| guard | `if ext is not None and ext > cfg.max_extension_from_pivot_pct` → never fires |
| observed trade log | `extension_from_pivot_pct` ∈ `{None}` for all 151 trades |

**Impact: no behaviour change, no trade affected** — the clause simply cannot block an entry. What is
affected is *contract accuracy* and the ability to ever test the filter's value. Same root cause keeps
`components` (per-component attribution) permanently `{}` for v1 trades.
Fix requires an architecture decision (`src/agents/setup_agent.py` is the frozen Setup v1
implementation): (a) record it as inert, (b) define a pullback-equivalent pivot, or (c) remove it.

## 4. The two CLARITY items (documentation precision, not defects)

**C1 — Setup clause 8 is written as a conjunction of four conditions; the code is a weighted sum with
a threshold.**

The implemented increments are `px>50SMA 0.10 · px>200SMA 0.10 · 50>200 0.10 · nearEMA 0.25 ·
vol_contract 0.20 · reversal 0.15 · RS_strong 0.10` (max 1.00, threshold 0.50). So **structure (0.30)
+ nearEMA (0.25) = 0.55 is a valid pullback with neither volume contraction nor a reversal candle**.
The document's "+" reads as "all four required". Also, the documented "RS 強勢" is implemented as
*price ≥ 0.9 × 50-day high* — a self-referential strength proxy, not a cross-sectional RS-vs-SPY test
(the SPY-relative RS filter lives in the **screener**, which is a different component).

This also **explains the Phase-4 D3 finding** that the setup score has no ordering power: the score is
a coarse count of booleans, i.e. it is doing the job of a *filter*, not of a *ranking signal*. That is
a design property, not a defect.

**C2 — Regime clause 2 states a fixed formula (`×50 / ×0.5`) while the code is a generalised weighted
mean** — `(hmm_bull_prob×100×w_hmm + breadth_percentile×w_breadth) / (w_hmm + w_breadth)`. They are
identical while the weights stay 0.5/0.5 (which is frozen), so this is a precision note only.

## 5. Honesty note — the audit's own first run produced two false MISMATCHes

The first version of this audit flagged (i) the composite formula and (ii) the DistDays prohibition.
Both were **audit artefacts, not repository defects**, and were fixed before publication:

* the composite check demanded the literal token `breadth_percentile_score` (the code says
  `breadth_percentile`) — operand check now tests the actual expressions;
* the DistDays check flagged the bare token `distribution_days`, which appears only as an import plus
  a **config-gated branch** (`enable_dist_day_overlay=False` in production) — the operative guarantee
  the freeze actually makes. The check now tests that guarantee.

An audit that emits false findings is worse than no audit, so both were corrected and the runner now
documents why.

## 6. Recommendation

1. **Record this audit as a standing verification anchor** — re-run
   `production/tests/freeze_conformance_audit.py` alongside the test suite after any change touching
   setup, regime, or exit code. It answers "are the freeze contracts still true?" in one command.
2. **Resolve P6** (the INERT clause) with one of the three options; whichever is chosen, the freeze
   document should end up *accurate*.
3. **Optionally tighten the two CLARITY clauses** in their respective freeze documents (they are
   documentation edits, which every freeze contract explicitly permits without unfreezing).
