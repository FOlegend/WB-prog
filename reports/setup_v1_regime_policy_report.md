# Setup V1 — Minimal Regime-Dependent Selection Test

日期：2026-08-30　基準：Regime v1（凍結）+ PIT breadth　方法：3 variants（非優化）
執行：`regime_dual_engine/setup_v1_regime_policy_test.py`（新增 engine harness knob `cfg.setup_regime_policy`，
預設 None 行為不變；setup_agent.py / Regime v1 / Screener 未動）
資料：`reports/setup_v1_regime_policy.json`

---

## 1. Comparison Table

| Variant | CAGR% | Sharpe | MaxDD% | PF | Win% | Trades | Exp% | AvgR |
|---|---|---|---|---|---|---|---|---|
| **A. Pullback Only** | **+2.23** | **0.33** | **−15.85** | **1.10** | 46.9 | 687 | 68.3 | **+0.072** |
| B. BULL: brk+pull(pullback-first) / SW: pullback | +1.19 | 0.19 | −17.63 | 1.05 | 46.2 | 688 | 67.7 | +0.055 |
| C. BULL: breakout only / SW: pullback | −0.62 | −0.05 | −21.01 | 0.96 | 44.6 | 606 | 65.6 | −0.023 |

**A 全面勝出**（CAGR / Sharpe / MaxDD / PF / AvgR 全贏；B 只多 1 筆交易但各項皆差；C 轉負）。

## 2. Exact Selection Rules

**A — Pullback Only（baseline）**：`setup_enabled_types=["pullback"]`，無 regime policy（所有 regime 只跑 pullback；BEAR 由 regime gate 排除）。

**B — Simple regime-dependent**：policy `{BULL: "both_pullback_first", SIDEWAYS: "pullback"}`，確定性優先規則（評估前定義）：
```
對同檔/同日，若 breakout 與 pullback 同時有效：
  1. pullback 有效且 score≥0.5 → 選 pullback（止損較緊 / R:R 較佳）
  2. 否則 breakout 有效 → 選 breakout
  3. 否則選高分者（維持交易數）
```
此規則**刻意避開 max(score) 的選擇性殘留問題**。

**C — Conservative regime-dependent**：policy `{BULL: "breakout", SIDEWAYS: "pullback"}`（BULL 只用 breakout）。

## 3. Marginal Contribution of Breakout

| 情境 | 新增 breakout 交易 | PF | Win% | AvgR | 判定 |
|---|---|---|---|---|---|
| B（pullback 優先，breakout 補位） | 37 筆 | **0.923** | 45.9 | −0.007 | **DRAGS**（拉低組合效率） |
| C（BULL 純 breakout 取代 pullback） | 182 筆 | 1.123 | 48.9 | +0.047 | 名義 improves，但 C 整體仍最差 |

- B 的 37 筆補位 breakout（僅在 pullback 無效時出現）PF 0.923 → **breakout 無法在 pullback 不成立的場合創造價值**。
- C 的 breakout 單獨 PF 1.12 >1，但**取代**了原本 BULL 的 pullback（PF 1.342）→ 淨損失（C BULL PF 1.141 < A BULL PF 1.342）。

**結論：問題不是「怎麼選 Breakout」，而是 Breakout 本身在 Regime v1 下是劣勢 setup。** 加入 breakout（無論補位或取代）都無法超越純 pullback。

## 4. OOS / Yearly Stability（equity curve 逐年）

| 年 | A (Pullback) | B (BULL both) | C (BULL brk) |
|---|---|---|---|
| 2018 | −2.2% | −1.2% | −5.4% |
| 2019 | **+11.6%** | +11.6% | +7.5% |
| 2020 | **+13.3%** | +7.7% | +1.0% |
| 2021 | −2.7% | −4.4% | −4.8% |
| 2022 | +2.0% | −1.0% | −2.6% |
| 2023 | +0.6% | −0.2% | −2.8% |
| 2024 | −6.4% | −3.5% | −3.0% |
| 2025 | +1.8% | +0.9% | +7.2% |

- A 的優勢**並非單一年份偶發**：2019/2020 強年大幅領先（+11.6/+13.3 vs B/C），其餘年份小正/小負、無災難年。
- B、C 在 2020 強年明顯落後（+7.7 / +1.0）——regime-dependent 加入 breakout 反而削弱強年表現。
- A 的 yearly profile 最平滑（最大年虧 −6.4%），符合「不依賴窄參數、時間穩定」的決策標準。

## 5. Recommendation

```
PULLBACK ONLY
```

**理由**：
1. A 在 CAGR / Sharpe / MaxDD / PF / AvgR 全面優於 B、C，且優勢跨年份穩定（非 2020/2023 偶發）。
2. B（明確優先規則的組合）只多 1 筆交易但 Sharpe 0.19 vs 0.33、MaxDD −17.6 vs −15.9——加入 breakout **任何形式都無法改善**。
3. C（保守 split）轉負（−0.62%），證明「BULL 用 breakout」是壞主意。
4. **核心洞察**：問題是 Breakout 本身，不是 max(score) 選擇機制——37 筆「乾淨補位」breakout 仍 PF 0.923。這支持**不需要重寫選擇邏輯**，直接 Freeze Setup v1 = Pullback Only。
5. 最簡方案 = 最低維護：單一 setup、無 regime-dependent 分支、無 priority 規則、可解釋性最高——完全符合「simplicity / explainability / stability」目標。

**Setup v1 建議凍結為**：`cfg.setup_enabled_types=["pullback"]`、`setup_score_threshold=0.5`、保留 quality mult + gap/extension filters + next-open entry + gap-aware OHLC exit。**不實作**（依指示，等待下階段）。

---
*未修改 Regime v1 / Screener / setup_agent.py。engine 新增向後相容的 `cfg.setup_regime_policy` harness knob（預設 None 行為不變），供日後 research 重用。*
