"""
phase5_step0_marginal_cohort.py — Step-0 VALUE measurement (read-only)

Question this answers: **the 505 valid setups that the risk gate rejected for
"risk budget insufficient for one share" — if the budget had allowed them, would
they have made money?**

This is Phase-5 plan §2 Step 0. It is a READ-ONLY diagnostic:

  * no parameter, config or production path is changed;
  * no alternative strategy is implemented — the hypothetical trades are
    evaluated with the FROZEN, parity-verified Exit Engine
    (`production/exits`) through the Phase-3 adapter, i.e. the same code that
    production would run;
  * the accepted cohort is re-measured with the SAME machinery first, as an
    internal control: if the machinery cannot reproduce the 151 realised trades,
    its output for the marginal cohort is not trustworthy.

Fidelity notes (each one is a disclosed deviation, not a hidden one):
  * entry = next session's OPEN after the signal date (frozen next-open
    convention), with the frozen gap filter `max_entry_gap_pct` applied.
  * `max_extension_from_pivot_pct` is NOT applied: the prior_high20 it needs is
    not persisted in the DecisionRecord. Empirically harmless — the real run's
    only fill rejections were GAP_TOO_HIGH (5).
  * stop/target geometry is the frozen one (ATR × stop_atr_mult / ×
    take_profit_atr_mult) and the exit decision comes from the frozen Exit
    Engine, including trailing, priority order and the calendar-day time stop.
  * tech_signal is produced by the same function the pipeline calls.

Output: reports/phase5_step0_marginal_cohort_2026-10-01.json

Run:
  python production/tests/phase5_step0_marginal_cohort.py
"""
from __future__ import annotations

import collections
import json
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import production.backtest as backtest_mod
from production.backtest import ProductionBacktest
from production.config import ProductionConfig
from production.contracts.base import PriceBar
from production.contracts.position import PositionState
from production.contracts.stop import StopPlan
from production.datasource import build_cached_source
from production.exits import build_exit_context, evaluate_exit
from production.stops import initial_stop_plan, update_stop
from src.agents.technicals_agent import technicals_signal

START, END = "2024-01-01", "2025-07-31"
OUT = os.path.join(_REPO_ROOT, "reports",
                   "phase5_step0_marginal_cohort_2026-10-01.json")
HORIZON_CAL_DAYS = 30

_BARS: dict = {}


def _bars(cfg, ticker):
    if ticker in _BARS:
        return _BARS[ticker]
    path = os.path.join(cfg.cache_dir, f"{ticker}.csv")
    if not os.path.exists(path):
        _BARS[ticker] = None
        return None
    import pandas as pd
    _BARS[ticker] = pd.read_csv(path, parse_dates=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)
    return _BARS[ticker]


def _run_capturing(cfg, source):
    captured = []
    original = backtest_mod.run_daily

    def spy(*a, **kw):
        rec = original(*a, **kw)
        captured.append(rec)
        return rec

    backtest_mod.run_daily = spy
    try:
        bt = ProductionBacktest(cfg, source, START, END, verbose=False)
        cache = os.path.join(cfg.reports_dir,
                             f"production_buckets_{START}_{END}_top{cfg.screener_top_n}.json")
        res = bt.run(bucket_cache_file=cache)
    finally:
        backtest_mod.run_daily = original
    return res, captured


# ---------------------------------------------------------------------------
# the simulation kernel — frozen exit semantics, no new logic
# ---------------------------------------------------------------------------
def simulate_trade(cfg, ticker, signal_date, signal_close, atr,
                   apply_gap_filter=True):
    """Entry = next open; then the frozen Exit Engine runs the position.

    Returns a dict with the outcome in R units, or a skip record.
    """
    df = _bars(cfg, ticker)
    if df is None or atr is None or atr <= 0 or signal_close is None:
        return {"skipped": "no_data"}

    after = df[df["datetime"] > signal_date]
    if len(after) == 0:
        return {"skipped": "no_next_bar"}
    entry_row = after.iloc[0]
    entry_date = entry_row["datetime"].strftime("%Y-%m-%d")
    entry = float(entry_row["open"])
    gap = entry / float(signal_close) - 1.0
    if apply_gap_filter and gap > cfg.max_entry_gap_pct:
        return {"skipped": "GAP_TOO_HIGH", "gap_pct": round(gap, 5)}

    stop_distance = atr * cfg.stop_atr_mult
    initial_stop = entry - stop_distance
    if initial_stop <= 0:
        return {"skipped": "invalid_stop"}
    take_profit = entry + atr * cfg.take_profit_atr_mult

    plan = initial_stop_plan(
        ticker=ticker, session_date=entry_date, reference_price=entry,
        reference_price_source="FILL", atr_value=atr,
        atr_multiple=cfg.stop_atr_mult)
    plan = StopPlan(ticker=ticker, direction="LONG", session_date=entry_date,
                    reference_price=entry, reference_price_source="FILL",
                    initial_stop_price=initial_stop,
                    current_stop_price=initial_stop, atr_value=atr,
                    atr_multiple=cfg.stop_atr_mult, buffer=None,
                    risk_per_share=None, reason_code=plan.reason_code,
                    rationale=plan.rationale, provenance=plan.provenance).validate()

    window = df[(df["datetime"] >= entry_date)]
    anchor = entry                      # state.open_position seeds the anchor
    prev_anchor = None
    for _, row in window.iterrows():
        session = row["datetime"].strftime("%Y-%m-%d")
        held = (row["datetime"] - entry_row["datetime"]).days
        if held > HORIZON_CAL_DAYS:     # safety; TIME_STOP fires first
            break
        try:
            bar = PriceBar(open=float(row["open"]), high=float(row["high"]),
                           low=float(row["low"]),
                           close=float(row["close"])).validate()
        except Exception:
            continue
        slice_df = df[df["datetime"] <= row["datetime"]]
        try:
            tech = technicals_signal(slice_df, cfg)["signal"]
        except Exception:
            tech = "neutral"

        update = update_stop(plan, entry_fill_price=entry, anchor_price=anchor,
                             trailing_atr_multiple=cfg.trailing_atr_mult,
                             trailing_trigger_r=cfg.trailing_trigger_r,
                             previous_anchor_price=prev_anchor,
                             session_date=session)
        # assemble through the same contracts the Phase-3 adapter produces
        position = PositionState(
            ticker=ticker, direction="LONG", shares=1,
            entry_fill_price=entry, entry_session=entry_date,
            atr_at_entry=atr, initial_stop_price=initial_stop,
            current_stop_price=update.plan.current_stop_price,
            highest_price_since_entry=anchor, take_profit_price=take_profit,
            stop_reason_code=update.reason_code,
            stop_updated_session=session).validate()
        context = build_exit_context(
            position=position, stop_plan=update.plan, bar=bar,
            tech_signal=tech, session_date=session,
            max_holding_days=cfg.max_holding_days,
            trailing_armed=update.evaluation.armed)
        decision = evaluate_exit(context)
        if decision.should_exit:
            r = (decision.exit_price - entry) / stop_distance
            return {"exit_date": session, "reason": decision.exit_reason_code,
                    "fill_model": decision.fill_model,
                    "exit_price": round(float(decision.exit_price), 4),
                    "r": round(r, 4), "gap_pct": round(gap, 5),
                    "held_cal_days": held,
                    "current_stop_at_exit": round(update.plan.current_stop_price, 4),
                    "trailing_armed": bool(update.evaluation.armed)}
        # mark-to-market AFTER the exit check (production order)
        prev_anchor = anchor
        anchor = max(anchor, float(row["close"]))
    return {"skipped": "no_exit_within_horizon"}


def _summ(rs):
    rs = [r for r in rs if r is not None]
    if not rs:
        return {"n": 0}
    return {"n": len(rs), "avg_r": round(stats.mean(rs), 4),
            "median_r": round(stats.median(rs), 4),
            "sum_r": round(sum(rs), 3),
            "win_rate_pct": round(100.0 * sum(1 for r in rs if r > 0) / len(rs), 2),
            "share_ge_1r_pct": round(100.0 * sum(1 for r in rs if r >= 1.0)
                                     / len(rs), 2),
            "share_le_minus1r_pct": round(100.0 * sum(1 for r in rs if r <= -1.0)
                                          / len(rs), 2)}


# ---------------------------------------------------------------------------
# validation control: does the machinery reproduce the REALISED trades?
# ---------------------------------------------------------------------------
def validate_on_accepted(cfg, trades):
    rows = []
    for t in trades:
        df = _bars(cfg, t["ticker"])
        if df is None:
            continue
        entry = float(t["entry_price"])
        atr = float(t["atr_at_entry"] or 0)
        stop_distance = entry - float(t["stop_price"])
        if stop_distance <= 0 or atr <= 0:
            continue
        # replay from the day AFTER the signal (the entry bar itself is skipped:
        # the position opens at that bar's open, so exits start from that bar's
        # own high/low — handled by starting the window at the entry bar)
        sim = simulate_trade(cfg, t["ticker"], t["signal_date"],
                             float(t["signal_close"]) if t.get("signal_close")
                             else entry, atr, apply_gap_filter=False)
        rows.append({"ticker": t["ticker"], "recorded_r": t.get("r_multiple"),
                     "recorded_reason": t.get("exit_reason"),
                     "recorded_exit_date": t.get("exit_date"),
                     "sim_r": sim.get("r"), "sim_reason": sim.get("reason"),
                     "sim_exit_date": sim.get("exit_date"),
                     "skipped": sim.get("skipped"),
                     "r_abs_diff": (abs(t["r_multiple"] - sim["r"])
                                    if sim.get("r") is not None
                                    and t.get("r_multiple") is not None else None)})
    same_reason = sum(1 for r in rows
                      if r["sim_reason"] and r["sim_reason"] == r["recorded_reason"])
    near = sum(1 for r in rows
               if r["r_abs_diff"] is not None and r["r_abs_diff"] <= 0.05)
    return {
        "rows": len(rows),
        "same_exit_reason": same_reason,
        "same_exit_reason_pct": round(100.0 * same_reason / len(rows), 2) if rows else None,
        "r_within_0.05": near,
        "r_within_0.05_pct": round(100.0 * near / len(rows), 2) if rows else None,
        "note": "the accepted cohort is re-measured with the SAME machinery; the "
                "marginal-cohort result is only credible if this control passes",
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    cfg = ProductionConfig()
    source = build_cached_source(cfg)
    print(f"=== Step 0: marginal-cohort value measurement ({START}..{END}) ===")
    res, records = _run_capturing(cfg, source)
    trades = res["trade_log"]

    # ---- validation control on the accepted cohort ----
    control = validate_on_accepted(cfg, trades)
    print(f"  control (accepted cohort): reason match "
          f"{control['same_exit_reason']}/{control['rows']} "
          f"({control['same_exit_reason_pct']}%) | R within 0.05: "
          f"{control['r_within_0.05']}/{control['rows']} "
          f"({control['r_within_0.05_pct']}%)")

    # ---- collect the marginal cohort ----
    eq_curve = res["equity_curve"]
    prev_state = {}
    for i, e in enumerate(eq_curve):
        j = i - 1 if i else 0
        prev_state[e["date"]] = (eq_curve[j]["equity"], eq_curve[j]["cash"],
                                 e["date"])
    marginal = []
    for rec in records:
        ev_by = {e["ticker"]: e for e in rec["setup"]["evaluations"]}
        regime = (rec["regime"]["output"] or {}).get("regime_label")
        size_mult = (rec["regime"]["output"] or {}).get("position_size_mult")
        eq_prev, cash_prev, _ = prev_state.get(rec["as_of"], (None, None, None))
        for s in rec["risk"]["sizing"]:
            if s.get("allow"):
                continue
            ev = ev_by.get(s["ticker"], {})
            if not ev.get("valid"):
                continue
            marginal.append({
                "signal_date": rec["as_of"], "ticker": s["ticker"],
                "signal_close": ev.get("signal_close"), "atr": ev.get("atr"),
                "setup_score": ev.get("setup_score"),
                "quality": ev.get("setup_quality_mult"),
                "regime": regime, "size_mult_at_signal": size_mult,
                "equity_prev": eq_prev, "cash_prev": cash_prev})

    results = []
    for m in marginal:
        sim = simulate_trade(cfg, m["ticker"], m["signal_date"],
                             m["signal_close"], m["atr"])
        results.append({**m, **sim})

    done = [r for r in results if r.get("r") is not None]
    skipped = collections.Counter(r.get("skipped") for r in results
                                 if r.get("skipped"))
    reasons = collections.Counter(r.get("reason") for r in done)

    # de-duplicate: same ticker inside the same bucket month = one opportunity
    seen = set()
    uniq = []
    for r in sorted(results, key=lambda x: x["signal_date"]):
        key = (r["ticker"], r["signal_date"][:7])
        if key in seen or r.get("r") is None:
            continue
        seen.add(key)
        uniq.append(r)

    by_regime = {reg: _summ([r["r"] for r in done if r["regime"] == reg])
                 for reg in sorted({r["regime"] for r in done})}
    by_reason = {k: _summ([r["r"] for r in done if r.get("reason") == k])
                 for k in sorted(set(reasons))}
    # price bands (cheap vs expensive stocks -> integer-share pressure)
    bands = [("<$50", -9, 50), ("$50-150", 50, 150), ("$150-400", 150, 400),
             (">$400", 400, 1e9)]
    by_band = {}
    for label, lo, hi in bands:
        by_band[label] = _summ([r["r"] for r in done
                                if lo <= (r["signal_close"] or -9) < hi])

    accepted = _summ([t.get("r_multiple") for t in trades])
    out = {
        "generated": "2026-10-01",
        "step": "Phase-5 Step 0 — marginal-cohort value measurement (read-only)",
        "method": ("accepted cohort re-measured as a control; then each rejected "
                   "valid setup is traded hypothetically with next-open entry and "
                   "the FROZEN Exit Engine; no parameter, config or production path "
                   "changed"),
        "control_on_accepted_cohort": control,
        "accepted_cohort": accepted,
        "marginal_cohort": {
            "rejected_valid_setups": len(marginal),
            "simulated": len(done),
            "skipped": dict(skipped),
            "exit_reasons": dict(reasons),
            "overall": _summ([r["r"] for r in done]),
            "dedup_first_per_ticker_month": {
                "n": len(uniq), "overall": _summ([r["r"] for r in uniq])},
            "by_regime_at_signal": by_regime,
            "by_exit_reason": by_reason,
            "by_price_band": by_band,
            "sample": [{k: r.get(k) for k in
                        ("signal_date", "ticker", "regime", "signal_close", "r",
                         "reason", "held_cal_days")} for r in done[:15]],
            # full per-setup rows so lever-specific cohorts can be reconstructed
            # without re-running anything (see phase5_step0b_lever_cohorts.py)
            "rows": results,
        },
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)

    mc = out["marginal_cohort"]
    print(f"\n=== accepted cohort (control/benchmark) ===")
    print(f"  {accepted}")
    print(f"\n=== marginal cohort (rejected valid setups) ===")
    print(f"  rejected={mc['rejected_valid_setups']} simulated={mc['simulated']} "
          f"skipped={mc['skipped']}")
    print(f"  overall: {mc['overall']}")
    print(f"  de-duplicated: n={mc['dedup_first_per_ticker_month']['n']} "
          f"{mc['dedup_first_per_ticker_month']['overall']}")
    print(f"  exit reasons: {mc['exit_reasons']}")
    print(f"  by regime: {json.dumps(by_regime, ensure_ascii=False)}")
    print(f"  by price band: {json.dumps(by_band, ensure_ascii=False)}")

    # ---- pre-registered decision rule ----
    marg_avg = mc["overall"].get("avg_r")
    base_avg = accepted.get("avg_r")
    if marg_avg is None or base_avg is None:
        verdict = "INCONCLUSIVE (no data)"
    elif marg_avg >= base_avg:
        verdict = ("PROCEED — marginal expectancy >= accepted cohort: the "
                   "deployment lever is worth a controlled experiment")
    elif marg_avg > 0:
        verdict = ("PROCEED WITH CAUTION — marginal expectancy is positive but "
                   "below the accepted cohort: prefer the capital-base lever over "
                   "loosening the multiplier")
    else:
        verdict = ("STOP — marginal expectancy <= 0: the risk budget cap is "
                   "PROTECTIVE; do not loosen it. Retarget to Exit or make #4 "
                   "observable")
    print(f"\n=== pre-registered decision rule ===")
    print(f"  accepted avg R = {base_avg} | marginal avg R = {marg_avg}")
    print(f"  VERDICT: {verdict}")
    out["decision_rule"] = {"accepted_avg_r": base_avg, "marginal_avg_r": marg_avg,
                            "verdict": verdict}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
