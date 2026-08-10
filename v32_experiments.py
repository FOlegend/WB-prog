"""
v32_experiments.py — Controlled Repair Experiments for Regime Engine v3.2

Per reviewer feedback: "Regime should be a throttle, not a door."

4 variants on the SAME 2018-2025 dynamic universe (frozen parameters):
  - No Regime (baseline)     : size_mult=1.0 always, no regime filtering
  - Exp A: Threshold 60      : gate mode, score_full=60 (benchmark)
  - Exp B: Continuous         : continuous risk multiplier (PRIORITY 1)
  - Equal Weights             : gate mode, all weights 0.20 (challenger)

Pass criteria (5):
  1. MaxDD < -25% (better than No Regime's -31.72%)
  2. PF >= 1.15
  3. Sharpe >= 0.50
  4. 2023 must re-engage (exposure > 30%, return > 0%)
  5. 2020/2022 must still be protected (stress MaxDD better than No Regime)

Outputs:
  reports/v32_comparison.html       — equity curves + metrics + pass/fail
  reports/v32_2023_diagnostic.html  — month-by-month recovery table
  reports/v32_results.json          — raw results for further analysis

Run:
  python v32_experiments.py
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
# Experiment Variants
# ===========================================================================
V32_VARIANTS = {
    "No Regime": {
        "no_regime": True,
        "weights": None,
        "exposure_mode": "gate",
        "score_full": 70.0,
        "desc": "Baseline — no regime filtering, size_mult=1.0 always",
    },
    "Exp A: Threshold 60": {
        "no_regime": False,
        "weights": {"hmm": 0.20, "ma": 0.30, "ker": 0.18, "adx": 0.10, "dist": 0.22},
        "exposure_mode": "gate",
        "score_full": 60.0,
        "desc": "Gate mode, threshold lowered 70->60. Tests if threshold was simply too high.",
    },
    "Exp B: Continuous": {
        "no_regime": False,
        "weights": {"hmm": 0.20, "ma": 0.30, "ker": 0.18, "adx": 0.10, "dist": 0.22},
        "exposure_mode": "continuous",
        "score_full": 70.0,
        "desc": "Continuous risk multiplier: <30->0, 30-50->0.2, 50-70->0.4-0.7, 70+->1.0",
    },
    "Equal Weights": {
        "no_regime": False,
        "weights": {"hmm": 0.20, "ma": 0.20, "ker": 0.20, "adx": 0.20, "dist": 0.20},
        "exposure_mode": "gate",
        "score_full": 70.0,
        "desc": "Gate mode, all weights 0.20. Lower MA/dist weight = faster re-engagement.",
    },
}

VARIANT_COLORS = {
    "No Regime": "#d62728",
    "Exp A: Threshold 60": "#9467bd",
    "Exp B: Continuous": "#1f77b4",
    "Equal Weights": "#ff7f0e",
}


# ===========================================================================
# Main experiment runner
# ===========================================================================
def run_v32(start="2018-01-01", end="2025-07-31", top_n=20, verbose=True):
    """Run v3.2 controlled repair experiments."""

    # 1. Load cached buckets (from graduation run — same universe)
    buckets_path = os.path.join(
        _REPO_ROOT, "reports",
        f"dynamic_buckets_{start}_{end}_top{top_n}.json")
    if not os.path.exists(buckets_path):
        raise FileNotFoundError(f"Cached buckets not found: {buckets_path}")
    with open(buckets_path) as f:
        bucket_selections = json.load(f)
    if verbose:
        print(f"Loaded {len(bucket_selections)} cached buckets from graduation run")

    # 2. Load OHLCV data directly from cache (skip get_all_data which scrapes web)
    if verbose:
        print("Loading OHLCV data from cache...")
    # historical_cache.py has _REPO_ROOT one level up from this project,
    # so the cache dir is outside the project folder.
    cache_dir = os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(cache_dir):
        # Fallback: try inside the project
        cache_dir = os.path.join(_REPO_ROOT, "data", "cache", "equities")
    if verbose:
        print(f"  Cache dir: {cache_dir}")

    # Get unique tickers from buckets + SPY
    all_tickers = set()
    for tickers in bucket_selections.values():
        all_tickers.update(tickers)
    all_tickers.add("SPY")

    all_data = {}
    missing = []
    for t in sorted(all_tickers):
        csv_path = os.path.join(cache_dir, f"{t}.csv")
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path, parse_dates=["datetime"])
                if len(df) > 0:
                    # Trim to end date
                    if end:
                        cutoff = pd.Timestamp(end)
                        df = df[df["datetime"] <= cutoff].reset_index(drop=True)
                    all_data[t] = df
            except Exception:
                missing.append(t)
        else:
            missing.append(t)

    if "SPY" not in all_data:
        raise RuntimeError("SPY missing from data cache")
    if verbose:
        print(f"  {len(all_data)}/{len(all_tickers)} tickers loaded"
              f" ({len(missing)} missing)")
        if missing and len(missing) < 20:
            print(f"  Missing: {missing}")

    # 3. Build rebalance calendar
    all_rd = build_rebalance_calendar(all_data, "SPY")
    if verbose:
        print(f"  {len(all_rd)} rebalance dates")

    # 4. SPY buy & hold benchmark
    spy_eq = _spy_buyhold_equity(all_data["SPY"], start, end,
                                 Config().capital_usd)

    # 5. Run each variant
    results = {}
    for name, spec in V32_VARIANTS.items():
        if verbose:
            print(f"\n{'='*60}")
            print(f"  Variant: {name}")
            print(f"  {spec['desc']}")
            print(f"{'='*60}")

        cfg = Config()
        if spec["weights"] is not None:
            cfg.regime_score_weights = spec["weights"]
        cfg.regime_exposure_mode = spec["exposure_mode"]
        cfg.regime_score_full = spec["score_full"]

        eng = DynamicBacktestEngine(
            cfg, all_data, all_rd, bucket_selections, top_n=top_n,
            start=start, end=end, no_regime=spec["no_regime"],
            benchmark="SPY", progress=verbose,
        )
        summary = eng.run()
        metrics = compute_metrics(summary, cfg)
        metrics["exposure_pct"] = _exposure_pct(summary["equity_curve"])
        metrics["n_buckets"] = len(eng.rebalance_log)
        metrics["universe_size"] = len(summary["tickers"])
        results[name] = {
            "summary": summary,
            "metrics": metrics,
            "spec": spec,
            "rebalance_log": eng.rebalance_log,
        }

        m = metrics
        if verbose:
            print(f"\n  >> Return={m['total_return_pct']:+.2f}%  "
                  f"Sharpe={m['sharpe']:.2f}  "
                  f"MaxDD={m['max_drawdown_pct']:.2f}%  "
                  f"PF={m['profit_factor']}  "
                  f"Win={m['win_rate']*100:.1f}%  "
                  f"Trades={m['total_trades']}  "
                  f"Exp={m['exposure_pct']}%")

    results["_spy_buyhold"] = spy_eq
    results["_bucket_selections"] = bucket_selections
    results["_meta"] = {
        "start": start, "end": end, "top_n": top_n,
        "benchmark": "SPY", "n_buckets": len(bucket_selections),
        "variants": list(V32_VARIANTS.keys()),
    }
    return results


# ===========================================================================
# Pass/fail criteria evaluation
# ===========================================================================
def evaluate_pass_criteria(results: dict) -> list:
    """Evaluate each variant against the 5 pass criteria."""
    nr = results["No Regime"]["metrics"]
    nr_dd = nr["max_drawdown_pct"]

    # Get stress period metrics for No Regime
    nr_stress = _phase_metrics(results["No Regime"]["summary"],
                               "2020-02-19", "2020-04-30")
    nr_2022 = _phase_metrics(results["No Regime"]["summary"],
                             "2022-01-01", "2022-10-12")
    nr_stress_dd = min(nr_stress["max_dd_pct"], nr_2022["max_dd_pct"])

    rows = []
    for name in V32_VARIANTS:
        m = results[name]["metrics"]
        summary = results[name]["summary"]

        # Criterion 1: MaxDD < -25%
        dd = m["max_drawdown_pct"]
        c1 = dd > -25.0
        c1_detail = f"MaxDD={dd:.2f}% (threshold: >-25%, No Regime: {nr_dd:.2f}%)"

        # Criterion 2: PF >= 1.15
        pf = 0.0 if m["profit_factor"] == "inf" else float(m["profit_factor"])
        c2 = pf >= 1.15
        c2_detail = f"PF={pf:.2f} (threshold: >=1.15, No Regime: {nr['profit_factor']})"

        # Criterion 3: Sharpe >= 0.50
        sh = m["sharpe"]
        c3 = sh >= 0.50
        c3_detail = f"Sharpe={sh:.2f} (threshold: >=0.50, No Regime: {nr['sharpe']:.2f})"

        # Criterion 4: 2023 re-engaged
        p2023 = _phase_metrics(summary, "2023-01-01", "2023-12-31")
        c4 = p2023["exposure_pct"] > 30.0 and p2023["return_pct"] > 0.0
        c4_detail = (f"2023: exposure={p2023['exposure_pct']}%, "
                     f"return={p2023['return_pct']:+.2f}%, "
                     f"cash_pct={p2023['cash_pct']}%")

        # Criterion 5: 2020/2022 protected
        v_stress = _phase_metrics(summary, "2020-02-19", "2020-04-30")
        v_2022 = _phase_metrics(summary, "2022-01-01", "2022-10-12")
        v_stress_dd = min(v_stress["max_dd_pct"], v_2022["max_dd_pct"])
        c5 = v_stress_dd > nr_stress_dd
        c5_detail = (f"Stress MaxDD: COVID={v_stress['max_dd_pct']:.2f}%, "
                     f"2022Bear={v_2022['max_dd_pct']:.2f}% vs "
                     f"NoRegime={nr_stress_dd:.2f}%")

        passed = sum([c1, c2, c3, c4, c5])
        rows.append({
            "variant": name,
            "c1_maxdd": c1, "c1_detail": c1_detail,
            "c2_pf": c2, "c2_detail": c2_detail,
            "c3_sharpe": c3, "c3_detail": c3_detail,
            "c4_2023": c4, "c4_detail": c4_detail,
            "c5_stress": c5, "c5_detail": c5_detail,
            "passed": passed,
            "total": 5,
            "verdict": "PASS" if passed >= 4 else ("BORDERLINE" if passed >= 3 else "FAIL"),
        })
    return rows


# ===========================================================================
# 2023 Recovery Diagnostic Table
# ===========================================================================
def build_2023_diagnostic(results: dict) -> list:
    """Month-by-month 2023 recovery diagnostic for each variant.

    Output per row: {month, variant, regime_score, size_mult, strategy,
                     vetoes, selected_tickers, next_month_return_pct}
    """
    buckets = results.get("_bucket_selections", {})
    # 2023 month-end dates that exist in buckets
    months_2023 = sorted([d for d in buckets if d.startswith("2023")])

    out = []
    for name in V32_VARIANTS:
        if name not in results:
            continue
        summary = results[name]["summary"]
        regime_log = {r["date"]: r for r in summary.get("regime_log", [])}
        eq_map = {e["date"]: e["equity"] for e in summary["equity_curve"]}

        for i, rd in enumerate(months_2023):
            next_rd = months_2023[i + 1] if i + 1 < len(months_2023) else None
            # Also check for next month in 2024
            if next_rd is None:
                next_rd = sorted([d for d in buckets if d > rd])
                next_rd = next_rd[0] if next_rd else None

            rl = regime_log.get(rd, {})
            # Next month return
            nm_ret = None
            if next_rd and rd in eq_map and next_rd in eq_map:
                nm_ret = round((eq_map[next_rd] / eq_map[rd] - 1) * 100, 2)

            out.append({
                "month": rd,
                "variant": name,
                "regime_score": rl.get("score"),
                "size_mult": rl.get("size_mult"),
                "strategy": rl.get("strategy"),
                "vetoes": rl.get("vetoes", []),
                "selected_tickers": buckets.get(rd, []),
                "n_selected": len(buckets.get(rd, [])),
                "next_month_return_pct": nm_ret,
            })
    return out


# ===========================================================================
# Segmented breakdown (stress vs normal)
# ===========================================================================
def segmented_breakdown(results: dict) -> dict:
    """Per-variant, per-phase metrics."""
    out = {}
    for name in V32_VARIANTS:
        if name not in results:
            continue
        out[name] = [_phase_metrics(results[name]["summary"], s, e)
                     for (_, s, e, _) in MARKET_PHASES]
    return out


# ===========================================================================
# HTML Report Generation
# ===========================================================================
def _equity_chart_png(results: dict) -> str:
    """Render overlaid equity curves -> base64 PNG."""
    fig, ax = plt.subplots(figsize=(12, 5))
    spy = results.get("_spy_buyhold", {}).get("equity_curve", [])
    if spy:
        xs = [pd.Timestamp(e["date"]) for e in spy]
        ys = [e["equity"] for e in spy]
        ax.plot(xs, ys, color="#888888", lw=1.2, ls="--", label="SPY Buy&Hold")

    for name, col in VARIANT_COLORS.items():
        if name not in results:
            continue
        eq = results[name]["summary"]["equity_curve"]
        if not eq:
            continue
        xs = [pd.Timestamp(e["date"]) for e in eq]
        ys = [e["equity"] for e in eq]
        ax.plot(xs, ys, color=col, lw=1.4, label=name)

    ax.axvspan(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31"),
               alpha=0.08, color="green", label="2023 Recovery Zone")
    ax.set_title("v3.2 Experiment — Equity Curves (2018-2025)", fontsize=13)
    ax.set_ylabel("Equity (USD)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _size_mult_chart_png(results: dict) -> str:
    """Render regime size_mult over time for regime variants -> base64 PNG."""
    fig, ax = plt.subplots(figsize=(12, 3.5))
    for name, col in VARIANT_COLORS.items():
        if name not in results or name == "No Regime":
            continue
        rlog = results[name]["summary"].get("regime_log", [])
        if not rlog:
            continue
        xs = [pd.Timestamp(r["date"]) for r in rlog]
        ys = [r["size_mult"] for r in rlog]
        ax.plot(xs, ys, color=col, lw=0.8, alpha=0.8, label=name)

    ax.axvspan(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31"),
               alpha=0.08, color="green")
    ax.axhline(y=0.0, color="red", lw=0.5, ls="--")
    ax.axhline(y=0.2, color="orange", lw=0.5, ls=":", alpha=0.5)
    ax.set_title("Position Size Multiplier Over Time (regime variants only)", fontsize=11)
    ax.set_ylabel("size_mult")
    ax.set_ylim(-0.05, 1.1)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_comparison_html(results: dict, criteria: list, segmented: dict,
                          out_path: str):
    """Generate the main v3.2 comparison report."""
    meta = results["_meta"]
    spy = results["_spy_buyhold"]
    chart_b64 = _equity_chart_png(results)
    size_chart_b64 = _size_mult_chart_png(results)

    # Metrics table
    metrics_html = ""
    for name in V32_VARIANTS:
        if name not in results:
            continue
        m = results[name]["metrics"]
        pf = m["profit_factor"]
        pf_s = "inf" if pf == "inf" else f"{pf:.2f}"
        metrics_html += (
            f"<tr><td><b>{name}</b></td>"
            f"<td>{m['total_return_pct']:+.2f}</td>"
            f"<td>{m['sharpe']:.2f}</td>"
            f"<td>{m['max_drawdown_pct']:.2f}</td>"
            f"<td>{pf_s}</td>"
            f"<td>{m['win_rate']*100:.1f}</td>"
            f"<td>{m['total_trades']}</td>"
            f"<td>{m['exposure_pct']}</td></tr>"
        )
    metrics_html += (
        f"<tr style='background:#f0f0f0'><td>SPY Buy&amp;Hold</td>"
        f"<td>{spy['total_return_pct']:+.2f}</td><td>-</td><td>-</td>"
        f"<td>-</td><td>-</td><td>-</td><td>100</td></tr>"
    )

    # Pass criteria table
    crit_html = ""
    for row in criteria:
        vcolor = {"PASS": "#c8e6c9", "BORDERLINE": "#fff9c4",
                  "FAIL": "#ffcdd2"}.get(row["verdict"], "#eeeeee")
        cells = ""
        for c_key, d_key in [("c1_maxdd", "c1_detail"), ("c2_pf", "c2_detail"),
                              ("c3_sharpe", "c3_detail"), ("c4_2023", "c4_detail"),
                              ("c5_stress", "c5_detail")]:
            val = row[c_key]
            icon = "PASS" if val else "FAIL"
            color = "#c8e6c9" if val else "#ffcdd2"
            cells += f"<td style='background:{color}' title='{row[d_key]}'>{icon}</td>"
        crit_html += (
            f"<tr><td><b>{row['variant']}</b></td>{cells}"
            f"<td style='background:{vcolor};text-align:center'><b>{row['verdict']}</b></td>"
            f"<td>{row['passed']}/{row['total']}</td></tr>"
        )

    # Segmented table
    seg_html = ""
    for name in V32_VARIANTS:
        if name not in segmented:
            continue
        rows = ""
        for (pname, s, e, ptype), pm in zip(MARKET_PHASES, segmented[name]):
            tag = " <span class='stress'>(stress)</span>" if ptype == "stress" else ""
            pf = pm["pf"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            rows += (
                f"<tr><td>{pname}{tag}</td>"
                f"<td>{pm['return_pct']:+.2f}</td>"
                f"<td>{pm['sharpe']:.2f}</td>"
                f"<td>{pm['max_dd_pct']:.2f}</td>"
                f"<td>{pf_s}</td>"
                f"<td>{pm['trades']}</td>"
                f"<td>{pm['exposure_pct']}</td>"
                f"<td>{pm['cash_pct']}</td></tr>"
            )
        seg_html += (
            f"<h3>{name}</h3><table>"
            f"<tr><th>Phase</th><th>Ret%</th><th>Sharpe</th><th>MaxDD%</th>"
            f"<th>PF</th><th>Trades</th><th>Exp%</th><th>Cash%</th></tr>"
            f"{rows}</table>"
        )

    # Criteria details
    detail_html = ""
    for row in criteria:
        detail_html += f"<h4>{row['variant']} ({row['verdict']}: {row['passed']}/{row['total']})</h4><ul>"
        for label, d_key in [("1. MaxDD < -25%", "c1_detail"),
                              ("2. PF >= 1.15", "c2_detail"),
                              ("3. Sharpe >= 0.50", "c3_detail"),
                              ("4. 2023 Re-engaged", "c4_detail"),
                              ("5. 2020/2022 Protected", "c5_detail")]:
            detail_html += f"<li>{label}: {row[d_key]}</li>"
        detail_html += "</ul>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>v3.2 Controlled Repair Experiments</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 32px; color:#222; }}
 h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 30px; }}
 h3 {{ font-size: 14px; margin: 14px 0 4px; }} h4 {{ font-size: 13px; margin: 10px 0 2px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; font-size: 12.5px; }}
 th, td {{ border: 1px solid #ccc; padding: 5px 8px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 th {{ background: #fafafa; }}
 .stress {{ color:#c00; font-size: 11px; font-weight: bold; }}
 .note {{ background:#fff8e1; border-left:4px solid #ffc107; padding:10px 14px; margin:14px 0; font-size:13px; }}
 .chart {{ margin: 18px 0; }}
 ul {{ margin: 4px 0 8px; font-size: 12.5px; }}
</style></head>
<body>
<h1>v3.2 Controlled Repair Experiments — Regime as Throttle, Not Door</h1>
<p>Period <b>{meta['start']} ~ {meta['end']}</b> - Monthly rebalance - Top {meta['top_n']} -
Same dynamic universe as graduation exam (frozen parameters, no mid-run tuning).</p>

<div class="note">
<b>Core hypothesis (reviewer):</b> "Regime engine defends but doesn't attack."
The problem is NOT that regime_score is wrong - it's that the exposure response
is too extreme. When score < 50, the system goes completely to cash and stays
there too long, missing recoveries. v3.2 tests whether making the exposure
response continuous (throttle) instead of binary (door) fixes this.
</div>

<div class="chart"><img src="data:image/png;base64,{chart_b64}" style="width:100%"></div>
<div class="chart"><img src="data:image/png;base64,{size_chart_b64}" style="width:100%"></div>

<h2>1. Full-Period Performance Comparison</h2>
<table><tr><th>Variant</th><th>Ret%</th><th>Sharpe</th><th>MaxDD%</th><th>PF</th>
<th>Win%</th><th>Trades</th><th>Exp%</th></tr>
{metrics_html}</table>

<h2>2. Pass/Fail Criteria (5 criteria)</h2>
<table>
<tr><th style='text-align:left'>Variant</th>
<th title="MaxDD > -25%">C1: MaxDD</th>
<th title="PF >= 1.15">C2: PF</th>
<th title="Sharpe >= 0.50">C3: Sharpe</th>
<th title="2023 exposure > 30% & return > 0%">C4: 2023</th>
<th title="Stress MaxDD better than No Regime">C5: Stress</th>
<th>Verdict</th><th>Score</th></tr>
{crit_html}</table>

<h2>3. Criteria Details</h2>
{detail_html}

<h2>4. Regime-Segmented Breakdown</h2>
<p>Stress phases (2018 Q4, COVID, 2022 Bear) are where the regime engine must
still earn its keep. 2023 Recovery is the key fix target.</p>
{seg_html}

<div class="note">
<b>How to read this:</b>
<ul>
<li><b>Exp B (Continuous)</b> is the priority test - does partial exposure during
uncertain markets fix the re-engagement problem?</li>
<li><b>Exp A (Threshold 60)</b> is the benchmark - is the fix as simple as lowering
the bar? (Risk: optimization bias toward 2023)</li>
<li><b>Equal Weights</b> is the challenger - did lower MA/dist weights already
solve this in the graduation exam?</li>
<li>Check the size_mult chart: does Continuous maintain partial exposure (0.2-0.7)
during 2023 while still dropping to 0 during COVID/2022?</li>
</ul>
</div>
</body></html>"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def build_2023_diagnostic_html(diag: list, out_path: str):
    """Generate 2023 recovery diagnostic table (HTML)."""
    # Group by variant
    by_variant = {}
    for row in diag:
        v = row["variant"]
        if v not in by_variant:
            by_variant[v] = []
        by_variant[v].append(row)

    html_blocks = ""
    for name in V32_VARIANTS:
        if name not in by_variant:
            continue
        rows = by_variant[name]
        rows_html = ""
        for r in rows:
            vetoes_str = "; ".join(r["vetoes"]) if r["vetoes"] else "-"
            tickers_str = ", ".join(r["selected_tickers"][:10])
            if len(r["selected_tickers"]) > 10:
                tickers_str += f" ... ({r['n_selected']})"
            score = r["regime_score"]
            score_str = f"{score:.0f}" if score is not None else "-"
            sm = r["size_mult"]
            sm_str = f"{sm:.2f}" if sm is not None else "-"
            nm = r["next_month_return_pct"]
            nm_str = f"{nm:+.2f}%" if nm is not None else "-"

            # Color coding
            sm_color = "#c8e6c9" if sm and sm > 0 else "#ffcdd2"
            if sm and 0 < sm < 0.5:
                sm_color = "#fff9c4"

            rows_html += (
                f"<tr><td>{r['month']}</td>"
                f"<td>{score_str}</td>"
                f"<td style='background:{sm_color}'>{sm_str}</td>"
                f"<td>{r['strategy'] or '-'}</td>"
                f"<td style='font-size:11px'>{vetoes_str}</td>"
                f"<td style='font-size:11px'>{tickers_str}</td>"
                f"<td>{nm_str}</td></tr>"
            )
        html_blocks += (
            f"<h3>{name}</h3><table>"
            f"<tr><th>Month</th><th>Score</th><th>Size Mult</th><th>Strategy</th>"
            f"<th>Vetoes</th><th>Selected Tickers</th><th>Next Mo Return</th></tr>"
            f"{rows_html}</table>"
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>v3.2 — 2023 Recovery Diagnostic</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 32px; color:#222; }}
 h1 {{ font-size: 20px; }} h2 {{ font-size: 16px; margin-top: 24px; }}
 h3 {{ font-size: 14px; margin: 14px 0 4px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; font-size: 12px; }}
 th, td {{ border: 1px solid #ccc; padding: 4px 6px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 th {{ background: #fafafa; }}
 .note {{ background:#fff8e1; border-left:4px solid #ffc107; padding:10px 14px; margin:14px 0; font-size:13px; }}
</style></head>
<body>
<h1>v3.2 — 2023 Recovery Diagnostic Table</h1>
<p>Month-by-month regime score, position size, and next-month return during
the 2023 recovery period. This is the key diagnostic for whether v3.2 fixes
the "re-engagement too slow" problem identified in the graduation exam.</p>

<div class="note">
<b>What to look for:</b>
<ul>
<li><b>No Regime:</b> Should have full exposure (1.0) throughout - this is the baseline.</li>
<li><b>Exp A (Threshold 60):</b> Does lowering the bar from 70 to 60 allow re-entry?</li>
<li><b>Exp B (Continuous):</b> Does partial exposure (0.2-0.7) appear in 2023?
This is the key test - if size_mult > 0 throughout 2023, the throttle is working.</li>
<li><b>Equal Weights:</b> Already showed +33.83% in graduation - how does its
exposure profile differ from Current weights?</li>
</ul>
</div>

{html_blocks}
</body></html>"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


# ===========================================================================
# Main
# ===========================================================================
def main():
    import argparse
    ap = argparse.ArgumentParser(description="v3.2 Controlled Repair Experiments")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2025-07-31")
    ap.add_argument("--top-n", type=int, default=20)
    args = ap.parse_args()

    print("=" * 70)
    print("  v3.2 CONTROLLED REPAIR EXPERIMENTS")
    print("  Regime as Throttle, Not Door")
    print("=" * 70)

    t0 = time.time()
    results = run_v32(start=args.start, end=args.end, top_n=args.top_n,
                      verbose=True)
    elapsed = time.time() - t0

    # Evaluate pass criteria
    print("\n" + "=" * 70)
    print("  PASS/FAIL CRITERIA EVALUATION")
    print("=" * 70)
    criteria = evaluate_pass_criteria(results)
    for row in criteria:
        print(f"\n  {row['variant']} -> {row['verdict']} ({row['passed']}/{row['total']})")
        print(f"    C1 MaxDD:    {row['c1_detail']}")
        print(f"    C2 PF:       {row['c2_detail']}")
        print(f"    C3 Sharpe:   {row['c3_detail']}")
        print(f"    C4 2023:     {row['c4_detail']}")
        print(f"    C5 Stress:   {row['c5_detail']}")

    # Build 2023 diagnostic
    diag = build_2023_diagnostic(results)

    # Segmented breakdown
    segmented = segmented_breakdown(results)

    # Generate reports
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)

    r1 = os.path.join(out_dir, "v32_comparison.html")
    build_comparison_html(results, criteria, segmented, r1)

    r2 = os.path.join(out_dir, "v32_2023_diagnostic.html")
    build_2023_diagnostic_html(diag, r2)

    r3 = os.path.join(out_dir, "v32_results.json")
    dump = {
        "meta": results["_meta"],
        "spy_buyhold_return_pct": results["_spy_buyhold"]["total_return_pct"],
        "variants": {
            v: {"metrics": results[v]["metrics"],
                "spec": results[v]["spec"]}
            for v in V32_VARIANTS if v in results
        },
        "pass_criteria": criteria,
        "elapsed_seconds": round(elapsed, 1),
    }
    with open(r3, "w") as f:
        json.dump(dump, f, indent=2, default=str)

    print(f"\n{'='*70}")
    print(f"  DONE in {elapsed:.0f}s")
    print(f"{'='*70}")
    print(f"  Report 1 (comparison):  {r1}")
    print(f"  Report 2 (2023 diag):   {r2}")
    print(f"  Report 3 (JSON):        {r3}")


if __name__ == "__main__":
    main()
