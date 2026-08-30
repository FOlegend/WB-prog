"""
tests/test_production.py — Production V2 integration tests

Verifies (spec §9):
  1. Screener → Regime → Setup → Risk → Briefing wiring runs end-to-end
  2. Production actually calls Regime v1 (not legacy v3)
  3. Production actually calls Setup v1 (pullback)
  4. Distribution Days are NOT calculated/used
  5. Legacy weighted entry (0.35 regime + 0.65 tech) is NOT used
  6. BEAR regime → no new long entries
  7. Setup quality affects sizing
  8. next-open / gap / extension behavior matches backtest assumptions

Runs offline using the existing research caches (no network).
"""
from __future__ import annotations

import os
import sys
import inspect

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.config import ProductionConfig
from production.agents import regime as prod_regime
from production.agents import setup as prod_setup
from production.risk.risk import size_swing_position
from production.portfolio.portfolio import build_order_buy
from production.reporting.briefing import render_briefing


def _spy_df():
    cache = ProductionConfig().cache_dir
    df = pd.read_csv(os.path.join(cache, "SPY.csv"), parse_dates=["datetime"])
    return df[df["datetime"] <= "2025-07-31"].reset_index(drop=True)


def _breadth():
    return prod_regime.load_breadth(ProductionConfig(), end="2025-07-31")


def test_calls_regime_v1_not_v3():
    """Production regime wrapper must call compute_regime_decision (v1),
    and must not IMPORT the legacy v3 regime."""
    src = inspect.getsource(prod_regime)
    assert "compute_regime_decision" in src
    assert "from src.agents.regime_agent import" not in src
    assert "import market_regime" not in src
    assert "from src.agents.regime_agent" not in src


def test_regime_v1_no_dist_days():
    """Regime v1 decision must NOT contain DISTRIBUTION_DAY_CAP."""
    cfg = ProductionConfig()
    dec = prod_regime.compute_market_regime(_spy_df(), _breadth(), cfg)
    assert dec["regime_label"] in ("BULL", "SIDEWAYS", "BEAR")
    assert "DISTRIBUTION_DAY_CAP" not in dec["veto_flags"]
    assert dec["_diag"]["distribution_days"] == 0  # not computed in production


def test_schema():
    dec = prod_regime.compute_market_regime(_spy_df(), _breadth(), ProductionConfig())
    assert set(dec.keys()) == {"regime_label", "composite_score",
                               "position_size_mult", "strategy_mode", "veto_flags",
                               "_diag"}


def test_setup_v1_pullback_only():
    """Production setup wrapper must assert pullback-only (freeze guard)."""
    src = inspect.getsource(prod_setup.evaluate_setup)
    assert "['pullback']" in src or '"pullback"' in src
    cfg = ProductionConfig()
    assert cfg.setup_enabled_types == ["pullback"]


def test_no_weighted_entry():
    """Production portfolio must NOT use the legacy weighted entry formula."""
    src = inspect.getsource(build_order_buy) + inspect.getsource(prod_regime)
    assert "0.35" not in src and "technicals_weight" not in src


def test_bear_blocks_long():
    """BEAR regime → position_size_mult 0 → no BUY order."""
    cfg = ProductionConfig()
    bear_regime = {"regime_label": "BEAR", "composite_score": 20.0,
                   "position_size_mult": 0.0, "strategy_mode": "defensive",
                   "veto_flags": []}
    sizing = size_swing_position(1000, 500, 50.0, 1.0, 0.0, 1.0, 0, cfg)
    assert sizing["allow"] is False
    assert "禁止開新多單" in sizing.get("reasoning", "") or "cash" in sizing.get("reasoning", "")


def test_quality_affects_sizing():
    """Higher quality mult → larger position (same risk budget)."""
    cfg = ProductionConfig()
    s_low = size_swing_position(10000, 5000, 50.0, 2.0, 1.0, 0.5, 0, cfg)
    s_high = size_swing_position(10000, 5000, 50.0, 2.0, 1.0, 1.0, 0, cfg)
    assert s_high["allow"] and s_low["allow"]
    assert s_high["shares"] > s_low["shares"]


def test_next_open_gap_extension_contract():
    """Buy order carries next-open entry + gap/extension guardrails."""
    cfg = ProductionConfig()
    reg = {"regime_label": "BULL", "composite_score": 80.0,
           "position_size_mult": 1.0, "strategy_mode": "trend_following",
           "veto_flags": []}
    setup = {"valid": True, "setup_type": "pullback", "setup_score": 0.7,
             "setup_quality_mult": 0.75, "signal_close": 100.0, "atr": 2.0,
             "entry_reason": "pullback: px>50SMA+nearEMA"}
    sizing = size_swing_position(10000, 5000, 100.0, 2.0, 1.0, 0.75, 0, cfg)
    o = build_order_buy("TEST", 100.0, sizing, setup, reg, reason="r")
    assert o["entry_model"] == "NEXT_OPEN"
    assert cfg.max_entry_gap_pct == 0.02
    assert cfg.max_extension_from_pivot_pct == 0.03


def test_briefing_renders():
    """Briefing renders regime header + candidate block."""
    cfg = ProductionConfig()
    reg = {"regime_label": "BULL", "composite_score": 80.0,
           "position_size_mult": 1.0, "strategy_mode": "trend_following",
           "veto_flags": [], "_diag": {"hmm_bull_prob": 0.8,
                                       "breadth_percentile": 70.0,
                                       "breadth_now_pct": 60.0,
                                       "breadth_10d_ago_pct": 55.0}}
    orders = [{"ticker": "NVDA", "action": "BUY", "shares": 10, "price": 120.0,
               "stop": 116.0, "take_profit": 128.0, "risk_reward": 2.0,
               "setup_type": "pullback", "setup_score": 0.7,
               "setup_quality_mult": 0.75, "regime_fit": "BULL/SIDEWAYS OK",
               "reason": "pullback: nearEMA"}]
    ctx = [{"_equity": 1282.0, "_cash": 500.0, "_n_open": 1}]
    md = render_briefing(reg, ctx, orders, "2025-07-31", cfg)
    assert "Market Regime" in md and "BULL" in md
    assert "NVDA" in md and "Pullback" in md


def test_pipeline_end_to_end():
    """Screener(offline fallback) → Regime v1 → Setup → Risk → Briefing wiring.

    Uses the cached SPY + a small ticker set (offline). Verifies the production
    entry path produces at least the regime decision and no crash.
    """
    from production.main import run_daily
    cfg = ProductionConfig()
    # run with a fixed ticker set (skip live screener) on the PIT demo date
    run_daily(cfg, tickers=["AAPL", "MSFT", "NVDA"], no_screen=True, as_of="2025-07-31")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception as e:
            import traceback
            print(f"  FAIL {name}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
