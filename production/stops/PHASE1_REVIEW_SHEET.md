# Phase 0-1 Review Sheet — 請人類簽核

> 用途：讓你在 2 分鐘內驗證 Phase 0-1 是否符合「成功標準：清楚的 Stop Engine contract + isolated implementation + tests，且**沒有改變 production behaviour**」。
> 簽核完成後才進入 Phase 2（見 `reports/phase2_exit_engine_plan_2026-09-28.md`）。

---

## 0. 三十秒自我驗證（可自行重跑）

```bash
cd "<OneDrive>/WB-prog"
PY=/Users/curryzeng/.workbuddy/binaries/python/envs/wbprog/bin/python

# 1) 新測試（37）
$PY production/tests/test_contracts.py     # 期待 18/18 passed
$PY production/tests/test_stops.py         # 期待 19/19 passed

# 2) 既有測試零回歸（32）
$PY production/tests/test_datasource.py    # 8/8
$PY production/tests/test_pipeline.py      # 9/9
$PY production/tests/test_backtest.py      # 5/5
$PY production/tests/test_production.py    # 10/10  ← 會覆寫 reports/ 兩檔
git checkout -- reports/                   # 還原上面那個測試的副作用

# 3) 硬規則證據：受保護檔案零修改
git status --porcelain -uno                # 期待：空輸出

# 4) registry 狀態
sqlite3 control_center/project_control.db \
  "SELECT subsystem,status,blocker FROM migrations WHERE subsystem='Stop Engine';"
```

---

## 1. 待簽核項目（6 項）

### ☐ 1.1 契約定案（EntryDecision / StopPlan / RiskDecision / OrderIntent / PositionState）
* 看什麼：`production/contracts/*.py` 的 docstring 與 `validate()`；`DESIGN_NOTE.md §2` 的欄位/單位表。
* 我的設計選擇（請確認或要求修改）：
  * `EntryDecision` **刻意不含 shares**（sizing 屬 Risk Engine）。
  * `StopPlan` 區分 `initial_stop_price`（定義 1R）與 `current_stop_price`（現行）。
  * `RiskDecision.status` 為 **APPROVE / RESIZE / REJECT**（非 boolean）。
  * `OrderIntent` 把 signal / execution / risk 三者欄位分開。
  * `PositionState` 含 `current_stop_price` 概念但**不持久化**。
* 單位與時間語義：價格與 ATR = USD/share；atr_multiple 無因次；risk = USD；shares = 整數；`session_date` = naive `YYYY-MM-DD`。

### ☐ 1.2 reason-code 詞彙
* 看什麼：`production/contracts/reason_codes.py`（封閉集合，測試斷言 ≤10 個 stop codes）。
* 現有：`INITIAL_ATR` / `NEW_HIGH` / `TRAILING_ATR` / `STOP_UNCHANGED` / `TRAILING_NOT_ARMED` ＋錯誤碼 `ATR_UNAVAILABLE` / `INVALID_ATR` / `INVALID_MULTIPLE` / `INVALID_PRICE` / `INVALID_DIRECTION`；`STRUCTURE_STOP` / `STRUCTURE_BREAK` / `STRUCTURE_LEVEL_MISSING` **保留未實作**。
* 語義重點：`NEW_HIGH` = anchor 創新高造成改善；`TRAILING_ATR` = 無新極值但改善。

### ☐ 1.3 Stop Engine freeze face（是否可作為後續 wiring 的凍結面）
* 對外介面僅 5 個：`initial_stop_plan` / `update_stop` / `plan_from_position` / `atr_initial_stop` / `atr_trailing_stop`（＋`StopPlan` re-export）。
* 保證：deterministic、可解釋（`StopUpdate.explain()`）、不可放鬆、LONG/SHORT 對稱。
* 影響：一旦凍結，Phase 2 之後的接線以此為準。

### ☐ 1.4 structure stop：確認維持「未定義、不實作」
* 現況：`StopPlan.structure_level` 只是保留欄位；沒有任何 structure 計算。
* 待決（backlog B1）：定義方式（prior swing low / N-bar low / pivot…）、lookback、buffer、與 ATR stop 的關係（取較緊 / 較鬆 / 切換）。
* 若你現在不定義，Phase 2 不會碰它 — 請確認這符合預期。

### ☐ 1.5 `current_stop_price` 不持久化：確認可接受
* 現況：Stop Engine 會計算並回傳；`src/state/state.py` 與 production position schema **未動**。
* 影響：目前 production 仍每次由 `highest_since_entry` 重新推導 trailing stop；daily ledger 不會出現「今天 stop 由 X 變 Y」的持久紀錄。
* 待決（backlog B2）：Position / Ledger / State redesign 時再接入。

### ☐ 1.6 SHORT 僅鏡像、未啟用：確認可接受
* 契約與引擎已對稱實作並測試；`SHORT` 無 legacy 對照（baseline 只有 LONG），以對稱不變式驗證。
* production 仍 LONG-only；未動 entry logic / pipeline / 未開啟 short trading。

---

## 2. 我承諾沒做的事（可查證）

| 承諾 | 證據 |
|---|---|
| 未修改任何受保護檔案 | `git status --porcelain -uno` 空；`test_phase1_isolation_not_wired` 斷言 6 個檔案不含新 import |
| 未把 legacy 改成 delegate | `src/agents/risk_manager.py` 與 `src/portfolio/portfolio_manager.py` 內容未變（parity 測試直接呼叫它們） |
| 未改 config / thresholds | `production/config.py`、`config.py` 未修改 |
| 未動 Regime v1 / Setup v1 | `regime_dual_engine/**`、`src/agents/setup_agent.py` 未修改 |
| 未接線 production | registry 顯示新模組 `production_used=0`（15 檔）；其他 12 個 production 模組維持 1 |
| 未 commit | 所有變更仍為 untracked（AGENT_WORKFLOW：agent prepares, human commits） |

---

## 3. 簽核後解鎖什麼

* **Phase 2**：Exit Engine 契約化（計畫見 `reports/phase2_exit_engine_plan_2026-09-28.md`）— 仍不改 production，先以 parity 重現 `_exit_check` 的五種出場。
* 之後才討論任何 wiring / cut-over（需 production-equivalent 單變量比較）。

## 4. 若要修改

請直接在回覆列出要改的欄位／語義／reason code，我會以「Phase 1 修訂」處理（仍不動 production），並重跑 37 個測試與 registry refresh。
