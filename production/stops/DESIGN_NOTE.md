# Stop Engine — Design Note (Phase 0-1)

**Status:** Phase 1 complete — isolated implementation + tests, **not wired** into production.
**Date:** 2026-09-28 · **Scope:** contracts + Stop Engine only.
**Freeze context:** Regime v1 and Setup v1 untouched; production behaviour unchanged.

---

## 1. Responsibility split (why two engines)

```
Setup  ->  Entry Decision  ->  Stop Plan  ->  Risk Decision  ->  Order Intent  ->  Execution
                                     |
Position  ->  Stop Engine (levels)  ->  Exit Engine (decision)  ->  Exit
```

| Engine | Question it answers | Phase 1 status |
|---|---|---|
| Stop Engine | "Where is the stop, and why?" | **implemented (isolated)** |
| Exit Engine | "Should we exit?" | not in scope (still `_exit_check` in production) |
| Risk Engine | "How many shares may we take?" | contract only |
| Entry Engine | "Do we want this trade?" | contract only |

The Stop Engine never decides to exit; it only produces/maintains stop levels.
This is the split required before any exit research can be measured.

---

## 2. Contracts (`production/contracts/`, Phase 0)

| Model | Purpose | Key fields |
|---|---|---|
| `EntryDecision` | entry intent | `approved, ticker, direction, setup_type, signal_date, signal_price, execution_type, execution_window, expected_execution_price?, entry_score?, entry_reason, vetoes, source_components` |
| `StopPlan` | stop levels + derivation | `reference_price(+source), initial_stop_price, current_stop_price, stop_method, atr_value, atr_multiple, structure_level?, buffer?, risk_per_share, reason_code, rationale, session_date` |
| `RiskDecision` | 3-valued sizing decision | `status(APPROVE/RESIZE/REJECT), requested_/approved_shares, requested_/approved_risk, risk_per_share, current_/projected_portfolio_risk, projected_heat, gross_/net_exposure, remaining_capacity, constraints_triggered, decision_id` |
| `OrderIntent` | execution intent | `requested_/approved_shares, signal_price, expected_execution_type, expected_execution_price?, stop_price, order_reason, risk_decision_id, order_id` |
| `PositionState` | open-position view | `shares, entry_fill_price, entry_session, atr_at_entry, initial_stop_price, current_stop_price, highest_/lowest_price_since_entry?, take_profit_price?` |

All models: stdlib `dataclass` (frozen) · `validate()` deterministic · `as_dict()`
serialisable · `Provenance` attached · fail loud with `ContractError(contract, field, detail)`.

### Units & session semantics (architecture decision 10)

| Quantity | Unit |
|---|---|
| prices, ATR, `risk_per_share` (1R) | USD / share |
| `atr_multiple`, `trailing_trigger_r`, `projected_heat` | dimensionless |
| total risk (`approved_risk`, portfolio risk) | USD |
| shares | integer shares |
| `session_date` / `*_session` | `YYYY-MM-DD`, naive US trading session |

Price semantics are kept separate **on purpose**:

* `signal_price` — signal-bar close (session D). Not an execution price.
* `expected_execution_price` — pre-trade expectation; **may be None** (next-open unknown at signal time).
* `entry_fill_price` / fill price — exists only after execution.
* `reference_price` (+ `reference_price_source` ∈ `FILL|EXPECTED|SIGNAL`) — the price the *stop* was computed from. Real initial risk uses `FILL`; `EXPECTED`/`SIGNAL` are pre-trade previews and must be labelled.

---

## 3. Stop Engine (`production/stops/`, Phase 1)

| File | Responsibility |
|---|---|
| `initial.py` | ATR initial stop (legacy formula, unchanged) |
| `trailing.py` | ATR trailing candidate + trigger gate (legacy formula, unchanged) |
| `engine.py` | `initial_stop_plan`, `update_stop` (no-loosening), `plan_from_position`, `StopUpdate.explain()` |
| `errors.py` | `StopEngineError(reason_code, detail)` |
| `__init__.py` | public surface + `StopPlan` re-export |

### 3.1 Initial stop (unchanged from baseline)

```
LONG  : initial_stop = reference_price - ATR × atr_multiple
SHORT : initial_stop = reference_price + ATR × atr_multiple      (mirror)
```

Legacy source (reproduced, **not modified**): `src/agents/risk_manager.py::size_position`
(`stop_distance = atr × cfg.stop_atr_mult`, `stop_price = price - stop_distance` for LONG).
`initial_stop_price` defines 1R and therefore sizing.

### 3.2 Trailing stop (unchanged from baseline)

```
r_dist      = atr_at_entry × stop_atr_multiple          <- the 1R multiple, NOT the trailing one
armed       = anchor >= entry_fill + trigger_r × r_dist           (LONG)
              anchor <= entry_fill - trigger_r × r_dist           (SHORT)
candidate   = anchor - trailing_atr_multiple × atr_at_entry       (LONG)
              anchor + trailing_atr_multiple × atr_at_entry       (SHORT)
anchor      = highest_price_since_entry (LONG) / lowest_price_since_entry (SHORT)
```

Legacy source (reproduced, **not modified**): `src/portfolio/portfolio_manager.py::_exit_check`
trailing block (`highest_since_entry`, `cfg.trailing_trigger_r`, `cfg.trailing_atr_mult`).

The trigger deliberately uses the **stop** ATR multiple because that is what the
existing baseline does. The parity test uses a duck-typed config with
`stop_atr_mult=1.5` **but** `trailing_atr_mult=0.8`, so swapping the two
multiples would fail the test.

### 3.3 No-loosening rule

```
LONG  : updated_stop >= previous_stop        SHORT : updated_stop <= previous_stop
```

A candidate that would loosen is rejected → `STOP_UNCHANGED` with an explicit
detail; it is never silently applied.

### 3.4 Reason codes (closed vocabulary, `production/contracts/reason_codes.py`)

| Code | Meaning |
|---|---|
| `INITIAL_ATR` | initial stop produced from the ATR formula |
| `NEW_HIGH` | stop improved **because the anchor advanced** (new high / new low) |
| `TRAILING_ATR` | stop improved by the trailing formula without a new anchor extreme |
| `STOP_UNCHANGED` | armed evaluation produced no valid improvement (incl. a loosening candidate) |
| `TRAILING_NOT_ARMED` | trigger distance not reached yet |
| `STRUCTURE_STOP` · `STRUCTURE_BREAK` · `STRUCTURE_LEVEL_MISSING` | **reserved** — structure stops are not implemented |
| `ATR_UNAVAILABLE` · `INVALID_ATR` · `INVALID_MULTIPLE` · `INVALID_PRICE` · `INVALID_DIRECTION` | fail-loud errors |

`StopUpdate.explain()` answers "why did the stop go from X to Y?" in one line.

---

## 4. Legacy parity — how "no behaviour change" is proven

| Layer | Legacy function (untouched) | Parity test | Compared value |
|---|---|---|---|
| initial stop | `src/agents/risk_manager.py::size_position` | `test_legacy_parity_initial_stop` | `stop_price`, `stop_distance` (1R) over 4 price/ATR cases |
| trailing stop | `src/portfolio/portfolio_manager.py::_exit_check` | `test_legacy_parity_trailing` | `exit_price` of the `TRAILING` fill over 4 cases + the not-armed → `None` gate |
| end-to-end | both of the above | `test_legacy_parity_engine_end_to_end` | legacy initial stop vs `StopPlan`, legacy trailing fill vs `updated_stop` |

Tolerance 1e-9 (the formulas are identical expressions, so results match to
floating-point identity in practice).

Gap-aware **exit fill behaviour** (open ≤ stop → GAP fill) stays in the legacy
exit path; the Stop Engine only owns the levels, so `_exit_check` is untouched.

---

## 5. SHORT mirror status

`LONG` / `SHORT` are both first-class in the contracts and the Stop Engine
(`direction_sign`), with mirrored validation (stop above price, anchor = lowest,
no upward loosening) and mirrored tests. **Production remains LONG-only**: no
live entry logic, no pipeline change, no short trading is enabled. The legacy
baseline has no SHORT counterpart, so SHORT is verified by symmetry invariants,
not by legacy parity (stated explicitly in the test file).

---

## 6. Explicitly NOT done in Phase 1

* ❌ structure-based stop (no canonical implementation exists — deferred to a future architecture decision)
* ❌ `current_stop_price` persistence (`src/state/state.py` and the production position schema untouched)
* ❌ Entry Engine / Risk Engine / Portfolio Engine logic
* ❌ Exit Engine rewrite
* ❌ production wiring or cut-over (isolation is asserted by `test_phase1_isolation_not_wired`)
* ❌ any change to frozen Regime v1 / Setup v1, thresholds, or config values

---

## 7. Running the tests

```bash
PY=~/.../wbprog/bin/python         # repo venv
$PY production/tests/test_contracts.py    # 18 tests — contracts
$PY production/tests/test_stops.py        # 19 tests — stop engine + legacy parity
```

Both are plain-script runners (`__main__`), deterministic and isolated, so they
can be migrated to pytest later without rewriting assertions.

---

## 8. Review checklist for the human

1. Are the five contracts sufficient for the Entry/Risk phases (fields, units, semantics)?
2. Is `reference_price_source` the right way to keep expected vs actual fills apart?
3. Is the reason-code vocabulary expressive enough without duplication?
4. Accept the deferred items in `STOP_ENGINE_BACKLOG.md` (structure stop, persistence, wiring)?
5. Approve Phase 2 (wiring plan) — or request changes here first.
