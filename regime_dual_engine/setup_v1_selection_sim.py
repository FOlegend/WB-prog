"""
setup_v1_selection_sim.py — READ-ONLY: simulate regime-dependent setup selection
                          on EXISTING trade logs (no new backtest)

Uses reports/setup_v1_rootcause_logs.json (full trade logs from the 3 runs:
breakout / pullback / both, under Regime v1 + PIT).

Simulated selection rules applied to the 'both' run's realized trades:
  R1  BULL: breakout+pullback ; SIDEWAYS: pullback only   (regime-dependent)
  R2  BULL: breakout only     ; SIDEWAYS: pullback only   (strict regime split)
  R3  BULL: breakout+pullback ; SIDEWAYS: NOTHING         (trade only in BULL)
  R4  BULL: pullback only     ; SIDEWAYS: pullback only   (== pullback only)

IMPORTANT CAVEAT: trade-level subsets do NOT model capital reallocation —
skipping a trade frees capital for others in a real portfolio, so net PnL is
NOT additive across subsets. PF / Win% / AvgR are quality metrics and remain
meaningful; net PnL is shown only as an indicator. This is an understanding
step before any live decision.
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


def _perf(trades):
    if not trades:
        return {"n": 0}
    wins = sum(1 for t in trades if t["net_pnl"] > 0)
    gw = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0)
    gl = abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0))
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    return {
        "n": len(trades),
        "pf": round(gw / gl, 3) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "win_pct": round(wins / len(trades) * 100, 1),
        "avg_r": round(float(np.mean(rs)), 3) if rs else None,
        "net_pnl": round(sum(t["net_pnl"] for t in trades), 2),
    }


def main():
    log_path = os.path.join(_REPO_ROOT, "reports", "setup_v1_rootcause_logs.json")
    if not os.path.exists(log_path):
        print("missing setup_v1_rootcause_logs.json — run setup_v1_root_cause.py first")
        return
    with open(log_path) as f:
        logs = json.load(f)

    both = pd.DataFrame(logs["both"]["trades"])
    both["entry_date"] = pd.to_datetime(both["entry_date"])
    print(f"both-run trades: {len(both)}")

    # ---- single-run baselines (from their own runs, clean) ----
    brk = pd.DataFrame(logs["breakout"]["trades"])
    pull = pd.DataFrame(logs["pullback"]["trades"])
    print("\n=== single-run baselines (real runs) ===")
    for name, df in [("Breakout Only", brk), ("Pullback Only", pull)]:
        p = _perf(df.to_dict("records"))
        print(f"  {name:<14} n={p['n']:>4} PF={p['pf']} Win={p['win_pct']}% "
              f"AvgR={p['avg_r']} net=${p['net_pnl']:+.2f}")

    # ---- regime-dependent selection on BOTH-run trades ----
    print("\n=== simulated selection rules (applied to both-run trades) ===")
    def sel(rule):
        keep = []
        for _, t in both.iterrows():
            reg = t["entry_regime"]
            st = t["setup_type"]
            if rule(reg, st):
                keep.append(t.to_dict())
        return keep

    rules = {
        "R1 BULL:brk+pull / SW:pull":   lambda r, s: (r == "BULL") or (r == "SIDEWAYS" and s == "pullback"),
        "R2 BULL:brk / SW:pull":        lambda r, s: (r == "BULL" and s == "breakout") or (r == "SIDEWAYS" and s == "pullback"),
        "R3 BULL only (SW:nothing)":    lambda r, s: r == "BULL",
        "R4 pullback everywhere":       lambda r, s: s == "pullback",
        "R5 BULL:pull / SW:nothing":    lambda r, s: r == "BULL" and s == "pullback",
    }
    sim = {}
    for name, rule in rules.items():
        kept = sel(rule)
        p = _perf(kept)
        sim[name] = p
        print(f"  {name:<28} n={p['n']:>4} PF={p['pf']} Win={p['win_pct']}% "
              f"AvgR={p['avg_r']} net=${p['net_pnl']:+.2f}")

    # ---- yearly profile of each simulated rule (quality only) ----
    print("\n=== yearly PF by rule (from both-run trades) ===")
    both["year"] = both["entry_date"].dt.year
    yearly = {}
    for name, rule in rules.items():
        kept = sel(rule)
        kdf = pd.DataFrame(kept)
        if len(kdf) == 0:
            yearly[name] = {}
            continue
        kdf["y"] = pd.to_datetime(kdf["entry_date"]).dt.year
        yearly[name] = {int(y): _perf(sub.to_dict("records"))["pf"]
                        for y, sub in kdf.groupby("y")}
        print(f"  {name:<28} " + " ".join(f"{y}:{v}" for y, v in sorted(yearly[name].items())))

    # ---- which trades would R2 keep that the pullback-only run already had? ----
    # overlap sanity: pullback-only run's trades vs pullback trades inside both-run
    pull_keys = set(pull.apply(lambda r: r["ticker"] + "|" + str(pd.Timestamp(r["entry_date"]).date()), axis=1))
    both_pull = both[both["setup_type"] == "pullback"]
    both_pull_keys = set(both_pull.apply(lambda r: r["ticker"] + "|" + str(pd.Timestamp(r["entry_date"]).date()), axis=1))
    print(f"\n  pullback-only run trades: {len(pull_keys)}; pullback trades in both-run: {len(both_pull_keys)}; "
          f"overlap: {len(pull_keys & both_pull_keys)}")

    out = {"baselines": {"Breakout Only": _perf(brk.to_dict("records")),
                         "Pullback Only": _perf(pull.to_dict("records"))},
           "simulations": sim, "yearly_pf_by_rule": yearly}
    with open(os.path.join(_REPO_ROOT, "reports", "setup_v1_selection_sim.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\n  Saved -> reports/setup_v1_selection_sim.json")


if __name__ == "__main__":
    main()
