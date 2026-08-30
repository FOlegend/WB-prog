# Repository Archaeology — WB Swing Trading Bot

日期：2026-08-30　模式：**唯讀盤點**（未刪除/移動/修改任何檔案）

---

## 1. 目前 Production 架構（實際執行的）

**注意：main.py 目前跑的是舊 v3 regime（5-component），不是新兩引擎！**

```
main.py（每日簡報入口）
  ├─ src/screener/screener.py      → 6-filter 篩選（S&P500+NQ100, MCap>10B, DollarVol>50M,
  │                                   Price>10, ATR%>2, RS>1.0, ADX>20）→ top 30
  ├─ src/agents/regime_agent.py    → v3 5-component regime（HMM20%+MA30%+KER18%+ADX10%+Dist22%）
  │                                  → SPY 全域 size_mult + strategy
  ├─ src/agents/technicals_agent.py→ 個股技術 ensemble（trend/MR/momentum/volatility）
  ├─ src/agents/risk_manager.py    → ATR 倉位 × size_mult
  ├─ src/portfolio/portfolio_manager.py → 加權決策（net = 0.35×regime + 0.65×tech）→ BUY/SELL
  ├─ src/state/state.py            → data/state.json 持倉持久化
  └─ src/utils/display.py          → reports/briefing_*.md + orders_*.json
```

- **Regime Agent 位置**：`src/agents/regime_agent.py`（`market_regime` / `regime_score_engine`）
- **Regime 輸出**：`{regime, regime_score, position_size_mult, strategy, components, vetoes}`
- **重要發現（gap）**：新兩引擎 `regime_dual_engine/` 已驗證完畢但**尚未接入 main.py**。
  Production 仍用含 KER/ADX/MA/Dist 的 v3 5-component regime。

## 2. 目前執行路徑（import 追蹤）

| 檔案 | 被誰使用 | 角色 |
|---|---|---|
| `main.py` | —（入口） | live 每日簡報 |
| `config.py` | 全部 | 全域參數 |
| `src/data/data_fetcher.py` | main, screener, engine | yfinance 抓取 |
| `src/screener/screener.py` | main, historical_cache | live 篩選 |
| `src/agents/regime_agent.py` | main, backtest/engine, dynamic, dual_engine | v3 regime |
| `src/agents/technicals_agent.py` | main, backtest/engine, dynamic | 個股訊號 |
| `src/agents/risk_manager.py` | main, portfolio, engine, dynamic | 倉位 |
| `src/portfolio/portfolio_manager.py` | main | 決策層 |
| `src/state/state.py` | main, dynamic | 持久化 |
| `src/utils/display.py` | main | 簡報 |
| `src/indicators/technicals.py` | 多處 | 指標庫 |
| `src/backtest/engine.py` | backtest.py | 舊回測引擎 |
| `src/backtest/report.py` | backtest.py | 舊 HTML 報告 |
| `dynamic_universe_backtest.py` | —（research 入口） | 動態回測（最新方法論） |
| `screen_as_of.py` / `historical_cache.py` | dynamic_universe_backtest | PIT 篩選 / 資料層 |
| `src/agents/setup_agent.py` | **僅 dynamic_universe_backtest** | ⚠️ Setup Bot（未接入 main.py） |

## 3. Screener 位置與輸出

- **Live**：`src/screener/screener.py` → `screen(cfg)` → DataFrame（ticker + 6 指標）
- **PIT（回測）**：`screen_as_of.py` → `screen_as_of(all_data, as_of_date)` → top_n ticker list
- **輸出快取**：`data/screener_top20.csv`（一次性）、`reports/dynamic_buckets_*.json`（每月 bucket）
- 6-filter pipeline 已在兩處共用同一 RS 邏輯（`_compute_rs_composite`），**不需重建**

## 4. Regime Agent 位置與輸出

- **v3（現行 production）**：`src/agents/regime_agent.py` → `market_regime(spy_df, cfg)`
- **新兩引擎（驗證完成、未接線）**：`regime_dual_engine/` → `engine.compute_regime_decision()`
  - 架構：HMM 50% + PIT Market Breadth 50% → Composite → BULL/SIDEWAYS/BEAR
  - 判定：**PASS**（見 reports/dual_engine_review_report.html）
  - ⚠️ **待辦：把 main.py 從 market_regime 切到 compute_regime_decision**

## 5. 所有 Setup Bot 候選

| 候選 | 說明 | 判定 |
|---|---|---|
| **`src/agents/setup_agent.py`** | Breakout（VCP+pivot）+ Pullback（回踩 EMA）雙 setup，含 setup_score/quality_mult/entry_reason | **✅ 最完整、即為 Setup Bot** |
| `setup_comparison.py` | 比較 setup vs legacy weighted entry 的回測腳本 | 附屬實驗 |
| `technicals_agent.py` | 舊技術 ensemble（weighted 入口） | Legacy 入口（被 setup 取代） |
| `portfolio_manager.py` 內 weighted 邏輯 | net=0.35×regime+0.65×tech | Legacy 入口（config 標註被 setup_agent 取代） |
| `breakout_ablation.py` / `breakout_comparison.py` | setup 組件消融/閾值比較 | 附屬實驗 |
| `audit_return_calculation.py` | 提到「Build setup_agent.py」的審計筆記 | 歷史備忘 |

## 6. 原版 Setup Bot 的最佳候選

**`src/agents/setup_agent.py`（284 行）** — 由 commit `b38dc05 "setup"`（2026-08-13）引入，
與 `setup_comparison.py`、`audit_return_calculation.py`、portfolio_manager 的 setup 整合同批提交。
git 歷史無刪除/改名記錄，無其他分支/標籤。這就是你要找的原版 Setup Bot，它**仍然存在**，
只是**只被 `dynamic_universe_backtest.py`（Type D entry）使用，尚未接入 `main.py` live 路徑**。

## 7. Legacy / Duplicate 叢集

| 叢集 | 檔案 | 狀態 |
|---|---|---|
| **舊 regime 實驗（v3.2）** | `v32_experiments.py`, `v32_robustness.py`, `ablation_test.py`, `hostile_regime_test.py` | 已被兩引擎取代 → LEGACY |
| **舊靜態回測** | `backtest.py`, `src/backtest/engine.py`, `src/backtest/report.py` | 被 dynamic 取代但仍可跑 → LEGACY |
| **舊 screener 靜態回測** | `screener_backtest.py`（有 look-ahead bias，已被 dynamic 取代） | LEGACY |
| **HMM 調參工具** | `hmm_diagnostic.py`, `hmm_vw_compare.py`, `hmm_backtest_compare.py` | 一次性調參 → LEGACY/EXPERIMENTAL |
| **Setup 消融** | `breakout_ablation.py`, `breakout_comparison.py`, `setup_comparison.py` | 保留為 research → EXPERIMENTAL |
| **回測審計** | `audit_return_calculation.py` | 一次性審計 → EXPERIMENTAL |
| **舊 breadth（current-constituent）** | `regime_dual_engine/data/breadth_2016_2025.csv`, `breadth_current_*.csv` | 被 PIT 取代 → LEGACY（保留對比） |
| **distribution_days.py** | dual_engine 內 | spec 明訂 research-only → RESEARCH |

## 8. 建議清理計畫（僅供參考，未執行）

**分階段（先備份再動）：**
1. **Phase A — 歸檔（不移除）**：把 LEGACY 叢集移入 `archive/` 子目錄：
   `v32_*.py`, `ablation_test.py`, `hostile_regime_test.py`, `hmm_*.py`（3個調參工具）,
   `screener_backtest.py`, `audit_return_calculation.py`
2. **Phase B — 標記**：`backtest.py` + `src/backtest/` 標為「legacy 靜態回測，僅供對比」
3. **Phase C — 保留**：`dynamic_universe_backtest.py` + `screen_as_of.py` + `historical_cache.py`
   （最新回測方法論，動態 screening 正確）
4. **Phase D — 接線（最重要）**：
   - main.py regime 切到 `regime_dual_engine.engine.compute_regime_decision`（兩引擎）
   - main.py 個股入口切到 `setup_agent.setup_signal`（Setup Bot 上線）
   - 驗證 PIT breadth 需每日更新（`pit_breadth_data.get_breadth`，83% 覆蓋為已接受限制）
5. **Phase E — 去重**：確認 `regime_dual_engine/breadth_data.py`（current）是否仍需保留
   （PIT 版已含 current 比較功能，可刪舊版）

## 9. 建議下一步（建置/精煉 Setup Bot）

1. **先把 Setup Bot 接入 live**：main.py 目前仍用 weighted entry（technicals ensemble）。
   改為 `entry_mode="setup"`：`setup_signal(df, cfg, rs_rank)` 產出
   `{valid, setup_type, setup_score, setup_quality_mult, entry_reason}`。
2. **接上兩引擎 regime**：main.py 的 `market_regime` → `compute_regime_decision`（SPY + PIT breadth），
   產出 `{regime_label, composite_score, position_size_mult, strategy_mode, veto_flags}`。
3. **Screener → Setup 的消費方式**（不重建 screener）：
   screener 產出 RS 排序的 top-N 候選（已含 6 filter）→ Setup Bot 只需消費
   `cand_df["ticker"]` + RS rank（`rs_rank = 1-based rank`）即可，不需要複製篩選邏輯。
4. **補上 setup 的 live 出場整合**：`dynamic_universe_backtest.py` 已有完整
   gap-aware exit / next-open entry / quality_mult 邏輯，main.py 的 portfolio_manager
   需對齊同一套（STOP_LOSS/TAKE_PROFIT/TRAILING_STOP/TIME_STOP/SIGNAL_EXIT）。
5. **每日 bread 更新**：live 需在盤前下載 PIT 成員 OHLCV → 更新 breadth（約 616 tickers,
   ~2-3 分鐘），或退而用 current-constituent breadth（Pearson 0.996，差異可接受）。

---
*本報告為唯讀考古，未修改任何檔案。分類標記：CURRENT/CORE/LEGACY/EXPERIMENTAL/DUPLICATE/UNKNOWN 見各節。*
