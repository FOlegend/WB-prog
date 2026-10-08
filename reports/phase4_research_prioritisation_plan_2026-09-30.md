# Phase 4 — Research Prioritisation Plan (PLAN ONLY, not implemented)

**Date:** 2026-09-30 · **Status:** awaiting Phase-3 sign-off. **Nothing in this document has been implemented.**

Phase-3 spec §22 forbids starting the Risk / Entry / Portfolio Engine (or optimising stop/exit
parameters) before human review. This document therefore contains **no code and no parameter
changes** — it is the decision procedure for choosing the next research target, as the spec's own
closing instruction asks:

> "After this is complete, we will use the resulting end-to-end backtest to decide scientifically
> whether the next research target should be Entry, Risk, Exit, Portfolio construction, or
> something else."

---

## 1. Two independent threads

| Thread | What it is | Blocked by |
|---|---|---|
| **A. Close the live shadow gate** | run the daily briefing in `shadow` until 20 consecutive qualifying sessions with 0 divergence | CLI has no way to select the mode (see `PHASE3_REVIEW_SHEET.md` §1.5) — needs your decision |
| **B. Choose the next research target** | a read-only diagnostic pack on the frozen end-to-end backtest, then a ranked decision | Phase-3 sign-off |

Thread A is operational and cheap. Thread B is where the value is, and it must come **before** any
engine work so that we do not optimise the wrong component.

## 2. The decision to make

Which single target explains most of the *controllable* gap between the frozen system and a
buy-and-hold benchmark, in this order of investigation:

```
Entry  ·  Risk / sizing  ·  Exit  ·  Portfolio construction  ·  (something else)
```

## 3. Diagnostic pack (read-only, deterministic, existing artifacts only)

Every item below can be computed from artifacts that **already exist**: the trade log
(`reports/production_bt_2024-01-01_2025-07-31.json`), the coverage report
(`reports/phase3_shadow_coverage_2026-09-30.json`), the 19 monthly screen buckets, the per-session
`equity_curve` / `regime_log`, and the DecisionRecords the backtest already produces. No frozen
component is touched; no parameter is changed.

| # | Diagnostic | Question it answers | Feeds |
|---|---|---|---|
| **D1** | **Exit attribution** — exit reason × realised R multiple; per-trade MFE/MAE; profit given back after the peak | Is the loss in the stop, the target, the time stop, or in *giving back* open profit? | Exit vs Entry |
| **D2** | **Signal → fill funnel** — screened 30 → setup valid → risk-allowed → queued → filled, with the skip reasons (`gap`, `extension`, `not in bucket`, `no data`) | Do we lose more *before* entry (filtering) or *after* entry? | Entry |
| **D3** | **Quality/efficiency of fills** — entry-day gap, extension from pivot, setup score, quality multiplier bucket vs outcome | Is the sizing/quality multiplier adding information or noise? | Risk / Entry |
| **D4** | **Risk & sizing** — realised risk per trade vs `risk_per_trade=1%`; R-multiple histogram; consecutive-loss behaviour; how often `max_position_pct` binds | Is the per-trade risk the binding constraint, or is it position count? | Risk |
| **D5** | **Exposure & binding constraints** — % of sessions with 0/1/2… positions; `max_open_positions` binding days; regime `position_size_mult` distribution and cash%; blocked-entry opportunity cost | Is the real constraint **exposure** rather than decision quality? | Portfolio |
| **D6** | **Benchmark gap, exposure-adjusted** — return vs SPY **at matched average exposure**, plus Sharpe/MaxDD and the same metrics restricted to the sessions the system was actually invested | How much of the gap is simply "we were often flat in a bull market"? | Portfolio / Regime |

## 4. What the existing evidence already suggests (to be confirmed, not assumed)

These are observations from the Phase-3 artifacts, **not conclusions**:

* Exit mix over 151 trades: **STOP_LOSS 75 / TAKE_PROFIT 60 / TRAILING_STOP 10 / TIME_STOP 6**.
  The stop is the single largest bucket, and the time stop barely fires — so the "we hold too long"
  hypothesis is *not* obviously supported, while "the initial stop is too tight" and "targets are
  hit but too small" are both live.
* Shadow coverage shows **396 sessions of which only 280 had any held position** → 116 sessions
  (29%) with **no position at all**. Verified against the coverage data: of those 116 sessions,
  **116 had `n_held == 0`** (genuinely flat at the close) and **0** were "held but unevaluated", so
  this is not a data artefact. Separately, the maximum concurrent holdings reached **5 = the
  `max_open_positions` cap**, so the budget does bind at least sometimes.
  → If D5 attributes the flat sessions to regime/exposure rather than to a lack of qualifying
  setups, the binding constraint is *portfolio/exposure*, and optimising exits or entries would
  barely move the result.
* The window (2024-01→2025-07) was a strong bull market, so D6 is essential: a large part of the
  gap to SPY may be structural (deliberate de-risking) rather than a defect.

**Consequence:** the naive answer "improve the exits" is not yet supported by evidence, and D5/D6
must be run before any engine is chosen.

## 5. Ranking rule (fixed in advance, to avoid p-hacking)

After the pack runs, rank the candidates by:

1. **Attributable, controllable loss** — the share of the exposure-adjusted gap the diagnostic can
   attribute to that component (largest first).
2. **Interface cost** — prefer the target that touches the fewest frozen interfaces
   (`ENTRY` touches Setup v1 + Screener; `RISK` touches sizing/portfolio; `EXIT` touches the Stop or
   Exit freeze face; `PORTFOLIO` touches `max_open_positions` + regime consumption).
3. **Falsifiability** — prefer the target whose hypothesis can be killed by a single deterministic
   backtest comparison.

A component is only promoted to implementation if the diagnostic can state its hypothesis in **one
sentence with a measurable success criterion**, and if that hypothesis respects the existing freeze
contracts (any change to a frozen interface requires a new architecture decision first).

## 6. Non-goals for Phase 4 (unchanged from Phase 3 §22)

No parameter optimisation · no new indicators / setups / ML / LLM · no production cut-over ·
no legacy deletion · no state-persistence redesign · no `exit_engine_mode = "new"` without explicit
approval · no Risk/Entry/Portfolio Engine implementation until the ranking above is signed off.

## 7. What I need from you to proceed

1. **Phase-3 sign-off** (the 6 items in `production/exits/PHASE3_REVIEW_SHEET.md`).
2. **Decision on the CLI mode flag** (`§1.5` of that sheet) so Thread A can start.
3. **Approval to run the D1–D6 diagnostic pack** (read-only; expected cost ≈ one backtest-equivalent
   compute pass plus analysis; no repository behaviour changes; deliverable = one report with a
   ranked table and its evidence).

Once (3) is approved I will produce
`reports/phase4_diagnostics_<date>.md` — a ranked table plus the supporting numbers, and nothing else.
