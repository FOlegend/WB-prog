# Production V2 — Build Report

日期：2026-08-30　狀態：**完成（STOP）**
凍結基準：Regime v1（`regime_dual_engine`，HMM 50% + PIT Market Breadth 50%）+
Setup v1（`src/agents/setup_agent.py`，**Pullback Only**）

---

## 1. Production V2 Folder Tree

```
production/
├── __init__.py
├── config.py                  # ProductionConfig — 凍結參數（資金/Regime v1/Screener/Setup v1/Risk）
├── main.py                    # 每日簡報 runner（pipeline 編排，human-in-the-loop）
├── agents/
│   ├── __init__.py
│   ├── regime.py              # Regime v1 wrapper（compute_regime_decision，非 v3）
│   └── setup.py               # Setup v1 wrapper（setup_signal，Pullback Only + freeze guard）
├── screener/
│   ├── __init__.py
│   └── screener.py            # 既有 6-filter screener（reuse，不重建）
├── risk/
│   ├── __init__.py
│   └── risk.py                # ATR sizing（size_swing_position = size_mult × quality_mult）
├── portfolio/
│   ├── __init__.py
│   └── portfolio.py           # 出場（_exit_check 重用）+ BUY 訂單建構（無 weighted entry）
├── reporting/
│   ├── __init__.py
│   └── briefing.py            # 每日簡報 renderer（3-10 候選）
├── data/                      # 執行時 state.json（自動生成）
└── tests/
    ├── __init__.py
    └── test_production.py     # 10 integration tests
```

## 2. Files Created/Modified

**新增（隔離，未動舊檔）**：
- `production/` 全部（上述 17 檔）
- `src/agents/SETUP_V1_FREEZE.md`、`src/agents/SETUP_V2_BACKLOG.md`
- `reports/setup_v1_regime_policy_report.md`（先前階段）

**修改**：
- `dynamic_universe_backtest.py`：加入 `_setup_signal_policy()` harness knob（`cfg.setup_regime_policy`，
  預設 None 行為不變）——供 research 重用；production 未使用
- `reports/briefing_2024-05-28.md`、`orders_2024-05-28.json`（demo 輸出）

**未動**：`regime_dual_engine/`（Regime v1 凍結）、`src/agents/setup_agent.py`（Setup v1 凍結）、
`src/screener/screener.py`（reuse）、legacy main.py / backtest.py / 研究腳本。

## 3. Exact Execution Flow

```
production/main.py
  → 1. Regime v1：SPY OHLCV + PIT breadth → compute_regime_decision
       （HMM 50% + Market Breadth 50% → composite → BULL/SIDEWAYS/BEAR + Thrust/Divergence）
  → 2. Screener（reuse 6-filter：MCap/DVol/Price/ATR%/RS/ADX）
  → 3. Setup v1（Pullback Only）：每候選 setup_signal(df, cfg, rs_rank)
       （px>50/200SMA + 50>200 + nearEMA(10/20,±1%) + vol_contract + reversal + RS_strong）
  → 4. Risk：size_swing_position（equity×1%×size_mult×quality_mult / ATR×1.5）
  → 5. 出場檢查（持倉）：_exit_check（STOP/TARGET/TRAILING/TIME/SIGNAL，gap-aware OHLC）
  → 6. Briefing：market regime header + top setups（entry/stop/target/R:R/reason）→ human 審核
```

## 4. Regime v1 Integration ✅

- `production/agents/regime.py` 直接呼叫 `regime_dual_engine.engine.compute_regime_decision`
- 使用凍結 `DualEngineConfig`（50/50、65/35、overlay OFF）
- PIT breadth 優先，current-constituent fallback
- 測試 `test_calls_regime_v1_not_v3` 驗證：source 無 v3 import；`test_regime_v1_no_dist_days`
  驗證：veto 無 `DISTRIBUTION_DAY_CAP` 且 `distribution_days==0`

## 5. Screener Integration ✅

- `production/screener/screener.py` 包裝既有 `src.screener.screener.screen`（不重建、不複製 filter）
- 輸出 RS 排序 list → 提供 `rs_rank` 給 Setup v1
- ADX 留在 screener（stock-level），**不進入 Regime v1**（測試 `test_calls_regime_v1_not_v3` 確認）

## 6. Setup v1 Integration ✅

- `production/agents/setup.py` 使用 `src.agents.setup_agent.setup_signal`（source implementation）
- freeze guard：`assert cfg.setup_enabled_types == ["pullback"]`（測試 `test_setup_v1_pullback_only`）
- 無 Breakout / VCP / 新指標 / threshold 優化（thr=0.5 凍結）

## 7. Risk/Execution Integration ✅

- ATR sizing 重用 `risk_manager.size_position`（與 validated backtest 同函式）
- effective mult = `regime.size_mult × setup.quality_mult`（測試 `test_quality_affects_sizing`）
- 出場重用 `portfolio_manager._exit_check`（STOP/TP/TRAILING/TIME/SIGNAL，gap-aware OHLC）
- next-open entry + gap(≤2%)/extension(≤3%) guardrails 在 brief 中明示
  （測試 `test_next_open_gap_extension_contract`）

## 8. Daily Briefing Example（2024-05-28 demo，BULL）

```
Market Regime: BULL  Composite: 71  Strategy: trend_following  Size: 0.50
Vetoes: BEARISH_BREADTH_DIVERGENCE
Top Setups:
1. AAPL   Pullback | Score 0.75 | Quality 0.75 | Entry $188.36 | Stop $184.24 | Target $195.22 | R/R 1.67
2. GOOGL  Pullback | Score 0.60 | Quality 0.75 | Entry $174.85 | Stop $170.38 | Target $182.31 | R/R 1.67
3. NFLX   Pullback | Score 0.75 | Quality 0.75 | Entry $64.90  | Stop $62.77  | Target $68.46  | R/R 1.67
4. AMZN   Pullback | Score 1.00 | Quality 1.00 | Entry $182.15 | Stop $177.23 | Target $190.36 | R/R 1.67
```
（完整檔：`reports/briefing_2024-05-28.md`；BEAR 日 2025-07-31 demo 驗證「no new long」）

## 9. Test Results

| 套件 | 結果 |
|---|---|
| `regime_dual_engine/tests/test_regime_dual.py` | **11/11 passed**（Regime v1 不受影響） |
| `production/tests/test_production.py` | **10/10 passed** |

Production 10 tests：① calls Regime v1 (not v3) ② no DistDays ③ schema ④ Setup v1 pullback-only
⑤ no weighted entry ⑥ BEAR blocks long ⑦ quality affects sizing ⑧ next-open/gap/extension contract
⑨ briefing renders ⑩ pipeline end-to-end（offline, 2025-07-31 BEAR → 無 BUY；2024-05-28 BULL → 4 候選）

## 10. Live/Backtest Differences（已文件化）

| 項目 | Backtest | Production V2（live） | 差異 |
|---|---|---|---|
| 資料源 | cache（PIT 覆蓋 83.4%） | yfinance / cache-first | live 需每日更新 cache 才得最新 breadth（fallback current-constituent, Pearson 0.996） |
| 進場 | next-open 自動模擬 | brief 明示 next-open + gap/extension 檢查，**人為執行** | 人為環節 = 風險點（briefing 已提示） |
| 出場 | 自動（OHLC bar） | `_exit_check` 同邏輯，人為下單 | 執行時點人為控制 |
| 倉位 | 自動資金分配 | 同 `size_position` | 一致 |
| 狀態 | 模擬 state | `production/data/state.json` 持久化 | 一致（同一 src.state） |

## 11. Remaining Blockers Before Paper/Live Use

1. **每日資料更新流程**：live 前需跑 `historical_cache.get_all_data()`（或等效）更新 OHLCV cache，
   使 PIT breadth 反映當日——否則 breadth 停留在 cache 末日（fallback 差異可接受但需記錄）。
2. **Screener 網路依賴**：`screen()` 抓 Wikipedia + yfinance；失敗時 fallback universe（10 檔）。
   Paper 前建議先驗證一次完整 screener 輸出。
3. **狀態初始檔**：`production/data/state.json` 尚不存在——首次執行會以全新資金開始（需確認）。
4. **`--tickers` 模式**：指定標的跳過 screener（與 legacy main 一致）；`--no-screen` 只做持倉出場。
5. **暫無自動下單**（by design）：human-in-the-loop，需人類在 broker app 執行並回填 state.json。

---

**已完成全部 11 節交付。依指示 STOP，不再做策略變更。**
