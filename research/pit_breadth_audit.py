"""
research/pit_breadth_audit.py — does Regime v1's breadth engine respect the as-of date?

FINDING UNDER TEST (raised 2026-10-01, read-only)
-------------------------------------------------
`production/pipeline.py::run_daily` loads breadth with

    breadth_df = load_breadth(cfg, end=as_of)

but `regime_dual_engine/pit_breadth_data.get_breadth()` returns the WHOLE cached
CSV whenever the cache exists — the `end` argument only matters when rebuilding:

    if os.path.exists(path) and not rebuild:
        return pd.read_csv(path, ...)      # `end` ignored

`compute_regime_decision()` then reads `breadth_df["pct_above_50dma"].iloc[-1]`
(and the trailing percentile window) — i.e. the TAIL of the full 2016..2025
series. Consequence: in any historical replay, **Engine B evaluates the last row
of the cache (2025-07-31) for every as-of date**, so 50 % of the frozen Regime v1
composite is not point-in-time. Engine A (HMM on the PIT SPY slice) is unaffected.

This script measures the impact WITHOUT modifying any file:

  * PART 1 — the leak, directly: regime diagnostics for several as-of dates with
    the shipped loader vs a PIT-trimmed loader.
  * PART 2 — the end-to-end impact: the production-equivalent backtest is re-run
    with the loader patched IN-PROCESS ONLY (`production.pipeline.load_breadth`)
    to return `series[series.index <= as_of]`, and the baseline + R1 grid results
    are compared against the shipped (leaky) numbers already on disk.

Nothing on disk is changed; the patch lives only in this process.

Output: reports/pit_breadth_audit_2026-10-01.json

Run:
  python research/pit_breadth_audit.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

import production.pipeline as pipeline_mod
from production.config import ProductionConfig
from production.datasource import build_cached_source
from production.agents.regime import load_breadth, compute_market_regime
from research import harness as H

OUT = os.path.join(_REPO_ROOT, "reports", "pit_breadth_audit_2026-10-01.json")
PROBE_DATES = ["2018-06-29", "2020-03-31", "2022-06-30", "2024-01-31",
               "2024-06-28", "2025-01-31", "2025-07-31"]
GRID = [2.5, 3.0, 3.5, 4.0]
LEAKY_GRID_JSON = os.path.join(_REPO_ROOT, "reports",
                               "research_tp_grid_2026-10-01.json")


def _diag(spy, breadth, cfg) -> dict:
    out = compute_market_regime(spy, breadth, cfg)
    d = out.get("_diag", {})
    return {"label": out.get("regime_label"),
            "composite": out.get("composite_score"),
            "mult": out.get("position_size_mult"),
            "vetoes": out.get("veto_flags"),
            "breadth_percentile": d.get("breadth_percentile"),
            "breadth_now_pct": d.get("breadth_now_pct"),
            "breadth_10d_ago_pct": d.get("breadth_10d_ago_pct"),
            "hmm_bull_prob": d.get("hmm_bull_prob")}


def part1_leak(cfg) -> dict:
    src = build_cached_source(cfg)
    full = load_breadth(cfg, end="2025-07-31")
    rows = []
    for d in PROBE_DATES:
        if pd.Timestamp(d) < full.index.min() or pd.Timestamp(d) > full.index.max():
            continue
        spy, _ = src.get_ohlcv("SPY", as_of=d, min_bars=1)
        if spy is None:
            continue
        leaked = _diag(spy, full, cfg)                      # shipped behaviour
        trimmed = full[full.index <= pd.Timestamp(d)]       # intended PIT
        pit = _diag(spy, trimmed, cfg)
        rows.append({"as_of": d, "breadth_rows_shipped": int(len(full)),
                     "breadth_rows_pit": int(len(trimmed)),
                     "shipped": leaked, "pit_corrected": pit})
    same_breadth = len({(r["shipped"]["breadth_percentile"],
                         r["shipped"]["breadth_now_pct"]) for r in rows}) == 1
    return {
        "probe_rows": rows,
        "shipped_breadth_is_constant_across_as_of": same_breadth,
        "interpretation": (
            "if the shipped breadth diagnostics are identical for every as-of "
            "date, Engine B is not point-in-time: it always evaluates the last "
            "row of the cache"),
    }


def part2_end_to_end(cfg) -> dict:
    """Re-run the backtest with an in-process PIT-trimmed breadth loader."""
    full = load_breadth(cfg, end="2025-07-31")
    original = pipeline_mod.load_breadth

    def pit_loader(cfg_, end=None):                 # same signature as originals
        if end is None:
            return full
        return full[full.index <= pd.Timestamp(end)]

    pipeline_mod.load_breadth = pit_loader
    try:
        variants = {}
        for tp in GRID:
            c = H.load_config(take_profit_atr_mult=tp)
            res = H.run_backtest(c)
            result = res["result"]
            if "error" in result:
                return {"error": result["error"]}
            variants[str(tp)] = {"take_profit_atr_mult": tp,
                                 "dashboard": H.dashboard(result, c),
                                 "summary": result["summary"]}
    finally:
        pipeline_mod.load_breadth = original
    return {"grid": GRID, "variants": variants}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-e2e", action="store_true")
    args = ap.parse_args()

    cfg = ProductionConfig()
    print("=== PART 1: the leak, directly ===")
    p1 = part1_leak(cfg)
    for r in p1["probe_rows"]:
        s, q = r["shipped"], r["pit_corrected"]
        print(f"  {r['as_of']}: shipped rows={r['breadth_rows_shipped']} "
              f"pct={s['breadth_percentile']} now={s['breadth_now_pct']} "
              f"label={s['label']} | PIT rows={r['breadth_rows_pit']} "
              f"pct={q['breadth_percentile']} now={q['breadth_now_pct']} "
              f"label={q['label']}")
    print(f"  shipped breadth constant across as_of: "
          f"{p1['shipped_breadth_is_constant_across_as_of']}")

    out = {"generated": "2026-10-01",
           "finding": "Regime v1 Engine B (breadth) is not point-in-time in "
                      "historical replay: load_breadth() ignores `end` when the "
                      "cache exists, so compute_regime_decision() always reads "
                      "the cache tail (2025-07-31)",
           "part1_leak": p1}

    if not args.skip_e2e:
        print("\n=== PART 2: end-to-end, PIT-corrected breadth ===")
        p2 = part2_end_to_end(cfg)
        # borrow the shipped (leaky) dashboards for comparison
        leaky = None
        if os.path.exists(LEAKY_GRID_JSON):
            with open(LEAKY_GRID_JSON, encoding="utf-8") as fh:
                leaky = {k: v["dashboard"]
                         for k, v in json.load(fh)["variants"].items()}
        comp = {}
        for tp, v in p2.get("variants", {}).items():
            d = v["dashboard"]
            row = {"pit_corrected": d, "shipped_leaky": (leaky or {}).get(tp)}
            if row["shipped_leaky"]:
                b = row["shipped_leaky"]
                row["delta_return_pp"] = round(
                    d["return_pct"] - b["return_pct"], 2)
                row["delta_sharpe"] = round(d["sharpe"] - b["sharpe"], 3)
                row["delta_maxdd_pp"] = round(d["max_dd_pct"] - b["max_dd_pct"], 2)
                row["delta_trades"] = d["n_trades"] - b["n_trades"]
                row["delta_avg_r"] = round(d["avg_r"] - b["avg_r"], 4)
            comp[tp] = row
            print(f"  TP={tp}: PIT-corrected return={d['return_pct']}% "
                  f"sharpe={d['sharpe']} maxDD={d['max_dd_pct']}% "
                  f"trades={d['n_trades']} avgR={d['avg_r']}"
                  + (f" | vs leaky dRet={row.get('delta_return_pp')}pp "
                     f"dSharpe={row.get('delta_sharpe')} "
                     f"dMaxDD={row.get('delta_maxdd_pp')}pp "
                     f"dTrades={row.get('delta_trades')}"
                     if row.get("shipped_leaky") else ""))
        out["part2_end_to_end"] = {"comparison": comp,
                                   "raw": p2,
                                   "method": "production.pipeline.load_breadth "
                                             "patched in-process only; no file "
                                             "modified"}

    H.save_json(OUT, out)
    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
