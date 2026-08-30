# Setup V1 Validation Report — frozen Regime v1 (HMM + PIT Breadth)

日期：2026-08-30　基準：**Regime v1**（HMM 50% + PIT Market Breadth 50%，dist overlay OFF）
方法：`regime_dual_engine/setup_v1_validation.py`（7 個回測，2018-01-01..2025-07-31, top20,
monthly buckets, next-open entry, gap-aware OHLC exit, 資金分配規則三組一致）
PIT 覆蓋：1905/1905 交易日（缺 0）✅

---

## T1 — Setup × Regime v1（進場時 regime 交叉表）

| setup | regime | n | PF | Win% | AvgR | PnL $ | contrib % |
|---|---|---|---|---|---|---|---|
| breakout | **BULL** | 223 | **1.321** | 53.4 | +0.151 | +264.62 | +399.8 |
| breakout | SIDEWAYS | 215 | 0.683 | 39.1 | −0.144 | −198.44 | −299.8 |
| pullback | **BULL** | 296 | **1.342** | 52.4 | +0.188 | +362.22 | +163.7 |
| pullback | SIDEWAYS | 391 | 0.870 | 42.7 | −0.016 | −140.97 | −63.7 |
| *both* | *BEAR* | *0* | — | — | — | — | — |

**發現**：
- **BULL 是唯一賺錢 regime**（兩種 setup PF>1.3）；**SIDEWAYS 全部虧損**——SIDEWAYS 是績效最大拖累。
- BEAR 無交易（regime gate size=0 不開倉，符合設計）。
- pullback 在 BULL 的 n 較多（296 vs 223）且 AvgR 較高。

## T2 — Setup score threshold 敏感度（breakout only）

| threshold | trades | PF | Win% | AvgR | CAGR% | MaxDD% |
|---|---|---|---|---|---|---|
| 0.4 | 438 | 1.04 | 46.1 | +0.003 | 0.76 | −17.64 |
| **0.5（現行）** | 438 | 1.05 | 46.3 | +0.006 | 0.80 | −17.38 |
| 0.6 | 432 | 1.06 | 46.8 | +0.017 | 1.00 | −18.29 |
| 0.7 | 408 | 0.99 | 45.6 | −0.017 | 0.06 | −19.43 |

**發現**：0.4–0.6 平滑（CAGR 0.76→1.00%），**0.7 崩壞**（CAGR 0.06%、PF<1）。
現行 0.5 合理；依 spec **不因 CAGR 選 0.6**（差異小、0.7 警告過濾過度）。

## T3 — Quality multiplier 有效性（breakout only）

| 版本 | CAGR% | Sharpe | MaxDD% | PF | Exposure% |
|---|---|---|---|---|---|
| **current mult** | 0.80 | 0.15 | **−17.38** | 1.05 | 54.5 |
| fixed 1.0 | 0.77 | 0.13 | −23.71 | 1.04 | 55.4 |

**發現**：**現行 quality mult 有效**——MaxDD 改善 6.3pp（−23.71→−17.38）、Sharpe 略升。
mult 提供防護價值，保留。

## T4 — Live vs Backtest 一致性審計（靜態，9 項）

| # | 項目 | Live | Backtest | 判定 |
|---|---|---|---|---|
| 1 | 進場訊號源 | technicals weighted | **setup_signal** | ⚠️ MISMATCH |
| 2 | next-open entry | 當日 close | next open | ⚠️ MISMATCH |
| 3 | gap filter | 無 | GAP_TOO_HIGH | ⚠️ MISMATCH |
| 4 | extension filter | 無 | EXTENDED_FROM_PIVOT | ⚠️ MISMATCH |
| 5 | ATR sizing | size_position | size_position | ✅ MATCH（同函式） |
| 6 | stop/target/trailing | close-only | gap-aware OHLC | ⚠️ PARTIAL |
| 7 | quality multiplier | 無 | 有 | ⚠️ MISMATCH |
| 8 | briefing/order 輸出 | briefing.md + orders.json | trade_log JSON | ✅ MATCH（目的不同） |
| 9 | **Regime 來源** | **v3 market_regime** | **Regime v1** | ⚠️ MISMATCH |

**關鍵結論**：**live 路徑尚未接 Setup V1 也未接 Regime v1**——6/9 不一致，最嚴重的是
main.py 仍在用 weighted entry（technicals）與 v3 5-component regime。

## T5 — Core Setup 比較（Regime v1 + PIT，2018-2025）

| variant | Ret% | CAGR% | Sharpe | MaxDD% | PF | Win% | trades | Exp% |
|---|---|---|---|---|---|---|---|---|
| Breakout Only | +6.24 | 0.80 | 0.15 | −17.38 | 1.05 | 46.3 | 438 | 54.5 |
| **Pullback Only** | **+18.19** | **2.23** | **0.33** | **−15.85** | **1.10** | 46.9 | 687 | 68.3 |
| Breakout+Pullback | +6.47 | 0.83 | 0.14 | −26.38 | 1.03 | 45.6 | 697 | 65.0 |

**⚠️ 重大發現 — v3 結論不 transfer 到 Regime v1**：

| | v3 regime（先前） | Regime v1（本次） |
|---|---|---|
| Breakout Only | **+29.86%**（最佳） | +6.24%（次佳） |
| Pullback Only | +5.17%（最弱） | **+18.19%（最佳）** |
| Breakout+Pullback | +20.73% | +6.47%，MaxDD −26.38%（最差） |

- v3 下 Breakout 勝出；**v1 下 Pullback 反而最佳**（CAGR 2.23% vs 0.80%，Sharpe 0.33 vs 0.15）。
- **組合（Both）在 v1 下 MaxDD 惡化至 −26.38%**——比單一 setup 更差，資金分配/訊號優先順序需 refinement。
- 資金分配規則三組一致（同一 engine、同一 priority），非剔除偏差；差異來自 setup 訊號本質。

---

## 判定：**NEEDS REFINEMENT**

**原因（明確）：**
1. **Setup 選擇在 v1 下未定論**：v3 證明「Breakout 最佳」在 Regime v1 下**被推翻**（Pullback Only
   全面勝出：CAGR 2.23% vs 0.80%、MaxDD 更小）。無法依 v3 結果直接上 Production。
2. **SIDEWAYS 是績效黑洞**：兩種 setup 在 SIDEWAYS 全部虧損（breakout PF 0.68 / pullback PF 0.87），
   BULL 是唯一賺錢 regime——setup 端需處理 SIDEWAYS 進場過濾（regime v1 凍結，須在 setup 層解決）。
3. **組合訊號需 refinement**：Breakout+Pullback MaxDD −26.38% 劣於單一（−15.85/−17.38%），
   多訊號同時觸發的資金優先順序需定義（spec 提示的 priority rules）。
4. **live 未接線**：T4 顯示 main.py 仍用 weighted entry + v3 regime，Setup V1 尚未存在於 live 路徑
   （6/9 不一致），無法宣稱 ready。

**已驗證可用（v1 下）**：threshold 0.5 穩健（0.4-0.6 平滑）、quality mult 有效（MaxDD −6.3pp）、
regime gate 正確排除 BEAR、PIT 資料覆蓋完整。

**仍未被證明**：任何 setup 在 v1 下的 out-of-sample 穩定性（walk-forward）、
SIDEWAYS 進場過濾方案、組合優先順序方案、live 實裝後的一致性。

---
*Regime v1 / Screener / setup_agent 未修改。新增：`setup_v1_validation.py`、`audit_live_backtest.py`。*
