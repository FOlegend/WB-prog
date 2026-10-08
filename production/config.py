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
from production.contracts.reason_codes import (EXIT_ENGINE_LEGACY,
                                               EXIT_ENGINE_MODES)


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

    # ================= Phase 3 wiring（唯一新增 flag） =================
    # legacy : 舊決策路徑為唯一權威（DEFAULT — 不得自動改為 new）
    # shadow : 舊路徑仍為唯一權威；新 Stop / Exit Engine 只被評估 + 記錄
    # new    : 新 Exit Engine 的 ExitDecision 才可驅動 production 動作
    # 任何缺失 / 非法 / 初始化失敗 → 安全回退 legacy（見 resolve_exit_engine_mode）。
    exit_engine_mode: str = EXIT_ENGINE_LEGACY
    # 記錄 config 層的安全回退等訊息（pipeline 會帶進 DecisionRecord.warnings）
    config_warnings: list = field(default_factory=list)

    # ================= R7 observability（研究專用，預設關閉） =================
    # Tier B of the capacity-observability proposal: when entries are blocked
    # BEFORE any setup evaluation, also RUN the setup + risk engines on the
    # blocked candidates so the foregone cohort is measurable.
    #
    # This is a LOGGING capability, not a decision input: the result is written
    # only to rec["setup"]["blocked_snapshot"]["per_candidate"], after the
    # decision to block has already been taken. It never appends to `buys`,
    # never mutates `state`, and must never be enabled in live.
    # Cost: one extra evaluate_setup + size_position pass over the blocked
    # candidate population on blocked sessions only.
    research_blocked_setup_eval: bool = False

    # ================= BS-3/4/6 data-validity（預設關閉） =================
    # Freshness / temporal-consistency contract for Regime INPUTS.
    #
    # DEFAULT None = the assessment is not run at all, which is what keeps
    # HISTORICAL REPLAY byte-identical (task §9). The live runner may pass a
    # FreshnessPolicy explicitly once a human has approved the thresholds
    # (task §7: no arbitrary production threshold may be invented here).
    #
    # A policy never changes regime mathematics, weights, thresholds or the
    # divergence cap. It can only (a) record that the inputs are not current and
    # (b) optionally suppress the decision, leaving a human to act. It never
    # substitutes an input or invents a fallback regime.
    freshness_policy: object | None = None

    # The price basis the OHLCV dataset is expressed in, recorded verbatim in
    # every DecisionRecord so a reader can tell whether two observations are
    # comparable (task §10).
    #
    # The historical cache is `auto_adjust=True` (fully adjusted: splits AND
    # dividends, applied retrospectively to the whole series). That label is
    # deliberately NOT defaulted here: the frozen research baseline must not
    # acquire a provenance claim it never had, and the live dataset's basis is
    # a human decision (see reports/live_data_lineage_and_corporate_actions).
    # `None` means "not declared", which the record reports as `unrecorded`.
    ohlcv_price_basis: str | None = None

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
        # normalise the wiring flag in place; never trust an invalid value
        resolved = resolve_exit_engine_mode(self)
        if resolved != self.exit_engine_mode:
            self.config_warnings.append(
                f"exit_engine_mode {self.exit_engine_mode!r} is not one of "
                f"{sorted(EXIT_ENGINE_MODES)} — fell back to "
                f"{EXIT_ENGINE_LEGACY!r} (safe default)")
        self.exit_engine_mode = resolved


def resolve_exit_engine_mode(cfg) -> str:
    """Return a valid exit-engine mode, failing SAFE to `legacy`.

    Phase-3 §1.2: a missing attribute, an invalid value, or anything unexpected
    must resolve to `legacy` — the mode that leaves production behaviour
    untouched. This function never raises.
    """
    try:
        mode = getattr(cfg, "exit_engine_mode", None)
    except Exception:                                     # pragma: no cover
        return EXIT_ENGINE_LEGACY
    if not isinstance(mode, str):
        return EXIT_ENGINE_LEGACY
    mode = mode.strip().lower()
    return mode if mode in EXIT_ENGINE_MODES else EXIT_ENGINE_LEGACY
