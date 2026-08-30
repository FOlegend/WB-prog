# Setup V1 Root-Cause Analysis — READ-ONLY

日期：2026-08-30　基準：Regime v1（凍結）　模式：唯讀（未修改任何 code / 未調參）
資料：`reports/setup_v1_rootcause_logs.json`（3 組完整 trade log + equity）+ `setup_v1_rootcause.json`
方法：3 組回測（Breakout / Pullback / Both, 2018-2025, top20, Regime v1 + PIT）

---

## A. 有證據支持的根因

### A1. SIDEWAYS 為何虧損 → **零/負 gross edge + 高頻摩擦侵蝕**
| 指標 | BULL | SIDEWAYS |
|---|---|---|
| trades | 298 | 399 |
| PF | **1.215** | **0.843** |
| avg gross ret | +0.558% | **−0.001%** |
| avg net ret | +0.456% | **−0.104%** |
| friction | 0.103% | 0.103% |
| STOP_LOSS 佔比 | 40% (119) | **54% (216)** |
| TAKE_PROFIT 佔比 | 30% (88) | 29% (116) |

- **SIDEWAYS 的 gross edge 幾乎為零（−0.001%）**，摩擦 0.103% 把微薄的正期待值（若存在）吃光 → 淨虧 −0.104%。**摩擦假設成立**：高頻交易（399 筆）在盤整期摩擦侵蝕明顯。
- STOP_LOSS 216 筆 avgR −1.042 貢獻 −$1,174（主要虧損）；TAKE_PROFIT 116 筆 avgR +1.667 貢獻 +$922——輸贏比約 1.86:1 但輸的筆數 1.86 倍 → **不對稱但頻率不利**，不是單一 whipsaw 模式。
- setup_score 分佈：SIDEWAYS 高分段（0.8-0.9: 169 筆）反而多於 BULL（98 筆）→ **不是低品質單問題，是 regime 本身無 edge**。

### A2. Pullback 為何在 v1 勝出 → **SIDEWAYS 虧損小 + BULL 略優**
| | Breakout | Pullback |
|---|---|---|
| trades | 438 | 687 |
| PF | 1.046 | **1.103** |
| Win% | 46.3 | 46.9 |
| AvgR | +0.006 | **+0.072** |
| net $ | +66 | **+221** |
| **BULL** | n=223 PF 1.321 avgR +0.151 | **n=296 PF 1.342 avgR +0.188** |
| **SIDEWAYS** | n=215 PF 0.683 **avgR −0.144** | n=391 PF 0.870 **avgR −0.016** |

- 關鍵差異在 **SIDEWAYS**：pullback 損失小一個量級（avgR −0.016 vs −0.144，net −$141 vs −$198）——pullback 是「回踩 EMA」順勢低接，盤整期較不易追高被套。
- BULL 下 pullback 交易更多（296 vs 223）且 AvgR 更高（+0.188 vs +0.151）。
- 年度：pullback 2023 僅 −$11（vs breakout −$118）、2020 +$205（vs +$80）——復甦年表現優於 breakout。
- **這解釋 v3→v1 結論翻轉**：v3 regime 下 SIDEWAYS 佔比不同；v1 下 SIDEWAYS 佔 ~43% 交易日（886/1905），pullback 在最大佔比的 regime 虧損小 → 整體勝出。

### A3. Both 為何 MaxDD 惡化 → **優先順序造成的「選擇性殘留」+ 高同步持倉**
- both 中 **breakout 只剩 157 筆**（單一 run 438）且 **PF 0.862 虧損 −$87.54**；pullback 540 筆 PF 1.089 +$157。
- **機制**：`setup_signal` 用 `max(results, key=setup_score)` 選較高分者——breakout 只有當分數高於 pullback 時才被選中，等於「被 pullback 過濾後的 breakout 殘留」，品質反而差（PF 0.862）。
- marginal（both 新增 vs 單一 run）：breakout 38 筆 PF 0.825（差）、pullback 251 筆 PF 1.027（微弱正）→ 額外交易品質低。
- **同步持倉**：**overlap 702/1905 天 = 36.85%**；pullback 持倉 1268 天（67%）、breakout 706 天（37%）；max concurrent 4（brk）/5（pull）。
- **最大回撤**：唯一深度回撤 2021-11-10..2025-07-31（depth −26.4%，933 天）——橫跨 2022 bear + 2023 復甦 + 2024，期間 equity 未創新高；both 的同步持倉使回撤比單一（breakout −17.4% / pullback −15.9%）更深。此回撤主要是動態 universe 策略在 2022-2024 的系統性弱勢（graduation 已見），both 讓它加深。

---

## B. 尚未證實的假設

1. **SIDEWAYS 進場過濾可改善**：假設 SIDEWAYS 只收 high-score（>0.8）或降倉，可減損——未測試（會改 setup 邏輯）。
2. **regime-dependent 選擇規則**：假設「BULL 優先 breakout、SIDEWAYS 優先 pullback」可兼得兩者優勢——未測試。
3. **breakout 在 v1 下的弱勢可修**：假設 breakout 的 SIDEWAYS 追高問題可透過 stricter 條件修復——未測試。
4. **friction 是主因**：0.103% 摩擦讓 SIDEWAYS 由 −0.001% → −0.104%；若摩擦降到 0.05%，SIDEWAYS 仍虧（−0.05%）但幅度小一半——未做敏感性。

---

## C. 可改善 Setup v1 的最小變更（建議，未實作）

1. **Pullback Only 作為 v1 預設**（單一、最簡單、v1 下最佳：CAGR 2.23% / Sharpe 0.33 / MaxDD −15.9%）——只改 `cfg.setup_enabled_types=["pullback"]`，零 code 變更。
2. **移除 or 限制 Both 組合**：現行 `max(score)` 選擇機制會產生「選擇性殘留」劣質 breakout 單；若要組合需先定義 priority（見 E），否則 Both 不可用。
3. **SIDEWAYS 降頻/降倉**（若想留 breakout）：在 setup 層對 SIDEWAYS regime 的進場加 high-score 門檻或 size 折減——**會改 setup_agent/engine，需另行決策**（regime v1 凍結，此改動在 setup 層）。
4. 保留 threshold 0.5（0.4-0.6 平滑）與 quality mult（MaxDD −6.3pp 有效）。

---

## D. 應保持不變

- **Regime v1**（凍結契約）— HMM/Breadth 50/50、65/35、Thrust、Divergence、schema 全部不動
- **Screener** 6-filter pipeline — 不重建
- **threshold = 0.5** — 驗證穩健
- **quality multiplier** — 實證有效（MaxDD 改善 6.3pp）
- **gap / extension filters**（max_entry_gap 2% / max_extension 3%）— 實證避免劣質進場
- **BEAR gate（size=0 不開倉）** — 正確
- **next-open entry + gap-aware OHLC exit** — 一致性核心

---

## E. 推薦的 Setup v1 設計（建議，待決策）

```
推薦：Pullback Only（v1 預設）
  cfg.setup_enabled_types = ["pullback"]        # 零 code 變更
  cfg.setup_score_threshold = 0.5               # 驗證穩健
  quality mult + gap/extension filters 保留
  → v1 下實證：CAGR 2.23% / Sharpe 0.33 / MaxDD −15.9% / PF 1.10 / 687 trades

備選：regime-dependent 組合（需先實作 + 驗證）
  BULL   → 優先 breakout（v1 BULL 下 breakout avgR +0.151 略遜 pullback +0.188，
           需重新設計優先規則而非 max(score)）
  SIDEWAYS → pullback only
  風險：現行 max(score) 選擇機制的「殘留效應」已證明有害（A3），
       此方案需重寫 setup_signal 的選擇邏輯 → 超出「最小變更」
```

**證據支持**：Pullback Only 是唯一經 v1 實證、零 code 變更、可立即上 Production V2 的選項。
Both 組合在現行選擇機制下**不可用**（MaxDD −26.4% + 劣質殘留單）。
Breakout Only 在 v1 下無優勢（+6.24% vs pullback +18.19%）。

---

## 結論摘要
| 問題 | 根因 | 證據強度 |
|---|---|---|
| SIDEWAYS 虧損 | gross edge ≈ 0 + 摩擦侵蝕（−0.104% net）；STOP 頻率 54% | 強 |
| Pullback > Breakout | SIDEWAYS 虧損小一個量級 + BULL 略優 + 復甦年佳 | 強 |
| Both MaxDD 惡化 | max(score) 選擇性殘留劣質 breakout + 36.9% 同步持倉 + 2021-2025 系統性弱勢加深 | 強 |
| threshold 0.5 / quality mult | 穩健 / 有效 | 強 |

**下一步（未執行）**：決策 Pullback Only vs regime-dependent 組合；若選後者，需先定義 priority 規則並重驗證。所有分析腳本：`setup_v1_root_cause.py`（唯讀、可重跑）。
