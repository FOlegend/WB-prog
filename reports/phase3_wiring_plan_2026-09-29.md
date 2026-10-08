# Phase 3 計畫 — Stop + Exit 接線（**計畫 only，尚未實作**）

> **狀態**：PLANNED — 等待 Phase 2 人類簽核（`production/exits/PHASE2_REVIEW_SHEET.md`）
> **閘門**：本文件不含任何 production 程式碼變更；Phase 2 spec 明文禁止在未取得 freeze 決定前進行 production cut-over。
> **目標**：把 Stop Engine + Exit Engine 接進 production **而不改變任何交易行為**，並用可量測證據證明等價。

---

## 1. 為什麼要接線（以及為什麼要小心）

Phase 0-2 已交付：5 個契約 + 兩個 isolated 引擎，且以 parity 證明與 legacy 逐值等價。
但**production 仍跑 legacy 路徑**（`_exit_check` + `size_position`），所以：

* 新引擎目前無法產生任何可量測的價值；
* 任何「出場參數研究」都還沒有乾淨的實驗面。

接線的風險在於：這會**第一次**讓新程式碼進入 production 決策路徑。因此採用
**shadow-first（先影子、後切換）**，而非直接替換。

---

## 2. 提議做法：Shadow → 驗收 → 切換

### Step A — Shadow 模式（行為零改變）
在 `production/pipeline.py` 現有流程「之外」加一個觀測步驟（不改 legacy 決策）：

```
既有：legacy _exit_check(...) → 產生 SELL 建議        ← 唯一決策來源（不變）
新增：Stop Engine → StopPlan；Exit Engine → ExitDecision
      → 兩者一併寫進 DecisionRecord（決策欄位另存，不參與下單）
```

* DecisionRecord 新增唯讀區塊（例如 `shadow_exit`）：`legacy {reason, price, fill}`、
  `new {reason, price, fill}`、`agree: bool`、以及 StopPlan 的 `initial/current/armed`。
* **不產生任何新的 SELL/BUY**：下單建議仍 100% 由 legacy 路徑決定。
* 這一步需要一個小 adapter（把 production 的 position dict 轉成 `PositionState` +
  StopPlan），並在每日流程呼叫 `update_stop`（**必須無副作用**：只回傳、不持久化）。
* 需要新增的旗標（預設最保守）：`exit_engine_mode = "legacy" | "shadow" | "new"`，
  **預設 `legacy`**；shadow 由你決定何時開啟。

### Step B — 驗收（門檻先定好，避免事後移動龍門）
切換前必須同時滿足：

| # | 驗收項 | 門檻 |
|---|---|---|
| 1 | Production-equivalent backtest（2024-01~2025-07）以 legacy vs new 各跑一次 | **trade log 完全一致**（ticker/日期/reason/成交價） |
| 2 | Trade-level replay parity（現成 harness） | **0 divergences**（現已達成） |
| 3 | Shadow 期間實盤/簡報 | **連續 N 個交易日 0 divergence**（N 由你定，建議 20） |
| 4 | 單元測試 | 全綠（目前 100/100） |
| 5 | Registry | 兩個引擎的 `production_used` 依實際狀態更新（不再全部是 0） |

### Step C — 切換（單一變數）
把決策來源從 legacy 換成新引擎（`exit_engine_mode = "new"`），**legacy 保留為 shadow 對照**：

* 觀察期內若出現任何 divergence → 立即以旗標回退（不需 revert 程式碼）。
* legacy `_exit_check` 的**移除**屬另一個獨立決定（Control Center 既有 `[HIGH] OPEN`
  的 legacy 低層模組決策），本階段不刪任何檔案。

---

## 3. 建議凍結面（Freeze face）

切換前應凍結（與 Regime v1 / Setup v1 同規格的文件）：

| 凍結項 | 內容 |
|---|---|
| Contracts v0.1.0 | 6 個模型（EntryDecision / StopPlan / RiskDecision / OrderIntent / PositionState / ExitDecision）+ `PriceBar` |
| Reason codes | stop 詞彙 + legacy exit 詞彙 + risk/order 保留詞彙（封閉集合） |
| Stop 語義 | ATR initial 公式、trailing 公式與 trigger 用 `stop_atr_mult`、no-loosening、SHORT 鏡像 |
| Exit 語義 | 五規則、優先序、精確成交價等式、TIME_STOP 用日曆日、`tech_signal` 外部注入 |
| 兩個 stop level 映射 | `initial → STOP_LOSS`、`current → TRAILING_STOP`（Phase 2 §1.3 已待確認） |
| 邊界 | shadow 模式下 legacy 仍是唯一決策來源；引擎不得持久化 `current_stop_price` |

建議檔名：`production/STOP_EXIT_V1_FREEZE.md`（草案待你確認位置與名稱）。

---

## 4. 需要新增的程式（Phase 3 實作清單，屆時才寫）

| 檔案 | 用途 |
|---|---|
| `production/exits/adapter.py`（或 `production/engines_bridge.py`） | position dict → `PositionState` + StopPlan + ExitContext（純轉換，無策略） |
| `production/pipeline.py`（修改） | 讀 `exit_engine_mode`；shadow 區塊寫入 DecisionRecord；`new` 模式下以 ExitDecision 產生 SELL |
| `production/config.py`（新增 1 個旗標） | `exit_engine_mode`（預設 `"legacy"`） |
| `reports/wiring_acceptance_*.md` | Step B 的自動化驗收報告 |

> 以上檔案在 Phase 3 **未經批准前不會建立**。

---

## 5. 風險與緩解

| 風險 | 緩解 |
|---|---|
| 接線改變行為 | shadow-first；旗標預設 legacy；驗收門檻先定 |
| Stop Engine 每日被呼叫（新型態的副作用風險） | `update_stop` 保證無副作用（只回傳）；shadow 區塊不得寫 state |
| anchor 時序（Phase 2 曾在此踩坑） | shadow 與 legacy 共用同一 anchor 來源（`state.mark_to_market` 的既有順序），並在 DecisionRecord 記錄 anchor 值 |
| 兩個 stop level 語義誤解 | 已列 OPEN decision，須先簽核（Phase 2 §1.3） |
| 之後要調出場參數 | 屬**另一個階段**（見 §6），須在接線驗收完成後才開始 |

---

## 6. 接線完成後才可做的事（僅供排程，勿提前執行）

1. **出場參數研究**：以 production-equivalent backtest 做單變數比較
   （`stop_atr_mult`、`take_profit_atr_mult`、`trailing_atr_mult`、`trailing_trigger_r`、
   `max_holding_days`），一次一變數、記錄 ablation。
2. Risk Engine / Entry Engine 契約實作（順序應在出場研究之後，因 sizing 與 entry 評估
   都依賴穩定的出場語義）。
3. Position / Ledger / State redesign（`current_stop_price` 持久化）— 依賴 §2 Step C 穩定後。

---

## 7. 需要你先決定的事（Phase 3 開工前）

1. **是否批准 shadow-first 做法**（而非直接替換）？
2. **旗標名稱與預設值**：`exit_engine_mode = legacy | shadow | new`（預設 `legacy`）可以嗎？
3. **驗收門檻**：shadow 連續幾個交易日 0 divergence 才可切換（建議 20）？
4. **legacy 的未來**：切換後保留為永久 shadow 對照，或另案決定退役？
5. **freeze 文件**：是否建立 `production/STOP_EXIT_V1_FREEZE.md`（或你偏好的名稱／位置）？
