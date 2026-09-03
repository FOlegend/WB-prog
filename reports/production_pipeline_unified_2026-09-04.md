# Production V2 — Unified Decision Pipeline 交付報告（2026-09-04）

> Phase 目標：**不做任何交易邏輯優化**。讓 production 與 backtest 共用同一條確定性決策管線
> （`run_daily`），使端到端系統可被量測、可審計、可重播。之後才科學決定下一個研究目標
> （Entry / Risk / Exit / Portfolio）。

---

## 1. 檔案清單（新增/修改）

| 檔案 | 狀態 | 說明 |
|---|---|---|
| `production/pipeline.py` | 🆕 | **canonical**：`run_daily()` → DecisionRecord（schema v1.0） |
| `production/datasource.py` | 🆕 | DataSource 抽象 + Cached/YFinance/Stooq + Provenance |
| `production/ledger.py` | 🆕 | DecisionRecord → `reports/decision_YYYY-MM-DD.json` |
| `production/backtest.py` | 🆕 | Production-equivalent backtest（replay `run_daily`） |
| `production/screener/screener.py` | ✏️ | + `screen_from_source()`；移除靜默 fallback |
| `production/main.py` | ✏️ | thin CLI over pipeline（compat `run_daily(cfg,…)` 保留） |
| `production/config.py` | ✏️ | + `slippage_pct=0.0005`（執行費用模型，唯一新增欄位） |
| `production/tests/test_datasource.py` | 🆕 | 8 tests |
| `production/tests/test_pipeline.py` | 🆕 | 9 tests |
| `production/tests/test_backtest.py` | 🆕 | 5 tests |
| `reports/decision_2025-07-31.json` | 🆕 | Decision Ledger 範例 |
| `reports/production_bt_2024-01-01_2025-07-31.json` | 🆕 | production-equivalent backtest 結果 |
| `reports/production_buckets_*.json` | 🆕 | 每月 PIT screen bucket cache（可重用） |

未動（freeze）：`regime_dual_engine/`（Regime v1）、`src/agents/setup_agent.py`（Setup v1）、
`production/agents/regime.py`、`production/agents/setup.py`、risk/entry/exit 邏輯本體、
所有 screener thresholds / ranking / top-N 語義。舊 `dynamic_universe_backtest.py` 保留為 legacy research。

---

## 2. 管線描述（架構圖）

```
                        ┌────────────────────────────────────────────┐
  as_of + state ──────► │  production.pipeline.run_daily()           │
                        │  (唯一決策入口：live 與 backtest 共用)       │
  DataSource ─────────► │                                            │
   ├─ CachedSource      │  1. DATA     每檔 (df, Provenance)          │
   │  (backtest/PIT)    │  2. REGIME   Regime v1 (frozen wrapper)     │
   └─ YFinanceSource    │  3. SCREENER screen_from_source (6-filter)  │
      (live, cache-     │  4. SETUP    Setup v1 Pullback (frozen)     │
       first → stooq)   │  5. RISK     size_swing_position            │
                        │  6. EXIT     gap-aware _exit_check          │
                        │  7. PORTFOLIO mark-to-market / constraints  │
                        │  8. DecisionRecord (可序列化、可重播)        │
                        └────────────────────────────────────────────┘
                                     │ 寫入
                                     ▼
                        reports/decision_YYYY-MM-DD.json (ledger)
                        + human briefing (main.py render)

  ProductionBacktest ──► 每日呼叫 run_daily(as_of, state, CachedSource, cfg)
                         執行 DecisionRecord 的建議 → 更新 state → 明日再呼叫
```

執行慣例（與既有驗證一致）：D 收盤產生訊號 → SELL 當日以 gap-aware exit_price 成交 →
BUY 排入 pending → D+1 **開盤**執行（re-check bucket 成員、gap ≤2%、extension ≤3%、
於實際 open 價重算 sizing）。成本：買入 `slippage_pct`、賣出沿用 validated 費用模型。

---

## 3. `run_daily()` 介面 + DecisionRecord schema

```python
def run_daily(as_of: str, state: dict, data_source: DataSource,
              cfg: ProductionConfig,
              *, screen_mode: "live"|"bucket"|"provided"|"held_only" = "live",
              candidate_tickers: list[str] | None = None,
              screen_result: dict | None = None,
              universe: list[str] | None = None,
              verbose: bool = False) -> dict   # DecisionRecord
```

確定性保證：純函數（不讀時鐘、不碰全域狀態）；輸入 `state` **deepcopy 不 mutate**；
所有 iteration 排序確定；兩次相同輸入 → 位元相等輸出（有測試）。

DecisionRecord schema（v1.0，15 節，全部 JSON-serialisable）：

| 節 | 內容 |
|---|---|
| `meta` | schema_version / as_of / generated_by / pipeline_status |
| `data` | benchmark（provider/file/rows/as_of_bound/ok/error）、breadth、candidate_providers、status |
| `regime` | status + input_rows + **public 5-field output**（無 _diag 洩漏） |
| `screener` | mode / status(OK·EMPTY·FAILURE) / candidates(rs_rank) / filters_applied / apply_mcap / provider_counts |
| `setup` | per-candidate 評估（valid/score/quality/atr/reason/skipped_reason） |
| `positions` | n_open/equity/cash/nav_mark/exposure_pct + 逐倉 mark 與 unrealized PnL |
| `exits` | 逐倉評估 + proposed SELL（exit_price/fill_model/reason/detail） |
| `entries` | status(OK·EMPTY·BLOCKED) / blocked_reason / proposed BUY（含 signal_close/prior_high20/atr 供 next-open 執行） |
| `risk` | 每 BUY sizing（stop/target/R:R/eff_size_mult） |
| `portfolio` | constraints（max_open/max_position_pct/risk_per_trade）+ warnings |
| `warnings` | 全域聚合 |
| `recommendations` | summary / buys / sells（人類審核摘要） |

---

## 4. DataSource 架構

```
DataSource (ABC)
├── CachedSource     唯讀 cache CSV + membership files；`datetime <= as_of` 嚴格切片
├── YFinanceSource   LIVE：cache-first → 過期自動 yfinance 更新並寫回 cache
│                    （失敗 → 可選 stooq recovery，**labelled**；stale cache 標記）
└── StooqSource      次要 validation/recovery（CSV endpoint，無第三方依賴）
```

每次請求回傳 `(DataFrame, Provenance{provider, file, rows, as_of_bound, stale, ok, error})`
→ DecisionRecord 記錄**每個 dataset 的來源**。原則：

- YFinance 保持 primary；Stooq 只作 secondary recovery，**永不靜默混用 provider**（fallback 必標記）。
- 快取下載歷史；來源失敗明確（回 `None` + error，**不回空 DF** 假裝成功）。
- 不補造缺失值；as-of 邊界明確（PIT 切片在 DataSource 層統一完成）。

---

## 5. Fail-loud 行為

| 情境 | 行為 |
|---|---|
| Screener `EMPTY` | 合法零候選 → 無 BUY（非錯誤） |
| Screener `FAILURE`（benchmark 缺 / universe 全無資料 / 崩潰） | entries=**BLOCKED**「screener FAILURE — no new BUY from incomplete data」+ warning；**仍評估持倉出場** |
| Regime `FAILURE`（SPY 不足） | entries=**BLOCKED**；exits 照常（不依賴 regime） |
| 持倉 ticker 資料不足 | 該倉 exit 無法評估 → **明確 warning**「EXIT_EVAL_INCOMPLETE」（絕不靜默假設無 exit） |
| 個股資料不足（candidate） | 該檔 skip 並記錄 `skipped_reason: insufficient data` |

不再有任何「screen 失敗 → 硬編碼前 10 檔」的靜默 fallback。

---

## 6. Decision Ledger 範例（`reports/decision_2025-07-31.json` 摘要）

```json
{
  "schema_version": "1.0", "as_of": "2025-07-31", "pipeline_status": "OK",
  "data": {
    "benchmark": {"ticker": "SPY", "provider": "cache", "file": "SPY.csv",
                  "rows": 2408, "as_of_bound": "2025-07-31", "ok": true},
    "breadth": {"kind": "pit-or-current", "rows": 2408, "ok": true}
  },
  "regime": {"status": "OK", "output": {"regime_label": "BULL",
             "composite_score": 70.858, "position_size_mult": 0.5,
             "strategy_mode": "trend_following",
             "veto_flags": ["BEARISH_BREADTH_DIVERGENCE"]}},
  "screener": {"mode": "provided", "status": "OK", "n_candidates": 5},
  "setup": {"evaluations": 5, "n_valid": 1},
  "entries": {"status": "EMPTY", "n_buys": 0},
  "positions": {"equity": 1282.0, "cash": 1282.0, "n_open": 0},
  "recommendations": {"summary": "no action"}
}
```

---

## 7. Production-equivalent Backtest（`production/backtest.py`）

- **不是第二套決策實作**：引擎只是 DecisionRecord 的執行器。每日呼叫 `run_daily()`；
  買賣按 record 的 proposed 行動與執行慣例套到 state。
- **時序顯式**：monthly PIT screen（`screen_from_source` as_of，同一函式）→ bucket 為該月
  screener 決策 → 每日 run_daily 吃 bucket；SELL 當日 gap-aware fill；BUY pending 次日 open，
  re-check gap/extension/bucket 後以實際 open 重算倉位。
- 成本/狀態演化沿用既有（`slippage_pct`、賣出費用模型、mark-to-market、trade_log）。
- bucket cache（`production_buckets_*.json`）→ 重跑免重算（PIT 確定性，安全重用）。
- **執行結果（2024-01-01 ~ 2025-07-31，top-30，19 個月 screen 全 OK、0 FAILURE）**：

| 指標 | 值 |
|---|---|
| Return / CAGR | +7.99% / 5.01% |
| Sharpe | 0.935 |
| MaxDD | −5.98% |
| Trades / Win% | 151 / 50.3% |
| Profit Factor | 1.29 |
| Exposure days | 68.9% |
| Skipped entries / Failed screens | 5 / 0 |
| Equity end | $1,384.44（起 $1,282） |

> 註：數字與 legacy dynamic backtest 不同屬**預期**（Regime v1 + Pullback Only + top-30 vs
> 舊 v3 + both-setups + top-20）。本階段不比較、不優化績效。

---

## 8. 測試結果（32/32 PASS）

| 套件 | 覆蓋 | 結果 |
|---|---|---|
| `test_datasource.py` | primary success / PIT slice / missing=FAILURE / insufficient bars / cache / stale fallback / **stooq labelled** / **no provider mixing** | 8/8 |
| `test_pipeline.py` | determinism / state 不 mutate / regime schema / **valid EMPTY** / **FAILURE→BLOCKED** / **exit despite screen failure** / **no BUY on failure** / held_only / schema keys | 9/9 |
| `test_backtest.py` | determinism（兩次全等）/ window 覆蓋 / **entry 皆在其 bucket 之後且為成員**（no lookahead）/ trade dates 在窗內 / run_daily 產 DecisionRecord | 5/5 |
| `test_production.py`（既有） | Regime v1 not v3 / 無 DistDays / schema 5 欄 / Pullback Only / 無 weighted entry / BEAR 擋多 / quality sizing / next-open 契約 / end-to-end | 10/10 |

---

## 9. Regime v1 / Setup v1 未變確認

- Regime v1：production 仍只經 `production/agents/regime.py` → `compute_regime_decision`
  （frozen facade）；DecisionRecord 輸出即其 public 5-field schema；測試確認
  `veto_flags` 無 `DISTRIBUTION_DAY_CAP`、`_diag.distribution_days == 0`。
- Setup v1：仍 `evaluate_setup` → `setup_signal` with Pullback Only freeze guard；threshold 0.5、
  quality mult、gap/extension、next-open、gap-aware exit 全部保留原值。
- 唯一 config 新增欄位 = `slippage_pct`（執行費用模型基礎設施，非交易參數）。

---

## 10. 已知限制 / Blockers

1. **PIT breadth cache 至 2025-07-31**：backtest `end` 自動 clamp（print 警告）；
   live 2025-08 後需更新 breadth cache（既有 Blocker B1 的一部分）。
2. **Screener 頻率**：replay 採 monthly bucket（沿用 legacy 日曆），live 為每日。
   bucket 由**同一** `screen_from_source` 以 PIT 計算 → 決策管線一致，僅頻率不同。
3. **mcap filter**：PIT screen 跳過（取今日市值 = look-ahead），DecisionRecord 記錄
   `apply_mcap=false` + warning；live 仍套用（selection audit 已知、方向保守）。
4. **Universe survivorship**：現行成分快照（與 live Wikipedia 一致）；已披露並為
   freeze 契約接受；可選下一步用 `data/constituents/*_thuningxu.csv`（PIT membership）強化。
5. **Stooq recovery 預設關閉**（`allow_stooq_fallback=False`）：作為 resilience 選項存在且
   labelled，正式啟用前需先決定政策。
6. `YOT/`（另一專案 169M、含自有 .git）誤放 repo 根目錄 — 未追蹤、本次未動；
   建議移出 repo 或加入 .gitignore。

---

## 下一步（本階段之後，需另行 freeze/指示）

1. Reviewer 驗證 freeze contract（管線 = 新增 freeze 面）。
2. 視需要把 `screen_from_source` 接回 live 主流程（消除 main 對舊 wrapper 的最後依賴）。
3. 更新 breadth cache 以支援 2025-08 之後的 live/回測。
4. 之後才以本 end-to-end backtest 科學決定研究目標（Entry / Risk / Exit / Portfolio）。
