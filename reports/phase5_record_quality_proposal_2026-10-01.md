# Phase 5 — Record-Quality & Contract-Accuracy Proposal (PROPOSAL ONLY — not applied)

**Date:** 2026-10-01 · **Status:** proposed, awaiting approval. **Nothing in this document has been
applied to the repository.**

Why this exists: the Phase-4/5 diagnostics had to *reconstruct* inputs and could not answer several
questions at all, because the records do not carry them. Every item below is **strategy-neutral**:
no parameter, no decision rule, no frozen semantics changes — only what gets written into the
records. Two items (P6, P7) are contract-accuracy findings rather than record gaps.

Constraints respected: `src/state/state.py` and `src/agents/setup_agent.py` are **protected/frozen**
and are deliberately **not** modified by any patch below.

---

## P1 — `holding_days` is `null` for all trades

* **Evidence:** `Counter({'None': 151})`. `src/state/state.py:100` writes `"holding_days": None` with
  the comment *由呼叫端填* ("to be filled by the caller") — and the caller never fills it.
* **Patch (proposed)** — `production/backtest.py`, in the SELL execution block:

```python
            for s in rec["exits"]["proposed"]:
                idx = next((i for i, p in enumerate(state["open_positions"])
                            if p["ticker"] == s["ticker"]), None)
                if idx is None:
                    continue
-               close_position(state, idx, float(s["exit_price"]), date_s,
-                              s["reason"], {}, fill_model=s["fill_model"])
+               trade = close_position(state, idx, float(s["exit_price"]), date_s,
+                                      s["reason"], {}, fill_model=s["fill_model"])
+               # calendar days, matching the frozen TIME_STOP convention
+               trade["holding_days"] = (
+                   datetime.strptime(date_s, "%Y-%m-%d")
+                   - datetime.strptime(trade["entry_date"], "%Y-%m-%d")).days
```

* **Risk:** additive field on the trade record; no decision path touched. `state.py` untouched.

---

## P2 — the risk gate stores only `allow` (not the engine's own reason)

* **Evidence:** `production/pipeline.py` persists a 6-key subset of the sizing dict, so
  `"風險預算 $X 不足以買 1 股"` — the engine's own explanation — is discarded. Phase-4 had to
  re-derive it (verified at 99.7 %).
* **Patch (proposed)** — `production/pipeline.py`, `rec["risk"]["sizing"].append({...})`:

```python
                 rec["risk"]["sizing"].append({
                     "ticker": t, "shares": sizing.get("shares", 0),
                     "stop": sizing.get("stop_price"),
                     "take_profit": sizing.get("take_profit"),
                     "risk_reward": sizing.get("risk_reward"),
                     "eff_size_mult": round(regime["position_size_mult"] * quality, 4),
                     "allow": bool(sizing.get("allow", False)),
+                    "reasoning": sizing.get("reasoning"),
+                    "risk_amount": sizing.get("risk_amount"),
+                    "stop_distance": sizing.get("stop_distance"),
+                    "position_value": sizing.get("position_value"),
                 })
```

* **Risk:** additive; the values already exist in the engine's return dict. Makes the gate
  self-documenting and removes the need for reconstruction.

---

## P3 — the entries phase's equity/cash are not recorded

* **Evidence:** the sizing call uses `st["equity"]` / `st["cash"]` as they stood *before* the
  session's mark-to-market; the record only contains the post-mark values, so Phase-4 had to
  reconstruct them from the previous session (cash as an upper bound).
* **Patch (proposed)** — `production/pipeline.py`, at the start of section 7:

```python
     regime_allows = bool(regime.get("position_size_mult", 0.0) > 0)
     entries = rec["entries"]
+    # the exact inputs the sizing engine saw (pre mark-to-market)
+    entries["pre_trade_equity"] = st.get("equity")
+    entries["pre_trade_cash"] = st.get("cash")
```

* **Risk:** additive diagnostics; no behaviour change.

---

## P4 — `veto_flags` are not carried into the backtest's regime log

* **Evidence:** `production/backtest.py` keeps only `regime_label / composite_score /
  position_size_mult / strategy_mode`, although the pipeline's own regime output includes
  `veto_flags`. Phase-4 could only count them because it captured DecisionRecords in-process.
* **Patch (proposed)** — `production/backtest.py`, the `regime_log.append({...})` call:

```python
                 regime_log.append({"date": date_s,
                                    **{k: rec["regime"]["output"][k]
                                       for k in ("regime_label",
                                                 "composite_score",
                                                 "position_size_mult",
                                                 "strategy_mode")},
+                                   "veto_flags": rec["regime"]["output"].get("veto_flags", [])})
```

* **Risk:** additive; explains *why* the multiplier moved on a given day.

---

## P5 — `extension_from_pivot_pct` is null for all trades (and cannot be otherwise)

* **Root cause (verified):** see the Phase-4 report §D3a. `production/backtest.py:274` already
  computes `ext`; the field is null only because `prior_high20` is always `None` for Pullback-only
  v1. **No patch can fill it without first resolving P6.**
* **Patch (proposed, conditional on P6):** none required in the backtest — line 316 already writes
  the value once `prior_high20` exists.

---

## P6 — CONTRACT MISMATCH: the frozen `max_extension_from_pivot_pct` filter is inert

* **Finding:** Setup v1 enables only `pullback`; `prior_high20` is computed only in the breakout
  path (`src/agents/setup_agent.py:94`, returned at :165/:170, surfaced at :321), so the dispatcher
  always returns `None` for pullback → `ext` is always `None` → the guard never fires.
  Verified live: `evaluate_setup(...)["prior_high20"] is None` for valid pullback setups.
* **Impact:** **no behaviour change and no trade affected** — the clause simply cannot block. But the
  freeze document (`src/agents/SETUP_V1_FREEZE.md`) and the backtest's documented execution
  conventions list this filter as active, so the *contract is inaccurate*, and the filter's value has
  never been tested.
* **Options for the human (an architecture decision is required — `setup_agent.py` is the frozen
  Setup v1 implementation):**
  1. **Record it as inert** in the freeze document (no code change) — recommended minimum.
  2. **Define a pullback-equivalent pivot** (e.g. the 20-day high *without* the breakout
     requirement) and wire it — a real Setup v1 amendment.
  3. **Remove the clause** from the documented contract.
* **Not done here:** any of the three. This proposal deliberately does not touch a frozen file.

---

## P7 — `components` is permanently `{}` for v1 trades (same root cause)

* **Finding:** the component attribution flags live in the breakout dict; pullback never produces
  them, so `state.py:121` always writes `{}`.
* **Impact:** component-level attribution of Pullback trades is impossible from the records — which
  is precisely the evidence a future "which part of the setup carries the edge?" study would need.
* **Options:** same three as P6 (the cleanest is to have the **pullback** function emit its own
  component flags: `px>50SMA`, `px>200SMA`, `50>200`, `nearEMA`, `vol_contract`, `RS_strong` — those
  names already appear in `entry_reason`, so the components are computed; they are simply not
  returned). **Requires an architecture decision.**

---

## P8 (documentation only) — `shadow_summary.enabled` is ambiguous

`enabled` currently means *"shadow results are present"* (`bool(results)`), which is `False` on a
session where shadow mode ran but no position was held. The authoritative mode indicator is
`exits.mode`. Since the Phase-3 sign-off approved the shadow schema, this is **not** changed — it is
flagged so it can be clarified in a future schema revision (or documented in
`STOP_EXIT_V1_FREEZE.md`).

---

## Verification plan for the patches (if approved)

1. `python production/tests/test_datasource.py` … `test_wiring_safety.py` → expect **118/118**
   (the patches are additive; `test_backtest.py` asserts counts/metrics, not these fields).
2. Re-run `production/backtest.py --start 2024-01-01 --end 2025-07-31` and confirm the **summary
   block is byte-identical** to the current one (same return / Sharpe / MaxDD / PF / trade count) —
   that is the proof that record enrichment changed no behaviour.
3. Re-run `production/tests/phase4_diagnostics.py` and confirm D1–D6 **numbers are unchanged** while
   the previously-empty fields are now populated.

## What this proposal explicitly does NOT do

No parameter change · no decision-rule change · no frozen semantics change · no touch of
`src/state/state.py` or `src/agents/setup_agent.py` · no change applied to any file (this is a
proposal) · no commit.
