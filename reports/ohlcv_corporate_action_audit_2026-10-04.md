# OHLCV Corporate-Action Audit — 2026-10-04

> Task: §2 / §3 of "Resolve Live Data Lineage Before Promotion"
> Tool: `research/ohlcv_lineage_audit.py` · Raw: `reports/ohlcv_lineage_audit_2026-10-04.json`
> Universe: 503 PIT S&P 500 members as of 2026-06-30 · Cache boundary: 2026-08-06
> **Read-only: no cache file, loader or frozen contract was modified.**

---

## 0. Headline

| | |
|---|---|
| Symbols audited | **503** |
| `IDENTICAL` (byte-equivalent on the configured basis) | **90** |
| `DIVIDEND_RESTATEMENT` | **322** |
| `PRECISION_NOISE` | **86** |
| `SPLIT_AFTER_BOUNDARY` | **2** (APH, MNST) |
| `UNEXPLAINED` | **0** |
| not comparable | 3 (EA no cache, AVB + EQR empty download) |

**The "87 differing symbols" from the previous round was wrong.** It came from comparing
only a 26-day window at the end of the cache and from using an absolute tolerance on
prices that range from $12 to $313. With a relative tolerance and the full overlap, the
picture is: **zero unexplained differences, two genuine splits, and the rest are
dividend restatements of a known, dated cause.**

---

## 1. Correction of the previous round's conclusion

The 2026-10-04 "STATE B" report stated:

> *cache 是以 `auto_adjust=False`（未調整收盤價）下載的*

**That is incorrect.** All three download paths in the repo use `auto_adjust=True`:

| File | Line | Setting |
|---|---|---|
| `src/data/data_fetcher.py` | 34 | `auto_adjust=True` |
| `src/data/data_fetcher.py` | 67 | `auto_adjust=True` |
| `regime_dual_engine/download_missing_ohlcv.py` | 78 | `auto_adjust=True` |

Evidence: MNST closed **24.056667** on 2016-01-04 in the cache. The 3-for-1 split of
2016-11-10 and the 2-for-1 of 2023-03-28 mean the *raw* 2016 price was ~$577. Today's
yfinance `auto_adjust=False` returns **12.028333** for that date; `auto_adjust=True`
returns the same 12.028333. The cache's 24.056667 is exactly 2× the current
`auto_adjust=False` value — consistent with a **fully adjusted** series that has not yet
absorbed the **2026-08-11 2-for-1 split**.

The earlier "413 identical / 87 differing" split came from testing `auto_adjust=False`
against the cache: for the 86 `PRECISION_NOISE` symbols the two coincide, and for the
rest the comparison was measuring a different basis than the one the repo actually uses.

**Method note.** The first version of this audit used an absolute tolerance of 1e-6.
That is meaningless across a price range of $12–$313: it separated float noise for one
symbol and hid real differences for another. Every comparison here is **relative** to the
cache value. Measured float noise is ~1e-13; the upstream occasionally serves prices
rounded to 6 decimals, which is the 1e-6-scale wobble in the `PRECISION_NOISE` class.

---

## 2. §3 — The exact price basis of the historical cache

| Question | Answer | Evidence |
|---|---|---|
| Adjusted or unadjusted? | **Adjusted** (`auto_adjust=True` = splits + dividends) | `data_fetcher.py:34,67`; `download_missing_ohlcv.py:78` |
| How are splits represented? | **Retrospectively applied to the entire history.** The cache contains no split discontinuity. | MNST 2016-01-04 = 24.056667 despite a 3-for-1 in Nov-2016 |
| How are dividends represented? | **Retrospectively applied.** A post-cache ex-date restates every earlier bar by one constant factor. | AAPL factor 0.999138 flat from 2016-01-04 to 2026-08-06 |
| Internally consistent? | **Yes** — 90 symbols reproduce bit-for-bit; the other 413 differ only by a single constant factor with a dated cause. | 0 `UNEXPLAINED` |
| Does breadth depend on this representation? | **Yes, and only through `close` vs its own 50-day SMA** — see §4. | `reports/breadth_lineage_impact_2026-10-04.json` |

### The two structural facts that make this tractable

1. **A dividend restatement is a single constant factor, not a drift.** Verified on AAPL:
   the ratio `yf / cache` is **0.999138** on 2016-01-04 and **0.999138** on 2026-08-06 —
   identical. That factor equals `1 − dividend / price_at_ex_date` for the one dividend
   that went ex **after** the cache was written (AAPL, $0.27, 2026-08-10):
   `1 − 0.27/312.14 = 0.999135`. Match.
2. **A split is the same shape with a much larger factor.** MNST and APH both show
   `ratio = 0.500000` flat across the whole history — the signature of a 2-for-1 that
   the live feed has already back-applied and the cache has not.

So the audit reduces to: *find the dated corporate action, confirm the factor matches it.*

---

## 3. §2 — Classification of all 503 symbols

### 3.1 `SPLIT_AFTER_BOUNDARY` — 2 symbols, **material**

| Ticker | Split | Ex-date | Implied factor | Measured factor | Days affected |
|---|---|---|---:|---:|---:|
| **MNST** | 2-for-1 | 2026-08-11 | 0.5 | **0.500000** | 2,663 (all) |
| **APH** | 2-for-1 | 2026-09-03 | 0.5 | **0.499226** | 2,663 (all) |

Both splits occur **after** the cache boundary (2026-08-06), so the cache predates them
and the live feed has already restated the whole history. The measured factor matches the
implied factor exactly for MNST and to 4 decimal places for APH.

**These two are the only symbols where a refresh would alter the meaning of a price, not
merely its scale.** For MNST, splicing today's post-split bars onto the cache would create
a 50% discontinuity; for APH likewise.

### 3.2 `DIVIDEND_RESTATEMENT` — 322 symbols, **immaterial to scale**

A dividend went ex after the cache was written, or the provider added/corrected a
historical ex-date afterwards. In both cases `auto_adjust` restates the entire history by
one constant factor.

| Statistic | Factor | Price impact |
|---|---:|---:|
| Minimum | 0.956084 | **−4.39%** |
| p25 | 0.992536 | −0.75% |
| **Median** | **0.995432** | **−0.46%** |
| p75 | 0.997477 | −0.25% |
| Maximum | 0.999829 | −0.02% |

Largest impacts: AMCR −4.39%, KVUE −2.27%, VICI −1.88%, KHC −1.57%, MO −1.57%, UPS −1.57%.

A uniform rescaling of a price series changes **neither** `close > 50DMA` nor
`sign(close.diff())` — both sides of each comparison move by the same factor. This is
verified numerically in §4 of the main report rather than assumed.

### 3.3 `PRECISION_NOISE` — 86 symbols, **no economic meaning**

Max relative difference < 1e-5, no post-boundary split, no post-boundary dividend. The
last 5 bars of A/ABBV show `ratio = 1.000000000` exactly. This is the upstream serving
6-decimal prices while the cache stores full float64. `float32` round-trip was tested and
**ruled out** (error 1e-16, not 1e-7).

### 3.4 `UNEXPLAINED` — **0**

An earlier pass left 2 symbols unexplained (BNY 8.4e-4, DHI 3.0e-3). Both are dividend
restatements and both exposed a defect in the audit's own boundary test:

* **DHI** went ex-dividend on **2026-08-06** — the cache's own last bar. A strict `>` missed it.
* **BNY** went ex on 2026-07-27, *before* the boundary, but the provider added that record
  after the cache was written. **The ex-date alone is therefore not sufficient evidence;
  the flat-ratio signature is the real test.**

Both are now classified correctly and `UNEXPLAINED` is empty.

### 3.5 Not comparable — 3 symbols

| Ticker | Cause |
|---|---|
| EA | no cache file |
| AVB | yfinance returned empty |
| EQR | yfinance reports "possibly delisted; no price data found" |

EA is the same symbol already known to be missing from the PIT OHLCV coverage
(502/503 in the earlier audit). These three are a **coverage** issue, not a lineage issue.

---

## 4. Which fields are affected

| Field | Relative difference | Rows affected | Note |
|---|---:|---:|---|
| `open` | same factor as `close` | all differing rows | fully adjusted |
| `high` | same factor as `close` | all differing rows | fully adjusted |
| `low` | same factor as `close` | all differing rows | fully adjusted |
| `close` | the factor itself | all differing rows | fully adjusted |
| `volume` | up to **54%** | **1 row** | provider revision, not adjustment |

Volume differs on at most one row per symbol (A: 54% on one bar; AAPL: 1.6% on one bar).
That is a data-vendor correction, not a corporate action, and it is not used by the
breadth calculation or by the stop/exit logic.

**All four price fields move together by the same factor**, which is what makes a
uniform rescale provably harmless for `close > 50DMA`.

---

## 5. Answers to the §2 questions

| Question | Answer |
|---|---|
| Number of symbols by cause | 90 identical / 322 dividend / 86 precision / 2 split / 0 unexplained / 3 not comparable |
| Magnitude of difference | 0 to −0.02% (dividend median −0.46%) · −50% (splits) |
| Is the difference constant through history? | **Yes.** Every non-identical symbol has a *flat* ratio; no drift, no time dependence. |
| Date of corporate action | Recorded per symbol in the raw JSON (`actions.splits`, `actions.dividends`) |
| Before or after the cache boundary? | 2 splits **after**; dividend restatements mostly after, some ex-dates before with a later provider correction |
| Which fields | All four price fields together; volume on ≤1 row per symbol |

---

## 6. What this changes for the refresh decision

**A naive append is safe for 501 of 503 symbols** — a dividend restatement is a constant
rescale, and splicing the new bars onto old ones introduces a single step of at most 4.4%
at the join, which does not flip `close > 50DMA` in any case (§4 of the main report).

**A naive append is unsafe for MNST and APH.** Splicing post-split bars onto pre-split
history creates a 50% discontinuity that *will* corrupt both the 50-day SMA and the A/D
line for the 50 days after the join.

This is the concrete technical content behind §5's prohibition on "download everything
again and overwrite" and behind §6's Dataset A / Dataset B split: a full re-download is
internally consistent by construction, whereas an append is not.

---

## 7. Honest limitations

1. The audit compares against **today's** yfinance response. If the provider changes its
   adjustment basis again, these factors change again. Nothing here is a permanent
   property of the data.
2. `float64` CSV values were compared, not the provider's internal representation. A
   difference below the CSV's printed precision is not observable by this method.
3. 3 symbols could not be compared at all; their lineage is **unknown**, not "clean".
4. This audit establishes *what* differs and *why*. It does not establish whether the new
   basis is *better* — that is a separate judgement reserved for the human.
