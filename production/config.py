"""
config.py — Production V2 Configuration (FROZEN Regime v1 + Setup v1)

Deliberately small & self-contained: holds ONLY what the production pipeline
needs, with the frozen values baked in (no optimization knobs). The frozen
Regime v1 params live in regime_dual_engine.config.DualEngineConfig (imported
as-is); frozen Setup v1 params are defined here. Legacy v3 regime params
(regime_score_weights, exposure modes, etc.) are intentionally ABSENT.

Frozen contract: see regime_dual_engine/REGIME_V1_FREEZE.md and
src/agents/SETUP_V1_FREEZE.md
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from regime_dual_engine.config import DualEngineConfig  # frozen Regime v1


@dataclass
class ProductionConfig:
    # ================= 資金（鎖定） =================
    starting_capital: float = 10000.0        # HKD（鎖定）
    fx_to_usd: float = 0.1282                # HKD → USD
    risk_per_trade: float = 0.01             # 每筆風險 1% 權益

    # ================= 路徑 =================
    base_dir: str = field(default_factory=lambda: os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    state_file: str = ""                     # 執行時填 data/state.json
    reports_dir: str = ""                    # 執行時填 reports/
    cache_dir: str = ""                      # OHLCV cache（與 research 共用唯讀）

    # ================= Regime v1（凍結，不可改） =================
    regime: DualEngineConfig = field(default_factory=DualEngineConfig)
    # 凍結值確認（僅供測試斷言，不在此改）
    regime_market_index: str = "SPY"

    # ================= Screener（既有 6-filter，凍結） =================
    screener_min_market_cap: float = 10e9
    screener_min_dollar_vol: float = 50e6
    screener_dvol_window: int = 20
    screener_min_price: float = 10.0
    screener_min_atr_pct: float = 2.0
    screener_atr_period: int = 14
    screener_rs_lookbacks: list = field(default_factory=lambda: [50])
    screener_rs_weights: list = field(default_factory=lambda: [1.0])
    screener_min_rs_ratio: float = 1.0
    screener_rs_top_pct: float = 0.20
    screener_use_rs_top_pct: bool = False
    screener_min_adx: float = 20.0
    screener_adx_period: int = 14
    screener_top_n: int = 30

    # ================= Setup v1（凍結：Pullback Only） =================
    entry_mode: str = "setup"
    setup_enabled_types: list = field(default_factory=lambda: ["pullback"])  # FROZEN
    setup_score_threshold: float = 0.5
    setup_quality_score_high: float = 0.8
    setup_quality_score_mid: float = 0.6
    setup_quality_mult_high: float = 1.0
    setup_quality_mult_mid: float = 0.75
    setup_quality_mult_low: float = 0.5
    setup_pullback_ma_tol: float = 0.01     # close within 1% of 10/20 EMA
    max_entry_gap_pct: float = 0.02          # next open > signal_close*(1+2%) → skip
    max_extension_from_pivot_pct: float = 0.03
    setup_min_rs_rank: int = 10              # unused by pullback; kept for compat

    # ================= 風險 / 出場（與 validated backtest 一致） =================
    stop_atr_mult: float = 1.5
    take_profit_atr_mult: float = 2.5
    trailing_atr_mult: float = 1.5
    trailing_trigger_r: float = 1.0
    max_holding_days: int = 30
    max_open_positions: int = 5
    max_position_pct: float = 0.25
    slippage_pct: float = 0.0005            # 來回滑點約 0.05%（執行層費用模型）

    # ================= 技術指標（pullback / ATR sizing 需要） =================
    atr_period: int = 14

    # 便利屬性
    @property
    def capital_usd(self) -> float:
        return self.starting_capital * self.fx_to_usd

    def __post_init__(self):
        if not self.state_file:
            self.state_file = str(Path(self.base_dir) / "production" / "data" / "state.json")
        if not self.reports_dir:
            self.reports_dir = str(Path(self.base_dir) / "reports")
        if not self.cache_dir:
            d = str(Path(self.base_dir).parent / "data" / "cache" / "equities")
            if os.path.isdir(d):
                self.cache_dir = d
            else:
                self.cache_dir = str(Path(self.base_dir) / "data" / "cache" / "equities")
