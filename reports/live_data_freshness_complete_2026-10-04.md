# Next Task — Resolve Live Data Lineage Before Promotion

## Mission

The live-data freshness investigation has reached an honest STATE B:

```text
LIVE DATA NOT READY
```

Do NOT promote the refreshed breadth data to production yet.

Do NOT enable live `BLOCK_DECISION` yet.

Do NOT change any strategy parameter.

The two unresolved data-integrity issues are:

1. OHLCV historical price-basis differences caused by corporate actions.
2. PIT constituent snapshots ending at 2026-06-30 while refreshed breadth reaches 2026-08-06.

The purpose of this task is to determine the safest architecture for a reliable live data stream without corrupting the frozen historical research dataset.

---

# 1. Strong Constraint

Do NOT rewrite the frozen historical research dataset.

Do NOT silently modify:

- existing OHLCV historical cache
- historical breadth values
- PIT research baseline
- regime mathematics
- strategy parameters
- Stop/Exit contracts

The current historical research dataset must remain reproducible.

---

# 2. Corporate-Action Investigation

Investigate the 87 OHLCV symbols whose current yfinance data differs from the existing cache.

For each difference classify:

- split
- dividend
- other corporate action
- genuine source/data discrepancy
- unknown

Do not rely on a few examples.

Quantify:

- number of symbols by cause
- magnitude of difference
- whether the difference is constant through history
- date of corporate action
- whether the action occurs before or after the current cache boundary
- whether the difference affects OHLC, close, volume or all fields

Create:

```text
reports/ohlcv_corporate_action_audit_YYYY-MM-DD.md
```

---

# 3. Identify the Existing Price Basis

Determine exactly what price basis the historical cache uses.

Answer:

- adjusted or unadjusted?
- how are splits represented?
- how are dividends represented?
- are historical values internally consistent?
- does breadth historically rely on this exact representation?

Do not infer from one ticker.

Inspect metadata and actual values.

---

# 4. Breadth-Specific Impact

This is critical.

Determine how corporate-action differences affect the breadth calculations:

### A. 50-day SMA

Can a split discontinuity change:

```text
close > 50DMA
```

for one or more days?

### B. `% above 50DMA`

Can corporate-action restatement materially change the breadth percentage?

### C. A/D line

Can corporate-action differences alter:

- advancing/declining classification
- cumulative A/D

### D. Regime

Can those changes alter:

- breadth percentile
- composite score
- regime label
- position_size_mult

Do not assume the impact is negligible.

Quantify it where possible.

---

# 5. Do NOT Solve This by Rewriting Historical Research

Do not choose this as an implicit solution:

```text
download everything again
→ overwrite historical cache
→ rebuild all history
```

unless separately approved.

Such a restatement could invalidate the existing research baseline.

Instead investigate **versioned data**.

---

# 6. Recommended Architecture to Investigate

Evaluate:

## Dataset A — Frozen Research

Keep:

```text
existing historical OHLCV
existing historical PIT breadth
existing baseline
```

unchanged.

Purpose:

> reproducible historical research.

## Dataset B — Live

Create a separate live-consistent dataset using one explicitly documented price basis and current provenance.

Purpose:

> live Regime decisions only.

Investigate whether this is practical.

Do not implement unnecessarily until architecture is understood.

---

# 7. Live Dataset Requirements

If a separate live dataset is appropriate, document:

- source
- price basis
- corporate-action handling
- update frequency
- historical warm-up requirement
- constituent source
- breadth calculation
- provenance
- freshness
- promotion rules

The live dataset must be internally consistent.

Do not mix:

```text
+
new price basis
```

inside the same rolling breadth calculation unless explicitly validated.

---

# 8. Constituent Data Investigation

The second blocker is the PIT constituent data ending:

```text
2026-06-30
```

while OHLCV reaches:

```text
2026-10-02
```

Determine whether newer constituent data exists from:

- existing local files
- source repositories
- APIs already used by the project
- existing scripts
- documented public sources

Do not use web search as an excuse to invent a new source without evaluating provenance and reproducibility.

Document the available source and its update cadence.

---

# 9. Constituent Carry-Forward Policy

The current code effectively does:

```text
latest snapshot <= as_of
```

without a maximum carry-forward.

Do NOT simply add a 14-day limit and call the problem solved.

First determine whether:

- membership snapshots are event-based
- a missing snapshot means "unchanged"
- there is a documented rebalance schedule
- an official or reproducible source can provide the latest changes

A freshness rule is only meaningful if the underlying update mechanism is understood.

---

# 10. Breadth Validity Contract

A live breadth observation should carry provenance sufficient to answer:

```text
What OHLCV did this use?
What constituent universe did this use?
What were their effective dates?
What is the breadth calculation date?
```

At minimum record:

```text
breadth_date
ohlcv_tail_date
constituent_snapshot_date
constituent_age_days
price_basis
source
```

No opaque "current" label.

---

# 11. Temporal Validity

The final live Regime validity should eventually require:

```text
OHLCV valid
+
constituents valid
+
breadth valid
+
temporal consistency
```

Do not mark breadth VALID merely because:

```text
breadth_date = today-ish
```

if the constituent universe is materially stale.

---

# 12. Current Research Integrity

The existing corrected historical baseline must remain:

```text
168 trades
+5.57%
Sharpe 0.513
MaxDD -10.39%
```

Run the baseline after any implementation change that touches shared loaders.

If it changes:

STOP.

Determine why before proceeding.

No historical research result should silently move.

---

# 13. Freshness Policy

The previously proposed policy remains:

```text
max_age_days = 7
temporal_skew_days = 3
on_failure = BLOCK_DECISION
```

But do NOT activate it for live trading until:

- OHLCV is genuinely fresh
- constituent data is genuinely fresh/valid
- breadth has been rebuilt from valid inputs

The policy protects the decision; it does not create the missing data.

---

# 14. Shadow Gate

Do not count any live session as qualifying until:

```text
data_validity = VALID
```

A stale/invalid-data session must remain:

```text
NON_QUALIFYING_DATA_INVALID
```

Do not reset historical shadow evidence; simply do not count invalid sessions.

---

# 15. No Strategy Changes

Do NOT modify:

- Regime formula
- Regime weights
- Regime thresholds
- divergence cap
- risk
- setup
- entry
- stop
- exit
- portfolio
- execution

Do not return to S1.

The current question is infrastructure correctness.

---

# 16. Decision Options

At the end, present the human with clearly separated architectural options.

At minimum:

### Option A — Frozen research + independent live dataset

Pros / cons / risks.

### Option B — Rebuild and restate the entire historical dataset

Pros / cons / research consequences.

### Option C — Continue using the frozen dataset temporarily

Explain why this is unsafe or safe, and under what constraints.

Do not choose a production strategy automatically.

---

# 17. Required Final Report

Create:

```text
reports/live_data_lineage_and_corporate_actions_YYYY-MM-DD.md
```

Include:

1. Corporate-action audit.
2. Exact historical price basis.
3. Breadth-specific impact.
4. Current constituent-source audit.
5. Current constituent coverage.
6. Whether a live-consistent dataset can be built.
7. Proposed versioned-data architecture.
8. Freshness implications.
9. Research reproducibility implications.
10. Recommended human decision.

---

# 18. Completion Criteria

Complete only when:

```text
[ ] 87 OHLCV differences classified
[ ] price-basis identified
[ ] breadth impact quantified
[ ] constituent-source audit complete
[ ] constituent freshness understood
[ ] live/research dataset architecture evaluated
[ ] no historical research data rewritten
[ ] current baseline unchanged
[ ] full tests pass
[ ] freeze audit = 0 mismatch
[ ] exit_engine_mode = legacy
[ ] no strategy parameter changed
[ ] no commit/push
```

Final state may remain:

```text
STATE B — LIVE DATA NOT READY
```

This is acceptable and preferable to false readiness.

---

# 19. Final Principle

Do not optimize the data around the existing backtest.

Do not corrupt reproducibility in order to obtain "current" data.

The objective is:

> **A frozen, reproducible historical dataset for research AND a separately traceable, internally consistent current dataset for live decisions.**

The historical and live datasets may legitimately have different roles and versions, provided this is explicit and auditable.

Stop for human review when the architecture options and evidence are complete.



No silent substitution of any kind: no SPY-as-breadth proxy, no synthetic value, no  
default regime, no default multiplier, no degradation to SIDEWAYS. The operator block  
names the state, the tail dates and the ages. When `BLOCK_DECISION` is active the  
pipeline sets `regime.status = FAILURE`, `regime.output = None`, and  
`pipeline_status = FAILURE`.

---

## H. Shadow gate

**PENDING, 0 / 20 — unchanged.** A session counts only if `overall_state == VALID`  
**and** a held position was evaluated **and** legacy vs shadow agreed **and** zero  
divergence **and** zero engine error. No session to date would have qualified (the  
inputs were never fresh), so this task neither advances nor resets the gate. No  
stale-input session is counted.

---

## I. Historical invariance

| check                                                     | result                                                                                     |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Backtest summary                                          | **identical** — 168 trades, +5.57 %, Sharpe 0.513, MaxDD −10.39 %                          |
| Behavioural equivalence, 15 critical trade fields × 168   | **0 mismatches**                                                                           |
| trade_log / equity_curve / skipped / screens / regime_log | **identical**                                                                              |
| Full test suite                                           | 8+9+5+18+19+31+18+7 = **115** existing, **+58** R7, **+92** freshness = **265, all green** |
| `freeze_conformance_audit`                                | **0 MISMATCH** (13 OPERATIVE / 9 TEST-VERIFIED / 1 INERT / 2 CLARITY — unchanged)          |
| Frozen parameters                                         | **11 verified unchanged**                                                                  |
| `exit_engine_mode`                                        | **legacy**                                                                                 |
| Registry                                                  | 158 modules; all four integrity helpers `production_decision_authority = NONE`             |

---

## J. Remaining blockers — the exact dependencies

`LIVE DATA READY: False`. Three, in dependency order:

1. **OHLCV corporate-action policy (blocks everything).** The cache is on the  
   unadjusted basis and reproduces exactly for **413 of 503** PIT members. For the  
   other **87** — including two 2-for-1 splits (APH, MNST, ratio exactly 0.5) — the  
   current feed and the cache are on different price bases. Until a policy exists  
   (restate the cache once and accept the history change, or restrict breadth to the  
   413 stable symbols), SPY cannot move past 2026-08-06 without either rewriting  
   history or leaving the cache inconsistent.
2. **PIT constituent dataset (blocks breadth validity even with current prices).**  
   Snapshots end 2026-06-30, so any breadth observation after that date is computed  
   from a carried-forward universe — 37 days at the 2026-08-06 tail, 96 days today.  
   The builder does this silently; `STALE_CONSTITUENTS` now detects it, but detection  
   is not a fix. `sp500_current.csv` and the yfinance path would need refreshing to  
   close it.
3. **Breadth promotion.** Depends on (1) and (2). The refreshed CSV stays unpromoted  
   until both are resolved.

**Secondary, and a consequence rather than a cause:** the OHLCV tail itself is 59 days  
behind today, so even after (1) and (2) the data would not satisfy a 7-day bound until  
the cache is refreshed past 2026-08-06.

---

## K. Honest notes on this task's own execution

Three errors I made and corrected, all recorded because they are the kind that  
produce false confidence:

1. **I over-claimed the OHLCV blocker.** I tested only `auto_adjust=True`, concluded  
   the cache was adjusted, and wrote a "refresh blocked" finding. Testing both modes  
   reversed it: the cache is unadjusted and 413/503 reproduce exactly. The blocker is  
   real but narrower than I first stated — corporate actions on 87 symbols, not a  
   settings mismatch.
2. **The §9 readiness check failed open.** A duplicated function definition made every  
   matrix row error, and the script still printed `LIVE DATA READY: True`. That is the  
   single most dangerous possible bug in this task — readiness asserted from a failed  
   evaluation. The gate is now fail-closed: any evaluation error becomes a blocker.
3. **The constituent bound initially mis-flagged history.** A flat 14-day bound marked  
   2024-06-14 as `STALE_CONSTITUENTS` (snapshot 37 days earlier) purely because the  
   membership dataset has legitimate 91-day gaps. The bound is now LIVE-only.

---

## L. State

```text
╔══════════════════════════════════════════════════════════════════╗
║  STATE B — LIVE DATA NOT READY                                    ║
║                                                                  ║
║  OHLCV            : STALE by 59 d (tail 2026-08-06)                ║
║  constituents     : STALE by 96 d (snapshot 2026-06-30)            ║
║  breadth          : STALE by 430 d in production (2025-07-31)      ║
║  temporal skew    : FAILING on every live date                    ║
║  BLOCK_DECISION   : implemented + tested, NOT enabled              ║
║  shadow gate      : PENDING 0/20 (unchanged)                       ║
║  history          : byte-identical (168 / +5.57% / 0.513 / −10.39%)║
╚══════════════════════════════════════════════════════════════════╝
```

The breadth CSV reaching August is **not** readiness, and this report does not claim  
it. Readiness requires all three inputs current *and* temporally consistent, and today  
none of the three is.

**Stopped for human review.** No commit, no push. No strategy, regime, risk, setup,  
entry, stop, exit, portfolio or execution parameter was changed.
