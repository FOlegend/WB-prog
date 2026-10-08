"""
replay_exit_parity.py — trade-level replay parity (Phase 2 + Phase 3)

Samples REAL historical bars from a recorded backtest trade log and, for every
bar of every trade, compares:

    legacy  src/portfolio/portfolio_manager.py::_exit_check
    vs
    new     production/exits/engine.py::evaluate_exit

on four fields: should_exit · exit_reason_code · exit_price · fill_model.

Every divergence is reported (ticker, session, both outcomes, the inputs).

Two passes (Phase 3 adds the second one — no duplicate framework):

  PASS A (direct)  : the input contracts are built field by field, exactly as
                     Phase 2 did.
  PASS B (adapter) : the SAME legacy position dict + bar go through the
                     production adapter (`production/exits/adapter.py`), i.e.
                     dict -> PositionState -> StopPlan (via the Stop Engine)
                     -> ExitContext. This proves the Phase-3 translation layer
                     is faithful on real data, and that the adapter path yields
                     the same decision as the direct path.

How the inputs are reproduced (deterministic, no randomness):
  * bars        -> the real OHLCV cache used by the backtest (CachedSource dir)
  * highest/lows-> the same running-close anchor the engine used. Timing matters:
                   production/pipeline.py evaluates exits FIRST and only then
                   marks to market, so the exit check on session D sees the
                   anchor as of session D-1's close. This harness reproduces
                   that order exactly (originally it updated the anchor first,
                   which made trailing fire one session early — a harness bug,
                   not an engine one).
  * tech_signal -> produced by the SAME function the production pipeline calls
                   (src.agents.technicals_agent.technicals_signal)
  * stop / target / atr -> taken from the recorded trade itself
  * StopPlan    -> produced by the real Stop Engine (initial_stop_plan +
                   update_stop), so the Exit Engine never computes a level

Run:
  python production/tests/replay_exit_parity.py
  python production/tests/replay_exit_parity.py --max-trades 40
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.contracts.base import PriceBar
from production.contracts.position import PositionState
from production.contracts.stop import StopPlan
from production.exits import build_exit_context, evaluate_exit
from production.exits.adapter import adapt_exit_inputs
from production.stops import initial_stop_plan, update_stop
from src.agents.technicals_agent import technicals_signal
from src.portfolio.portfolio_manager import _exit_check

DEFAULT_TRADE_LOG = os.path.join(_REPO_ROOT, "reports",
                                 "production_bt_2024-01-01_2025-07-31.json")
REPORT_PATH = os.path.join(_REPO_ROOT, "reports",
                           "phase3_exit_parity_replay_2026-09-30.json")


_BARS_CACHE: dict[str, object] = {}


def _load_bars(cfg, ticker: str):
    """Full cached OHLCV for one ticker (read-only, no network, memoised)."""
    if ticker in _BARS_CACHE:
        return _BARS_CACHE[ticker]
    path = os.path.join(cfg.cache_dir, f"{ticker}.csv")
    if not os.path.exists(path):
        _BARS_CACHE[ticker] = None
        return None
    import pandas as pd
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    _BARS_CACHE[ticker] = df
    return df


def _legacy_pos(trade: dict, anchor: float) -> dict:
    return {
        "ticker": trade["ticker"], "direction": trade.get("direction", "LONG"),
        "shares": trade.get("shares", 10),
        "entry_price": float(trade["entry_price"]),
        "entry_date": trade["entry_date"],
        "atr_at_entry": float(trade.get("atr_at_entry") or 0.0),
        "stop_price": float(trade["stop_price"]),
        "take_profit": float(trade["target_price"]),
        "highest_since_entry": float(anchor),
        "entry_regime": trade.get("entry_regime", "UNKNOWN"),
        "entry_reasoning": trade.get("entry_reason", ""),
    }


def _compare(legacy: dict | None, decision) -> tuple[bool, dict]:
    """Compare the four required fields; return (agree, legacy_view)."""
    legacy_view = {"should_exit": legacy is not None}
    if legacy is not None:
        legacy_view.update({"exit_reason_code": legacy["reason"],
                            "exit_price": legacy["exit_price"],
                            "fill_model": legacy["fill_model"]})
    else:
        legacy_view.update({"exit_reason_code": None, "exit_price": None,
                            "fill_model": None})

    new_view = {"should_exit": decision.should_exit,
                "exit_reason_code": decision.exit_reason_code,
                "exit_price": decision.exit_price,
                "fill_model": decision.fill_model}

    agree = (legacy_view["should_exit"] == new_view["should_exit"])
    if agree and legacy_view["should_exit"]:
        agree = (legacy_view["exit_reason_code"] == new_view["exit_reason_code"]
                 and legacy_view["fill_model"] == new_view["fill_model"]
                 and abs(legacy_view["exit_price"] - new_view["exit_price"]) <= 1e-9)
    return agree, {"legacy": legacy_view, "new": new_view}


def _same_decision(a, b) -> bool:
    """Field equality between two ExitDecisions (tolerance 1e-9)."""
    if a is None or b is None:
        return a is b
    if a.should_exit != b.should_exit:
        return False
    if not a.should_exit:
        return True
    return (a.exit_reason_code == b.exit_reason_code
            and a.fill_model == b.fill_model
            and a.exit_price is not None and b.exit_price is not None
            and abs(a.exit_price - b.exit_price) <= 1e-9)


def replay(cfg=None, trade_log_path: str = DEFAULT_TRADE_LOG,
           max_trades: int | None = None, verbose: bool = False) -> dict:
    cfg = cfg or ProductionConfig()
    with open(trade_log_path) as f:
        payload = json.load(f)
    trades = payload.get("trade_log", [])
    if max_trades:
        trades = trades[:max_trades]

    stats = {"trades_replayed": 0, "trades_skipped": 0, "bars_compared": 0,
             "divergences": 0, "reason_counts_legacy": {},
             "reason_counts_new": {}, "unexplained": [],
             # ---- Phase 3: the same comparison through the production adapter
             "adapter_bars_compared": 0, "adapter_divergences": 0,
             "adapter_reason_counts_new": {}, "adapter_unexplained": [],
             "adapter_errors": 0, "adapter_error_detail": [],
             "adapter_vs_direct_mismatches": 0,
             "adapter_vs_direct": []}
    skipped: list[dict] = []
    reconciliations: list[dict] = []

    for trade in trades:
        ticker = trade["ticker"]
        bars = _load_bars(cfg, ticker)
        if bars is None:
            stats["trades_skipped"] += 1
            skipped.append({"ticker": ticker, "reason": "no cached OHLCV"})
            continue

        entry_date, exit_date = trade["entry_date"], trade["exit_date"]
        if not exit_date:
            stats["trades_skipped"] += 1
            skipped.append({"ticker": ticker, "reason": "no exit_date"})
            continue

        window = bars[(bars["datetime"] >= entry_date)
                      & (bars["datetime"] <= exit_date)]
        if len(window) == 0:
            stats["trades_skipped"] += 1
            skipped.append({"ticker": ticker, "reason": "no bars in window"})
            continue

        entry_price = float(trade["entry_price"])
        atr_at_entry = float(trade.get("atr_at_entry") or 0.0)
        stop_price = float(trade["stop_price"])
        target_price = float(trade["target_price"])
        anchor = entry_price                      # open_position seeds the anchor
        first_exit = None
        stats["trades_replayed"] += 1

        plan0 = initial_stop_plan(
            ticker=ticker, session_date=entry_date, reference_price=entry_price,
            reference_price_source="FILL", atr_value=atr_at_entry,
            atr_multiple=cfg.stop_atr_mult)
        # sanity: the recorded stop came from the same formula (the record is
        # rounded to 4 dp by the sizing layer, hence the loose tolerance)
        assert abs(plan0.initial_stop_price - stop_price) <= 1e-3, (
            f"{ticker}: StopPlan initial {plan0.initial_stop_price} != recorded "
            f"stop {stop_price}")
        # The replayed plan carries the RECORDED stop level (exact price parity
        # with the legacy position) while keeping the configured ATR multiple
        # (exact trigger-distance parity with cfg.stop_atr_mult).
        plan0 = StopPlan(
            ticker=ticker, direction="LONG", session_date=entry_date,
            reference_price=entry_price, reference_price_source="FILL",
            initial_stop_price=stop_price, current_stop_price=stop_price,
            atr_value=atr_at_entry, atr_multiple=cfg.stop_atr_mult,
            buffer=None, risk_per_share=None,
            reason_code=plan0.reason_code, rationale=plan0.rationale,
            provenance=plan0.provenance).validate()

        for _, row in window.iterrows():
            session = row["datetime"].strftime("%Y-%m-%d")
            try:
                bar = PriceBar(open=float(row["open"]), high=float(row["high"]),
                               low=float(row["low"]),
                               close=float(row["close"])).validate()
            except Exception as exc:
                # real cached data can contain a malformed bar; the contract
                # fails loud rather than fabricating OHLC — record and skip
                stats.setdefault("bars_skipped_bad_ohlc", 0)
                stats["bars_skipped_bad_ohlc"] += 1
                stats.setdefault("skipped_bars_detail", []).append(
                    {"ticker": ticker, "session": session, "error": str(exc)})
                continue

            slice_df = bars[bars["datetime"] <= row["datetime"]]
            try:
                tech = technicals_signal(slice_df, cfg)["signal"]
            except Exception:
                tech = "neutral"

            legacy_pos = _legacy_pos(trade, anchor)
            legacy = _exit_check(legacy_pos, bar.as_dict(), session, tech, cfg)

            position = PositionState(
                ticker=ticker, direction="LONG", shares=trade.get("shares", 10),
                entry_fill_price=entry_price, entry_session=entry_date,
                atr_at_entry=atr_at_entry, initial_stop_price=stop_price,
                current_stop_price=stop_price,
                highest_price_since_entry=anchor,
                take_profit_price=target_price,
                entry_regime=trade.get("entry_regime", "UNKNOWN"),
                entry_reason=trade.get("entry_reason", "")).validate()

            update = update_stop(
                plan0, entry_fill_price=entry_price, anchor_price=anchor,
                trailing_atr_multiple=cfg.trailing_atr_mult,
                trailing_trigger_r=cfg.trailing_trigger_r,
                session_date=session)

            ctx = build_exit_context(
                position=position, stop_plan=update.plan, bar=bar,
                tech_signal=tech, session_date=session,
                max_holding_days=cfg.max_holding_days,
                trailing_armed=update.evaluation.armed)
            decision = evaluate_exit(ctx)

            # ---- PASS B (Phase 3): the SAME inputs through the adapter ----
            stats["adapter_bars_compared"] += 1
            try:
                adapted = adapt_exit_inputs(legacy_pos, bar.as_dict(), tech,
                                            session, cfg)
                decision_adapter = evaluate_exit(adapted.context)
            except Exception as exc:
                decision_adapter = None
                stats["adapter_errors"] += 1
                stats["adapter_error_detail"].append(
                    {"ticker": ticker, "session": session,
                     "error": f"{type(exc).__name__}: {exc}"})
            if decision_adapter is not None:
                if decision_adapter.should_exit:
                    key = decision_adapter.exit_reason_code
                    stats["adapter_reason_counts_new"][key] = \
                        stats["adapter_reason_counts_new"].get(key, 0) + 1
                agree_b, views_b = _compare(legacy, decision_adapter)
                if not agree_b:
                    stats["adapter_divergences"] += 1
                    stats["adapter_unexplained"].append({
                        "ticker": ticker, "session": session,
                        "legacy": views_b["legacy"], "new": views_b["new"],
                        "inputs": {"bar": bar.as_dict(), "anchor": anchor,
                                   "tech_signal": tech}})
                # adapter fidelity: dict -> contracts must not change the answer
                if not _same_decision(decision, decision_adapter):
                    stats["adapter_vs_direct_mismatches"] += 1
                    stats["adapter_vs_direct"].append({
                        "ticker": ticker, "session": session,
                        "direct": {"should_exit": decision.should_exit,
                                   "reason": decision.exit_reason_code,
                                   "price": decision.exit_price,
                                   "fill": decision.fill_model},
                        "adapter": {"should_exit": decision_adapter.should_exit,
                                    "reason": decision_adapter.exit_reason_code,
                                    "price": decision_adapter.exit_price,
                                    "fill": decision_adapter.fill_model}})

            stats["bars_compared"] += 1
            if legacy is not None:
                key = legacy["reason"]
                stats["reason_counts_legacy"][key] = \
                    stats["reason_counts_legacy"].get(key, 0) + 1
            if decision.should_exit:
                key = decision.exit_reason_code
                stats["reason_counts_new"][key] = \
                    stats["reason_counts_new"].get(key, 0) + 1

            agree, views = _compare(legacy, decision)
            if not agree:
                stats["divergences"] += 1
                stats["unexplained"].append({
                    "ticker": ticker, "session": session,
                    "legacy": views["legacy"], "new": views["new"],
                    "inputs": {"bar": bar.as_dict(), "anchor": anchor,
                               "tech_signal": tech,
                               "stop_plan_initial": update.plan.initial_stop_price,
                               "stop_plan_current": update.plan.current_stop_price,
                               "trailing_armed": update.evaluation.armed,
                               "atr_at_entry": atr_at_entry,
                               "target": target_price}})
            if first_exit is None and (legacy is not None or decision.should_exit):
                first_exit = {"session": session,
                              "legacy": views["legacy"], "new": views["new"]}

            # mark to market AFTER the exit check (production/pipeline.py order):
            # session D's close only feeds the anchor from D+1 onwards
            anchor = max(anchor, bar.close)

        reconciliations.append({
            "ticker": ticker, "recorded_exit_date": exit_date,
            "recorded_exit_reason": trade.get("exit_reason"),
            "recorded_exit_price": trade.get("exit_price"),
            "replayed_first_exit": first_exit})

    matches = sum(1 for r in reconciliations
                  if r["replayed_first_exit"]
                  and r["replayed_first_exit"]["legacy"]["exit_reason_code"]
                  == r["recorded_exit_reason"])
    stats["trades_with_legacy_first_exit_matching_record"] = matches
    stats["reconciliation"] = reconciliations
    stats["skipped"] = skipped
    stats["trade_log"] = os.path.basename(trade_log_path)
    stats["config"] = {"stop_atr_mult": cfg.stop_atr_mult,
                       "trailing_atr_mult": cfg.trailing_atr_mult,
                       "trailing_trigger_r": cfg.trailing_trigger_r,
                       "max_holding_days": cfg.max_holding_days}
    if verbose:
        print(json.dumps({k: v for k, v in stats.items()
                          if k not in ("reconciliation", "unexplained",
                                       "skipped")}, indent=2))
    return stats


def main():
    ap = argparse.ArgumentParser(description="Trade-level exit parity replay")
    ap.add_argument("--trade-log", default=DEFAULT_TRADE_LOG)
    ap.add_argument("--max-trades", type=int, default=None)
    ap.add_argument("--out", default=REPORT_PATH)
    args = ap.parse_args()

    print("=== Exit parity: trade-level replay (Phase 2 direct + Phase 3 adapter) ===")
    stats = replay(trade_log_path=args.trade_log, max_trades=args.max_trades,
                   verbose=True)

    print(f"\nbars compared        : {stats['bars_compared']}")
    print(f"trades replayed      : {stats['trades_replayed']}"
          f"  (skipped {stats['trades_skipped']})")
    print(f"exit events legacy   : {stats['reason_counts_legacy']}")
    print(f"exit events new      : {stats['reason_counts_new']}")
    print(f"DIVERGENCES          : {stats['divergences']}")
    if stats["divergences"]:
        print("  first divergences:")
        for d in stats["unexplained"][:5]:
            print(f"   - {d['ticker']} {d['session']}: legacy={d['legacy']} "
                  f"new={d['new']}")

    print("\n--- PASS B: through the production adapter ---")
    print(f"bars compared        : {stats['adapter_bars_compared']}")
    print(f"exit events new      : {stats['adapter_reason_counts_new']}")
    print(f"DIVERGENCES          : {stats['adapter_divergences']}")
    print(f"adapter errors       : {stats['adapter_errors']}")
    print(f"adapter != direct    : {stats['adapter_vs_direct_mismatches']}")
    for d in stats["adapter_unexplained"][:5]:
        print(f"   - DIVERGENCE {d['ticker']} {d['session']}: "
              f"legacy={d['legacy']} new={d['new']}")

    ok = (stats["divergences"] == 0 and stats["adapter_divergences"] == 0
          and stats["adapter_errors"] == 0
          and stats["adapter_vs_direct_mismatches"] == 0)
    print(f"\nRESULT               : {'PASS' if ok else 'FAIL'}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(stats, f, indent=2, default=str)
    print(f"report -> {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
