# Live Constituent Source Validation — 2026-10-05

> Companion to `reports/constituent_source_audit_2026-10-05.md`
> Artifacts: `data/live/breadth/breadth_live.csv`, `data/live/breadth/breadth_live_provenance.json`
> Tools: `research/{live_breadth_build,live_breadth_crosscheck}.py`
> Cross-check: `reports/live_breadth_crosscheck_2026-10-05.json`

This report covers §14–§16: what was built, what it says, and what it does **not** claim.

---

## A. §14 — The rebuild

```
constituents : chinobing/historical_sp500_constituents
               sp_500_historical_components.csv
               sha256 457035ecec33c69f61c6249d6a114626…
universe     : members_as_of(T) = latest snapshot ≤ T  (searchsorted, right-1)
trusted from : 2017-01-03
prices       : data/cache/equities  (READ-ONLY, auto_adjust=True)
formula      : pit_breadth_data.build_breadth  (UNMODIFIED)
as_of        : 2026-08-06
```

| | |
|---|---|
| Output rows | **2,411** (2017-01-03 → 2026-08-06) |
| Latest reading | `pct_above_50dma` **64.400**, `ad_line` 51,843, `n_stocks` **500** |
| Panel days excluded (no snapshot) | **252** |
| Frozen artifacts written | **none** |

Nothing under `regime_dual_engine/data/`, `data/constituents/` or `production/` was
touched. The frozen breadth CSV still hashes to
`f4dbf1836d8b46fbd47dd034f460837a` (2,408 rows, tail 2025-07-31).

### Why 252 days were excluded rather than computed

The candidate's history is only usable from 2017. Before its first trusted snapshot the
panel has 252 trading days for which **no universe exists**, and `build_breadth` computes
`above.sum() / n_stocks * 100`. With an all-`False` membership row that is `0 / 0`, which
pandas resolves to **50.0** — a plausible-looking breadth reading derived from no data at
all.

I hit exactly that on the first run: 252 rows of `pct = 50.0, n_stocks = 0`, and a
cross-check reporting a 50.00 pp difference against the frozen series. It is the precise
failure this whole exercise exists to prevent — a number that looks fine and means nothing.

The build now marks those days and drops them explicitly, and records the count in the
provenance file so a future rebuild knows the artifact does not start at the panel start.

---

## B. §16 — Cross-check against the previous implementation

### B.1 Where the two series can legitimately be compared

| Series | Tail |
|---|---|
| Frozen research breadth | 2025-07-31 |
| Live breadth | 2026-08-06 |
| **Last common date** | **2025-07-31** |

The frozen series stops at 2025-07-31 because the earlier breadth refresh was **never
promoted** — by design, pending exactly this decision. That is the intended state, not a
data gap, and the cross-check is anchored on the last common date rather than on each
series' own tail.

### B.2 Decomposition of the difference (§16 requires all three sources separated)

| Difference source | Finding |
|---|---|
| **(3) Calculation** | **Zero.** The same frozen `build_breadth` produced both series. |
| **(1) Universe** | **All of it.** On every one of the 1,820 differing days the two universes differ, averaging **8.2 names only in the frozen source** and 1.8 only in the candidate. |
| **(2) Price data** | **Zero contribution.** Both arms read the same `data/cache/equities`; the previous round established that the price basis does not move breadth at all. |

| Metric | Value |
|---|---|
| Overlapping days | 2,156 |
| Days with any difference | 1,820 (84.4%) |
| Mean abs difference | **0.184 pp** |
| Max abs difference | **0.790 pp** |
| Identical days | 15.6% |

At the last common date, 2025-07-31, `pct_above_50dma` is **identical to the last decimal**
(57.23) and `n_stocks` matches (491).

### B.3 The `ad_line` difference is a start-date artefact, not a discrepancy

At 2025-07-31: `pct` identical, `n` identical, but `ad_line` **47,916 vs 53,988** — a gap
of 6,072.

This is not a data disagreement. The A/D line is a **cumulative sum**, so its value depends
on where the accumulation starts:

* frozen: accumulates from **2016-01-04**
* live: accumulates from **2017-01-03** (the candidate's trusted window)

One extra year of accumulation accounts for the entire gap. The two series are measuring
the same daily changes over different spans.

**Practical consequence:** the live `ad_line` must not be compared to the frozen
`ad_line` in absolute terms, and it must not be used as a Regime input without either
re-basing to a common epoch or treating the level as source-relative. The regime currently
uses the breadth **percentile** of `pct_above_50dma`, not the A/D level, so no live
decision is affected today — but this must be recorded before anyone wires the A/D line in.

### B.4 What the live series shows beyond the frozen tail

The frozen series cannot display these 255 days. They exist only in the live build:

| | |
|---|---|
| Range | 2025-08-01 → 2026-08-06 (255 days) |
| `pct_above_50dma` first / last | 50.102 → **64.400** |
| Min / max over the window | 19.355 / 73.185 |

The 19.4% trough and 73.2% peak inside this window are **not visible in any frozen
artifact** — they are exactly the readings a stale universe would have concealed. Whether
they are correct depends on the universe being right, which is the open question in the
audit report.

---

## C. §12 — What the provenance record guarantees

A reader of `breadth_live_provenance.json` can reconstruct exactly what produced any row:

* **Which universe** — `membership_hash` (sha256 of the sorted member list) plus
  `constituent_snapshot_date` and `constituent_snapshot_count`.
* **Which file version** — `constituent_sha256`. The upstream CSV is mutable; this hash is
  not, so a universe observed today can be re-derived years from now.
* **Which prices** — `ohlcv_source`, `ohlcv_tail_date`, `price_basis`.
* **Which formula** — `breadth_formula`, explicitly marked UNMODIFIED.
* **What was not checked** — `not_validated` lists the two open items rather than leaving
  a reader to assume full validation.

The artifact also records `frozen_research_artifacts_touched: []` as a machine-checkable
statement, so a future run that *did* touch them would produce a different record.

---

## D. §17/§20 — Policy and shadow gate status

The approved policy remains `max_age_days = 7`, `temporal_skew_days = 3`,
`on_failure = BLOCK_DECISION`. It is **still not enabled**, and the audit gives a concrete
reason rather than a procedural one:

| Input | Tail | Age at 2026-10-05 | 7-day policy |
|---|---|---:|---|
| Constituents (candidate) | 2026-10-04 | 1 day | would pass |
| Breadth (live build) | 2026-08-06 | 60 days | **fails** |
| OHLCV / SPY | 2026-08-06 | 60 days | **fails** |

So even with a current universe, the chain does not satisfy the policy. Enabling
`BLOCK_DECISION` today would block every live run on the **price** side, not the
constituent side — which means the constituent work, while necessary, is not by itself the
thing standing between the project and a live run.

Shadow gate: **PENDING, 0 qualifying sessions.** No session has been counted. The gate
requires `data_validity = VALID`, and no session can reach that while breadth and OHLCV are
60 days stale. All sessions so far would be `NON_QUALIFYING_DATA_INVALID`.

---

## E. What this validation does not establish

1. **It does not validate the candidate's date semantics.** The 0–2 day offset against the
   frozen source is measured but its direction is not. If the candidate records
   announcement dates, `members_as_of(T)` has a one-session look-ahead that this build
   inherits.
2. **It does not validate the three post-2026-06-30 events** against anything external. The
   current universe reconciles internally; internal consistency is not independent
   verification.
3. **It does not validate the 255-day extension.** Those readings exist only in this build.
4. **It does not make the live dataset production-ready.** It is a research artifact in a
   separate directory, deliberately not wired into `run_daily`.
