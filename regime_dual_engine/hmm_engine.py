"""
hmm_engine.py — Engine A: HMM Statistical State Generator (50% weight)

Reuses the legacy HMM core (fit_hmm / compute_features / decode_and_label)
which was already validated. Split into two steps so the backtest can fit once
every N days and reuse the cached state:

  fit_state_labels(close, cfg)        -> state dict (or None)
  bull_probability_from_state(state)  -> {hmm_bull_probability, hmm_regime_label}

Architectural rule: this engine uses ONLY returns + volatility (statistical
price behavior). No KER / ADX / MA-trend components are mixed in.
"""
from __future__ import annotations
import warnings

import numpy as np
import pandas as pd

# Reuse the validated legacy HMM core (same functions, same params)
from src.agents.regime_agent import (fit_hmm, compute_features,
                                     decode_and_label)


def fit_state_labels(close: pd.Series, cfg) -> dict | None:
    """Fit HMM and label states. Returns cache dict or None if insufficient."""
    feats = compute_features(close, cfg.hmm_vol_window)
    if len(feats) < cfg.hmm_min_obs:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = fit_hmm(feats.values, cfg)
        states, stats, labels, trending, spread = decode_and_label(model, feats, cfg)
    posteriors = model.predict_proba(feats.values)
    return {
        "labels": labels,
        "trending": trending,
        "states": states,
        "posteriors": posteriors,
    }


def bull_probability_from_state(state: dict | None, cfg) -> dict:
    """Extract Engine A signal from a fitted state dict.

    hmm_bull_probability = posterior of the BULL-labeled state (0-1).
    If no BULL state exists (all-sideways), fall back to cfg.hmm_neutral_bull_prob.
    """
    if state is None:
        return {"hmm_bull_probability": cfg.hmm_neutral_bull_prob,
                "hmm_regime_label": "UNKNOWN",
                "latest_posterior": None}

    labels = state["labels"]
    posteriors = state["posteriors"]
    trending = state["trending"]

    latest_posterior = posteriors[-1]
    bull_state = next((s for s, lbl in labels.items() if lbl == "BULL"), None)
    if bull_state is not None:
        bull_prob = float(latest_posterior[bull_state])
    else:
        bull_prob = cfg.hmm_neutral_bull_prob

    latest_state = int(state["states"][-1])
    regime_label = labels.get(latest_state, "SIDEWAYS")
    if not trending:
        regime_label = "RANGE_BOUND"

    return {
        "hmm_bull_probability": round(float(bull_prob), 4),
        "hmm_regime_label": regime_label,
        "latest_posterior": latest_posterior,
    }
