"""
v32_robustness.py — Equal Weights Robustness Validation (Step 2)

Per reviewer: "目標不是找最佳組合，而是問：Equal 是否在不同設定下仍然穩？"

Tests Equal Weights (gate mode, score_full=70, all weights 0.20) across:
  1. Top 10 / 20 / 30  — universe size robustness
  2. Higher slippage    — 0.2% vs 0.05% (4x)
  3. Higher commission  — $0.005/share vs $0

If only Top 20 Monthly works well, it might be overfit.
If Equal is stable across all settings, confidence is high.

Outputs:
  reports/v32_robustness.html  — comparison table + verdict
  reports/v32_robustness.json  — raw results
"""
from __future__ import annotations

import os
import sys
import json
import time
import warnings
import base64
import io
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from screen_as_of import screen_all_buckets
from dynamic_universe_backtest import (
    DynamicBacktestEngine,
    build_rebalance_calendar,
    _exposure_pct,
    MARKET_PHASES,
    _phase_metrics,
    _spy_buyhold_equity,
)
from src.backtest.engine import compute_metrics

warnings.filterwarnings("ignore")


# ===========================================================================
# Robustness test configurations
# ===========================================================================
# All use Equal Weights (gate mode, score_full=70, all weights 0.20)
# Only ONE variable changes per test (per reviewer's controlled experiment rule)
ROBUSTNESS_TESTS = [
    {
        "name": "Top 10",
        "top_n": 10,
        "slippage_pct": 0.0005,
        "commission_per_share": 0.0,
        "desc": "Smaller universe = more concentrated. Tests if Equal works with fewer tickers.",
    },
    {
        "name": "Top 20 (baseline)",
        "top_n": 20,
        "slippage_pct": 0.0005,
        "commission_per_share": 0.0,
        "desc": "Baseline from v3.2 experiment. Already have results.",
    },
    {
        "name": "Top 30",
        "top_n": 30,
        "slippage_pct": 0.0005,
        "commission_per_share": 0.0,
        "desc": "Larger universe = more diversified. Tests if Equal works with more tickers.",
    },
    {
        "name": "Top 20 + High Slippage",
        "top_n": 20,
        "slippage_pct": 0.002,
        "commission_per_share": 0.0,
        "desc": "4x slippage (0.2% vs 0.05%). Tests cost sensitivity.",
    },
    {
        "name": "Top 20 + High Commission",
        "top_n": 20,
        "slippage_pct": 0.0005,
        "commission_per_share": 0.005,
        "desc": "$0.005/share commission (typical discount broker). Tests cost sensitivity.",
    },
]


# ===========================================================================
# Data loading (direct from cache — avoids get_all_data web scraping)
# ===========================================================================
def load_data_from_cache(end: str = "2025-07-31"):
    """Load OHLCV data directly from cache directory."""
    cache_dir = os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(cache_dir):
        cache_dir = os.path.join(_REPO_ROOT, "data", "cache", "equities")

    # Get all available tickers from cache
    all_tickers = [f.replace(".csv", "") for f in os.listdir(cache_dir)
                   if f.endswith(".csv")]
    print(f"  Found {len(all_tickers)} cached tickers in {cache_dir}")

    cutoff = pd.Timestamp(end)
    all_data = {}
    for t in sorted(all_tickers):
        csv_path = os.path.join(cache_dir, f"{t}.csv")
        try:
            df = pd.read_csv(csv_path, parse_dates=["datetime"])
            if len(df) > 0:
                df = df[df["datetime"] <= cutoff].reset_index(drop=True)
                all_data[t] = df
        except Exception:
            pass

    print(f"  Loaded {len(all_data)} tickers")
    return all_data


def get_buckets(all_data, rebalance_dates, top_n, benchmark="SPY"):
    """Get or create cached buckets for given top_n."""
    start_str = "2018-01-01"
    end_str = "2025-07-31"
    cache_path = os.path.join(
        _REPO_ROOT, "reports",
        f"dynamic_buckets_{start_str}_{end_str}_top{top_n}.json")

    if os.path.exists(cache_path):
        print(f"  Loading cached buckets (top {top_n}) from {cache_path}")
        with open(cache_path) as f:
            return json.load(f)

    # For top_n < 20, we can derive from the top 20 buckets (take first N)
    if top_n < 20:
        top20_path = os.path.join(
            _REPO_ROOT, "reports",
            f"dynamic_buckets_{start_str}_{end_str}_top20.json")
        if os.path.exists(top20_path):
            print(f"  Deriving top {top_n} from cached top 20 buckets")
            with open(top20_path) as f:
                top20 = json.load(f)
            return {rd: tickers[:top_n] for rd, tickers in top20.items()}

    # For top_n > 20, need to re-screen
    print(f"  Re-screening {len(rebalance_dates)} buckets for top {top_n}...")
    buckets = screen_all_buckets(all_data, rebalance_dates, cfg=Config(),
                                  top_n=top_n, benchmark=benchmark,
                                  verbose=True)
    # Cache to disk
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(buckets, f, indent=1)
    print(f"  Cached {len(buckets)} buckets to {cache_path}")
    return buckets


# ===========================================================================
# Run one robustness test
# ===========================================================================
def run_one_test(name, spec, all_data, all_rd, buckets, start, end):
    """Run one robustness test with Equal Weights."""
    print(f"\n{'='*60}")
    print(f"  Test: {name}")
    print(f"  {spec['desc']}")
    print(f"{'='*60}")

    cfg = Config()
    # Equal Weights (the v3.2 candidate)
    cfg.regime_score_weights = {"hmm": 0.20, "ma": 0.20, "ker": 0.20,
                                 "adx": 0.20, "dist": 0.20}
    cfg.regime_exposure_mode = "gate"
    cfg.regime_score_full = 70.0
    # Cost overrides
    cfg.slippage_pct = spec["slippage_pct"]
    cfg.commission_per_share = spec["commission_per_share"]

    eng = DynamicBacktestEngine(
        cfg, all_data, all_rd, buckets, top_n=spec["top_n"],
        start=start, end=end, no_regime=False,
        benchmark="SPY", progress=True,
    )
    summary = eng.run()
    metrics = compute_metrics(summary, cfg)
    metrics["exposure_pct"] = _exposure_pct(summary["equity_curve"])
    metrics["slippage_pct"] = spec["slippage_pct"]
    metrics["commission_per_share"] = spec["commission_per_share"]
    metrics["top_n"] = spec["top_n"]

    m = metrics
    print(f"\n  >> Return={m['total_return_pct']:+.2f}%  "
          f"Sharpe={m['sharpe']:.2f}  "
          f"MaxDD={m['max_drawdown_pct']:.2f}%  "
          f"PF={m['profit_factor']}  "
          f"Win={m['win_rate']*100:.1f}%  "
          f"Trades={m['total_trades']}  "
          f"Exp={m['exposure_pct']}%")

    return {"summary": summary, "metrics": metrics, "spec": spec}


# ===========================================================================
# Robustness evaluation
# ===========================================================================
def evaluate_robustness(results: dict) -> dict:
    """Evaluate whether Equal Weights is stable across all tests.

    Criteria:
    - Stable = Return > 0, Sharpe > 0.3, MaxDD > -25% across ALL tests
    - The baseline (Top 20) is the reference point
    - If other tests deviate too much, it might be overfit
    """
    baseline = results["Top 20 (baseline)"]["metrics"]
    baseline_return = baseline["total_return_pct"]
    baseline_sharpe = baseline["sharpe"]
    baseline_maxdd = baseline["max_drawdown_pct"]

    evaluations = []
    for name, res in results.items():
        m = res["metrics"]
        ret = m["total_return_pct"]
        sh = m["sharpe"]
        dd = m["max_drawdown_pct"]
        pf = 0.0 if m["profit_factor"] == "inf" else float(m["profit_factor"])

        # Stability checks
        positive_return = ret > 0
        reasonable_sharpe = sh > 0.3
        controlled_drawdown = dd > -25.0
        pf_above_1 = pf > 1.0

        # Deviation from baseline
        ret_dev = abs(ret - baseline_return) / max(abs(baseline_return), 1) * 100
        sharpe_dev = abs(sh - baseline_sharpe)
        maxdd_dev = abs(dd - baseline_maxdd)

        # Verdict for this test
        stable = positive_return and reasonable_sharpe and controlled_drawdown and pf_above_1
        verdict = "STABLE" if stable else "UNSTABLE"

        evaluations.append({
            "test": name,
            "return_pct": ret,
            "sharpe": sh,
            "max_dd_pct": dd,
            "pf": pf,
            "win_rate": m["win_rate"],
            "trades": m["total_trades"],
            "exposure_pct": m["exposure_pct"],
            "positive_return": positive_return,
            "reasonable_sharpe": reasonable_sharpe,
            "controlled_drawdown": controlled_drawdown,
            "pf_above_1": pf_above_1,
            "return_dev_pct": round(ret_dev, 1),
            "sharpe_dev": round(sharpe_dev, 2),
            "maxdd_dev": round(maxdd_dev, 2),
            "verdict": verdict,
        })

    # Overall verdict
    n_stable = sum(1 for e in evaluations if e["verdict"] == "STABLE")
    n_total = len(evaluations)
    overall = "ROBUST" if n_stable == n_total else (
        "MOSTLY STABLE" if n_stable >= n_total - 1 else "FRAGILE")

    return {"per_test": evaluations, "n_stable": n_stable,
            "n_total": n_total, "overall_verdict": overall}


# ===========================================================================
# HTML report
# ===========================================================================
def build_robustness_html(results: dict, evaluation: dict, out_path: str):
    """Generate robustness validation report."""
    baseline = results["Top 20 (baseline)"]["metrics"]

    # Main comparison table
    table_html = ""
    for ev in evaluation["per_test"]:
        vcolor = {"STABLE": "#c8e6c9", "UNSTABLE": "#ffcdd2"}.get(ev["verdict"], "#eeeeee")
        pf = ev["pf"]
        pf_s = f"{pf:.2f}" if pf > 0 else "inf"
        table_html += (
            f"<tr><td><b>{ev['test']}</b></td>"
            f"<td>{ev['return_pct']:+.2f}</td>"
            f"<td>{ev['sharpe']:.2f}</td>"
            f"<td>{ev['max_dd_pct']:.2f}</td>"
            f"<td>{pf_s}</td>"
            f"<td>{ev['win_rate']*100:.1f}</td>"
            f"<td>{ev['trades']}</td>"
            f"<td>{ev['exposure_pct']}</td>"
            f"<td>{ev['return_dev_pct']}%</td>"
            f"<td>{ev['sharpe_dev']:.2f}</td>"
            f"<td>{ev['maxdd_dev']:.2f}</td>"
            f"<td style='background:{vcolor};text-align:center'><b>{ev['verdict']}</b></td></tr>"
        )

    # Per-test detail
    detail_html = ""
    for ev in evaluation["per_test"]:
        checks = []
        checks.append(("Return > 0%", ev["positive_return"], f"Return={ev['return_pct']:+.2f}%"))
        checks.append(("Sharpe > 0.3", ev["reasonable_sharpe"], f"Sharpe={ev['sharpe']:.2f}"))
        checks.append(("MaxDD > -25%", ev["controlled_drawdown"], f"MaxDD={ev['max_dd_pct']:.2f}%"))
        checks.append(("PF > 1.0", ev["pf_above_1"], f"PF={ev['pf']:.2f}"))

        checks_html = ""
        for label, passed, detail in checks:
            color = "#c8e6c9" if passed else "#ffcdd2"
            icon = "PASS" if passed else "FAIL"
            checks_html += f"<span style='background:{color};padding:2px 6px;border-radius:3px;font-size:11px;margin-right:6px'>{icon}</span> {label} ({detail})"

        detail_html += f"<div style='margin:8px 0'><b>{ev['test']}</b>: {checks_html}</div>"

    # Overall verdict color
    ovr_color = {"ROBUST": "#c8e6c9", "MOSTLY STABLE": "#fff9c4",
                 "FRAGILE": "#ffcdd2"}.get(evaluation["overall_verdict"], "#eeeeee")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>v3.2 Robustness Validation — Equal Weights</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 32px; color:#222; }}
 h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 30px; }}
 h3 {{ font-size: 14px; margin: 14px 0 4px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; font-size: 12.5px; }}
 th, td {{ border: 1px solid #ccc; padding: 5px 8px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 th {{ background: #fafafa; }}
 .note {{ background:#fff8e1; border-left:4px solid #ffc107; padding:10px 14px; margin:14px 0; font-size:13px; }}
 .verdict-box {{ display:inline-block; padding:8px 20px; border-radius:6px; font-size:16px; font-weight:bold; margin:10px 0; }}
</style></head>
<body>
<h1>v3.2 Robustness Validation — Equal Weights</h1>
<p>Period <b>2018-01-01 ~ 2025-07-31</b> - Same dynamic universe - Equal Weights (gate mode, score_full=70)<br>
Per reviewer: "目標不是找最佳組合，而是問：Equal 是否在不同設定下仍然穩？"</p>

<div class="note">
<b>Test methodology:</b> Each test changes ONLY ONE variable (top N, slippage, or commission).
All other settings remain identical. If Equal Weights is robust, it should perform
reasonably across ALL tests — not just the baseline.
</div>

<h2>1. Overall Verdict</h2>
<div class="verdict-box" style="background:{ovr_color}">
  {evaluation['overall_verdict']} — {evaluation['n_stable']}/{evaluation['n_total']} tests stable
</div>
<p>If ROBUST: Equal Weights can be promoted to v3.2 production candidate with high confidence.<br>
If MOSTLY STABLE: Investigate the unstable test before promoting.<br>
If FRAGILE: Equal Weights may be overfit to Top 20 Monthly — do not promote.</p>

<h2>2. Comparison Table</h2>
<table>
<tr><th>Test</th><th>Ret%</th><th>Sharpe</th><th>MaxDD%</th><th>PF</th>
<th>Win%</th><th>Trades</th><th>Exp%</th>
<th>Ret Dev</th><th>Sharpe Dev</th><th>MaxDD Dev</th><th>Verdict</th></tr>
{table_html}
</table>
<p><i>Dev = deviation from Top 20 baseline. Large deviations suggest sensitivity to that parameter.</i></p>

<h2>3. Per-Test Stability Checks</h2>
{detail_html}

<h2>4. Interpretation Guide</h2>
<div class="note">
<ul>
<li><b>Top 10:</b> If return drops significantly, Equal needs diversified universe to work.</li>
<li><b>Top 30:</b> If return drops, adding more tickers dilutes the edge. If it improves, the edge scales.</li>
<li><b>High Slippage:</b> If Sharpe drops below 0.3, the strategy is too cost-sensitive for real trading.</li>
<li><b>High Commission:</b> If PF drops below 1.0, the strategy can't survive typical broker costs.</li>
</ul>
</div>

<div class="note">
<b>Next steps after robustness:</b><br>
If ROBUST -> Proceed to walk-forward validation (Step 3).<br>
If MOSTLY STABLE -> Investigate unstable test, then decide.<br>
If FRAGILE -> Do not promote Equal Weights. Reconsider approach.
</div>
</body></html>"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


# ===========================================================================
# Main
# ===========================================================================
def main():
    start = "2018-01-01"
    end = "2025-07-31"

    print("=" * 70)
    print("  v3.2 ROBUSTNESS VALIDATION — Equal Weights")
    print("  Is Equal stable across different settings?")
    print("=" * 70)

    t0 = time.time()

    # 1. Load data
    print("\nLoading OHLCV data from cache...")
    all_data = load_data_from_cache(end=end)
    if "SPY" not in all_data:
        raise RuntimeError("SPY missing from data cache")

    # 2. Build rebalance calendar
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"  {len(all_rd)} rebalance dates")

    # 3. Run each test
    results = {}
    for spec in ROBUSTNESS_TESTS:
        name = spec["name"]
        print(f"\nPreparing buckets for: {name}")

        # Get buckets for this top_n
        buckets = get_buckets(all_data, all_rd, spec["top_n"])

        # Run test
        res = run_one_test(name, spec, all_data, all_rd, buckets, start, end)
        results[name] = res

    elapsed = time.time() - t0

    # 4. Evaluate robustness
    print("\n" + "=" * 70)
    print("  ROBUSTNESS EVALUATION")
    print("=" * 70)
    evaluation = evaluate_robustness(results)
    for ev in evaluation["per_test"]:
        print(f"  [{ev['verdict']}] {ev['test']}: "
              f"Return={ev['return_pct']:+.2f}%  "
              f"Sharpe={ev['sharpe']:.2f}  "
              f"MaxDD={ev['max_dd_pct']:.2f}%  "
              f"PF={ev['pf']:.2f}")
    print(f"\n  Overall: {evaluation['overall_verdict']} "
          f"({evaluation['n_stable']}/{evaluation['n_total']} stable)")

    # 5. Generate reports
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)

    r1 = os.path.join(out_dir, "v32_robustness.html")
    build_robustness_html(results, evaluation, r1)

    r2 = os.path.join(out_dir, "v32_robustness.json")
    dump = {
        "meta": {"start": start, "end": end, "candidate": "Equal Weights gate mode"},
        "evaluation": evaluation,
        "tests": {name: {"metrics": res["metrics"], "spec": res["spec"]}
                  for name, res in results.items()},
        "elapsed_seconds": round(elapsed, 1),
    }
    with open(r2, "w") as f:
        json.dump(dump, f, indent=2, default=str)

    print(f"\n{'='*70}")
    print(f"  DONE in {elapsed:.0f}s")
    print(f"{'='*70}")
    print(f"  Report (HTML): {r1}")
    print(f"  Results (JSON): {r2}")


if __name__ == "__main__":
    main()
