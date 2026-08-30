# Selection Layer Audit — Backtest vs Live Stock Selection
日期：2026-08-30（唯讀審計，未修改任何代碼）
範圍：`dynamic_universe_backtest.py` + `screen_as_of.py` + `historical_cache.py`（backtest 側）
     vs `production/screener/screener.py` + `production/main.py` + `production/config.py`（live 側）
未觸碰：Regime v1、Setup v1、Screener、Production V2、backtest 邏輯 —— 全部唯讀。

---

## 1. Dynamic Backtest Selection（運作方式）

| 項目 | 實作 | 檔案:行 |
| --- | --- | --- |
| Rebalance 日 | 基準（SPY）月曆的**每月最後交易日** | `build_rebalance_calendar` |
| Candidate bucket 建立 | 每個 rd 獨立執行 `screen_as_of(rd)`，只用 `datetime <= rd` 的 bar | `screen_as_of.py:111` |
| Bucket 生效 | `bisect_right(rb_ts, d) - 1` → **d 當天即生效（含 rd 當日）**；最早進場 = rd+1 open | `_build_active_bucket_map` |
| Top-20 | `sort_values("rs", ascending=False).head(20)` | `screen_as_of.py:172-174` |
| Look-ahead（價格） | **無**。所有指標在 `<= as_of` 切片上計算；signal 於 D close 產生、D+1 open 進場（gap≤2%/extension≤3% 過濾） | `dynamic_universe_backtest.py:349-420, 456-512` |
| Look-ahead（成員） | **有（已知、已披露）**：universe = 目前 S&P500 + NASDAQ 100，歷史月份也用它篩 | `historical_cache.py:71-76` |

### Bucket 生效驗證（無未來資訊）
- rd 當天（如 2024-01-31）：screen 用 `≤ 01-31` 的資料 → bucket 於同日生效 → 同日 close 產生 signal → 最早 02-01 open 進場。進場日之前的所有價格都在 screen 日之後，**時序閉合**。
- 持有倉位跨月續抱（TP/SL/trailing/time-stop），不受新 bucket 影響（`allowed_set` 只在進場時 re-validate）。
- 進場 re-validation：`t not in allowed_set → 放棄`，與 live 語義一致。

---

## 2. Historical Universe（與已審計的 PIT Breadth 分開）

| 面向 | 現況 |
| --- | --- |
| Backtest 股票 universe | **目前**成分（`get_universe()` 即時爬 Wikipedia 的 S&P500 + NDX100，cache 建立當下快照）→ **非 point-in-time membership** |
| 是否用現行成分篩歷史 | **是**。2024-01 的 bucket 用今天仍在指數中的股票（含 2025 年才加入者）；被剔除/併購/下市的標的缺席 → **survivorship bias**（已在 dynamic 報告與 historical_cache docstring 標註，freeze 契約接受） |
| 價格資料 | **PIT 正確**：`df[df["datetime"] <= as_of]`，嚴格切片 |
| Market cap | **故意跳過**（screen_as_of.py:16-19）：對歷史日期抓「現在」的市值是更糟的 bias，故以指數成員身份當 large-cap proxy |
| PIT Breadth universe（已另審計） | `regime_dual_engine/pit_constituents.py` 用 fja05680/sp500 快照 + Jaccard 0.9995 交叉驗證 → **真正的 point-in-time**。**這是另一套 universe，與本審計的 screener universe 無關**。 |

---

## 3. Live vs Backtest Screener — 語義逐項對照

| 項目 | Backtest（screen_as_of） | Live（screen → run_screener → main） | 判定 |
| --- | --- | --- | --- |
| Universe | 目前 S&P500+NDX100（cache 快照，~518） | 目前 S&P500+NDX100（每次執行時爬，~518） | BENIGN（live 一定是最新；backtest 是快照） |
| 價格 | `<= as_of` 切片（全歷史） | yfinance `period=4mo`（~84 bars，到最近收盤） | BENIGN（rolling 指標只看最後值，兩者 ≥ min_bars=55，結果一致） |
| 日期截止 | 明確 as_of（month-end close） | 執行當下的最近 completed bar（盤前 = 昨日 close） | BENIGN（live 本質上就是「現在」） |
| Filter 1 Market Cap | **跳過**（故意） | `> $10B`（current fast_info，並行抓取） | MATERIAL（backtest 少一道 filter；已披露、故意） |
| Filter 2 Dollar Vol | 20d rolling mean > $50M | 同 | MATCH |
| Filter 3 Price | close > $10 | 同 | MATCH |
| Filter 4 ATR% | 14d ATR / close > 2% | 同 | MATCH |
| Filter 5 RS | `_compute_rs_composite([50],[1.0])` > 1.0 | 同（同一函式） | MATCH |
| RS date-alignment | **真日期對齊**（DatetimeIndex intersection） | **位置對齊**（RangeIndex intersection，因 fetch_batch reset_index） | BENIGN（大型股交易日曆一致時數值相同；backtest 反而更嚴謹） |
| Filter 6 ADX | 14d > 20 | 同 | MATCH |
| Ranking | RS desc | RS desc（rs_sort_col） | MATCH |
| Top-N | **20**（run_comparison/CLI 預設） | **30**（cfg.screener_top_n） | **MATERIAL** |
| rs_rank 來源 | bucket 內 RS 排序位（確定性，v3.2.1 fix） | `list(set(cand + held))` — **set 破壞排序** → rs_rank 非確定 | BENIGN（frozen Pullback 不用 rs_rank；若啟用 breakout 會失真） |
| 篩選頻率/有效期間 | **每月**（bucket 生效 ~1 個月，月底前內容會 stale） | **每日**（每次執行重篩，最即時） | **MATERIAL** |
| 失敗 fallback | 無 | screen 空 → `UNIVERSE[:10]` 硬編碼前 10 檔 | BENIGN（防禦性；網路失敗才觸發） |

---

## 4. Selection Timing — 具體範例（用實際 bucket + 實際 trade 驗證）

```
2024-01-31（Wed，1 月最後交易日）
→ 當時可得的資料：518 檔（目前成分）OHLCV ≤ 2024-01-31（歷史 cache）
→ 篩選後 universe：6 filters 通過 → Top 20（RS50 desc）：
   SMCI CRWD AMD GM PANW MRNA COHR VRT SYF BLDR URI COIN TPR ARM ECHO RMD ASML VTRS FTNT COF
   （2024-01-31 bucket，reports/dynamic_buckets_2024-01-01_2025-07-31_top20.json 實存）
→ bucket 有效期間：2024-01-31（當日含）→ 2024-02-28（下一個 rd=2024-02-29 之前）
→ Setup candidate + 進場（audit_trade_log.json 實存，Current Weights 2024-01-31 bucket）：
   PANW：signal @ 01-31 close（或窗口內任一日 close）→ entry @ 2024-02-01 open $169.99 → TP 02-07 +$24.17
   SMCI：entry @ 2024-02-07 open $68.36 → TP 02-14 +$39.23
→ 無未來資訊驗證：
   • bucket 只用 ≤ 01-31 的 bar（screen_as_of 切片）
   • 01-31 是 2024-01 最後交易日 → bucket 當日生效 → 最早 02-01 進場，**screen 日之後才有交易**
   • signal 用 D close（≤ D），進場用 D+1 open —— 時序閉合，無洩漏
```
（註：該 trade log 為 graduation 世代，`signal_date`/`setup_*` 未持久化；selection 層機制與現行 dynamic backtest 相同。）

---

## 5. Backtest/Live Drift 全表

| # | 差異 | 分類 |
| --- | --- | --- |
| 1 | Top-N：backtest 20 vs live 30 | **MATERIAL DIFFERENCE** |
| 2 | Market Cap filter：backtest 跳過 vs live 套用 | **MATERIAL DIFFERENCE**（披露、故意） |
| 3 | 篩選頻率/有效期間：monthly vs daily | **MATERIAL DIFFERENCE** |
| 4 | Universe membership：backtest 用現行成分篩歷史（survivorship） | **LOOK-AHEAD RISK（membership 層；非價格層；已披露並接受）** |
| 5 | RS 對齊：真日期 vs 位置 | BENIGN DIFFERENCE |
| 6 | 資料窗口：全歷史 vs 4mo | BENIGN DIFFERENCE |
| 7 | rs_rank：確定性 RS 位 vs set() 亂序 | BENIGN（現行 Pullback 不受影響） |
| 8 | fallback universe（live 空檔） | BENIGN |
| 9 | 其餘 6 filters 門檻/計算/排序 | MATCH |
| 10 | 進場時序（D close signal → D+1 open，gap/extension 過濾） | MATCH |

---

## 6. 結論

### `SELECTION AUDIT PASS`（可進入紙交易）

**判定理由**：
- **Live 側無任何 look-ahead**：universe/資料/日期截止都是「現在」，語義正確，可直接紙交易。
- **Backtest 價格層嚴格 PIT**：所有指標在 `≤ as_of` 切片上計算，bucket 生效/進場時序閉合，無價格洩漏。
- Backtest 唯一的非 PIT 元素是 **membership survivorship**（現行成分篩歷史）——已在 dynamic 報告、graduation 報告、historical_cache docstring 三處披露，freeze 契約接受；**不影響 live 正確性**。
- 3 個 MATERIAL DIFFERENCE（top_n 20 vs 30、mcap skip、monthly vs daily）都是**已知簡化/保守方向**，且已文件化——不會讓 live 犯錯，只會讓 backtest 與 live 數字不能逐日對齊。

**Paper trading 前請留意（不修，僅記錄）**：
1. 紙交易應以 **live screener（top 30）** 為準；backtest 績效基於 top 20，不可直接外推成 live 期望值。
2. live `rs_rank` 經 `set()` 後非確定——現行 Pullback-only 不讀它（BENIGN）；**若日後啟用 breakout 的 rs_rank 計分，必須先修正**（屬 v2 backlog，勿現在動）。
3. live screener 空結果時 fallback 到 `UNIVERSE[:10]` 硬編碼清單——建議紙交易時以「screen 失敗 → 不出單」為人工規則。
