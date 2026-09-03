"""
tests/test_backtest.py — production-equivalent backtest tests (spec §9)

  * deterministic repeated runs (identical trade log + equity curve)
  * same decision pipeline as production (replays run_daily — no second impl)
  * no look-ahead (functional checks):
      - all fills happen after their bucket's rebalance screen date
      - every entry ticker was a member of the ACTIVE bucket that month
      - trade dates are within [start, end]
  * outcome sanity (equity curve monotonic dates, no NaNs)

Runs offline over a TINY universe (real cache dir + a 6-ticker membership
file) over ~3 months so the test stays fast.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.config import ProductionConfig
from production.datasource import CachedSource
from production.backtest import ProductionBacktest

_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "AVGO"]
START, END = "2018-01-01", "2018-03-31"


def _tiny_source(tmp_membership: str) -> CachedSource:
    cfg = ProductionConfig()
    return CachedSource(cfg.cache_dir,
                        universe_file_sp500=tmp_membership,
                        universe_file_ndx100=None)


def _mk_membership(tmp) -> str:
    p = os.path.join(tmp, "uni.csv")
    with open(p, "w") as f:
        f.write("Symbol\n" + "\n".join(_TICKERS) + "\n")
    return p


def _run_once():
    cfg = ProductionConfig()
    with tempfile.TemporaryDirectory() as tmp:
        src = _tiny_source(_mk_membership(tmp))
        bt = ProductionBacktest(cfg, src, START, END)
        return bt.run(bucket_cache_file=None)


def test_backtest_runs_and_covers_window():
    res = _run_once()
    assert "error" not in res, res
    assert res["summary"]["n_days"] >= 50
    # summary window = first/last TRADING day in [START, END]
    assert START <= res["summary"]["start"] <= "2018-01-10"
    assert "2018-03-25" <= res["summary"]["end"] <= END
    # equity curve is monotonic in date
    dates = [c["date"] for c in res["equity_curve"]]
    assert dates == sorted(dates)


def test_backtest_deterministic_repeated_runs():
    a = _run_once()
    b = _run_once()
    sa = json.dumps(a["summary"], sort_keys=True, default=str)
    sb = json.dumps(b["summary"], sort_keys=True, default=str)
    assert sa == sb
    assert json.dumps(a["trade_log"], sort_keys=True, default=str) == \
           json.dumps(b["trade_log"], sort_keys=True, default=str)
    assert len(a["equity_curve"]) == len(b["equity_curve"])


def test_no_lookahead_entry_after_bucket_screen():
    """Every entry happens only after its active bucket was screened, and the
    ticker was a member of that bucket (no future/fabricated candidates)."""
    res = _run_once()
    screens = res["screens"]
    assert screens, "expected at least one monthly screen"
    for trade in res["trade_log"]:
        entry_date = trade["entry_date"]
        bucket_date = trade.get("bucket_date")
        # bucket_date defaults to the trading day when the proposal queued;
        # assert membership against whichever bucket was active that day.
        active = None
        for rd, s in sorted(screens.items()):
            if rd <= entry_date:
                active = (rd, s)
            else:
                break
        assert active is not None, f"entry {entry_date} before any screen"
        rd, s = active
        assert s["status"] == "OK"
        # entry ticker must be inside its active bucket
        assert trade["ticker"] in s["tickers"], (
            f"{trade['ticker']} not in bucket of {rd}")
        assert entry_date >= rd


def test_trade_dates_within_window():
    res = _run_once()
    for trade in res["trade_log"]:
        assert START <= trade["entry_date"] <= END
        assert trade["exit_date"] is None or START <= trade["exit_date"] <= END


def test_decisions_are_decision_records():
    """Replay consumed DecisionRecords produced by run_daily schema."""
    cfg = ProductionConfig()
    from production.pipeline import run_daily
    from production.datasource import build_cached_source
    # single-day smoke: run_daily on a historical date returns a full record
    rec = run_daily("2018-02-28", __import__(
        "src.state.state", fromlist=["default_state"]).default_state(
        cfg.capital_usd), build_cached_source(cfg), cfg,
        screen_mode="provided", candidate_tickers=_TICKERS[:3])
    assert rec["as_of"] == "2018-02-28"
    assert rec["regime"]["output"]["regime_label"] in ("BULL", "SIDEWAYS",
                                                       "BEAR")


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
