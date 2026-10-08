"""
research/r3_capacity_magnitude.py — R3: how much was foregone by blocking entries?

R3 has been BLOCKED across every phase because the pipeline returned before
evaluating any setup when entries were blocked, so the skipped population was
nowhere in the record. R7 Tier A made the population visible; this script turns
Tier B on to make its VALUE measurable, then simulates the foregone cohort with
the SAME machinery the Step-0 study already validated
(`production/tests/phase5_step0_marginal_cohort.py::simulate_trade`).

What this measures
------------------
For every session where entries were blocked, the unmodified production
`evaluate_setup` + `size_swing_position` are run over the blocked candidates
(R7 Tier B). Every candidate that would have produced a valid setup is then run
through `simulate_trade` — next-open entry, the frozen gap filter, the frozen
Stop/Exit Engine — to obtain a realised R.

The result answers the question the capacity proposal was written to answer:

    how many potentially good trades are being blocked, and what are they worth?

What this does NOT do
----------------------
It does not change any constraint. `max_open_positions`, `risk_per_trade` and
every other parameter stay exactly as the baseline has them, and the baseline
backtest is NOT re-run — the blocked sessions are, by construction, sessions on
which the production system bought nothing. The cohort is therefore genuinely
foregone.

The simulation is a what-if measurement, not a strategy: the cohort trades would
have competed for the same cash and the same slots as the trades that *were*
taken, and this script does not attempt to resolve that contention (that is a
lever experiment, L2, and it was rejected on its own evidence). Read the output
as an upper bound on the recoverable value, not as a return forecast.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import math
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import harness as H
from production.config import ProductionConfig
from production.datasource import build_cached_source
from production.pipeline import run_daily

sys.path.insert(0, os.path.join(_REPO_ROOT, "production", "tests"))
from phase5_step0_marginal_cohort import simulate_trade  # noqa: E402

SUBPERIODS = {
    "2024_full": ("2024-01-01", "2024-12-31"),
    "2025_jan_mar": ("2025-01-01", "2025-03-31"),
    "2025_apr_jul": ("2025-04-01", "2025-07-31"),
}


def _stage_key(rec: dict) -> str | None:
    snap = rec["setup"].get("blocked_snapshot")
    if not snap:
        return None
    return snap.get("blocked_stage")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.mean(xs), 4) if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"r3_capacity_magnitude_pitcorrected_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    # Tier B ON. Every other parameter is the frozen baseline, untouched.
    cfg = ProductionConfig(research_blocked_setup_eval=True)
    assert cfg.stop_atr_mult == 1.5 and cfg.take_profit_atr_mult == 2.5
    assert cfg.trailing_atr_mult == 1.5 and cfg.trailing_trigger_r == 1.0
    assert cfg.max_open_positions == 5 and cfg.risk_per_trade == 0.01

    source = build_cached_source(cfg)
    base = H.load_config()
    control = H.run_backtest(base)["result"]
    n_control_trades = len(control["trade_log"])

    import pandas as pd
    spy, _ = source.get_ohlcv(source.benchmark, as_of=base and "2025-07-31",
                               min_bars=1)
    dates = sorted(d for d in spy["datetime"].tolist()
                   if pd.Timestamp("2024-01-01") <= d <= pd.Timestamp("2025-07-31"))

    # Replay the baseline state machine so the foregone cohort is measured in the
    # exact portfolio context in which it was blocked. Sells and buys are
    # executed exactly as production/backtest.py does; nothing is altered.
    from src.state.state import (default_state, close_position, mark_to_market,
                                 open_position, default_position)
    from src.agents.risk_manager import size_position
    import bisect

    bucket_cache = os.path.join(
        cfg.reports_dir,
        "production_buckets_2024-01-01_2025-07-31_top30.json")
    blob = json.load(open(bucket_cache, encoding="utf-8"))
    rb = sorted(blob.keys())

    def active_bucket(d: str):
        i = bisect.bisect_right(rb, d) - 1
        return blob[rb[i]] if i >= 0 else {"status": "EMPTY", "tickers": []}

    state = default_state(cfg.capital_usd)
    pending: dict = {}
    blocked_sessions = []
    evaluated_sessions = 0

    for d in dates:
        ds = d.strftime("%Y-%m-%d")
        bucket = active_bucket(ds)
        tickers = [c["ticker"] for c in bucket.get("tickers", [])]
        held = {p["ticker"] for p in state["open_positions"]}

        # execute yesterday's proposals at today's open (production rules)
        for t in sorted(pending.keys()):
            o = pending[t]
            if t not in set(tickers) or t in held:
                del pending[t]
                continue
            df, _ = source.get_ohlcv(t, as_of=ds, min_bars=1)
            if df is None or not len(df):
                del pending[t]
                continue
            bar = df.iloc[-1]
            open_px = float(bar["open"])
            sig = o.get("signal_close")
            gap = ((open_px - sig) / sig) if sig else None
            if gap is not None and gap > cfg.max_entry_gap_pct:
                del pending[t]
                continue
            sizing = size_position(
                state["equity"], state["cash"], open_px, o.get("atr") or 0.0,
                o.get("size_mult", 1.0) * o.get("quality_mult", 1.0),
                len(state["open_positions"]), cfg)
            if not sizing.get("allow"):
                del pending[t]
                continue
            pos = default_position(
                t, sizing["shares"], open_px, ds, o.get("atr") or 0.0,
                sizing["stop_price"], sizing["take_profit"],
                o.get("regime_label", "UNKNOWN"), o.get("reason", ""))
            pos.update({"entry_fill_model": "NEXT_OPEN",
                        "signal_date": o.get("signal_date"),
                        "setup_type": o.get("setup_type"),
                        "setup_score": o.get("setup_score"),
                        "entry_size_mult": o.get("size_mult")})
            open_position(state, pos, open_px, cfg)
            held.add(t)
            del pending[t]

        rec = run_daily(ds, state, source, cfg, screen_mode="bucket",
                        candidate_tickers=tickers, screen_result=bucket)
        evaluated_sessions += 1

        for s in rec["exits"]["proposed"]:
            idx = next((i for i, p in enumerate(state["open_positions"])
                        if p["ticker"] == s["ticker"]), None)
            if idx is None:
                continue
            close_position(state, idx, float(s["exit_price"]), ds,
                           s["reason"], {}, fill_model=s["fill_model"])
            for t in [s["ticker"]]:
                pending.pop(t, None)
        held = {p["ticker"] for p in state["open_positions"]}

        for o in rec["entries"]["proposed"]:
            pending[o["ticker"]] = {**o, "signal_date": ds}

        prices = {}
        for t in held:
            df, _ = source.get_ohlcv(t, as_of=ds, min_bars=1)
            if df is not None and len(df):
                prices[t] = float(df["close"].iloc[-1])
        mark_to_market(state, prices)

        snap = rec["setup"].get("blocked_snapshot")
        stage = _stage_key(rec)
        if stage and snap:
            per = snap.get("per_candidate")
            if isinstance(per, list):
                blocked_sessions.append({
                    "as_of": ds, "stage": stage,
                    "blocked_reason": snap.get("blocked_reason"),
                    "regime_label": (snap.get("regime") or {}).get("regime_label"),
                    "position_size_mult": (snap.get("regime") or {}).get(
                        "position_size_mult"),
                    "n_open": (snap.get("portfolio") or {}).get("n_open"),
                    "capacity_slots_free": (snap.get("portfolio") or {}).get(
                        "capacity_slots_free"),
                    "risk_budget_usd": (snap.get("capital") or {}).get(
                        "risk_budget_usd"),
                    "n_candidates": (snap.get("candidates") or {}).get(
                        "n_candidates"),
                    "per_candidate": per,
                })

    # ---- aggregate the foregone cohort ---------------------------------
    by_stage: dict = {}
    rows = []
    for s in blocked_sessions:
        st = by_stage.setdefault(s["stage"], {
            "sessions": 0, "candidates_examined": 0, "setup_valid": 0,
            "setup_invalid": 0, "risk_allowed": 0, "risk_denied": 0,
            "already_held_or_unavailable": 0, "simulated": 0, "skipped": 0,
            "r_values": [], "blocked_reasons": {},
            "subperiod_sessions": {k: 0 for k in SUBPERIODS}})
        st["sessions"] += 1
        st["blocked_reasons"][s["blocked_reason"]] = st["blocked_reasons"].get(
            s["blocked_reason"], 0) + 1
        for k, (a, b) in SUBPERIODS.items():
            if a <= s["as_of"] <= b:
                st["subperiod_sessions"][k] += 1
        for c in s["per_candidate"]:
            st["candidates_examined"] += 1
            status = c.get("setup_status")
            if status == "valid":
                st["setup_valid"] += 1
            elif status == "invalid":
                st["setup_invalid"] += 1
            else:
                st["already_held_or_unavailable"] += 1
                continue
            if status != "valid":
                continue
            if c.get("risk_status") == "allowed":
                st["risk_allowed"] += 1
            elif c.get("risk_status") == "denied":
                st["risk_denied"] += 1
            sim = simulate_trade(cfg, c["ticker"], s["as_of"],
                                 c.get("signal_close"), c.get("atr"))
            row = {
                "as_of": s["as_of"], "stage": s["stage"],
                "ticker": c["ticker"], "regime_label": s["regime_label"],
                "setup_score": c.get("setup_score"),
                "setup_quality_mult": c.get("setup_quality_mult"),
                "risk_status": c.get("risk_status"),
                "score_components": c.get("score_components"),
                **{k: v for k, v in sim.items()},
            }
            rows.append(row)
            if "r" in sim:
                st["simulated"] += 1
                st["r_values"].append(sim["r"])
            else:
                st["skipped"] += 1

    out_stage = {}
    for k, v in by_stage.items():
        rs = v.pop("r_values")
        v["simulated_avg_r"] = _mean(rs)
        v["simulated_median_r"] = (round(stats.median(rs), 4) if rs else None)
        v["simulated_sum_r"] = round(sum(rs), 3) if rs else 0.0
        v["simulated_std_r"] = (round(stats.pstdev(rs), 4) if len(rs) > 1
                                else None)
        v["simulated_pct_positive"] = (
            round(100 * sum(1 for r in rs if r > 0) / len(rs), 2) if rs else None)
        v["simulated_pf"] = (
            round(sum(r for r in rs if r > 0)
                  / abs(sum(r for r in rs if r <= 0)), 3)
            if rs and any(r <= 0 for r in rs) else None)
        out_stage[k] = v

    # sub-period split of the foregone cohort
    by_sub = {}
    for sp, (a, b) in SUBPERIODS.items():
        rs = [r["r"] for r in rows if a <= r["as_of"] <= b and "r" in r]
        by_sub[sp] = {"n": len(rs), "avg_r": _mean(rs),
                      "sum_r": round(sum(rs), 3) if rs else 0.0,
                      "pct_positive": (round(100 * sum(1 for r in rs if r > 0)
                                             / len(rs), 2) if rs else None)}

    # compare against what the system actually took
    taken = [t.get("r_multiple") for t in control["trade_log"]
             if t.get("r_multiple") is not None]
    skip_reasons: dict = {}
    for r in rows:
        if "skipped" in r:
            skip_reasons[r["skipped"]] = skip_reasons.get(r["skipped"], 0) + 1

    payload = {
        "generated": datetime.date.today().isoformat(),
        "deliverable": "R3 — capacity/entry-block magnitude on the PIT-correct "
                       "baseline (R7 Tier B)",
        "method": {
            "tier_b": "research_blocked_setup_eval=True; the UNMODIFIED "
                      "production evaluate_setup + size_swing_position are run "
                      "over blocked candidates",
            "simulation": "production/tests/phase5_step0_marginal_cohort.py::"
                          "simulate_trade — next-open entry, frozen gap filter, "
                          "frozen Stop/Exit Engine; validated at 163/168 on the "
                          "accepted set",
            "r_unit": "(exit - entry) / (entry - initial stop), the backtest's own "
                      "unit",
            "portfolio_context": "the cohort is measured in the exact portfolio "
                                 "state in which it was blocked (the baseline "
                                 "state machine is replayed)",
        },
        "caveats": [
            "This is an UPPER BOUND on recoverable value, not a return forecast: "
            "the foregone trades would have competed for the same cash and slots "
            "as the trades that were taken, and that contention is NOT resolved "
            "here (it is lever L2, which was rejected on its own evidence).",
            "No constraint was changed. max_open_positions, risk_per_trade and "
            "every other parameter remain at the frozen baseline.",
            "simulate_trade applies the same horizon guard artefact the Step-0 "
            "audit documented (HARNESS_HORIZON_GUARD_ARTIFACT).",
        ],
        "control": {"n_trades_taken": n_control_trades,
                    "avg_r_taken": _mean(taken),
                    "sum_r_taken": round(sum(taken), 3) if taken else 0.0,
                    "pct_positive_taken": (
                        round(100 * sum(1 for r in taken if r > 0) / len(taken), 2)
                        if taken else None)},
        "n_sessions_replayed": evaluated_sessions,
        "n_blocked_sessions_measured": len(blocked_sessions),
        "by_blocked_stage": out_stage,
        "foregone_by_subperiod": by_sub,
        "simulation_skip_reasons": skip_reasons,
        "foregone_cohort": rows,
    }
    H.save_json(args.out, payload)

    print("=== R3 — capacity / entry-block magnitude (R7 Tier B) ===")
    print(f"  sessions replayed            : {evaluated_sessions}")
    print(f"  blocked sessions measured    : {len(blocked_sessions)}")
    print(f"  trades actually taken        : {n_control_trades} "
          f"(avg R {_mean(taken)})")
    print()
    for stage, v in sorted(out_stage.items()):
        print(f"  --- {stage} ---")
        print(f"    sessions                   : {v['sessions']}  "
              f"{v['subperiod_sessions']}")
        print(f"    candidates examined        : {v['candidates_examined']}")
        print(f"    setup valid / invalid      : {v['setup_valid']} / "
              f"{v['setup_invalid']}")
        print(f"    risk allowed / denied      : {v['risk_allowed']} / "
              f"{v['risk_denied']}")
        print(f"    simulated                  : {v['simulated']} "
              f"(skipped {v['skipped']})")
        print(f"    foregone avg R             : {v['simulated_avg_r']}  "
              f"median {v['simulated_median_r']}  sum {v['simulated_sum_r']}")
        print(f"    foregone %positive / PF    : {v['simulated_pct_positive']} / "
              f"{v['simulated_pf']}")
    print()
    print("  sub-period split of the foregone cohort:")
    for sp, v in by_sub.items():
        print(f"    {sp:<14} n={v['n']:>3}  avgR={str(v['avg_r']):>8}  "
              f"sumR={v['sum_r']:>7}  %pos={v['pct_positive']}")
    print(f"\n  simulation skips: {skip_reasons}")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
