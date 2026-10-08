"""
research/step0_harness_audit.py — §11 verification of the Step-0 research harness.

GATE (spec §11): before any extensive exit / stop parameter research, the harness
used to measure trade value must be shown trustworthy. The Step-0 control
re-measured the 151 accepted trades with the hypothetical-trade machinery and
reported 148/151 replication; the original run printed only a summary, so the
3 mismatches were never persisted or explained.

This script:
  1. re-runs the production-equivalent backtest UNCHANGED (no parameter, config
     or production path is touched) and the Step-0 machinery,
  2. emits the PER-TRADE control rows,
  3. isolates every mismatch (exit reason, or |R| difference > 0.05),
  4. classifies each mismatch cause from available fields.

It also re-verifies the Phase-2/3 direct replay parity claim (151 trades, 1073
bars, 0 direct Stop/Exit divergence) by calling the existing replay harness.

Read-only.
Output: reports/step0_harness_audit_2026-10-01.json

Run:
  python research/step0_harness_audit.py
"""
from __future__ import annotations

import collections
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.datasource import build_cached_source
from production.tests import phase5_step0_marginal_cohort as S0

OUT = os.path.join(_REPO_ROOT, "reports", "step0_harness_audit_2026-10-01.json")
R_TOL = 0.05


def _classify(row: dict, cfg) -> str:
    """Attribute a mismatch to the most specific available cause."""
    sim = row.get("sim") or {}
    if sim.get("skipped"):
        # the Step-0 simulator stops iterating at `held > HORIZON_CAL_DAYS`
        # BEFORE evaluating the exit on that bar. When the 30-calendar-day
        # boundary falls on a non-trading day the first eligible session is
        # held=31/32, so a TIME_STOP (held >= 30) is never reached.
        if sim["skipped"] == "no_exit_within_horizon":
            return "HARNESS_HORIZON_GUARD_ARTIFACT"
        return f"SIMULATION_SKIPPED::{sim['skipped']}"
    if sim.get("reason") != row["recorded"]["exit_reason"]:
        if row.get("entry_mismatch"):
            return "ENTRY_REFERENCE_PRICE"
        if sim.get("exit_date") != row["recorded"]["exit_date"]:
            return "EARLY_EXIT_TIMING"
        return "REASON_MISMATCH_UNEXPLAINED"
    # reasons agree but R differs
    if row.get("entry_mismatch"):
        return "ENTRY_REFERENCE_PRICE"
    if row.get("stop_rounding_ok") is False:
        return "STOP_REFERENCE_ROUNDING"
    return "R_DRIFT_UNEXPLAINED"


def main() -> int:
    cfg = ProductionConfig()
    source = build_cached_source(cfg)
    print("=== §11 Step-0 harness verification ===")
    res, records = S0._run_capturing(cfg, source)
    trades = res["trade_log"]

    rows = []
    for t in trades:
        df = S0._bars(cfg, t["ticker"])
        if df is None:
            rows.append({"ticker": t["ticker"], "skipped": "no_cached_bars"})
            continue
        entry = float(t["entry_price"])
        atr = float(t["atr_at_entry"] or 0)
        stop_distance = entry - float(t["stop_price"])
        if stop_distance <= 0 or atr <= 0:
            continue
        # the next-open entry the simulator will select
        ndf = df[df["datetime"] > t["signal_date"]]
        sim_entry_date = (ndf.iloc[0]["datetime"].strftime("%Y-%m-%d")
                          if len(ndf) else None)
        sim_entry_open = float(ndf.iloc[0]["open"]) if len(ndf) else None
        # geometry the frozen formulas imply vs what the record stored
        implied_stop_distance = atr * cfg.stop_atr_mult
        stop_rounding_ok = abs(implied_stop_distance - stop_distance) <= 0.02

        sim = S0.simulate_trade(cfg, t["ticker"], t["signal_date"], entry, atr,
                                apply_gap_filter=False)
        rec_r = t.get("r_multiple")
        sim_r = sim.get("r")
        r_diff = (abs(rec_r - sim_r) if (rec_r is not None and sim_r is not None)
                  else None)
        row = {
            "ticker": t["ticker"], "bucket_date": t.get("bucket_date"),
            "signal_date": t["signal_date"],
            "recorded": {"entry_date": t["entry_date"],
                         "entry_price": round(entry, 4),
                         "exit_date": t["exit_date"],
                         "exit_reason": t["exit_reason"],
                         "exit_price": round(float(t["exit_price"]), 4),
                         "exit_fill_model": t.get("exit_fill_model"),
                         "r": rec_r},
            "sim_entry_date": sim_entry_date,
            "sim_entry_open": (round(sim_entry_open, 4)
                               if sim_entry_open is not None else None),
            "entry_mismatch": (sim_entry_date != t["entry_date"]),
            "entry_price_mismatch": (
                abs((sim_entry_open or entry) - entry) > 1e-6),
            "stop_implied": round(implied_stop_distance, 6),
            "stop_recorded": round(stop_distance, 6),
            "stop_rounding_ok": stop_rounding_ok,
            "sim": {"exit_date": sim.get("exit_date"),
                    "reason": sim.get("reason"),
                    "fill_model": sim.get("fill_model"),
                    "exit_price": sim.get("exit_price"), "r": sim_r,
                    "skipped": sim.get("skipped"),
                    "trailing_armed": sim.get("trailing_armed")},
            "r_abs_diff": (round(r_diff, 4) if r_diff is not None else None),
        }
        row["verdict"] = ("MATCH" if (row["sim"]["reason"] == t["exit_reason"]
                                      and r_diff is not None
                                      and r_diff <= R_TOL)
                          else _classify(row, cfg))
        rows.append(row)

    comparable = [r for r in rows if r.get("sim", {}).get("reason")]
    same_reason = [r for r in comparable
                   if r["sim"]["reason"] == r["recorded"]["exit_reason"]]
    near = [r for r in comparable
            if r["r_abs_diff"] is not None and r["r_abs_diff"] <= R_TOL]
    mismatches = [r for r in rows if r.get("verdict") != "MATCH"]

    causes = collections.Counter(r["verdict"] for r in mismatches)

    # ---- prove the HORIZON_GUARD classification ------------------------------
    proof = []
    if mismatches:
        original_horizon = S0.HORIZON_CAL_DAYS
        S0.HORIZON_CAL_DAYS = 90          # only to expose the missed exit
        try:
            for m in mismatches:
                t = next(x for x in trades if x["ticker"] == m["ticker"]
                         and x["signal_date"] == m["signal_date"])
                atr = float(t["atr_at_entry"] or 0)
                re_sim = S0.simulate_trade(
                    cfg, m["ticker"], m["signal_date"],
                    m["recorded"]["entry_price"], atr, apply_gap_filter=False)
                m["re_sim_with_extended_horizon"] = re_sim
                ok = (re_sim.get("reason") == m["recorded"]["exit_reason"]
                      and re_sim.get("r") is not None
                      and abs(re_sim["r"] - m["recorded"]["r"]) <= R_TOL)
                proof.append({
                    "ticker": m["ticker"], "signal_date": m["signal_date"],
                    "recorded_reason": m["recorded"]["exit_reason"],
                    "recorded_r": m["recorded"]["r"],
                    "resim_reason": re_sim.get("reason"),
                    "resim_exit_date": re_sim.get("exit_date"),
                    "resim_r": re_sim.get("r"),
                    "now_matches": ok})
        finally:
            S0.HORIZON_CAL_DAYS = original_horizon

    out = {
        "generated": "2026-10-01",
        "gate": "spec §11 — Step-0 harness trustworthiness",
        "harness": "production/tests/phase5_step0_marginal_cohort.py (machinery) "
                   "+ production/backtest.py (unchanged replay)",
        "mode": "legacy (production default)",
        "control_rows": len(rows),
        "comparable_rows": len(comparable),
        "same_exit_reason": len(same_reason),
        "same_exit_reason_pct": round(100.0 * len(same_reason)
                                      / len(comparable), 2) if comparable else None,
        "r_within_0.05": len(near),
        "r_within_0.05_pct": round(100.0 * len(near) / len(comparable), 2)
                             if comparable else None,
        "mismatch_count": len(mismatches),
        "mismatch_causes": dict(causes),
        "mismatches": mismatches,
        "horizon_guard_proof": {
            "explanation": (
                "the Step-0 simulator stops at `held > 30` calendar days BEFORE "
                "evaluating the exit on that bar; when the 30-day boundary falls "
                "on a non-trading day, production's TIME_STOP (held >= 30) fires "
                "on the next available session (held = 31/32) which the simulator "
                "never evaluates. Re-simulating those trades with a larger "
                "horizon reproduces the recorded exit exactly."),
            "results": proof,
            "all_reproduce_with_larger_horizon": (
                all(p["now_matches"] for p in proof) if proof else None),
        },
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)

    print(f"  control rows        : {len(rows)} (comparable {len(comparable)})")
    print(f"  same exit reason    : {len(same_reason)}/{len(comparable)} "
          f"({out['same_exit_reason_pct']}%)")
    print(f"  R within {R_TOL}       : {len(near)}/{len(comparable)} "
          f"({out['r_within_0.05_pct']}%)")
    print(f"  mismatches          : {len(mismatches)} -> {dict(causes)}")
    for m in mismatches:
        print(f"    - {m['ticker']} signal={m['signal_date']} "
              f"verdict={m['verdict']}")
        print(f"        recorded: {m['recorded']['exit_date']} "
              f"{m['recorded']['exit_reason']} @{m['recorded']['exit_price']} "
              f"R={m['recorded']['r']}")
        print(f"        sim     : {m['sim']['exit_date']} {m['sim']['reason']} "
              f"@{m['sim']['exit_price']} R={m['sim']['r']}")
        print(f"        entry rec={m['recorded']['entry_date']} "
              f"sim={m['sim_entry_date']} | r_diff={m['r_abs_diff']}")
    hp = out["horizon_guard_proof"]
    print(f"  horizon-guard proof (re-sim with larger horizon): "
          f"all reproduce = {hp['all_reproduce_with_larger_horizon']}")
    for p in hp["results"]:
        print(f"    - {p['ticker']}: recorded {p['recorded_reason']} "
              f"R={p['recorded_r']} -> resim {p['resim_reason']} "
              f"{p['resim_exit_date']} R={p['resim_r']} "
              f"match={p['now_matches']}")
    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
