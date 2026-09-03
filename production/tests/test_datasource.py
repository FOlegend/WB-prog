"""
tests/test_datasource.py — Data layer tests (spec §9 "Data layer")

  * primary source success (cache read + provenance)
  * point-in-time slice (no bars after as_of)
  * missing ticker -> explicit failure, NOT empty frame
  * cache behavior (CachedSource lazy load / universe from membership files)
  * yfinance network failure -> stale cache fallback marked stale
  * secondary (stooq) recovery labelled when enabled
  * no silent provider mixing (one provider per ticker per call)
  * explicit failure state preserved through the API

All offline except the two tests that monkeypatch the network layer.
"""
from __future__ import annotations

import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.datasource import (CachedSource, YFinanceSource, StooqSource,
                                   DataSourceError, Provenance)
from production.config import ProductionConfig


def _mk_cache(tmp) -> str:
    d = os.path.join(tmp, "equities")
    os.makedirs(d, exist_ok=True)
    idx = pd.date_range("2024-01-01", "2024-06-30", freq="B")
    df = pd.DataFrame({
        "datetime": idx, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.5, "volume": 1e6})
    df.to_csv(os.path.join(d, "AAA.csv"), index=False)
    df2 = df.copy()
    df2["close"] = 200.0
    df2.to_csv(os.path.join(d, "SPY.csv"), index=False)
    return d


def test_cache_source_success():
    with tempfile.TemporaryDirectory() as tmp:
        cs = CachedSource(_mk_cache(tmp))
        df, p = cs.get_ohlcv("AAA", as_of="2024-03-31")
        assert df is not None and len(df) > 0
        assert p.provider == "cache" and p.ok
        # PIT: no row after 2024-03-31
        assert df["datetime"].max() <= pd.Timestamp("2024-03-31")
        assert p.as_of_bound <= "2024-03-31"


def test_missing_ticker_is_failure_not_empty():
    with tempfile.TemporaryDirectory() as tmp:
        cs = CachedSource(_mk_cache(tmp))
        df, p = cs.get_ohlcv("ZZZ")
        assert df is None and p.ok is False
        assert "no cache file" in (p.error or "")


def test_insufficient_bars_is_failure():
    with tempfile.TemporaryDirectory() as tmp:
        cs = CachedSource(_mk_cache(tmp))
        df, p = cs.get_ohlcv("AAA", min_bars=500)
        assert df is None and p.ok is False


def test_no_provider_mixing_single_call():
    """One ticker one provider in a single get_ohlcv call."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _mk_cache(tmp)
        ys = YFinanceSource(d, refresh_network=False)  # cache-only behaviour
        df, p = ys.get_ohlcv("AAA", as_of="2024-03-31")
        assert df is not None and p.provider == "cache"
        # network disabled -> as_of beyond tail -> explicit fail (no mixing)
        df2, p2 = ys.get_ohlcv("AAA", as_of="2025-01-31", min_bars=500)
        assert df2 is None and p2.ok is False


def test_yfinance_stale_cache_marked_stale():
    """Network failure + stale cache -> returns cache but provenance.stale."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _mk_cache(tmp)
        ys = YFinanceSource(d)
        orig = ys._download
        ys._download = lambda t: None        # network fails
        try:
            df, p = ys.get_ohlcv("AAA", as_of="2025-01-01", min_bars=60)
        finally:
            ys._download = orig
        assert df is not None
        assert p.stale is True
        assert p.provider == "cache"


def test_stooq_recovery_labelled():
    """yfinance fails, stooq fallback enabled -> provider == 'stooq'."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _mk_cache(tmp)

        class FakeStooq:
            name = "stooq"

            def get_ohlcv(self, ticker, as_of=None, min_bars=60):
                idx = pd.date_range("2024-01-01", "2024-06-30", freq="B")
                df = pd.DataFrame({
                    "datetime": idx, "open": 10.0, "high": 11.0, "low": 9.0,
                    "close": 10.5, "volume": 5e5})
                return df, Provenance("stooq", file="stooq")

        ys = YFinanceSource(d, allow_stooq_fallback=True, stooq=FakeStooq())
        orig = ys._download
        ys._download = lambda t: None        # network fails
        try:
            df, p = ys.get_ohlcv("AAA", as_of="2025-01-01", min_bars=60)
        finally:
            ys._download = orig
        assert df is not None
        assert p.provider == "stooq"          # labelled fallback, not silent
        assert "stooq recovery" in (p.note or "")


def test_cache_universe_from_membership(cfg: ProductionConfig = None):
    """Universe excludes benchmark and resolves from membership files."""
    cfg = cfg or ProductionConfig()
    cs = CachedSource(cfg.cache_dir,
                      universe_file_sp500=os.path.join(cfg.base_dir,
                                                       "data", "constituents",
                                                       "sp500_current.csv"),
                      universe_file_ndx100=os.path.join(cfg.base_dir,
                                                        "data", "constituents",
                                                        "nasdaq100_thuningxu.csv"))
    uni = cs.universe()
    assert "SPY" not in uni
    assert "AAPL" in uni and "MSFT" in uni
    assert len(uni) > 400


def test_build_cached_source_reads_real_cache():
    """Integration: build_cached_source returns a working PIT source."""
    cfg = ProductionConfig()
    cs = CachedSource(cfg.cache_dir)
    df, p = cs.get_ohlcv("SPY", as_of="2025-07-31", min_bars=200)
    assert df is not None and len(df) >= 200
    assert df["datetime"].max() <= pd.Timestamp("2025-07-31")


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
