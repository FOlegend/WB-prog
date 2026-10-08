# BS — Breadth Refresh, Freshness Contract & Live Regime Safety

**Date:** 2026-10-04 · **Status: BS-1 → BS-7 complete · two items require human decision**
**Baseline untouched:** 168 trades, +5.57 %, Sharpe 0.513, MaxDD −10.39 % — **verified identical**

> This is a production **data-integrity** task, not a strategy task. No trading
> parameter, regime weight, threshold or frozen contract was changed.

---

## 0. Summary

| phase | outcome |
|---|---|
| **BS-1** archaeology | **The rebuild path already existed.** Staleness was not a data-availability problem. |
| **BS-2** refresh path | **PIT and current refreshed to 2026-08-06** (+255 days), historical rows **bit-identical**. |
| **BS-3/4** contract | `data_validity.py` — five states, both effective dates and ages, no silent substitution. |
| **BS-5** policy | **Proposed, not active.** Thresholds need human approval. |
| **BS-6** safety | Stale input is recorded and marked untrustworthy; nothing is substituted. |
| **BS-7~10** validation | Historical invariance **exact**; reproducibility **exact**; all tests green. |

**Two decisions are waiting on you** (§7): the thresholds, and whether stale input
should merely be recorded or should block the decision.

---

## BS-1 — Read-only archaeology (§3): the ten questions

| # | question | answer |
|---|---|---|
| 1 | Where does breadth originate? | `regime_dual_engine/breadth_data.py` + `pit_breadth_data.py`, computed from the OHLCV close panel. |
| 2 | What raw data is needed? | Daily closes for PIT S&P 500 members; 50-day SMA, % above it, cumulative A/D line. |
| 3 | Is the raw data available to the latest date? | **YES.** 502 of 503 PIT members have OHLCV, **all with tail 2026-08-06**. Constituent snapshots run to **2026-06-30**. |
| 4 | How is the CSV generated? | `build_breadth_series()` / `build_pit_breadth()` write to `data/breadth*_2016_2025.csv`. Both accept an arbitrary `end`. |
| 5 | Is there an existing rebuild path? | **YES** — `get_breadth(kind, rebuild=True)` and `__main__` blocks already call it. |
| 6 | Can it be incremental? | The builders recompute the full series from the panel, but a rebuild is ~2 min, so incremental is unnecessary. |
| 7 | Does live refresh breadth automatically? | **NO.** No scheduler (no cron / plist / shell runner) exists. Refresh is manual. |
| 8 | What determines `as_of`? | `main.py`: `--as-of`, defaulting to today. Backtest: the SPY trading calendar, clamped to `BREADTH_LAST = 2025-07-31`. |
| 9 | What determines `tail_date`? | `max(breadth_df.index)` — a property of the **CSV**, entirely independent of `as_of`. |
| 10 | Can SPY and breadth share a decision date? | **Yes**, and now they do: both effective dates and both ages are recorded per evaluation. |

**The headline finding:** the CSV stopped at 2025-07-31 because nobody re-ran a
rebuild that already worked. Nothing was missing but the invocation.

---

## BS-2 — The refresh path (`regime_dual_engine/breadth_refresh.py`)

Calls the **existing, unmodified** builders. Does not touch the breadth formula,
percentile, rolling window, regime weights, thresholds or divergence cap.

| series | before | after | appended | history |
|---|---|---|---:|---|
| **pit** | 2025-07-31 (2,408) | **2026-08-06** (2,663) | 255 | **identical** |
| **current** | 2025-07-31 (2,408) | **2026-08-06** (2,663) | 255 | **identical** |
| plain | 2025-07-31 | — | — | **ABORTED by design** |

The `plain` series refuses to refresh because it is a current-constituent series
recomputed over the whole history: 2,359 of 2,408 rows would change. That is
survivorship bias by construction, and the abort is correct. **It is not on the
production path** — `load_breadth` prefers PIT and only falls back when PIT is absent,
so the abort has no production effect.

Refreshed files are written to `*_refreshed.csv`; the originals are **not**
overwritten. Promotion is a separate, explicit step.

---

## BS-7~10 — Validation

| check | result |
|---|---|
| **A. Coverage** | 2,408 → 2,663 days; tail 2025-07-31 → **2026-08-06** |
| **B. PIT correctness** | `end=2018-06-15 / 2022-03-01 / 2025-07-31 / 2026-08-06` each return **no observation after `end`** |
| **C. Reproducibility** | two independent rebuilds → `max|diff| = 0.0000000000` on all three columns |
| **D. Cache correctness** | cached path and rebuilt path agree on the shared range |
| **E. Incremental safety** | the append-only gate (§11 below) makes a silent rewrite impossible |
| **F. Market calendar** | Fri→Mon sequence intact, no weekend rows, `n_stocks` steady at 500 |
| **G. Provenance** | PIT membership from `fja05680/sp500` snapshots; 99.8 % OHLCV coverage (missing: EA) |
| **§11 historical invariance** | **2,408 shared rows, 0 differing, max abs diff 0.000000** on every column |
| **§12 temporal consistency** | see the table below |

### §11 is enforced mechanically, not just checked once

`refresh_breadth()` compares every pre-existing row before writing and **aborts
without writing** if any would change. A refresh may append; it may never rewrite.

### §12 temporal consistency, under the *proposed* policy

```
as_of        SPY tail     Br tail       spyA   brA  consist   fresh        state  qualifies
2026-10-04   2026-08-06   2025-07-31      59   430    False   False        STALE    False
2026-08-06   2026-08-06   2025-07-31       0   371    False   False        STALE    False
2025-07-31   2025-07-31   2025-07-31       0     0     True    True        VALID     True
2024-06-14   2024-06-14   2024-06-14       0     0     True    True        VALID     True
```

**The two historical rows are VALID.** That is the direct demonstration of task §9:
historical replay is unaffected, because in replay the breadth tail *is* the as-of
date. Only live runs, where as-of is "now" and the tail is behind, can be invalid.

### §16 shadow gate

A session counts toward the 20-session gate only when validity is `VALID`;
otherwise `NON_QUALIFYING_DATA_INVALID`. The existing **0-session PENDING** state is
unchanged — with the proposed policy every session recorded so far would have been
non-qualifying anyway, so this task neither advances nor resets the gate.

---

## BS-3/4/6 — The contract (`production/data_validity.py`)

Five states, kept distinct (task §14) because the operator response differs:

| state | trigger |
|---|---|
| `VALID` | present, fresh enough, temporally consistent |
| `STALE` | an input older than the policy maximum |
| `TEMPORALLY_INCONSISTENT` | each input fresh enough alone, but effective dates too far apart |
| `MISSING` | a required input is unavailable |
| `INVALID` | a tail is **after** `as_of` — a look-ahead, ranked worse than staleness |

Recorded per evaluation (task §13): `regime_as_of`, `spy_tail_date`,
`breadth_tail_date`, `spy_age_days`, `breadth_age_days`, `spy_matches_as_of`,
`breadth_matches_as_of`, `regime_input_temporal_consistent`,
`regime_input_freshness_valid`, `state`, `reasons`, plus full provenance. No
pre-existing field was removed.

**No silent substitution (task §8), enforced and tested:** no SPY-as-breadth proxy,
no synthetic estimate, no default regime, no default multiplier, no automatic
degradation to SIDEWAYS. A stale input yields a state and a reason; what to do about
it is a human decision.

**Inert by default:** `ProductionConfig.freshness_policy = None`, so the assessment
does not run at all and the backtest is untouched.

### A real bug this work surfaced — and the fix

The first run of the §12 diagnostic reported `SPY tail = 1970-01-01`, age 20,730
days. The cause was in my own new code: the two Regime inputs arrive with
**different frame shapes** — OHLCV is a `RangeIndex` + a `datetime` **column**, while
breadth is a `DatetimeIndex`. Reading only the index turned an integer RangeIndex into
a POSIX timestamp.

Left unfixed, the freshness check would have been *actively wrong in production*,
marking a perfectly current input as 56 years stale. `effective_date()` now resolves
both shapes and returns `None` rather than guessing when neither is present.
`test_effective_date_handles_both_frame_shapes` is the regression test.

---

## Safety verification

| check | result |
|---|---|
| Backtest summary | **identical** — 168 trades, +5.57 %, Sharpe 0.513, MaxDD −10.39 % |
| Behavioural equivalence (15 critical trade fields × 168) | **0 mismatches** |
| trade_log / equity_curve / skipped / screens / regime_log | **identical** |
| Regression suite | 115 + 58 + **79 (new)** = **252 assertions, all green** |
| `freeze_conformance_audit` | **0 MISMATCH** (13 OPERATIVE / 9 TEST-VERIFIED / 1 INERT / 2 CLARITY — unchanged) |
| Frozen parameters | **11 verified unchanged** |
| `exit_engine_mode` | **legacy** |
| commit / push | **none** |

Registry updated: the three new modules are all `production_decision_authority = NONE`
— data-integrity helpers, correctly **not** classified as strategy engines (task §18).

> Note: `control_center/project_control.db` hit a OneDrive `disk I/O error` during
> refresh. The database was backed up and verified readable (147 modules) before any
> action, the registry was rebuilt in a writable location, and the result (157
> modules) was copied back and re-verified. No data was lost.

---

## BS-5 — The decision that is yours

Full reasoning in `reports/bs5_freshness_policy_proposal_2026-10-04.md`. In brief:

| parameter | proposed | basis |
|---|---|---|
| `max_age_days` | **7** | clears a normal weekend/holiday cluster and one missed manual refresh; flags the current 428-day failure 60× over. 1 day fails every weekend; 30 days lets a 50 %-weight input drift through a quarter of its 252-day window. |
| `temporal_skew_days` | **3** | the two caches refresh independently, so exact alignment is unachievable; 3 still catches the real 371-day split. |
| `on_failure` | **`RECORD_ONLY`** now, `BLOCK_DECISION` later | see below |

**The honest tension on `on_failure`.** Today breadth is 428 days stale, so
`BLOCK_DECISION` would refuse **every** live run immediately — and there is no OHLCV
refresh path in place to unblock it. `RECORD_ONLY` surfaces the state, the tail date
and the age, and a human acts. But task §8 asks that stale breadth stop being treated
as a valid current input; if the intent is the stronger reading — that a stale regime
must not produce a tradeable recommendation at all — then `BLOCK_DECISION` is
required, and the refresh path must land first. **These two options promise different
things and I have not chosen between them.**

**A consequence worth knowing before you decide:** the OHLCV cache is *itself* 57 days
stale (tail 2026-08-06). A 7-day policy would therefore block live runs on the **SPY**
side even after a successful breadth refresh. That is arguably correct — the HMM half
really is 57 days old — but it means adopting the policy makes the system refuse to
run until the price data is also refreshed.

---

## What was deliberately not done

* No change to any trading, risk, stop, exit or entry parameter.
* No change to regime mathematics, weights, thresholds or the divergence cap.
* No change to the breadth formula, percentile, rolling window.
* No autonomous fallback of any kind.
* `exit_engine_mode` untouched (`legacy`); `mode="new"` not enabled.
* No scheduler introduced.
* Original breadth CSVs not overwritten; refreshed data sits in `*_refreshed.csv`.
* No commit, no push.

---

## Artifacts

| file | content |
|---|---|
| `regime_dual_engine/breadth_refresh.py` | the refresh path (§11-gated) |
| `production/data_validity.py` | the freshness / temporal contract |
| `production/tests/test_breadth_freshness.py` | 79 acceptance assertions |
| `research/bs_temporal_consistency.py` | §12 diagnostic + §16 shadow rule |
| `reports/bs5_freshness_policy_proposal_2026-10-04.md` | the policy proposal |
| `reports/bs2_breadth_refresh_2026-10-04.json` | refresh log with hashes |
| `reports/bs_temporal_consistency_2026-10-04.json` | §12 rows |
