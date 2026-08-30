"""
test_regime_dual.py — Unit tests for the dual-engine regime core.

Covers (spec §14):
  - composite boundaries
  - regime mapping boundaries (64.99/65.00/35.00/35.01)
  - distribution days (4/5/6+)
  - breadth divergence (HMM BULL + declining -> cap; not declining -> no veto)
  - breadth thrust (25% 10d ago + >20% relative increase -> BULL + 1.0)
  - combined thrust + dist days >= 5 -> 0.50

Run: python -m pytest regime_dual_engine/tests/test_regime_dual.py -v
  (or: python regime_dual_engine/tests/test_regime_dual.py)
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.regime_dual import (dual_engine_regime,
                                            _regime_from_score)
from regime_dual_engine.distribution_days import count_distribution_days

import pandas as pd

CFG = DualEngineConfig()                      # production default: no dist overlay
CFG_RESEARCH = DualEngineConfig(enable_dist_day_overlay=True)  # research: with overlay


def _mk(**kw) -> dict:
    """Call dual_engine_regime with defaults (production config, overlay off)."""
    cfg = kw.pop("cfg", CFG)
    defaults = dict(
        hmm_bull_prob=0.8, hmm_regime_label="BULL",
        breadth_percentile=80.0, breadth_now=0.8, breadth_10d_ago=0.8,
        n_dist_days=0, cfg=cfg,
    )
    defaults.update(kw)
    return dual_engine_regime(**defaults)


def test_composite_boundaries():
    # HMM = 1.0, Breadth = 100 -> Composite = 100
    d = _mk(hmm_bull_prob=1.0, breadth_percentile=100.0)
    assert d["composite_score"] == 100.0, d
    # HMM = 0.0, Breadth = 0 -> Composite = 0
    d = _mk(hmm_bull_prob=0.0, breadth_percentile=0.0)
    assert d["composite_score"] == 0.0, d


def test_regime_boundaries():
    assert _regime_from_score(64.99, CFG) == "SIDEWAYS"
    assert _regime_from_score(65.00, CFG) == "BULL"
    assert _regime_from_score(35.00, CFG) == "BEAR"
    assert _regime_from_score(35.01, CFG) == "SIDEWAYS"


def test_distribution_days_cap():
    # overlay enabled (research config) -> 4 days no cap, 5 days cap
    d = _mk(n_dist_days=4, cfg=CFG_RESEARCH)
    assert d["position_size_mult"] == 1.0
    assert "DISTRIBUTION_DAY_CAP" not in d["veto_flags"]
    # 5 days -> cap at 0.50
    d = _mk(n_dist_days=5, cfg=CFG_RESEARCH)
    assert d["position_size_mult"] == 0.50
    assert "DISTRIBUTION_DAY_CAP" in d["veto_flags"]
    # 6 days -> cap at 0.50
    d = _mk(n_dist_days=6, cfg=CFG_RESEARCH)
    assert d["position_size_mult"] == 0.50
    # PRODUCTION default (overlay off): dist days have NO effect
    d = _mk(n_dist_days=6)
    assert d["position_size_mult"] == 1.0
    assert "DISTRIBUTION_DAY_CAP" not in d["veto_flags"]


def test_breadth_divergence():
    # HMM BULL + breadth declining -> <= 0.50 + veto
    d = _mk(hmm_regime_label="BULL", hmm_bull_prob=0.9, breadth_now=0.55,
            breadth_10d_ago=0.70)
    assert d["position_size_mult"] <= 0.50
    assert "BEARISH_BREADTH_DIVERGENCE" in d["veto_flags"]
    # regime label unchanged (still BULL from composite)
    assert d["regime_label"] == "BULL"

    # HMM BULL + breadth NOT declining -> no divergence veto
    d = _mk(hmm_regime_label="BULL", hmm_bull_prob=0.9, breadth_now=0.70,
            breadth_10d_ago=0.55)
    assert "BEARISH_BREADTH_DIVERGENCE" not in d["veto_flags"]


def test_breadth_thrust():
    # breadth 10d ago = 25% (< 30%), current 35% -> rel increase 40% > 20%
    d = _mk(hmm_bull_prob=0.3, breadth_percentile=90.0,
            breadth_now=0.35, breadth_10d_ago=0.25)
    assert d["regime_label"] == "BULL"
    assert d["strategy_mode"] == "trend_following"
    assert d["position_size_mult"] == 1.0
    assert "BREADTH_THRUST" in d["veto_flags"]


def test_combined_thrust_and_dist_days():
    # Breadth Thrust + Distribution Days >= 5 -> hard cap wins at 0.50 (research cfg)
    d = _mk(hmm_bull_prob=0.3, breadth_percentile=90.0,
            breadth_now=0.35, breadth_10d_ago=0.25, n_dist_days=5,
            cfg=CFG_RESEARCH)
    assert d["regime_label"] == "BULL"
    assert d["strategy_mode"] == "trend_following"
    assert d["position_size_mult"] == 0.50
    assert "DISTRIBUTION_DAY_CAP" in d["veto_flags"]


def test_output_schema():
    d = _mk()
    assert d["regime_label"] in {"BULL", "BEAR", "SIDEWAYS"}
    assert 0.0 <= d["composite_score"] <= 100.0
    assert 0.0 <= d["position_size_mult"] <= 1.0
    assert d["strategy_mode"] in {"trend_following", "mean_reversion", "defensive"}
    assert isinstance(d["veto_flags"], list)


def test_dist_day_counter():
    # synthetic: 6 distribution days out of last 25 -> count >= 5
    n = 260
    close = pd.Series([100.0] * n)
    volume = pd.Series([1e6] * n)
    # inject 6 drops on strictly increasing volume at the end
    drop_vols = [2e6, 3e6, 4e6, 5e6, 6e6, 7e6]
    for j, v in enumerate(drop_vols):
        i = n - 6 + j
        close.iloc[i] = close.iloc[i - 1] * 0.995  # -0.5% drop
        volume.iloc[i] = v                          # increasing volume
    df = pd.DataFrame({"close": close, "volume": volume,
                       "high": close * 1.01, "low": close * 0.99,
                       "open": close})
    cnt = count_distribution_days(df, CFG)
    assert cnt >= 5, f"expected >=5 dist days, got {cnt}"


def test_dist_days_never_affect_score_or_label():
    """Architecture guard (reviewer §6): Distribution Days may only cap size,
    never change composite_score or regime_label. Checked on research config."""
    base = _mk(hmm_bull_prob=0.5, breadth_percentile=50.0, n_dist_days=0,
               cfg=CFG_RESEARCH)
    capped = _mk(hmm_bull_prob=0.5, breadth_percentile=50.0, n_dist_days=6,
                 cfg=CFG_RESEARCH)
    assert base["composite_score"] == capped["composite_score"]
    assert base["regime_label"] == capped["regime_label"]
    assert capped["position_size_mult"] == 0.50
    assert "DISTRIBUTION_DAY_CAP" in capped["veto_flags"]


def test_dist_days_overlay_off():
    """Production default (overlay off): dist days have no effect."""
    d = dual_engine_regime(hmm_bull_prob=0.8, hmm_regime_label="BULL",
                           breadth_percentile=80.0, breadth_now=0.8,
                           breadth_10d_ago=0.8, n_dist_days=6, cfg=CFG)
    assert d["position_size_mult"] == 1.0
    assert "DISTRIBUTION_DAY_CAP" not in d["veto_flags"]


def test_production_config_has_no_dist_days():
    """Final architecture: default config must have overlay OFF (two engines only)."""
    assert DualEngineConfig().enable_dist_day_overlay is False


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
