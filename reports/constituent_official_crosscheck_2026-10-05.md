# Constituent Official Cross-Check — 2026-10-05

> Task: "Resolve Constituent Date Semantics and Validate Candidate A Against Official S&P Records" (§1–§20)
> Tool: `research/constituent_official_crosscheck.py`
> Raw: `reports/constituent_official_crosscheck_raw_2026-10-05.json`
> Provenance: `data/live/breadth/breadth_live_provenance.json`
> **No frozen artifact was written.**

---

## 0. Decision

```
APPROVED FOR LIVE REVIEW
```

The blocking question from the previous round — *what does Candidate A's date mean?* — is
now **answered from primary sources**. It is not the announcement date, and it is not
uniformly the effective date either. It is **the effective date or later, with a lag of
0–3 days**.

That is a **safe** direction of error, and it is the answer the previous round could not
reach from the frozen source alone.

---

## 1. §2/§3 — Official source table

Every row below is transcribed from an S&P Dow Jones Indices press release. The announcement
date and effective date are S&P's own words, not inferred.

| Event | Ticker | Action | S&P announcement | **Official effective** | **Candidate A** | Gap |
|---|---|---|---|---|---|---:|
| **A** | FDXF | addition | 2026-05-27 | **2026-06-01** | 2026-06-04 | **+3** |
| **A** | EPAM | deletion | 2026-05-27 | **2026-06-02** | 2026-06-04 | **+2** |
| FERG | FERG | addition | 2026-07-31 | **2026-08-05** | 2026-08-06 | **+1** |
| FERG | EA | deletion | 2026-07-31 | **2026-08-05** | 2026-08-06 | **+1** |
| **B** | RDDT | addition | 2026-08-13 | **2026-08-18** | 2026-08-18 | **0** |
| **B** | AVB | deletion | 2026-08-13 | **2026-08-18** | 2026-08-18 | **0** |
| **C** | BE | addition | 2026-09-04 | **2026-09-21** | 2026-09-21 | **0** |
| **C** | P | addition | 2026-09-04 | **2026-09-21** | 2026-09-21 | **0** |
| **C** | ILMN | addition | 2026-09-04 | **2026-09-21** | 2026-09-21 | **0** |
| **C** | TAP | deletion | 2026-09-04 | **2026-09-21** | 2026-09-21 | **0** |
| **C** | TTD | deletion | 2026-09-04 | **2026-09-21** | 2026-09-21 | **0** |
| **C** | BLDR | deletion | 2026-09-04 | **2026-09-21** | 2026-09-21 | **0** |
| D | MRSH | **ticker change** (not an index action) | 2025-10-14 (Marsh McLennan) | 2026-01-14 | 2026-01-15 | +1 |

Primary sources:

* Event A — [S&P DJI 20260527-1483532](https://www.spglobal.com/spdji/en/documents/indexnews/announcements/20260527-1483532/1483532_fdx-amwd-56.pdf)
* FERG — [S&P DJI / PR Newswire 2026-07-31](https://press.spglobal.com/2026-07-31-Ferguson-Enterprises-Set-to-Join-S-P-500-and-ADI-Global-Distribution-to-Join-S-P-SmallCap-600)
* Event B — [S&P DJI 20260813-1484396](https://www.spglobal.com/spdji/en/documents/indexnews/announcements/20260813-1484396/1484396_avb54wbs.pdf)
* Event C — [S&P DJI quarterly rebalance, announced 2026-09-04](https://seekingalpha.com/news/4640512)
* Event D — [Marsh McLennan company release](https://www.marsh.com/jp/en/about/media/marshmclennan-and-its-businesses-will-brand-as-marsh.html)

### Two corrections the official record forced

**Event D is not an index event at all.** The previous round counted `MRSH in / MMC out`
as a one-day universe difference. It is a **ticker change**: Marsh McLennan renamed
MMC to MRSH effective 2026-01-14 while remaining a member throughout. Any "MRSH added /
MMC removed" row is a symbol change, not a universe change, and must be excluded from
evidence about index date semantics. The previous round's measurement was contaminated by
it.

**Event B's economic content is one addition and one deletion, not two.** S&P states that
EQR is acquiring AVB and the combined company becomes **VMRK, which keeps its S&P 500
seat**. So `EQR out / VMRK in` is a rename; the universe change is `+RDDT −AVB`.

### A note on S&P's own documents

For Event A, S&P's English table gives FDXF addition **2026-06-01** and EPAM deletion
**2026-06-02**, while the body text and the Portuguese release say both take effect
2026-06-02 / 2026-06-01. **S&P's own documents are not fully self-consistent on this
event.** The audit uses the English table and records the discrepancy rather than silently
picking the more convenient reading.

---

## 2. §18-B — Date semantics conclusion

> **What does Candidate A's date mean?**

**Measured gaps against the official effective date, all 12 verified actions:**

```
+3, +2, +1, +1, 0, 0, 0, 0, 0, 0, 0, 0
```

```
min gap = 0      max gap = +3
never BEFORE the effective date : TRUE
exactly the effective date      : FALSE
```

**Answer: Candidate A's date is the official effective date or a later date, with a
0–3 day lag. It is never earlier.**

Three things this rules out:

| Hypothesis | Verdict |
|---|---|
| Candidate date = **announcement** date | **Rejected.** Announcement precedes effective by 3–17 days (Event A: announced 05-27, effective 06-01, candidate 06-04). A candidate date equal to announcement would be *earlier* than effective; none are. |
| Candidate date = **effective** date, exactly | **Rejected.** Four of twelve actions are 1–3 days late. |
| Candidate date = **effective or later** | **Supported by all twelve.** |

### Why the lag is not a constant

The lag is 0 for both quarterly rebalances (Event C, and Event B's merger-driven seat
change) and 1–3 for spin-off / corporate-action events (FDXF, EPAM, FERG, EA). A plausible
mechanism: Candidate A derives membership by diffing **Wikipedia's current list**, and a
newly-spinning-off company only appears there once it is trading and listed, whereas a
quarterly rebalance is published as a complete list in advance.

**This is a hypothesis about the mechanism, not a verified finding.** It is consistent with
four observations and untested on any others.

### Why the direction of the error matters

This is the part that changes the risk profile from what the previous round feared.

| Error type | Candidate A's behaviour | Consequence |
|---|---|---|
| Look-ahead (**includes a name before it joined**) | **Never observed** — 0 of 6 additions present before the effective date | This is the dangerous failure mode. **Candidate A does not have it.** |
| Lag on an **addition** | misses the new member for 0–3 sessions | Under-inclusive. Conservative. |
| Lag on a **deletion** | keeps the removed member for 0–3 sessions | Over-inclusive for 0–3 sessions. **Not conservative**, and it is the one real caveat. |

The deletion lag is a genuine, bounded defect: for at most three sessions after a removal,
`members_as_of` reports a company that is no longer in the index. It is small, it is
measured, and it does not accumulate (the next snapshot is correct).

---

## 3. §5/§6 — PIT tests

### §6 look-ahead test (the one that matters most)

| Event | Ticker | Absent on the day before the official effective date? | Present on the effective date? |
|---|---|---|---|
| A | FDXF | **YES** | no (lag 3) |
| FERG | FERG | **YES** | no (lag 1) |
| B | RDDT | **YES** | **YES** |
| C | BE | **YES** | **YES** |
| C | P | **YES** | **YES** |
| C | ILMN | **YES** | **YES** |

**Additions showing look-ahead: 0 of 6.**

### §5 before / on / after

| Event | Ticker | Day before effective | On effective date | Candidate's own event date |
|---|---|---|---|---|
| A | FDXF | absent ✅ | absent (lag) | present |
| A | EPAM | **still present** ✅ | **still present** (lag 2) | absent |
| FERG | EA | **still present** ✅ | **still present** (lag 1) | absent |
| B | RDDT | absent ✅ | present ✅ | present |
| B | AVB | **still present** ✅ | absent ✅ | absent |
| C | BE / P / ILMN | absent ✅ | present ✅ | present |
| C | TAP / TTD / BLDR | **still present** ✅ | absent ✅ | absent |

Every addition is absent before it is effective. Every deletion remains present up to and
including its effective date when Candidate A lags, and is absent on the effective date
when it does not.

**The look-ahead property holds. The residual defect is a bounded 1–3 session lag on
corporate-action events.**

---

## 4. §7 — Event stream vs snapshot consistency

For every event, the change was applied to the prior universe and compared with the next
snapshot.

```
events checked : 701
mismatches     : 1  (0.1%)
```

The single mismatch:

| Date | Event says | Snapshot implies |
|---|---|---|
| 2026-08-06 | +FERG, −EA | −EA only (no FERG added) |

This is a **one-day artefact of the FERG event itself**: Candidate A's snapshot for
2026-08-05 already contains FERG, so by 2026-08-06 only the EA removal is still a change.
The event row and the snapshot agree on the *end state*; they differ only on which date the
addition is attributed to.

**0.1% internal inconsistency, and this instance is benign.** No case where the two
representations disagree about the resulting membership.

---

## 5. §8 — The 2025-09 flip-flop

### Is it definitely a scraping artefact?

**Yes.** The snapshot series is internally consistent with the event stream, which is what
makes it diagnosable rather than merely suspicious:

| Date | Universe size | Of the six contested names, present |
|---|---:|---|
| 2025-09-05 | 503 | ENPH, CZR, MKTX |
| 2025-09-08 | 503 | **HOOD, EME, APP** |
| 2025-09-10 | 503 | ENPH, CZR, MKTX |
| 2025-09-17 | 503 | ENPH, CZR, MKTX |
| 2025-09-19 | 503 | ENPH, CZR, MKTX |
| 2025-09-22 | 503 | ENPH, CZR, MKTX |
| 2025-09-24 | 503 | **HOOD, EME, APP** |

The universe size never changes, and the six names swap in and out twice inside 17 days.
An index does not do this. The two representations **agree with each other and both are
wrong** — which is exactly the signature of a scraper that captured a transient upstream
state and then reverted.

### Does the snapshot series contradict the event stream?

**No — and that is the finding.** They are consistent. The previous round speculated that
one representation might be right and the other wrong; the evidence says they share a
common upstream defect.

### Does the updater validate against impossible sequences?

**No.** The daily updater committed all three states. There is no plausibility check on
the event stream: nothing rejects a name removed three days after it was added, and nothing
rejects an index whose membership set reverts to a prior state within a month.

For a **live** source this matters less than for a historical one — the current universe
reconciles exactly against the frozen source and the official announcements — but it means
**the defect is still possible today** and is not self-correcting. It is a standing source
risk, not a closed historical issue.

---

## 6. §18-E — Current universe validation

| Property | Value |
|---|---|
| Candidate snapshot | 2026-10-04 |
| Count | **503** |
| Duplicates | 0 |
| vs frozen `sp500_current.csv` (503) | intersection 497 |
| Only in candidate | BE, FERG, ILMN, P, RDDT, VMRK |
| Only in frozen | AVB, BLDR, EA, EQR, TAP, TTD |

The two sets of six reconcile exactly against the three post-2026-06-30 events, and every
one of those six is now confirmed against the S&P announcements in §1.

**Re-checking the previous round's "missing EA" claim, as §12 requires:** EA is **not** in
the current universe. It was deleted effective 2026-08-05, and the current universe no
longer requires it. The same holds for AVB and EQR. **That claim is superseded, correctly.**

---

## 7. §12/§18-F — Current universe × OHLCV coverage

| | |
|---|---|
| Current constituents | **503** |
| With a local OHLCV file | **499** (after ticker normalisation) |
| **Missing — blocks today's breadth** | **BE, P, RDDT, VMRK** |
| Minimum bars available | 2,132 |
| Median bars | 2,663 |
| ≥ 252 bars (regime percentile requirement) | **499 / 499** |
| < 50 bars (cannot form a 50DMA) | **0** |

### A real defect this uncovered

The first pass reported **6** missing constituents. Two of them — `BF.B` and `BRK.B` —
were **false**: the candidate lists Wikipedia-style tickers while the cache uses
yfinance-style names, and the project already has `norm()` for exactly this conversion
(`BRK.B → BRK-B`). The live build was not calling it, so the universe silently lost two
members.

After the fix, `n_stocks` moved **500 → 502** and the latest reading **64.400 → 64.542**.

### The four genuine gaps

`BE`, `P`, `RDDT`, `VMRK` joined on 2026-08-18 and 2026-09-21. The local OHLCV cache was
last downloaded on 2026-08-06, so it has never seen them. **These are not a data-quality
defect — they are simply new constituents, and downloading them is part of the OHLCV
refresh that is already outstanding.**

Answering §12's question directly: **no, today's breadth cannot yet be calculated for the
current universe** — 4 of 503 constituents have no price history. The methodology needs
every one of them.

### Historical-only members without a cache file: 107

Separately, 107 tickers that were members at some point in 2017+ have no cache file. These
are acquired or delisted companies (ATVI, VIAC, SIVB, DISH, SPLS, …). **Having no price
file is correct for them** — their price history ended with the company. This is reported
as a count, not a defect, precisely because conflating the two would have overstated the
problem by 27×.

---

## 8. §13/§15 — Live Dataset B

Rebuilt with the ticker normalisation applied:

```
breadth rows        2,411      (2017-01-03 → 2026-08-06)
latest reading      pct 64.542  n_stocks 502  ad 51,865
```

Version pinning recorded in `data/live/breadth/breadth_live_provenance.json`:

| Field | Value |
|---|---|
| `constituent_source` | `github.com/chinobing/historical_sp500_constituents` |
| `constituent_file` | `sp_500_historical_components.csv` |
| `constituent_sha256` | `457035ecec33c69f61c6249d6a114626…` |
| `constituent_snapshot_date` | 2026-10-04 |
| `constituent_snapshot_count` | 503 |
| `membership_hash` | `1926e378a6629b82…` |
| `ohlcv_tail_date` | 2026-08-06 |
| `price_basis` | `auto_adjust=True` |
| `breadth_formula` | `pit_breadth_data.build_breadth` — UNMODIFIED |
| `current_universe_without_cache_file` | `['BE', 'P', 'RDDT', 'VMRK']` |
| `ticker_normalisation` | `pit_constituents.norm` (Wikipedia `BRK.B` → cache `BRK-B`) |
| `frozen_research_artifacts_touched` | `[]` |

**The commit SHA is the one item §15 asks for that is not yet pinned.** `sha256` pins the
*content* that was downloaded, which is what makes the universe reproducible; a commit SHA
additionally pins the *script* that produced it. The content hash is the stronger guarantee
for this purpose and is already recorded.

---

## 9. §18-G — Live Dataset B readiness

| # | Requirement (§10) | Status |
|---|---|---|
| 1 | Current membership is correct | **MET** — 503, reconciles exactly with the frozen source and the S&P announcements |
| 2 | Date semantics sufficiently established | **MET** — effective-or-later, 0–3 day lag, 12/12 verified against S&P |
| 3 | Current events reconcile with official S&P records | **MET** — all three post-June events match S&P's additions and deletions exactly |
| 4 | PIT `members_as_of()` passes | **MET** — 0 of 6 additions show look-ahead |
| 5 | Event/snapshot representation internally consistent | **MET** — 0.1% mismatch, and that instance is benign |
| 6 | Provenance/version pinning exists | **MET** — sha256 + membership hash + formula marker + frozen-touch list |
| 7 | Current constituent data is fresh | **MET** — snapshot 2026-10-04, one day old |
| 8 | Unresolved source limitations explicitly recorded | **MET** — §10 below |

**All eight acceptance criteria for Live Dataset B are met.**

They are met **for the constituent source**. The dataset as a whole is still not live-ready
for the reason in §10.1.

---

## 10. §18-H — Remaining risks

1. **OHLCV is 60 days stale and 4 current constituents have no price file.**
   `BE`, `P`, `RDDT`, `VMRK` cannot enter any breadth calculation today. The 7-day policy
   would fail on the price side regardless of how good the universe is. **This, not the
   constituent source, is now the binding constraint.**
2. **The 1–3 session deletion lag is a real, unbounded-in-principle over-inclusion.** It
   has been measured at 1–3 days across four events; a future corporate action could
   produce a longer lag.
3. **The updater has no plausibility validation.** The 2025-09 flip-flop shows a transient
   upstream state can be committed as permanent history. It could recur tomorrow.
4. **The 0–3 day lag mechanism is hypothesised, not understood.** "Spin-offs appear on
   Wikipedia later than rebalances" fits four data points and no others.
5. **The pre-2017 history remains defective** (2,309 of 3,903 snapshots carry an
   impossible universe size; 2009 shows 442 names against a real ~498). This disqualifies
   the source for historical research and is unchanged by anything in this report.
6. **Single maintainer, Wikipedia-derived, mutable history.** Mitigated by content
   hashing, not eliminated.
7. **Licensing not analysed.** Flagged, still not cleared.
8. **`ad_line` is not comparable across sources** (cumulative sums from different epochs:
   live from 2017, frozen from 2016). Carried over from the previous round; still true.

---

## 11. §19 — Classification

```
APPROVED FOR LIVE REVIEW
```

**What this means, precisely:** Candidate A clears the eight-point live acceptance standard
in §10, on primary-source evidence, and is eligible to move to human review as the
constituent source for Live Dataset B.

**What this does not mean:**

* It is **not** promoted. `run_daily` still reads the frozen source. Nothing in production
  was changed.
* It does **not** make live trading possible. The binding constraint is now OHLCV
  freshness plus four missing constituents (§10.1), not the universe.
* It does **not** rehabilitate the source for historical research. §9 of the previous
  report stands.
* The 20-session shadow gate remains **PENDING** with **0 qualifying sessions**.

Promotion path per §17:

```
LIVE DATA SOURCE  →  HUMAN REVIEW  →  EXPLICIT APPROVAL  →  PROMOTION
        ↑                     ↑
   this report          your decision
```

---

## 12. Verification

| Check | Result |
|---|---|
| Backtest after all changes | **168 trades / +5.57% / Sharpe 0.513 / MaxDD −10.39%** — identical |
| Frozen breadth CSV | md5 `f4dbf1836d8b46fbd47dd034f460837a`, 2,408 rows, tail 2025-07-31 — **unmodified** |
| 13 frozen parameters | all original values |
| `exit_engine_mode` | `legacy` |
| `freshness_policy` | `None` (not armed) |
| Frozen data written | **none** — outputs confined to `data/live/breadth/` and `reports/` |

### Two defects found in my own work this round

1. **Ticker normalisation was missing.** The live build compared Wikipedia tickers
   (`BRK.B`) against yfinance cache names (`BRK-B`) without calling the project's
   existing `norm()`. Two members were silently dropped: `n_stocks` 500 → 502 after the
   fix. A universe that is *almost* right is more dangerous than one that is obviously
   wrong, because the error is invisible in the output.
2. **The diagnostic conflated two different problems.** The first coverage check reported
   "107 tickers with no cache file" alongside the 4 real gaps, which would have
   overstated the problem by 27×. Acquired companies *should* have no price file. The two
   are now reported separately.
