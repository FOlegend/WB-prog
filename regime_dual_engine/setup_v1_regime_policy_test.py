"""
setup_v1_regime_policy_test.py — Minimal Regime-Dependent Selection Test (Setup V1)

Baseline & 2 minimal alternatives on frozen Regime v1 + PIT breadth:

  A  Pullback Only (all regimes)                          [baseline]
  B  BULL: breakout+pullback (pullback-first rule)       [simple regime-dep.]
     SIDEWAYS: pullback only
     BEAR: no long setup (regime gate, unchanged)
  C  BULL: breakout only                                 [conservative split]
     SIDEWAYS: pullback only
     BEAR: no long setup

Selection rule B (exact, deterministic — defined BEFORE evaluation):
  for a ticker/day where BOTH breakout and pullback are valid:
    1. pullback wins whenever it is valid & score >= threshold
       (tighter stop / better R:R per reviewer recommendation)
    2. else breakout if valid
    3. else the higher-score candidate (keeps legacy trade count)
  This avoids the max(score) survivorship-residue problem.

Marginal contribution: breakout trades that appear in B/C but NOT in the
pullback-only run are isolated; their standalone PF / Win% / AvgR reported.
If marginal PF < 1.0, added breakout trades drag portfolio efficiency.

OOS: yearly return / MaxDD per variant from the equity curve (2018..2025) —
any in-sample edge must not come from a single year.

No parameter optimization. Regime v1 / Screener / setup_agent.py untouched.
Run (background, ~7 min):
  python regime_dual_engine/setup_v1_regime_policy_test.py
"""
from __future__ import annotations

import os
import sys
import json
import warnings
from collections import Counter

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
warnings.filterwarnings("ignore")

from config import Config
from dynamic_universe_backtest import build_rebalance_calendar, _exposure_pct
from src.backtest.engine import compute_metrics

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.pit_breadth_data import get_breadth
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data

START, END, TOP_N = "2018-01-01", "2025-07-31", 20
YEARS = (pd.Timestamp(END) - pd.Timestamp(START)).days / 365.25


def _cagr(summary):
    if summary.get("final_equity", 0) > 0:
        return ((summary["final_equity"] / summary["starting_equity"]) ** (1 / YEARS) - 1) * 100
    return 0.0


def _avg_r(trades):
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    return round(float(np.mean(rs)), 3) if rs else None


def _pf(trades):
    gw = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    gl = abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0))
    return round(gw / gl, 3) if gl > 0 else (float("inf") if gw > 0 else 0.0)


def _perf(trades):
    if not trades:
        return {"n": 0}
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    return {
        "n": len(trades),
        "pf": _pf(trades),
        "win_pct": round(wins / len(trades) * 100, 1),
        "avg_r": _avg_r(trades),
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
    }


def _yearly_metrics(summary):
    eq = pd.DataFrame(summary["equity_curve"])
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    out = {}
    for y, sub in eq.groupby("year"):
        if len(sub) < 5:
            continue
        ret = (sub["equity"].iloc[-1] / sub["equity"].iloc[0] - 1) * 100
        dd = ((sub["equity"] / sub["equity"].cummax() - 1) * 100).min()
        out[int(y)] = {"return_pct": round(float(ret), 2), "max_dd_pct": round(float(dd), 2)}
    return out


def _run(setup_types, policy=None):
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = setup_types
    cfg.setup_score_threshold = 0.5
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
    if policy is not None:
        cfg.setup_regime_policy = policy
    dual_cfg = DualEngineConfig(hmm_weight=0.5, breadth_weight=0.5,
                                enable_dist_day_overlay=False)
    all_data = load_data(end=END)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{START}_{END}_top{TOP_N}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    breadth = get_breadth("pit", rebuild=False)
    eng = DualEngineBacktest(cfg, all_data, all_rd, buckets, dual_cfg, breadth,
                             top_n=TOP_N, start=START, end=END,
                             no_regime=False, benchmark="SPY", progress=False)
    return eng.run()


def main():
    print("=" * 78)
    print("  SETUP V1 — MINIMAL REGIME-DEPENDENT SELECTION TEST")
    print(f"  {START}..{END} | top{TOP_N} | Regime v1 + PIT | thr=0.5 | next-open | gap-aware")
    print("=" * 78, flush=True)

    variants = {}
    # A: pullback only (no policy)
    print("\n--- A. Pullback Only (baseline) ---", flush=True)
    variants["A_pullback_only"] = _run(["pullback"], policy=None)
    # B: BULL both (pullback-first), SIDEWAYS pullback
    policy_b = {"BULL": "both_pullback_first", "SIDEWAYS": "pullback"}
    print("\n--- B. BULL: brk+pull(pullback-first) / SW: pullback ---", flush=True)
    variants["B_bull_both_sw_pull"] = _run(["breakout", "pullback"], policy=policy_b)
    # C: BULL breakout only, SIDEWAYS pullback
    policy_c = {"BULL": "breakout", "SIDEWAYS": "pullback"}
    print("\n--- C. BULL: breakout only / SW: pullback ---", flush=True)
    variants["C_bull_brk_sw_pull"] = _run(["breakout", "pullback"], policy=policy_c)

    # ---- comparison table ----
    print("\n" + "=" * 78)
    print("  COMPARISON TABLE")
    print("=" * 78)
    print(f"{'Variant':<26}{'CAGR%':>8}{'Sharpe':>8}{'MaxDD%':>9}{'PF':>7}{'Win%':>7}"
          f"{'Trades':>8}{'Exp%':>7}{'AvgR':>7}")
    results = {}
    for name, s in variants.items():
        m = compute_metrics(s, Config())
        ex = _exposure_pct(s["equity_curve"])
        cagr = _cagr(s)
        avg_r = _avg_r(s["trade_log"])
        results[name] = {
            "cagr_pct": round(cagr, 2), "sharpe": m["sharpe"],
            "max_dd_pct": m["max_drawdown_pct"], "pf": m["profit_factor"],
            "win_pct": round(m["win_rate"] * 100, 1), "trades": m["total_trades"],
            "exposure_pct": round(ex, 1), "avg_r": avg_r,
            "setup_dist": dict(Counter(t.get("setup_type") for t in s["trade_log"])),
            "yearly": _yearly_metrics(s),
            "_trades": s["trade_log"],
        }
        print(f"{name:<26}{cagr:>+8.2f}{m['sharpe']:>8.2f}{m['max_drawdown_pct']:>9.2f}"
              f"{str(m['profit_factor']):>7}{m['win_rate']*100:>7.1f}"
              f"{m['total_trades']:>8}{ex:>7.1f}{str(avg_r):>7}")
        print(f"    setup dist: {results[name]['setup_dist']}")

    # ---- performance by regime & setup type ----
    print("\n  PERFORMANCE BY REGIME (entry regime):")
    for name, s in variants.items():
        tr = pd.DataFrame(s["trade_log"])
        parts = []
        for reg in ["BULL", "SIDEWAYS", "BEAR"]:
            sub = tr[tr["entry_regime"] == reg]
            if not sub.empty:
                p = _perf(sub.to_dict("records"))
                parts.append(f"{reg}:n={p['n']},PF={p['pf']},Win={p['win_pct']}%,R={p['avg_r']}")
        print(f"    {name:<26} " + " | ".join(parts))

    print("\n  PERFORMANCE BY SETUP TYPE (B and C runs):")
    for name in ["B_bull_both_sw_pull", "C_bull_brk_sw_pull"]:
        tr = pd.DataFrame(variants[name]["trade_log"])
        for st, sub in tr.groupby("setup_type"):
            p = _perf(sub.to_dict("records"))
            print(f"    {name:<26} {st:<9} n={p['n']:<4} PF={p['pf']} Win={p['win_pct']}% "
                  f"AvgR={p['avg_r']} net=${p['net_pnl']:+.2f}")

    # ---- marginal contribution of added breakout trades ----
    print("\n  MARGINAL CONTRIBUTION (added breakout trades vs pullback-only run):")
    pull_keys = {(t["ticker"], str(t["entry_date"])) for t in variants["A_pullback_only"]["trade_log"]}
    for name in ["B_bull_both_sw_pull", "C_bull_brk_sw_pull"]:
        tr = variants[name]["trade_log"]
        added_brk = [t for t in tr if t.get("setup_type") == "breakout"
                     and (t["ticker"], str(t["entry_date"])) not in pull_keys]
        p = _perf(added_brk)
        verdict = "improves" if (p["n"] > 0 and p["pf"] >= 1.0) else "DRAGS" if p["n"] > 0 else "none"
        print(f"    {name:<26} added breakout n={p['n']:<4} PF={p['pf']} "
              f"Win={p['win_pct']}% AvgR={p['avg_r']} -> {verdict}")

    # ---- yearly (OOS-style stability) ----
    print("\n  YEARLY RETURN % BY VARIANT (equity curve):")
    for name, s in variants.items():
        ys = " ".join(f"{y}:{d['return_pct']:+.1f}%" for y, d in sorted(results[name]["yearly"].items()))
        print(f"    {name:<26} {ys}")

    out = {k: {kk: vv for kk, vv in v.items() if kk != "_trades"}
           for k, v in results.items()}
    with open(os.path.join(_REPO_ROOT, "reports", "setup_v1_regime_policy.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\n  Saved -> reports/setup_v1_regime_policy.json")


if __name__ == "__main__":
    main()
