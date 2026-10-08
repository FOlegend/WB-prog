# Phase 0-1 驗證報告 — Contracts + Stop Engine（2026-09-28）

> **範圍**：Phase 0（data contracts）+ Phase 1（isolated Stop Engine + tests）
> **權威 working copy**：`WB-prog`（連字號）— 依人類決策 item 1（`WB prog` 已退役）
> **核心成功標準**：清楚的 Stop Engine contract + isolated implementation + tests，**且沒有改變 production behaviour**
> **狀態**：完成，**停止並等待 human review**（未進入 Risk / Entry / Portfolio / Exit Engine）

---

## A. Files created（18 個新檔）

### `production/contracts/`（8 檔，Phase 0 cross-engine 共用模型）
| 檔案 | 內容 |
|---|---|
| `__init__.py` | 套件 re-export（5 個模型 + primitives） |
| `base.py` | `ContractError` / `Provenance` / `ExecutionWindow` / 驗證器 / deterministic id |
| `reason_codes.py` | 封閉式 reason-code 詞彙（stop / risk / order / execution / reference price sources） |
| `entry.py` | `EntryDecision`（signal 語義；**刻意不含 shares**） |
| `stop.py` | `StopPlan`（initial vs current；`structure_level` 保留） |
| `risk.py` | `RiskDecision`（三值狀態；契約 only） |
| `order.py` | `OrderIntent`（signal / execution / risk 分離） |
| `position.py` | `PositionState`（含 `current_stop_price` 概念，不持久化） |

### `production/stops/`（5 檔，Phase 1 Stop Engine）
| 檔案 | 內容 |
|---|---|
| `__init__.py` | 對外介面（+ `StopPlan` re-export） |
| `initial.py` | ATR initial stop（重現 legacy 公式） |
| `trailing.py` | ATR trailing stop + trigger gate（重現 legacy 公式；SHORT 為鏡像） |
| `engine.py` | `initial_stop_plan` / `update_stop`（不可放鬆）/ `plan_from_position` / `StopUpdate.explain()` |
| `errors.py` | `StopEngineError(reason_code, detail)` |

### 測試與文件（5 檔）
| 檔案 | 內容 |
|---|---|
| `production/tests/test_contracts.py` | 18 個契約驗證測試 |
| `production/tests/test_stops.py` | 19 個 Stop Engine 測試（含 legacy parity + 隔離守衛） |
| `production/stops/DESIGN_NOTE.md` | 設計說明（契約 / 公式 / 單位語義 / parity / 未做事項） |
| `production/stops/STOP_ENGINE_BACKLOG.md` | B1–B7 backlog（structure stop、持久化、wiring…） |
| `reports/phase0_1_stop_engine_2026-09-28.md` | 本報告 |

---

## B. Files modified（**只有 Control Center 工具層**，無 trading code）

| 檔案 | 變更 |
|---|---|
| `control_center/classify.py` | `LAYER_RULES` +2（`contracts/`→CONTRACTS、`stops/`→STOP）；`SUBSYSTEM_BY_LAYER` +2；`SUBSYSTEM_CANONICAL` +2；`CURATED` +15 條目；`MIGRATIONS` +1；`DECISIONS` +3（並把兩 copy 議題標為 RESOLVED）；分類迴圈新增 **CURATED `production_used` override**（讓隔離模組不被 `production/` 前綴誤標為 production_used=1） |
| `control_center/project_control.db` | 由 refresh 重建（modules 101→116、deps 720、migrations 4→5、decisions 5→8）＋ 寫入 human_decision |
| `control_center/ARCHITECTURE.md` / `.yaml` | 由 refresh 重生（新子系統、新 migration、新決策） |

---

## C. Files explicitly NOT modified（Phase 1 hard rule）

```
src/agents/risk_manager.py              （initial stop 來源，未改、未 delegate）
src/portfolio/portfolio_manager.py      （_exit_check trailing 來源，未改、未 delegate）
src/state/state.py                      （position schema 未動）
production/risk/risk.py                 （canonical risk wrapper 未動）
production/portfolio/portfolio.py       （canonical portfolio/exit wrapper 未動）
production/pipeline.py                  （canonical 決策管線未動）
production/config.py                    （未新增/修改任何參數）
production/main.py / datasource.py / screener / backtest.py / ledger.py
regime_dual_engine/**                   （Regime v1 freeze 未動）
src/agents/setup_agent.py               （Setup v1 freeze 未動）
config.py                               （legacy config 未動）
```

證據：測試前的 `git status --porcelain` 顯示受保護路徑**零修改**（只有我的 15 個新檔為 untracked）；`test_phase1_isolation_not_wired` 額外斷言 6 個受保護檔案不含 `production.stops` / `production.contracts` 的 import。

> ⚠️ 過程中 `test_production.py` 的 e2e 測試會覆寫 `reports/briefing_2025-07-31.md` 與 `reports/decision_2025-07-31.json`（既有行為，非本次改動）。已用 `git checkout -- reports/` 還原至 HEAD。

---

## D. Stop formulas implemented

```
# initial (per share, USD)
LONG  : initial_stop = reference_price - ATR × atr_multiple
SHORT : initial_stop = reference_price + ATR × atr_multiple      (mirror)
        risk_per_share (1R) = |reference_price - initial_stop|

# trailing candidate
r_dist      = atr_at_entry × stop_atr_multiple        # 注意：用 stop multiple，非 trailing multiple
armed       = anchor >= entry_fill + trigger_r × r_dist         (LONG)
              anchor <= entry_fill - trigger_r × r_dist         (SHORT)
candidate   = anchor - trailing_atr_multiple × atr_at_entry     (LONG)
              anchor + trailing_atr_multiple × atr_at_entry     (SHORT)
anchor      = highest_price_since_entry (LONG) / lowest_price_since_entry (SHORT)

# no-loosening（硬規則）
LONG  : updated_stop >= previous_stop     SHORT : updated_stop <= previous_stop
（候選若放鬆 → 拒絕，回 STOP_UNCHANGED，絕不靜默套用）
```

Reason codes：`INITIAL_ATR` / `NEW_HIGH`（anchor 創新高造成）/ `TRAILING_ATR`（無新極值但改善）/ `STOP_UNCHANGED` / `TRAILING_NOT_ARMED`；錯誤：`ATR_UNAVAILABLE` / `INVALID_ATR` / `INVALID_MULTIPLE` / `INVALID_PRICE` / `INVALID_DIRECTION`；`STRUCTURE_*` 保留未實作。`StopUpdate.explain()` 一句回答「stop 為何由 X 變 Y」。

---

## E. Legacy parity results（**NEW == LEGACY**，容差 1e-9）

| 層 | Legacy（未改） | 測試 | 比對值 | 案例 | 結果 |
|---|---|---|---|---|---|
| initial stop | `src/agents/risk_manager.py::size_position` | `test_legacy_parity_initial_stop` | `stop_price`、`stop_distance`(1R) | (100,2) (250.75,4.13) (37.5,1.05) (412.9,9.87) | ✅ 全等 |
| trailing stop | `src/portfolio/portfolio_manager.py::_exit_check` | `test_legacy_parity_trailing` | `TRAILING` fill 的 `exit_price` | (100,2,110) (100,2,104) (250.75,4.13,300) (50,3.7,62) | ✅ 全等 |
| not-armed gate | 同上 | 同上（第二段） | legacy 回 `None` ↔ 我們 `armed=False`、`TRAILING_NOT_ARMED` | anchor 低於 trigger 1e-6 | ✅ 一致 |
| end-to-end | 兩者 | `test_legacy_parity_engine_end_to_end` | legacy initial stop ↔ `StopPlan`；legacy trailing fill ↔ `updated_stop` | 正常 case | ✅ 全等 |

**強化設計**：parity 測試刻意使用 `stop_atr_mult=1.5` 但 `trailing_atr_mult=0.8` 的 duck-typed config — 若把「trigger 用的 multiple」與「trail 距離用的 multiple」搞混，測試會失敗（legacy 的 trigger 用 stop multiple，這是容易誤實作的點）。

---

## F. Unit test results（全部通過）

| 套件 | 結果 |
|---|---|
| `production/tests/test_contracts.py` | **18/18 PASS** |
| `production/tests/test_stops.py` | **19/19 PASS** |
| `test_datasource.py`（既有） | **8/8 PASS** |
| `test_pipeline.py`（既有） | **9/9 PASS** |
| `test_backtest.py`（既有） | **5/5 PASS** |
| `test_production.py`（既有） | **10/10 PASS** |
| **合計** | **69/69 PASS**（新增 37、既有 32 零回歸） |

要求清單對照：①ATR initial LONG ②ATR initial SHORT ③trailing LONG ④trailing SHORT ⑤trigger threshold（含 exact / 略低於）⑥LONG 不可放鬆 ⑦SHORT 不可放鬆 ⑧invalid price ⑨invalid ATR ⑩invalid multiple ⑪invalid direction ⑫determinism（3 次結果全等）⑬legacy parity — **13/13 全數覆蓋**。

---

## G. LONG / SHORT results

| 項目 | LONG | SHORT |
|---|---|---|
| Contract 驗證 | ✅ stop < reference、current ≥ initial | ✅ stop > reference、current ≤ initial（鏡像） |
| Initial stop | `ref − ATR×mult` | `ref + ATR×mult` |
| Trailing anchor | `highest_price_since_entry` | `lowest_price_since_entry` |
| Trailing candidate | `anchor − trail_mult×ATR` | `anchor + trail_mult×ATR` |
| No-loosening | `updated ≥ previous` | `updated ≤ previous` |
| Legacy parity | ✅（有 legacy 對照） | ⚠️ **無 legacy 對照**（baseline 只有 LONG）→ 以對稱不變式 + 鏡像測試驗證 |
| Production | 仍為唯一方向 | **未啟用**（未改 entry logic / pipeline / 未開啟 short trading） |

---

## H. Control Center changes

| 項目 | 之前 | 之後 |
|---|---|---|
| modules | 101 | **116**（+13 production 檔、+2 tests） |
| 新模組標記 | — | `type=core`(stops/contracts) / `test`、`status=ACTIVE`、`canonical=1`、**`production_used=0`**、`subsystem=Stop Engine/Contracts`、`confidence=HIGH`、`needs_review=0` |
| dependencies | — | 720 edges（refresh 重算；stops ↔ contracts 邊已入表） |
| migrations | 4 | **5**（新增 `Stop Engine — IMPLEMENTED — NOT WIRED (parity verified)`，blocker: human review） |
| decisions | 5（1 HIGH 為兩 copy） | **8**（兩 copy → **RESOLVED**；+3 新 OPEN：wiring / structure stop / current_stop 持久化） |
| human decisions | 空 | 已寫入兩 copy 決策的 `human_decision`/`human_note`（refresh 會保留） |
| ARCHITECTURE.md/.yaml | 09-24 版 | 2026-09-28T23:02 重生（新增 Stop Engine / Contracts 子系統、新 migration、新決策；production pipeline 10 階段不變） |
| classify.py 邏輯修正 | `production/` 前綴一律 production_used=1 | 允許 CURATED 顯式 override（避免把隔離模組誤標為 production 路徑） |

---

## I. Remaining architecture questions

1. **契約是否足夠**給 Entry/Risk phase 使用？（欄位、單位、語義）
2. **`reference_price_source`（FILL/EXPECTED/SIGNAL）** 是否為區分「預期 vs 實際成交」的正確機制？
3. **reason-code 詞彙**是否表達力足夠且無重複？
4. **B1 structure stop** — 需要一個獨立的 architecture decision 定義規則（不自行發明 prior swing low / 20D pivot）。
5. **B2 `current_stop_price` 持久化** — Position / Ledger / State redesign 決策。
6. **B3 wiring** — Stop Engine freeze face + Phase 2 接線計畫。
7. **B4 legacy 低層模組**（Control Center 仍列 HIGH/OPEN）— 接線後要保留為 shared layer 還是遷移？
8. **B5 SHORT 啟用**、**B6 `buffer` 語義**、**B7 Exit Engine 分離**（建議下一階段）。

---

## J. NEEDS HUMAN REVIEW

| # | 項目 | 為何需要人 | 影響 |
|---|---|---|---|
| 1 | **契約定案**（5 個模型的欄位/單位/語義） | 決策屬人類（AGENT_WORKFLOW：architecture decisions are made by the human） | 一旦定案即成為後續 Entry/Risk 依賴面 |
| 2 | **reason-code 詞彙**（含保留的 `STRUCTURE_*`） | 語義定義 | 後續 log/ledger 的可讀性 |
| 3 | **Stop Engine freeze face** | wiring 前提 | 決定 Phase 2 能否動 production |
| 4 | **Structure stop 未定義** | 無 canonical 實作 | 阻擋任何 structure-based 研究 |
| 5 | **`current_stop_price` 未持久化** | 涉及 state schema | 影響每日 ledger 可解釋性 |
| 6 | **SHORT 僅鏡像、無 legacy 對照** | 需決定是否啟用 | 目前 production 仍 LONG-only |
| 7 | 兩個仍 OPEN 的舊決策（legacy main.py 未 cut over；legacy src/ 仍被 production import） | 屬既有 backlog | 與本次無關但仍在 registry |

---

## K. Recommended next phase

**Phase 2 建議：Exit Engine 契約化（仍不動 production）**

1. 先由人類 sign-off 本階段 contracts + reason codes（可修訂後凍結）。
2. 定義 **Exit Engine contract**（`ExitDecision`：reason、exit_price、fill_model、gap-aware 語義），並以 **parity 方式**重現 `_exit_check` 的 5 種出場（STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / TIME_STOP / SIGNAL_EXIT）— 與本次相同紀律：新引擎先證明等價，不改 legacy。
3. 讓 Exit Engine **消費 `StopPlan` + `PositionState`**，正式完成「Stop 只管 level；Exit 才決定是否出場」的分離。
4. 之後才考慮任何 wiring / freeze cut-over（需 production-equivalent backtest 單變量比較）。

**不建議**在本階段之後直接跳到 Risk 或 Entry Engine 實作 — 先讓 Stop/Exit 這對分離乾淨，後續 sizing 與 entry 才有可量測的基準。

---

*Report generated 2026-09-28 · Stop Engine Phase 1 · no production behaviour changed.*
