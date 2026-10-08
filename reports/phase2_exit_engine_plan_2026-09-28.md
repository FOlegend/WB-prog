# Phase 2 計畫 — Exit Engine 契約化（設計 only，**尚未實作**）

> **狀態**：PLANNED — 等待 Phase 0-1 人類簽核（見 `production/stops/PHASE1_REVIEW_SHEET.md`）
> **前提**：Phase 0-1 成功標準已達成（契約 + isolated Stop Engine + 測試，production behaviour 未變）
> **本文件不含任何程式碼**；未經簽核不得開始實作。

---

## 1. 為什麼是 Exit Engine（而不是先做 Risk / Entry）

Phase 1 已把 **stop 的 level** 從 legacy 中抽離出來。現在 `_exit_check` 仍同時做兩件事：

1. 決定 **是否出場**（STOP / TARGET / TRAILING / TIME / SIGNAL）
2. 決定 **用哪個價格成交**（gap-aware fill model）

只要這兩件事還糾纏在一起，任何「出場時機」的研究都無法被單獨量測。Phase 2 的目標就是把 (1) 契約化、(2) 的語義顯式化，讓「Stop 只管 level、Exit 才決定是否出場」正式落地。

**這不是 Exit Engine rewrite**：與 Phase 1 相同紀律 — 先證明新引擎與 `_exit_check` **逐值等價**，legacy 一行不改。

---

## 2. 目標契約（草案，待簽核）

`production/contracts/exit.py` → `ExitDecision`

| 欄位 | 型別 | 必填 | 語義 / 單位 |
|---|---|---|---|
| `should_exit` | bool | ✅ | 是否建議出場 |
| `ticker` | str | ✅ | — |
| `direction` | str | ✅ | LONG / SHORT |
| `session_date` | str | ✅ | naive `YYYY-MM-DD`（決策當日） |
| `exit_reason_code` | str | 條件 | 見 §3；`should_exit=False` 時為 `None` |
| `exit_price` | float? | 條件 | USD/share；`should_exit=True` 時必填 |
| `fill_model` | str? | 條件 | `STOP` / `GAP` / `TARGET` / `TRAILING` / `CLOSE`（沿用 legacy 詞彙） |
| `stop_reference_price` | float? | 選填 | 本次判斷所用的 current stop（來自 `StopPlan`） |
| `target_reference_price` | float? | 選填 | take profit |
| `bar` | dict? | 選填 | 判斷用的 OHLC（審計用；`open/high/low/close`） |
| `detail` | str | ✅ | 人類可讀理由（沿用 legacy 文字風格） |
| `provenance` | Provenance | ✅ | producer = `production/exits/engine.py` |

驗證規則（草案）：
* `should_exit=True` → `exit_reason_code`、`exit_price`、`fill_model` 必填；`exit_price > 0`。
* `fill_model=GAP` → `exit_price` 必須等於 `bar.open`；`fill_model=TARGET` → 等於 target；`STOP/TRAILING` → 介於 `bar.low` 與 `bar.open/close` 的合理範圍（依 legacy 語義）；`CLOSE` → 等於 `bar.close`。
* `should_exit=False` → 不得帶 `exit_price` / `fill_model` / `exit_reason_code`。
* `exit_price` 必須落在「該方向保護側」：LONG 出場價通常 ≤ 當前參考價（除 TARGET），SHORT 鏡像。
* SHORT 為鏡像（logical only；production 仍 LONG-only）。

---

## 3. Legacy parity matrix（Phase 2 的驗收核心）

現行 `src/portfolio/portfolio_manager.py::_exit_check`（**未改**）的 5 種出場與優先序：

| 優先 | 出場 | 觸發條件 | 成交價 / fill_model | 判斷基礎 |
|---|---|---|---|---|
| 1 | `STOP_LOSS` | `open ≤ stop` | `open` / `GAP` | 當日 OHLC |
| 1 | `STOP_LOSS` | `low ≤ stop` | `stop` / `STOP` | 當日 OHLC |
| 2 | `TAKE_PROFIT` | `high ≥ target` | `target` / `TARGET` | 當日 OHLC |
| 3 | `TRAILING_STOP` | 已 armed 且 `open ≤ trail` | `open` / `GAP` | 當日 OHLC＋anchor |
| 3 | `TRAILING_STOP` | 已 armed 且 `low ≤ trail` | `trail` / `TRAILING` | 當日 OHLC＋anchor |
| 4 | `TIME_STOP` | `held_days ≥ cfg.max_holding_days` | `close` / `CLOSE` | 日曆日數 |
| 5 | `SIGNAL_EXIT` | `tech_signal == "bearish"` | `close` / `CLOSE` | 收盤訊號 |

**必須逐項重現的行為細節**（容易出錯，Phase 1 已吃過一次同樣的教訓）：
* 優先序：**stop 先於 target**（同日兩者都觸發 → 取 stop，保守）。
* `trailing` 只在 **armed** 時評估（armed 條件來自 Phase 1 的 `atr_trailing_stop`，含「trigger 用 `stop_atr_mult`」這個細節）。
* `TIME_STOP` 的 held days 用 **日曆日**（`datetime` 相減 `.days`），不是交易日。
* `TRAILING` 的 trail 值必須與 Phase 1 `StopPlan.current_stop_price` 一致（**Phase 2 必須直接消費 StopPlan，不得另算一份**）。
* `tech_signal` 由呼叫端注入（production 目前用 `technicals_signal`；引擎不得自行呼叫）。

Parity 測試設計（與 Phase 1 同風格）：
* 對每個 legacy 出場原因，構造 deterministic 的 bar/position，**同時**呼叫 legacy `_exit_check` 與新引擎，斷言 `exit_reason_code` / `exit_price` / `fill_model` 全等（容差 1e-9）。
* 邊界：`low == stop`（`≤` 觸發）、`open == stop`（GAP 優先）、`high == target`、armed 恰好成立 / 差 1e-6 未成立、同日 stop+target → stop。
* `TIME_STOP` 邊界：`held == 0`、`held == max-1`、`held == max`。
* 無出場 → `should_exit=False` 且不帶價格欄位。
* 隔離守衛：`test_phase2_isolation_not_wired`（production 路徑不得 import `production.exits` / `production.stops` / `production.contracts`）。

---

## 4. 建議檔案佈局（與 Phase 1 對稱）

```
production/contracts/exit.py          # ExitDecision 契約
production/exits/__init__.py          # 對外介面
production/exits/engine.py            # evaluate_exit(position, stop_plan, bar, cfg, tech_signal) -> ExitDecision
production/exits/rules.py             # 五種規則（純函式、無狀態）
production/tests/test_exit_engine.py  # parity + 邊界 + 隔離守衛（plain-script runner）
```

停止條件（Phase 2 exit criteria）：
1. 五種 legacy 出場原因 **逐值等價**（parity 全綠）。
2. 新引擎 **只消費** `StopPlan.current_stop_price`（不另算 trailing）。
3. production 路徑零修改（`git status --porcelain -uno` 空）。
4. 既有 32 測試零回歸、新增測試全綠。
5. registry 更新（新模組 `production_used=0`、migration 一筆、decisions 更新）。
6. 報告 A–K。

---

## 5. 需要你先決定的事（Phase 2 開工前）

1. **是否批准 Phase 2 以「parity first」方式進行**（新引擎先證明等價，legacy 不動）？
2. **reason code 命名**：沿用 legacy 字串（`STOP_LOSS` / `TAKE_PROFIT` / `TRAILING_STOP` / `TIME_STOP` / `SIGNAL_EXIT`）以利對帳，還是改為新詞彙（如 `EXIT_STOP_HIT`）並在 ledger 保留 legacy 別名？
3. **`ExitDecision` 是否要攜帶 `stop_reference_price` / `target_reference_price`**（我傾向要，方便審計「為何出場」）？
4. **`fill_model` 詞彙**是否維持 legacy 五值（`STOP/GAP/TARGET/TRAILING/CLOSE`）？
5. **SHORT 出場**：同樣只做契約層鏡像（不啟用），對嗎？

---

## 6. Phase 2 之後（僅供排程參考，勿提前執行）

Exit Engine 落地後，出場研究才有乾淨的實驗面（stop/target/trailing/time 參數的單變量比較）。
Risk Engine 與 Entry Engine 的契約已存在（Phase 0），但**應排在 Exit Engine 之後**：sizing 與 entry 的評估都依賴穩定的出場語義。
