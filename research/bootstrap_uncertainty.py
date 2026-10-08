"""
research/bootstrap_uncertainty.py — how much of S1–S3 survives sampling noise?

S1–S3 reported point estimates with no confidence intervals. A Sharpe of 1.107
against a baseline of 0.513 looks decisive until you ask how much of that gap a
different draw of the same 19 months would produce on its own. This script
answers that, and it is the check the S1 §12 discussion was missing.

Method
------
BLOCK BOOTSTRAP on daily equity returns, PAIRED across variants.

* Pairs matter. All variants run over the same dates against the same market, so
  their daily returns are strongly correlated. Resampling each curve
  independently would destroy that pairing and inflate the intervals. The same
  block indices are therefore applied to every variant in a draw.
* Blocks, not i.i.d. days. Daily strategy returns are autocorrelated (a position
  is held for ~10 days), so an i.i.d. bootstrap would understate the true
  variance. Blocks of 10 trading days (~2 weeks) preserve it.
* The regime recomputes nothing. Each block-resampled equity path is scored with
  the SAME `_summary` conventions the backtest uses, so the interval is on the
  reported metrics, not on a proxy.

What it can and cannot settle
----------------------------
It CAN say whether a variant's edge is larger than the noise floor at this sample
size. It CANNOT validate the strategy, and a wide interval is not evidence that a
variant is bad — 168 trades over 19 months is simply not many. That distinction is
kept explicit in the output rather than collapsed into a verdict.
"""
from __future__ import annotations

import argparse
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

BLOCK = 10          # trading days per block
N_BOOT = 5000
SEED = 20261002

STAGES = {
    "s1": ("stop_atr_mult", 1.5, [1.2, 1.8, 2.0]),
    "s2": ("trailing_atr_mult", 1.5, [2.0, 2.5]),
    "s3": ("trailing_trigger_r", 1.0, [0.5, 1.5]),
}


def _daily_returns(curve: list) -> list[float]:
    eq = [c["equity"] for c in curve]
    out = []
    for a, b in zip(eq, eq[1:]):
        out.append((b / a - 1.0) if a else 0.0)
    return out


def _metrics_from_path(path: list[float]) -> dict:
    """Sharpe / MaxDD / total return for a synthetic daily-return path.

    Uses the same conventions as production/backtest.py::_summary: Sharpe is
    mean/std x sqrt(252) on daily equity returns; drawdown is measured on the
    cumulative path.
    """
    eq, v = 1.0, []
    for r in path:
        eq *= (1.0 + r)
        v.append(eq)
    n = len(path)
    if n < 2:
        return {"sharpe": 0.0, "max_dd_pct": 0.0, "return_pct": 0.0}
    mean = stats.mean(path)
    sd = stats.pstdev(path)
    sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else 0.0
    peak, mdd = v[0], 0.0
    for x in v:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    return {"sharpe": sharpe, "max_dd_pct": mdd * 100.0,
            "return_pct": (v[-1] - 1.0) * 100.0}


def _block_indices(n: int, n_boot: int, rng) -> list[list[int]]:
    """One set of resampled index sequences, reused across all variants."""
    n_blocks = math.ceil(n / BLOCK)
    max_start = n - BLOCK
    out = []
    for _ in range(n_boot):
        idx = []
        for _ in range(n_blocks):
            s = rng.randint(0, max_start) if max_start > 0 else 0
            idx.extend(range(s, min(s + BLOCK, n)))
        out.append(idx)
    return out


def _pct(sorted_xs: list[float], p: float) -> float:
    if not sorted_xs:
        return float("nan")
    k = min(len(sorted_xs) - 1, max(0, int(round(p * (len(sorted_xs) - 1)))))
    return sorted_xs[k]


def run_stage(stage: str) -> dict:
    param, control_v, grid = STAGES[stage]
    curves, trades = {}, {}
    for v in [control_v] + grid:
        cfg = H.load_config(**{param: v})
        r = H.run_backtest(cfg)["result"]
        curves[v] = _daily_returns(r["equity_curve"])
        trades[v] = r["trade_log"]
        print(f"    ran {param}={v}", flush=True)

    n = min(len(c) for c in curves.values())
    for v in curves:
        curves[v] = curves[v][:n]

    import random
    rng = random.Random(SEED)
    boot_idx = _block_indices(n, N_BOOT, rng)

    boot = {v: {"sharpe": [], "max_dd_pct": [], "return_pct": []} for v in curves}
    for idx in boot_idx:
        for v, path in curves.items():
            m = _metrics_from_path([path[i] for i in idx])
            for k in boot[v]:
                boot[v][k].append(m[k])

    point = {v: _metrics_from_path(curves[v]) for v in curves}
    point_r = {v: round(stats.mean(t.get("r_multiple") or 0
                                  for t in trades[v]), 4) for v in curves}

    out = {"point_estimate": {str(v): {**point[v], "avg_r": point_r[v]}
                               for v in curves},
           "n_days": n, "block_days": BLOCK, "n_boot": N_BOOT,
           "paired": True, "variants": {}}

    for v in grid:
        c = control_v
        row = {}
        for metric in ("sharpe", "max_dd_pct", "return_pct"):
            d = sorted(b - a for a, b in zip(boot[c][metric], boot[v][metric]))
            lo, hi = _pct(d, 0.025), _pct(d, 0.975)
            row[metric] = {
                "delta_point": round(point[v][metric] - point[c][metric], 4),
                "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                "excludes_zero": bool(lo > 0 or hi < 0),
                "prob_variant_better": round(
                    sum(1 for x in d if x > 0) / len(d), 4),
            }
        # avg R bootstrap over TRADES (independent of the equity path)
        r_c = [t.get("r_multiple") for t in trades[c]
               if t.get("r_multiple") is not None]
        r_v = [t.get("r_multiple") for t in trades[v]
               if t.get("r_multiple") is not None]
        dr = []
        rr = random.Random(SEED + 1)
        for _ in range(N_BOOT):
            sc = stats.mean(rr.choices(r_c, k=len(r_c)))
            sv = stats.mean(rr.choices(r_v, k=len(r_v)))
            dr.append(sv - sc)
        dr.sort()
        row["avg_r"] = {
            "delta_point": round(point_r[v] - point_r[c], 4),
            "ci95_low": round(_pct(dr, 0.025), 4),
            "ci95_high": round(_pct(dr, 0.975), 4),
            "excludes_zero": bool(_pct(dr, 0.025) > 0 or _pct(dr, 0.975) < 0),
            "prob_variant_better": round(
                sum(1 for x in dr if x > 0) / len(dr), 4),
        }
        out["variants"][str(v)] = row

    out["interpretation_rule"] = (
        "A delta whose 95% CI excludes zero is larger than this sample's noise "
        "floor. A CI spanning zero means the sample cannot distinguish the "
        "variant from the control — which is NOT the same as the variant being "
        "harmful, and with 128-201 trades it mostly reflects limited power.")
    out["caveats"] = [
        "The bootstrap resamples the realised path; it cannot correct for the "
        "selection channel documented in S1-D, where a variant's trade SET "
        "differs from the control's. These intervals cover sampling variation "
        "in the equity path, not the mechanism behind the difference.",
        "Block length 10 is a judgement call matched to the ~10-day average "
        "holding period. Shorter blocks understate variance; longer blocks "
        "understate the effective sample size.",
        "168 trades over 19 months is a small sample for any of these "
        "comparisons. A wide CI is a statement about power, not about merit.",
    ]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="s1,s2,s3")
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"bootstrap_uncertainty_pitcorrected_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    payload = {
        "generated": datetime.date.today().isoformat(),
        "deliverable": "block-bootstrap uncertainty on S1–S3 headline metrics",
        "method": {"block_days": BLOCK, "n_boot": N_BOOT, "seed": SEED,
                   "paired_across_variants": True,
                   "sharpe": "mean/std x sqrt(252) on daily equity returns "
                             "(production convention)"},
        "stages": {},
    }
    for stage in args.stages.split(","):
        stage = stage.strip()
        if not stage:
            continue
        print(f"  === {stage.upper()} ===", flush=True)
        payload["stages"][stage.upper()] = run_stage(stage)

    H.save_json(args.out, payload)

    print("\n=== block-bootstrap uncertainty (95% CI on the delta vs control) ===")
    for stage, res in payload["stages"].items():
        print(f"\n{stage}  (n_days={res['n_days']}, {res['n_boot']} paired draws)")
        for v, row in res["variants"].items():
            sh, av = row["sharpe"], row["avg_r"]
            print(f"  {v:>5}  dSharpe {sh['delta_point']:>7} "
                  f"[{sh['ci95_low']:>7}, {sh['ci95_high']:>7}]  "
                  f"P(better)={sh['prob_variant_better']:.3f}  "
                  f"{'SIGNIF' if sh['excludes_zero'] else 'noise  '}   "
                  f"dAvgR {av['delta_point']:>7} "
                  f"[{av['ci95_low']:>7}, {av['ci95_high']:>7}] "
                  f"P(better)={av['prob_variant_better']:.3f} "
                  f"{'SIGNIF' if av['excludes_zero'] else 'noise'}")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
