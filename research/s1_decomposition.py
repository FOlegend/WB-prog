"""
research/s1_decomposition.py — S1-D: why does stop_atr_mult=1.8 look better?

S1 found that `stop_atr_mult = 1.8` improves every headline metric (return
+5.57 % -> +11.09 %, Sharpe 0.513 -> 1.107, MaxDD -10.39 % -> -8.73 %) but
fails the §12 neighbour test (2.0 gives back more than half the gain). S1 also
established that `stop_atr_mult` is not one lever but three at once:

  (1) POSITION SIZE  shares = risk_budget / (atr * stop_mult)
  (2) THE R UNIT    r_multiple = (exit - entry) / (entry - stop)
  (3) TRAILING ARM  r_dist = atr_at_entry * stop_mult  ->  the arming price

This script decomposes the 1.8 effect into those channels plus a fourth that
S1 could not separate: TRADE SELECTION. Only 87 of 1.8's 145 trades are shared
with the control (Jaccard 0.385), so most of the difference is a different set
of trades, not different exits on the same trades.

Nothing here modifies production code. Every number is either
  (a) a pure post-hoc re-expression of an already-recorded trade, or
  (b) a re-run of the unchanged ProductionBacktest.
No frozen contract is touched, and no parameter is written to disk.

Method
------
A. R-unit normalisation. For every CONTROL trade, recompute what its R would
   have been under each candidate's R unit. This isolates channel (2) exactly:
   the take-profit R of 2.5/stop_mult is arithmetic, not performance.
B. Risk-normalised efficiency. Since shares = floor(risk_budget / stop_distance),
   the DOLLAR risk per trade is ~constant across stop widths; only the position
   VALUE falls. Reported as $ risk/trade and exposure, so "less exposure" is not
   mistaken for "less risk".
C. Trailing arm price, in ATR units, per variant.
D. Selection vs execution. Split each variant's trades into SHARED (same ticker
   and entry date as the control) and UNIQUE, and report realised R for each.
   If the gain sits in UNIQUE, it is selection; if in SHARED, it is execution.
E. Leave-one-out. Recompute the control's R sum using only the trades a variant
   also took, to separate "did the same trades do better" from "were different
   trades taken".
"""
from __future__ import annotations

import argparse
import datetime
import math
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import harness as H

GRID = [1.2, 1.5, 1.8, 2.0]
CONTROL = 1.5
TP_MULT = 2.5


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.mean(xs), 4) if xs else None


def _key(t):
    return (t["ticker"], t["entry_date"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"s1_decomposition_pitcorrected_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    base_cfg = H.load_config()
    assert base_cfg.take_profit_atr_mult == TP_MULT, "TP mult is part of the freeze"

    runs, trades = {}, {}
    for v in GRID:
        cfg = H.load_config(stop_atr_mult=v)
        r = H.run_backtest(cfg)["result"]
        runs[v] = r
        trades[v] = r["trade_log"]
        d = H.dashboard(r, cfg)
        print(f"  ran stop_atr_mult={v}: return={d['return_pct']}% "
              f"sharpe={d['sharpe']} trades={d['n_trades']}", flush=True)

    ctrl = trades[CONTROL]
    ctrl_keys = {_key(t) for t in ctrl}

    # ---- A. R-unit normalisation (pure arithmetic on control trades) ----
    # A control trade's R is (exit-entry)/(entry-stop). Under a candidate's
    # stop width the DENOMINATOR changes even though the trade did not. Rescale
    # each control trade by its own stop ratio to express it in the candidate's
    # R unit: R_cand_unit = R_ctrl * (entry-stop_ctrl) / (entry-stop_cand_unit)
    # where the candidate's stop distance is atr_at_entry * candidate_mult.
    r_unit = {}
    for v in GRID:
        rows, raw = [], []
        for t in ctrl:
            r = t.get("r_multiple")
            atr = t.get("atr_at_entry")
            if r is None or not atr:
                continue
            d_ctrl = atr * CONTROL
            d_cand = atr * v
            if d_ctrl <= 0:
                continue
            raw.append(r)
            rows.append(r * d_ctrl / d_cand)
        r_unit[str(v)] = {
            "control_r_as_recorded": _mean(raw),
            "control_r_in_this_variants_unit": _mean(rows),
            "delta_from_unit_alone": (round(_mean(rows) - _mean(raw), 4)
                                       if rows and raw else None),
            "take_profit_R_in_this_unit": round(TP_MULT / v, 4),
            "note": ("same 168 trades, same exit prices; only the R denominator "
                     "is re-expressed. Any change here is arithmetic, not skill."),
        }

    # ---- B. risk / exposure per variant ---------------------------------
    risk_rows = {}
    for v in GRID:
        rs, pv, shares = [], [], []
        for t in trades[v]:
            atr = t.get("atr_at_entry")
            if not atr:
                continue
            d = atr * v
            entry = float(t["entry_price"])
            shares.append(t.get("shares") or 0)
            rs.append((t.get("shares") or 0) * d)          # realised $ risk
            pv.append(t.get("position_value"))
        dd = H.dashboard(runs[v], H.load_config(stop_atr_mult=v))
        risk_rows[str(v)] = {
            "mean_dollar_risk_per_trade": _mean(rs),
            "mean_position_value": _mean(pv),
            "mean_shares": _mean(shares),
            "avg_exposure_pct": dd["avg_exposure_pct"],
            "note": ("dollar risk is ~constant because shares = "
                     "floor(risk_budget / stop_distance); only position VALUE "
                     "falls as the stop widens"),
        }

    # ---- C. trailing arm price in ATR units -----------------------------
    arm = {}
    for v in GRID:
        # _exit_check arms when highest >= entry + trailing_trigger_r * (atr*stop_mult)
        arm[str(v)] = {
            "trailing_trigger_r": 1.0,
            "r_dist_atr_multiple": v,
            "arm_price_formula": f"entry + 1.0 * atr * {v}",
            "note": ("the arming threshold in ATR units IS stop_atr_mult when "
                     "trailing_trigger_r = 1.0, so a wider initial stop also "
                     "delays the trailing arm"),
        }

    # ---- D. selection vs execution --------------------------------------
    sel = {}
    for v in GRID:
        if v == CONTROL:
            continue
        ks = {_key(t) for t in trades[v]}
        shared = [t for t in trades[v] if _key(t) in ctrl_keys]
        unique = [t for t in trades[v] if _key(t) not in ctrl_keys]
        ctrl_shared = [t for t in ctrl if _key(t) in ks]
        sel[str(v)] = {
            "jaccard_vs_control": round(len(ks & ctrl_keys) / len(ks | ctrl_keys), 4),
            "n_shared": len(shared), "n_unique": len(unique),
            "n_control_dropped": len(ctrl_keys - ks),
            "shared_variant_avg_r": _mean([t.get("r_multiple") for t in shared]),
            "shared_control_avg_r": _mean([t.get("r_multiple") for t in ctrl_shared]),
            "shared_delta": (
                round(_mean([t.get("r_multiple") for t in shared])
                      - _mean([t.get("r_multiple") for t in ctrl_shared]), 4)
                if shared and ctrl_shared else None),
            "unique_variant_avg_r": _mean([t.get("r_multiple") for t in unique]),
            "shared_sum_r": round(sum(t.get("r_multiple") or 0 for t in shared), 3),
            "unique_sum_r": round(sum(t.get("r_multiple") or 0 for t in unique), 3),
            "shared_net_pnl": round(sum(t.get("net_pnl") or 0 for t in shared), 2),
            "unique_net_pnl": round(sum(t.get("net_pnl") or 0 for t in unique), 2),
        }

    # ---- E. exit-reason migration on shared trades ----------------------
    mig = {}
    for v in GRID:
        if v == CONTROL:
            continue
        ks = {_key(t) for t in trades[v]}
        table = {}
        for t in ctrl:
            if _key(t) not in ks:
                continue
            k = t.get("exit_reason") or "UNKNOWN"
            e = table.setdefault(k, {"n": 0, "sum_r": 0.0})
            e["n"] += 1
            e["sum_r"] += t.get("r_multiple") or 0.0
        mig[str(v)] = {
            "control_exit_reasons_on_shared_trades": {
                k: {"n": e["n"], "sum_r": round(e["sum_r"], 3),
                    "avg_r": round(e["sum_r"] / e["n"], 4)}
                for k, e in sorted(table.items())},
            "note": ("what the control did with the trades this variant ALSO "
                     "took — isolates the execution channel from selection"),
        }

    # ---- F. headline, restated in unit-free terms ------------------------
    headline = {}
    for v in GRID:
        d = H.dashboard(runs[v], H.load_config(stop_atr_mult=v))
        headline[str(v)] = {
            "return_pct": d["return_pct"], "sharpe": d["sharpe"],
            "max_dd_pct": d["max_dd_pct"], "calmar": d["calmar"],
            "profit_factor": d["profit_factor"],
            "n_trades": d["n_trades"], "avg_exposure_pct": d["avg_exposure_pct"],
            "avg_r": d["avg_r"],
            "sum_r": round(sum(t.get("r_multiple") or 0
                               for t in trades[v]), 3),
            "sum_net_pnl": round(sum(t.get("net_pnl") or 0
                                     for t in trades[v]), 2),
            "unit_free": ("return_pct / sharpe / max_dd_pct / sum_net_pnl do "
                          "not depend on the R denominator; avg_r and sum_r DO"),
        }

    payload = {
        "generated": datetime.date.today().isoformat(),
        "deliverable": "S1-D — decomposition of the stop_atr_mult=1.8 effect "
                       "into position size / R unit / trailing arm / trade selection",
        "method": {
            "no_production_change": True,
            "A": "control trades re-expressed in each variant's R unit (pure "
                 "arithmetic; isolates the R-denominator channel)",
            "B": "realised dollar risk and position value per variant (isolates "
                 "the sizing channel)",
            "C": "trailing arming price in ATR units (isolates the trigger channel)",
            "D": "shared vs unique trade split (isolates selection)",
            "E": "control exit reasons on the shared trades (isolates execution)",
        },
        "headline_by_variant": headline,
        "A_r_unit_normalisation": r_unit,
        "B_risk_and_exposure": risk_rows,
        "C_trailing_arm": arm,
        "D_selection_vs_execution": sel,
        "E_exit_migration_shared": mig,
        "caveats": [
            "Channels are not independent: position size feeds back into cash, "
            "which changes which candidates are affordable, which changes "
            "selection. The split attributes the OBSERVED difference; it does "
            "not prove the mechanism is causal.",
            "Shared/unique is defined on (ticker, entry_date). A variant may "
            "re-enter the same ticker on a different date; that counts as unique.",
            "No production parameter was changed and no frozen contract touched.",
        ],
        "git": H.git_commit(),
    }
    H.save_json(args.out, payload)

    print("\n=== S1-D decomposition ===")
    print("\nA. R-unit normalisation (control's own 168 trades, re-expressed):")
    print(f"   {'stop':>5} {'R as recorded':>15} {'R in this unit':>16} "
          f"{'delta':>8} {'TP_R':>7}")
    for v in GRID:
        r = r_unit[str(v)]
        print(f"   {v:>5} {str(r['control_r_as_recorded']):>15} "
              f"{str(r['control_r_in_this_variants_unit']):>16} "
              f"{str(r['delta_from_unit_alone']):>8} "
              f"{r['take_profit_R_in_this_unit']:>7}")
    print("\nB. risk vs exposure (is it de-risking, or just smaller positions?):")
    print(f"   {'stop':>5} {'$risk/trade':>13} {'pos value':>11} {'expo%':>7} "
          f"{'trades':>7}")
    for v in GRID:
        b = risk_rows[str(v)]
        print(f"   {v:>5} {str(b['mean_dollar_risk_per_trade']):>13} "
              f"{str(b['mean_position_value']):>11} "
              f"{str(b['avg_exposure_pct']):>7} "
              f"{str(headline[str(v)]['n_trades']):>7}")
    print("\nD. selection vs execution (vs control):")
    print(f"   {'stop':>5} {'jaccard':>8} {'shared':>7} {'uniq':>5} "
          f"{'sharedR_v':>10} {'sharedR_c':>10} {'delta':>7} {'uniqR_v':>8} "
          f"{'sumR_sh':>8} {'sumR_un':>8}")
    for v in GRID:
        if v == CONTROL:
            continue
        s = sel[str(v)]
        print(f"   {v:>5} {s['jaccard_vs_control']:>8} {s['n_shared']:>7} "
              f"{s['n_unique']:>5} {str(s['shared_variant_avg_r']):>10} "
              f"{str(s['shared_control_avg_r']):>10} "
              f"{str(s['shared_delta']):>7} "
              f"{str(s['unique_variant_avg_r']):>8} {s['shared_sum_r']:>8} "
              f"{s['unique_sum_r']:>8}")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
