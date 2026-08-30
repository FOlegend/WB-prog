"""
regime_dual.py — Dual-Engine Regime Core

Composite score:
    composite = (hmm_bull_prob * 100 * hmm_weight
                 + breadth_percentile * breadth_weight) / (hmm_weight + breadth_weight)
    clamp [0, 100]

Regime mapping (exact):
    score >= 65 -> BULL
    score <= 35 -> BEAR
    else        -> SIDEWAYS

Strategy mode (exact):
    BULL -> trend_following | SIDEWAYS -> mean_reversion | BEAR -> defensive

Ordered risk logic (per spec section 11):
    Step 1: composite
    Step 2: baseline regime
    Step 3: baseline strategy mode + size
    Step 4: Breadth Thrust override (BULL + trend_following + 1.0)
    Step 5: Bearish Breadth Divergence cap (HMM BULL + breadth declining -> <= 0.50)
    Step 6: Distribution Days hard cap (>= 5 -> <= 0.50)
    final: clamp [0, 1]

Output schema (public contract):
    {regime_label, composite_score, position_size_mult, strategy_mode, veto_flags}
"""
from __future__ import annotations

from .config import DualEngineConfig
from .distribution_days import count_distribution_days, distribution_day_cap


def _regime_from_score(score: float, cfg) -> str:
    if score >= cfg.bull_threshold:
        return "BULL"
    if score <= cfg.bear_threshold:
        return "BEAR"
    return "SIDEWAYS"


def _base_mode_and_size(regime: str, cfg) -> tuple[str, float]:
    if regime == "BULL":
        return "trend_following", cfg.base_size_bull
    if regime == "BEAR":
        return "defensive", cfg.base_size_bear
    return "mean_reversion", cfg.base_size_sideways


def _breadth_declining(breadth_now: float, breadth_10d_ago: float) -> bool:
    """'Trending down over 10 trading days' = current breadth strictly below
    10 trading days ago. Documented precisely (spec: avoid ambiguous impl).
    """
    return breadth_now < breadth_10d_ago


def _breadth_thrust(breadth_now: float, breadth_10d_ago: float, cfg) -> bool:
    """Breadth was below 30% ten days ago AND increased by > 20% relative."""
    if breadth_10d_ago is None or breadth_10d_ago >= cfg.thrust_floor_10d_ago:
        return False
    if breadth_10d_ago <= 0:
        return False
    change_pct = breadth_now / breadth_10d_ago - 1.0
    return change_pct > cfg.thrust_min_increase


def dual_engine_regime(
    hmm_bull_prob: float,
    hmm_regime_label: str,
    breadth_percentile: float,
    breadth_now: float,
    breadth_10d_ago: float | None,
    n_dist_days: int,
    cfg: DualEngineConfig,
) -> dict:
    """Compute the full dual-engine regime decision.

    All inputs are point-in-time (known at decision timestamp).
    """
    # ---- Step 1: composite score ----
    total_w = cfg.hmm_weight + cfg.breadth_weight
    composite = (hmm_bull_prob * 100.0 * cfg.hmm_weight
                 + breadth_percentile * cfg.breadth_weight) / total_w
    composite = max(0.0, min(100.0, composite))

    # ---- Step 2: baseline regime ----
    regime_label = _regime_from_score(composite, cfg)

    # ---- Step 3: baseline strategy mode + size ----
    strategy_mode, size_mult = _base_mode_and_size(regime_label, cfg)
    veto_flags: list[str] = []

    # ---- Step 4: Breadth Thrust override ----
    thrust_triggered = False
    if breadth_10d_ago is not None:
        thrust_triggered = _breadth_thrust(breadth_now, breadth_10d_ago, cfg)
    if thrust_triggered:
        regime_label = "BULL"
        strategy_mode = "trend_following"
        size_mult = 1.0
        veto_flags.append("BREADTH_THRUST")

    # ---- Step 5: Bearish Breadth Divergence cap ----
    divergence_triggered = False
    if hmm_regime_label == "BULL" and breadth_10d_ago is not None:
        divergence_triggered = _breadth_declining(breadth_now, breadth_10d_ago)
    if divergence_triggered:
        size_mult = min(size_mult, cfg.divergence_cap)
        veto_flags.append("BEARISH_BREADTH_DIVERGENCE")
        # NOTE: regime_label is NOT changed by the divergence veto

    # ---- Step 6: Distribution Days hard cap (unless overlay disabled for ablation) ----
    if cfg.enable_dist_day_overlay and n_dist_days >= cfg.dist_day_cap_threshold:
        size_mult, flags = distribution_day_cap(n_dist_days, cfg, size_mult)
        veto_flags.extend(flags)

    # ---- final clamp + validate ----
    size_mult = max(0.0, min(1.0, size_mult))
    composite = max(0.0, min(100.0, composite))

    return {
        "regime_label": regime_label,
        "composite_score": round(composite, 3),
        "position_size_mult": round(size_mult, 4),
        "strategy_mode": strategy_mode,
        "veto_flags": veto_flags,
    }
