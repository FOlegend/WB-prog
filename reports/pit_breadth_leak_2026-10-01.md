# CRITICAL FINDING — Regime v1's breadth engine is not point-in-time in historical replay

**Date:** 2026-10-01 · **Method:** read-only audit (`research/pit_breadth_audit.py`) · **No file was modified.**
**Status:** `NEEDS HUMAN REVIEW` — production fix deliberately **not** applied.

---

## 0. The finding in one paragraph

`production/pipeline.py::run_daily` loads the breadth series with
`load_breadth(cfg, end=as_of)`, intending to get a series that ends at the as-of date. But
`regime_dual_engine/pit_breadth_data.py::get_breadth()` returns the **whole cached CSV** whenever the cache
exists — the `end` argument is only used when *rebuilding*:

```python
def get_breadth(kind, rebuild=False, end="2025-07-31"):
    path = _PIT_OUT if kind == "pit" else _CUR_OUT
    if os.path.exists(path) and not rebuild:
        df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
        return df                      # <-- `end` IGNORED; the full 2016..2025 series
```

`regime_dual_engine/engine.py::compute_regime_decision()` then reads
`breadth_df["pct_above_50dma"].iloc[-1]` (and the trailing percentile window) — i.e. **the tail of the
full series**. Consequently, in **every historical replay**, Engine B evaluates the **2025-07-31** row
regardless of the as-of date. **50 % of the frozen Regime v1 composite is not point-in-time.**

Engine A (HMM) is **not** affected — it consumes the point-in-time SPY slice from the DataSource.

---

## 1. Evidence A — the breadth diagnostics are constant across as-of dates

`research/pit_breadth_audit.py` PART 1:

| as_of | shipped rows | shipped pct | shipped now | shipped label | **PIT rows** | **PIT pct** | **PIT now** | **PIT label** |
|---|---:|---:|---:|---|---:|---:|---:|---|
| 2018-06-29 | 2408 | **41.746** | **57.23** | BEAR | 628 | 27.738 | 52.87 | BEAR |
| 2020-03-31 | 2408 | **41.746** | **57.23** | BEAR | 1068 | 11.786 | 3.88 | BEAR |
| 2022-06-30 | 2408 | **41.746** | **57.23** | BEAR | 1635 | 17.063 | 15.70 | **BULL** |
| 2024-01-31 | 2408 | **41.746** | **57.23** | SIDEWAYS | 2033 | 55.992 | 64.38 | SIDEWAYS |
| 2024-06-28 | 2408 | **41.746** | **57.23** | SIDEWAYS | 2136 | 34.365 | 48.97 | SIDEWAYS |
| 2025-01-31 | 2408 | **41.746** | **57.23** | BEAR | 2284 | 31.468 | 51.74 | BEAR |
| 2025-07-31 | 2408 | **41.746** | **57.23** | BULL | 2408 | **41.746** | **57.23** | BULL |

Two decisive readings:

* The shipped breadth inputs are **bit-identical for every date** (percentile 41.746, breadth_now 57.23)
  while the PIT-correct values range **11.79 → 55.99**.
* At **2025-07-31** shipped and PIT **coincide exactly** — which is precisely what the mechanism predicts
  (the cache tail). That is the fingerprint of the bug, not a coincidence.
* The regime **label already differs** at 2022-06-30 (shipped BEAR vs PIT BULL), so this is not a
  cosmetic diagnostic-only difference.

---

## 2. Evidence B — end-to-end impact on the production-equivalent backtest

The audit re-ran the unchanged `ProductionBacktest` with `production.pipeline.load_breadth` patched
**in-process only** to return `series[series.index <= as_of]`. **No file was modified.**

### 2.1 Regime distribution changes materially

| | BEAR | SIDEWAYS | BULL |
|---|---:|---:|---:|
| shipped (leaky) | 185 | 160 | **51** |
| **PIT-corrected** | 136 | 174 | **86** |

BULL days **nearly double**; BEAR days fall by 26 %. The PIT regime is *less* defensive.

### 2.2 The baseline is materially worse than believed

| Metric | shipped (leaky) | **PIT-corrected (true)** | Δ |
|---|---:|---:|---:|
| Return | 7.99 % | **5.57 %** | **−2.42 pp** |
| CAGR | 5.01 % | 3.51 % | −1.50 pp |
| **MaxDD** | −5.98 % | **−10.39 %** | **−4.41 pp** |
| **Sharpe** | 0.935 | **0.513** | **−0.422** |
| Sortino | 1.141 | 0.557 | −0.584 |
| Calmar | 0.838 | 0.338 | −0.500 |
| Profit factor | 1.29 | 1.13 | −0.16 |
| avg R | 0.1822 | 0.1878 | +0.006 |
| Trades | 151 | 168 | +17 |
| Avg exposure | 17.16 % | 23.39 % | +6.2 pp |

Drawdown roughly **doubles**. The "drawdown control" proposition survives only in weaker form
(−10.39 % vs SPY −18.76 %, instead of −5.98 %), and risk-adjusted performance (Sharpe 0.513) is now clearly
inferior to SPY's 1.218 over the same window.

### 2.3 The R1 exit-target conclusion is **withdrawn**

| TP ATR | shipped return | **PIT-corrected return** | shipped Sharpe | **PIT Sharpe** | shipped MaxDD | **PIT MaxDD** |
|---:|---:|---:|---:|---:|---:|---:|
| 2.5 (baseline) | 7.99 % | **5.57 %** | 0.935 | **0.513** | −5.98 % | **−10.39 %** |
| 3.0 | 10.22 % | **−1.43 %** | 1.181 | **−0.084** | −5.52 % | −11.29 % |
| 3.5 | 11.17 % | **−0.41 %** | 1.242 | **0.005** | −4.13 % | −10.78 % |
| 4.0 | 14.13 % | **7.38 %** | 1.468 | **0.612** | −4.50 % | −10.70 % |

The clean monotone pattern (and the "12/12 sub-period cells favour TP ≥ 3.5" claim) **disappears**: with
correct PIT breadth the response is a non-monotone zig-zag (5.57 → **−1.43** → **−0.41** → 7.38) with two of
the four settings losing money outright. **The R1 signal was an artefact of the leaky regime.** The R1
recommendation in `return_improvement_research_2026-10-01.md` is **withdrawn**.

### 2.4 What is *not* invalidated

* **Stop/Exit parity and semantics** — `replay_exit_parity.py` compares two exit engines on identical
  entries; the leak does not enter that comparison. 151 trades / 1073 bars / 0 divergences still stands.
* **The Step-0 harness classification** (§11 gate) — also an engine-vs-engine comparison.
* **The qualitative bottleneck ordering** (exposure dominates, marginal capital has negative expectancy) —
  but the *magnitudes* were computed on a leaky equity curve and must be re-derived.
* **Phase 3 shadow coverage** (shadow vs legacy) — both arms consumed the same leaky regime, so the
  comparison itself is valid; only its performance context changes.

---

## 3. Why this is a "frozen constant", not classic peeking

It is worth being precise about the failure mode. Engine B does **not** see tomorrow's price path. It reads a
**single fixed row** (2025-07-31) plus a trailing window ending there. So its contribution to the composite is
a **constant offset** (½ × 41.746 = 20.87 points) and the divergence flag is a **constant state**, while
Engine A varies normally.

Two consequences:

1. The backtest effectively ran an **HMM-only regime with a fixed breadth offset** — not the frozen
   50/50 HMM + Breadth architecture that production claims to run. That alone makes every historical result
   **not a faithful replay of the frozen decision function** (violating the project's own central guarantee).
2. Because it is a fixed offset rather than day-varying knowledge, it is a **contract violation and a
   confounding artefact rather than a catastrophic information leak**: it cannot manufacture an edge
   day-by-day, but it *can* (and did) shift the regime distribution enough to change which trades are
   taken — as §2 shows.

---

## 4. Live-path implication (separate risk)

The same loader is used live. With the breadth cache ending **2025-07-31**, a live run on any later date
would silently use a **stale tail** — the ledger records only `breadth_rows`, never the *date* of the series
tail, so staleness is **invisible** in the DecisionRecord. This has not bitten yet (live shadow sessions = 0),
but it must be fixed before the daily shadow run starts accumulating the §10 gate.

---

## 5. What I did NOT do, and why

* I did **not** patch `get_breadth()` or `pipeline.py`. Both sit inside the frozen Regime v1 surface; a
  change there is a production behaviour change and needs an explicit architecture decision.
* I did **not** re-baseline the downstream reports' numbers in place; they are annotated instead, so the
  original (leaky) evidence remains auditable against this correction.
* I did **not** delete or rewrite any earlier report.

The repair itself is small and surgical — `get_breadth` should slice to `end` (or `run_daily` should slice the
frame before calling `compute_regime_decision`). The choice between those two is a human architecture decision.

---

## 6. Recommended action

1. **Fix the PIT contract** (human decision): slice the breadth series to `<= as_of` at the loader or the call
   site. Add a regression test asserting that two different as-of dates produce different breadth inputs.
2. **Re-baseline** — re-run Phase 4 (D1–D6), Phase 5 Step 0 and R1 against the PIT-correct regime, and
   restate the frozen reference values (the current "396 / 151 / +7.99 % / −5.98 % / 0.935" set is a
   **leaky** baseline).
3. **Withdraw the R1 recommendation** in `return_improvement_research_2026-10-01.md` (done — banner added).
4. **Add a staleness guard** for the live path: record the breadth series' last date in the DecisionRecord
   and fail loud if it is older than the as-of session.
5. **Check whether the frozen Regime v1 freeze document** assumed a PIT breadth input — if so, the freeze
   contract's *input precondition* was violated by the adapter, not by the decision maths.

---

## 7. NEEDS HUMAN REVIEW

| # | Question |
|---|---|
| 1 | Approve the PIT repair and choose **where** to slice (loader vs pipeline call site)? |
| 2 | Authorise the **re-baseline** of Phase 4 / Phase 5 / R1 against a PIT-correct regime? |
| 3 | Accept the **withdrawal of the R1 exit-target recommendation** until re-derived? |
| 4 | Should the live ledger be required to persist the breadth tail date (fail-loud on staleness)? |
| 5 | Does this change the Regime v1 freeze contract's stated input precondition (adapter vs maths)? |

Artifacts: `reports/pit_breadth_audit_2026-10-01.json`; runner `research/pit_breadth_audit.py`.
Registry: new `OPEN / HIGH` decision *"LOOK-AHEAD: Regime v1 Engine B (breadth) is NOT point-in-time in
historical replay"*.
