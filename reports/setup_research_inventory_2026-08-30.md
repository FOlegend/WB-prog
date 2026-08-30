# Setup Agent — 既有研究盤點（唯讀）

日期：2026-08-30　模式：唯讀搜尋（未修改程式碼、未跑新回測）
方法：掃描 repo 腳本 + reports/ JSON + git log + README/docs。

---

## 問題對照表

| # | Question | Previously tested? | Script/report | Key result | Still valid? |
|---|----------|-------------------|---------------|-----------|--------------|
| 1 | Breakout vs Pullback vs Both | ✅ 是 | `setup_comparison.py` → `reports/setup_comparison.json`（2018-2025, top20, Current Weights, gate, score_full=70） | Breakout Only **+29.86%** (Sharpe 0.39, MaxDD −12.48, PF 1.17) ＞ Breakout+Pullback +20.73% (0.28) ＞ Pullback Only +5.17% (0.12) ＞ legacy −15.78% | ⚠️ 部分失效：跑在 **v3 5-component regime**（Current Weights），非新的 Regime v1 兩引擎 |
| 2 | Breakout component ablation (MA/RS/volume/VCP) | ✅ 是 | `breakout_ablation.py` → `reports/breakout_ablation.json` | A. base(MA+RS) −13.29% → B. +volume −11.74% → C. +VCP −9.09% → D/E. full −8.91%。Attribution: **volume_expansion 最強**（PF 1.35 vs 0.84）；VCP 弱正（1.05 vs 0.89）；**rs_rank_pass 負貢獻**（0.83 vs 1.04）；ma_aligned 負（0.86 vs 1.15）；gap 1-2% 很差（PF 0.48） | ⚠️ 部分失效：回測全為負報酬（與 Q1 同設定卻差 ~38pp，疑腳本版本/權重差異，需重跑驗證）；component 相對排序可參考 |
| 3 | Setup score threshold sensitivity | ❌ 否（未測 `setup_score_threshold`） | 無專門測試。`breakout_comparison.py` 比的是 **regime `score_full` 70 vs 60**（非 setup 閾值） | regime score_full=70: +7.59% vs 60: +4.38%（這是 regime 曝險閾值，不是 setup 分數閾值） | —（未測） |
| 4 | Setup quality multiplier effectiveness | ❌ 否 | 無。`breakout_comparison.py` 僅註記「quality_mult capped at 1.0」；config 有 0.5/0.75/1.0 mapping | 無對照實驗（quality_mult 高/低 vs 績效） | —（未測） |
| 5 | Setup Agent vs legacy weighted entry | ✅ 是 | 同上 `setup_comparison.json`（variant "Current Simplified"） | Setup 大幅勝出：legacy weighted **−15.78%** (PF 0.92, 766 trades) vs Breakout +29.86% (PF 1.17) | ⚠️ 部分失效：legacy 對照亦跑在 v3 regime |
| 6a | Performance by regime (BULL/SIDEWAYS/BEAR) | ❌ 否 | trade log 有 `entry_regime` 欄位（`dynamic_universe_backtest.py:347`、`src/backtest/engine.py:186`）但**無腳本匯出 setup×regime 績效表** | 無 | —（未測，資料已備） |
| 6b | Performance by setup type | ⚠️ 部分 | `setup_comparison.json` setup_distribution | 各 variant 的 setup 交易數（Breakout+Pullback: 387 breakout / 423 pullback）；無逐型 win/PF 對照 | 部分有效 |
| 6c | Performance by setup score bucket | ✅ 是 | `breakout_comparison.json` → `setup_buckets`（score_full=70） | 0.75-0.80 **最佳**（n=45, PF 1.46, Win 60%, contrib +50.88）；0.80-0.85 **最差**（n=80, PF 0.71, contrib −101.42）；0.85-1.00 PF 1.21 (+84.26)；0.70-0.75 PF 0.83 (−48.86) | ⚠️ 部分失效：v3 regime；且 bucket 非線性（非越高越好）值得注意 |
| 7 | Entry/exit timing & live-vs-backtest consistency | ❌ 否 | 無 live-vs-backtest 一致性分析。有間接證據：`setup_comparison.json` fill_model_distribution（exit fill 分佈）+ `breakout_ablation.json` skip_summary（GAP_TOO_HIGH 126、EXTENDED_FROM_PIVOT 263 跳單） | exit fill 分佈：STOP ~310-335 / TARGET ~242-278 / GAP ~78-91 / TRAILING ~64-69 / CLOSE ~41-56；跳單 389 次（263+126） | —（未測） |
| 8 | False positives / failed setups | ⚠️ 部分 | `breakout_ablation.json` skip_summary + gap_bucket/extension_bucket attribution | 跳單統計（見 Q7）；gap 分析：<0%（gap down）PF 1.0、0-1% PF 0.94、**1-2% PF 0.48**（gap up 越高越差）；extension <0%（below pivot）PF 1.24 vs 0-1.5% PF 0.89 | 部分有效（未做系統性 false-positive 分析） |
| 9 | Determinism / reproducibility | ✅ 是 | `setup_comparison.py` determinism test → `setup_comparison.json` | **3 次完全相同**（Breakout+Pullback: 20.73/0.28/−19.50/1.1/810 三連一致） | ✅ 仍有效（dynamic engine 確定性設計保留） |
| 10 | Setup design decisions / reviewer conclusions | ✅ 是 | git commit 訊息 + `setup_agent.py` 註解 + `config.py` 註記 + `audit_return_calculation.py` | v3.2.3: breakout-only、quality_mult cap 1.0、engulfing 需先 bearish；v3.2.4: **strict-only（近 pivot 不算 entry）**、next-open extension filters（gap>2% / extension>3% 跳單）、signal_close/prior_high20 輸出 | ⚠️ 部分失效：設計基於 v3 regime + 舊 data；strict-only 決策沿用至 v1 基準 |

---

## 結論

### 1. 我們已經知道的
- **Breakout 明顯優於 Pullback 與 legacy weighted entry**（+29.86% vs +5.17% vs −15.78%）→ config 已鎖 `setup_enabled_types=["breakout"]`（v3.2.3）
- **Breakout 組件相對貢獻**：volume 擴張最強、VCP 弱正、RS rank 與 MA alignment 為負貢獻（在 v3 設定下）——但絕對報酬為負，排序僅供參考
- **Setup score bucket 非線性**：0.75-0.80 最佳、0.80-0.85 最差——分數高≠好，有品質「甜蜜區」
- **gap/extension 過濾有效**：gap up 1-2% PF 0.48（應跳）、gap down PF 1.0（可進）；現行 max_entry_gap=2%/max_extension=3% 跳單 389 次
- **動態回測完全確定性**（3 runs 一致）
- **設計決策史**：strict-only entry、quality_mult cap 1.0、next-open 進場、gap-aware 出場

### 2. 我們不知道的
- **Setup × Regime 交叉績效**（BULL/SIDEWAYS/BEAR 下的 setup 表現）——trade log 有 entry_regime 但從未匯出分析
- **setup_score_threshold 敏感度**（現在固定 0.5，未掃 0.4/0.5/0.6...）
- **quality_mult 有效性**（0.5/0.75/1.0 對績效的實際影響）
- **live-vs-backtest 一致性**（簡報推薦的 order 與回測假設是否一致；沒有對照研究）
- **系統性 false-positive 分析**（只有 gap/extension 跳單統計，沒有「進場後立即失敗」的歸因）

### 3. 真正全新的測試
1. **Setup × Regime v1 交叉表**（用 trade log 的 entry_regime + setup_type + setup_score 欄位，直接從既有 summary 匯出，不需重跑引擎）
2. **setup_score_threshold 敏感度**（0.4/0.5/0.6/0.7 on Regime v1）
3. **quality_mult 有效性對照**（mult 生效 vs 恆為 1.0）
4. **live-vs-backtest 一致性審計**（briefing_*.md / orders_*.json vs 回測假設）
5. **Regime v1 兩引擎下重跑 Q1/Q2**（確認 v3 的結論是否 transfer 到新 regime）

### 4. 不應重複的
- **Q9 determinism**（已確認，引擎未改）
- **Q10 設計史**（git 已記錄完整）
- **Q5 的 legacy 對照**（legacy weighted 已被取代，無需再比）
- **Q1/Q2 的 v3-regime 版本**（除非需要「v3 vs v1 對照」做決策，否則直接以 v1 重跑即可）

---
*本報告為唯讀盤點，未修改任何檔案、未執行新回測。*
