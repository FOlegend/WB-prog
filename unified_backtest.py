"""
unified_backtest.py — 三合一統一回測（Screener + Regime + Setup）

一條命令跑晒三個 bot，出一份 consolidated 報告：
  Layer 1: historical_cache.get_all_data     (價格歷史)
  Layer 2: build_rebalance_calendar          (月尾 rebalance)
  Layer 3: screen_as_of.screen_all_buckets   (point-in-time screener = screener bot)
  Layer 4: DynamicBacktestEngine             (regime HMM + setup_signal 入場)
  Layer 5: 單一整合報告 (metrics + 三 bot 貢獻拆解 + equity curve)

用法:
  python unified_backtest.py
  python unified_backtest.py --start 2018-01-01 --end 2025-07-31 --top-n 20
  python unified_backtest.py --regime-weights reviewer   # current|reviewer|equal|none
  python unified_backtest.py --entry-mode setup          # setup|weighted
  python unified_backtest.py --use-buckets-cache         # reuse precomputed buckets
  python unified_backtest.py --refresh                   # force re-download prices
"""
from __future__ import annotations
import os
import sys
import json
import base64
import io
import warnings
import argparse
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
from historical_cache import get_all_data
from screen_as_of import screen_all_buckets
from dynamic_universe_backtest import (
    build_rebalance_calendar, DynamicBacktestEngine,
    _spy_buyhold_equity, _exposure_pct, REGIME_VARIANTS,
)
from src.backtest.engine import compute_metrics

warnings.filterwarnings("ignore")

CURRENT_WEIGHTS = {"hmm": 0.20, "ma": 0.30, "ker": 0.18, "adx": 0.10, "dist": 0.22}
REVIEWER_WEIGHTS = {"hmm": 0.25, "ma": 0.30, "ker": 0.20, "adx": 0.10, "dist": 0.15}
EQUAL_WEIGHTS = {"hmm": 0.20, "ma": 0.20, "ker": 0.20, "adx": 0.20, "dist": 0.20}


# ---------------------------------------------------------------------------
# Pipeline contribution breakdown (the "三 bot 各自貢獻" 拆解)
# ---------------------------------------------------------------------------
def _screener_breakdown(bucket_selections: dict) -> dict:
    """How the screener (Layer 3) narrowed the universe."""
    sizes = [len(v) for v in bucket_selections.values()]
    uniq = set()
    for v in bucket_selections.values():
        uniq.update(v)
    return {
        "n_buckets": len(bucket_selections),
        "avg_candidates_per_bucket": round(float(np.mean(sizes)), 1) if sizes else 0.0,
        "min_candidates": min(sizes) if sizes else 0,
        "max_candidates": max(sizes) if sizes else 0,
        "unique_tickers_passed": len(uniq),
        "note": "Screener = screen_as_of (point-in-time 6-filter). Universe fed to regime+setup each month.",
    }


def _regime_breakdown(regime_log: list) -> dict:
    """How the regime engine (Layer 4a) gated exposure."""
    if not regime_log:
        return {"n_days": 0, "cash_days_pct": 0.0, "regimes": {}, "avg_size_mult": 0.0,
                "avg_size_mult_invested": 0.0}
    n = len(regime_log)
    cash = sum(1 for r in regime_log if r.get("strategy") == "cash" or r.get("size_mult", 1) <= 0)
    regimes = {}
    sm_all, sm_inv = [], []
    for r in regime_log:
        regimes[r.get("regime", "UNKNOWN")] = regimes.get(r.get("regime", "UNKNOWN"), 0) + 1
        sm = r.get("size_mult", 0.0)
        sm_all.append(sm)
        if sm > 0:
            sm_inv.append(sm)
    return {
        "n_days": n,
        "cash_days_pct": round(cash / n * 100, 1),
        "invested_days_pct": round((n - cash) / n * 100, 1),
        "regimes": regimes,
        "avg_size_mult": round(float(np.mean(sm_all)), 3) if sm_all else 0.0,
        "avg_size_mult_invested": round(float(np.mean(sm_inv)), 3) if sm_inv else 0.0,
    }


def _setup_breakdown(trade_log: list, skipped_signals: list) -> dict:
    """How the setup bot (Layer 4b) converted candidates into trades."""
    if not trade_log:
        return {"total_trades": 0, "breakout": 0, "pullback": 0, "none": 0,
                "avg_setup_score": 0.0, "avg_r_multiple": 0.0,
                "win_rate": 0.0, "win_rate_breakout": 0.0, "win_rate_pullback": 0.0,
                "exit_reasons": {}, "skipped": {}}
    bo = [t for t in trade_log if t.get("setup_type") == "breakout"]
    pb = [t for t in trade_log if t.get("setup_type") == "pullback"]
    none = [t for t in trade_log if not t.get("setup_type")]
    wins = [t for t in trade_log if t.get("net_pnl", 0) > 0]
    bo_w = [t for t in bo if t.get("net_pnl", 0) > 0]
    pb_w = [t for t in pb if t.get("net_pnl", 0) > 0]
    scores = [t["setup_score"] for t in trade_log if isinstance(t.get("setup_score"), (int, float))]
    rms = [t["r_multiple"] for t in trade_log if isinstance(t.get("r_multiple"), (int, float))]
    exits = {}
    for t in trade_log:
        exits[t.get("exit_reason", "UNKNOWN")] = exits.get(t.get("exit_reason", "UNKNOWN"), 0) + 1
    skip_counts = {}
    for s in (skipped_signals or []):
        skip_counts[s.get("skip_reason", "UNKNOWN")] = skip_counts.get(s.get("skip_reason", "UNKNOWN"), 0) + 1
    return {
        "total_trades": len(trade_log),
        "breakout": len(bo),
        "pullback": len(pb),
        "none": len(none),
        "avg_setup_score": round(float(np.mean(scores)), 3) if scores else 0.0,
        "avg_r_multiple": round(float(np.mean(rms)), 3) if rms else 0.0,
        "win_rate": round(len(wins) / len(trade_log), 3),
        "win_rate_breakout": round(len(bo_w) / len(bo), 3) if bo else 0.0,
        "win_rate_pullback": round(len(pb_w) / len(pb), 3) if pb else 0.0,
        "exit_reasons": exits,
        "skipped": skip_counts,
        "skipped_total": len(skipped_signals or []),
    }


def _safe_metrics(summary: dict, cfg: Config) -> dict:
    """compute_metrics 在 0 trades 時唔出 total_return_pct，手動補回。"""
    m = compute_metrics(summary, cfg)
    if "total_return_pct" not in m and summary.get("equity_curve"):
        eq0 = summary["equity_curve"][0]["equity"]
        eq1 = summary["equity_curve"][-1]["equity"]
        m["total_return_pct"] = round((eq1 / eq0 - 1) * 100, 2)
    m["exposure_pct"] = _exposure_pct(summary["equity_curve"])
    return m


# ---------------------------------------------------------------------------
# Equity curve chart -> base64 PNG
# ---------------------------------------------------------------------------
def _equity_chart_png(equity_curve: list, spy_curve: list) -> str:
    if not equity_curve:
        return ""
    eq = pd.DataFrame(equity_curve)
    eq["date"] = pd.to_datetime(eq["date"])
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=110)
    if spy_curve:
        s = pd.DataFrame(spy_curve)
        s["date"] = pd.to_datetime(s["date"])
        ax.plot(s["date"], s["equity"], color="#888", lw=1.4, label="SPY buy&hold (Core)")
    ax.plot(eq["date"], eq["equity"], color="#1f77b4", lw=1.8, label="Strategy (Screener+Regime+Setup)")
    ax.set_title("Equity Curve — Unified Backtest", fontsize=12)
    ax.set_ylabel("Equity (USD)")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def _html_report(meta: dict, metrics: dict, spy: dict, breakdown: dict) -> str:
    sc, rg, su = breakdown["screener"], breakdown["regime"], breakdown["setup"]
    chart = _equity_chart_png(meta["equity_curve"], spy.get("equity_curve", []))

    def pct(x):
        return f"{x:+.2f}%" if isinstance(x, (int, float)) else str(x)

    overall_rows = [
        ("Total Return", pct(metrics.get("total_return_pct", 0)), pct(spy.get("total_return_pct", 0))),
        ("Sharpe", f"{metrics.get('sharpe',0):.2f}", "—"),
        ("Max Drawdown", pct(metrics.get("max_drawdown_pct", 0)), "—"),
        ("Profit Factor", str(metrics.get("profit_factor", 0)), "—"),
        ("Win Rate", f"{metrics.get('win_rate',0)*100:.1f}%", "—"),
        ("Total Trades", str(metrics.get("total_trades", 0)), "—"),
        ("Expectancy", f"${metrics.get('expectancy',0):.2f}", "—"),
        ("Exposure", f"{metrics.get('exposure_pct',0):.1f}%", "—"),
    ]
    overall_html = "".join(
        f"<tr><td>{k}</td><td>{v}</td><td>{s}</td></tr>" for k, v, s in overall_rows)

    scr_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in [
            ("Buckets screened (months)", sc["n_buckets"]),
            ("Avg candidates / bucket", sc["avg_candidates_per_bucket"]),
            ("Min / Max candidates", f"{sc['min_candidates']} / {sc['max_candidates']}"),
            ("Unique tickers passed", sc["unique_tickers_passed"]),
        ])

    reg_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in [
            ("Regime days logged", rg["n_days"]),
            ("Cash days (size_mult=0)", f"{rg['cash_days_pct']}%"),
            ("Invested days", f"{rg['invested_days_pct']}%"),
            ("Regime label distribution", ", ".join(f"{k}={v}" for k, v in rg["regimes"].items())),
            ("Avg size_mult (all)", rg["avg_size_mult"]),
            ("Avg size_mult (invested)", rg["avg_size_mult_invested"]),
        ])

    su_rows = [
        ("Total trades", su["total_trades"]),
        ("Breakout entries", su["breakout"]),
        ("Pullback entries", su["pullback"]),
        ("Avg setup_score", su["avg_setup_score"]),
        ("Avg R-multiple", su["avg_r_multiple"]),
        ("Win rate (overall)", f"{su['win_rate']*100:.1f}%"),
        ("Win rate (breakout)", f"{su['win_rate_breakout']*100:.1f}%"),
        ("Win rate (pullback)", f"{su['win_rate_pullback']*100:.1f}%"),
        ("Signals skipped (gap/ext)", su.get("skipped_total", 0)),
    ]
    su_html = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in su_rows)
    if su.get("exit_reasons"):
        su_html += "<tr><td>Exit reasons</td><td>" + ", ".join(
            f"{k}={v}" for k, v in su["exit_reasons"].items()) + "</td></tr>"
    if su.get("skipped"):
        su_html += "<tr><td>Skip reasons</td><td>" + ", ".join(
            f"{k}={v}" for k, v in su["skipped"].items()) + "</td></tr>"

    chart_html = f'<img src="data:image/png;base64,{chart}" style="max-width:100%"/>' if chart else ""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Unified Backtest — {meta['label']}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#222;}}
 h1{{font-size:20px;}} h2{{font-size:15px;margin-top:24px;border-left:4px solid #1f77b4;padding-left:8px;}}
 table{{border-collapse:collapse;width:auto;margin:6px 0 4px;font-size:13px;}}
 td,th{{border:1px solid #ddd;padding:5px 10px;text-align:left;}}
 th{{background:#f5f5f5;}}
 .box{{background:#fafafa;border:1px solid #eee;border-radius:8px;padding:12px 16px;margin:8px 0;}}
 .muted{{color:#777;font-size:12px;}}
</style></head><body>
<h1>Unified Backtest — {meta['label']}</h1>
<p class="muted">Window {meta['start']} → {meta['end']} · top_n={meta['top_n']} · benchmark={meta['benchmark']} · generated {meta['generated']}</p>

<h2>① Overall Performance (vs SPY buy&amp;hold)</h2>
<div class="box"><table>
<tr><th>Metric</th><th>Strategy</th><th>SPY</th></tr>
{overall_html}
</table></div>

<h2>② Equity Curve</h2>
{chart_html}

<h2>③ Pipeline Contribution Breakdown</h2>
<div class="box"><b>Screener bot</b> (Layer 3, point-in-time 6-filter)
<table>{scr_rows}</table></div>
<div class="box"><b>Regime bot</b> (Layer 4a, HMM on SPY → size_mult)
<table>{reg_rows}</table></div>
<div class="box"><b>Setup bot</b> (Layer 4b, breakout/pullback → entry signal)
<table>{su_html}</table></div>

<p class="muted">Config: regime_weights={meta['regime_weights']} · entry_mode={meta['entry_mode']} · no_regime={meta['no_regime']}</p>
</body></html>"""


def run_unified(start="2018-01-01", end="2025-07-31", cache_start="2016-01-01",
                top_n=20, benchmark="SPY", regime_weights="current",
                entry_mode="setup", use_buckets_cache=False, refresh=False,
                verbose=True) -> dict:
    cfg = Config()
    cfg.entry_mode = entry_mode

    if regime_weights == "none":
        no_regime = True
        cfg.regime_score_weights = None
        label = f"NoRegime+{entry_mode}"
    else:
        no_regime = False
        preset = {"current": CURRENT_WEIGHTS, "reviewer": REVIEWER_WEIGHTS,
                  "equal": EQUAL_WEIGHTS}[regime_weights]
        cfg.regime_score_weights = preset
        label = f"{regime_weights.capitalize()}Weights+{entry_mode}"

    print("=" * 70)
    print(f"  UNIFIED BACKTEST — {label}")
    print(f"  window {start}→{end}  cache_start={cache_start}  top_n={top_n}")
    print("=" * 70)

    # Layer 1
    print("[L1] historical cache...")
    all_data = get_all_data(start=cache_start, end=end, force_refresh=refresh,
                            verbose=verbose)
    if benchmark not in all_data:
        raise RuntimeError(f"Benchmark {benchmark} missing from cache")

    # Layer 2
    print("[L2] rebalance calendar...")
    all_rd = build_rebalance_calendar(all_data, benchmark)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    screen_from = start_ts - pd.Timedelta(days=40)
    rd_to_screen = [r for r in all_rd if r <= end_ts and r >= screen_from]
    print(f"     {len(all_rd)} month-ends; screening {len(rd_to_screen)} relevant buckets")

    # Layer 3 — screener (point-in-time)
    buckets_cache = os.path.join(_REPO_ROOT, "reports",
                                 f"dynamic_buckets_{start}_{end}_top{top_n}.json")
    if use_buckets_cache and os.path.exists(buckets_cache):
        print(f"[L3] loading cached buckets ({buckets_cache})")
        with open(buckets_cache) as f:
            bucket_selections = json.load(f)
    else:
        print("[L3] point-in-time screening (screener bot)...")
        bucket_selections = screen_all_buckets(all_data, rd_to_screen, cfg=cfg,
                                                top_n=top_n, benchmark=benchmark,
                                                verbose=verbose)
        os.makedirs(os.path.dirname(buckets_cache), exist_ok=True)
        with open(buckets_cache, "w") as f:
            json.dump(bucket_selections, f, indent=1)
        print(f"     saved buckets -> {buckets_cache}")

    # Layer 4 — dynamic backtest (regime + setup)
    print(f"[L4] backtest engine (regime={ 'OFF' if no_regime else 'ON' }, entry={entry_mode})...")
    eng = DynamicBacktestEngine(
        cfg, all_data, all_rd, bucket_selections, top_n=top_n,
        start=start, end=end, no_regime=no_regime,
        benchmark=benchmark, progress=verbose,
    )
    summary = eng.run()
    metrics = _safe_metrics(summary, cfg)

    # Layer 5 — consolidated report
    spy = _spy_buyhold_equity(all_data[benchmark], start, end, cfg.capital_usd)
    breakdown = {
        "screener": _screener_breakdown(bucket_selections),
        "regime": _regime_breakdown(summary.get("regime_log", [])),
        "setup": _setup_breakdown(summary.get("trade_log", []), summary.get("skipped_signals", [])),
    }
    meta = {
        "label": label, "start": start, "end": end, "top_n": top_n,
        "benchmark": benchmark, "generated": datetime.now().isoformat(timespec="seconds"),
        "regime_weights": "none" if no_regime else regime_weights,
        "entry_mode": entry_mode, "no_regime": no_regime,
        "equity_curve": summary["equity_curve"],
    }
    html = _html_report(meta, metrics, spy, breakdown)

    out = {
        "meta": meta, "metrics": metrics,
        "spy_buyhold": {"total_return_pct": spy.get("total_return_pct", 0.0)},
        "breakdown": breakdown,
        "summary": summary,
    }
    return out, html, label


def main():
    ap = argparse.ArgumentParser(description="Unified backtest: Screener + Regime + Setup")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2025-07-31")
    ap.add_argument("--cache-start", default="2016-01-01")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--benchmark", default="SPY")
    ap.add_argument("--regime-weights", default="current",
                    choices=["current", "reviewer", "equal", "none"])
    ap.add_argument("--entry-mode", default="setup", choices=["setup", "weighted"])
    ap.add_argument("--use-buckets-cache", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args()

    out, html, label = run_unified(
        start=args.start, end=args.end, cache_start=args.cache_start,
        top_n=args.top_n, benchmark=args.benchmark,
        regime_weights=args.regime_weights, entry_mode=args.entry_mode,
        use_buckets_cache=args.use_buckets_cache, refresh=args.refresh,
    )

    reports_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    safe = label.replace("+", "_")
    json_path = os.path.join(reports_dir, f"unified_backtest_{safe}.json")
    with open(json_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  JSON: {json_path}")

    if not args.no_html:
        html_path = os.path.join(reports_dir, f"unified_backtest_{safe}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  HTML: {html_path}")

    # Console summary
    m = out["metrics"]
    b = out["breakdown"]
    print("\n=== UNIFIED BACKTEST SUMMARY ===")
    print(f"  {label}: Ret={m.get('total_return_pct',0):+.2f}%  "
          f"Sharpe={m.get('sharpe',0):.2f}  MaxDD={m.get('max_drawdown_pct',0):.2f}%  "
          f"PF={m.get('profit_factor',0)}  Win={m.get('win_rate',0)*100:.1f}%  "
          f"Trades={m.get('total_trades',0)}")
    print(f"  SPY buy&hold: {out['spy_buyhold']['total_return_pct']:+.2f}%")
    print(f"  Screener: {b['screener']['n_buckets']} buckets, "
          f"{b['screener']['unique_tickers_passed']} unique passed, "
          f"avg {b['screener']['avg_candidates_per_bucket']}/bucket")
    print(f"  Regime: {b['regime']['cash_days_pct']}% cash days, "
          f"avg size_mult(invested)={b['regime']['avg_size_mult_invested']}")
    print(f"  Setup: {b['setup']['total_trades']} trades "
          f"(BO={b['setup']['breakout']}, PB={b['setup']['pullback']}), "
          f"avg score={b['setup']['avg_setup_score']}, "
          f"skipped={b['setup'].get('skipped_total',0)}")


if __name__ == "__main__":
    main()
