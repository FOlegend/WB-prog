"""
test_pit_breadth.py — regression protection for the point-in-time breadth contract.

WHY THIS FILE EXISTS
--------------------
A PIT audit (reports/pit_breadth_leak_2026-10-01.md) found that both breadth
loaders ignored their `end` argument whenever the cache file existed:

    if os.path.exists(path) and not rebuild:
        return pd.read_csv(path, ...)      # `end` ignored -> whole 2016..2025 file

`compute_regime_decision` then reads `pct_above_50dma.iloc[-1]`, so in EVERY
historical replay Regime v1's breadth engine evaluated the 2025-07-31 tail: the
shipped breadth inputs were bit-identical (percentile 41.746) for as-of dates in
2018, 2020, 2022, 2024 and 2025 — a look-ahead covering 50 % of the composite.

These tests make that failure mode impossible to reintroduce silently.

Required cases
  1 different historical as_of dates must not return the same tail
  2 no returned observation may be later than as_of
  3 the CACHE path is PIT-safe (and the cache file itself is never truncated)
  4 the REBUILD path is PIT-safe (full frame cached, sliced frame returned)
  5 an exact trading date selects exactly that observation
  6 a missing / non-trading-day as_of follows the documented date semantics

Runs as a plain script (repo convention, no pytest):
  python production/tests/test_pit_breadth.py
"""
from __future__ import annotations

import os
import sys
import tempfile

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from regime_dual_engine import pit_breadth_data as P
from regime_dual_engine import breadth_data as B
from regime_dual_engine.breadth_data import slice_to_end

PROBES = ["2018-06-29", "2020-03-31", "2022-06-30", "2024-01-31",
          "2024-06-28", "2025-01-31", "2025-07-31"]
FAILURE_TAIL = pd.Timestamp("2025-07-31")   # the tail the bug always returned


def _synthetic(n_start="2016-01-04", n_end="2025-07-31") -> pd.DataFrame:
    """Deterministic stand-in for a built breadth frame (business-day index)."""
    idx = pd.bdate_range(n_start, n_end)
    return pd.DataFrame({"pct_above_50dma": [float(i % 100) for i in range(len(idx))],
                         "ad_line": [float(i) for i in range(len(idx))],
                         "n_stocks": [500] * len(idx)}, index=idx)


# ===========================================================================
# 1. the discovered failure mode
# ===========================================================================
def test_1_different_as_of_do_not_share_the_future_tail():
    tails = {}
    for d in PROBES:
        df = P.get_breadth("pit", rebuild=False, end=d)
        assert len(df) > 0, f"{d}: empty breadth"
        tails[d] = (pd.Timestamp(df.index.max()), float(df["pct_above_50dma"].iloc[-1]))
    # the OLD behaviour: every probe returned index.max() == 2025-07-31 and the
    # same pct value. That must now be impossible.
    assert len({v[0] for v in tails.values()}) == len(PROBES), \
        f"distinct tail dates expected; got {tails}"
    assert len({v[1] for v in tails.values()}) > 1, \
        "all probes returned the SAME breadth value — the leak is back"
    for d, (mx, _) in tails.items():
        assert mx <= pd.Timestamp(d), f"{d}: tail {mx.date()} is in the future"
    # explicitly: only the true last session may coincide with the cache tail
    hitting_tail = [d for d, (mx, _) in tails.items() if mx == FAILURE_TAIL]
    assert hitting_tail == ["2025-07-31"], \
        f"only the last session may reach the cache tail; got {hitting_tail}"


# ===========================================================================
# 2. no observation later than as_of (both loaders, many ends)
# ===========================================================================
def test_2_no_observation_after_as_of():
    ends = ["2016-02-01", "2017-06-30", "2018-06-29", "2019-12-31",
            "2021-03-15", "2023-11-30", "2024-01-31", "2025-07-31"]
    for end in ends:
        for name, df in (("get_breadth", P.get_breadth("pit", rebuild=False, end=end)),
                         ("fallback", B.get_breadth_series(verbose=False, end=end))):
            assert len(df) > 0, f"{name} {end}: empty"
            assert df.index.max() <= pd.Timestamp(end), \
                f"{name}: {end} returned {df.index.max().date()}"
    # a series restricted further must be a subset
    lo, hi = P.get_breadth("pit", rebuild=False, end="2020-06-30"), \
             P.get_breadth("pit", rebuild=False, end="2021-06-30")
    assert len(lo) < len(hi)
    assert lo.index.max() <= pd.Timestamp("2020-06-30") < hi.index.max()


# ===========================================================================
# 3. cache path is PIT-safe and never truncates the cache file
# ===========================================================================
def test_3_cache_path_pit_safe_and_cache_not_truncated():
    path = P._PIT_OUT
    with open(path, "rb") as fh:
        before = fh.read()
    sub = P.get_breadth("pit", rebuild=False, end="2019-06-28")
    assert sub.index.max() <= pd.Timestamp("2019-06-28")
    with open(path, "rb") as fh:
        after = fh.read()
    assert before == after, "a read-only PIT request must not rewrite the cache"
    # default end must be unchanged behaviour (full series)
    full = P.get_breadth("pit", rebuild=False)
    assert full.index.max() == FAILURE_TAIL and len(full) > len(sub)


# ===========================================================================
# 4. rebuild path is PIT-safe (full frame cached, sliced frame returned)
# ===========================================================================
def test_4_rebuild_path_pit_safe():
    full = _synthetic()
    with tempfile.TemporaryDirectory() as td:
        for mod, attr, builder_name in ((P, "_PIT_OUT", "build_pit_breadth"),
                                        (B, "_OUT_PATH", "build_breadth_series")):
            old_path = getattr(mod, attr)
            old_builder = getattr(mod, builder_name)
            tmp = os.path.join(td, f"{builder_name}.csv")
            setattr(mod, attr, tmp)
            setattr(mod, builder_name, lambda end=None, **kw: full)
            try:
                out = (mod.get_breadth("pit", rebuild=True, end="2018-06-29")
                       if mod is P else
                       mod.get_breadth_series(rebuild=True, end="2018-06-29",
                                              verbose=False))
                cached = pd.read_csv(tmp, parse_dates=["datetime"])
            finally:
                setattr(mod, attr, old_path)
                setattr(mod, builder_name, old_builder)
            assert out.index.max() <= pd.Timestamp("2018-06-29"), \
                f"{builder_name}: rebuild returned a future observation"
            assert len(cached) == len(full), \
                f"{builder_name}: the cache must hold the FULL built frame"
    # the guard itself must fail loud rather than leak a frame it cannot slice
    try:
        slice_to_end(pd.DataFrame({"a": [1, 2]}), "2020-01-01", name="t")
    except TypeError as exc:
        assert "point-in-time" in str(exc)
    else:
        raise AssertionError("slice_to_end must raise on an unuslicable frame")


# ===========================================================================
# 5. an exact trading date selects exactly that observation
# ===========================================================================
def test_5_exact_trading_date_is_selected():
    full = P.get_breadth("pit", rebuild=False)
    for d in ("2024-01-31", "2024-06-28", "2025-03-31"):
        sub = P.get_breadth("pit", rebuild=False, end=d)
        ts = pd.Timestamp(d)
        if ts not in full.index:
            continue
        assert sub.index.max() == ts, f"{d}: tail is {sub.index.max().date()}"
        assert float(sub["pct_above_50dma"].iloc[-1]) == \
            float(full.loc[ts, "pct_above_50dma"]), f"{d}: row content differs"
        assert len(sub) == int((full.index <= ts).sum())


# ===========================================================================
# 6. non-trading-day / missing as_of semantics
# ===========================================================================
def test_6_non_trading_day_as_of_uses_last_prior_session():
    full = P.get_breadth("pit", rebuild=False)
    cases = {
        "2024-01-01": "2023-12-29",   # New Year's Day (holiday)
        "2024-01-06": "2024-01-05",   # Saturday
        "2024-01-07": "2024-01-05",   # Sunday
        "2024-07-04": "2024-07-03",   # Independence Day (holiday)
    }
    for end, expected in cases.items():
        sub = P.get_breadth("pit", rebuild=False, end=end)
        assert len(sub) > 0, f"{end}: empty"
        assert sub.index.max() == pd.Timestamp(expected), \
            f"{end}: expected last prior session {expected}, " \
            f"got {sub.index.max().date()}"
        assert pd.Timestamp(end) not in sub.index, f"{end} is not a session"
    # before the series start -> EMPTY on purpose (a data failure, not "neutral")
    early = P.get_breadth("pit", rebuild=False, end="2010-01-01")
    assert len(early) == 0, "a pre-history as_of must return an empty frame"


# ===========================================================================
# 7. the ledger must expose the breadth tail date (no silent staleness)
# ===========================================================================
def test_7_ledger_exposes_breadth_tail_date():
    from production.config import ProductionConfig
    from production.datasource import build_cached_source
    from production.pipeline import run_daily
    from src.state.state import default_state

    cfg = ProductionConfig()
    src = build_cached_source(cfg)

    def _rec(as_of):
        return run_daily(as_of, default_state(cfg.capital_usd), src, cfg,
                         screen_mode="provided", candidate_tickers=["AAPL"])

    b_recent = _rec("2025-07-24")["data"]["breadth"]
    assert "tail_date" in b_recent and "tail_matches_as_of" in b_recent, \
        "the ledger must expose breadth_tail_date (spec §5)"
    assert b_recent["tail_date"] is not None
    assert b_recent["tail_date"] <= "2025-07-24"
    assert b_recent["tail_matches_as_of"] is True

    # a historical run must NOT report the cache tail (the old failure mode)
    b_old = _rec("2019-06-28")["data"]["breadth"]
    assert b_old["tail_date"] <= "2019-06-28", b_old
    assert b_old["tail_date"] != b_recent["tail_date"], \
        "a historical run reported the same breadth tail as a recent one"
    assert b_old["rows"] < b_recent["rows"]


# ===========================================================================
# runner
# ===========================================================================
def main() -> int:
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:                       # noqa: BLE001
            print(f"  FAIL  {fn.__name__}: {type(exc).__name__}: {exc}")
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
