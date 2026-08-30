"""
config.py — Dual-Engine Regime Configuration

Engine A: HMM (50%)
Engine B: Market Breadth (50%)
Tactical Overlay: Distribution Days (risk cap only, never scores)

Weights are configurable so the ablation harness can run:
  HMM only          -> hmm_weight=1.0, breadth_weight=0.0
  Breadth only      -> hmm_weight=0.0, breadth_weight=1.0
  HMM + Breadth     -> 0.5 / 0.5
  + Distribution Days overlay -> overlay enabled
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class DualEngineConfig:
    # ---- Engine weights (default 50/50) ----
    hmm_weight: float = 0.5          # Engine A contribution
    breadth_weight: float = 0.5      # Engine B contribution

    # ---- HMM (reuses legacy HMM core) ----
    hmm_n_states: int = 3
    hmm_vol_window: int = 10
    hmm_n_iter: int = 75
    hmm_covariance_type: str = "diag"
    hmm_random_state: int = 42
    hmm_min_obs: int = 200
    regime_refit_days: int = 20
    # robust labeling thresholds
    min_bull_ret: float = 0.03
    min_bear_ret: float = -0.02
    min_regime_spread: float = 0.05
    # fallback bull probability when no BULL state exists (e.g. all-sideways)
    hmm_neutral_bull_prob: float = 0.35

    # ---- Market Breadth (Engine B) ----
    breadth_sma_period: int = 50                 # % of stocks above 50DMA
    breadth_percentile_lookback: int = 252       # trailing window for percentile rank
    breadth_pct_above_weight: float = 0.7        # % above 50DMA weight in breadth score
    breadth_ad_momentum_weight: float = 0.3      # A/D line momentum weight
    breadth_ad_momentum_lookback: int = 10       # A/D line ROC lookback (days)

    # ---- Regime mapping boundaries ----
    bull_threshold: float = 65.0     # score >= 65 -> BULL
    bear_threshold: float = 35.0     # score <= 35 -> BEAR

    # ---- Baseline position size by regime (before overrides) ----
    base_size_bull: float = 1.0
    base_size_sideways: float = 0.5
    base_size_bear: float = 0.0

    # ---- Bearish Breadth Divergence (tactical cap) ----
    divergence_lookback: int = 10        # "trending down over N trading days"
    divergence_cap: float = 0.50         # HMM BULL + breadth declining -> cap at 0.50

    # ---- Breadth Thrust Override (structural) ----
    thrust_floor_10d_ago: float = 0.30   # breadth 10d ago < 30%
    thrust_min_increase: float = 0.20    # relative increase > 20% over 10d

    # ---- Distribution Days (RESEARCH ONLY — removed from production) ----
    # Final production architecture = HMM + Market Breadth only (reviewer spec
    # 2026-08-30). The overlay is disabled by DEFAULT; research scripts
    # (ablation variant D, dist-day audit) explicitly set it to True.
    enable_dist_day_overlay: bool = False   # True -> research ablation variant
    dist_day_lookback: int = 25          # rolling 25-trading-day count
    dist_day_drop: float = -0.002        # close drops > 0.2% on volume up
    dist_day_rally_reset: float = 0.05   # 5% rally voids a dist day (O'Neil)
    dist_day_cap_threshold: int = 5      # >= 5 days -> hard cap
    dist_day_cap: float = 0.50           # hard position-size cap

    # ---- market index for HMM ----
    market_index: str = "SPY"

    # weight sanity
    def __post_init__(self):
        w = self.hmm_weight + self.breadth_weight
        if w <= 0:
            raise ValueError("hmm_weight + breadth_weight must be > 0")
