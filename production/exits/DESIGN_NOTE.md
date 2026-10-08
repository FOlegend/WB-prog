# Exit Engine — Design Note (Phase 2)

**Status:** Phase 2 complete — isolated implementation + parity verified, **not wired** into production.
**Date:** 2026-09-29 · **Scope:** ExitDecision contract + Exit Engine only.
**Oracle:** `src/portfolio/portfolio_manager.py::_exit_check` (untouched).

---

## 1. Responsibility split

```
Setup -> Entry Decision -> Stop Plan -> Risk Decision -> Order Intent -> Execution
                                  |
Position -> Stop Engine (levels) -> Exit Engine (decision) -> (future) execution
```

| Engine | Question | Phase status |
|---|---|---|
| Stop Engine | "Where is the stop, and why?" | implemented (Phase 1, isolated) |
| **Exit Engine** | **"Should we exit, why, at what modelled price, which fill model?"** | **implemented (Phase 2, isolated)** |
| Risk / Entry Engine | sizing / wanting the trade | contracts only |

The Exit Engine does **not**: size positions, compute shares, send or execute orders,
modify portfolio state, call `technicals_agent`, or calculate a trailing stop.

---

## 2. `ExitDecision` contract (`production/contracts/exit.py`)

| Field | Type | Notes |
|---|---|---|
| `should_exit` | bool | required |
| `ticker`, `direction`, `session_date` | str | naive US session date |
| `exit_reason_code` | str \| None | **legacy vocabulary**: STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / TIME_STOP / SIGNAL_EXIT |
| `exit_price` | float \| None | USD/share |
| `fill_model` | str \| None | **legacy vocabulary**: STOP / GAP / TARGET / TRAILING / CLOSE |
| `stop_reference_price` | float \| None | the stop level that caused the decision (audit) |
| `target_reference_price` | float \| None | the target level (audit) |
| `detail`, `bar`, `provenance` | str / PriceBar / Provenance | audit |

**Exact fill semantics — no "reasonable range" rules** (Phase 2 validation correction):

| fill_model | required equality |
|---|---|
| `GAP` | `exit_price == bar.open` |
| `STOP` | `exit_price == stop_reference_price` |
| `TRAILING` | `exit_price == stop_reference_price` |
| `TARGET` | `exit_price == target_reference_price` |
| `CLOSE` | `exit_price == bar.close` |

plus reason↔fill coherence (STOP_LOSS ∈ {GAP, STOP}, TAKE_PROFIT = TARGET,
TRAILING_STOP ∈ {GAP, TRAILING}, TIME_STOP/SIGNAL_EXIT = CLOSE) and
"`should_exit=False` carries no execution fields".

Deliberate, documented input tightening: `PriceBar` requires a valid OHLC
(open/close within [low, high]) — the legacy code tolerated a missing close by
falling back to the entry price. Invalid input now fails loud instead of being
silently defaulted; on valid inputs the behaviour is identical.

---

## 3. `ExitContext` (`production/exits/context.py`)

Minimum complete, deterministic input:

| Input | Source |
|---|---|
| `position: PositionState` | position facts incl. `entry_session`, `anchor_price`, `take_profit_price` |
| `stop_plan: StopPlan` | **the authoritative stop levels (Stop Engine)** |
| `bar: PriceBar` | the session's OHLC |
| `target_reference_price` | defaults from `position.take_profit_price` (mismatch = error) |
| `tech_signal` | **injected by the caller** (never computed here) |
| `session_date` | naive session date |
| `trailing_armed: bool` | **produced by the Stop Engine** |
| `config: ExitConfigSnapshot(max_holding_days)` | only the parameter the rules need |

**Hard architecture rule — one owner for stops.** `context.py` is the only place
that talks to the Stop Engine, and it does so purely to read the *arming gate*:

```python
evaluation = atr_trailing_stop(...)     # Stop Engine owns the formula
trailing_armed = evaluation.armed       # candidate stop is DISCARDED
```

The trailing level used by the rules is `StopPlan.current_stop_price`.
`test_no_trailing_math_in_exit_engine` asserts that `rules.py` / `engine.py`
contain no ATR or trailing arithmetic.

---

## 4. Rules and priority (`production/exits/rules.py`, `engine.py`)

| # | Rule | Level used | LONG | SHORT (mirror) |
|---|---|---|---|---|
| 1 | `STOP_LOSS` | `stop_plan.initial_stop_price` (static) | `open ≤ stop` → GAP; `low ≤ stop` → STOP | `open ≥ stop` → GAP; `high ≥ stop` → STOP |
| 2 | `TAKE_PROFIT` | `target_reference_price` | `high ≥ target` → TARGET | `low ≤ target` → TARGET |
| 3 | `TRAILING_STOP` | `stop_plan.current_stop_price` (gated by `trailing_armed`) | `open ≤ trail` → GAP; `low ≤ trail` → TRAILING | `open ≥ trail` → GAP; `high ≥ trail` → TRAILING |
| 4 | `TIME_STOP` | — | `held_days ≥ max_holding_days` → CLOSE | same |
| 5 | `SIGNAL_EXIT` | — | `tech_signal == "bearish"` → CLOSE | same |

* Priority is the evaluation order; **stop wins over target on the same bar** —
  no intrabar path is inferred (matches legacy).
* `held_days` = **calendar days** (`datetime` difference), not trading days.
* `tech_signal` is injected; the agent is never called internally.

---

## 5. Parity evidence

| Level | Evidence |
|---|---|
| 17 required synthetic cases | `production/tests/test_exit_engine.py` — four-field comparison (should_exit / reason / price / fill_model) vs the legacy oracle, using a **discriminating config** (`stop_atr_mult=1.5` vs `trailing_atr_mult=0.8`) so a multiple mix-up fails |
| Real production config | all 17 fixtures re-run with `ProductionConfig` (1.5 / 1.5 / 1.0 / 30) — parity holds |
| Trade-level replay | `production/tests/replay_exit_parity.py` — 151 real trades / **1073 bars**: 0 divergences, identical reason distribution, 151/151 first-exit reconciliation with the recorded trade log |

---

## 6. Stop-level mapping (needs human confirmation)

The legacy position carries **two** stop notions: `pos["stop_price"]` (static,
never updated) and a separately derived trailing level. Phase 2 maps them onto
the single `StopPlan`:

* `STOP_LOSS` → `initial_stop_price` (the static protective stop)
* `TRAILING_STOP` → `current_stop_price` (the Stop Engine's live level)

Both are fields of the plan produced by the Stop Engine, so the Exit Engine
still recalculates nothing — but the interpretation is worth an explicit
sign-off (recorded as an OPEN decision in the Control Center).

---

## 7. Isolation & not-done

* Nothing in the production path imports `production.exits` / `production.stops`
  / `production.contracts` (`test_phase2_isolation_not_wired`); registry shows
  `production_used = 0` for all 8 Phase-2 modules.
* Not done: production wiring/cut-over, exit-parameter research, SHORT
  enablement, `current_stop_price` persistence, Risk/Entry/Portfolio engines.

---

## 8. Running the tests

```bash
PY=~/.../wbprog/bin/python
$PY production/tests/test_exit_engine.py       # 31 tests (17 parity cases)
$PY production/tests/replay_exit_parity.py     # real trades: 1073 bars, 0 divergences
```

## 9. Review checklist

1. Accept the `ExitDecision` field set (incl. the two reference prices)?
2. Accept the exact fill-price rules (no range heuristics)?
3. Confirm the stop-level mapping in §6.
4. Accept the documented input tightening (no silent close fallback)?
5. Approve the combined Stop + Exit wiring plan as the next step (or request changes).
