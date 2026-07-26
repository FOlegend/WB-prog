"""
config.py — 全域設定（swing trading bot）

所有可調參數集中在這裡，方便你每天 30-60 分鐘審核時一次看完。
本金鎖定 10,000 HKD（≈1,282 USD），Alpaca 帳戶計價。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass
class Config:
    # ---- 資金（鎖定 🔒）----
    starting_capital: float = 10000.0     # 🔒 本金（HKD）
    capital_currency: str = "HKD"
    fx_to_usd: float = 0.1282             # HKD→USD（≈7.8 HKD/USD）

    # ---- 路徑 ----
    base_dir: str = field(default_factory=lambda: os.path.dirname(os.path.abspath(__file__)))
    state_file: str = ""                  # 執行時填：data/state.json
    reports_dir: str = ""                 # 執行時填：reports/

    # ---- 標的篩選（screener）----
    price_min: float = 2.0
    price_max: float = 25.0
    min_dollar_vol: float = 2.0e7         # 近 20 日均金額成交量 ≥ $20M
    min_daily_range: float = 0.015        # 日內波幅 ≥ 1.5%
    min_er: float = 0.12                  # Kaufman ER 最低門檻（日線）
    screener_top_n: int = 12              # 篩選後保留前 N 名

    # ---- HMM regime 分類（照 MDPI 論文）----
    hmm_n_states: int = 3
    hmm_vol_window: int = 10              # 10 日 MSE 波動度
    hmm_n_iter: int = 75
    hmm_covariance_type: str = "full"
    hmm_random_state: int = 42
    hmm_min_obs: int = 200                # HMM 至少需要 200 根日線
    # 穩健命名門檻（修復 rigid labeling）
    min_bull_ret: float = 0.03            # 日報酬(%) 低於此不稱 BULL
    min_bear_ret: float = -0.02
    min_regime_spread: float = 0.05       # bull-bear spread 小於此 → 全 SIDEWAYS
    switch_posterior_thr: float = 0.60    # 後驗機率低於此 = 切換疑慮
    regime_refit_days: int = 20           # backtest 每 N 個交易日重 fit 一次 HMM

    # ---- 技術指標 ----
    ema_fast: int = 8
    ema_mid: int = 21
    ema_slow: int = 55
    rsi_period: int = 14
    bollinger_window: int = 20
    adx_period: int = 14
    atr_period: int = 14
    er_lookback: int = 30                 # Kaufman ER 回看天數

    # ---- 進出場（swing）----
    entry_threshold: float = 0.25         # 加權 net score > 此 → 買進候選
    stop_atr_mult: float = 1.5            # 止損 = entry - ATR × 1.5
    take_profit_atr_mult: float = 2.5     # 止盈 = entry + ATR × 2.5（≈1.67:1 R:R）
    trailing_atr_mult: float = 1.5        # 移動停利：從最高點回撤 ATR × 1.5
    trailing_trigger_r: float = 1.0       # 獲利達 1R 後才啟動移動停利
    max_holding_days: int = 30            # 時間停損（swing 約數週~月）
    max_open_positions: int = 5           # 投組層：最多同時持倉數
    max_position_pct: float = 0.25        # 單一持倉 ≤ 25% 權益
    risk_per_trade: float = 0.01          # 每筆風險 1% 權益
    regime_size_mult: dict = field(default_factory=lambda: {
        "BULL": 1.0, "SIDEWAYS": 0.6, "BEAR": 0.0, "RANGE_BOUND": 0.0
    })
    # 技術 ensemble 權重
    tech_weights: dict = field(default_factory=lambda: {
        "trend": 0.30, "mean_reversion": 0.20, "momentum": 0.30, "volatility": 0.20
    })
    # agent 加權（regime gate + technicals timing）
    regime_weight: float = 0.35
    technicals_weight: float = 0.65

    # ---- Alpaca 費用模型（2026-04 Brokerage Fee Schedule）----
    commission_per_share: float = 0.0
    sec_fee_per_dollar: float = 0.0000206   # SEC 費（sells only）
    finra_taf_per_share: float = 0.000195   # FINRA TAF（sells only），單筆上限 $9.79
    finra_taf_cap: float = 9.79
    slippage_pct: float = 0.0005            # 來回滑點約 0.05%

    # ---- backtest ----
    backtest_start: str = "2024-01-01"
    backtest_end: str = ""
    backtest_universe: list = field(default_factory=lambda: [
        "NIO", "PLUG", "RIVN", "LCID", "NOK", "INTC", "MARA", "VALE", "BAC", "F",
    ])

    def __post_init__(self):
        if not self.state_file:
            self.state_file = str(Path(self.base_dir) / "data" / "state.json")
        if not self.reports_dir:
            self.reports_dir = str(Path(self.base_dir) / "reports")

    # 便利屬性
    @property
    def capital_usd(self) -> float:
        return self.starting_capital * self.fx_to_usd
