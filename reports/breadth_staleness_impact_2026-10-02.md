# Breadth staleness — a live-run hazard, measured

**Date:** 2026-10-02 · **Status: MEASURED — no production change made**
**This is the highest-severity open item in the project.**

---

## 0. The finding

> A live run today would compute **50 % of the Regime v1 decision from a breadth series
> that ended 428 days ago**, while the other 50 % comes from an SPY series that ended
> 57 days ago. The two halves of the same composite come from points in time **371 days
> apart**, and no rule in the system objects.

The PIT fix (2026-10-01) guaranteed breadth can never contain a bar *after* `as_of`. That
closed the future-leak half of the problem. It did not close the other half: the cache can
be arbitrarily **old**, and the staleness is invisible to every current check.

---

## 1. Measured state

| input | coverage | staleness vs 2026-10-02 |
|---|---|---:|
| PIT breadth cache | 2016-01-04 → **2025-07-31** (2,408 rows) | **428 days (14.1 months)** |
| SPY / HMM input | 2016-01-04 → **2026-08-06** (2,663 bars) | **57 days** |
| OHLCV universe | 1,136 tickers | not audited here |

**The two halves of the composite are 371 days apart.** Regime v1 is defined as
`0.5 × hmm_bull_prob + 0.5 × breadth_percentile`; today that is
`0.5 × HMM(2026-08-06) + 0.5 × breadth(2025-07-31)`.

What production would decide today:

```
breadth tail            : 2025-07-31   (tail_matches_as_of = False)
hmm_bull_prob           : 0.0048       (from SPY 2026-08-06)
breadth_percentile      : 41.746
composite_score         : 21.113
regime_label            : BEAR
position_size_mult      : 0.0          →  defensive, no new longs
```

`data.breadth.tail_date` and `tail_matches_as_of` are already recorded (added 2026-10-01,
information-only), so a run *can* see this. Nothing acts on it.

---

## 2. How much does the staleness matter?

Holding SPY fixed and moving only the breadth tail:

| breadth tail | lag | % above 50DMA | regime | mult |
|---|---:|---:|---|---:|
| 2025-07-31 | 0 d | 57.23 | BEAR | 0.0 |
| 2025-07-10 | 21 d | 75.05 | BEAR | 0.0 |
| 2025-05-29 | 63 d | 69.26 | BEAR | 0.0 |
| **2025-03-27** | **126 d** | 40.25 | **BULL** | **1.0** |
| 2024-11-21 | 252 d | 60.83 | BEAR | 0.0 |

**The regime flips twice across that range** — BEAR → BULL → BEAR, with
`position_size_mult` moving 0.0 → 1.0 → 0.0. A stale breadth input does not degrade the
decision gracefully; it can invert it, and `position_size_mult` is the difference between
trading and not trading.

The 252-day rolling percentile is slow-moving, so short staleness is nearly harmless
(0–63 days all give BEAR/0.0). The exposure appears at the scale actually present today.

### A correction to this sweep

This table holds SPY at its 2026-08-06 bar while varying breadth, so it isolates the
breadth half. In a real live run **both** halves are stale and move together, and the
composite would be computed from two different historical moments regardless. The sweep
therefore *understates* the incoherence rather than overstating it.

### Where the boundary sits

| | breadth percentile needed |
|---|---:|
| current | 41.75 |
| to reach BULL (≥65) | **108.9** — unreachable |
| to reach BEAR (≤35) | 48.9 |

The current composite is far enough inside BEAR that the breadth half would have to move a
long way to change the label. **The decision is currently robust to breadth staleness but
not to HMM staleness** — `hmm_bull_prob = 0.0048` is the dominant term, and that comes
from the SPY side, which is 57 days old on its own.

---

## 3. Why this is the same class of bug as the PIT leak

The 2026-10-01 PIT defect and this one are mirror images:

| | PIT leak (fixed) | staleness (open) |
|---|---|---|
| breadth tail used | 2025-07-31 **for every historical date** | 2025-07-31 **for today** |
| symptom | 50 % of regime was a **constant** | 50 % of regime is **14 months old** |
| detection | required a deliberate PIT audit | visible in `tail_date`, **unenforced** |
| fix | `slice_to_end()` in the loaders | **no rule exists** |

The lesson recorded on 2026-10-01 was *"沒做 PIT 檢驗前，任何回測結論都可能整體作廢"*.
The symmetric lesson is: **a data-plumbing guarantee is only as good as the dimension it
constrains.** `slice_to_end` constrains the future; nothing constrains the past.

---

## 4. What is and is not affected

**Not affected:** every result in this research programme. The backtest window is
2024-01-01 → 2025-07-31, and `slice_to_end` guarantees each session saw a breadth tail of
exactly that date. The staleness issue is a **live-run** hazard only.

**Affected:** any live `python production/main.py` invocation today. The regime it computes
combines a 2026-08 HMM reading with a 2025-07 breadth reading.

**Also unverified:** the OHLCV universe cache and the HMM model's own staleness tolerance
were not audited here. `hmm_bull_prob = 0.0048` is an extreme value and deserves its own
look — it may be correct (a genuine bear reading) or may itself be a stale-input artefact.

---

## 5. Policy options — for the human, deliberately not chosen here

| option | mechanism | safety | cost |
|---|---|---|---|
| **warn** | log when `breadth tail != as_of` | none — relies on someone reading logs | trivial |
| **flag** | add to `DecisionRecord.warnings` + briefing | visible in human review; still not blocking | small; the P7 note records this is a deliberate choice |
| **fail-loud** | refuse the regime decision when the breadth tail exceeds N sessions; `pipeline_status = FAILURE` | **highest** — would stop today's live run entirely | needs a threshold and a data-refresh path first |

**Recommendation, for the human to accept or overrule:** `flag` now, `fail-loud` once a
breadth refresh path exists. `fail-loud` today would block every live run with no way to
run at all, which is its own failure mode.

**Prerequisite either way:** a way to **rebuild the PIT breadth cache past 2025-07-31**.
Until that exists, any staleness rule is choosing between "wrong regime" and "no regime".
That is a data-pipeline question and it is the real blocker, not the policy.

---

## 6. Honest limitations

* Measured against the caches on disk today. A live run using `YFinanceSource` would fetch
  current SPY but would still hit the same on-disk breadth cache, so the incoherence
  persists for live.
* The sensitivity sweep isolates the breadth half. Real incoherence is larger (§2).
* `hmm_bull_prob = 0.0048` was **not** audited. If the HMM input is also stale, the
  dominant term of the composite is unverified.
* No claim is made about what the *correct* regime would be today. The point is that the
  system cannot currently know.

---

## 7. Artifacts

* `research/breadth_staleness_impact.py`
* `reports/breadth_staleness_impact_2026-10-02.json`

No production file was touched. No parameter was written. No commit, no push.
