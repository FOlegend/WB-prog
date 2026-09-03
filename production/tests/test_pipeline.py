"""
tests/test_pipeline.py — canonical pipeline tests (spec §9 "Pipeline")

  * deterministic: two runs on identical inputs -> identical DecisionRecord
  * valid EMPTY candidate set (screener EMPTY -> no BUY, exits still run)
  * explicit FAILURE state (screener failure -> BLOCKED entries + warning)
  * existing positions still evaluated for exits when screener fails
  * no BUY recommendation on incomplete screening/data
  * regime v1 output schema preserved inside the record
  * input `state` dict is never mutated by run_daily
  * DecisionRecord is JSON-serialisable

Uses the real offline cache (CachedSource) with small candidate sets so the
tests are deterministic and fast.
"""
from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.config import ProductionConfig
from production.datasource import build_cached_source, DataSource, Provenance
from production.pipeline import run_daily
from src.state.state import default_state, default_position


def _cfg() -> ProductionConfig:
    return ProductionConfig()


def _source() -> DataSource:
    return build_cached_source(_cfg())


def _fresh_state():
    st = default_state(_cfg().capital_usd)
    # give it a position so exits are exercisable
    pos = default_position("AAPL", 5, 180.0, "2024-01-10", 2.0,
                           170.0, 200.0, "BULL", "test")
    pos["signal_date"] = "2024-01-09"
    st["open_positions"].append(pos)
    st["cash"] = 1000.0
    return st


def test_deterministic_repeated_runs():
    cfg, src = _cfg(), _source()
    state = _fresh_state()
    a = run_daily("2025-07-31", state, src, cfg, screen_mode="provided",
                  candidate_tickers=["MSFT", "NVDA", "GOOGL"])
    b = run_daily("2025-07-31", state, src, cfg, screen_mode="provided",
                  candidate_tickers=["MSFT", "NVDA", "GOOGL"])
    assert a == b
    # and serialisable
    json.dumps(a)


def test_input_state_not_mutated():
    cfg, src = _cfg(), _source()
    state = _fresh_state()
    before = deepcopy(state)
    run_daily("2025-07-31", state, src, cfg, screen_mode="provided",
              candidate_tickers=["MSFT"])
    assert state == before


def test_regime_v1_output_in_record():
    cfg, src = _cfg(), _source()
    rec = run_daily("2025-07-31", _fresh_state(), src, cfg,
                    screen_mode="provided", candidate_tickers=["MSFT"])
    assert rec["regime"]["status"] == "OK"
    out = rec["regime"]["output"]
    assert set(out.keys()) == {"regime_label", "composite_score",
                               "position_size_mult", "strategy_mode",
                               "veto_flags"}
    assert out["regime_label"] in ("BULL", "SIDEWAYS", "BEAR")


def test_valid_empty_candidate_set():
    """provided empty list == valid empty (no BUY, no failure)."""
    cfg, src = _cfg(), _source()
    rec = run_daily("2025-07-31", _fresh_state(), src, cfg,
                    screen_mode="provided", candidate_tickers=[])
    assert rec["screener"]["status"] == "OK"
    assert rec["entries"]["status"] in ("EMPTY", "OK")
    assert rec["entries"]["n_buys"] == 0
    assert rec["pipeline_status"] != "FAILURE"


def test_failure_state_when_benchmark_unavailable():
    """Benchmark data missing -> regime FAILURE -> entries BLOCKED, no BUY."""
    cfg, src = _cfg(), _source()

    class NoBenchSource(DataSource):
        name = "no_bench"

        def universe(self):
            return ["MSFT"]

        def get_ohlcv(self, ticker, as_of=None, min_bars=60):
            return None, Provenance("x", ok=False, error=f"no {ticker}")

    rec = run_daily("2025-07-31", _fresh_state(), NoBenchSource(), cfg,
                    screen_mode="provided", candidate_tickers=["MSFT"])
    assert rec["regime"]["status"] == "FAILURE"
    assert rec["entries"]["status"] == "BLOCKED"
    assert rec["entries"]["n_buys"] == 0
    assert any("REGIME_FAILURE" in w for w in rec["warnings"])


def test_no_buy_when_screener_failure():
    """Screener FAILURE -> entries BLOCKED (no BUY from incomplete data),
    but held positions are still evaluated for exits."""
    cfg, src = _cfg(), _source()

    class BrokenScreenSource(DataSource):
        name = "broken_screen"
        benchmark = "SPY"

        def universe(self):
            return ["MSFT", "NVDA"]

        def get_ohlcv(self, ticker, as_of=None, min_bars=60):
            # benchmark OK; every other ticker fails => screen FAILURE
            if ticker == "SPY":
                df, _ = src.get_ohlcv("SPY", as_of=as_of, min_bars=min_bars)
                return df, Provenance("cache", ok=True)
            return None, Provenance("cache", ok=False, error=f"no {ticker}")

    state = _fresh_state()
    rec = run_daily("2025-07-31", state, BrokenScreenSource(), cfg,
                    screen_mode="live")
    assert rec["screener"]["status"] == "FAILURE"
    assert rec["entries"]["status"] == "BLOCKED"
    assert rec["entries"]["n_buys"] == 0
    # exits section still present (evaluated or with an explicit warning)
    assert "exits" in rec
    assert any("SCREENER_FAILURE" in w for w in rec["warnings"])


def test_exit_evaluated_when_held_data_available():
    """Held ticker with data -> exit check runs (returns proposed or holds)."""
    cfg, src = _cfg(), _source()
    state = _fresh_state()   # holds AAPL
    rec = run_daily("2025-07-31", state, src, cfg,
                    screen_mode="provided", candidate_tickers=[])
    assert rec["exits"]["status"] in ("OK", "PARTIAL")
    # either an exit was proposed or none triggered — never an error
    assert isinstance(rec["exits"]["proposed"], list)


def test_held_only_mode():
    """held_only: no candidates requested, exits evaluated."""
    cfg, src = _cfg(), _source()
    rec = run_daily("2025-07-31", _fresh_state(), src, cfg,
                    screen_mode="held_only")
    assert rec["screener"]["status"] == "EMPTY"
    assert rec["entries"]["n_buys"] == 0


def test_schema_keys_present():
    cfg, src = _cfg(), _source()
    rec = run_daily("2025-07-31", _fresh_state(), src, cfg,
                    screen_mode="provided", candidate_tickers=["MSFT"])
    expected = {"schema_version", "as_of", "generated_by", "pipeline_status",
                "data", "regime", "screener", "setup", "positions", "exits",
                "entries", "risk", "portfolio", "warnings", "recommendations"}
    assert expected <= set(rec.keys())


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
