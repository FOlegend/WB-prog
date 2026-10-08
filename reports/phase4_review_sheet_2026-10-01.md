# Phase 4 Review Sheet — D1–D6 Diagnostics（請人類簽核）

> 用途：3 分鐘內驗證 Phase 4 是否符合「唯讀診斷、未改任何參數、結論經得起獨立檢核」。
> 簽核後才進入 Phase 5（槓桿菜單與預先註冊實驗見 `reports/phase5_plan_2026-10-01.md`）。

---

## 0. 三十秒自我驗證（可自行重跑）

```bash
cd "<OneDrive>/WB-prog"
PY=/Users/curryzeng/.workbuddy/binaries/python/envs/wbprog/bin/python

# 1) 診斷（約 2 分鐘；會重跑既有 backtest 以捕捉 DecisionRecord）
$PY production/tests/phase4_diagnostics.py | tail -30

# 2) 獨立驗證（約 2 分鐘）— 期待 V1 一致率 99.7%、V4 顯示 gap 訊號不穩定
$PY production/tests/phase4_verify.py 2>&1 | tail -25

# 3) 測試無回歸（期待 118/118）
for t in test_datasource test_pipeline test_backtest test_production \
         test_contracts test_stops test_exit_engine test_wiring_safety; do
  printf "%-22s " "$t"; $PY production/tests/$t.py 2>&1 | tail -1
done
git checkout -- reports/            # 還原 test_production.py 的 e2e 副作用

# 4. 受保護檔案零修改（只應看到 4 個受權檔案）
git status --porcelain -uno
#   期待：production/{backtest,config,main,pipeline}.py
```

---

## 1. 待簽核項目（6 項）

### ☐ 1.1 診斷方法是否可接受
* 方式：**`production/backtest.py` 完全不改**；由 `production/tests/phase4_diagnostics.py` 在自進程內
  wrap `production.backtest.run_daily` 捕捉它平常丟棄的每日 DecisionRecord。
* **未改任何參數、未改任何 production 路徑、未重跑任何替代模擬、未建平行回測框架。**

### ☐ 1.2 核心結論
* 系統 **+7.99%** vs SPY **+36.26%**（缺口 −28.26pp）；但用系統**自身每日曝險權重**套 SPY 得 **+7.37%**
  → **決策品質僅差 +0.62pp**；**平均曝險只有 17.16%**。
* 該結論在**每個子期間都成立**（§V2：selection gap −1.69 ~ +1.93pp）。

### ☐ 1.3 歸因（權威版，已由真實風險引擎確認）
* 669 個有效 setup 只有 163 個能下單；506 次拒絕中 **505 次（99.8%）是風險引擎自己的判定
  「風險預算 $X 不足以買 1 股」**（被拒者平均預算 **$4.95**）。
* 實質風險只有 **0.33%**（目標 1%，上限 0.499%）：`1% × regime_mult(≤0.5) × quality_mult(≤1.0)` 再受整股取整。
* registry/報告已**更正**首輪報告中「413 次取整為 0 / 92 次市值上限」的自行推導標籤。

### ☐ 1.4 ⚠️ 一項結論經檢核後**被否證**（請確認接受此更正）
* 首輪報告稱「進場 gap 是唯一單調有效訊號」。**獨立驗證（§V4）顯示它不穩定**：
  2024 單調（<0% +0.366 → 1–2% −0.568），但 **2025 完全反轉**（1–2% 族群 avgR **+0.618**）；
  各格僅 8–20 筆。
* 處置：**候選 #2 由「第二優先」降級為「先取得穩定性再議」**，報告已據實修正。

### ☐ 1.5 V3 反事實表的正確讀法（避免誤用）
* `effective multiplier` 提高時「可下單 setup」數：164 → 265（×2 至 0.5）→ 401（regime mult 1.0）→ **485（eff 1.0, 72.5%）**。
* ⚠️ 這是**算術計數，不是績效估計**：只說「引擎會接受」，**沒有說這些交易會賺錢**。
  且 D3 顯示 BULL 進場 avgR −0.321 → 加碼的邊際族群可能不好。
* 即使 eff = 1.0，仍有 **184 個（27.5%）** 無法下單 → 帳戶規模（~$1,282）確有殘餘下限效應。

### ☐ 1.6 建議目標與可否證判準
* 建議 **#1 資本部署機制**：*缺口源於資本從未真正投入；下一步應把「實際投入的每筆風險」推向配置的 1%，
  而非改選股或出場。*
* 判準（**風險調整**，非原始報酬）：預算不足拒絕率顯著下降、實質風險趨近目標，**且 Sharpe/MaxDD 不得惡化**，
  並須在另一視窗（2018–2025）複驗後才可晉升。
* **建議的第一步不是改參數，而是 Step 0 價值量測**（見 §2）。

---

## 2. 建議的第一步（Step 0，唯讀、需你批准）

對 **505 個因預算不足而被拒的 setup**，用快取 bars 計算其**固定持有期後的前向結果**，並依拒絕原因分組：

* 若其期望值 **≥** 已被接受的族群 → 部署槓桿值得做單變量實驗；
* 若 **≤ 0** → 該 cap 是保護性的，目標應轉向別處。

**成本**：純讀取快取資料（不需重跑回測、不改任何檔、不改任何參數）。
**為何先做這個**：目前只證明了「機制」（曝險 17%、預算取整為 0、multiplier 從未 > 0.5）與
「缺口是曝險而非選股」，**尚未證明被拒的邊際族群值得吃下來** —— 而 D3 的 BULL 結果正警告它可能不值得。

---

## 3. 我承諾沒做的事（可查證）

| 承諾 | 證據 |
|---|---|
| 未改任何參數／策略／指標／setup | 4 個 tracked 變更全是已批准 wiring（`pipeline`/`config`/`main`/`backtest` 加法式聚合） |
| 未改受保護檔案 | `portfolio`/`risk`/`state`/legacy risk+portfolio/setup/regime 全 CLEAN |
| 未改 Stop/Exit 凍結面 | `STOP_EXIT_V1_FREEZE.md`、`adapter.py`、`shadow.py` 未動 |
| 未建平行回測框架 | 同一 `ProductionBacktest`，只加 in-process 捕捉 |
| 未刪任何檔、未 commit／push | `git status`；依 AGENT_WORKFLOW 由你 commit |
| 未開始 Risk / Entry / Portfolio Engine 實作 | 本輪只有診斷與文件 |
| 未啟用 `exit_engine_mode="new"` | production 仍 `legacy` |
| 測試 118/118 無回歸 | 上節 (3) 指令 |
