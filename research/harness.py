"""
research/harness.py — isolated research harness for the Return Improvement task.

Runs the EXISTING production-equivalent backtest
(`production/backtest.py::ProductionBacktest`) UNCHANGED and computes a
standardised dashboard, optionally with EXACTLY ONE config parameter overridden
for a controlled single-variable experiment.

Isolation guarantees
--------------------
* `production/config.py` is never edited. Overrides are applied to an in-memory
  copy of `ProductionConfig` via `dataclasses.replace`, so production keeps
  whatever the file says (`take_profit_atr_mult = 2.5`, etc.).
* No production module is monkeypatched except `production.backtest.run_daily`,
  and only temporarily (in-process) when a caller asks to capture the
  per-session DecisionRecords the backtest normally discards.
* The frozen universe / data / costs / execution assumptions are inherited from
  the production config; no experiment changes them.

Metrics follow the conventions of `production/backtest.py::_summary` (Sharpe =
mean/std x sqrt(252) on daily equity returns) so variant and baseline numbers
are directly comparable.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import os
import statistics as stats
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pandas as pd

import production.backtest as backtest_mod
from production.backtest import ProductionBacktest
from production.config import ProductionConfig
from production.datasource import build_cached_source

START = "2024-01-01"
END = "2025-07-31"

# every parameter this research task is allowed to vary, one at a time
ALLOWED_OVERRIDES = {
    "take_profit_atr_mult", "stop_atr_mult", "trailing_atr_mult",
    "trailing_trigger_r", "max_holding_days", "risk_per_trade",
    "max_open_positions", "max_position_pct", "max_entry_gap_pct",
}

_BARS: dict = {}


# ---------------------------------------------------------------------------
# provenance helpers
# ---------------------------------------------------------------------------
def git_commit() -> dict:
    def _run(args):
        try:
            return subprocess.run(args, cwd=_REPO_ROOT, capture_output=True,
                                  text=True, timeout=20).stdout.strip()
        except Exception as exc:  # pragma: no cover
            return f"<error: {exc}>"
    return {"head": _run(["git", "rev-parse", "HEAD"]),
            "head_short": _run(["git", "rev-parse", "--short", "HEAD"]),
            "subject": _run(["git", "log", "-1", "--pretty=%s"]),
            "is_dirty": bool(_run(["git", "status", "--porcelain", "-uno"]))}


def data_fingerprint(cfg) -> dict:
    """Identify the exact OHLCV data set used (window + content hash of SPY)."""
    out = {"cache_dir": cfg.cache_dir, "n_ticker_files": None, "spy": None}
    try:
        files = [f for f in os.listdir(cfg.cache_dir) if f.endswith(".csv")]
        out["n_ticker_files"] = len(files)
    except Exception as exc:
        out["cache_error"] = str(exc)
        return out
    path = os.path.join(cfg.cache_dir, "SPY.csv")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()[:16]
        df = pd.read_csv(path, parse_dates=["datetime"])
        out["spy"] = {"sha256_16": digest, "rows": int(len(df)),
                      "first": str(df["datetime"].min().date()),
                      "last": str(df["datetime"].max().date())}
    return out


def load_config(**overrides) -> ProductionConfig:
    """Fresh production config with at most the allowed single overrides."""
    bad = set(overrides) - ALLOWED_OVERRIDES
    if bad:
        raise ValueError(f"override not permitted by the research task: {bad}")
    cfg = ProductionConfig()
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    return cfg


def _bars(cfg, ticker: str):
    if ticker in _BARS:
        return _BARS[ticker]
    path = os.path.join(cfg.cache_dir, f"{ticker}.csv")
    if not os.path.exists(path):
        _BARS[ticker] = None
        return None
    _BARS[ticker] = (pd.read_csv(path, parse_dates=["datetime"])
                     .sort_values("datetime").reset_index(drop=True))
    return _BARS[ticker]


# ---------------------------------------------------------------------------
# the backtest runner
# ---------------------------------------------------------------------------
def run_backtest(cfg: ProductionConfig, start: str = START, end: str = END,
                 capture_records: bool = False, verbose: bool = False) -> dict:
    """Execute the existing ProductionBacktest.

    The monthly screen bucket is reused from the shared PIT cache: screening is
    deterministic, point-in-time and independent of every parameter this task
    varies (only `screener_top_n` affects it, and that is never overridden), so
    reusing it both speeds up the sweep and guarantees a frozen universe.
    """
    source = build_cached_source(cfg)
    captured: list[dict] = []
    original = backtest_mod.run_daily
    if capture_records:
        def spy(*args, **kwargs):
            rec = original(*args, **kwargs)
            captured.append(rec)
            return rec
        backtest_mod.run_daily = spy
    try:
        bt = ProductionBacktest(cfg, source, start, end, verbose=verbose)
        cache = os.path.join(
            cfg.reports_dir,
            f"production_buckets_{start}_{end}_top{cfg.screener_top_n}.json")
        res = bt.run(bucket_cache_file=cache)
    finally:
        backtest_mod.run_daily = original
    return {"result": res, "records": captured}


# ---------------------------------------------------------------------------
# metric helpers
# ---------------------------------------------------------------------------
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.mean(xs), 4) if xs else None


def _safe_div(a, b):
    return round(a / b, 4) if (a is not None and b not in (None, 0)) else None


def _mfe_mae(cfg, trade) -> tuple:
    """(MFE, MAE) in R over the realised holding window, from daily bars."""
    entry = float(trade["entry_price"])
    risk = entry - float(trade["stop_price"])
    if risk <= 0:
        return None, None
    df = _bars(cfg, trade["ticker"])
    if df is None:
        return None, None
    w = df[(df["datetime"] >= trade["entry_date"])
           & (df["datetime"] <= trade["exit_date"])]
    if not len(w):
        return None, None
    mfe = (float(w["high"].max()) - entry) / risk
    mae = (float(w["low"].min()) - entry) / risk
    return mfe, mae


def dashboard(res: dict, cfg: ProductionConfig) -> dict:
    """§15 standardised performance dashboard — comparable across variants."""
    summary = res["summary"]
    curve = res["equity_curve"]
    trades = res["trade_log"]

    eq = pd.Series([c["equity"] for c in curve],
                   index=[c["date"] for c in curve])
    daily = eq.pct_change().dropna()
    downside = daily[daily < 0]
    sortino = (daily.mean() / downside.std(ddof=1) * math.sqrt(252)
               if len(downside) > 1 and downside.std(ddof=1) > 0 else None)
    dd_dev = (math.sqrt(float((daily.clip(upper=0) ** 2).mean()))
              if len(daily) else None)
    calmar = (summary["cagr_pct"] / abs(summary["max_dd_pct"])
              if summary.get("max_dd_pct") else None)

    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]

    exp_frac = [((c["equity"] - c["cash"]) / c["equity"])
                for c in curve if c["equity"]]
    npos = [c["n_positions"] for c in curve]

    hold_days = []
    mfe_r, mae_r, capture = [], [], []
    for t in trades:
        try:
            d0 = datetime.date.fromisoformat(t["entry_date"])
            d1 = datetime.date.fromisoformat(t["exit_date"])
            hold_days.append((d1 - d0).days)
        except Exception:
            pass
        m, a = _mfe_mae(cfg, t)
        if m is not None:
            mfe_r.append(m)
            mae_r.append(a)
            if m > 0 and t.get("r_multiple") is not None:
                capture.append(t["r_multiple"] / m)

    # exit reason distribution with realised R
    exit_dist = {}
    for t in trades:
        k = t.get("exit_reason")
        exit_dist.setdefault(k, {"n": 0, "sum_r": 0.0, "sum_net_pnl": 0.0})
        exit_dist[k]["n"] += 1
        exit_dist[k]["sum_r"] += (t.get("r_multiple") or 0.0)
        exit_dist[k]["sum_net_pnl"] += (t.get("net_pnl") or 0.0)
    exit_dist = {k: {"n": v["n"], "share_pct": round(100 * v["n"] / len(trades), 2)
                     if trades else None,
                     "sum_r": round(v["sum_r"], 3),
                     "avg_r": _safe_div(v["sum_r"], v["n"]),
                     "sum_net_pnl": round(v["sum_net_pnl"], 2)}
                 for k, v in sorted(exit_dist.items(), key=lambda kv: -kv[1]["n"])}

    # return contribution by entry regime
    by_regime = {}
    for t in trades:
        k = t.get("entry_regime") or "UNKNOWN"
        by_regime.setdefault(k, {"n": 0, "sum_r": 0.0, "sum_net_pnl": 0.0,
                                 "wins": 0})
        by_regime[k]["n"] += 1
        by_regime[k]["sum_r"] += (t.get("r_multiple") or 0.0)
        by_regime[k]["sum_net_pnl"] += (t.get("net_pnl") or 0.0)
        by_regime[k]["wins"] += 1 if (t.get("net_pnl") or 0) > 0 else 0
    by_regime = {k: {"n": v["n"], "sum_r": round(v["sum_r"], 3),
                     "avg_r": _safe_div(v["sum_r"], v["n"]),
                     "win_rate_pct": round(100 * v["wins"] / v["n"], 2),
                     "sum_net_pnl": round(v["sum_net_pnl"], 2)}
                 for k, v in sorted(by_regime.items())}

    return {
        "return_pct": summary["return_pct"],
        "cagr_pct": summary["cagr_pct"],
        "max_dd_pct": summary["max_dd_pct"],
        "sharpe": summary["sharpe"],
        "sortino": round(sortino, 3) if sortino is not None else None,
        "downside_deviation_daily": (round(dd_dev, 6)
                                     if dd_dev is not None else None),
        "calmar": round(calmar, 3) if calmar is not None else None,
        "n_trades": summary["n_trades"],
        "win_rate_pct": summary["win_rate_pct"],
        "profit_factor": summary["profit_factor"],
        "expectancy_r": _mean(rs),
        "avg_r": _mean(rs),
        "median_r": (round(stats.median(rs), 4) if rs else None),
        "sum_r": round(sum(rs), 3),
        "avg_winner_r": _mean(wins),
        "avg_loser_r": _mean(losses),
        "largest_win_r": (round(max(rs), 4) if rs else None),
        "largest_loss_r": (round(min(rs), 4) if rs else None),
        "avg_holding_cal_days": _mean(hold_days),
        "median_holding_cal_days": (round(stats.median(hold_days), 1)
                                    if hold_days else None),
        "mfe_r_mean": _mean(mfe_r),
        "mae_r_mean": _mean(mae_r),
        "mfe_capture_mean": _mean(capture),
        "mfe_capture_total": _safe_div(sum(rs), sum(mfe_r)),
        "avg_exposure_pct": (round(100 * stats.mean(exp_frac), 2)
                             if exp_frac else None),
        "median_exposure_pct": (round(100 * stats.median(exp_frac), 2)
                                if exp_frac else None),
        "max_exposure_pct": (round(100 * max(exp_frac), 2) if exp_frac else None),
        "cash_drag_pct": (round(100 * (1 - stats.mean(exp_frac)), 2)
                          if exp_frac else None),
        "flat_days_pct": (round(100 * sum(1 for n in npos if n == 0) / len(npos), 2)
                          if npos else None),
        "avg_positions": (round(stats.mean(npos), 3) if npos else None),
        "sessions_at_max_open_positions": sum(
            1 for n in npos if n >= cfg.max_open_positions),
        "sessions_at_max_open_positions_pct": (
            round(100 * sum(1 for n in npos if n >= cfg.max_open_positions)
                  / len(npos), 2) if npos else None),
        "exit_reason_distribution": exit_dist,
        "by_entry_regime": by_regime,
    }


def subperiods(res: dict, spans: list) -> dict:
    """Metrics restricted to a date span, computed from the equity curve."""
    curve = res["equity_curve"]
    trades = res["trade_log"]
    out = {}
    for label, (a, b) in spans.items():
        sub = [c for c in curve if a <= c["date"] <= b]
        if len(sub) < 2:
            out[label] = {"n_days": len(sub)}
            continue
        ret = sub[-1]["equity"] / sub[0]["equity"] - 1.0
        eq = pd.Series([c["equity"] for c in sub], index=[c["date"] for c in sub])
        dr = eq.pct_change().dropna()
        peak = eq.cummax()
        mdd = float((eq / peak - 1.0).min())
        sharpe = (dr.mean() / dr.std() * math.sqrt(252)
                  if len(dr) > 1 and dr.std() > 0 else 0.0)
        ts = [t for t in trades if a <= t["entry_date"] <= b]
        rs = [t.get("r_multiple") for t in ts if t.get("r_multiple") is not None]
        out[label] = {
            "window": [sub[0]["date"], sub[-1]["date"]], "n_days": len(sub),
            "return_pct": round(ret * 100, 2), "sharpe": round(float(sharpe), 3),
            "max_dd_pct": round(mdd * 100, 2), "n_trades": len(ts),
            "avg_r": _mean(rs), "win_rate_pct": (
                round(100 * sum(1 for r in rs if r > 0) / len(rs), 1) if rs else None),
        }
    return out


def compare_to_baseline(variant: dict, baseline: dict) -> dict:
    keys = ("return_pct", "cagr_pct", "max_dd_pct", "sharpe", "sortino",
            "calmar", "avg_r", "expectancy_r", "profit_factor", "n_trades",
            "avg_exposure_pct", "win_rate_pct", "mfe_capture_total")
    return {k: {"baseline": baseline.get(k), "variant": variant.get(k),
                "delta": (round(variant[k] - baseline[k], 4)
                          if isinstance(variant.get(k), (int, float))
                          and isinstance(baseline.get(k), (int, float)) else None)}
            for k in keys}


def save_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)
