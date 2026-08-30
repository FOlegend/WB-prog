# Regime v2 — Research Backlog

**用途**：記錄所有「疑似需要修改 Regime v1」的策略改進想法。依 freeze 契約
（`REGIME_V1_FREEZE.md`），**這些項目只記錄、不實作**，等解除凍結後再評估。
每個項目附證據來源與影響方向，便於未來排序。

| # | 項目 | 觀察/證據 | 來源 | 影響方向 |
|---|------|-----------|------|----------|
| V2-1 | **HMM 崩盤後慢回場** | 2023 整年 avg bull_prob≈0.34 而 SPY +26.7%；2020/2022 復甦期同 pattern；「inverted days」232/2156 集中在復甦期 | `reports/dual_engine_state_alignment.json`、graduation 診斷 | 提升復甦期 re-engagement（可能：recovery detection、降低回場門檻） |
| V2-2 | **Bearish Breadth Divergence 假警報** | 2024-Q3 warning 領先 96 交易日、機會成本 +11.46%（真頂部 2018/2021 僅領先 19-30d） | `reports/dual_engine_divergence_review.json` | 加嚴重度/條件化（如：breadth 須低於某水位才 veto） |
| V2-3 | **Dist-Day 審計發現**（僅研究） | count≥5 佔 47.3% 交易日 → 5 是常態非極端；thr7 回測最佳（+9.92% vs 無 overlay +6.24%）但依 spec 不因 CAGR 選閾值 | `reports/dual_engine_distdays_audit.json`、`distdays_thresholds_pit.json` | 若要重引入 dist overlay：閾值須按「極端條件」統計定義（如 ≥8 = p90），非現行 ≥5 |
| V2-4 | **Breadth 閾值 70/30 略優** | 70/30: +7.67% vs 65/35: +6.24%（同一 universe）——平滑改善但未達 magic param | `reports/dual_engine_robustness_pit.json` | 潛在閾值重校（須先解除凍結） |
| V2-5 | **PIT OHLCV 覆蓋 83.4%** | 16.6% 歷史成員無免費資料（BK/ANSS/ATVI/CERN/FB 等已下市/更名） | `reports/dual_engine_constituents.json` | 換有 delisting 覆蓋的資料源（成本考量） |
| V2-6 | **Breadth universe 僅 S&P500** | PIT 無 NASDAQ-100 membership；current-constituent 含 NQ100（Pearson 0.996 差異小） | `reports/dual_engine_breadth_compare.json` | 若需 NQ100：找 PIT NQ100 資料源 |
| V2-7 | **composite 對 BULL 保守** | BULL 僅佔 ~21% 交易日（496/2408），BEAR 43%——regime 傾向保守 | `reports/dual_engine_regime_conditioning.json` | 與 V2-1 連動，評估曝險曲線 |

**規則**：
1. 新增想法 → 在此 append 一行（含證據路徑），**不實作**
2. 評估時機 → 解除 freeze、或 Production V2 上線後有空窗
3. 優先序建議 → V2-1 / V2-2（行為影響最大）＞ V2-3（如需重引入）＞ 其餘
