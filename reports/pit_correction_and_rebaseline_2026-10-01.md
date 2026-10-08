# PIT Correction & Full Research Re-baseline — decision report

**Date:** 2026-10-01 · **Commit:** `da9f5dc` (+ uncommitted work) · **Mode:** `exit_engine_mode = legacy`
**Approval:** human sign-off 2026-10-01 for (1) the PIT fix at the loader boundary, (2) the full re-baseline,
(3) the re-run of D1–D6 / V1–V4 / Step 0 / R1, (4) exposing the breadth tail date, (5) the freeze audit.

Companion documents: `pit_breadth_leak_2026-10-01.md` (the finding),
`SUPERSEDED_PIT_ARTIFACTS.md` (audit legend), `pit_breadth_audit_2026-10-01.json` (before/after evidence).

---

## A. PIT root cause

**What was wrong.** `production/pipeline.py::run_daily` asked for a point-in-time breadth series:

```python
breadth_df = load_breadth(cfg, end=as_of)          # production/pipeline.py:158
```

`load_breadth` → `regime_dual_engine/pit_breadth_data.get_breadth("pit", rebuild=False, end=end)`, whose
cached path was:

```python
if os.path.exists(path) and not rebuild:
    return pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")   # `end` IGNORED
```

`end` was honoured **only on rebuild**, so whenever the cache existed (always, in practice) the caller
received the **entire 2016-01-04 → 2025-07-31 file**. `regime_dual_engine/engine.py::compute_regime_decision`
then consumes the *tail* of whatever it is given:

```python
breadth_now     = float(breadth_df["pct_above_50dma"].iloc[-1]) / 100.0        # -> 2025-07-31
breadth_10d_ago = float(breadth_df["pct_above_50dma"].iloc[-1 - lb]) / 100.0
breadth_score   = breadth_engine.breadth_percentile_score(breadth_df["pct_above_50dma"], ad_line, cfg)
```

**Exactly why this creates look-ahead.** Every historical `as_of` therefore evaluated the **same fixed
row** (2025-07-31) and a trailing percentile window that always ended there. Measured:

| as_of | shipped `pct` | shipped `breadth_now` | PIT-correct `pct` | PIT-correct `breadth_now` |
|---|---:|---:|---:|---:|
| 2018-06-29 | **41.746** | **57.23** | 27.738 | 52.87 |
| 2020-03-31 | **41.746** | **57.23** | 11.786 | 3.88 |
| 2022-06-30 | **41.746** | **57.23** | 17.063 | 15.70 |
| 2024-01-31 | **41.746** | **57.23** | 55.992 | 64.38 |
| 2025-07-31 | **41.746** | **57.23** | **41.746** | **57.23** |

The last row is the fingerprint: shipped and correct **coincide exactly at the cache tail** and nowhere else.

Two consequences:

1. **50 % of the frozen Regime v1 composite was a constant** (½ × 41.746 = 20.87 points) in every replay.
   The backtest was therefore *not* a replay of the frozen 50/50 HMM+Breadth decision function — it ran an
   effectively **HMM-only** regime with a fixed breadth offset and a fixed divergence-flag state.
2. The divergence guard (`HMM BULL + breadth declining`) was evaluated against a constant, so the
   regime's defensiveness was an artefact. This is why the earlier diagnosis concluded the multiplier
   "never reached 1.0" — under PIT-correct breadth it **does** (61 sessions).

**Affected**: every historical replay (Phase 3 shadow coverage, Phase 4 D1–D6/V1–V4, Phase 5 Step 0, R1)
and — as a separate live risk — any live run whose cache tail is older than the session (see §5 below).

**Not affected**: the frozen decision mathematics (it is correct given a PIT series), the Stop/Exit parity
harness (engine-vs-engine on identical entries), and the Phase-3 shadow comparison (both arms used the same regime).

---

## B. PIT fix

**Location chosen: the loader/data boundary** (the human-approved preference), so no caller has to remember
to slice.

`regime_dual_engine/breadth_data.py` gains the single definition of the contract:

```python
def slice_to_end(df, end, *, name="breadth"):
    """Keep only observations with date <= `end`. THE PIT CONTRACT."""
    if df is None or len(df) == 0 or end is None:
        return df
    ts = pd.Timestamp(end)
    if isinstance(df.index, pd.DatetimeIndex):
        return df[df.index <= ts]
    if "datetime" in df.columns:
        return df[pd.to_datetime(df["datetime"]) <= ts]
    raise TypeError(...)          # fail loud; never leak future rows
```

Applied **identically on both paths of both loaders** — the cache path *and* the rebuild path of
`pit_breadth_data.get_breadth` (PIT series) and `breadth_data.get_breadth_series` (current-constituent
fallback). On the rebuild path the helper slices only the **returned** frame; the **full built frame** is
still what gets written to the cache.

**Why this is the safest location.**

* It fixes the fallback loader too — the same defect was present in `get_breadth_series`, which
  `load_breadth` uses when the PIT cache is unavailable. Fixing only `get_breadth` would have left a
  second identical hole.
* Default behaviour is **unchanged**: the default `end="2025-07-31"` equals the cache tail, so all
  existing research callers (which use the default) receive byte-identical series. Verified: 
  `get_breadth("pit")` and `get_breadth_series()` still return 2408 rows.
* A caller that cannot be satisfied fails **loud** (`TypeError`) instead of silently returning future data.
* `end=None` explicitly means "the whole series" — an escape hatch for research that genuinely wants it.

**Nothing else changed.** No formula, weight, threshold, label, thrust/divergence rule, multiplier, Setup,
Stop, Exit, Risk or Portfolio rule was touched; no parameter value moved; `exit_engine_mode` stays `legacy`.

---

## C. Regression proof

`production/tests/test_pit_breadth.py` — **7/7 PASS**. It pins the exact discovered failure mode.

| # | Test | What it forbids |
|---|---|---|
| 1 | `test_1_different_as_of_do_not_share_the_future_tail` | the exact bug: probes 2018/2020/2022/2024/2025 and asserts the tail dates are all **distinct**, the breadth values are **not all identical**, and **only the final session** may coincide with the 2025-07-31 cache tail |
| 2 | `test_2_no_observation_after_as_of` | 8 `end` values × both loaders; every returned row's date ≤ `end`; a narrower request is a strict subset |
| 3 | `test_3_cache_path_pit_safe_and_cache_not_truncated` | the cached path returns ≤ `end` **and** the cache file is byte-identical before/after (a read must not rewrite it) |
| 4 | `test_4_rebuild_path_pit_safe` | redirects the cache path to a temp file and patches the builder: asserts the **returned** frame is sliced while the **cached** file holds the full frame; plus `slice_to_end` raises `TypeError` on an unsliceable frame |
| 5 | `test_5_exact_trading_date_is_selected` | an exact session date selects exactly that observation (content compared against the full series, count compared too) |
| 6 | `test_6_non_trading_day_as_of_uses_last_prior_session` | holiday (2024-01-01 → 2023-12-29), Saturday/Sunday (→ 2024-01-05), mid-week holiday (2024-07-04 → 2024-07-03); a pre-history `end` returns **EMPTY** on purpose |
| 7 | `test_7_ledger_exposes_breadth_tail_date` | end-to-end: two `run_daily` calls at different dates must report **different** breadth tails, and a historical run must report a tail ≤ its own as-of |

**Full suite after the fix: 124/124** (pit_breadth 7 · datasource 8 · pipeline 9 · backtest 5 · production 10 ·
contracts 18 · stops 19 · exit_engine 31 · wiring_safety 18) **plus the Regime v1 freeze anchor 11/11**.

---

## D. Corrected baseline

Command: `python research/run_baseline.py --out reports/research_baseline_pitcorrected_2026-10-01.json
--raw-out reports/production_bt_pitcorrected_2024-01-01_2025-07-31.json`
Window 2024-01-02 → 2025-07-31 · 396 sessions · unchanged universe/data/costs/execution/parameters · `legacy`.

| Metric | SUPERSEDED (leaky) | **CORRECTED (PIT)** |
|---|---:|---:|
| Return | +7.99 % | **+5.57 %** |
| CAGR | 5.01 % | **3.51 %** |
| Sharpe | 0.935 | **0.513** |
| Sortino | 1.141 | **0.557** |
| Calmar | 0.838 | **0.338** |
| MaxDD | −5.98 % | **−10.39 %** |
| Profit factor | 1.29 | **1.13** |
| Win rate | 50.3 % | **47.6 %** |
| avg R | 0.1822 | **0.1878** |
| Trades | 151 | **168** |
| Avg exposure | 17.16 % | **23.39 %** |
| Regime distribution | BEAR 185 / SIDE 160 / BULL 51 | **BEAR 136 / SIDE 174 / BULL 86** |

The pre-fix reference set is retained in `run_baseline.py::SUPERSEDED_PRE_PIT_REFERENCE` and every
contaminated artifact is marked `SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE`
(see `SUPERSEDED_PIT_ARTIFACTS.md`).

**Note.** With correct breadth the drawdown advantage is much thinner: MaxDD **−10.39 %** vs SPY −18.76 %
(previously −5.98 %), and Sharpe **0.513** vs SPY 1.218. The "drawdown control" proposition survives, but at
roughly **half** its previously believed strength, and risk-adjusted performance remains clearly inferior to
buy-and-hold in this window.

---

## E. Corrected D1–D6

Re-derived by `research/rerun_corrected_research.py` on the PIT-corrected regime, using the **existing**
diagnostics unchanged (only the output path redirected).

### D1 exit attribution

| Exit reason | n (was) | avg R | MFE (mean) | give-back |
|---|---:|---:|---:|---:|
| STOP_LOSS | 84 (75) | −1.026 | **0.483** (was 0.369) | — |
| TAKE_PROFIT | 67 (60) | 1.667 | **2.249** | **0.582** |
| TIME_STOP | 9 (6) | 0.560 | 1.267 | 0.708 |
| TRAILING_STOP | 8 (10) | 0.123 | 1.361 | 1.238 |
| **all** | **168** (151) | expectancy **0.1878**, total **+31.5 R** | | |

### D2 funnel

| Stage | Count | Conversion |
|---|---:|---:|
| Candidate ticker-evaluations | 969 (1037) | — |
| Valid setup | 610 (669) | 62.95 % |
| Risk-allowed | 185 (163) | **30.33 %** (was 24.36 %) |
| Filled trades | 168 (151) | 90.81 % |
| **candidate → filled** | | **17.34 %** (was 14.56 %) |

Rejection causes: **425 / 425 (100 %)** = the engine's own message *"risk budget insufficient for one
share"*; mean rejected budget **$5.24** (was $4.95).

Session-level blocks: `max_open_positions` **131 sessions** (was 99), `regime_defensive` **136** (was 185).

### D4 risk & sizing

| | leaky | **PIT** |
|---|---:|---:|
| Realised risk % of equity (mean / median / max) | 0.334 / 0.335 / 0.499 | **0.399 / 0.357 / 0.997** |
| Position value % of equity (mean / max) | 7.37 / 16.30 | **8.43 / 23.08** |
| R distribution: mean / median | +0.182 / +0.021 | **+0.188 / −0.562** |
| Share ≤ −1R | 49.67 % | **50.00 %** |

### D5 exposure & binding constraints

| | leaky | **PIT** |
|---|---:|---:|
| Avg / max exposure | 17.16 % / 57.10 % | **23.39 % / 87.84 %** |
| Cash drag | 82.84 % | **76.61 %** |
| Flat sessions | 31.06 % | **23.48 %** |
| Sessions at `max_open_positions` | 68 (17.17 %) | **96 (24.24 %)** |
| Avg positions held | 2.366 | **2.823** |
| Size-multiplier distribution | {0.5: 211, 0.0: 185} | **{1.0: 61, 0.5: 199, 0.0: 136}** |
| Veto flags | DIVERGENCE 139 | **DIVERGENCE 70, BREADTH_THRUST 27** |

### D6 exposure-adjusted benchmark gap

| | leaky | **PIT** |
|---|---:|---:|
| System vs SPY | +7.99 % vs +36.26 % | **+5.57 % vs +36.26 %** |
| Raw gap | −28.27 pp | **−30.69 pp** |
| SPY at the system's **daily** exposure weights | +7.37 % | **+5.75 %** |
| **Selection gap (system − matched)** | +0.62 pp | **−0.18 pp** |

### V1–V4

| | Result |
|---|---|
| **V1** risk-engine agreement | 609 / 610 (**99.84 %**); the one reject reason is the engine's own budget string → re-derivation **CONFIRMED** |
| **V2** decomposition stability | selection gap −0.18 pp full window; halves +0.43 / −0.44; calendar 2024 +2.30 / 2025 −2.36 → the raw gap moves (−30.7 / −20.5 / −8.4) while the selection gap stays near zero → **"exposure dominates" CONFIRMED** |
| **V3** sizing counterfactual (arithmetic only) | sizeable setups 184/610 (30.16 %) → 248 (eff. 0.5) → 358 (regime mult 1.0) → **421 (69.02 %)** at full multiplier. Even at 1.0, **31 % of valid setups remain un-buyable** → the account size imposes a residual floor |
| **V4** entry-gap stability | **still non-monotone**: 2024 `<0 %: +0.303, 0–0.5 %: +0.068, 0.5–1 %: +0.466, 1–2 %: −0.239`; 2025 `<0 %: −0.008, 0–0.5 %: +0.527, 0.5–1 %: −0.407, 1–2 %: +0.419` → the monotonicity claim stays **REJECTED** |

### Cohort detail (D3, corrected)

| Cohort | n | avg R | sum P&L |
|---|---:|---:|---:|
| SIDEWAYS entries | 120 | **+0.2495** | +108.69 |
| BULL entries | 48 | **+0.0334** | −41.57 |
| size multiplier **0.5** | 127 | **+0.2267** | **+102.20** |
| size multiplier **1.0** | 41 | **+0.0673** | **−35.08** |
| extension (pivot distance) | 0 with value | — | **unmeasurable — filter still INERT** |

---

## Classification of every previous conclusion

| # | Prior conclusion | Verdict | Evidence |
|---|---|---|---|
| 1 | "The benchmark gap is exposure, not decision quality" | **CONFIRMED** | selection gap −0.18 pp vs raw −30.69 pp; stable across windows |
| 2 | "Average exposure 17.16 %, cash drag 82.84 %" | **CHANGED** | 23.39 % / 76.61 % |
| 3 | "Realised risk 0.33 %, capped at 0.499 %" | **CHANGED** | 0.399 %, max **0.997 %** (the cap was a leak artefact) |
| 4 | "The regime multiplier never reached 1.0" | **REJECTED** | it reaches 1.0 on **61** sessions; the divergence cap fires 70 (not 139) times |
| 5 | "506 rejections, mean budget $4.95" | **CHANGED** | 425 rejections, $5.24; still 100 % one cause |
| 6 | "valid setup → risk-allowed 24.36 %" | **CHANGED** | 30.33 % |
| 7 | "BULL entries are the worst cohort (−0.321 R)" | **REJECTED/CHANGED** | BULL is now **+0.033 R** (48 trades); the old figure came from the constant-breadth regime |
| 8 | "The regime filter / divergence cap is protective" | **INCONCLUSIVE → re-test** | the cap now fires 70×; and within the corrected data the **full-multiplier (1.0) cohort underperforms** (+0.067 R vs +0.227 R) — suggestive but n=41 |
| 9 | "The stop is not the bottleneck (3/75 stop-outs ever reached +1 R)" | **CHANGED (weakened)** | **11 of 84 (13 %)** stop-outs reached ≥ +1 R first (was 4 %) |
| 10 | "The target leaves ~47 R on the table (MFE 2.25 vs TP 1.667)" | **CONFIRMED** | MFE 2.249, TP 1.667, give-back 0.582/winner |
| 11 | "Entry gap is a monotone signal" | **REJECTED (stands)** | V4 non-monotone in 2024 vs 2025 |
| 12 | "`max_open_positions` magnitude is unmeasurable" | **CONFIRMED** | still unobservable; but it now binds on **24.2 %** of sessions (was 17.2 %) |
| 13 | "The Setup v1 extension filter is INERT" | **CONFIRMED** | 168/168 trades have no extension value |
| 14 | "Marginal capital has negative expectancy" (Step 0) | see **§F** | re-derived |
| 15 | "TP 2.5 → 4.0 improves return monotonically" (R1) | see **§G** | re-derived |

---

## F. Corrected Step 0

`reports/phase5_step0_marginal_cohort_pitcorrected_2026-10-01.json` (+ the lever addendum
`..._step0b_lever_cohorts_pitcorrected_2026-10-01.json`). Same machinery, same pre-registered rule,
PIT-corrected regime.

### The control is weaker than before

| | leaky run | **PIT run** |
|---|---:|---:|
| Control replication (exit reason **and** R ±0.05) | 148/148 = 100 % | **163/168 = 97.02 %** |

5 of 168 control trades no longer reproduce; 6 marginal simulations were also skipped as
`no_exit_within_horizon`. This is the **known `HARNESS_HORIZON_GUARD_ARTIFACT`** (the Step-0 simulator
breaks at `held > 30` calendar days before evaluating the exit, so a boundary that lands on a weekend is
missed — see `return_bottleneck_report_2026-10-01.md` §11). The corrected run holds positions longer, so it
hits the guard more often. **Flagged, not fixed** — the artifact is inside the Step-0 simulator, and the
verdict below is not sensitive to 5 trades.

### Cohorts

| Cohort | n | avg R | median R | win % | sum R |
|---|---:|---:|---:|---:|---:|
| Accepted (control) | 168 | **+0.1878** | −0.562 | 48.21 % | +31.55 |
| **Marginal (rejected valid setups)** | 391 | **−0.0069** | −1.0 | 44.25 % | −2.71 |
| Marginal, de-duplicated (1/ticker/month) | 167 | **+0.0856** | −1.0 | 46.71 % | +14.30 |

**Pre-registered rule → `STOP`** (marginal expectancy ≤ 0: do not loosen the budget cap).
**But the margin has collapsed**: the prior run measured the marginal cohort at **−0.270 R**; under PIT data
it is **−0.0069 R**, and the de-duplicated view is **positive (+0.086 R)**.

### Lever-specific cohorts (which capital each lever actually admits)

| Lever | newly admitted | their avg R | win % | *(leaky run, for contrast)* |
|---|---:|---:|---:|---:|
| (b) `risk_per_trade` 1 % → 1.5 % | 101 | **+0.3489** | 58.4 % | −0.138 |
| (b2) `risk_per_trade` 1 % → 2 % | 173 | **+0.1329** | 50.9 % | −0.171 |
| (c) divergence cap removed (mult → 1.0) | 163 | **+0.1457** | 51.5 % | −0.171 |
| (c2) `base_size_sideways` 0.5 → 0.75 | 87 | **+0.3258** | 57.5 % | −0.070 |
| (e) capital base × 2 | 177 | **+0.1604** | 52.0 % | −0.169 |

**This reverses the earlier finding.** Under PIT data **every** lever admits a **positive**-expectancy
cohort, and the most modest lever — `risk_per_trade` 1 % → 1.5 %, admitting only 101 of 391 — admits the
**best** cohort (+0.349 R, above even the accepted cohort's +0.188 R).

The tension is real and must be stated plainly: the **aggregate** marginal pool is ≈ 0 R (median −1.0,
only 44 % winners), while the **subsets a lever would actually admit** are positive. Both come from the same
hypothetical-trade simulation; the lever subsets are the decision-relevant cut.

### Price-band pattern (also changed)

| band | n | avg R | *(leaky run)* |
|---|---:|---:|---:|
| $50–150 | 129 | +0.1677 | — |
| $150–400 | 199 | **−0.2417** | −0.363 |
| > $400 | 63 | **+0.3770** | — |

The "$150–400 band is worst" reading survives (−0.242), but the previously implied monotone
"expensive is worse" story does not: **the > $400 band is now the best**.

By signal-time regime: BULL +0.0551 (n = 77), SIDEWAYS −0.0221 (n = 314).

### Leverage preference table (corrected baseline)

| | return | MaxDD | Sharpe | avg exposure |
|---|---:|---:|---:|---:|
| System (PIT) | **5.57 %** | **−10.39 %** | **0.513** | **23.39 %** |
| SPY | 36.26 % | −18.76 % | 1.218 | 100 % |
| Scale to match SPY's return | ≈ 36 % | **≈ −67.6 %** | ≈ 0.513 | ≈ 152 % |

Matching SPY by leverage now needs **k ≈ 6.51×** with an implied drawdown of **−67.6 %** (previously
k ≈ 4.54×, −27.1 %). **"Just deploy more leverage" is even less defensible than before.**

---

## G. Corrected R1

Re-run of the **pre-registered** grid only (`take_profit_atr_mult` ∈ {2.0, 2.5, 3.0, 3.5, 4.0}); the
ad-hoc extension to 4.5/5.0 is **excluded by design**. Single variable; universe, data, costs, execution and
every other parameter frozen.

### Full-window dashboard

| TP (ATR) | Return % | MaxDD % | Sharpe | Sortino | Calmar | avg R | PF | Trades | Avg expo % | Avg hold (d) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2.0 | **−3.32** | −11.24 | **−0.264** | −0.271 | −0.190 | 0.0232 | **0.92** | 175 | 23.40 | 9.23 |
| **2.5 (frozen baseline)** | **+5.57** | **−10.39** | **0.513** | 0.557 | 0.338 | 0.1878 | 1.13 | 168 | 23.39 | 9.78 |
| 3.0 | **−1.43** | −11.29 | −0.084 | −0.096 | −0.081 | 0.0572 | 0.97 | 156 | 23.36 | 10.83 |
| 3.5 | **−0.41** | −10.78 | 0.005 | 0.006 | −0.024 | 0.0742 | 0.99 | 151 | 24.19 | 11.16 |
| 4.0 | **+7.38** | −10.70 | **0.612** | **0.689** | **0.433** | **0.2139** | **1.20** | 141 | 24.32 | 12.33 |

### Sub-period returns

| TP | 2024 full | 2024 H2 | 2025 → Jul | 2025 H1 |
|---:|---:|---:|---:|---:|
| 2.0 | 1.78 | 1.15 | **−5.06** | −4.38 |
| **2.5** | **6.50** | **5.76** | −0.88 | −2.36 |
| 3.0 | 1.42 | 3.89 | −2.81 | −2.28 |
| 3.5 | 0.97 | 3.01 | −1.37 | −1.59 |
| 4.0 | 6.12 | 5.39 | **+1.14** | **+0.38** |

### Mechanism (unchanged, as expected)

| TP | STOP_LOSS | TAKE_PROFIT | TRAILING_STOP | TIME_STOP |
|---:|---|---|---|---|
| 2.0 | 89 (−1.045) | 72 (**1.333 R**) | 9 | 5 |
| 2.5 | 84 (−1.026) | 67 (1.667 R) | 8 | 9 |
| 3.0 | 82 (−1.065) | 43 (2.000 R) | 21 | 10 |
| 3.5 | 80 (−1.062) | 36 (2.333 R) | 26 | 9 |
| 4.0 | 71 (−1.046) | 33 (**2.667 R**) | 28 | 9 |

Widening the target still converts target-hits into trailing exits at higher R — the **mechanism is real**.
What is not real is a stable performance benefit.

### Verdict on R1 — **INCONCLUSIVE / NOT ROBUST.** No take-profit change is supported.

| Test (spec §14/§16) | Result |
|---|---|
| Smooth response? | **✗ FAILS** — the response is a **zig-zag**: 2.5 good (+5.57 %) → 3.0/3.5 **negative** (−1.43 / −0.41) → 4.0 best (+7.38 %). Two separated good points with a valley between them is the signature of noise, not a plateau |
| Do neighbours support the winner? | **✗** — the best setting (4.0) is **not** supported by its immediate neighbours (3.0, 3.5 are negative); exactly the isolated-peak pattern §14 warns about |
| Risk-adjusted improvement? | 4.0 improves Sharpe (0.612 vs 0.513) and PF (1.20 vs 1.13) with MaxDD roughly flat (−10.70 vs −10.39) — but the gain is **not monotone** in the parameter and vanishes one step away |
| Sub-period consistency | **✗** — 2024 favours 2.5/4.0; **2025 is negative for 4 of 5 settings**; nothing holds in both halves |
| Turnover artefact | trades move 175 → 141 across the grid; the two "good" points sit at opposite ends of the trade count |
| Out-of-sample | **none** (both halves were examined) |

**Therefore:** the earlier (leaky) "wider target is monotonically better" is **withdrawn and not replaced**.
On corrected data the pre-registered grid shows **no robust take-profit improvement**; the frozen 2.5 ATR
target is **not demonstrated to be wrong**, and 4.0 — although the best point — is an **unreliable**
candidate (isolated peak, no sub-period support). **R1 does not justify a `STOP_EXIT_V1` revision.**

---

## H. Updated conclusion — what actually limits the strategy's return?

**Read this as a description of the corrected evidence, not a ranking to be optimised.**

After removing the PIT leak, four facts dominate:

1. **The system's edge is thin and real, but small.** Expectancy **+0.188 R** over **168** trades
   (**+31.5 R** total), PF **1.13**, win rate **47.6 %**, and **the median trade loses** (−0.562 R). Over
   1.5 years that is the entire P&L: the strategy is not broken, it is *small*.
2. **Selection is not the limiter.** At the system's own daily exposure weights SPY would have returned
   **+5.75 %** against the system's **+5.57 %** → selection gap **−0.18 pp** on a raw gap of **−30.69 pp**.
   The system picks about as well as SPY at matched exposure; it just holds far less.
3. **But "deploy more" is not a free lever either — the corrected data says the opposite of a free lunch.**
   The corrected regime already deploys materially more than the leaky one (exposure 17.16 % → **23.39 %**,
   cash drag 82.84 % → 76.61 %, flat days 31.06 % → 23.48 %) and it **returns less** (+7.99 % → **+5.57 %**)
   while **drawing down almost twice as much** (−5.98 % → **−10.39 %**). Within the corrected data the
   positions actually sized at the **full** multiplier 1.0 **underperformed** those sized at 0.5
   (+0.067 R vs +0.227 R, n = 41 vs 127). Deployment is a **risk-preference trade**, not an alpha source.
4. **The binding mechanical constraint is the account, not the rulebook.** 425 of 425 risk-gate rejections
   are the engine's own *"risk budget insufficient for one share"*, at a mean budget of **$5.24**; V3 shows
   that even with an effective multiplier of 1.0, **31 %** of valid setups still cannot buy a single share.
   On a $1,282 account with frozen ATR stop geometry, integer-share flooring is arithmetic, not policy.

**Secondary, all measured on corrected data:**

* **Exit efficiency** is a real *gap* but not currently an *opportunity*: winners reach **2.249 R** MFE while
  the target fills at **1.667 R** (give-back **0.582 R** per winner), and 11 of 84 stop-outs (13 %) had been
  ≥ +1 R — but §G shows the pre-registered target grid produces **no robust improvement**, so the gap is not
  actionable with the evidence we now have.
* **The corrected return is almost entirely a 2024 effect.** 2024 = **+6.50 %**, 2025 → Jul = **−0.88 %**
  (the full window's +5.57 % is just their compound); in the 2025 half **every** take-profit setting except
  4.0 loses money. So the corrected +5.57 % rests on roughly twelve months of one bull market.
* **Portfolio capacity binds harder now**: **24.2 %** of sessions sit at `max_open_positions` (was 17.2 %),
  and 131 sessions were blocked on capacity — but the *magnitude* of what was foregone is still unmeasurable.
* **Regime participation is no longer the villain it looked like.** The corrected regime is much less
  defensive (BEAR 136 not 185; the multiplier reaches 1.0 on 61 sessions; the divergence cap fires 70 not 139
  times; BREADTH_THRUST fires 27 times). The prior claim that the cap was "protective" was built on a
  constant-breadth artefact and is **rejected**.
* **Entry gap** is still not a usable signal (V4 non-monotone between 2024 and 2025).
* **The Setup v1 extension filter is still INERT** (0/168 trades carry a value).

**Plain answer:** *return is limited by (i) a genuinely thin, positive-but-small per-trade edge, taken on
(ii) an account so small that integer-share flooring blocks roughly a third of otherwise valid setups, and
(iii) a deployment level that is a deliberate risk choice rather than a missed opportunity — because in the
corrected data every increase in deployment so far has come with proportionally (or worse) more drawdown.*

No new candidate is crowned here.

---

## I. Next research step

Chosen **only** from the corrected evidence, and **not** an optimisation.

| Priority | Direction | One-sentence hypothesis | Falsifiable criterion |
|---|---|---|---|
| **1** | **Controlled deployment experiment (single variable)** | With the "protective cap" reading now rejected, the capital a modest sizing lever would actually admit has **positive** expectancy (Step-0b: `risk_per_trade` 1 %→1.5 % admits 101 setups at **+0.349 R**, better than the accepted cohort), so raising effective size slightly should improve return without a proportional drawdown increase | On the corrected baseline, `risk_per_trade` 1 % → 1.25 % / 1.5 % (**one value at a time**) must improve **Sharpe** and not worsen **MaxDD beyond −10.39 %**, and must hold in **both** sub-periods (2024 and 2025) — otherwise reject |
| **2** | **Make portfolio capacity observable** (logging only) | `max_open_positions` blocks 131 sessions but the foregone setups are never evaluated, so the constraint's magnitude is unmeasurable; persisting the *would-be* setup evaluations when entries are blocked turns it into a measurable question | After the logging change the foregone-setup count is non-null for every blocked session (no strategy semantics change; backtest summary must stay byte-identical) |
| **3** | **Exit path — different question, not the same one** | The target-grid question is **closed for now** (§G: no robust improvement), but the *stop/trailing* half of the exit path has never been tested on corrected data (S1/S2/S3 in the experiment matrix) | Any stop/trailing variant must beat the corrected baseline's Sharpe **0.513** without worsening MaxDD beyond **−10.39 %**, over **both** sub-periods |
| — | **Closed / do NOT pursue** | **R1 target grid — closed** (§G, inconclusive, no revision justified); regime-multiplier relaxation (the fully-sized cohort underperformed); TP values beyond the pre-registered grid (that is exactly how the leak artefact was manufactured); leverage (k ≈ 6.5×, implied MaxDD −67.6 %); any conclusion drawn only from the 2024 half | |

**Caveats carried forward:** the Step-0/0b cohorts are **hypothetical-trade simulations** (control
97.02 %), not portfolio backtests; the corrected window is a **single bull regime**; and the only
out-of-sample test available inside this dataset is the 2024/2025 split.

---

## 5. Live breadth staleness (approved as a separate data-integrity task)

**Existing contract inspection (done first, as instructed).** There is **no freshness threshold anywhere**
in the project's data contracts. The only analogous concept is
`production/datasource.Provenance.stale: bool` — *"data is older than requested as_of (no update)"* — which is
**informational and non-blocking**: on a network failure the datasource returns the stale cache *marked*
stale rather than failing the run. There is no precedent for a data-age threshold, and no precedent for
blocking a trading decision on data age.

**What was added (approved — informational only).** `DecisionRecord.data.breadth` now carries:

```json
{"kind": "pit-or-current", "rows": 2403, "ok": true,
 "tail_date": "2025-07-24", "tail_matches_as_of": true, "error": null}
```

`tail_date` is the **actual last observation date** of the series the regime consumed; `tail_matches_as_of`
is a plain equality check against the session being decided. Both are pure diagnostics: they change no
sizing, no veto, no warning, no order. (Test: `test_pit_breadth.py::test_7`.)

**Desired safety property — achieved.** Stale breadth is now **visible** and can no longer silently
masquerade as current data. Demonstrated on a live-shaped run:

| run | `tail_date` | `tail_matches_as_of` | `warnings` |
|---|---|---|---|
| `as_of = 2025-07-24` | `2025-07-24` | `true` | `[]` |
| `as_of = 2026-06-30` (cache ends 2025-07-31) | `2025-07-31` | **`false`** | `[]` |

The second row is the risk in the clear: an **11-month-stale** breadth series drove a `BEAR / mult 0.0`
regime **with no warning** — visible in the ledger, but not acted upon.

**What was NOT done (deliberately).** No threshold, no warning, no fail-loud, no change to live trading
behaviour. Per the instruction: *do not silently introduce a new trading-strategy freshness rule.*

**Proposed behaviour (for human decision — registered as an OPEN/MEDIUM decision):**

| Option | Behaviour | Assessment |
|---|---|---|
| **(a) Warn only** | if `tail_date < as_of`, append `BREADTH_STALE: tail <as_of>` to `DecisionRecord.warnings` (the existing fail-loud channel, already used for `REGIME_FAILURE` / `CONFIG_WARNING`) | **Recommended.** Consistent with the existing `Provenance.stale` convention (mark, don't block); no trading behaviour changes; makes the condition loud in the human-reviewed briefing |
| (b) Flag but keep sizing | as (a) + a decision-affecting flag on the record | Feeds downstream logic — closer to a strategy change; needs its own approval |
| (c) Fail loud beyond N sessions | refuse to produce BUYs past a tolerance | Strongest, but it *is* a trading-behaviour change and the tolerance would be invented |

Suggested tolerance if (b)/(c) is ever wanted: **0 sessions** (any staleness is abnormal — the breadth
cache is rebuilt from the same OHLCV cache the screener uses, so a current OHLCV cache should give a current
breadth tail). No tolerance is implemented.

---

## 6. Regime v1 freeze audit — Case A or Case B?

**Determination: Case B.** `regime_dual_engine/REGIME_V1_FREEZE.md` **does not state a point-in-time input
requirement** for the breadth series.

| Freeze clause | Content | PIT requirement? |
|---|---|---|
| §1 rows 1–8 | HMM/Breadth weights · composite formula · thresholds · breadth *method* (%above 50DMA 70 % + A/D 10 d 30 %, 252-day percentile) · thrust · divergence · output schema · semantics | **no** — every row governs *how breadth is computed from a given series*, never *which rows of the series may be used* |
| §2 (prohibitions) | no new indicators; no DistDays/KER/ADX/MA reintroduction; no weight/threshold/schema changes; no perf-driven tuning | **no** |
| §3 (allowed without unfreezing) | "✅ 可證明的 correctness/bug 修復（非行為調整）" · "✅ Logging、performance、testing、**data-pipeline 修正**" | **explicitly permits this fix** |
| §4 (process) | a v1 bug fix requires a stated "demonstrable correctness issue" first | satisfied: the audit measured a *constant* breadth input (41.746) across seven as-of dates spanning 2018–2025 |
| §5 (verification anchor) | `test_regime_dual.py` (11/11) | still passes |

**Classification.**

* This is a **data-adapter / documentation ambiguity**, *not* a flaw in the Regime v1 decision
  mathematics. `compute_regime_decision` is correct **given** a point-in-time series; the loader failed to
  provide one, and the freeze never stated the requirement it was silently relying on.
* **No unfreeze is required**: the repair is a "data-pipeline 修正" that fixes a "demonstrable correctness
  issue" (§3 + §4). The frozen values (weights, formula, thresholds, method, thrust/divergence, schema,
  semantics) are **untouched** — verified by `freeze_conformance_audit.py`.

**Proposed clarification (NOT applied — proposed only).** Add to §1:

```
| 9 | Breadth 輸入前提 (input precondition) | 傳入 compute_regime_decision 的 breadth 序列必須是
point-in-time：所有觀測日期 <= 該決策的 as-of session。非交易日 as-of 取「當日或之前最近一筆」。
由 loader 邊界強制（breadth_data.slice_to_end），回歸測試 production/tests/test_pit_breadth.py。|
```

and add `python production/tests/test_pit_breadth.py` to the §5 verification anchors. A docs-only edit is
already permitted by §3 ("文件更新（本契約、README、報告）"), but it is left to the human to apply.

---

---

## Appendix — artifacts, git, tests, registry

### Artifacts produced (all new; nothing overwritten)

| Path | Content |
|---|---|
| `research/pit_breadth_audit.py` | the finding's measurement tool (before/after, in-process patch) |
| `research/rerun_corrected_research.py` | re-derives D1–D6 / V1–V4 / Step 0 / Step 0b on corrected data |
| `production/tests/test_pit_breadth.py` | 7 regression tests pinning the failure mode |
| `reports/pit_breadth_leak_2026-10-01.md` | the finding (written pre-fix, still the defect record) |
| `reports/pit_correction_and_rebaseline_2026-10-01.md` | this report (A–I) |
| `reports/SUPERSEDED_PIT_ARTIFACTS.md` | audit legend + index of superseded artifacts |
| `reports/research_baseline_pitcorrected_2026-10-01.json` | corrected baseline |
| `reports/production_bt_pitcorrected_2024-01-01_2025-07-31.json` | corrected full backtest (trade log) |
| `reports/phase4_diag_data_pitcorrected_2026-10-01.json` | corrected D1–D6 |
| `reports/phase4_verify_pitcorrected_2026-10-01.json` | corrected V1–V4 |
| `reports/phase5_step0_marginal_cohort_pitcorrected_2026-10-01.json` | corrected Step 0 |
| `reports/phase5_step0b_lever_cohorts_pitcorrected_2026-10-01.json` | corrected Step 0b |
| `reports/research_tp_grid_pitcorrected_2026-10-01.json` | corrected R1 |
| `reports/phase3_exit_parity_replay_pitcorrected_2026-10-01.json` | Stop/Exit parity on corrected trades (168/1271/0) |

### Files changed (tracked)

| File | Change | Authorised by |
|---|---|---|
| `regime_dual_engine/breadth_data.py` | **PIT fix**: new `slice_to_end()` + applied in `get_breadth_series` | human approval 2026-10-01 (data-pipeline correctness fix) |
| `regime_dual_engine/pit_breadth_data.py` | **PIT fix**: import guard + applied in `get_breadth` | idem |
| `production/pipeline.py` | additive `data.breadth.tail_date` / `tail_matches_as_of` diagnostics | idem (spec §5 "expose the actual breadth tail date") |
| `production/backtest.py`, `production/config.py`, `production/main.py` | unchanged this turn (Phase-3 approved wiring from earlier) | earlier approval |
| `reports/production_bt_2024-01-01_2025-07-31.json` | `_status: SUPERSEDED — POINT-IN-TIME DATA INTEGRITY FAILURE` marker | spec §2 |

### Protected / frozen files — all CLEAN (verified by `git diff --quiet`)

`src/state/state.py` · `src/agents/setup_agent.py` · `src/agents/risk_manager.py` ·
`src/portfolio/portfolio_manager.py` · `production/risk/risk.py` · `production/portfolio/portfolio.py` ·
`regime_dual_engine/engine.py` · `regime_dual_engine/regime_dual.py` · `regime_dual_engine/config.py` ·
`regime_dual_engine/pit_constituents.py` · `production/STOP_EXIT_V1_FREEZE.md` ·
`src/agents/SETUP_V1_FREEZE.md` · `regime_dual_engine/REGIME_V1_FREEZE.md` ·
`production/exits/adapter.py` · `production/exits/shadow.py`

**Production strategy parameters: unchanged.** `exit_engine_mode = legacy`. Stop/Exit strategy contract
unchanged. Risk / Entry / Portfolio parameters unchanged. No risk, exposure, TP, stop, regime-multiplier or
capacity change was made or implied.

### Tests

| Suite | Result |
|---|---|
| `test_pit_breadth.py` | **7/7** |
| `test_datasource` / `test_pipeline` / `test_backtest` / `test_production` | 8/8 · 9/9 · 5/5 · 10/10 |
| `test_contracts` / `test_stops` / `test_exit_engine` / `test_wiring_safety` | 18/18 · 19/19 · 31/31 · 18/18 |
| `regime_dual_engine/tests/test_regime_dual.py` (freeze anchor) | **11/11** |
| **Total** | **125/125 + 11/11** |

### New OPEN decisions registered

1. **Live breadth staleness policy** (OPEN / MEDIUM) — `tail_date` is now recorded; whether staleness should
   warn, flag, or fail loud is **undecided** (options a/b/c in §5).
2. **Freeze-document clarification** (proposed) — add the PIT input precondition as freeze §1 row 9
   (proposal text in §6; **not applied**).

