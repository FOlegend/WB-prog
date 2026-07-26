# WB Swing Trading Bot

Human-in-the-loop swing trading bot — 每天花 30-60 分鐘審核 bot 產出的簡報，手動在 broker app 下單。

**無 LLM API** — 所有 agent 皆為純 Python 確定性函數，不需要任何 API key。

## 核心架構

仿 `virattt/ai-hedge-fund` 的 multi-agent 架構，但用確定性規則取代 LLM：

```
Stock Screening → Regime Classification (HMM) → Technicals Ensemble
                                                        ↓
                                          Risk Manager → Portfolio Manager
                                                        ↓
                                              Daily Briefing (Markdown)
                                              Backtest Engine (HTML)
```

## 六階段工作流程

| 階段 | 模組 | 說明 |
|------|------|------|
| 1. Stock Screening | `src/screener/screener.py` | 從便宜流動標的池篩選 ER + 波幅 + 流動性 TOP N |
| 2. Regime Classification | `src/agents/regime_agent.py` | HMM 3-state 分類 (BULL/BEAR/SIDEWAYS)，含穩健命名 |
| 3. Technicals | `src/agents/technicals_agent.py` | EMA + ADX + RSI + Bollinger + MACD + Momentum ensemble |
| 4. Risk & Position Sizing | `src/agents/risk_manager.py` | ATR-based 倉位計算，regime-gated |
| 5. Recommended Order | `src/portfolio/portfolio_manager.py` | 加權決策 + 完整進場/TP/SL 推理 |
| 6. Backtest | `src/backtest/engine.py` + `report.py` | 逐日回測 + HTML 視覺化報告 |

## 快速開始

### 安裝

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 每日簡報（live 模式）

```bash
# 用 screener 自動篩選標的
python main.py

# 指定標的（跳過 screener）
python main.py --tickers NIO,PLUG,NOK

# 只檢查現有持倉的出場訊號（不篩新標的）
python main.py --no-screen
```

輸出：
- `reports/briefing_YYYY-MM-DD.md` — 每日簡報（持倉、推薦訂單、下單 checklist）
- `reports/orders_YYYY-MM-DD.json` — 機器可讀訂單
- `data/state.json` — 持倉狀態（JSON 持久化）

### 回測

```bash
# 預設 5 檔、2024-01-01 至今
python backtest.py --fast

# 指定標的與區間
python backtest.py --tickers NIO,PLUG,NOK,SOFI,BBAI --start 2023-01-01

# 全 universe 回測（較慢）
python backtest.py
```

輸出：
- `reports/backtest_report.html` — 自包含 HTML 報告（權益曲線、回撤、P/L 分佈、regime 時間線、交易紀錄）
- `reports/backtest_result.json` — 績效指標 + 交易 log

## 配色慣例

採用中文市場慣例：**漲紅跌綠**（上漲=紅色，下跌=綠色）。

## 本金設定

- 起始本金：**10,000 HKD**（鎖定）
- 匯率：1 HKD = 0.1282 USD → $1,282.00 USD
- 單筆風險：equity × 1%
- 最大持倉：5 檔，單檔 ≤ 25% equity

## Agent 訊號契約

每個 agent 回傳統一格式的 signal dict：

```python
{
    "agent": "regime_agent",       # agent 名稱
    "signal": "bullish",           # bullish / bearish / neutral
    "confidence": 79.1,            # 0-100
    "score": 1.0,                  # -1.0 ~ 1.0
    "reasoning": "HMM 3-state：..." # 人類可讀推理
}
```

Portfolio Manager 加權決策：
- `net = 0.35 × regime_score + 0.65 × technicals_score`
- BUY if `net > 0.25` AND `regime == BULL AND trending == True`
- 出場規則：STOP_LOSS / TAKE_PROFIT / TRAILING_STOP / TIME_STOP(30d) / SIGNAL_EXIT

## 目錄結構

```
WB prog/
├── config.py                    # 全域參數（本金、HMM、指標、風控、費用）
├── main.py                      # 每日簡報 runner
├── backtest.py                  # 回測 runner
├── requirements.txt
├── src/
│   ├── data/
│   │   └── data_fetcher.py      # yfinance 日線抓取
│   ├── indicators/
│   │   └── technicals.py        # EMA/RSI/Bollinger/MACD/ATR/ADX/ER/Hurst
│   ├── agents/
│   │   ├── regime_agent.py      # HMM regime 分類
│   │   ├── technicals_agent.py  # 技術 ensemble
│   │   └── risk_manager.py      # ATR 倉位計算
│   ├── portfolio/
│   │   └── portfolio_manager.py # 加權決策 + 出場邏輯
│   ├── screener/
│   │   └── screener.py          # 便宜流動標的篩選
│   ├── state/
│   │   └── state.py             # JSON 持倉持久化
│   ├── backtest/
│   │   ├── engine.py            # 逐日回測引擎
│   │   └── report.py            # HTML 報告產生器
│   └── utils/
│       ├── fees.py              # Alpaca 費用模型
│       └── display.py           # 簡報 markdown 渲染
├── data/
│   └── state.json               # 持倉狀態（自動生成）
└── reports/                     # 簡報 + 回測報告（自動生成）
```

## 技術細節

### HMM Regime 分類
- 3-state GaussianHMM，full covariance，EM n_iter=75
- 特徵：日報酬% + 10日 MSE 波動度
- 穩健命名：range-bound guard + 經濟門檻 + symmetric guard
- 切換偵測：`predict_proba` 後驗機率（bounded [0,1]）
- 參考：MDPI JRFM 13(12):311 (Wang, Lin, Mikhelson 2020)

### 回測引擎
- 逐日 replay，無 lookahead（agents 只看 ≤ 當日資料）
- HMM 每 20 天重 fit（平衡準確度與速度）
- 收盤成交，含 Alpaca 費用（SEC/TAF/滑點）
- 回測結束自動平掉殘留部位

### Alpaca 費用模型
- Commission: $0
- SEC fee: $0.0000206/$ (sells only)
- FINRA TAF: $0.000195/share, cap $9.79
- Slippage: 0.05%
