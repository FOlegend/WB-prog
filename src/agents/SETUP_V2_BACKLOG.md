# Setup v2 — Research Backlog

**用途**：記錄所有「疑似需要修改 Setup v1」的改進想法。依 `SETUP_V1_FREEZE.md`，
**只記錄、不實作**，等解除凍結後評估。每項附證據來源與影響方向。

| # | 項目 | 觀察/證據 | 來源 | 影響方向 |
|---|------|-----------|------|----------|
| S2-1 | **SIDEWAYS 進場過濾** | SIDEWAYS 下 pullback PF 0.87（BULL 1.34）；gross edge≈0 + 摩擦侵蝕（net −0.104%） | `setup_v1_root_cause_report.md` | 盤整期降頻/降倉（會改 setup 或 entry 邏輯） |
| S2-2 | **Breakout 重新評估** | v3 下 Breakout 最佳（+29.86%）；v1 下劣勢（補位 PF 0.923）——可能與 regime 方法或 BULL 定義有關 | `setup_v1_regime_policy_report.md` | 需先理解 v1 下 breakout 為何失效，再決定是否值得重測 |
| S2-3 | **Regime-dependent 選擇** | B（BULL both, pullback-first）+1.19% 與 C（BULL breakout）+0.62% 皆輸 A（+2.23%）——現行證據不支持 | `setup_v1_regime_policy_report.md` | 若 v2 後有新 regime 或新 setup，可重測 |
| S2-4 | **Setup score 更高門檻** | 0.6 CAGR 1.00% vs 0.5 的 0.80%（breakout）；pullback 未測 0.6/0.7 | `setup_v1_validation_report.md` | 潛在 threshold 重校（須解除凍結） |
| S2-5 | **Setup × Regime 交叉表** | BULL 是唯一賺錢 regime（兩種 setup PF>1.3）——BULL 期間可考慮加碼 | `setup_v1_validation_report.md` | 潛在 regime-conditioned sizing |
| S2-6 | **出場 refinement** | STOP_LOSS 佔 54%（SIDEWAYS）；avgR −1.042 顯示多數 −1R 全損 | `setup_v1_root_cause_report.md` | 更早的尾隨/訊號出場研究 |
| S2-7 | **摩擦敏感度** | 0.103% 摩擦把 SIDEWAYS −0.001% → −0.104% | `setup_v1_root_cause_report.md` | 若換低費 broker 可重估 |

**規則**：
1. 新增想法 → append 一行（含證據路徑），不實作
2. 評估時機 → 解除 freeze、或 Production V2 上線後有空窗
3. 優先序建議 → S2-1（行為影響最大）＞ S2-2 ＞ 其餘
