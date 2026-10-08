# S1-D — Why `stop_atr_mult = 1.8` looked better (decomposition)

**Date:** 2026-10-02 · **Baseline:** the PIT-corrected one (168 trades, +5.57 %, Sharpe 0.513)
**No production code was modified.** Every number is either a post-hoc re-expression of an
already-recorded trade, or a re-run of the unchanged `ProductionBacktest`.

---

## 0. The finding

S1 recorded that `stop_atr_mult = 1.8` improves every headline metric (return +5.57 % →
+11.09 %, Sharpe 0.513 → 1.107, MaxDD −10.39 % → −8.73 %) but fails the §12 neighbour
test. It also established that the parameter is three levers at once. This decomposition
separates them and reaches a single conclusion:

> **The 1.8 effect is not the stop being wider. On the trades the two variants share, 1.8
> is very slightly WORSE than the control (−0.009 R). The entire improvement comes from
> the 58 trades 1.8 takes that the control does not.**

| channel | verdict |
|---|---|
| R-unit change | works **against** 1.8 |
| position sizing | ~neutral in dollar terms |
| trailing arm | no effect on realised R |
| **trade selection** | **carries the whole effect** |

---

## 1. Channel A — the R-unit change works *against* 1.8

Re-expressing the control's **own 168 trades** in each variant's R unit (pure arithmetic:
same trades, same exit prices, only the denominator changes):

| stop mult | R as recorded | R in this variant's unit | Δ from the unit alone | TP's R value |
|---:|---:|---:|---:|---:|
| 1.2 | 0.1878 | 0.2347 | **+0.0469** | 2.0833 |
| **1.5** | **0.1878** | **0.1878** | — | 1.6667 |
| 1.8 | 0.1878 | **0.1565** | **−0.0313** | 1.3889 |
| 2.0 | 0.1878 | **0.1408** | **−0.0470** | 1.2500 |

The control's own performance, expressed in 1.8's R unit, is **0.1565 — worse than its
recorded 0.1878**. So the R-unit channel cannot explain 1.8's apparent 0.2445; on this
channel 1.8 is penalised by roughly 0.031 R, not boosted.

**Implication:** the avg-R column in S1 understates 1.8's real improvement. The improvement
is larger than it looked, and it is not a unit artefact.

---

## 2. Channel B — this is not de-risking

Because `shares = floor(risk_budget / (atr × stop_mult))`, widening the stop buys fewer
shares for the **same dollar risk**. The dollar risk per trade is therefore flat, while the
position value falls:

| stop mult | $ risk / trade | mean position value | exposure % | trades |
|---:|---:|---:|---:|---:|
| 1.2 | $5.39 | $137.75 | 26.60 | 201 |
| **1.5** | **$5.34** | **$112.59** | **23.39** | **168** |
| 1.8 | $5.51 | $100.52 | 20.37 | 145 |
| 2.0 | $5.57 | $93.00 | 20.08 | 128 |

Dollar risk per trade moves by ~3 % across the whole grid (rounding only). **1.8 is not
safer in any meaningful sense** — it simply holds smaller positions. The MaxDD improvement
(−10.39 % → −8.73 %) therefore comes from reduced position *value*, not from better
risk control per trade.

---

## 3. Channel C — the trailing arm moves, but changes no realised R

With `trailing_trigger_r = 1.0`, the freeze contract fixes
`r_dist = atr_at_entry × stop_atr_mult` (`STOP_EXIT_V1_FREEZE.md:54-55`), so the arming
price is `entry + 1.0 × stop_mult × ATR`. Widening the stop therefore delays the arm
(1.5 ATR → 1.8 ATR of profit before the trailing engages).

Measured effect on the control's shared trades: at 1.8 the trailing class contributes
5 trades at +0.1124 R versus +0.1231 R in the full control — a difference of about 0.01 R
on 3 % of trades. **Immaterial.** Channel C is not the source.

---

## 4. Channel D — trade selection carries the entire effect

Shared = the variant took the same (ticker, entry_date) as the control. Unique = it took
trades the control did not.

| stop mult | Jaccard | shared | unique | shared R (variant) | shared R (control) | **Δ shared** | **unique avg R** | unique ΣR |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.2 | 0.342 | 94 | 107 | 0.2439 | 0.2211 | +0.0228 | +0.0450 | +4.81 |
| **1.8** | **0.385** | **87** | **58** | **0.2547** | **0.2640** | **−0.0093** | **+0.2292** | **+13.29** |
| 2.0 | 0.298 | 68 | 60 | 0.3343 | 0.3154 | +0.0189 | **−0.0485** | **−2.91** |

**This is the answer.** At 1.8, on the 87 trades it has in common with the control, 1.8 is
marginally **worse** (−0.009 R). The variant's headline advantage is produced entirely by
its 58 unique trades, which average **+0.229 R**.

The comparison with 2.0 is the clincher. Its 60 unique trades average **−0.049 R** — worse
than nothing — which is why 2.0 gives back more than half the gain. The two neighbours
differ almost entirely in the *quality of the extra trades they admit*, not in how they
manage the trades they share. That is the signature of **selection noise**, not of a
better stop.

---

## 5. Why selection changes at all — the mechanism, measured

If 1.8 mostly re-picks rather than re-manages, what is it re-picking? The funnel, captured
from the R7 risk-gate records:

| stop mult | setups valid | risk-allowed | **`RISK_BUDGET_BELOW_ONE_SHARE` rejections** | trades |
|---:|---:|---:|---:|---:|
| **1.5** | 610 | 185 | **425** | 168 |
| 1.8 | 629 | 157 | **472** | 145 |
| 2.0 | 712 | 142 | **570** | 128 |

Widening the stop raises `stop_distance`, which raises the risk budget required to buy a
single share, which rejects more candidates outright. Measured on the control's own
book, the affordable share count is tightly packed near 1: **57.7 % of baseline trades sit
below 2 shares** and **42.3 % below 1.5 shares**, with a median of just 1.83.

**So `stop_atr_mult` is silently an admission filter as well as a stop width.** A 20 %
wider stop does not merely tolerate more noise per trade — it changes which trades the
account can afford at all, and with 43 % of the book sitting near the 1-share boundary,
that reshuffling is large. It is also why the variant's exposure falls (23.39 % → 20.37 %)
while its dollar risk per trade does not.

This is the concrete mechanism behind S1's §12 failure. The response surface is jagged
because the parameter is doing at least four jobs, and the neighbour 2.0 lands on a
different set of admissible trades.

---

## 6. Verdict on S1

| question | answer |
|---|---|
| Is the 1.5 initial stop too tight? | **Not established.** 1.8's gain is not attributable to stop width. |
| Is the gain real (vs a unit artefact)? | Yes — and larger than S1 measured, since the R-unit channel penalises it. But its **source** is trade selection, not stop quality. |
| Is it robust? | **No.** The isolated-peak test (§12) and the neighbour's negative unique cohort both fail. |
| Should `stop_atr_mult` change in production? | **No.** The evidence does not support it, and the parameter is not the clean lever the experiment assumed. |

**S1: the initial stop width remains UNRESOLVED — and the reason is now understood.** It is
unresolved not because the sample is too small, but because `stop_atr_mult` cannot be
varied as a single variable in this architecture.

---

## 7. What would actually resolve it

The confound is not one coupling but **four**, in descending order of measured impact:

1. **Admission filtering** (§5) — the largest, and previously unrecognised. The integer
   share floor turns stop width into a minimum-ATR requirement.
2. **Trade selection** (§4) — which candidates pass the gate, and therefore which trades
   exist.
3. **The R unit** (§1) — a measurement channel; it penalises 1.8, so fixing it would
   *strengthen* the 1.8 case without explaining it.
4. **The trailing arm** (§3) — immaterial, but documented and frozen.

A clean S1 requires holding 1 and 2 fixed while varying only the stop distance. That means
decoupling the stop distance from the sizing divisor — a change to frozen semantics, and an
architecture decision, not a research parameter.

**This is written up as a decision proposal, not implemented:**
`reports/s1_stop_width_decision_proposal_2026-10-02.md`.

---

## 8. Artifacts

* `research/s1_decomposition.py` — this decomposition
* `reports/s1_decomposition_pitcorrected_2026-10-02.json` — raw output

No production file was touched. No parameter was written. No commit, no push.
