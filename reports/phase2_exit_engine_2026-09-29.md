# Phase 2 驗證報告 — Exit Engine（2026-09-29）

> **範圍**：Exit Engine 契約化 + legacy parity（`_exit_check` 為 oracle）
> **權威 copy**：`WB-prog` · **狀態**：完成，**停止並等待 human review**
> **核心紀律**：parity-first；legacy 一行未改、未 delegate；production 未接線；SHORT 僅契約層鏡像

---

## A. Files created（12）

| 檔案 | 內容 |
|---|---|
| `production/contracts/exit.py` | `ExitDecision` 契約（legacy reason codes / fill models；**精確**成交價規則） |
| `production/exits/context.py` | `ExitContext` + `ExitConfigSnapshot` + `build_exit_context()`（trailing **arming gate** 由 Stop Engine 提供，零重算） |
| `production/exits/rules.py` | 五條 legacy 規則（純函式，無 ATR/trailing 運算） |
| `production/exits/engine.py` | `evaluate_exit(ctx) -> ExitDecision` / `hold_decision()` |
| `production/exits/__init__.py` | 對外介面（+ `ExitDecision` re-export） |
| `production/tests/exit_fixtures.py` | 17 個 deterministic fixtures（無隨機、可重現輸入狀態） |
| `production/tests/test_exit_engine.py` | 31 測試（17 必要 parity + production-config parity + SHORT 鏡像 + 隔離/禁算守衛） |
| `production/tests/replay_exit_parity.py` | 真實交易級 replay parity harness（逐 divergence 報告） |
| `production/exits/DESIGN_NOTE.md` | 設計說明（契約/規則/優先序/parity/待確認） |
| `reports/phase2_exit_parity_replay_2026-09-29.json` | Replay 結果（1073 bars、0 divergences、151 對帳明細） |
| `reports/phase2_exit_engine_2026-09-29.md` | 本報告 |
| （契約擴充）`production/contracts/base.py` + `reason_codes.py` + `__init__.py` | +`PriceBar`；+exit reason/fill vocabularies；+re-exports |

## B. Files modified（**僅 Control Center 工具層**）

| 檔案 | 變更 |
|---|---|
| `control_center/classify.py` | `LAYER_RULES` +`exits/`→EXIT_ENGINE；`SUBSYSTEM_BY_LAYER` +Exit Engine；`SUBSYSTEM_CANONICAL` +Exit Engine；`CURATED` +8 條（全部 `production_used=0`）；`MIGRATIONS` +Exit Engine；`DECISIONS` +2 |
| `control_center/project_control.db` | refresh 重建：modules 116→**124**、deps **807**、migrations 5→**6**、decisions 8→**10** |
| `control_center/ARCHITECTURE.md` / `.yaml` | 重生（新子系統、新 migration、新決策） |

## C. Files not modified（**production safety，全部未動**）

```
production/pipeline.py          production/portfolio/portfolio.py
src/portfolio/portfolio_manager.py   production/risk/risk.py
src/agents/risk_manager.py      src/state/state.py
production/config.py            config.py
regime_dual_engine/**           src/agents/setup_agent.py
```
證據：測試後 `git status --porcelain -uno` **空輸出**；`test_phase2_isolation_not_wired` 斷言 6 個受保護檔案不含 `production.exits` / `production.stops` / `production.contracts`。

## D. Contract design

**`ExitDecision`**：`should_exit` · `ticker` · `direction` · `session_date` · `exit_reason_code` · `exit_price` · `fill_model` · `stop_reference_price` · `target_reference_price` · `detail` · `bar` · `provenance`。

**精確成交價規則（無「合理範圍」判定 — 依 validation correction）**

| fill_model | 契約強制 |
|---|---|
| `GAP` | `exit_price == bar.open` |
| `STOP` / `TRAILING` | `exit_price == stop_reference_price` |
| `TARGET` | `exit_price == target_reference_price` |
| `CLOSE` | `exit_price == bar.close` |

外加 reason↔fill 一致性（STOP_LOSS∈{GAP,STOP}、TAKE_PROFIT=TARGET、TRAILING_STOP∈{GAP,TRAILING}、TIME_STOP/SIGNAL_EXIT=CLOSE）與 `should_exit=False` 不得攜帶執行欄位。

**`ExitContext`**（確定性完整輸入）：`position` · `stop_plan` · `bar` · `target_reference_price` · `tech_signal`（外部注入）· `session_date` · `trailing_armed`（Stop Engine 產出）· `config(max_holding_days)`。

**單一 stop owner**：`context.py` 是唯一與 Stop Engine 接觸處，且只用其 **arming gate**（候選 stop 值直接丟棄）；規則讀 `StopPlan.initial_stop_price`（STOP_LOSS）與 `StopPlan.current_stop_price`（TRAILING_STOP）。`test_no_trailing_math_in_exit_engine` 斷言 `rules.py`/`engine.py` 內無任何 ATR/trailing 運算。

## E. Legacy parity results

| 層級 | 方法 | 結果 |
|---|---|---|
| 17 必要 synthetic cases | 四欄位比對（should_exit · reason · price · fill_model）vs `_exit_check`，**具區辨力 config**（`stop_atr_mult=1.5` vs `trailing_atr_mult=0.8`） | **17/17 全等**（容差 1e-9） |
| 真實 production config（1.5/1.5/1.0/30） | 同 17 cases 重跑 | **全等** |
| 優先序 | 同日 stop+target → `STOP_LOSS` 勝 | ✅ |
| 邊界 | `open==stop`（GAP 勝）、`low==stop`、`high==target`、trailing 恰好 armed、trailing 差 1e-6 未 armed | ✅ |

覆蓋：STOP_LOSS(open/gap)、STOP_LOSS(intraday)、TAKE_PROFIT、TRAILING_STOP(gap)、TRAILING_STOP(intraday)、TIME_STOP、SIGNAL_EXIT、no-exit、stop+target 同日、open==stop、low==stop、high==target、恰好 armed、略低未 armed、held=0、held=max-1、held=max。

## F. Replay results（真實 bar/trade）

來源：`reports/production_bt_2024-01-01_2025-07-31.json`（151 筆真實交易，含 stop/target/atr/entry/exit）。

| 指標 | 值 |
|---|---|
| Trades replayed | **151**（skipped 0） |
| Bars compared | **1073** |
| **Divergences** | **0** |
| Exit events — legacy | TAKE_PROFIT 60 · STOP_LOSS 75 · TIME_STOP 6 · TRAILING_STOP 10 |
| Exit events — new | **完全相同**（60 / 75 / 6 / 10） |
| First-exit 對帳（replay vs 交易紀錄） | **151/151** |
| 跳過的壞 OHLC bar | 0 |

> 註：此樣本中 SIGNAL_EXIT 為 0 次（無 bearish 訊號先觸發）；該路徑由 synthetic parity case #7 覆蓋。

## G. Test results（全專案 100/100）

| 套件 | 結果 |
|---|---|
| `test_exit_engine.py`（新） | **31/31** |
| `test_stops.py`（Phase 1） | 19/19 |
| `test_contracts.py`（Phase 0） | 18/18 |
| `test_datasource.py` / `test_pipeline.py` / `test_backtest.py` / `test_production.py`（既有） | 8/8 · 9/9 · 5/5 · 10/10 |
| **合計** | **100/100**（含既有零回歸） |

## H. Divergences

1. **引擎層：0 divergence**（1073 bars、四欄位全等）。
2. **發現並修正一個 harness 保真度問題（非引擎問題）**：初版 replay 在評估當日 exits 前就用「當日收盤」更新 anchor，導致 trailing 提早一日觸發（DVA/FTNT/NFLX 三筆與交易紀錄不一致）。真因：`production/pipeline.py` 是**先評估 exits、之後才 mark-to-market**，故 D 日所見 anchor 應為 D-1 收盤。修正順序後 → 對帳 148/151 → **151/151**、divergence 仍為 0。
3. **文件化的輸入收緊（非行為差異）**：legacy 在 `bar` 缺 `close` 時會回退用 entry price；`PriceBar` 契約要求合法 OHLC（fail loud）。另 legacy 對日期解析失敗會靜默 `held=0`，新契約在輸入層驗證日期。

## I. Control Center updates

- modules **116 → 124**（+8：1 契約、4 exits、3 測試/fixtures）
- 新模組標記：`type=core|test`、`status=ACTIVE`、`canonical=1`（源碼）/`0`（測試）、**`production_used=0`**、`subsystem=Exit Engine | Contracts`、`confidence=HIGH`
- dependencies 重算（**807** edges，含 exits→stops/contracts）
- migrations **5 → 6**：`Exit Engine — IMPLEMENTED — NOT WIRED (parity verified, 0 divergences)`，blocker: human review
- decisions **8 → 10**：新增「Exit Engine 未接線（HIGH）」「legacy 兩個 stop level 的映射需確認（MEDIUM）」；原有人類決策（兩 copy RESOLVED）**未改動**
- ARCHITECTURE.md / .yaml 已重生

## J. Production impact

**無。** 未改任何 production 路徑、未改 config/thresholds、未刪任何檔案、未遷移 legacy caller、未接線、未 commit（依 AGENT_WORKFLOW 由人類 commit）。Phase 2 期間唯一副作用：`test_production.py` 的 e2e 測試會覆寫 `reports/` 兩檔，已 `git checkout -- reports/` 還原。

## K. NEEDS HUMAN REVIEW

1. **ExitDecision 欄位集**（含 `stop_reference_price` / `target_reference_price`）是否定案。
2. **精確成交價規則**是否符合你對「legacy 即 oracle」的要求（現為等式強制，無範圍判定）。
3. **兩個 stop level 的映射**（STOP_LOSS→`initial_stop_price`；TRAILING_STOP→`current_stop_price`）— 這是 Phase 2 唯一需要語義確認的設計決定（詳見 DESIGN_NOTE §6）。
4. **輸入收緊**（不允許缺 close 的 bar、日期必須合法）是否接受。
5. **下一步**：是否批准 Stop + Exit 合併接線計畫（仍屬新 freeze 面），或先做 Exit 參數研究前的其他準備。

---

**Phase 2 success criteria 對照**：① 五種 legacy 出場全重現 ✅ ② 成交價語義相符 ✅ ③ fill model 相符 ✅ ④ 優先序相符 ✅ ⑤ trailing 消費 StopPlan ✅ ⑥ Exit Engine 無獨立 trailing 計算 ✅（有守衛測試）⑦ 無 production 接線 ✅ ⑧ 隔離測試通過 ✅ ⑨ 單元測試通過（31/31）✅ ⑩ replay 無未解釋 divergence（0）✅ ⑪ registry 已更新 ✅ ⑫ 驗證報告已產出 ✅

**Phase 2 完成，停止。** 未進入 Risk / Entry / Portfolio Engine，亦未做任何 production cut-over。
