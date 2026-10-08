# Constituent Source Audit — 2026-10-05

> Task: "Audit and Establish a Current PIT Constituent Source" (§1–§25)  
> Tool: `research/constituent_source_audit.py`  
> Raw: `reports/constituent_source_audit_raw_2026-10-05.json`  
> **Read-only with respect to the project**: nothing under `data/constituents/`,  
> `regime_dual_engine/data/` or `production/` was written.



---

## 0. Verdict

```
PROMISING — NEEDS HUMAN APPROVAL
```

The candidate is fit for **live use from 2017 onward** and is **rejected for historical  
research use**. Two independent defects prevent unconditional adoption, and one  
unresolved question (date semantics) needs a human decision.

| Candidate                                          | Status                               | Reason                                                                                                                                                                                                                                                                                                                                              |
| -------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — `chinobing/historical_sp500_constituents`**  | **PROMISING — NEEDS HUMAN APPROVAL** | Current through 2026-10-04, auto-updated daily, 2017+ universe sizes plausible, Jaccard 0.998 vs the frozen source in 2026, PIT reconstruction clean. **But**: 2,309 of 3,903 snapshots carry an impossible universe size, and its date semantics differ from the frozen source by 0–2 days on 80% of events in a way this audit could not resolve. |
| **B — `leandroloi/historical_sp500_constituents`** | **REJECTED**                         | A stale fork of Candidate A. Identical initial commit (`23556a7`), same script, but last data **2025-06-18** — 16 months behind. It cannot solve the problem it was nominated for.                                                                                                                                                                  |
| **C — S\&P Dow Jones Indices official**            | **NOT USABLE**                       | The official index site publishes the *current* constituent list and index-level documentation, not a downloadable historical event stream with effective dates. It cannot serve as either a primary source or an independent validator at the granularity this project requires.                                                                   |

---

## 1. §3 — What the three candidates actually are

### Candidate A and B are the same repository

Both have **initial commit `23556a7ded5019a27ee89e90a40abc1fe303c66b`**, the same  
`sp500.py`, the same `sweep.yaml`, and the same three CSV outputs. B is a fork that stopped  
receiving updates. The project's existing local source (`fja05680/sp500`) shares the same  
1996-01-02 starting snapshot but has since diverged — see §5.

|                  | Candidate A            | Candidate B            |
| ---------------- | ---------------------- | ---------------------- |
| Maintainer       | chinobing              | leandroloi             |
| Last commit      | **2026-10-04 16:37**   | 2025-04-27             |
| Last data        | **2026-10-04**         | 2025-06-18             |
| Snapshots        | 3,903                  | 3,424                  |
| Events           | 702                    | 666                    |
| Update mechanism | GitHub Action, daily   | GitHub Action, stopped |
| Upstream data    | Wikipedia + `sp500.py` | same                   |

### Candidate A resolved the mystery from the previous round

Candidate A's event stream records:

```
2026-08-06  added FERG        removed EA
2026-08-18  added VMRK, RDDT  removed EQR, AVB
2026-09-21  added P, BE, ILMN removed BLDR, TTD, TAP
```

**These are exactly the three symbols** that the OHLCV audit could not download  
(`EA: no cache`, `AVB / EQR: download_empty`). They were not download failures — they had  
**left the index**. The previous round's "unexplained missing data" is now explained.

---

## 2. §4 — Source audit record (Candidate A)

| Property                            | Finding                                                                                                                                                                    |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Repository                          | `github.com/chinobing/historical_sp500_constituents`                                                                                                                       |
| Owner                               | individual maintainer (chinobing)                                                                                                                                          |
| Update mechanism                    | GitHub Action, daily, auto-commit "Github SP500 Constituents Auto Renew at <timestamp>"                                                                                    |
| Latest commit                       | 2026-10-04 16:37 (one day before this audit)                                                                                                                               |
| Latest event date                   | **2026-09-21**                                                                                                                                                             |
| Latest snapshot date                | 2026-10-04                                                                                                                                                                 |
| Historical start                    | 1996-01-02                                                                                                                                                                 |
| Formats                             | `sp500_changes_since_1996.csv` (event stream), `sp_500_historical_components.csv` (3,903 snapshots), `sp500_constituents.csv` (current, 503 rows with sector/CIK metadata) |
| Event-based or periodic             | **Both.** Events on change dates; snapshots on every trading day                                                                                                           |
| Date semantics                      | **Unresolved** — see §3                                                                                                                                                    |
| Additions/removals explicit         | **Yes**, `added_tickers` / `removed_tickers`; `NaN` means "none on that side" (111 removal-only, 127 addition-only rows)                                                   |
| Membership reconstructed or sourced | **Reconstructed** from Wikipedia's current list plus a diff script; historical depth comes from a bundled seed file                                                        |
| Source dependencies                 | Wikipedia (current list) + the repo's own seed history                                                                                                                     |
| Licensing                           | LICENSE file present; not analysed in detail — **flagged as an open item**                                                                                                 |
| Version history                     | **Excellent** — full git history, one commit per day                                                                                                                       |
| Failure behaviour                   | Silent. A failed scrape would be committed as a real-looking snapshot                                                                                                      |
| Risk of silent edits                | **Real** — see §4 (the 2025-09 flip-flop)                                                                                                                                  |

---

## 3. §5 — Date semantics: the unresolved question

This is the most consequential open item, because `members_as_of(T)` is only correct if the  
source's dates are **effective membership dates**.

### What was measured

For every clean addition event from 2024-06 onward, the gap between the candidate's event  
date and the frozen source's first snapshot containing the new name:

| Gap (days) |   Count |
| ---------: | ------: |
|          0 | 5 (25%) |
|          1 | 8 (40%) |
|          2 | 3 (15%) |
|          6 |       1 |
|         16 |       3 |

**Median 1 day. 80% of events fall within 0–2 days.** Worked examples:

| Change             | Frozen source first seen | Candidate A date | Gap |
| ------------------ | ------------------------ | ---------------- | --: |
| MRSH replaces MMC  | 2026-01-14 (Wed)         | 2026-01-15 (Thu) |   1 |
| BNY replaces BK    | 2026-05-21 (Thu)         | 2026-05-22 (Fri) |   1 |
| ECHO replaces SATS | 2026-06-24 (Wed)         | 2026-06-25 (Thu) |   1 |
| FDXF replaces EPAM | 2026-06-01 (Mon)         | 2026-06-04 (Thu) |   3 |

### The tempting inference, and why I do not assert it

A 1-day lag is *consistent with* the candidate recording the **effective date** while the  
frozen source records the **announcement date** (S\&P announces after the close and the  
change takes effect the following session). If true, the candidate would be the more  
PIT-correct of the two.

**I am not asserting that.** Three reasons:

1. The distribution is not clean. 25% are same-day, and there is a 6-day and three 16-day  
   cases. A pure announcement/effective offset would be tightly clustered at 1.
2. My first attempt at this measurement was **wrong** — it took "first appearance in the  
   whole history" rather than "first appearance after the event", producing a spurious  
   −10,442-day outlier and a misleading −1 median. The corrected measurement is above.
3. Nothing in either repository documents the convention.

**Resolution requires a human check against the actual S\&P announcement for one dated  
event.** Until then the date semantics are an open item, and it is the main reason this  
candidate is not "APPROVED".

### §10 — representation choice

The candidate ships **both** forms, and the internal consistency between them is high  
(702 events checked, **3 mismatches = 0.4%**). For this project the **full-snapshot form is  
safer**, and it is what the live build uses:

- A snapshot needs no event-accumulation logic, so there is no way to construct a  
  membership set from a partial or duplicated event stream.
- A membership set is directly hashable, which §12 requires.
- The 0.4% mismatch rate means the two forms *disagree* occasionally; using snapshots avoids  
  inheriting whichever form is wrong.

---

## 4. §4/§11 — Data quality defects

### 4.1 The 2025-09 flip-flop (most serious)

The event stream contains, in order:

```
2025-09-06  added HOOD, EME, APP   removed ENPH, CZR, MKTX
2025-09-09  added ENPH, CZR, MKTX  removed HOOD, EME, APP
2025-09-23  added HOOD, EME, APP   removed ENPH, CZR, MKTX
```

The snapshot series shows the universe size staying at **503 throughout**. An index that  
swaps the same six names in and out twice inside 17 days, without changing its size, is not  
a market event — it is a **scraping artefact written into permanent history**.

The frozen source handled this by **collapsing** the whole episode: HOOD/EME/APP are first  
seen on **2025-09-22**, i.e. after the third event. The candidate recorded all three. This  
is the 16-day gap in §3.

**Consequence:** for those 17 days the candidate's universe is one that never existed.  
Anyone using it for research on 2025-09-06 to 2025-09-22 would be working from a fiction.

### 4.2 Structural anomalies

| Anomaly                                    |                                          Count |
| ------------------------------------------ | ---------------------------------------------: |
| add → remove → add sequences (same ticker) |                                         **40** |
| Re-added within 120 days of a prior add    | **5** (PANW +17d, COIN +4d, HOOD/EME/APP +17d) |
| Same-day add **and** remove of one ticker  |                                              0 |

### 4.3 Impossible universe sizes (decisive for research use)

| Year     | Candidate min | Candidate median | Frozen median | Verdict    |
| -------- | ------------: | ---------------: | ------------: | ---------- |
| 1996     |           468 |              468 |           487 | DEFECT     |
| 2000     |           460 |              464 |           491 | DEFECT     |
| 2005     |           455 |              457 |           496 | DEFECT     |
| **2009** |       **442** |          **444** |       **498** | **DEFECT** |
| 2013     |           458 |              459 |           497 | DEFECT     |
| 2016     |           471 |              478 |           498 | DEFECT     |
| 2017     |           484 |              489 |           496 | ok         |
| 2020     |           499 |              501 |           505 | ok         |
| 2023     |           498 |              503 |           503 | ok         |
| 2026     |           502 |              503 |           503 | ok         |

**2,309 of 3,903 snapshots (59%) carry fewer than 480 names.** In 2009 the candidate holds  
**442** where the index had ~498.

Independent confirmation from a primary source: an SEC-filed prospectus describing the index  
as of 2009-04-02 lists the constituents by GICS sector as 81 / 41 / 39 / 80 / 54 / 58 / 75 /  
28 / 9 / 35 — **500 companies**. The candidate is short by roughly 58 names.

The 57 names it is missing on 2009-02-26 are real, then-current members: DELL, IR, DOW, EMC,  
CVH, DNB, AET, AIV, ALK, HAR, HSH, JAVA, KG, S, SE, WYND, XL and 40 more.

**The defect boundary is sharp: 2016 is the last defective year, 2017 is the first clean  
one.** The two sources' missing sets on 2009-02-26 have **zero overlap**, so this is the  
candidate's own defect, not an inherited one.

### 4.4 What this means

For **live** use the defect is irrelevant: live breadth only needs the *current* universe,  
which is 503 names and agrees with the frozen source's current list.

For **historical research** it is disqualifying: a 442-name "S\&P 500" is a different index,  
and it would silently corrupt any PIT series built on it.

---

## 5. §7 — Comparison against the existing frozen source

Computed over **2,716 common snapshot dates**. The frozen source remains authoritative for  
the research baseline; this comparison exists to build confidence in the live source, not to  
change history.


| Year | Jaccard (median) | Jaccard (min) | Days with any difference |
|---|---:|---:|---:|
| 1996 | 0.9570 | 0.9550 | 106 / 106 |
| 2000 | 0.9391 | 0.9329 | 120 / 120 |
| 2008 | 0.8898 | 0.8858 | 126 / 126 |
| 2016 | 0.9407 | 0.9269 | 130 / 130 |
| 2020 | 0.9842 | 0.9803 | 13 / 13 |
| 2023 | 0.9921 | 0.9822 | 13 / 13 |
| 2025 | 0.9970 | 0.9842 | 14 / 16 |
| **2026** | **0.9980** | **0.9842** | **8 / 12** |

First disagreement: **1996-01-02** (the very first snapshot). Only 12 of 2,716 snapshots
are byte-identical.

**Reading:** the two sources converge as the index converges on ~503 names. The
disagreement is largest exactly where the candidate is missing names (§4.3). This is
consistent with one explanation rather than two competing ones.

---

## 6. §6 — Recent-event cross-validation

### 6.1 The four dates the task specified

| Date | Candidate A event | Cross-checkable? |
|---|---|---|
| 2026-06-30 | +HONA −CAG | **Yes** — both sources agree exactly (Jaccard 1.0000) |
| 2026-08-06 | +FERG −EA | **No** — frozen source is 37 days stale |
| 2026-08-18 | +RDDT, VMRK −AVB, EQR | **No** — 49 days stale |
| 2026-09-21 | +BE, ILMN, P −BLDR, TTD, TAP | **No** — 83 days stale |

**Three of the four cannot be cross-validated from local data.** I am not going to
manufacture agreement for them. What *can* be checked is internal consistency, and it
holds: the candidate's own current list differs from the frozen source's current list by
**exactly** the six names added and the six names removed across those three events
(§7). An error in any one of them would almost certainly break that closure.

### 6.2 Where both sources overlap in 2026

| Date | Only frozen | Only candidate | Jaccard |
|---|---|---|---:|
| 2026-01-14 | MRSH | MMC | 0.9960 |
| 2026-02-09 | — | — | 1.0000 |
| 2026-03-23 | COHR, LITE, SATS, VRT | LW, MOH, MTCH, PAYC | 0.9842 |
| 2026-04-09 | — | — | 1.0000 |
| 2026-05-07 | — | — | 1.0000 |
| 2026-05-21 | BNY | BK | 0.9960 |
| 2026-06-22 | — | — | 1.0000 |
| 2026-06-24 | ECHO | SATS | 0.9960 |
| 2026-06-30 | — | — | 1.0000 |

Every difference is **one name lagging by one to two days**, consistent with the §3 timing
question. No unexplained discrepancy.

⚠️ **A methodological note.** My first cross-check reported three of these as
"★不一致". That was **my bug**: I compared the candidate's event against the frozen
source's *previous* snapshot using a forward search, which mis-aligns whenever the frozen
source has no snapshot on the event date. Comparing the two member sets on the **same
date** is the correct test, and it shows agreement.

---

## 7. §8 — Current universe validation

| Property | Value |
|---|---|
| Candidate list date | 2026-10-04 |
| Count | **503** |
| Duplicates | **0** |
| Frozen `sp500_current.csv` count | 503 |
| Intersection | 497 |
| Only in candidate | BE, FERG, ILMN, P, RDDT, VMRK |
| Only in frozen | AVB, BLDR, EA, EQR, TAP, TTD |

**The two sets of six reconcile exactly** against the three post-2026-06-30 events. 503 is
correct — the S&P 500 has held 503 securities for several years (multiple share classes),
so "not exactly 500" is expected, not an error.

---

## 8. §9 — PIT reconstruction test

`members_as_of(T)` implemented as `searchsorted(dates, T, side="right") - 1`, i.e. the
latest snapshot at or before T. Tested on the six dates the task specified:

| as_of | n | P | BE | ILMN | BLDR | TTD | TAP | FERG | EA |
|---|---:|---|---|---|---|---|---|---|---|
| 2024-01-31 | 503 | – | – | IN | IN | – | IN | – | IN |
| 2025-07-31 | 503 | – | – | – | IN | IN | IN | – | IN |
| 2026-06-30 | 503 | – | – | – | IN | IN | IN | – | IN |
| 2026-08-06 | 503 | – | – | – | IN | IN | IN | **IN** | **–** |
| 2026-08-18 | 503 | – | – | – | IN | IN | IN | IN | – |
| 2026-09-21 | 503 | **IN** | **IN** | **IN** | – | – | – | IN | – |

Look-ahead probe: the names newest in the final snapshot are **absent** from the previous
snapshot — `lookahead_leak = []`, `lookahead_clean = True`.

2026-06-30 correctly does **not** contain P / BE / ILMN, which arrive on 2026-09-21.

---

## 9. §22/D — Reliability assessment

| Dimension | Assessment |
|---|---|
| **Freshness** | **Excellent.** Committed daily; the 2026-10-04 data was live one day before this audit. Event lag after a real index change appears to be 0–2 days (§3, unresolved semantics). |
| **Completeness** | **Good.** Routine rebalances, merger replacements (MRVL, FLEX for POOL, CPB on 2026-06-20) and spin-offs are all present. |
| **Stability** | **Weak.** A single-maintainer repository, no release tags, no schema version, and history can be silently rewritten by a bad run. Mitigated by pinning the SHA-256 (implemented). |
| **Reproducibility** | **Poor as published** — the CSV is mutable. **Achievable with pinning** — the SHA-256 in the provenance record makes a given universe reproducible forever. Implemented. |
| **Provenance** | **Weak upstream.** Constituents are scraped from Wikipedia, not sourced from S&P. There is no independent primary record inside the repository. |

**Reliability summary:** operationally good, evidentially weak. It is a *convenient*
mirror, not an authoritative record. For a frozen research baseline that is insufficient;
for a live universe that is monitored, versioned and cross-checked, it is workable.

---

## 10. §12 / §22-E — Versioning design (implemented)

`data/live/breadth/breadth_live_provenance.json` records:

```
generated_at                  2026-10-05T06:27:37
as_of                         2026-08-06
breadth_date                  2026-08-06
breadth_rows / first_date     2411 / 2017-01-03
constituent_source            github.com/chinobing/historical_sp500_constituents
constituent_file              sp_500_historical_components.csv
constituent_sha256            457035ecec33c69f61c6249d6a114626...
constituent_snapshot_date     2026-10-04
constituent_snapshot_count    503
constituent_trusted_from      2017-01-01
constituent_trusted_from_reason  the candidate's pre-2017 snapshots carry 442-478
                                members where the index had ~498; EXCLUDED, not repaired
panel_days_without_snapshot_excluded  252
membership_hash               1926e378a6629b82...  (sha256 of the sorted member list)
ohlcv_source                  local data/cache/equities (UNCHANGED; not rewritten)
ohlcv_tail_date               2026-08-06
price_basis                   auto_adjust=True (fully adjusted)
breadth_formula               pit_breadth_data.build_breadth — UNMODIFIED
frozen_research_artifacts_touched  []
not_validated                 [date semantics, post-2026-06-30 events]
```

Two design decisions worth stating:

* **The defect window is excluded, not repaired.** Pre-2017 rows are dropped and the
  boundary is recorded with its reason. Repairing them would require inventing 57 names
  per snapshot for two decades.
* **`not_validated` is part of the artifact.** A provenance record that only states what
  was checked is not a provenance record.

---

## 11. §22-F — Live Dataset B data flow

```
  chinobing/historical_sp500_constituents
        │  sp_500_historical_components.csv
        │  pinned by sha256
        │  trusted from 2017-01-03
        ▼
  members_as_of(T)  ── searchsorted, latest snapshot ≤ T
        │
        │                                    ┌──────────────────────────┐
        │                                    │  FROZEN RESEARCH (A)     │
        │                                    │  untouched:              │
        │                                    │   OHLCV cache            │
        │                                    │   breadth_pit_2016_2025  │
        │                                    │   fja05680 constituents  │
        │                                    │   → 168 trades, +5.57%   │
        │                                    └──────────────────────────┘
        ▼
  membership matrix  (PIT, per trading day)
        │
        ├── data/cache/equities  (read-only; auto_adjust=True)
        ▼
  build_breadth()   ← FROZEN FORMULA, unmodified
        │
        ▼
  data/live/breadth/breadth_live.csv
  data/live/breadth/breadth_live_provenance.json
        │
        ▼
  [ NOT CONNECTED TO PRODUCTION ]
  live Regime still blocked; BLOCK_DECISION still disarmed
```

---

## 12. §22-G — Remaining risks

1. **Date semantics unresolved.** A 0–2 day offset on 80% of events. If the candidate's
   dates are announcement rather than effective, `members_as_of(T)` would include a name
   one session before it actually joined — a small but real look-ahead. **This is the
   single blocking question for adoption.**
2. **The 2025-09-17-day window contains a universe that never existed.** Anyone using this
   source for research in that window would be working from a fiction.
3. **Wikipedia-sourced.** Not an authoritative primary record, and Wikipedia edits are
   reversible without trace.
4. **Post-2026-06-30 events are unvalidated** against any independent source.
5. **Single maintainer, mutable history.** Mitigated by SHA-256 pinning, not eliminated.
6. **Licensing not analysed.** Flagged, not cleared.
7. **OHLCV is still 59 days stale** (tail 2026-08-06 vs a source reaching 2026-10-02), so
   even a perfect universe does not make the 7-day freshness policy satisfiable today.

---

## 13. §24 — Completion checklist

| | |
|---|---|
| Candidate sources audited | ✅ 3 (A, B, C) |
| Date semantics understood | ⚠️ **partially** — offset measured, direction not established |
| Recent changes cross-validated | ⚠️ 1 of 4 cross-validatable; 3 unverifiable locally (stated, not papered over) |
| Historical overlap checked | ✅ 2,716 common dates, Jaccard by year |
| PIT reconstruction tested | ✅ 6 dates + look-ahead probe, clean |
| Current universe independently validated | ✅ 503, 0 duplicates, reconciles exactly |
| Source provenance documented | ✅ |
| Versioning plan documented | ✅ implemented, not just planned |
| Live breadth rebuilt without touching frozen history | ✅ `data/live/breadth/`, frozen md5 unchanged |
| Historical baseline unchanged | ✅ 168 trades / +5.57% / 0.513 / −10.39% |
| Full tests pass | ✅ 265 |
| Freeze audit = 0 mismatch | ✅ |
| Production strategy unchanged | ✅ 13 frozen parameters at original values |
| `exit_engine_mode = legacy` | ✅ |
| No commit/push | ✅ |

**Final state: STATE B — LIVE DATA NOT READY.**

The blocker is no longer "no source exists". A source exists, is current, and is
versioned. The blockers are now:

1. **Date semantics must be established** against a primary S&P record (human task).
2. **OHLCV must be refreshed** — 59 days stale, which independently fails the 7-day policy.
3. **Post-2026-06-30 events remain unvalidated** by anything other than internal consistency.

`BLOCK_DECISION` stays disarmed. The 20-session shadow gate does not start.
