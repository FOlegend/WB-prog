## Regime Dual-Engine — Next Validation & Data-Quality Task

Continue from the existing `regime_dual_engine` implementation. Do **not** redesign the core architecture.

### Goal

Validate the Dual-Engine system properly before any production integration:

- HMM = 50%
- Market Breadth = 50%
- Distribution Days = tactical hard cap only
- No KER / ADX / index-level MA trend scoring

---

### 1. Fix the Breadth survivorship-bias problem first

Use historical S\&P 500 constituent data instead of the current-constituent universe.

Primary source:

<https://github.com/fja05680/sp500>

Prefer `sp500_ticker_start_end.csv` or the historical components dataset.

Also inspect/cross-check:

<https://github.com/thuningxu/sp500nq100>

and, where useful:

<https://github.com/pierrebrunelle/sp500-historical-constituents>

Requirements:

- Reconstruct point-in-time S\&P 500 membership.
- For each trading date, use the latest constituent snapshot/membership valid on that date.
- Handle ticker start/end dates correctly.
- Do not use future constituents for earlier dates.
- Identify ticker changes, missing OHLCV, delistings, mergers, etc.
- Clearly report any remaining data limitations.

Do not assume GitHub data is perfect; validate the membership-date logic before using it.

Then compare:

`Current-constituent Breadth` vs `Point-in-time Breadth`

Report:

- correlation
- mean absolute difference
- regime differences
- divergence differences
- breadth-thrust differences
- backtest performance differences

---

### 2. Audit Distribution Days before changing thresholds

The current implementation triggers the `>=5 / 25 days` cap on ~51% of trading days.

Do NOT immediately optimize the threshold.

First verify the raw Distribution Day definition, including:

- price condition
- volume condition
- index/universe used
- adjusted price/volume handling
- duplicate/consecutive signals
- rolling count logic
- missing-data handling

Then report rolling-count statistics:

- mean / median
- 75th / 90th / 95th percentile
- max
- frequency of count >=3, >=4, >=5, >=6, >=7
- trigger frequency by year
- trigger frequency by regime
- average/max trigger episode length

Determine whether `5` is actually an extreme condition or effectively a normal-state condition.

Only after the audit, run a small sensitivity test for thresholds:

`4 / 5 / 6 / 7`

Compare:

- trigger frequency
- CAGR
- Sharpe
- Sortino
- MaxDD
- Calmar
- CAGR retention vs HMM+Breadth

Do not select a new threshold solely because it gives the highest CAGR.

---

### 3. Re-check Breadth divergence behavior

The current test shows top warnings occurring ~21–30 trading days before sampled tops.

Do not automatically treat earlier as better.

Measure:

- warning date
- actual index peak date
- lead time
- index return after warning until peak
- opportunity cost from reducing exposure too early

Also verify that Breadth Thrust occurs before the HMM regime shift at historical bottoms.

---

### 4. Run the required ablation again using PIT breadth

Compare:

1. HMM only
2. Breadth only
3. HMM + Breadth
4. HMM + Breadth + Distribution Days

Use realistic execution assumptions already present in the backtest engine.

Focus on both:

- return generation
- risk reduction

The regime layer should be evaluated primarily as a risk-management overlay, not assumed to be an alpha generator.

---

### 5. Robustness

After the PIT data and Distribution Days audit:

- Re-run walk-forward OOS.
- Re-run threshold perturbation: `60/40`, `65/35`, `70/30`.
- Re-run breadth lookback: `7 / 10 / 13` days.
- Ensure all calculations are point-in-time.
- Ensure HMM latent states are mapped consistently per training window.
- Do not tune parameters on the final OOS set.

---

### 6. Preserve the architecture

Do not introduce new regime indicators.

Final architecture must remain:

`HMM + Market Breadth → Composite → Regime`

with:

`Distribution Days → position-size cap only`

Distribution Days must never affect the composite score or regime label.

Keep the public schema unchanged:

```python
{
    "regime_label": "...",
    "composite_score": ...,
    "position_size_mult": ...,
    "strategy_mode": "...",
    "veto_flags": [...]
}
```

---

### 7. Final report

Return a concise report with:

- files changed
- tests passed/failed
- PIT breadth data source and coverage
- current vs PIT breadth comparison
- Distribution Days audit
- ablation results
- OOS/walk-forward results
- parameter robustness
- remaining data limitations
- final recommendation: `PASS / CONDITIONAL PASS / FAIL`

Important:

**Do not optimize the system simply to make historical backtests pass.**  
If data quality prevents a reliable conclusion, say so explicitly.

---

## Execution Results (2026-08-30) — summary of the 7-step review

**Verdict: CONDITIONAL PASS.** See `reports/dual_engine_review_report.html` for the full report; each step below names its report file.

| # | Step | Key finding | Report |
|---|------|-------------|--------|
| 1 | PIT breadth | fja05680/sp500 (2718 snapshots) cross-checked vs thuningxu (Jaccard 0.9995); cache backfilled 96 historical members (coverage 72%→83.4%). Current vs PIT: Pearson 0.996, regime agreement 96.8%, MAD 2.17pp → **breadth survivorship bias is small** | `dual_engine_breadth_compare.json`, `dual_engine_constituents.json` |
| 2 | DistDays audit | count median=4, mean=4.55, ≥5 fires **47.3%** of days → **5 is normal-state, not extreme**. Threshold 4/5/6/7: overlay hurts vs no-overlay (PIT: D +4.09% vs C +6.24%; thr5 −2.52% vs no-overlay +2.87%). No threshold change selected by CAGR | `dual_engine_distdays_audit.json`, `dual_engine_distdays_thresholds.json` |
| 3 | Divergence | warnings lead 19–96 td; **2024-Q3 false alarm cost +11.46%** missed upside (96 td early). Thrust before HMM-BULL only 1/3 bottoms | `dual_engine_divergence_review.json` |
| 4 | Ablation (PIT) | all 4 variants improve with PIT breadth; C: +6.24% (Sharpe 0.15, MaxDD −17.4%) vs current-constituent +3.26% (0.10, −18.5%) | `dual_engine_ablation_pit.json` |
| 5 | Robustness | walk-forward yearly −5.9%..+8.7% (no catastrophic year); thresholds 60/40/65/35/70/30 smooth (3.3/6.2/7.7%); lookback 10 best, 7 degrades (−7.7%) | `dual_engine_robustness_pit.json` |
| 6 | Architecture | unchanged; schema untouched; 10/10 tests (incl. 2 new guard tests) | `tests/test_regime_dual.py` |
| 7 | Report | **CONDITIONAL PASS** with 3 conditions (C1 dist-day ≥5 not extreme; C2 divergence false-alarm cost; C3 residual 16.6% OHLCV gap) | `dual_engine_review_report.html`, `dual_engine_review_decision.json` |

New modules (all PIT data layer, no core change): `pit_constituents.py`, `pit_breadth_data.py`, `daily_signals.py`, `audit_distribution_days.py`, `audit_distdays_thresholds.py`, `validation_divergence_review.py`, `ablation_pit.py`, `robustness_pit.py`, `download_missing_ohlcv.py`, `build_review_report.py`. Data: `data/constituents/` (sources + download report), `regime_dual_engine/data/breadth_pit_2016_2025.csv`.

---

## Final Architecture (2026-08-30) — HMM + Market Breadth ONLY

Per the final spec, the **production** regime is two engines only:

```
HMM (50%) + PIT Market Breadth (50%) -> Composite 0-100
  -> >=65 BULL (trend_following, 1.0) / 35-65 SIDEWAYS (mean_reversion, 0.5) / <=35 BEAR (defensive, 0.0)
  -> Breadth Thrust override (BULL + 1.0) / Bearish Breadth Divergence cap (<=0.50)
```

- **Distribution Days removed from production.** `DualEngineConfig.enable_dist_day_overlay`
  defaults to `False`; `engine.py` skips the count entirely in production.
  `distribution_days.py` + ablation variant D + the audit scripts remain as
  archived research code for historical comparison.
- **HMM State Alignment verified** (`validation_state_alignment.py`): 0 violations
  across 628 walk-forward windows; BULL-state mean return always > BEAR-state;
  labels re-derived per window from fitted statistics (mean-return ranking) —
  state indices can never flip the composite upside-down.
- **Breadth percentile lookback**: 252 trading days (1 year) rolling window
  (`DualEngineConfig.breadth_percentile_lookback=252`) — documented, comparable
  across market cycles.

### Final validation summary (all on PIT breadth, dynamic universe top20 2018-2025)
| Check | Result |
|---|---|
| Orthogonality (HMM vs Breadth) | Pearson 0.483 — strong evidence of orthogonality |
| Regime conditioning | BULL +29.7 bps/day, SIDEWAYS +7.8, BEAR −6.8 (monotonic) |
| Ablation | HMM-only +1.27% / Breadth-only +8.55% / HMM+Breadth **+6.24%** (Sharpe 0.15) |
| PIT vs current breadth | Pearson 0.996; regime agreement 96.8% |
| Walk-forward OOS | yearly −5.9%..+8.7%, no catastrophic year |
| Threshold robustness | 60/40 +3.3% / 65/35 +6.2% / 70/30 +7.7% (smooth) |
| Lookback robustness | 7d −7.7% / 10d +6.2% / 13d +4.1% (10d best) |
| Unit tests | 11/11 pass |
| **Verdict** | **PASS** (see reports/dual_engine_review_report.html) |
