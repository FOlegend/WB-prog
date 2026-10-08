# Phase 2 Review Sheet — Exit Engine（請人類簽核）

> 用途：2 分鐘內驗證 Phase 2 是否符合「parity-first、legacy 為 oracle、production 零修改」。
> 簽核後才進入 Phase 3（接線計畫見 `reports/phase3_wiring_plan_2026-09-29.md`）。

---

## 0. 三十秒自我驗證

```bash
cd "<OneDrive>/WB-prog"
PY=/Users/curryzeng/.workbuddy/binaries/python/envs/wbprog/bin/python

$PY production/tests/test_exit_engine.py      # 期待 31/31 passed
$PY production/tests/replay_exit_parity.py    # 期待 DIVERGENCES: 0、151/151 對帳
$PY production/tests/test_stops.py            # Phase 1 未回歸 19/19
$PY production/tests/test_contracts.py        # 18/18
$PY production/tests/test_production.py       # 既有 10/10（會覆寫 reports/ 兩檔）
git checkout -- reports/                      # 還原上述副作用

git status --porcelain -uno                   # 期待：空輸出（production 零修改）
sqlite3 control_center/project_control.db \
  "SELECT subsystem,status FROM migrations WHERE subsystem LIKE '%Engine%';"
```

---

## 1. 待簽核項目（5 項）

### ☐ 1.1 `ExitDecision` 欄位集
`should_exit · ticker · direction · session_date · exit_reason_code · exit_price · fill_model · stop_reference_price · target_reference_price · detail · bar · provenance`
* 決策 3 要求的兩個 reference price 均在；`should_exit=False` 時不得攜帶執行欄位。

### ☐ 1.2 精確成交價規則（**無範圍判定**）
| fill | 契約等式 |
|---|---|
| GAP | `exit_price == bar.open` |
| STOP / TRAILING | `exit_price == stop_reference_price` |
| TARGET | `exit_price == target_reference_price` |
| CLOSE | `exit_price == bar.close` |

外加 reason↔fill 一致性。**沒有**任何「reasonable range」規則。

### ☐ 1.3 ⚠️ 兩個 stop level 的映射（唯一需要語義確認處）
legacy 位置有兩個 stop 概念：靜態 `pos["stop_price"]` 與推導的 `trail_stop`。Phase 2 映射為：
* `STOP_LOSS` → `StopPlan.initial_stop_price`
* `TRAILING_STOP` → `StopPlan.current_stop_price`（並以 Stop Engine 的 armed 狀態為 gate）

兩者同屬一個 StopPlan → Exit Engine **仍零重算**（有守衛測試）。請確認此解讀。

### ☐ 1.4 輸入收緊（2 項，文件化）
* `PriceBar` 要求合法 OHLC（open/close 落在 [low, high]）→ 缺 `close` 的 bar **fail loud**（legacy 會回退 entry price）。
* `session_date` / `entry_session` 必須為合法 `YYYY-MM-DD`（legacy 解析失敗會靜默 `held=0`）。
* 在**合法輸入**下行為與 legacy 完全相同（parity 已證）。

### ☐ 1.5 SHORT 僅契約層鏡像
契約與規則皆對稱實作並測試（stop/target/trailing/time/signal），但 `SHORT` 無 legacy 對照；
production 仍 LONG-only、未開啟 short trading、未動 entry logic。

---

## 2. 我承諾沒做的事（可查證）

| 承諾 | 證據 |
|---|---|
| 未修改任何受保護檔案 | `git status --porcelain -uno` 空；`test_phase2_isolation_not_wired` |
| 未把 legacy 改成 delegate | `portfolio_manager.py` 未變；parity 直接呼叫它 |
| Exit Engine 未自行算 trailing | `test_no_trailing_math_in_exit_engine`（rules/engine 無 ATR/trailing 運算） |
| 未接線 production | registry：8 個 Phase 2 模組 `production_used=0`（連同 Phase 1 共 23 個） |
| 未改 config / thresholds / freeze 面 | `production/config.py`、`config.py`、Regime v1、Setup v1 未動 |
| 未 commit | 仍是 untracked（AGENT_WORKFLOW：agent prepares, human commits） |

---

## 3. 已附證據

* `reports/phase2_exit_engine_2026-09-29.md`（A–K 報告）
* `reports/phase2_exit_parity_replay_2026-09-29.json`（151 trades / 1073 bars / 0 divergences / 151-151 對帳）
* `production/exits/DESIGN_NOTE.md`（設計與 parity 方法）
* `production/tests/exit_fixtures.py`（17 個可重現 fixtures）

## 4. 若要修改

直接列出要改的欄位／語義；我會以「Phase 2 修訂」處理（仍不動 production），並重跑 31 個 Exit 測試＋replay＋registry refresh。
