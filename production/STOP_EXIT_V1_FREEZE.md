# STOP_EXIT_V1_FREEZE.md — frozen Stop + Exit contract (Phase 3)

**Status:** FROZEN (2026-09-30) — Stop Engine v1 + Exit Engine v1 + the Phase-3
wiring flag.

**Scope.** This document freezes the contracts, the stop semantics, the exit
semantics and the production/persistence boundaries of the Stop Engine and the
Exit Engine. It introduces **no new trading rules** of its own; it describes
what is already implemented and parity-verified.

**Freeze rule.** Changing anything below requires an explicit new architecture
decision. Optimising stop/exit parameters is explicitly OUT of scope and must go
through a separate research phase (see `production/stops/STOP_ENGINE_BACKLOG.md`
and `production/exits/PHASE2_REVIEW_SHEET.md`).

---

## 1. Contracts

Owned by `production/contracts/` (stdlib dataclasses + explicit `validate()`;
no pydantic, no new dependencies).

| Contract | Module | Role |
|---|---|---|
| `EntryDecision` | `contracts/entry.py` | approved + signal/execution split (carries NO shares) |
| `StopPlan` | `contracts/stop.py` | the stop LEVEL (initial + current), method, reason |
| `RiskDecision` | `contracts/risk.py` | APPROVE / RESIZE / REJECT (never a bare boolean) |
| `OrderIntent` | `contracts/order.py` | signal / execution / risk kept separate |
| `PositionState` | `contracts/position.py` | the Stop Engine's view of an open position |
| `ExitDecision` | `contracts/exit.py` | should_exit / why / modelled price / fill model |
| `PriceBar` | `contracts/base.py` | the OHLC bar an exit is evaluated against |

**Units.** prices and ATR: USD per share · `atr_multiple`: dimensionless ·
`risk_per_share`: USD per share · risk/amounts: USD · shares: integer ·
`session_date`: naive US trading-session date `YYYY-MM-DD` (no timezone).

**Price semantics (never conflated).** `signal_price` = the signal bar (D) close;
`expected_execution_price` = a pre-trade reference and may be unknown (`None`);
`fill_price` exists only after an actual fill; `reference_price` is whatever the
stop was measured from and MUST be labelled by `reference_price_source`
(`FILL` | `EXPECTED` | `SIGNAL`).

**Reason codes:** a controlled vocabulary in `contracts/reason_codes.py`.

---

## 2. Stop semantics

**Ownership.** The Stop Engine (`production/stops/`) is the ONLY owner of stop
levels. It answers *what the level is and why*, never *whether to exit*.

* **Initial stop** (`INITIAL_ATR`): LONG `reference_price − ATR × stop_atr_mult`;
  SHORT `reference_price + ATR × stop_atr_mult`. `initial_stop_price` defines 1R.
* **Trailing** (ATR-based): the trigger distance uses `stop_atr_mult`
  (`r_dist = atr_at_entry × stop_atr_mult`); the stop is **armed** once the
  anchor reaches `entry_fill_price ± trailing_trigger_r × r_dist`; the candidate
  level is `anchor ∓ trailing_atr_mult × atr_at_entry`.
* **Anchors.** LONG anchor = `highest_since_entry`; SHORT anchor =
  `lowest_since_entry`.
* **No loosening.** LONG `updated >= previous`; SHORT `updated <= previous`.
  A non-tightening evaluation returns `STOP_UNCHANGED` and the level is kept.
* **LONG / SHORT mirror.** SHORT is a true mirror, not a direction string. It is
  implemented and unit-tested but **production remains LONG-only**; SHORT trading
  is NOT enabled by this freeze.
* **Deterministic & explainable.** No clock, no randomness; every update carries
  a reason code and a human-readable `detail` (`StopUpdate.explain()`).
* **Structure stops are NOT part of v1.** `structure_level` is an optional,
  unused field; `STRUCTURE_*` reason codes are reserved for a future decision.
* Stop calculation reason codes: `INITIAL_ATR`, `TRAILING_ATR`, `NEW_HIGH`,
  `STOP_UNCHANGED`, `TRAILING_NOT_ARMED`.

---

## 3. Exit semantics

**Ownership.** The Exit Engine (`production/exits/`) answers exactly four
questions: should we exit? why? at what modelled price? which fill model? It does
NOT size, send orders, execute, mutate portfolio state, call `technicals_agent`,
or calculate a trailing stop.

**Exact priority (evaluated top-down, first match wins):**

```
1. STOP_LOSS        -> fills at the bar OPEN when the open breaches the stop (GAP),
                       otherwise at the stop level (STOP)
2. TAKE_PROFIT      -> fills exactly at the target level (TARGET)
3. TRAILING_STOP    -> armed only; fills at the bar OPEN when the open breaches the
                       trailing level (GAP), otherwise at the trailing level (TRAILING)
4. TIME_STOP        -> fills at the bar CLOSE
5. SIGNAL_EXIT      -> fills at the bar CLOSE
```

If stop and target both trigger on the same bar, **STOP_LOSS wins**. There is
deliberately NO intrabar path inference.

**Exact modelled fill price (no "reasonable range" rule):**

| fill model | contract equality |
|---|---|
| `GAP` | `exit_price == bar.open` |
| `STOP` / `TRAILING` | `exit_price == stop_reference_price` |
| `TARGET` | `exit_price == target_reference_price` |
| `CLOSE` | `exit_price == bar.close` |

**Stop-level mapping (single StopPlan, zero recomputation):**

```
STOP_LOSS      -> StopPlan.initial_stop_price      (the static protective stop)
TRAILING_STOP  -> StopPlan.current_stop_price      (the Stop Engine's live level)
```

**Trigger / trailing arming.** The Exit Engine only reads the arming gate from
the Stop Engine (`ExitContext.trailing_armed`); the candidate level returned by
that call is discarded. `StopPlan.current_stop_price` is the sole authoritative
trailing level. **The Exit Engine MUST NOT independently recalculate a trailing
stop, and contains no ATR/trailing arithmetic** (enforced by tests).

**Holding limit.** `TIME_STOP` uses **calendar** days between `entry_session` and
the decision session (`held_days`), exactly as the legacy oracle did. It is NOT
converted to trading days.

**Technical signal.** `tech_signal` is **injected by the caller** (the pipeline);
the Exit Engine never imports or calls the technicals agent.

**Oracle.** `src/portfolio/portfolio_manager.py::_exit_check` is the behavioural
oracle for v1. The Exit Engine must reproduce its reason codes, fill models and
prices exactly; legacy code is NOT modified and NOT delegated to.

---

## 4. Production boundary

```
exit_engine_mode = "legacy" | "shadow" | "new"      (default: "legacy")
```

* **`legacy` (default, frozen as the production mode):** the legacy oracle is the
  only decision source. The new engine packages are not even imported.
* **`shadow`:** the legacy oracle remains **authoritative** — it decides BUY,
  SELL, position, cash, sizing and state exactly as before. The new Stop + Exit
  Engine is evaluated, compared and recorded **only** inside
  `DecisionRecord.exits.shadow` (+ `exits.shadow_summary`). Shadow failures are
  **fail-open**: an exception is recorded and ignored, and the legacy decision
  continues. Shadow has **no trading side effects** and cannot change any output.
* **`new`:** the `ExitDecision` drives the production action. Implemented and
  tested, but **must not be enabled** without explicit human approval.

Wiring is reached only through `production/exits/adapter.py` (translation +
validation) and `production/exits/shadow.py` (comparison). `production/pipeline.py`
is the only production module allowed to touch them, and only through lazy
imports.

**Rollback.** Setting `exit_engine_mode = "legacy"` restores previous behaviour
immediately — no code revert required.

---

## 5. Persistence boundary

**`current_stop_price` is NOT persisted by Phase 3.**

* `src/state/state.py` and the production position schema are **untouched**; the
  position dict has no stop-state column.
* The Stop Engine computes and *returns* the current stop; the adapter copies it
  into `PositionState` / `StopPlan` for the duration of one evaluation only.
* Shadow mode writes diagnostics to the DecisionRecord, never to state.
* Persisting `current_stop_price` (plus its reason code and session) requires a
  separate Position / Ledger / State redesign decision.

---

## 6. Explicitly OUT of scope for this freeze

Stop/exit parameter optimisation · structure stops · SHORT trading enablement ·
Risk / Entry / Portfolio engine implementation · production cut-over to
`new` · legacy deletion · state persistence redesign · any change to Regime v1 or
Setup v1.
