"""
tests/test_contracts.py — Phase 0 contract validation tests

Plain-script runner (repository convention: no pytest — architecture decision 8).
Run:  python production/tests/test_contracts.py

Verifies each contract's deterministic validation, the unit/session semantics
boundaries, and the deliberate phase boundaries (e.g. EntryDecision carries no
share count).
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.contracts import (ContractError, EntryDecision, ExecutionWindow,
                                  OrderIntent, PositionState, Provenance,
                                  RiskDecision, StopPlan, entry_decision)
from production.contracts.reason_codes import (LONG, SHORT,
                                               RISK_REASON_CODES,
                                               STOP_REASON_CODES)


def _expect(fn, field: str):
    try:
        fn()
    except ContractError as exc:
        assert exc.field_name == field, \
            f"expected failure on {field!r}, got {exc.field_name!r} ({exc.detail})"
        return exc
    raise AssertionError(f"expected ContractError on {field!r} — none raised")


def _prov():
    return Provenance.engine("test", "unit-test", session_date="2026-09-28")


# ---------------------------------------------------------------------------
# Provenance / ExecutionWindow
# ---------------------------------------------------------------------------
def test_provenance_validation():
    _prov().validate("t")
    _expect(lambda: Provenance(producer="", source="x").validate("t"), "producer")
    _expect(lambda: Provenance(producer="p", source="x",
                               session_date="28-09-2026").validate("t"),
            "session_date")


def test_execution_window_validation():
    ExecutionWindow("2026-09-29", "2026-09-29").validate()
    _expect(lambda: ExecutionWindow("2026-09-30", "2026-09-29").validate(),
            "to_session")
    _expect(lambda: ExecutionWindow("2026/09/30", "2026-09-30").validate(),
            "from_session")


# ---------------------------------------------------------------------------
# EntryDecision
# ---------------------------------------------------------------------------
def test_entry_decision_happy_path():
    d = entry_decision(approved=True, ticker="NVDA", setup_type="pullback",
                       signal_date="2026-09-28", signal_price=178.42,
                       entry_score=0.72, entry_reason="pullback nearEMA",
                       execution_window=ExecutionWindow("2026-09-29",
                                                        "2026-09-29"),
                       provenance=_prov())
    assert d.timestamp == "2026-09-28"
    assert d.execution_type == "NEXT_OPEN"
    assert d.expected_execution_price is None      # unknown at signal time
    d.as_dict()


def test_entry_decision_has_no_size_fields():
    """Phase boundary: EntryDecision must not fix shares/quantity."""
    d = entry_decision(approved=True, ticker="NVDA", setup_type="pullback",
                       signal_date="2026-09-28", signal_price=100.0)
    payload = d.as_dict()
    for forbidden in ("shares", "requested_shares", "approved_shares",
                      "quantity", "size"):
        assert forbidden not in payload, f"EntryDecision must not carry {forbidden}"
    assert not hasattr(d, "shares")


def test_entry_decision_rejection_requires_vetoes():
    _expect(lambda: entry_decision(approved=False, ticker="NVDA",
                                   setup_type="pullback",
                                   signal_date="2026-09-28",
                                   signal_price=100.0), "vetoes")
    ok = entry_decision(approved=False, ticker="NVDA", setup_type="pullback",
                        signal_date="2026-09-28", signal_price=100.0,
                        vetoes=("BEAR_REGIME",))
    assert ok.approved is False
    # approval carrying vetoes is contradictory
    _expect(lambda: entry_decision(approved=True, ticker="NVDA",
                                   setup_type="pullback",
                                   signal_date="2026-09-28",
                                   signal_price=100.0, vetoes=("X",)), "vetoes")


def test_entry_decision_field_rules():
    _expect(lambda: entry_decision(approved=True, ticker="", setup_type="pullback",
                                   signal_date="2026-09-28", signal_price=100.0),
            "ticker")
    _expect(lambda: entry_decision(approved=True, ticker="X", setup_type="pullback",
                                   signal_date="2026-09-28", signal_price=0.0),
            "signal_price")
    _expect(lambda: entry_decision(approved=True, ticker="X", setup_type="pullback",
                                   signal_date="2026-09-28", signal_price=100.0,
                                   direction="SIDEWAYS"), "direction")
    _expect(lambda: entry_decision(approved=True, ticker="X", setup_type="pullback",
                                   signal_date="2026-09-28", signal_price=100.0,
                                   entry_score=1.5), "entry_score")
    # execution window must not start before the signal session
    _expect(lambda: EntryDecision(approved=True, ticker="X", direction=LONG,
                                  setup_type="pullback",
                                  signal_date="2026-09-28",
                                  signal_price=100.0,
                                  execution_window=ExecutionWindow(
                                      "2026-09-27", "2026-09-28")).validate(),
            "execution_window")
    _expect(lambda: EntryDecision(approved=True, ticker="X", direction=LONG,
                                  setup_type="pullback",
                                  signal_date="2026-09-28", signal_price=100.0,
                                  execution_type="MARKET").validate(),
            "execution_type")


# ---------------------------------------------------------------------------
# StopPlan
# ---------------------------------------------------------------------------
def test_stop_plan_happy_path_long():
    p = StopPlan(ticker="AAPL", direction=LONG, session_date="2026-09-28",
                 reference_price=180.0, reference_price_source="FILL",
                 initial_stop_price=175.5, current_stop_price=178.0,
                 atr_value=3.0, atr_multiple=1.5,
                 provenance=_prov()).validate()
    assert abs(p.risk_per_share_value - 4.5) < 1e-12
    assert p.risk_r == 4.5
    assert p.timestamp == "2026-09-28"
    assert p.structure_level is None            # reserved, unused in Phase 1
    p.as_dict()


def test_stop_plan_rules():
    base = dict(ticker="AAPL", direction=LONG, session_date="2026-09-28",
                reference_price=180.0, reference_price_source="FILL",
                atr_value=3.0, atr_multiple=1.5)
    # LONG initial stop must be below the reference price
    _expect(lambda: StopPlan(initial_stop_price=181.0,
                             current_stop_price=181.0, **base).validate(),
            "initial_stop_price")
    # LONG current stop may not loosen (below initial)
    _expect(lambda: StopPlan(initial_stop_price=175.0,
                             current_stop_price=174.0, **base).validate(),
            "current_stop_price")
    # ATR method requires ATR inputs
    _expect(lambda: StopPlan(reference_price=180.0, reference_price_source="FILL",
                             ticker="A", direction=LONG,
                             session_date="2026-09-28",
                             initial_stop_price=175.0,
                             current_stop_price=175.0).validate(), "atr_value")
    # reference price source must be one of FILL / EXPECTED / SIGNAL
    _expect(lambda: StopPlan(initial_stop_price=175.0, current_stop_price=175.0,
                             ticker="A", direction=LONG,
                             session_date="2026-09-28", reference_price=180.0,
                             reference_price_source="GUESS",
                             atr_value=3.0, atr_multiple=1.5).validate(),
            "reference_price_source")
    # risk_per_share must equal |reference - initial|
    _expect(lambda: StopPlan(initial_stop_price=175.0, current_stop_price=175.0,
                             risk_per_share=99.0, **base).validate(),
            "risk_per_share")


def test_stop_plan_short_mirror():
    p = StopPlan(ticker="AAPL", direction=SHORT, session_date="2026-09-28",
                 reference_price=180.0, reference_price_source="EXPECTED",
                 initial_stop_price=184.5, current_stop_price=182.0,
                 atr_value=3.0, atr_multiple=1.5).validate()
    assert p.risk_r == 4.5
    _expect(lambda: StopPlan(ticker="A", direction=SHORT,
                             session_date="2026-09-28", reference_price=180.0,
                             reference_price_source="FILL",
                             initial_stop_price=179.0,
                             current_stop_price=179.0, atr_value=3.0,
                             atr_multiple=1.5).validate(), "initial_stop_price")


# ---------------------------------------------------------------------------
# RiskDecision
# ---------------------------------------------------------------------------
def test_risk_decision_statuses():
    approve = RiskDecision(status="APPROVE", ticker="AAPL", direction=LONG,
                           requested_shares=10, approved_shares=10,
                           requested_risk=45.0, approved_risk=45.0,
                           risk_per_share=4.5, reason_code="RISK_APPROVED",
                           session_date="2026-09-28").validate()
    assert approve.decision_id.startswith("risk-")

    resize = RiskDecision(status="RESIZE", ticker="AAPL", direction=LONG,
                          requested_shares=10, approved_shares=4,
                          requested_risk=45.0, approved_risk=18.0,
                          risk_per_share=4.5, reason_code="RISK_RESIZED",
                          constraints_triggered=("max_position_pct",)).validate()
    assert resize.approved_shares < resize.requested_shares

    reject = RiskDecision(status="REJECT", ticker="AAPL", direction=LONG,
                          approved_shares=0, approved_risk=0.0,
                          risk_per_share=4.5, reason_code="RISK_REJECTED").validate()
    assert reject.approved_shares == 0


def test_risk_decision_rules():
    def mk(**over):
        base = dict(status="APPROVE", ticker="AAPL", direction=LONG,
                    requested_shares=10, approved_shares=10, approved_risk=45.0,
                    risk_per_share=4.5)
        base.update(over)
        return RiskDecision(**base).validate()

    _expect(lambda: mk(status="MAYBE"), "status")
    _expect(lambda: mk(approved_shares=9), "approved_shares")
    _expect(lambda: mk(status="RESIZE", approved_shares=10), "approved_shares")
    _expect(lambda: mk(status="RESIZE", requested_shares=None,
                       approved_shares=4), "requested_shares")
    _expect(lambda: mk(status="REJECT", approved_shares=1,
                       approved_risk=45.0), "approved_shares")
    # risk bookkeeping consistency
    _expect(lambda: mk(current_portfolio_risk=100.0,
                       projected_portfolio_risk=140.0), "projected_portfolio_risk")
    _expect(lambda: mk(projected_heat=1.5), "projected_heat")
    _expect(lambda: mk(remaining_capacity=-5.0), "remaining_capacity")
    _expect(lambda: mk(reason_code="NOPE"), "reason_code")


def test_risk_decision_id_is_deterministic():
    p = dict(status="APPROVE", ticker="AAPL", direction=LONG,
             requested_shares=10, approved_shares=10, approved_risk=45.0,
             risk_per_share=4.5, session_date="2026-09-28")
    a = RiskDecision(**p).validate()
    b = RiskDecision(**p).validate()
    c = RiskDecision(**{**p, "approved_shares": 8, "requested_shares": 8,
                        "approved_risk": 36.0}).validate()
    assert a.decision_id == b.decision_id
    assert a.decision_id != c.decision_id


# ---------------------------------------------------------------------------
# OrderIntent
# ---------------------------------------------------------------------------
def test_order_intent_happy_path():
    oi = OrderIntent(ticker="AAPL", direction=LONG, requested_shares=10,
                     approved_shares=4, signal_price=180.0,
                     expected_execution_price=181.2, stop_price=175.5,
                     order_reason="pullback entry, resized by risk",
                     risk_decision_id="risk-abc123", session_date="2026-09-28",
                     reason_code="ENTRY_RESIZED",
                     execution_window=ExecutionWindow("2026-09-29",
                                                      "2026-09-29")).validate()
    assert oi.order_id.startswith("order-")
    # signal / execution / risk stay separate concepts
    assert oi.signal_price != oi.expected_execution_price
    oi.as_dict()


def test_order_intent_rules():
    base = dict(ticker="AAPL", direction=LONG, requested_shares=10,
                approved_shares=4, signal_price=180.0, stop_price=175.5,
                order_reason="r", risk_decision_id="risk-1")
    _expect(lambda: OrderIntent(**{**base, "approved_shares": 11}).validate(),
            "approved_shares")
    _expect(lambda: OrderIntent(**{**base, "approved_shares": 0}).validate(),
            "approved_shares")
    _expect(lambda: OrderIntent(**{**base, "stop_price": 185.0}).validate(),
            "stop_price")           # LONG stop above the execution basis
    _expect(lambda: OrderIntent(**{**base, "signal_price": 0.0}).validate(),
            "signal_price")
    _expect(lambda: OrderIntent(**{**base, "risk_decision_id": ""}).validate(),
            "risk_decision_id")
    _expect(lambda: OrderIntent(**{**base, "order_reason": ""}).validate(),
            "order_reason")
    _expect(lambda: OrderIntent(**{**base,
                                   "expected_execution_type": "CLOSE"}).validate(),
            "expected_execution_type")
    # expected price may legitimately be None (next-open unknown at signal time)
    assert OrderIntent(**base).validate().expected_execution_price is None


def test_order_intent_id_deterministic():
    base = dict(ticker="AAPL", direction=LONG, requested_shares=10,
                approved_shares=4, signal_price=180.0, stop_price=175.5,
                order_reason="r", risk_decision_id="risk-1",
                session_date="2026-09-28")
    assert (OrderIntent(**base).order_id
            == OrderIntent(**base).order_id)
    assert (OrderIntent(**base).order_id
            != OrderIntent(**{**base, "approved_shares": 3}).order_id)


# ---------------------------------------------------------------------------
# PositionState
# ---------------------------------------------------------------------------
def test_position_state_happy_path():
    ps = PositionState(ticker="AAPL", direction=LONG, shares=5,
                       entry_fill_price=180.0, entry_session="2026-09-01",
                       atr_at_entry=3.0, initial_stop_price=175.5,
                       current_stop_price=178.0,
                       highest_price_since_entry=190.0,
                       take_profit_price=187.5, entry_regime="BULL",
                       entry_reason="pullback", stop_reason_code="TRAILING_ATR",
                       stop_updated_session="2026-09-10",
                       provenance=_prov()).validate()
    assert abs(ps.risk_per_share - 4.5) < 1e-12
    assert ps.anchor_price == 190.0
    ps.as_dict()


def test_position_state_rules():
    base = dict(ticker="AAPL", direction=LONG, shares=5, entry_fill_price=180.0,
                entry_session="2026-09-01", atr_at_entry=3.0,
                initial_stop_price=175.5, current_stop_price=178.0)
    _expect(lambda: PositionState(**{**base, "shares": 0}).validate(), "shares")
    _expect(lambda: PositionState(**{**base,
                                     "initial_stop_price": 181.0,
                                     "current_stop_price": 181.0}).validate(),
            "initial_stop_price")
    _expect(lambda: PositionState(**{**base,
                                     "current_stop_price": 170.0}).validate(),
            "current_stop_price")
    _expect(lambda: PositionState(**{**base,
                                     "highest_price_since_entry": 170.0}
                                  ).validate(), "highest_price_since_entry")
    _expect(lambda: PositionState(**{**base,
                                     "take_profit_price": 170.0}).validate(),
            "take_profit_price")
    _expect(lambda: PositionState(**{**base,
                                     "stop_updated_session": "2026-08-31"}
                                  ).validate(), "stop_updated_session")
    _expect(lambda: PositionState(**{**base,
                                     "stop_reason_code": "NOPE"}).validate(),
            "stop_reason_code")
    # SHORT mirror: anchor must be <= entry, TP < entry, no loosening upward
    short = dict(ticker="AAPL", direction=SHORT, shares=5,
                 entry_fill_price=180.0, entry_session="2026-09-01",
                 atr_at_entry=3.0, initial_stop_price=184.5,
                 current_stop_price=182.0, lowest_price_since_entry=170.0,
                 take_profit_price=172.5)
    s = PositionState(**short).validate()
    assert s.anchor_price == 170.0
    _expect(lambda: PositionState(**{**short,
                                     "lowest_price_since_entry": 185.0}
                                  ).validate(), "lowest_price_since_entry")
    _expect(lambda: PositionState(**{**short,
                                     "current_stop_price": 185.0}).validate(),
            "current_stop_price")


def test_reason_code_vocabularies_are_small_and_closed():
    assert "INITIAL_ATR" in STOP_REASON_CODES
    assert "TRAILING_ATR" in STOP_REASON_CODES
    assert "STOP_UNCHANGED" in STOP_REASON_CODES
    assert "NEW_HIGH" in STOP_REASON_CODES
    assert "TRAILING_NOT_ARMED" in STOP_REASON_CODES
    assert "STRUCTURE_STOP" in STOP_REASON_CODES      # reserved for the future
    assert len(STOP_REASON_CODES) <= 10, "avoid duplicated reason codes"
    assert "RISK_APPROVED" in RISK_REASON_CODES


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_")]
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
