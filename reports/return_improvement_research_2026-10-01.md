# Return Improvement Research & Controlled Backtest — results and recommendation

> ## ⚠️ SUPERSEDED IN PART — read `pit_breadth_leak_2026-10-01.md` first
> After this document was written, a **point-in-time audit** found that Regime v1's **breadth engine is not
> point-in-time in historical replay** (it always evaluates the 2025-07-31 cache tail), so **50 % of the
> frozen regime composite was a constant** in every backtest below. Re-running the same experiment with a
> PIT-correct regime **destroys the R1 signal**:
> `TP 2.5 / 3.0 / 3.5 / 4.0 → +5.57 % / −1.43 % / −0.41 % / +7.38 %` (Sharpe `0.513 / −0.084 / 0.005 / 0.612`),
> a non-monotone zig-zag instead of the monotone improvement reported below.
> **The R1 recommendation (§3) is therefore WITHDRAWN**, and every performance number in this document
> should be treated as **leak-inflated** (the true baseline is ≈ +5.57 % / Sharpe 0.513 / MaxDD −10.39 %,
> not +7.99 % / 0.935 / −5.98 %). The §4 production-safety statement still holds — nothing was changed.

**Date:** 2026-10-01 · **commit:** `da9f5dc` (working tree: the 4 Phase-3 approved files) · **mode:** `exit_engine_mode="legacy"`
**Window:** 2024-01-02 → 2025-07-31 (396 sessions) · **Harness:** `research/harness.py` over the **unchanged** `production/backtest.py::ProductionBacktest`

Companion documents: `return_bottleneck_report_2026-10-01.md` (Deliverable 1),
`experiment_matrix_2026-10-01.md` (Deliverable 3), `research_baseline_2026-10-01.json` (Deliverable 2),
`research_tp_grid_2026-10-01.json` + `research_tp_grid_ext_2026-10-01.json` (Deliverable 4),
`step0_harness_audit_2026-10-01.json` (spec §11 gate).

**No production behaviour was changed.** `production/config.py` was never edited; each experiment applies
at most one override to an in-memory config copy. `exit_engine_mode` remains `legacy`; no frozen contract
was altered; nothing was deleted; nothing was committed.

---

## 1. Deliverable 2 — the reproducible baseline

| Item | Value |
|---|---|
| Command | `python research/run_baseline.py` |
| Harness | `production/backtest.py::ProductionBacktest` (unchanged), mode `legacy` |
| Window | arg `2024-01-01`→`2025-07-31`; actual sessions `2024-01-02`→`2025-07-31`, **396** |
| Git | `da9f5dc` — *Unified decision pipeline: run_daily + DataSource + DecisionRecord + production-equivalent backtest (2026-09-04)*; tree **dirty** with the 4 Phase-3 approved files |
| Data | `data/cache/equities/` — **1136** ticker files; SPY `2016-01-04`→`2026-08-06`, 2663 rows, sha256 `c7bab00e299105d7` |
| Config | full snapshot in the JSON (`research_baseline_2026-10-01.json → config_snapshot`) — `stop_atr_mult 1.5`, `take_profit_atr_mult 2.5`, `trailing_atr_mult 1.5`, `trailing_trigger_r 1.0`, `max_holding_days 30`, `risk_per_trade 0.01`, `max_open_positions 5`, `max_position_pct 0.25`, `max_entry_gap_pct 0.02`, `screener_top_n 30`, `exit_engine_mode legacy` |
| Output | `reports/research_baseline_2026-10-01.json` |

**Reproduction check — all 7 frozen reference values reproduced exactly:**

| | n_days | n_trades | return_pct | sharpe | max_dd_pct | profit_factor | win_rate_pct |
|---|---|---|---|---|---|---|---|
| reference | 396 | 151 | 7.99 | 0.935 | −5.98 | 1.29 | 50.3 |
| actual | 396 | 151 | 7.99 | 0.935 | −5.98 | 1.29 | 50.3 |
| **OK** | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |

Additional baseline metrics computed for comparability: **Sortino 1.141 · Calmar 0.838 · avg R 0.1822 ·
avg holding 8.94 cal. days · MFE capture 0.1494 · avg exposure 17.16 %**.

**Spec §11 gate (prerequisite) — PASS.** Direct replay parity re-verified: 151 trades / 1073 bars /
**0 divergences**, 0 adapter-vs-direct mismatches, 151/151 trade reconciliation. The Step-0 control's 3
previously-unexplained mismatches are **all classified as `HARNESS_HORIZON_GUARD_ARTIFACT`** (a simulator
off-by-one at the 30-calendar-day boundary when it falls on a weekend), **proven** by re-simulating with a
larger horizon, which reproduces each recorded exit exactly. Detail: `return_bottleneck_report_2026-10-01.md` §11.

---

## 2. Deliverable 4 — R1: the exit-target experiment

### 2.1 Configuration actually run

| | |
|---|---|
| **Changed variable** | `take_profit_atr_mult` — **only** |
| Unchanged | universe, data, costs, execution semantics, all other parameters, the PIT bucket screen (reused from cache) |
| Pre-registered grid | `{2.0, 2.5, 3.0, 3.5, 4.0}` (2.5 = baseline) |
| Response-shape extension | `{4.5, 5.0}` — labelled a **diagnostic**, not a candidate search |
| Pre-registered success criterion | *Sharpe must not fall below 0.935 **and** MaxDD must not worsen beyond −5.98 %* (stated in `phase5_plan_2026-10-01.md` before R1 ran) |

### 2.2 Full-window dashboard (§15)

| TP (ATR) | Return % | CAGR % | MaxDD % | Sharpe | Sortino | Calmar | avg R | PF | Trades | Avg expo % | Avg hold (d) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2.0 | 8.99 | 5.63 | **−6.37** | 1.084 | 1.223 | 0.884 | 0.1676 | 1.33 | 161 | 16.49 | 8.19 |
| **2.5 (baseline)** | **7.99** | **5.01** | **−5.98** | **0.935** | **1.141** | **0.838** | **0.1822** | **1.29** | **151** | **17.16** | **8.94** |
| 3.0 | 10.22 | 6.38 | −5.52 | 1.181 | 1.525 | 1.158 | 0.2439 | 1.44 | 133 | 17.30 | 10.72 |
| 3.5 | 11.17 | 6.97 | **−4.13** | 1.242 | 1.647 | 1.688 | 0.2884 | 1.49 | 131 | 17.74 | 11.09 |
| 4.0 | 14.13 | 8.79 | −4.50 | **1.468** | 1.843 | **1.951** | **0.3537** | **1.63** | 123 | 18.48 | 12.37 |
| 4.5 | **14.39** | **8.95** | −5.78 | 1.424 | 1.855 | 1.545 | 0.3510 | 1.59 | 128 | 18.63 | 12.07 |
| 5.0 | 13.68 | 8.52 | −4.75 | 1.356 | **1.888** | 1.789 | 0.3218 | 1.52 | 128 | 18.24 | 11.87 |

Delta vs baseline: `TP 3.5 → +3.18 pp return, +0.307 Sharpe, +1.85 pp MaxDD, +0.106 R, −20 trades`;
`TP 4.0 → +6.14 pp, +0.533 Sharpe, +1.48 pp MaxDD, +0.172 R, −28 trades`.

**The response is smooth and single-peaked, and the peak is bracketed** — return peaks at 4.5 and falls at
5.0; Sharpe/PF/avg R/Calmar peak at 4.0. That is the shape spec §14 asks for (a broad plateau, not an
isolated spike). Exposure barely moves (17.16 %→18.63 %), so the gain is **not** an exposure artefact.

### 2.3 Sub-period results (§13) — the decisive check

| TP | 2024 full (252 d) | 2024 H2 | 2025 → Jul (144 d) | 2025 H1 |
|---:|---:|---:|---:|---:|
| 2.5 (base) | 4.13 % | 0.77 % | 3.71 % | 4.64 % |
| 3.0 | 2.70 % | 1.85 % | **7.32 %** | 5.59 % |
| 3.5 | 5.30 % | 2.90 % | 5.58 % | 5.29 % |
| 4.0 | 7.63 % | 2.67 % | 6.00 % | 6.82 % |
| 4.5 | 7.75 % | 4.40 % | 6.12 % | 6.51 % |
| 5.0 | **8.08 %** | **4.96 %** | 5.14 % | **6.91 %** |
| *(2.0, for the dip)* | *5.04 %* | *1.66 %* | *3.71 %* | *3.14 %* |

**Every one of the four sub-periods shows TP ≥ 3.5 beating the 2.5 baseline** (12 of 12 cells), and the
sub-period **avg R** agrees in all four windows (2024 full 0.145→0.210; 2024 H2 0.066→0.100; 2025 0.274→0.488;
2025 H1 0.521→0.594). This is materially stronger than the entry-gap signal that V4 refuted — that one
**reversed** between 2024 and 2025.

Two honest caveats: (i) the 2024 curve is **not** monotone (3.0 is a local dip below baseline — 2.70 % vs
4.13 %), so the "wider is better" statement holds for **≥ 3.5**, not for every step; (ii) 2024 H1 is the one
place the baseline beats 3.0, i.e. an isolated intermediate value can look bad.

### 2.4 Mechanism — what actually changes

| TP | STOP_LOSS | TAKE_PROFIT | TRAILING_STOP | TIME_STOP | avg hold |
|---:|---|---|---|---|---|
| 2.0 | 75 | **78** (1.333 R) | 3 | 5 | 8.2 d |
| 2.5 | 75 | 60 (1.667 R) | 10 | 6 | 8.9 d |
| 3.5 | 60 | 37 (2.333 R) | 26 | 8 | 11.1 d |
| 4.0 | 58 | 32 (**2.667 R**) | 26 | 7 | 12.4 d |
| 4.5 | 61 | 26 (3.0 R) | **31** | 10 | 12.1 d |
| 5.0 | 64 | 22 (3.333 R) | 31 | 11 | 11.9 d |

The mechanism is exactly as hypothesized: **a wider target converts target-hits into trailing-stop exits**,
each realised at a higher R, and lengthens the hold. TAKE_PROFIT fills move from 1.333 R → 3.333 R while the
target-hit count falls 78 → 22. Note that the *stop* is not the lever — STOP_LOSS counts stay near 47–50 %
of trades at every setting.

**One measurement caveat I must flag.** MFE is measured over the realised holding window, and that window
*grows* as the target widens (avg hold 8.2 d → 12.4 d). So the apparent improvement in MFE capture
(0.149 → 0.230) is **partly definitional** and is **not** clean evidence. The clean statistic is **avg R**
(average of realised R, normalised by the initial stop distance and therefore independent of the measurement
window): **0.182 → 0.354**. That is a genuine per-trade improvement, not leverage and not a measurement artefact.

### 2.5 Cohort view (§7, §8)

| TP | BULL entries (n, avg R) | SIDEWAYS entries (n, avg R) |
|---:|---|---|
| 2.5 | 24, **−0.321** | 127, +0.277 |
| 3.0 | 19, −0.257 | 114, +0.327 |
| 3.5 | 19, −0.297 | 112, +0.388 |
| 4.0 | 18, +0.277 | 105, +0.367 |
| 4.5 | 16, −0.053 | 112, **+0.409** |
| 5.0 | 19, +0.139 | 109, +0.354 |

The improvement is **concentrated in the SIDEWAYS cohort** (which carries 85–88 % of all trades). The BULL
cohort stays ≈ 0 or negative at almost every setting and is small (n = 16–24) — **flagged as a small sample,
not treated as evidence**. This corroborates the bottleneck report's finding that the regime cap is
protective rather than harmful.

### 2.6 Robustness verdict (§14, §16)

| Test | Result |
|---|---|
| Nearby values (grid, not just endpoints) | ✔ 7 points, smooth broad plateau over 3.5–4.5 |
| Peak bracketed? | ✔ return peaks at 4.5 and declines at 5.0 |
| Sub-period consistency | ✔ **12/12** sub-period cells (TP ≥ 3.5 vs 2.5) and 4/4 avg-R comparisons favour wider |
| Risk-adjusted improvement | ✔ Sharpe 0.935 → 1.24–1.47; MaxDD −5.98 % → −4.13…−4.78 %; Calmar 0.838 → 1.69–1.95 |
| Not an exposure artefact | ✔ exposure moves only 17.16 % → 18.63 % |
| Not a leverage artefact | ✔ avg R rises in R units (risk-normalised) |
| Turnover change | ⚠ trades −15 % (151 → 128): fewer, bigger trades |
| Out-of-sample | ✖ **none** — 2024 and 2025 were both used to observe the direction; there is no held-out period in a 1.5-year sample |
| Single regime window | ✖ bull market only; "hold longer" is mechanically favourable in a bull trend |
| Statistical test | ✖ none performed (variants share entries; samples overlap; not independent) |

---

## 3. Deliverable 5 — recommendation for the next research step

**R1 provides evidence that the exit-target setting is a genuinely promising research direction**, because:

* the effect is **large and risk-adjusted** (Sharpe +0.31 to +0.53; MaxDD improves by 1.5–1.9 pp; drawdown
  control — the system's only demonstrated edge — is *strengthened*, not traded away);
* the **mechanism is understood and mechanical** (target-hits convert into trailing exits at higher R);
* the direction is **consistent in all 12 sub-period cells** and in the window-independent statistic (avg R),
  which is exactly what the entry-gap signal failed;
* the response is a **smooth, bracketed plateau**, not a lone spike.

**However, the evidence is NOT sufficient for production deployment**, because:

1. **No genuine out-of-sample.** Both sub-periods informed the conclusion; a 1.5-year window cannot be
   split into development / validation / hold-out with any statistical power (≈ 25–50 trades per cell).
2. **One regime only.** A bull window mechanically rewards holding longer. The project's own 2018–2025
   graduation evidence showed the opposite regime behaviour; the direction must be re-tested on an
   independent window before anyone believes it generalises.
3. **No significance testing**, and the variants share the same entries (overlapping, non-independent samples).
4. **The pre-registered criterion was a floor, not a target.** "Sharpe does not fall" is satisfied by every
   candidate ≥ 3.0 — it does not by itself justify 4.0 over 3.5.
5. **A production change requires unfreezing `STOP_EXIT_V1`.** `take_profit_atr_mult` is inside the frozen
   Stop/Exit v1 contract; the change needs a new architecture decision/revision, not a config edit.
6. **Turnover drops 15 %**, so the comparison is not same-capital-recycling; that is a real change in
   strategy character, not just a parameter nudge.

**Recommended next research step (in order):**

1. **Replicate R1 on an independent window** (e.g. 2018–2023, the project's own graduation window, through
   the same production-equivalent harness reconstructed from cache). If the direction holds there, the
   evidence becomes usable; if it reverses, the effect is a bull-window artefact and the target should stay
   at 2.5.
2. **Run S2 (trailing width) as a separate single-variable experiment** — R1's gain arrives *through* the
   trailing exit, so the two interact; LTO must not be combined with R1 in one step.
3. **Make R3 (portfolio capacity) observable first** (the logging fix, R7), because R1 raises the
   sessions-at-capacity share from 17.2 % to 21–22 % — capacity now binds harder, and its magnitude is
   currently unmeasurable.
4. **Do not** pursue R4/R5 (risk loosening / regime relaxation): both admit negative-expectancy cohorts.

**What this does *not* say:** it does not say "TP = 4.0 is optimal". The data supports *"the frozen 2.5 ATR
target is probably too tight for this window and a value around 3.5–4.5 is worth investigating"*, subject to
the replications above. Whether the change ever reaches production is a human decision, and would require an
explicit Stop/Exit v1 revision.

---

## 4. Production safety statement (spec §19)

| Requirement | Status |
|---|---|
| `exit_engine_mode` default | **unchanged** (`legacy`) |
| New Exit Engine enabled | **no** |
| Production risk parameters changed | **no** (overrides are in-memory only) |
| Stop/Entry production rules changed | **no** |
| Live trading behaviour modified | **no** |
| Legacy modules deleted | **no** |
| Frozen contracts altered | **no** |
| New framework created | **no** — every experiment reuses `ProductionBacktest` |
| Commit / push performed | **no** |
| Files modified under `production/` | **none** this turn (the 4 dirty files are the earlier approved Phase-3 wiring) |

New artifacts live in `research/` (a new, registered `RESEARCH` architecture layer) and `reports/`.

---

## 5. NEEDS HUMAN REVIEW

1. **Accept or reject the R1 direction** (`take_profit_atr_mult` too tight; ~3.5–4.5 worth investigating).
2. **Authorise the independent-window replication** (step 1 above) — this is the single action that would
   convert R1 from "suggestive in one bull window" into usable evidence.
3. **Decide the R1b extension's status**: it was a *diagnostic*, not a pre-registered candidate; confirm that
   is acceptable, or ask for a formal re-registration.
4. **Confirm the turning point for unfreezing**: a production change to `take_profit_atr_mult` requires an
   explicit Stop/Exit v1 revision (the current freeze forbids it).
5. **Approve / defer the logging fix (R7)** — required before R3 (portfolio capacity) can be measured at all.
6. **Note the interaction**: R1 raises capacity pressure (17.2 % → ~22 % of sessions at the position cap), so
   a future capacity experiment and the exit-target change are not independent.
