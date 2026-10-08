"""
test_breadth_freshness.py — BS acceptance tests (data integrity, not strategy)

The contract being tested is narrow and strict:

  A. the refresh path is reproducible, PIT-safe and APPEND-ONLY
  B. the loader exposes tail_date / requested_as_of / age_days / matches_as_of
  C. the five validity states are distinguished, never collapsed
  D. a stale input is RECORDED and never silently substituted
  E. HISTORICAL REPLAY IS UNCHANGED (the policy is inert by default)

(E) is the one that matters most. A data-integrity feature that quietly
recomputes yesterday's regime is worse than no feature at all.

Run:  python production/tests/test_breadth_freshness.py
"""
from __future__ import annotations

import datetime
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

from production.config import ProductionConfig
from production.data_validity import (FreshnessPolicy, InputRef,
                                      VALIDITY_INVALID, VALIDITY_MISSING,
                                      VALIDITY_STALE,
                                      VALIDITY_STALE_CONSTITUENTS,
                                      VALIDITY_STATES,
                                      VALIDITY_TEMPORALLY_INCONSISTENT,
                                      VALIDITY_VALID, assess, human_report)
from regime_dual_engine.breadth_refresh import compare_historical

_R: list[tuple[str, bool, str]] = []


def _check(name: str, cond: bool, detail: str = "") -> None:
    _R.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}"
          + (f" — {detail}" if detail and not cond else ""))


def _frame(dates: list[str]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in dates], name="datetime")
    return pd.DataFrame({"pct_above_50dma": [50.0] * len(dates)}, index=idx)


def _ohlcv(dates: list[str]) -> pd.DataFrame:
    """The REAL OHLCV shape: RangeIndex + a `datetime` column (not a
    DatetimeIndex). Used so the tests exercise the production frame layout."""
    return pd.DataFrame({
        "datetime": pd.to_datetime(dates),
        "open": [100.0] * len(dates), "high": [101.0] * len(dates),
        "low": [99.0] * len(dates), "close": [100.5] * len(dates),
        "volume": [1000] * len(dates),
    })


# ---------------------------------------------------------------------------
# 1. loader contract (§5)
# ---------------------------------------------------------------------------
def test_loader_exposes_freshness_fields():
    from production.agents.regime import load_breadth
    cfg = ProductionConfig()
    b = load_breadth(cfg, end="2025-07-31")
    _check("loader returns rows", b is not None and len(b) > 0)
    tail = pd.Timestamp(b.index.max()).strftime("%Y-%m-%d")
    ref = InputRef("breadth", tail, "2025-07-31", n_observations=len(b))
    _check("InputRef.age_days is 0 on an exact session", ref.age_days == 0,
           f"got {ref.age_days}")
    _check("InputRef.matches_as_of True on an exact session",
           ref.matches_as_of is True)

    stale = InputRef("breadth", "2025-07-31", "2026-10-04")
    _check("age_days computed against requested as_of",
           stale.age_days == (datetime.date(2026, 10, 4)
                              - datetime.date(2025, 7, 31)).days,
           f"got {stale.age_days}")
    _check("matches_as_of False when the tail lags", stale.matches_as_of is False)

    future = InputRef("breadth", "2026-10-05", "2026-10-04")
    _check("is_future detects a look-ahead tail", future.is_future is True)

    # non-trading day resolves to the last prior session, per existing semantics
    sat = load_breadth(cfg, end="2025-08-02")   # a Saturday
    _check("non-trading as_of resolves to the last prior session",
           pd.Timestamp(sat.index.max()) <= pd.Timestamp("2025-08-02"),
           str(pd.Timestamp(sat.index.max()).date()))


# ---------------------------------------------------------------------------
# 2. the five states are distinguished (§14)
# ---------------------------------------------------------------------------
def test_validity_states_are_distinct():
    pol = FreshnessPolicy(max_age_days=7, temporal_skew_days=3,
                          policy_id="test", approved_by="test")

    v_valid = assess(regime_as_of="2026-08-06",
                     spy_df=_frame(["2026-08-05", "2026-08-06"]),
                     breadth_df=_frame(["2026-08-05", "2026-08-06"]),
                     policy=pol)
    _check("both current -> VALID", v_valid.state == VALIDITY_VALID,
           f"{v_valid.state} {v_valid.reasons}")
    _check("VALID is trustworthy", v_valid.decision_trustworthy is True)
    _check("VALID -> temporal_consistent True",
           v_valid.regime_input_temporal_consistent is True)
    _check("VALID -> freshness_valid True",
           v_valid.regime_input_freshness_valid is True)

    v_stale = assess(regime_as_of="2026-10-04",
                     spy_df=_frame(["2026-08-05", "2026-08-06"]),
                     breadth_df=_frame(["2025-07-30", "2025-07-31"]),
                     policy=pol)
    _check("428-day-old breadth -> STALE", v_stale.state == VALIDITY_STALE,
           f"{v_stale.state} {v_stale.reasons}")
    _check("STALE is NOT trustworthy", v_stale.decision_trustworthy is False)
    _check("STALE -> freshness_valid False",
           v_stale.regime_input_freshness_valid is False)
    _check("STALE records the breadth age",
           v_stale.breadth_age_days == (datetime.date(2026, 10, 4)
                                        - datetime.date(2025, 7, 31)).days)

    v_incons = assess(regime_as_of="2026-08-12",
                      spy_df=_frame(["2026-08-11", "2026-08-12"]),
                      breadth_df=_frame(["2026-08-05", "2026-08-06"]),
                      policy=pol)
    _check("both fresh but 6 days apart -> TEMPORALLY_INCONSISTENT",
           v_incons.state == VALIDITY_TEMPORALLY_INCONSISTENT,
           f"{v_incons.state} {v_incons.reasons}")
    _check("TEMPORALLY_INCONSISTENT is NOT trustworthy",
           v_incons.decision_trustworthy is False)

    v_miss = assess(regime_as_of="2026-08-06", spy_df=_frame(["2026-08-06"]),
                    breadth_df=pd.DataFrame(), policy=pol)
    _check("empty breadth -> MISSING", v_miss.state == VALIDITY_MISSING,
           f"{v_miss.state} {v_miss.reasons}")
    _check("MISSING reports age None", v_miss.breadth_age_days is None)

    v_future = assess(regime_as_of="2026-08-06",
                      spy_df=_frame(["2026-08-05", "2026-08-06"]),
                      breadth_df=_frame(["2026-08-05", "2026-08-07"]),
                      policy=pol)
    _check("breadth tail AFTER as_of -> INVALID (worse than stale)",
           v_future.state == VALIDITY_INVALID, f"{v_future.state}")

    _check("all six states are in the vocabulary",
           VALIDITY_STATES == {VALIDITY_VALID, VALIDITY_STALE,
                               VALIDITY_STALE_CONSTITUENTS,
                               VALIDITY_TEMPORALLY_INCONSISTENT,
                               VALIDITY_MISSING, VALIDITY_INVALID})
    _check("states are not collapsed into one",
           len({v_valid.state, v_stale.state, v_incons.state,
                v_miss.state, v_future.state}) == 5)


def test_stale_constituents_is_its_own_state():
    """§6: current OHLCV does NOT make breadth current.

    The PIT builder forward-fills the last membership snapshot, so a recent
    breadth row can rest on an old universe. That must be distinguishable from
    ordinary staleness, because the remedy is different: refresh the
    constituent dataset, not the price cache.
    """
    pol = FreshnessPolicy(max_age_days=7, temporal_skew_days=3,
                          max_constituent_age_days=14)
    # SPY and breadth both perfectly current; membership 37 days old
    v = assess(regime_as_of="2026-08-06",
               spy_df=_ohlcv(["2026-08-05", "2026-08-06"]),
               breadth_df=_frame(["2026-08-05", "2026-08-06"]),
               policy=pol, constituent_snapshot="2026-06-30")
    _check("current OHLCV + stale membership -> STALE_CONSTITUENTS",
           v.state == VALIDITY_STALE_CONSTITUENTS, f"{v.state} {v.reasons}")
    _check("STALE_CONSTITUENTS is NOT trustworthy",
           v.decision_trustworthy is False)
    _check("the constituent age is reported",
           v.constituent_age_days == 37, f"got {v.constituent_age_days}")
    _check("constituents_valid is False", v.constituents_valid is False)
    _check("SPY itself is fresh (so it is not plain STALE)",
           v.spy_age_days == 0)
    _check("the carry-forward is named in the reasons",
           "CONSTITUENT_MEMBERSHIP_CARRIED_FORWARD" in v.reasons
           or "CONSTITUENTS_STALE" in v.reasons, str(v.reasons))

    # membership within the bound -> VALID
    v2 = assess(regime_as_of="2026-08-06",
                spy_df=_ohlcv(["2026-08-05", "2026-08-06"]),
                breadth_df=_frame(["2026-08-05", "2026-08-06"]),
                policy=pol, constituent_snapshot="2026-08-01")
    _check("membership within 14 d -> VALID", v2.state == VALIDITY_VALID,
           f"{v2.state} {v2.reasons}")

    # no constituent declared -> check disabled (historical replay)
    v3 = assess(regime_as_of="2026-08-06",
                spy_df=_ohlcv(["2026-08-05", "2026-08-06"]),
                breadth_df=_frame(["2026-08-05", "2026-08-06"]), policy=pol)
    _check("no constituent declared -> check disabled, VALID",
           v3.state == VALIDITY_VALID, f"{v3.state} {v3.reasons}")
    _check("a disabled constituent check reports valid", v3.constituents_valid)

    # a bound of None disables it even when a snapshot is supplied
    pol_none = FreshnessPolicy(max_age_days=7, temporal_skew_days=3,
                               max_constituent_age_days=None)
    v4 = assess(regime_as_of="2026-08-06",
                spy_df=_ohlcv(["2026-08-05", "2026-08-06"]),
                breadth_df=_frame(["2026-08-05", "2026-08-06"]),
                policy=pol_none, constituent_snapshot="2020-01-01")
    _check("max_constituent_age_days=None disables the check",
           v4.state == VALIDITY_VALID, f"{v4.state} {v4.reasons}")

    d = v.as_dict()
    for k in ("constituent_snapshot_date", "constituent_age_days",
              "constituents_valid"):
        _check(f"record exposes {k}", k in d)


# ---------------------------------------------------------------------------
# 3. no silent substitution (§8)
# ---------------------------------------------------------------------------
def test_no_silent_substitution():
    pol = FreshnessPolicy(max_age_days=7, temporal_skew_days=3)
    stale = assess(regime_as_of="2026-10-04",
                   spy_df=_frame(["2026-08-05", "2026-08-06"]),
                   breadth_df=_frame(["2025-07-30", "2025-07-31"]), policy=pol)
    d = stale.as_dict()
    _check("the STALE breadth tail is reported verbatim",
           d["breadth_tail_date"] == "2025-07-31")
    _check("the requested as_of is reported",
           d["regime_as_of"] == "2026-10-04")
    _check("no synthetic breadth value is invented",
           "pct_above" not in str(d.get("inputs")) or
           d["inputs"]["breadth"].get("tail_date") == "2025-07-31")
    _check("no default regime is substituted",
           "regime_label" not in d)
    _check("no default multiplier is substituted", "position_size_mult" not in d)
    _check("the record says the decision is untrustworthy",
           d["decision_trustworthy"] is False)
    _check("provenance is present", d["provenance"] is not None)
    rep = human_report(stale)
    for token in ("STALE", "2025-07-31", "2026-10-04", "decision_trustworthy"):
        _check(f"operator report contains {token!r}", token in rep)
    _check("operator report does NOT assert a regime label",
           "BEAR" not in rep and "BULL" not in rep and "SIDEWAYS" not in rep)


# ---------------------------------------------------------------------------
# 4. §13 record fields are all present
# ---------------------------------------------------------------------------
def test_record_fields_required_by_spec():
    pol = FreshnessPolicy(max_age_days=7, temporal_skew_days=3)
    v = assess(regime_as_of="2026-10-04",
               spy_df=_frame(["2026-08-05", "2026-08-06"]),
               breadth_df=_frame(["2025-07-30", "2025-07-31"]), policy=pol)
    d = v.as_dict()
    for k in ("regime_as_of", "spy_tail_date", "breadth_tail_date",
              "spy_age_days", "breadth_age_days", "spy_matches_as_of",
              "breadth_matches_as_of", "regime_input_temporal_consistent",
              "regime_input_freshness_valid", "state", "reasons", "policy"):
        _check(f"record exposes {k}", k in d)
    # the pre-existing fields must NOT be removed (§13)
    _check("policy is echoed in the record", d["policy"]["max_age_days"] == 7)


# ---------------------------------------------------------------------------
# 5. historical invariance of the refresh (§11)
# ---------------------------------------------------------------------------
def test_refresh_appends_and_never_rewrites():
    old = _frame(["2024-01-03", "2024-01-04", "2024-01-05"])
    same = _frame(["2024-01-03", "2024-01-04", "2024-01-05",
                   "2024-01-08"])
    rep = compare_historical(old, same)
    _check("an append leaves history identical", rep["identical"] is True)
    _check("the appended day is outside the compared range",
           rep["n_common"] == 3)

    old2 = old.copy()
    old2.iloc[2, 0] = 99.0
    rep2 = compare_historical(old2, same)
    _check("a rewrite is DETECTED", rep2["identical"] is False)
    _check("the differing column is named",
           "pct_above_50dma" in rep2["differences"])
    _check("the differing date is reported",
           bool(rep2["differences"]["pct_above_50dma"]["first_differing_date"]))


def test_real_pit_refresh_preserved_history():
    """If the refreshed PIT artifact exists, assert §11 on the real files."""
    base = os.path.join(_REPO_ROOT, "regime_dual_engine", "data")
    old_p = os.path.join(base, "breadth_pit_2016_2025.csv")
    new_p = os.path.join(base, "breadth_pit_2016_2025_refreshed.csv")
    if not os.path.exists(new_p):
        _check("real PIT refresh history invariance (skipped — no artifact)",
               True, "run regime_dual_engine/breadth_refresh.py first")
        return
    o = pd.read_csv(old_p, parse_dates=["datetime"]).set_index("datetime")
    n = pd.read_csv(new_p, parse_dates=["datetime"]).set_index("datetime")
    rep = compare_historical(o, n)
    _check(f"real PIT refresh preserved all {rep['n_common']} historical rows",
           rep["identical"] is True, str(rep.get("differences")))
    _check("real PIT refresh APPENDED rows", len(n) > len(o),
           f"{len(o)} -> {len(n)}")
    _check("real PIT refresh extended the tail",
           n.index.max() > o.index.max(),
           f"{o.index.max().date()} -> {n.index.max().date()}")


# ---------------------------------------------------------------------------
# 6. THE decisive one: history is untouched by default (§9)
# ---------------------------------------------------------------------------
def test_policy_is_inert_by_default():
    cfg = ProductionConfig()
    _check("ProductionConfig.freshness_policy defaults to None",
           cfg.freshness_policy is None)
    _check("the backtest path therefore runs unassessed",
           getattr(cfg, "freshness_policy", None) is None)

    # with a policy in place, the pipeline records the assessment
    from production.data_validity import assess as _assess
    pol = FreshnessPolicy(max_age_days=7, temporal_skew_days=3)
    v = _assess(regime_as_of="2024-06-14",
                spy_df=_frame(["2024-06-13", "2024-06-14"]),
                breadth_df=_frame(["2024-06-13", "2024-06-14"]), policy=pol)
    _check("a historical as_of whose inputs match is VALID",
           v.state == VALIDITY_VALID, f"{v.state} {v.reasons}")
    _check("so enabling the policy would NOT block historical replay",
           v.decision_trustworthy is True)


def test_effective_date_handles_both_frame_shapes():
    """REGRESSION: the two Regime inputs arrive with DIFFERENT frame shapes.

    OHLCV (the SPY/HMM input) is a RangeIndex + a `datetime` COLUMN; breadth is a
    DatetimeIndex. An earlier version of this module read only the index, so a
    current SPY frame was read as 1970-01-01 and reported ~20,700 days stale —
    i.e. the freshness check would have been actively wrong in production.
    """
    from production.data_validity import effective_date, pd_index_max

    # shape 1: breadth-style DatetimeIndex
    f_idx = _frame(["2026-08-05", "2026-08-06"])
    _check("DatetimeIndex frame -> correct date",
           effective_date(f_idx) == "2026-08-06", str(effective_date(f_idx)))

    # shape 2: OHLCV-style RangeIndex + datetime column (the real SPY shape)
    f_col = pd.DataFrame({
        "datetime": pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06"]),
        "close": [1.0, 2.0, 3.0],
    })
    _check("RangeIndex + datetime column -> correct date",
           effective_date(f_col) == "2026-08-06", str(effective_date(f_col)))
    _check("OHLCV frame does NOT resolve to an epoch date",
           not str(effective_date(f_col)).startswith("1970"))

    # shape 3: neither -> None (never a guess)
    f_none = pd.DataFrame({"value": [1, 2, 3]})
    _check("no date anywhere -> None (no fabrication)",
           effective_date(f_none) is None)
    try:
        pd_index_max(f_none)
        _check("pd_index_max raises when the date is undeterminable", False,
               "it should have raised")
    except ValueError:
        _check("pd_index_max raises when the date is undeterminable", True)

    # end-to-end: a current SPY + current breadth must be VALID, not STALE
    pol = FreshnessPolicy(max_age_days=7, temporal_skew_days=3)
    v = assess(regime_as_of="2026-08-06", spy_df=f_col, breadth_df=f_idx,
               policy=pol)
    _check("real-shaped frames both current -> VALID", v.state == VALIDITY_VALID,
           f"{v.state} {v.reasons}")
    _check("SPY age is 0, not 20000+", v.spy_age_days == 0,
           f"got {v.spy_age_days}")


def test_no_strategy_surface_touched():
    """The data-validity layer must not be able to reach a trading parameter."""
    import ast
    src = open(os.path.join(_REPO_ROOT, "production", "data_validity.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imported.add(base)
            imported |= {f"{base}.{a.name}" for a in node.names}
    banned = {"size_position", "evaluate_setup", "exit_check", "_exit_check",
              "build_order_buy", "compute_regime_decision", "run_daily",
              "regime_dual", "breadth_engine"}
    leaked = sorted(banned & imported)
    _check("data_validity imports no decision rule (AST-verified)",
           not leaked, f"leaked={leaked}")
    forbidden = ("stop_atr_mult", "take_profit_atr_mult", "trailing_atr_mult",
                 "trailing_trigger_r", "risk_per_trade", "max_open_positions",
                 "position_size_mult", "bull_threshold", "bear_threshold",
                 "divergence_cap", "exit_engine_mode", "setup_enabled_types")
    hits = [f for f in forbidden if f in src]
    _check("data_validity names no frozen strategy parameter", not hits,
           f"found={hits}")

    cfg = ProductionConfig()
    for f, expected in (("stop_atr_mult", 1.5), ("take_profit_atr_mult", 2.5),
                        ("trailing_atr_mult", 1.5), ("trailing_trigger_r", 1.0),
                        ("risk_per_trade", 0.01), ("max_open_positions", 5),
                        ("max_holding_days", 30), ("max_position_pct", 0.25),
                        ("slippage_pct", 0.0005), ("setup_score_threshold", 0.5),
                        ("exit_engine_mode", "legacy")):
        _check(f"frozen `{f}` is still {expected}",
               getattr(cfg, f) == expected)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"=== BS breadth-freshness acceptance ({len(tests)} groups) ===\n")
    for t in tests:
        print(f"-- {t.__name__}")
        t()
        print()
    passed = sum(1 for _, ok, _ in _R if ok)
    print(f"=== {passed}/{len(_R)} assertions passed ===")
    for n, d in [(n, d) for n, ok, d in _R if not ok]:
        print(f"  FAIL {n}: {d}")
    return 0 if passed == len(_R) else 1


if __name__ == "__main__":
    sys.exit(main())
