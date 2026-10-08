# Live Data Lineage & Corporate Actions — 2026-10-04

> Task: "Resolve Live Data Lineage Before Promotion" (§1–§19)
> Companion: `reports/ohlcv_corporate_action_audit_2026-10-04.md`
> Tools: `research/{ohlcv_lineage_audit,breadth_lineage_impact,constituent_staleness_impact}.py`
> Raw: `reports/{ohlcv_lineage_audit,breadth_lineage_impact,constituent_staleness_impact}_2026-10-04.json`

**Final state: STATE B — LIVE DATA NOT READY.** One blocker was eliminated this round
(§2), one was reclassified and is now provably small (§5), and one is confirmed to be an
upstream data problem that no local action can fix (§4).

---

## 0. What changed since the STATE B report

| | Before (2026-10-04 STATE B) | After (this round) |
|---|---|---|
| Blocker 1 — OHLCV basis | "87 symbols, 2 branches, refresh impossible" | **ELIMINATED.** 503 audited, **0 unexplained**. 322 dividend restatements, 86 precision noise, 2 splits, 90 identical. |
| Blocker 2 — constituents | "stale by 37 days, unknown impact" | **RECLASSIFIED.** Impact measured: **0.000 pp median** at 37 and 60 days. The real blocker is that the **upstream has no newer data**. |
| Blocker 3 — breadth promotion | blocked by 1 and 2 | Still blocked, but for a **different and narrower** reason. |
| Price basis of the cache | *stated as* `auto_adjust=False` | **`auto_adjust=True`** — the earlier statement was wrong (§1). |

---

## 1. Correction of the previous round's central claim

The STATE B report asserted:

> *cache 是以 `auto_adjust=False`（未調整收盤價）下載的* — and on that basis concluded
> that 87 of 503 symbols could not be safely refreshed.

**Both halves of that were wrong.**

1. **The basis.** All three downloaders use `auto_adjust=True`
   (`src/data/data_fetcher.py:34,67`; `regime_dual_engine/download_missing_ohlcv.py:78`).
   Proof from the data: MNST closed **24.056667** on 2016-01-04 in the cache. With a
   3-for-1 in Nov-2016 and a 2-for-1 in Mar-2023 the *raw* price was ~$577. The cache value
   is split-adjusted and dividend-adjusted throughout.
2. **The 87.** That count came from (a) comparing only a 26-day window at the end of the
   cache, and (b) an **absolute** tolerance of 1e-6 on prices ranging $12–$313. A
   relative tolerance over the full overlap gives a completely different answer.

**Method lesson worth keeping:** an absolute tolerance on a mixed-price panel is not a
loose test, it is a *wrong* test — it is tight enough to flag float noise on a $300 stock
and loose enough to hide a real difference on a $12 one. Every comparison in this round is
relative to the cache value.

---

## 2. §2/§3 — Corporate-action audit (all 503 symbols)

Full detail: `reports/ohlcv_corporate_action_audit_2026-10-04.md`.

| Verdict | n | Cause | Economic meaning |
|---|---:|---|---|
| `IDENTICAL` | 90 | — | reproduces bit-for-bit |
| `DIVIDEND_RESTATEMENT` | 322 | a dividend went ex after the cache was written, or the provider added/corrected a historical ex-date | one constant factor, median **−0.46%** |
| `PRECISION_NOISE` | 86 | upstream serves 6-decimal prices; cache stores full float64 | < 1e-5, none |
| `SPLIT_AFTER_BOUNDARY` | 2 | 2-for-1 after the cache boundary: **MNST 2026-08-11**, **APH 2026-09-03** | **−50%** level shift |
| `UNEXPLAINED` | **0** | — | — |
| not comparable | 3 | EA (no cache), AVB + EQR (no price data) | coverage, not lineage |

**The key structural fact:** every non-identical symbol's ratio is **flat** across the whole
history. A dividend restatement is one constant factor (`yf / cache` = 0.999138 for AAPL on
both 2016-01-04 and 2026-08-06; that factor equals `1 − 0.27/312.14` for AAPL's single
post-cache dividend). A split is the same shape with factor 0.5. There is no time drift
anywhere, which is what makes the audit conclusive rather than statistical.

**Affected fields:** all four price fields move together by the same factor. `volume`
differs on at most **one row per symbol** (provider revision, not adjustment; unused by
breadth or by stop/exit logic).

**Two audit defects found and fixed** (recorded because they produced false confidence):

* The boundary test used `>` where DHI's ex-date is **2026-08-06**, the cache's own last
  bar. Fixed to `>=`.
* BNY's ex-date is 2026-07-27 — *before* the boundary — yet it still restated. The provider
  **added that record after the cache was written**. So the ex-date alone is not
  sufficient evidence; the flat-ratio signature is the real test. Both are now
  `DIVIDEND_RESTATEMENT` and `UNEXPLAINED` is empty.

---

## 3. §3 — The exact historical price basis

| Question | Answer |
|---|---|
| Adjusted or unadjusted? | **Adjusted** — `auto_adjust=True` (splits + dividends) |
| Splits | Retrospectively applied to the entire series; the cache contains no split discontinuity |
| Dividends | Retrospectively applied; a post-cache ex-date restates every earlier bar by one constant factor |
| Internally consistent? | **Yes** — 90 exact, 413 differ only by a dated, single-factor cause, 0 unexplained |
| Does breadth depend on it? | **No** — see §4. The dependence is mathematically null. |

---

## 4. §4 — Breadth-specific impact: the price basis moves nothing

Method: reuse `pit_breadth_data.build_breadth` + `PitMembership.membership_matrix`
**unchanged**, so the two arms differ only in price source. The baseline arm reproduces the
shipped `breadth_pit_2016_2025.csv` **exactly** (2,408 overlap days, max |diff| = 0.0), so
the counterfactual is measuring the right thing.

### 4.A/4.B — 50-day SMA and % above 50DMA

A constant rescale of a price cannot change `close > 50DMA`, because both sides of the
comparison scale identically. Verified by applying each **measured** factor to the same
panel:

| Factor applied | Source | `pct_above_50dma` max abs diff | Days changed |
|---:|---|---:|---:|
| 0.956084 | AMCR (worst dividend) | **0.0000000000 pp** | **0** |
| 0.995432 | median dividend | **0.0000000000 pp** | **0** |
| 0.999829 | mildest dividend | **0.0000000000 pp** | **0** |
| 0.500000 | MNST 2-for-1 split | **0.0000000000 pp** | **0** |
| 0.499226 | APH 2-for-1 split | **0.0000000000 pp** | **0** |

**A split does not move it either**, because the split is applied retrospectively — the
whole series including the 50-day window is on one scale. (A split applied *mid-window* to
only part of the data would break this; that is precisely the risk of a naive append, §6.)

### 4.C — A/D line

`sign(close.diff())` is also invariant under a positive rescale. Measured: **0 days
changed** for all five factors.

### 4.D — Regime

| Metric | Baseline | Counterfactual | Diff |
|---|---:|---:|---:|
| Final breadth percentile | 91.2698 | 91.2698 | **0.0 pp** |
| Max abs percentile diff | — | — | 5.56 pp (coverage-driven, §4.E) |
| Days flipping the BULL (≥65) side | — | — | 27 |
| Days flipping the BEAR (≤35) side | — | — | 14 |

### 4.E — The confounder I had to remove

The two independent panels differ in **coverage**, not only in price basis: live yfinance
returns *"possibly delisted; no price data found"* for members the cache holds. **92 of 614**
cached tickers stop before 2026-06-01 (AAL 2024-09-23, AAP 2023-08-25, AET 2018-11-29, …)
and 11 disappear entirely from a live re-download.

That is what produced the raw counterfactual numbers (mean 0.249 pp, max 1.886 pp, A/D
final −571). **Those are coverage effects, not basis effects.** §4.A–4.D above are the
correctly isolated figures. Reporting the uncorrected numbers as "the impact of the price
basis" would have been wrong.

**Answer to §4: the price basis has no measurable effect on `pct_above_50dma`, on the A/D
line, or on any regime input. What does have an effect is coverage.**

---

## 5. §5/§8/§9 — Constituent audit: impact is small, and the source is the problem

### 5.1 The update mechanism is event-based, and that is now measured

| Statistic | Value |
|---|---:|
| Snapshots (all history) | 2,718 |
| Inter-snapshot gap, median | 2 days |
| p90 / p99 / **max** | 10 / 34 / **91 days** |
| Gaps > 14 days | 67 |
| 2026 snapshots | 12 (avg gap 15.2 days) |
| 2025 snapshots | 16 |

Membership snapshots are written **when membership changes**, not on a schedule. There is
**no documented rebalance schedule and no documented allowable carry-forward period**. A
missing snapshot does mean "unchanged" as a *data* statement, but the code has no bound on
how long that assumption may hold.

### 5.2 How much does a stale universe actually move breadth?

T1 — marginal sensitivity at 2026-08-06 (base 64.5418%): removing the 5 / 10 / 20 / 50
names closest to their own 50DMA moves the reading by **+0.25 / +0.30 / +0.60 / +2.27 pp**.

T2 — historical analogue, measuring only days **inside** the carry-forward window
(244 snapshots, 2018→2026):

| Carry-forward | Snapshots | Members changed (median) | `pct_above_50dma` mean diff (median) | p90 | Final-day diff p90 |
|---:|---:|---:|---:|---:|---:|
| 37 days | 244 | 84 | **0.000 pp** | 0.000 pp | 0.000 pp |
| 60 days | 240 | 85 | **0.000 pp** | 0.000 pp | 0.000 pp |
| 90 days | 237 | 87 | **0.036 pp** | 0.084 pp | 0.478 pp |

~85 names change in 90 days (17% of the index), yet the breadth reading moves a median of
**0.036 pp**. Individually small moves cancel.

*Method defect found and fixed here too:* the first version of T2 sliced the panel from
2016 and averaged over every day since, which diluted the carry-forward effect with two
decades of unrelated churn and produced a mean that **decreased** as the horizon grew — an
impossibility. The monotonicity check is now built into the script
(`T2_monotonicity_check`) so a contaminated measurement cannot be reported silently.

**A third defect:** membership churn was counted by positional index across two frames with
different column spaces, reporting 572 of 503 names as "changed". Now counted by ticker
name against each frame's own columns: the true figure is ~85.

### 5.3 The actual blocker: the upstream has no newer data

| File | mtime | content tail |
|---|---|---|
| `sp500_changes.csv` | 2026-09-04 21:18 | **2026-06-30** |
| `sp500_ticker_start_end.csv` | 2026-09-04 21:18 | 2026-06-29 / 2026-06-30 |
| `sp500_current.csv` | 2026-09-04 21:18 | 2026-06-29 |
| `sp500_thuningxu.csv` | 2026-09-04 21:18 | 2026-06-02 |
| `nasdaq100_thuningxu.csv` | 2026-09-04 21:18 | 2026-05-18 |
| `sp500_pierre.csv` | 2026-09-04 21:18 | **`404: Not Found`** (14 bytes) |

**Every file was downloaded on 2026-09-04 — a month ago — and none contains data past
2026-06-30.** One file is a stored HTTP 404. There is no fetch script in the repo and no
scheduler.

This is the **opposite** of the OHLCV situation and the distinction matters:

* **OHLCV**: the source *can* provide to 2026-10-02; the cache simply was not refreshed.
  Re-running fixes it.
* **Constituents**: a refresh was already attempted on 2026-09-04 and the source had
  nothing newer to give. **Re-running does not help.** This is an upstream data-availability
  problem, and no local code change resolves it.

I did not search the web for a replacement source. §8 asks for a source with evaluated
provenance and reproducibility, and inventing one on the spot would be exactly the wrong
move: a different universe definition would silently change the breadth methodology that
Regime v1 is frozen against.

### 5.4 Current state

`2026-07-01 → 2026-08-06` is **26 trading days** computed on the **2026-06-30** universe
(502 names, 501 with data), carried forward **37 days**. T2 says the reading is probably
right — but *probably* is not *verifiably*, and the true membership for those dates is
unobservable from local data.

---

## 6. §6/§7 — Can a separate live-consistent dataset be built?

**Yes, technically. It is not yet appropriate, and one dependency is missing.**

Evidence that it works:

* A freshly downloaded panel is **internally self-consistent**: 0 single-day jumps > 35%
  across the full 2016→2026 history for AAPL, i.e. splits are already back-applied.
* Warm-up is satisfied: **502/503** current members have ≥ 2,663 bars each, far beyond the
  252 bars the regime percentile needs.
* The breadth formula is scale-invariant (§4), so a new price basis introduces no
  methodological discontinuity.

**The missing dependency is the universe.** A live dataset built on the 2026-06-30 universe
is self-consistent but **not current** — it would be internally coherent and wrong, which
is harder to detect than an obviously stale file.

Specification for Dataset B, for when the human approves it:

| Property | Value |
|---|---|
| Source | yfinance, `interval=1d`, `auto_adjust=True` |
| Price basis | Fully adjusted, single consistent basis for the whole series |
| Corporate-action handling | Handled by the provider, applied retrospectively |
| Universe | Current PIT membership, carried forward **only** under a bounded rule |
| Warm-up | ≥ 252 bars (satisfied: 2,663) |
| Breadth | `build_pit_breadth` unchanged — the formula is frozen |
| Update frequency | Not decided; no scheduler exists |
| Promotion rule | Append-only for the live dataset, with a hard fail on any bar the provider restates |
| Provenance | `price_basis`, `ohlcv_tail_date`, `constituent_snapshot_date`, `constituent_age_days` recorded per observation (§10) |

---

## 7. §16 — Architectural options

### Option A — Frozen research dataset + independent live dataset

| | |
|---|---|
| **Pros** | Historical baseline stays bit-reproducible (verified this round). Live data can use one clean basis. The two roles are explicitly separated and auditable, which §19 asks for. No risk to the research line. |
| **Cons** | Two datasets to maintain and keep in sync. Needs a real scheduler and a constituent source that does not currently exist. Requires the Dataset B build (~600 downloads). |
| **Risks** | Divergence between the two can be mistaken for a strategy change. Mitigated by the §10 provenance fields now implemented. |

### Option B — Rebuild and restate the entire historical dataset

| | |
|---|---|
| **Pros** | One dataset, one basis, no splicing question at all. |
| **Cons** | **It would move the frozen baseline.** The 92 delisted members have no live source, so a full re-download *loses* PIT history that the current cache holds — a survivorship regression in the opposite direction from the one the project has been guarding against since the PIT leak. |
| **Research consequences** | 168 trades / +5.57% / Sharpe 0.513 / MaxDD −10.39% would all become invalid and every S1–S3, R2 and R3 conclusion would need re-running. §12 says stop if the baseline moves. |
| **Verdict** | **Rejected on the evidence.** The coverage loss alone disqualifies it. |

### Option C — Continue on the frozen dataset temporarily

| | |
|---|---|
| **When it is safe** | For **historical research**: completely safe, and it is what all existing results are based on. |
| **When it is unsafe** | For **live decisions**. Today the composite would be built from a 430-day-old breadth, a 96-day-old universe and a 59-day-old price feed. `position_size_mult` is the trade/don't-trade boundary, and BS measurement showed the regime can **flip** on stale breadth alone (BEAR→BULL→BEAR, mult 0.0→1.0→0.0). |
| **Constraint** | Only defensible while live trading stays disabled and `BLOCK_DECISION` is armed. The 20-session shadow gate must not count sessions with invalid data. |

### Recommendation

**Option A, in two stages, with C as the interim state.** Specifically:

1. **Now:** keep the frozen dataset for research; keep live blocked. Nothing about the
   strategy changes. The §10 provenance fields are already implemented so that from now on
   every record states what it was built from.
2. **Next, and it is a data task not a code task:** establish a constituent source that
   actually updates. Until that exists, Dataset B would be internally consistent and
   wrong.
3. **Then:** build Dataset B, pin its price basis explicitly in
   `ProductionConfig.ohlcv_price_basis`, and only then arm
   `max_age_days=7 / skew=3 / BLOCK_DECISION`.

I am not selecting a production change, and I have not made one.

---

## 8. §8 — Decision-record contract (implemented this round)

`production/data_validity.py` and `production/pipeline.py` gained the §10 fields. These are
**additive record fields only** — no decision branch reads them.

| Field | Status |
|---|---|
| `breadth_date` | added (flat alias of `breadth_tail_date`) |
| `ohlcv_tail_date` | added (via `breadth_ohlcv_tail()`) |
| `price_basis` | added, from `ProductionConfig.ohlcv_price_basis` |
| `constituent_snapshot_date` | already present |
| `constituent_age_days` | already present |
| `source` | already present |

`ohlcv_price_basis` **defaults to `None`**, and the record then reports `unrecorded`. This
is deliberate: the frozen baseline must not acquire a provenance claim it never had, and
the live dataset's basis is a human decision.

---

## 9. §12 — Research integrity

| Check | Result |
|---|---|
| Backtest after all changes | **168 trades / +5.57% / Sharpe 0.513 / MaxDD −10.39%** — identical |
| 13 frozen parameters | all original values |
| `exit_engine_mode` | `legacy` |
| `freshness_policy` | `None` (not armed) |
| Test suite | **265 green** (115 existing + 58 R7 + 92 freshness) |
| Freeze audit | **0 MISMATCH** (13 OPERATIVE / 9 TEST-VERIFIED / 1 INERT / 2 CLARITY) |
| Historical data rewritten | **none** — no cache file, loader or breadth CSV was modified |
| Registry | 161 modules; the three new tools are `production_used=0`, `authority=NONE` |
| Commit / push | none |

---

## 10. §18 — Completion checklist

| | |
|---|---|
| 87 OHLCV differences classified | ✅ all **503** classified, 0 unexplained |
| Price basis identified | ✅ `auto_adjust=True`, fully adjusted |
| Breadth impact quantified | ✅ **zero** for the basis; the raw numbers were coverage and are now separated |
| Constituent-source audit complete | ✅ complete, including the stored HTTP 404 |
| Constituent freshness understood | ✅ event-based; impact measured at 0.000 pp (37/60 d) |
| Live/research architecture evaluated | ✅ §7 |
| No historical data rewritten | ✅ |
| Baseline unchanged | ✅ |
| Full tests pass | ✅ 265 |
| Freeze audit = 0 mismatch | ✅ |
| `exit_engine_mode = legacy` | ✅ |
| No strategy parameter changed | ✅ |
| No commit/push | ✅ |

---

## 11. §16/J — Remaining blocker

**One blocker, and it is not a code problem:**

> The constituent source (`fja05680/sp500`) provided nothing after 2026-06-30 when it was
> fetched on 2026-09-04, and one of its files is a stored HTTP 404. Until a source with
> evaluated provenance and a real update cadence is in place, any live breadth built today
> would be computed on a 96-day-old universe. The magnitude is probably small (T2: 0.000 pp
> median at 37 days), but *probably* is not *verifiably*, and the regime label is the
> trade/don't-trade boundary.

Secondary, and a consequence rather than a cause: the OHLCV cache is 59 days behind
(2026-08-06 vs a source that reaches 2026-10-02), so a 7-day freshness policy would also
block on the SPY side until prices are refreshed.

**The price-basis question that was blocking everything last round is resolved: it is not a
blocker at all.** It never was — the effect on breadth is exactly zero. The blocker was and
remains the universe.

---

## 12. Limitations

1. The audit compares against **today's** yfinance response. If the provider changes its
   adjustment basis again, these factors change again. None of this is a permanent property
   of the data.
2. T2 measures how much a stale universe *would* have moved breadth using historical
   membership changes as the sample. The actual 2026-06-30 → present changes are
   unobservable, so this is an estimate of the mechanism, not a measurement of the instance.
3. Three symbols (EA, AVB, EQR) could not be compared at all. Their lineage is **unknown**,
   not clean.
4. The Dataset B feasibility evidence is a spot check plus structural reasoning, not a
   completed build.
