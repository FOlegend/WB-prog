"""
breakout_ablation.py — Breakout Component Ablation + Attribution

Controlled ablation of breakout scoring components (strict-only entry,
gap-aware exit, next-open entry, deterministic, score_full=70, Current weights).

Variants:
  A. base (MA + strict breakout + rs_rank)
  B. base + volume expansion
  C. base + VCP
  D. base + volume + VCP  (= full current scoring)
  E. full current scoring  (= D, kept for completeness)

Plus component attribution on the full variant (E):
  PF / Avg R / Win% grouped by volume_expansion / vcp_contraction / rs_rank_pass
  / gap bucket / extension bucket.

Run:
  python breakout_ablation.py
"""
from __future__ import annotations

import os
import sys
import json
import warnings
from collections import Counter

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from dynamic_universe_backtest import DynamicBacktestEngine, build_rebalance_calendar
from src.backtest.engine import compute_metrics

warnings.filterwarnings("ignore")


ABLATION_VARIANTS = {
    "A. base":        ["ma", "rs_rank"],
    "B. base+volume": ["ma", "rs_rank", "volume"],
    "C. base+vcp":    ["ma", "rs_rank", "vcp"],
    "D. base+vol+vcp": ["ma", "rs_rank", "volume", "vcp"],
    "E. full(current)": ["ma", "rs_rank", "volume", "vcp"],
}


def load_data_from_cache(end: str = "2025-07-31"):
    cache_dir = os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(cache_dir):
        cache_dir = os.path.join(_REPO_ROOT, "data", "cache", "equities")
    all_tickers = [f.replace(".csv", "") for f in os.listdir(cache_dir)
                   if f.endswith(".csv")]
    cutoff = pd.Timestamp(end)
    all_data = {}
    for t in sorted(all_tickers):
        p = os.path.join(cache_dir, f"{t}.csv")
        try:
            df = pd.read_csv(p, parse_dates=["datetime"])
            if len(df) > 0:
                df = df[df["datetime"] <= cutoff].reset_index(drop=True)
                all_data[t] = df
        except Exception:
            pass
    return all_data


def _phase_maxdd(equity_curve, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    eq = [x for x in equity_curve if s <= pd.Timestamp(x["date"]) <= e]
    if len(eq) < 2:
        return 0.0
    vals = pd.Series([x["equity"] for x in eq])
    return round(float((vals / vals.cummax() - 1).min() * 100), 2)


def _phase_ret_exp(summary, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    eq = [x for x in summary["equity_curve"] if s <= pd.Timestamp(x["date"]) <= e]
    if len(eq) < 2:
        return 0.0, 0.0
    ret = (eq[-1]["equity"] / eq[0]["equity"] - 1) * 100
    exp = sum(1 for x in eq if x["n_positions"] > 0) / len(eq) * 100
    return round(ret, 2), round(exp, 1)


def _dd_duration(equity_curve):
    vals = [x["equity"] for x in equity_curve]
    peak, peak_idx, longest = vals[0], 0, 0
    for i, v in enumerate(vals):
        if v >= peak:
            peak, peak_idx = v, i
        else:
            longest = max(longest, i - peak_idx)
    return longest


def _cagr(summary, start, end):
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    ratio = summary["final_equity"] / summary["starting_equity"]
    return round((ratio ** (1 / years) - 1) * 100, 2) if years > 0 and ratio > 0 else 0.0


def detailed_metrics(summary, cfg, start, end):
    m = compute_metrics(summary, cfg)
    trades = summary["trade_log"]
    pnls = [t["net_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    skipped = summary.get("skipped_signals", [])
    skip_dist = Counter(s.get("skip_reason") for s in skipped)
    ret_2023, exp_2023 = _phase_ret_exp(summary, "2023-01-01", "2023-12-31")
    return {
        "return_pct": m["total_return_pct"],
        "cagr_pct": _cagr(summary, start, end),
        "sharpe": m["sharpe"],
        "max_dd_pct": m["max_drawdown_pct"],
        "max_dd_duration_days": _dd_duration(summary["equity_curve"]),
        "pf": m["profit_factor"],
        "win_rate": m["win_rate"],
        "trades": m["total_trades"],
        "avg_win": round(float(np.mean(wins)), 2) if wins else 0.0,
        "avg_loss": round(float(np.mean(losses)), 2) if losses else 0.0,
        "avg_r": round(float(np.mean(rs)), 3) if rs else None,
        "worst_r": round(float(min(rs)), 3) if rs else None,
        "gap_fill_count": sum(1 for t in trades if t.get("exit_fill_model") == "GAP"),
        "skipped_gap_too_high": skip_dist.get("GAP_TOO_HIGH", 0),
        "skipped_extended": skip_dist.get("EXTENDED_FROM_PIVOT", 0),
        "skipped_total": len(skipped),
        "dd_2020": _phase_maxdd(summary["equity_curve"], "2020-02-19", "2020-04-30"),
        "dd_2022": _phase_maxdd(summary["equity_curve"], "2022-01-01", "2022-10-12"),
        "ret_2023_pct": ret_2023,
        "exposure_2023_pct": exp_2023,
    }


def run_variant(components, all_data, all_rd, buckets, start, end):
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = ["breakout"]
    cfg.setup_breakout_components = components
    cfg.regime_score_weights = {"hmm": 0.20, "ma": 0.30, "ker": 0.18,
                                 "adx": 0.10, "dist": 0.22}
    cfg.regime_exposure_mode = "gate"
    cfg.regime_score_full = 70.0
    eng = DynamicBacktestEngine(cfg, all_data, all_rd, buckets, top_n=20,
                                start=start, end=end, no_regime=False,
                                benchmark="SPY", progress=False)
    summary = eng.run()
    return summary, cfg


def _group_stats(trades, key_fn):
    """Return {label: (n, pf, win_rate, avg_r)} grouped by key_fn."""
    groups = {}
    for t in trades:
        k = key_fn(t)
        groups.setdefault(k, []).append(t)
    out = {}
    for k, sel in groups.items():
        pnls = [t["net_pnl"] for t in sel]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gw, gl = sum(wins), abs(sum(losses))
        pf = round(gw / gl, 2) if gl > 0 else float("inf")
        rs = [t.get("r_multiple") for t in sel if t.get("r_multiple") is not None]
        out[k] = {
            "n": len(sel),
            "pf": pf,
            "win_rate": round(len(wins) / len(sel), 3) if sel else 0.0,
            "avg_r": round(float(np.mean(rs)), 3) if rs else None,
        }
    return out


def component_attribution(trades):
    """PF/AvgR/Win% grouped by each component + gap/extension buckets."""
    def comp(key):
        return _group_stats(trades, lambda t: t.get("components", {}).get(key))

    def gap_bucket(t):
        g = t.get("next_open_gap_pct")
        if g is None:
            return "N/A"
        if g < 0:
            return "<0% (gap down)"
        if g <= 0.01:
            return "0-1%"
        if g <= 0.02:
            return "1-2%"
        return ">2%"

    def ext_bucket(t):
        e = t.get("extension_from_pivot_pct")
        if e is None:
            return "N/A"
        if e < 0:
            return "<0% (below pivot)"
        if e <= 0.015:
            return "0-1.5%"
        if e <= 0.03:
            return "1.5-3%"
        return ">3%"

    return {
        "volume_expansion": comp("volume_expansion"),
        "vcp_contraction": comp("vcp_contraction"),
        "rs_rank_pass": comp("rs_rank_pass"),
        "above_50": comp("above_50"),
        "above_150": comp("above_150"),
        "above_200": comp("above_200"),
        "ma_aligned": comp("ma_aligned"),
        "strict_breakout": comp("strict_breakout"),
        "gap_bucket": _group_stats(trades, gap_bucket),
        "extension_bucket": _group_stats(trades, ext_bucket),
    }


def main():
    start, end, top_n = "2018-01-01", "2025-07-31", 20
    print("=" * 70)
    print("  BREAKOUT COMPONENT ABLATION + ATTRIBUTION")
    print("  strict-only | gap-aware | next-open | score_full=70 | Current wts")
    print("=" * 70)

    all_data = load_data_from_cache(end=end)
    with open(os.path.join(_REPO_ROOT, "reports",
                           f"dynamic_buckets_{start}_{end}_top{top_n}.json")) as f:
        buckets = json.load(f)
    all_rd = build_rebalance_calendar(all_data, "SPY")
    print(f"\n  {len(all_data)} tickers, {len(buckets)} buckets")

    results = {}
    full_summary = None
    for name, comps in ABLATION_VARIANTS.items():
        print(f"\n--- {name}: {comps} ---")
        summary, cfg = run_variant(comps, all_data, all_rd, buckets, start, end)
        m = detailed_metrics(summary, cfg, start, end)
        results[name] = m
        if name == "E. full(current)":
            full_summary = summary
        print(f"    Return={m['return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
              f"MaxDD={m['max_dd_pct']:.2f}%  PF={m['pf']}  Trades={m['trades']}  "
              f"Skipped={m['skipped_total']}")

    # Component attribution on full variant
    print("\n" + "=" * 70)
    print("  COMPONENT ATTRIBUTION (variant E: full current)")
    print("=" * 70)
    attribution = component_attribution(full_summary["trade_log"])
    for label, groups in attribution.items():
        print(f"\n  {label}:")
        for k in sorted(groups.keys(), key=str):
            g = groups[k]
            pf_s = "inf" if g["pf"] == float("inf") else f"{g['pf']:.2f}"
            print(f"    {str(k):<20} n={g['n']:>4}  PF={pf_s:>6}  "
                  f"win={g['win_rate']*100:.0f}%  avgR={g['avg_r']}")

    # Save
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out = {
        "meta": {"start": start, "end": end, "top_n": top_n,
                 "setup": "Breakout strict-only", "entry": "next-open",
                 "exit": "gap-aware OHLC", "weights": "Current", "score_full": 70},
        "ablation": results,
        "attribution": attribution,
        "skip_summary": Counter(
            s.get("skip_reason") for s in full_summary.get("skipped_signals", [])),
    }
    with open(os.path.join(out_dir, "breakout_ablation.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)

    # Ablation table
    print("\n" + "=" * 70)
    print("  ABLATION TABLE")
    print("=" * 70)
    cols = [("Return%", "return_pct", "+.2f"), ("CAGR%", "cagr_pct", "+.2f"),
            ("Sharpe", "sharpe", ".2f"), ("MaxDD%", "max_dd_pct", ".2f"),
            ("PF", "pf", ""), ("Win%", "win_rate", ""), ("Trades", "trades", "d"),
            ("AvgR", "avg_r", ""), ("WorstR", "worst_r", ""), ("Gap", "gap_fill_count", "d"),
            ("Skip", "skipped_total", "d"), ("2020DD", "dd_2020", ".2f"),
            ("2022DD", "dd_2022", ".2f"), ("2023Ret", "ret_2023_pct", "+.2f")]
    hdr = "".join(f"{n:>9}" for n, _, _ in cols)
    print(f"{'Variant':<18}{hdr}")
    print("-" * (18 + 9 * len(cols)))
    for name in ABLATION_VARIANTS:
        m = results[name]
        row = ""
        for _, k, fmt in cols:
            v = m[k]
            if k == "win_rate":
                row += f"{v*100:>9.1f}"
            elif fmt == "+.2f":
                row += f"{v:>+9.2f}"
            elif fmt == ".2f":
                row += f"{v:>9.2f}"
            elif fmt == "d":
                row += f"{v:>9d}"
            else:
                row += f"{str(v):>9}"
        print(f"{name:<18}{row}")

    print(f"\n  Saved to: {out_dir}/breakout_ablation.json")


if __name__ == "__main__":
    main()
