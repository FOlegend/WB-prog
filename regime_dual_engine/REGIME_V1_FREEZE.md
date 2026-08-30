# Regime v1 — FREEZE CONTRACT

**狀態：FROZEN（凍結）**　日期：2026-08-30

Regime v1（HMM + Market Breadth 兩引擎）已定案並凍結，作為 Production V2 與
Setup Bot 的固定市場情境基準。**以下任何項目在未經明確解除凍結前，不得修改。**

---

## 1. 凍結範圍（不得變更）

| # | 項目 | 凍結值/規則 |
|---|------|------------|
| 1 | HMM/Breadth 權重 | 50 / 50（`hmm_weight=0.5, breadth_weight=0.5`） |
| 2 | Composite 公式 | `composite = hmm_bull_prob×50.0 + breadth_percentile_score×0.5`，clamp [0,100] |
| 3 | Regime 閾值 | ≥65 BULL / ≤35 BEAR / 其餘 SIDEWAYS（`bull_threshold=65, bear_threshold=35`） |
| 4 | Breadth percentile 方法 | `% above 50DMA`(70%) + A/D 10d momentum(30%)，252 交易日 rolling percentile |
| 5 | Breadth Thrust | breadth_10d_ago<0.30 且相對增幅>20% → BULL + trend_following + 1.0 + `BREADTH_THRUST` |
| 6 | Bearish Breadth Divergence | HMM BULL + breadth 10d 遞減 → size≤0.50 + `BEARISH_BREADTH_DIVERGENCE`（label 不變） |
| 7 | Public output schema | `{regime_label, composite_score, position_size_mult, strategy_mode, veto_flags}` |
| 8 | Regime 語義 | 僅 `trend_following / mean_reversion / defensive`；無 `selective`；label 僅 BULL/BEAR/SIDEWAYS |

## 2. 禁止事項（不得執行）

- ❌ 新增任何 regime 指標
- ❌ 重新引入 Distribution Days、KER、ADX、index-level MA-trend 到 regime 決策
- ❌ 變更權重、composite、閾值、breadth 方法、thrust/divergence 規則、schema、語義
- ❌ 為了提升回測績效而調整 v1 任何參數

## 3. 允許的變更（不需要解除凍結）

- ✅ 可證明的 correctness/bug 修復（非行為調整）
- ✅ Integration（把 v1 接入 main.py / Production V2 / Setup Bot）
- ✅ Logging、performance、testing、data-pipeline 修正
- ✅ 文件更新（本契約、README、報告）

## 4. 流程規則

- 任何疑似需要改 v1 的策略改進 → 先寫入 `REGIME_V2_BACKLOG.md`，**不現在實作**
- 需要改 v1 的 bug 修復 → 先說明「demonstrable correctness issue」再動
- 解除凍結 = 明確指示 + 版本號升為 v2（屆時 v1 程式碼以 tag/archive 保留）

## 5. 驗證錨點（每次更動後重跑）

```bash
python regime_dual_engine/tests/test_regime_dual.py   # 期望 11/11 passed
```

凍結簽核依據：`reports/dual_engine_review_decision.json`（PASS, 2026-08-30）
