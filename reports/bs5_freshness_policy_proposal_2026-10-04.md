# BS-5 — Freshness Policy Proposal

**Date:** 2026-10-04 · **Status: PROPOSAL — thresholds require human approval**
**Implemented mechanism:** `production/data_validity.py` (assessment + recording, live)
**No threshold is active in production. `ProductionConfig.freshness_policy` defaults to
`None`, so nothing is enforced and historical replay is untouched.**

---

## 0. What is being asked

Three numbers, and what happens when they are breached:

| parameter | meaning | proposed value | who decides |
|---|---|---|---|
| `max_age_days` | max calendar age of ANY Regime input vs `as_of` | **7 days** — see §3 | human |
| `temporal_skew_days` | max difference between SPY and breadth effective dates | **3 days** — see §3 | human |
| `on_failure` | what the pipeline does on breach | **`RECORD_ONLY`** now, `BLOCK_DECISION` later — see §5 | human |

---

## 1. Step 1 findings (task §7 required these BEFORE proposing)

**Data publication / refresh cadence**

| source | cadence | evidence |
|---|---|---|
| OHLCV cache | manual, all-or-nothing | 520 of 616 files share the tail **2026-08-06**; stragglers at 2026-06-30 (1), 2026-06-22 (2), 2026-03-23 (4). The cache is refreshed as a batch, not daily. |
| PIT constituents | refreshed on membership change | snapshots to **2026-06-30**, 2,718 snapshots since 1996 |
| breadth CSV | **never refreshed since 2025-07-31** | this is the defect; a working rebuild path now exists (`regime_dual_engine/breadth_refresh.py`) |

**Rebuild latency (measured this session, not estimated)**

| series | wall time |
|---|---:|
| PIT | ~120 s |
| current | ~110 s |
| plain (current-constituent, all history) | ~150 s |

**Realistic rebuild latency: ~2 minutes.** This is the key input to the threshold — a
policy that demanded same-day freshness would be violated by a correct rebuild
started after the close.

**Market-calendar behaviour.** Breadth is a trading-session series. `as_of` on a weekend
or holiday resolves to the last prior session, so `age_days` of 1–3 is the *normal* state
on Mondays and after holidays. A threshold of 1 day would produce false alarms every
weekend; the observed pattern in the rebuilt series is Friday → Monday with no gap.

**Existing analogous logic.** `production/datasource.py` already carries
`Provenance.stale` for OHLCV and marks a cache stale when a network refresh fails
(`CachedSource` → `stale=True, note="network refresh failed; stale cache used"`). So the
project already has a freshness concept — it was simply never applied to breadth, which
is where the 50 % weight sits.

**No scheduler exists.** No cron, no launchd plist, no shell runner. Refresh is manual,
which is precisely why breadth fell 428 days behind.

---

## 2. Why 7 days, derived rather than guessed

The threshold has to clear normal weekend/holiday gaps and realistic manual-refresh
latency, while still catching the actual failure.

```
observed normal gap (weekend/holiday)   : 1–3 calendar days
manual rebuild latency after a miss     : ~0 days (2 min of compute)
current failure being guarded against   : 428 days
```

* **1 day** — fails every weekend. Rejected.
* **3 days** — survives a normal weekend, but is tight against a long weekend plus a
  holiday, and leaves almost no room for a human to notice and act.
* **7 days** — one full trading week. Survives any plausible holiday cluster, tolerates
  a missed manual refresh, and flags the current 428-day failure by a factor of 60.
  **Proposed.**
* **30 days** — would have caught the current failure eventually, but 30 days of
  breadth is roughly a quarter of the 252-day percentile window, so the input can
  materially drift before the rule objects. Too loose for a 50 %-weight input.

**`temporal_skew_days = 3`.** The two inputs refresh on different cadences (OHLCV
batch vs breadth rebuild), so perfect alignment is not achievable. 3 days allows them
to be within the same weekend window while still catching the real incoherence — a
371-day split is caught by any value below 371.

---

## 3. What "fresh" cannot mean here

An important limit on any threshold: **the OHLCV cache itself is 57 days stale**
(tail 2026-08-06, today 2026-10-04). A 7-day policy on breadth alone would be met by a
fresh breadth rebuild while SPY is still 57 days behind — and vice versa. The policy
therefore applies to **all** Regime inputs (task §7 asked for this explicitly), and the
`temporal_skew` check is what catches one input being refreshed while the other is not.

**Consequence worth stating plainly:** until the OHLCV cache is refreshed past
2026-08-06, a 7-day policy would block live runs on the **SPY** side. That is arguably
correct — the HMM half is genuinely 57 days old — but it means adopting the policy makes
the system refuse to run until the data is refreshed. That is a real operational
consequence and the human should choose it knowingly.

---

## 4. State semantics under the proposal (task §14)

| state | trigger | operator meaning |
|---|---|---|
| `VALID` | both inputs present, ≤ 7 d, skew ≤ 3 d | decision usable |
| `STALE` | an input older than 7 d | refresh the data |
| `TEMPORALLY_INCONSISTENT` | each input fresh enough, but effective dates > 3 d apart | refresh the lagging one |
| `MISSING` | an input unavailable | transient; retry |
| `INVALID` | a tail is **after** `as_of` (look-ahead) | stop — the decision cannot be trusted at all |

Look-ahead is deliberately ranked worse than staleness: a stale input is merely old, a
future-dated input means the PIT contract itself is violated.

---

## 5. `on_failure` — the honest sequencing

`RECORD_ONLY` is proposed **now**, `BLOCK_DECISION` later. This is not fence-sitting;
it follows from the data state:

* Today, breadth is 428 days stale. `BLOCK_DECISION` would refuse **every** live run
  immediately, and there is no OHLCV refresh path in place to unblock it. The bot would
  simply stop, with no route to recovery.
* `RECORD_ONLY` surfaces the exact reason, tail date and age in the DecisionRecord, the
  briefing and the console — a human sees `Regime input validity : STALE` with the dates
  and acts.
* Once a breadth refresh path is routine (and the OHLCV cache is current), promoting to
  `BLOCK_DECISION` is a one-word policy change with no code edit.

Task §8 requires stale breadth to stop being treated as a valid current input.
`RECORD_ONLY` satisfies that only in the weaker sense that the record no longer claims
validity. **If the intent is the stronger reading — that a stale regime must not produce
a tradeable recommendation at all — then `BLOCK_DECISION` is required, and the
data-refresh path must land first.** That is the human's call, and the two options are
genuinely different in what they promise.

---

## 6. Explicitly not proposed

* No change to the breadth formula, percentile, rolling window, or any regime weight,
  threshold, or divergence cap.
* No autonomous fallback: no SPY-as-breadth proxy, no synthetic estimate, no default
  regime, no default multiplier, no silent degradation to SIDEWAYS.
* No change to any trading, risk, stop, exit or entry parameter.
* No scheduler. Introducing cron is an operational decision, not a data-integrity one.
* No change to `exit_engine_mode` (stays `legacy`).

---

## 7. Decision requested

1. Approve `max_age_days = 7` and `temporal_skew_days = 3`?
2. Choose `on_failure`: `RECORD_ONLY` now, or `BLOCK_DECISION` now (accepting that live
   runs stop until the data is refreshed)?
3. Should the OHLCV cache be refreshed past 2026-08-06 as part of this work? Without it,
   any freshness policy will keep flagging the SPY side.

Until (1) and (2) are answered, `freshness_policy` stays `None` and nothing is enforced.
