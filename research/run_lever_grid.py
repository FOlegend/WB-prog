"""
research/run_lever_grid.py — generic single-variable lever experiment.

Runs the EXISTING production-equivalent backtest with EXACTLY ONE config
parameter overridden in memory (production/config.py is never edited), and
reports the §15 dashboard plus the sub-period split, so a lever can be judged
against the pre-registered criteria:

    * Sharpe must improve
    * MaxDD must not worsen beyond the corrected baseline's -10.39 %
    * the improvement must hold in BOTH sub-periods (2024 and 2025)

Design rules honoured: one variable at a time, frozen universe (the PIT bucket
cache is reused), frozen data / costs / execution, no look-ahead.

Usage
-----
python research/run_lever_grid.py --param risk_per_trade \
    --grid 0.01,0.0125,0.015 --baseline 0.01 \
    --out reports/research_lever_risk_per_trade_2026-10-01.json

python research/run_lever_grid.py --param max_open_positions \
    --grid 5,6 --baseline 5 \
    --out reports/research_lever_max_open_positions_2026-10-01.json
"""
from __future__ import annotations

import argparse
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import harness as H

SUBPERIODS = {
    "2024_full": ("2024-01-01", "2024-12-31"),
    "2025_to_Jul": ("2025-01-01", "2025-07-31"),
}


def _coerce(raw: str, template):
    """Cast a CLI value to the type of the existing config attribute."""
    if isinstance(template, bool):
        return raw.lower() in ("1", "true", "yes")
    if isinstance(template, int) and not isinstance(template, bool):
        return int(raw)
    return float(raw)


def _realised_risk(res: dict) -> dict:
    """Mean/median/max realised risk per trade as % of equity at entry."""
    eq = {e["date"]: e["equity"] for e in res["equity_curve"]}
    pcts = []
    for t in res["trade_log"]:
        e = eq.get(t["entry_date"])
        if not e:
            continue
        risk_usd = float(t["shares"]) * (float(t["entry_price"])
                                         - float(t["stop_price"]))
        pcts.append(100.0 * risk_usd / e)
    if not pcts:
        return {"n": 0}
    return {"n": len(pcts), "mean_pct": round(stats.mean(pcts), 4),
            "median_pct": round(stats.median(pcts), 4),
            "max_pct": round(max(pcts), 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--param", required=True,
                    help="a config field in research.harness.ALLOWED_OVERRIDES")
    ap.add_argument("--grid", required=True, help="comma list, e.g. 0.01,0.0125")
    ap.add_argument("--baseline", required=True, help="the frozen value")
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    base_cfg = H.load_config()
    if args.param not in H.ALLOWED_OVERRIDES:
        print(f"REFUSED: {args.param} is not in the research override whitelist")
        return 2
    template = getattr(base_cfg, args.param)
    values = [_coerce(v.strip(), template) for v in args.grid.split(",") if v.strip()]
    baseline_value = _coerce(args.baseline, template)
    if baseline_value not in values:
        print("NOTE: the frozen baseline value is not in the grid; it will be "
              "reported from the corrected baseline artifact instead.")

    variants = {}
    for v in values:
        cfg = H.load_config(**{args.param: v})
        eff = getattr(cfg, args.param)
        print(f"=== {args.param} = {eff} "
              f"({'FROZEN BASELINE' if eff == baseline_value else 'candidate'}) ===",
              flush=True)
        res = H.run_backtest(cfg)
        result = res["result"]
        if "error" in result:
            print("ERROR:", result["error"])
            return 1
        variants[str(eff)] = {
            args.param: eff,
            "dashboard": H.dashboard(result, cfg),
            "subperiods": H.subperiods(result, SUBPERIODS),
            "realised_risk": _realised_risk(result),
            "summary": result["summary"],
        }
        d = variants[str(eff)]["dashboard"]
        print(f"  return={d['return_pct']}% maxDD={d['max_dd_pct']}% "
              f"sharpe={d['sharpe']} sortino={d['sortino']} calmar={d['calmar']} "
              f"avgR={d['avg_r']} PF={d['profit_factor']} trades={d['n_trades']} "
              f"expo={d['avg_exposure_pct']}% "
              f"risk={variants[str(eff)]['realised_risk'].get('mean_pct')}%", flush=True)

    base_dash = variants.get(str(baseline_value), {}).get("dashboard")
    for k, v in variants.items():
        if base_dash:
            v["vs_baseline"] = H.compare_to_baseline(v["dashboard"], base_dash)

    payload = {
        "generated": "2026-10-01",
        "deliverable": args.label or f"single-variable lever experiment: {args.param}",
        "changing_variable": args.param,
        "baseline_value": baseline_value,
        "grid": values,
        "unchanged": "everything else (universe, data, costs, execution, all other "
                     "parameters); PIT bucket screen reused; corrected (PIT) regime",
        "criteria": [
            "Sharpe must improve vs the corrected baseline",
            "MaxDD must not worsen beyond the corrected baseline's -10.39%",
            "the improvement must hold in BOTH sub-periods (2024 and 2025)",
        ],
        "git": H.git_commit(),
        "variants": variants,
    }
    H.save_json(args.out, payload)

    print(f"\n=== comparison ===")
    print(f"{args.param:>18} {'ret%':>7} {'maxDD%':>8} {'sharpe':>7} "
          f"{'sortino':>8} {'calmar':>7} {'avgR':>7} {'PF':>5} {'trades':>6} "
          f"{'expo%':>6} {'risk%':>6}")
    for v in values:
        x = variants[str(v)]
        d = x["dashboard"]
        print(f"{v:>18} {d['return_pct']:>7} {d['max_dd_pct']:>8} {d['sharpe']:>7} "
              f"{str(d['sortino']):>8} {str(d['calmar']):>7} {str(d['avg_r']):>7} "
              f"{str(d['profit_factor']):>5} {d['n_trades']:>6} "
              f"{str(d['avg_exposure_pct']):>6} "
              f"{str(x['realised_risk'].get('mean_pct')):>6}")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
