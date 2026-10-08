# Phase 3 Review Sheet — Stop + Exit Shadow Wiring（請人類簽核）

> 用途：3 分鐘內驗證 Phase 3 是否符合「legacy 仍為唯一決策來源、shadow 零側作用、可一鍵回退、未動受保護邏輯」。
> 簽核後才進入 Phase 4（研究優先序計畫見 `reports/phase4_research_prioritisation_plan_2026-09-30.md`）。

---

## 0. 三十秒自我驗證（可自行重跑）

```bash
cd "<OneDrive>/WB-prog"
PY=/Users/curryzeng/.workbuddy/binaries/python/envs/wbprog/bin/python

# 1) 全套測試（期待 117/117）
for t in test_datasource test_pipeline test_backtest test_production \
         test_contracts test_stops test_exit_engine test_wiring_safety; do
  printf "%-22s " "$t"; $PY production/tests/$t.py 2>&1 | tail -1
done
git checkout -- reports/            # 還原 test_production.py 的 e2e 副作用

# 2) replay parity（期待兩條路徑皆 0 divergence、RESULT: PASS）
$PY production/tests/replay_exit_parity.py

# 3) shadow 覆蓋 + gate（期待 1088 agree / 0 diverged；gate = PENDING）
$PY production/reporting/shadow_monitor.py \
    --coverage reports/phase3_shadow_coverage_2026-09-30.json \
    --historical-replay-ok --unit-tests-ok

# 4) 受保護檔案零修改（只應看到 3 個受權檔案）
git status --porcelain -uno
#   期待：M production/backtest.py / production/config.py / production/pipeline.py

# 5) registry 語義
sqlite3 control_center/project_control.db \
  "SELECT production_used, production_decision_authority, COUNT(*) FROM modules
   WHERE path LIKE 'production/%' GROUP BY 1,2;"
sqlite3 control_center/project_control.db \
  "SELECT key || ' = ' || value FROM meta WHERE key='production_exit_engine_mode';"
```

---

## 1. 待簽核項目（6 項）

### ☐ 1.1 凍結面（freeze face）
* 看什麼：`production/STOP_EXIT_V1_FREEZE.md`、`production/exits/adapter.py`、`production/exits/shadow.py`。
* 凍結內容：contracts 清單、stop 語義（ATR initial/trailing、trigger、no-loosening、LONG/SHORT 鏡像）、exit 語義（五規則優先序、精確成交價等式、TIME_STOP 用日曆日、注入 tech_signal、Exit Engine 零重算）、production 邊界、persistence 邊界。
* **請確認這三檔可作為後續接線與研究的凍結面。**

### ☐ 1.2 `exit_engine_mode` 語義
* `legacy`（**default**）→ 完全不導入新引擎（子程序實測 `sys.modules` 無 `production.exits*`／`production.stops*`）。
* `shadow` → legacy 仍唯一決策；新引擎只被評估、比對、記錄在 `DecisionRecord.exits.shadow`。
* `new` → `ExitDecision` 驅動動作（**未啟用**）。
* 缺失／`None`／非字串／未知值 → 一律安全回退 `legacy` 並記 `CONFIG_WARNING`。case/空白容忍（`" SHADOW "` → `shadow`）。

### ☐ 1.3 shadow 記錄 schema 與 divergence 詞彙
* 位置：`exits.shadow[]`（每持倉每日一筆）+ `exits.shadow_summary`。**不寫入** `warnings` / `pipeline_status` / `proposed` / state / cash。
* 每筆含：`legacy{reason,exit_price,fill_model}`、`new{should_exit,reason,exit_price,fill_model,stop_reference_price,target_reference_price,detail}`、`agree`、`divergence_types[]`、`stop_plan{initial,current,armed,reason_code,changed,previous_stop,detail}`、`error`／`error_type`。
* 8 類 divergence：`SHOULD_EXIT_MISMATCH`、`REASON_MISMATCH`、`PRICE_MISMATCH`、`FILL_MODEL_MISMATCH`、`STOP_REFERENCE_MISMATCH`、`TARGET_REFERENCE_MISMATCH`、`INPUT_VALIDATION_MISMATCH`、`ENGINE_EXCEPTION`。**一律不自動判定為 harmless。**

### ☐ 1.4 `new` 模式的失敗政策（請確認）
* 目前：`new` 模式下引擎失敗 → **不出場** + `NEW_ENGINE_FAILURE` 警告 + `pipeline_status=PARTIAL`（fail-loud，不靜默回退 legacy）。
* 替代方案：失敗時回退 legacy（較不刺眼，但會讓「new 其實沒生效」難以察覺）。
* **我的建議：維持 fail-loud**，因出場失敗屬安全相關，寧可吵。

### ☐ 1.5 ⚠️ live 20-session gate 缺少可執行路徑（**需要你做決定**）
* 現況：`production/main.py` 建立 `ProductionConfig()`，**沒有**任何 CLI 參數或環境變數可指定模式 → 目前**無法以 `shadow` 模式跑每日簡報**，因此 §10 的 live gate 無法開始累積。
* 影響：gate 永遠停在 PENDING（現有證據只有 production-equivalent backtest：396 sessions / 280 場次有實際持倉評估 / 0 divergence）。
* 可選做法（**我尚未實作，等你拍板**）：
  (a) `production/main.py` 加一個 `--exit-engine-mode {legacy,shadow,new}` 參數（最小、顯式，2–3 行）；
  (b) 讀環境變數 `WB_EXIT_ENGINE_MODE`（改動更小但較隱蔽）；
  (c) 不改 CLI，另外寫一個 operator script 注入 cfg（不碰 main.py，但與正式入口分岔）。
* 我的建議：**(a)** — 顯式、可審計、與 `--cached` 等既有旗標同風格。

### ☐ 1.6 registry 語義（你的第 10 點落實方式）
* `production_used` = 「production pipeline 有 import 它」。Phase 3 之後 stops/exits/contracts **確實**被 import → 誠實為 **1**（我移除了 Phase 1/2 留下的 `production_used=0` 覆寫）。
* `production_decision_authority` = 「它是否決定 production 動作」：**PRIMARY 19**／**SHADOW_ONLY 20**／**NONE 90**。
* `meta.production_exit_engine_mode = legacy` 記錄現行模式；migrations 顯示 Stop/Exit 皆 `SHADOW WIRED`。

---

## 2. 我承諾沒做的事（可查證）

| 承諾 | 證據 |
|---|---|
| 未修改受保護檔案 | 上節 (4) 指令；`portfolio`/`risk`/`state`/legacy risk+portfolio/setup/regime/config.py/main.py/ledger/datasource/screener 全 CLEAN |
| 未把 legacy `_exit_check` 改成 delegate | `portfolio_manager.py` 未變；parity 直接呼叫它 |
| Exit Engine 未自行算 trailing | `test_no_trailing_math_in_exit_engine` + test 9（adapter/shadow 皆無 ATR 運算） |
| shadow 無交易側作用 | shadow vs legacy 兩次完整回測：trade_log/equity_curve/skipped/regime_log/screens/n_decisions/summary 全 equal |
| 未持久化 `current_stop_price` | `src/state/state.py` 無該字串；position dict 無該欄位 |
| 未刪任何檔、未 commit／push | `git status`；依 AGENT_WORKFLOW 由你 commit |
| 未啟用 `new`、未開始 Risk/Entry/Portfolio Engine | `meta.production_exit_engine_mode = legacy` |
| 未優化任何 stop/exit 參數 | config 凍結參數由 test 1 斷言不變 |

## 3. 需知悉的既有測試改動（你已批准，如實列出）

| 測試 | 處置 |
|---|---|
| `test_exit_engine.py::test_phase2_isolation_not_wired` | 改寫為 `test_phase3_wiring_safety_import_graph`（保護檔案仍乾淨；**只允許 pipeline 經 adapter/shadow 觸及**；pipeline 不得 module-level import 新引擎；不得 import `exits.rules`） |
| `test_stops.py::test_phase1_isolation_not_wired` | 改寫為 `test_phase3_wiring_safety_stop_imports`（同理；adapter 為唯一 production 進入點） |

其餘 100 項既有測試**零修改**。
