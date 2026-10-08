# Stop Engine — Backlog (Phase 1 output)

Items deliberately **not** implemented in Phase 0-1. Each needs an explicit
architecture decision before code (control_center AGENT_WORKFLOW: strategy ideas
go to the backlog, not into code).

Source: human decisions 2026-09-28 (Phase 0-1 kickoff, items 2, 3, 7).

---

## B1 — Structure-based stop (DEFERRED, needs definition)

* Status: **not implemented**. `StopPlan.structure_level` exists as an optional
  field only; `STRUCTURE_STOP` / `STRUCTURE_BREAK` / `STRUCTURE_LEVEL_MISSING`
  are reserved reason codes.
* Reason: the repository has **no reliable canonical structure-stop
  implementation** to extract. Candidate notions (prior swing low, 20-day pivot
  low, N-bar low) are new trading logic, so they must not be invented here.
* Needs: an architecture decision defining the structure level (definition,
  lookback, buffer, interaction with the ATR stop — tighter-of / wider-of /
  switch-over rule), plus parity/replay evidence.
* Notes: the breakout path uses `prior_high20` as an **entry pivot**, which is
  not a stop level — do not reuse it as one without a decision.

## B2 — `current_stop_price` persistence (DEFERRED)

* Status: computed and returned by the Stop Engine, **never persisted**.
* Reason: `src/state/state.py` and the production position schema are on the
  frozen production path (Phase 1 must not change behaviour). Today the trailing
  stop is re-derived on every `_exit_check` call from `highest_since_entry`.
* Needs: a Position / Ledger / State redesign decision, including
  * whether `current_stop_price` + `stop_reason_code` + `stop_updated_session`
    become part of the persisted position,
  * migration of existing state files,
  * audit fields for "why did the stop change on this date".
* Note: `PositionState` (contracts) already models the target shape.

## B3 — Production wiring (DEFERRED to a later phase)

* Status: `production/stops/` is **not imported** by the production pipeline;
  `test_phase1_isolation_not_wired` asserts this.
* Needs: a freeze decision for the Stop Engine face (like Regime v1 / Setup v1)
  **before** any wiring, then a controlled single-variable change with a
  production-equivalent backtest comparison.

## B4 — Legacy low-level layer decision (OPEN, owned by Control Center)

* The production path still imports `src/agents/risk_manager.py`,
  `src/portfolio/portfolio_manager.py`, `src/state/state.py`
  (Control Center decision `[HIGH] OPEN`).
* Phase 1 decision: **do not modify them and do not delegate them to the new
  engine** — parity verification ranks above refactoring.
* Follow-up question for the human: after Phase 2 wiring, should these become a
  shared low-level layer (relabelled) or be migrated onto `production/`?

## B5 — SHORT enablement (DEFERRED)

* Status: contracts + Stop Engine support SHORT symmetrically and are tested;
  production is LONG-only.
* Needs: a decision covering entry logic, borrow/availability, and backtest
  support before any SHORT trading is enabled.

## B6 — `buffer` semantics (RESERVED)

* `atr_initial_stop(buffer=...)` supports an extra protective distance, default
  `None` (== legacy behaviour). No strategy uses it; parity tests cover
  `buffer=None` only. Decide semantics before use (absolute USD vs ATR fraction).

## B7 — Exit Engine separation (NEXT PHASE CANDIDATE)

* The Stop Engine owns **levels**; deciding to exit is still done by
  `_exit_check` (STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / TIME_STOP /
  SIGNAL_EXIT, gap-aware fills).
* Recommended next phase: define the Exit Engine contract and make it consume
  `StopPlan` + `PositionState`, with the same parity discipline
  (no behaviour change, prove equivalence against `_exit_check` first).

---

## Migration note (for the Control Center Migration Board)

| Field | Value |
|---|---|
| subsystem | Stop Engine |
| old | derived inline: `risk_manager.size_position` (initial) + `portfolio_manager._exit_check` (trailing) |
| new | `production/stops/` + `production/contracts/` (isolated, Phase 1) |
| status | IMPLEMENTED — NOT WIRED (parity verified, behaviour unchanged) |
| blocker | human review of the contracts + reason-code vocabulary |
| evidence | `production/stops/DESIGN_NOTE.md`; `production/tests/test_stops.py` |
