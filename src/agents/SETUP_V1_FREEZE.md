# Setup v1 — FREEZE CONTRACT

**狀態：FROZEN（凍結）**　日期：2026-08-30

Setup v1（Pullback Only）已定案並凍結，作為 Production V2 的進場訊號基準。
**以下任何項目在未經明確解除凍結前，不得修改。**

---

## 1. 凍結範圍（不得變更）

| # | 項目 | 凍結值/規則 |
|---|------|------------|
| 1 | Setup 類型 | **Pullback Only**（`setup_enabled_types=["pullback"]`）— Breakout/VCP 不進 production |
| 2 | 進場門檻 | `setup_score_threshold = 0.5` |
| 3 | Quality multiplier | current mapping（high 1.0 / mid 0.75 / low 0.5），cap 1.0 |
| 4 | Gap filter | `max_entry_gap_pct = 0.02`（next open 高於 signal close 2% → 跳過） |
| 5 | Extension filter | `max_extension_from_pivot_pct = 0.03`（偏離 pivot 3% → 跳過） |
| 6 | Entry timing | next-open entry（signal close 次一交易日開盤成交） |
| 7 | Exit model | gap-aware OHLC exit（STOP/TARGET/TRAILING/TIME/SIGNAL） |
| 8 | Pullback 定義 | 回踩 10/20 EMA（±1%）+ 量縮 + 反轉 K + RS 強勢（`setup_agent.pullback_setup`） |

## 2. 禁止事項（不得執行）

- ❌ 加入 Breakout / VCP / 新 setup 類型 / 新指標
- ❌ 調整 setup_score_threshold（不做 threshold 優化）
- ❌ 修改 pullback scoring 邏輯（除非 integration correctness 需要）
- ❌ 新增 regime-dependent setup 規則（BULL/SIDEWAYS 一律同一 pullback 評估；BEAR 由 regime gate 排除）

## 3. 允許的變更

- ✅ 可證明的 integration/correctness bug 修復
- ✅ 接入 Production V2 / 回測 harness 所需的包裝（不變更 scoring）
- ✅ Logging、testing、data-pipeline、文件

## 4. 流程規則

- 任何 setup 改進想法 → 寫入 `SETUP_V2_BACKLOG.md`，不現在實作
- 解除凍結 = 明確指示 + 版本號升 v2（v1 程式碼保留）

## 5. 驗證錨點

```bash
python regime_dual_engine/tests/test_regime_dual.py   # 11/11（regime 不受影響）
python production/tests/test_production.py            # Production V2 integration tests
```

## 6. 凍結依據（實證）

在凍結 Regime v1 + PIT breadth 下（2018-01..2025-07, top20, next-open, gap-aware exit）：

| Variant | CAGR% | Sharpe | MaxDD% | PF | AvgR |
|---|---|---|---|---|---|
| **A. Pullback Only** | **+2.23** | **0.33** | **−15.85** | **1.10** | +0.072 |
| B. BULL both / SW pullback | +1.19 | 0.19 | −17.63 | 1.05 | +0.055 |
| C. BULL breakout / SW pullback | −0.62 | −0.05 | −21.01 | 0.96 | −0.023 |

- A 全面勝出且跨年穩定（2019 +11.6% / 2020 +13.3%，最大年虧 −6.4%）。
- 核心洞察：Breakout 本身在 Regime v1 下是劣勢 setup（乾淨補位 breakout 仍 PF 0.923）——
  問題不是選擇機制，是 Breakout 本身。因此不需要重寫選擇邏輯。
- 報告：`reports/setup_v1_regime_policy_report.md`、`reports/setup_v1_validation_report.md`、
  `reports/setup_v1_root_cause_report.md`
