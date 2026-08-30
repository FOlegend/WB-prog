"""
engine.py — Dual-Engine Regime Facade

Ties Engine A (HMM) + Engine B (Breadth) + Distribution Days overlay into the
public decision function used by backtests and validations.

    decision = compute_regime_decision(market_df, breadth_df, cfg,
                                       hmm_cache=None)

market_df : market index OHLCV (datetime/open/high/low/close/volume)
breadth_df: daily breadth series (index=datetime, columns:
            pct_above_50dma [0-100], ad_line [cumulative A/D])
hmm_cache : persistent dict; HMM is refit every cfg.regime_refit_days calls.
"""
from __future__ import annotations

import pandas as pd

from .config import DualEngineConfig
from . import hmm_engine, breadth_engine
from .distribution_days import count_distribution_days
from .regime_dual import dual_engine_regime


def compute_regime_decision(
    market_df: pd.DataFrame,
    breadth_df: pd.DataFrame,
    cfg: DualEngineConfig,
    hmm_cache: dict | None = None,
) -> dict:
    """Full dual-engine decision at the LAST row of market_df/breadth_df.

    Point-in-time: only data up to the last timestamp is used.
    """
    if hmm_cache is None:
        hmm_cache = {}

    # ---- Engine A: HMM (statistical), refit every N calls ----
    if hmm_cache.get("state") is None or hmm_cache.get("calls", 0) >= cfg.regime_refit_days:
        state = hmm_engine.fit_state_labels(market_df["close"].astype(float), cfg)
        hmm_cache["state"] = state
        hmm_cache["calls"] = 0
    else:
        state = hmm_cache["state"]
        hmm_cache["calls"] += 1
    hmm = hmm_engine.bull_probability_from_state(state, cfg)
    hmm_bull_prob = hmm["hmm_bull_probability"]
    hmm_label = hmm["hmm_regime_label"]

    # ---- Engine B: Market Breadth ----
    if breadth_df is None or len(breadth_df) == 0:
        breadth_score = 50.0
        breadth_now = 0.5
        breadth_10d_ago = None
    else:
        ad_line = breadth_df["ad_line"] if "ad_line" in breadth_df.columns else None
        breadth_score = breadth_engine.breadth_percentile_score(
            breadth_df["pct_above_50dma"], ad_line, cfg)
        breadth_now = float(breadth_df["pct_above_50dma"].iloc[-1]) / 100.0
        lb = cfg.divergence_lookback
        if len(breadth_df) > lb:
            breadth_10d_ago = float(breadth_df["pct_above_50dma"].iloc[-1 - lb]) / 100.0
        else:
            breadth_10d_ago = None

    # ---- Tactical overlay: Distribution Days — RESEARCH ONLY ----
    # Final production architecture is HMM + Market Breadth only. The dist-day
    # count is computed ONLY when the research overlay is enabled; in the
    # default (production) config it is never computed and never affects the
    # decision.
    if cfg.enable_dist_day_overlay:
        n_dist = count_distribution_days(market_df, cfg)
    else:
        n_dist = 0

    # ---- Composite + mapping + overrides ----
    decision = dual_engine_regime(
        hmm_bull_prob=hmm_bull_prob,
        hmm_regime_label=hmm_label,
        breadth_percentile=breadth_score,
        breadth_now=breadth_now,
        breadth_10d_ago=breadth_10d_ago,
        n_dist_days=n_dist,
        cfg=cfg,
    )
    # attach diagnostics (separate from public contract)
    decision["_diag"] = {
        "hmm_bull_prob": hmm_bull_prob,
        "hmm_regime_label": hmm_label,
        "breadth_percentile": breadth_score,
        "breadth_now_pct": round(breadth_now * 100.0, 2),
        "breadth_10d_ago_pct": round(breadth_10d_ago * 100.0, 2) if breadth_10d_ago is not None else None,
        "distribution_days": n_dist,
    }
    return decision
