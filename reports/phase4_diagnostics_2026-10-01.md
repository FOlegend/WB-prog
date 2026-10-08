# Phase 4 — D1–D6 Read-Only Diagnostics

**Date:** 2026-10-01 · **Repo:** `WB-prog` · **Mode:** `legacy` (production default)
**Window:** 2024-01-02 → 2025-07-31 (396 sessions, 151 trades)
**Status:** diagnostics complete. **No implementation beyond the approved CLI wiring.** Awaiting human review.

Harness: `production/backtest.py::ProductionBacktest` executed **unchanged**; the per-day
DecisionRecords it normally discards were captured in-process by
`production/tests/phase4_diagnostics.py` (which temporarily wraps `production.backtest.run_daily`
inside its own process). **No production file was modified, no parameter was changed, and nothing was
re-simulated.** Raw data: `reports/phase4_diag_data_2026-10-01.json`.

---

## 0. Headline

| | |
|---|---|
| System return | **+7.99 %** (Sharpe 0.935, MaxDD **−5.98 %**, PF 1.29) |
| SPY buy & hold | **+36.26 %** → raw gap **−28.27 pp** |
| System at **its own** average exposure (17.16 %) | SPY would have returned **+7.37 %** → **+0.62 pp** |
| Average exposure | **17.16 %** (cash drag 82.84 %) |
| Valid setups that never became a trade | **506 of 669** (75.6 %), of which **505 (99.8 %)** = the production risk engine's own verdict *"風險預算 $X 不足以買 1 股"* (risk budget insufficient for one share) |

**The benchmark gap is an exposure/deployment phenomenon, not (in this window) a decision-quality
one.** At the exposure the system actually took, its decisions were benchmark-equivalent (+0.62 pp),
and this holds in **every** sub-period tested (see §V2). Whether that is *good* news is discussed in
§8 — in a bull window, deploying more always looks better, so the conclusion must be risk-adjusted,
not raw-return.

> **Verification status.** All load-bearing numbers below were re-checked by an independent script
> (`production/tests/phase4_verify.py`, results in §V). One earlier claim — that entry gap was "the
> clearest monotone signal" — **did not survive verification and has been corrected in §D3**.

---

## D1 — Exit attribution

| reason | n | share | win % | avg R | sum R | avg MFE (R) | avg give-back (R) |
|---|---|---|---|---|---|---|---|
| STOP_LOSS | 75 | 49.7 % | 0.0 | −1.049 | **−78.69** | 0.369 | 1.418 |
| TAKE_PROFIT | 60 | 39.7 % | 100.0 | +1.667 | **+100.02** | 2.252 | 0.585 |
| TIME_STOP | 6 | 4.0 % | 100.0 | +0.621 | +3.73 | 1.185 | 0.565 |
| TRAILING_STOP | 10 | 6.6 % | 100.0 | +0.246 | +2.46 | 1.427 | 1.181 |

* **Expectancy +0.182 R**, total **+27.5 R** over 151 trades.
* **Stops are not cutting off good trades**: only **3 of 75** stop-outs had ever been ≥ +1 R in
  favour (average MFE at the stop = 0.37 R). The stop distance and entry timing are not the problem.
* **Targets are leaving money on the table**: TP fills at exactly **1.667 R** while those trades
  averaged **2.25 R** MFE → 0.585 R given back per winner. Including trailing exits the theoretical
  give-back is ≈ 0.585×60 + 1.181×10 ≈ **47 R** (vs +27.5 R actually realised) — but capturing it
  requires selling at the peak, so this is an **upper bound, not an expectation**.
* Only **3 trades** ("winners given back") were ≥ +1 R and then closed ≤ 0 R (IBKR, KLAC, LRCX).

**Reading:** the exit *rules* behave as designed; the only visible inefficiency is that the fixed
+1.667 R target is systematically below the average favourable excursion.

## D2 — Signal → fill funnel

| stage | count | conversion |
|---|---|---|
| candidate ticker-evaluations | 1 037 | — |
| setup valid | 669 | 64.51 % of candidates |
| **risk allowed** | **163** | **24.36 % of valid** |
| buy proposals | 163 | 100 % |
| filled trades | 151 | 92.64 % of proposals (5 rejected at next open: `GAP_TOO_HIGH`) |
| candidate → filled | — | **14.56 %** |

**Why 506 valid setups were rejected by the risk gate** — cause read from the **production risk
engine's own rejection string** (the engine is re-called on the reconstructed inputs; the
reconstruction is validated at 99.7 % agreement in §V1):

| cause (engine's own verdict) | n | share |
|---|---|---|
| **"風險預算 $X 不足以買 1 股"** — `floor(budget / stop_distance) == 0` | **505** | **99.8 %** |
| other / unclassified | 1 | 0.2 % |

Mean risk budget available to a *rejected* setup: **$4.95**.

**Blocked at the session level:** `regime_defensive` on **185** sessions (46.7 %, `position_size_mult = 0`),
`max_open_positions reached` on **99** sessions (25.0 %).

## D3 — Fill / setup quality

**Entry gap looks monotone over the full window — but it does NOT survive verification.**
The window-level table is a **2024 effect**; in 2025 the ordering reverses (§V4):

| entry gap (open vs signal close) | n | share | avg R | win % | net P&L |
|---|---|---|---|---|---|
| **< 0 %** (gapped down — favourable) | 65 | 43.0 % | **+0.399** | 61.5 | **+112.37** |
| 0 – 0.5 % | 38 | 25.2 % | +0.199 | 47.4 | +13.93 |
| 0.5 – 1 % | 28 | 18.5 % | −0.147 | 39.3 | −6.25 |
| 1 – 2 % (currently allowed) | 20 | 13.3 % | −0.094 | 35.0 | **−16.23** |

| avg R by cohort | < 0 % | 0–0.5 % | 0.5–1 % | 1–2 % | monotone? |
|---|---|---|---|---|---|
| 2024 (n=107) | +0.366 | +0.311 | −0.178 | **−0.568** | yes |
| **2025 (n=44)** | +0.480 | −0.163 | −0.072 | **+0.618** | **no — reversed** |
| first half | +0.143 | **+0.440** | +0.050 | −0.467 | no |
| second half | **+0.648** | −0.042 | −0.345 | +0.279 | no |

→ With 8–20 trades per cell in 2025, the gap cohorts are **too small and too unstable to act on**.
The honest statement is: *"filling at or below the signal close was highly profitable in 2024; in 2025
the relationship vanished."* **Candidate #2 is therefore downgraded (§7).**

**Setup score does not order outcomes** (no monotone relation):

| setup_score | n | share | avg R | win % |
|---|---|---|---|---|
| < 0.60 | 7 | 4.6 % | +0.905 | 71.4 |
| **0.60–0.70** | **83** | **55.0 %** | **+0.100** | 48.2 |
| 0.70–0.80 | 9 | 6.0 % | +0.932 | 77.8 |
| 0.80–0.90 | 51 | 33.8 % | **+0.056** | 45.1 |
| ≥ 0.90 | 1 | 0.7 % | +1.667 | 100.0 |

**Regime at entry inverts the intuition:**

| regime at entry | n | share | avg R | win % | net P&L |
|---|---|---|---|---|---|
| **BULL** | 24 | 15.9 % | **−0.321** | 33.3 | **−47.99** |
| SIDEWAYS | 127 | 84.1 % | +0.277 | 53.5 | +151.81 |

* `extension_from_pivot_pct` is not populated for any trade and `size_mult` at entry is 0.5 for
  **all** 151 trades → both are uninformative fields in the current record.

### D3a — CONTRACT MISMATCH found: a frozen Setup v1 filter appears to be inert

Tracing *why* `extension_from_pivot_pct` is empty produced a finding that is bigger than a record
gap. Chain of evidence:

| step | fact |
|---|---|
| 1 | the trade log has `extension_from_pivot_pct = null` for all 151 trades |
| 2 | `src/state/state.py:123` copies it from the position dict, so the position dict is null |
| 3 | `production/backtest.py:274,316` computes `ext = (open − prior_high20)/prior_high20` and stores `round(ext,4) if ext is not None` → therefore **`prior_high20` was `None`** |
| 4 | `production/pipeline.py:439` sets `order["prior_high20"] = setup.get("prior_high20")` |
| 5 | `src/agents/setup_agent.py:94` computes `prior_high20` **only inside the breakout path**, and returns it at line 165/170; the dispatcher returns `best.get("prior_high20")` (line 321) |
| 6 | **Setup v1 enables only `pullback`** (`cfg.setup_enabled_types = ["pullback"]`), so the pullback dict is always `best` and has no `prior_high20` key → the dispatcher always returns `None` |
| 7 | verified live: `evaluate_setup(...)` returns `prior_high20 = None` for valid pullback setups (AAPL/MSFT/NVDA/COHR test) |

**Consequence:** the frozen Setup v1 clause `max_extension_from_pivot_pct = 0.03` (documented in
`src/agents/SETUP_V1_FREEZE.md` and in the production pipeline's execution conventions) **cannot fire
in production**, because its input never exists. The guard `if ext is not None and ext > cfg.…` is
always skipped.

**This is a contract/reality mismatch, not a behaviour bug:** no decision is changed by it (it only
fails to *block* entries), and no trade in the window was affected. But it means the extension
filter's *value* has never been tested, and any future attempt to evaluate it would silently pass
everything. The same root cause makes the `components` attribution field permanently empty for v1
trades (it too is breakout-only), which is why component-level attribution is impossible from the
records.

Fixing it requires an architecture decision: `src/agents/setup_agent.py` is the canonical frozen
Setup v1 implementation.

## D4 — Risk & sizing

| | target | realised |
|---|---|---|
| risk per trade | 1.00 % of equity | **mean 0.334 %** (median 0.335, max **0.499**) |
| position value | ≤ 25 % of equity | mean **7.37 %** (max 16.30) — **0 trades at the cap** |

* **No trade ever used the full 1 % risk budget; the ceiling observed is 0.50 %.** Because
  `eff = regime_mult × quality_mult`, and `regime_mult ≤ 0.5` and `quality_mult ≤ 1.0`, the maximum
  attainable risk is **0.5 %**, and integer-share flooring reduces it further to ~0.33 %.
* R distribution: mean +0.182, median +0.021, 50.3 % positive, 41.1 % ≥ +1 R, 49.7 % ≤ −1 R.
* Max consecutive losing trades **12**; worst R-sequence drawdown **−13.56 R**.
* The *risk limits* (position cap, cash) are essentially never binding — the constraint is the
  **size of the risk budget**, not the limits on it.

## D5 — Exposure & binding constraints

| | value |
|---|---|
| regime labels | BEAR **185** / SIDEWAYS **160** / BULL **51** |
| `position_size_mult` | 0.5 ×211, **0.0 ×185** — **never 1.0** |
| veto flags | **`BEARISH_BREADTH_DIVERGENCE` ×139** (35.1 % of sessions) |
| sessions flat | **31.06 %** (0 positions) |
| sessions at `max_open_positions` (5) | **17.17 %** |
| average positions held | 2.37 |
| **average exposure** | **17.16 %** (cash drag 82.84 %) |

* The base multipliers are BULL 1.0 / SIDEWAYS 0.5 / BEAR 0.0, but the Bearish Breadth Divergence
  cap applies `min(x, 0.50)` whenever the HMM label is BULL and breadth is declining — **on all 51
  BULL sessions the multiplier was 0.5**, so the system never once ran at full size in this window.
* 185 sessions at multiplier 0 means new longs were impossible for **47 %** of the window.
* Binding-constraint order: **regime (185 sessions) > position budget (99) > risk-gate arithmetic
  (506 individual setups)** — three different mechanisms, all pushing exposure down.

## D6 — Exposure-adjusted benchmark gap

| benchmark | return |
|---|---|
| System | +7.99 % |
| SPY buy & hold | +36.26 % |
| SPY × average exposure (static) | +6.22 % |
| **SPY with the system's own daily exposure weight** | **+7.37 %** |
| SPY on the system's invested days only | +29.80 % |

* Raw gap **−28.27 pp**; gap at matched daily exposure **+0.62 pp**.
* Risk profile: MaxDD −5.98 % vs SPY's drawdown over the same window — the system did deliver its
  risk-control proposition; it simply deployed 17 % of the capital.
* The "SPY on invested days only" figure (+29.8 %) shows *when* the system was invested, SPY moved
  a lot — the cost is in the days it was flat/cash, not in the days it held.

---

## V. Independent verification (`production/tests/phase4_verify.py`)

Raw data: `reports/phase4_verify_2026-10-01.json`. Read-only; the same unchanged backtest is
replayed to capture records; nothing is modified or re-simulated.

### V1 — the risk-gate attribution is confirmed by the real engine

Every one of the 669 sizing attempts was replayed through
`production.risk.risk.size_swing_position` on the reconstructed inputs:

| | |
|---|---|
| attempts replayed | 669 |
| `allow` agrees with the record | **667 (99.7 %)** |
| mismatches | 2 — both the known `local_open` increment subtlety (the pipeline increments its own open-counter as it accepts buys; the replay starts from the session's opening count) |
| reject causes (engine's own string) | 504 × *"風險預算 $X 不足以買 1 股"*, 2 × *"已達最大持倉數"* |
| **verdict** | **re-derivation CONFIRMED** |

The 2-case difference between the diagnostic (505 budget / 1 other) and this replay
(504 budget / 2 position-cap) is exactly those two increment cases; both agree the dominant cause is
the risk budget, not the position-value cap. **The earlier "92 = single-position value cap"
classification in the first diagnostic run was a mislabelling of my own re-derivation and is corrected
above.**

### V2 — the exposure-vs-selection decomposition is robust

| window | system | SPY | avg exposure | SPY daily-matched | **selection gap** | raw gap |
|---|---|---|---|---|---|---|
| full | +7.99 % | +36.26 % | 17.20 % | +7.37 % | **+0.62 pp** | −28.26 pp |
| 2024 | +4.13 % | +25.59 % | 19.41 % | +4.17 % | **−0.04 pp** | −21.46 pp |
| 2025 | +3.71 % | +8.76 % | 13.45 % | +3.08 % | **+0.63 pp** | −5.05 pp |
| first half | +2.59 % | +24.79 % | 19.53 % | +4.28 % | −1.69 pp | −22.20 pp |
| second half | +5.16 % | +10.04 % | 14.79 % | +3.23 % | +1.93 pp | −4.88 pp |

**The selection gap is within ±2 pp in every window** while the raw gap is −5 to −22 pp → the
"gap = exposure, not skill" conclusion is not a period artefact.

### V3 — how much of the sizing problem is the multiplier? (arithmetic only)

| effective multiplier | sizeable setups | share | Δ vs recorded |
|---|---|---|---|
| as recorded (≈0.375 = 0.5 × 0.75) | 164 / 669 | 24.5 % | — |
| doubled to 0.5 | 265 / 669 | 39.6 % | **+101** |
| regime multiplier 1.0 (quality kept) | 401 / 669 | 59.9 % | **+237** |
| full 1.0 (regime × quality removed) | 485 / 669 | 72.5 % | **+321** |

Two things follow: **(a)** the halved regime multiplier and the quality multiplier are large
*arithmetic* levers; **(b)** even at an effective multiplier of 1.0, **184 setups (27.5 %) remain
unsizeable** — there is a residual floor caused by the account size versus ATR stop distances. So
both the multiplier lever and the capital-base lever are real; this table **does not** say the extra
setups are profitable (see the caveat in §7/#1 and D3's BULL cohort).

### V4 — entry-gap stability → **claim refuted, candidate downgraded**

See the sub-period table in §D3. The full-window monotonicity is a 2024 effect and reverses in 2025.
Flagged as a correction rather than a finding.

---

## 7. Ranked research candidates

Ranking rule was fixed in advance (Phase-4 plan §5): **attributable controllable gap → interface
cost → falsifiability.** All estimates below are *within this window only*.

| # | Candidate | Evidence | Estimated attributable gap | Controllability | Interface cost | Falsifiable criterion |
|---|---|---|---|---|---|---|
| **1** | **Capital deployment mechanics** (risk budget insufficient for one share; halved multiplier) | 506/669 valid setups unsized, **505 = the engine's own "budget insufficient for 1 share"**; mean budget $4.95; realised risk 0.33 % vs 1 % target; avg exposure 17.16 %; V3: multiplier lever worth +237 sizeable setups, residual floor 27.5 % | **Dominant for the gap** — the −28.26 pp raw gap is ≈ exposure (V2: +0.62 pp selection, robust in all windows). **But the *value* of the marginal cohort is not yet measured** | High (arithmetic + capital base) | **Medium** — sizing arithmetic is not frozen; `risk_per_trade`, regime multipliers and `max_open_positions` are | Reduce budget-insufficient rejections and lift realised risk/trade toward target **while Sharpe and MaxDD do not degrade**, confirmed out-of-window |
| **2** | ~~Entry gap quality~~ → **downgraded to "measure first"** | Full window looks monotone (<0 % +0.399 R → 1–2 % −0.094 R) but **V4 refutes stability**: 2025 reverses (+0.618 R for the 1–2 % cohort), cells have only 8–20 trades | Originally −22.5 USD in the losing cohort; **now treated as unquantified** | High (single threshold) | **High** — `max_entry_gap_pct` is Setup v1 (frozen) | Requires a *stability* result first: the gap→outcome relation must hold in more than one period before it is worth a parameter experiment |
| **3** | **Exit target efficiency** | TP fills at 1.667 R with 2.25 R MFE; give-back 0.585 R/winner; theoretical ≤ 47 R | Potentially large but **unattainable in full** (needs peak-perfect exits) | High (one parameter) | **High** — Stop/Exit v1 freeze face | A wider/trailing target rule raises sumR/PF without degrading MaxDD |
| **4** | **Portfolio constraint count** (`max_open_positions`) | Budget full on 99 sessions (25 %); the real engine also rejected 2 setups for this reason in-replay | **Unmeasurable from these artifacts** (setup evaluation is skipped when blocked) | Medium | Medium (portfolio constraints) | Only after foregone-signal volume is made observable |
| **5** | **Regime calibration** (185 BEAR sessions; divergence cap) | 47 % of the window at multiplier 0; cap fired on 139 sessions | Large *mechanically* (+237 in V3) but **counter-indicated**: BULL entries averaged −0.321 R, i.e. the cap was suppressing size on the worst cohort | Low | **Very high** — Regime v1 frozen | Not proposed in this phase |
| **6** | **Capital base / account size** | V3: even at full multiplier, 27.5 % of valid setups stay unsizeable | Interacts with #1 (the residual floor) | High (operational) | **None** (no code change) | Same criterion as #1; the V3 residual isolates it |

## 8. Recommended next target

**#1 — Capital deployment mechanics**, stated as one sentence:

> *Hypothesis: the system's return gap is caused by capital never being deployed (risk budget
> `floor()`s to zero shares on 82 % of rejected setups and the regime multiplier never exceeds 0.5),
> so the next change must raise the **deployed** risk per trade toward the configured 1 % rather than
> change selection or exit logic.*

**Measurable success criterion (risk-adjusted, not raw return):** on the same frozen pipeline and
window, with the same or fewer trades — (a) the share of valid setups rejected for
"risk budget rounds to zero" falls materially from 81.6 %, and (b) realised risk per trade rises
toward the configured target, **while Sharpe and MaxDD do not deteriorate**, and (c) the result
holds in a *separate* window (the 2018–2025 period) before any promotion.

**Why this and not the others**

* **The first step must be a VALUE measurement, not a parameter change.** The diagnostic has
  established the *mechanics* (exposure 17 %, budget floors to zero, multiplier never > 0.5) and that
  the gap is exposure rather than selection (V2, robust). What it has **not** established is whether
  the *marginal* setups that a larger budget would admit are worth taking — and D3's BULL result
  (avgR −0.321) warns they may not be. Proposed step 0 (read-only, no config change, no backtest
  re-run): evaluate the forward outcome of the 505 budget-rejected setups over a fixed horizon using
  the cached bars, split by the reason they were rejected. **If their expectancy is at or above the
  accepted cohort's, the deployment lever deserves an experiment; if it is ≤ 0, the cap is
  protective and the target moves elsewhere.**
* **Entry (#2) is now downgraded, not because the effect is small but because it is unstable.**
  V4 shows the full-window monotonicity is a 2024 effect that reverses in 2025 (+0.618 R for the
  cohort the current filter permits). With 8–20 trades per 2025 cell it is not yet a signal; it needs
  a stability result before it warrants a frozen-Setup-v1 change.
* **Exit (#3) is second-order.** Only 3 stop-outs had ever been ≥ +1 R (the stop is not the problem),
  and the 47 R give-back is a perfect-timing upper bound; the realistic capture is a fraction.
* **Portfolio count (#4) cannot be ranked yet** — the diagnostic cannot observe what was skipped when
  the budget was full, so its magnitude is unknown. Making that observable is a prerequisite.
* **Regime (#5) is not proposed**, despite being the largest *mechanical* lever (V3: +237), because
  D3 shows the BULL cohort was the **worst** performer (avgR −0.321). Removing the divergence cap
  would have increased size on the losing cohort. **"Deploy more" is not the same as "deploy more
  everywhere."**
* **Capital base (#6) is inseparable from #1** and is the only lever that needs no code change at
  all; V3's residual (27.5 % still unsizeable at full multiplier) isolates its contribution.

## 9. Uncertainties and limitations (must constrain the next phase)

1. **Single window, bull market.** 2024-01→2025-07 is a strong up-market; mechanically more exposure
   would have improved raw return *regardless of skill*. Every conclusion above is window-bound, which
   is why the success criterion is risk-adjusted and requires out-of-window confirmation.
2. **D6's matched benchmark is an approximation.** It applies the system's own daily exposure weight
   to SPY's daily return; it ignores trade timing within the day, costs, and cash interest. It is
   evidence for "exposure explains most of the gap", not a performance claim.
3. **MFE/MAE use daily bars** → intrabar path unknown → the "give-back" figures are upper bounds.
4. **Risk-rejection causes were first re-derived, then replaced by the engine's own verdict** (§V1:
   99.7 % agreement on all 669 attempts, 2 explained mismatches). Inputs use the previous session's
   equity/cash (cash is an upper bound because same-day open fills are not subtracted). Nothing was
   re-simulated.
5. **Small-sample cohorts.** Several D3 cells (and every 2025 gap cell) contain only 8–20 trades;
   this is precisely why §V4 refuted the entry-gap claim. Any cohort-level conclusion in this report
   with n < 30 should be treated as a hypothesis, not a result.
6. **Foregone setups are unobservable** when entries are blocked (the pipeline skips setup evaluation
   entirely), so D5's opportunity cost is a **lower bound**.
7. **Record-quality gaps found:** `holding_days` is `null` for all 151 trades (written as `None` by
   `src/state/state.py:100` "由呼叫端填" and never filled by the caller); `components` is always `{}`
   (inherited from the v3-era position schema); `extension_from_pivot_pct` is **null for all 151
   trades** — its input `prior_high20` is never produced by the Pullback-only setup path (see §D3a);
   the risk
   gate stores only `allow`; `veto_flags` and the entries-phase equity/cash are not persisted.
8. **`r_multiple` is quantised** (+1.667 for targets, −1.0 for stops), so R-distribution statistics are
   coarser than they appear.
9. **The multiplier counterfactual (V3) counts setups, not profit.** A "sizeable" setup is one the
   engine would accept at that budget; V3 says nothing about whether those trades would have made
   money. It must not be read as a performance estimate.
10. **Stop/exit evidence is backtest-only**; the Phase-3 live shadow gate is still **PENDING (0 live
    sessions)**, so exit behaviour in live operation remains unevidenced.

## 10. NEEDS HUMAN REVIEW

1. **Approve Step 0 — the marginal-cohort value measurement** (read-only, no parameter change, no
   backtest re-run): evaluate the forward outcome of the 505 budget-rejected setups over a fixed
   horizon from the cached bars, split by rejection reason. This is the cheapest test that can
   decide whether the deployment lever (#1) is worth a real experiment. **Recommended first action.**
2. **Approve or reject the recommended target (#1)**, and decide **which lever** is in scope:
   (a) the sizing arithmetic/flooring, (b) `risk_per_trade`, (c) the regime base multipliers and/or
   the divergence cap (frozen), (d) `max_open_positions`, (e) the **capital base** (~$1,282 — V3's
   27.5 % residual points at it, but it is the one lever that needs no code change).
3. **Accept the §V4 correction** that the entry-gap signal (candidate #2) is not stable and is
   downgraded to "measure first"; confirm whether `max_entry_gap_pct` (frozen Setup v1) should be
   parked until a stability result exists.
4. **The counter-intuitive BULL result**: 24 BULL-regime entries averaged −0.321 R while the
   divergence cap suppressed size on all 51 BULL sessions. Confirm how this should inform the Regime
   v2 backlog (it suggests the cap was *protective*).
5. **Record-quality fixes** (separate small task, no strategy impact): persist `holding_days`,
   `components`, the risk `reasoning`, the entries-phase equity/cash and `veto_flags` so future
   diagnostics do not need in-process capture or reconstruction.
6. **NEW — the inert extension filter (§D3a).** Setup v1 is documented with a frozen
   `max_extension_from_pivot_pct` filter, but its input (`prior_high20`) is produced only by the
   breakout path, which v1 disables — so the clause cannot fire. Decide whether to (a) leave the
   freeze document as-is and record the clause as inert, (b) re-derive an equivalent pivot for
   Pullback (an architecture decision), or (c) remove the clause from the documented contract.
   **No behaviour changed and no trade was affected**; the issue is contract accuracy.
7. **No commit / no push performed** (AGENT WORKFLOW: agent prepares, human commits).

---

### Deliverables in this phase

| Artifact | Purpose |
|---|---|
| `production/tests/phase4_diagnostics.py` | the D1–D6 runner (analysis path; wraps the existing backtest in-process) |
| `production/tests/phase4_verify.py` | independent verification (V1–V4) of the load-bearing claims |
| `reports/phase4_diag_data_2026-10-01.json` | all raw diagnostic numbers |
| `reports/phase4_verify_2026-10-01.json` | all raw verification numbers |
| `reports/phase4_diagnostics_2026-10-01.md` | this report |
| `reports/phase4_review_sheet_2026-10-01.md` | 2-minute sign-off sheet |
| `reports/phase5_plan_2026-10-01.md` | Phase-5 lever menu + pre-registered experiments (plan only) |
| `production/main.py` | approved CLI wiring: `--exit-engine-mode {legacy,shadow,new}` |
| `production/tests/test_wiring_safety.py` | +1 test (CLI mode access) → 18 tests |
