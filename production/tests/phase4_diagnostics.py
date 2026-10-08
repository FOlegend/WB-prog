"""
phase4_diagnostics.py — D1-D6 read-only diagnostics (Phase 4, approved 2026-10-01)

Answers ONE question: **where does the frozen end-to-end system lose, and which
component owns the loss?** It changes nothing: no parameter, no strategy, no
production path.

Harness reuse (spec §10: do not build a parallel backtest framework)
--------------------------------------------------------------------
The production-equivalent backtest (`production/backtest.py::ProductionBacktest`)
is executed UNCHANGED. To reach the per-day DecisionRecords it discards, this
script temporarily wraps `production.backtest.run_daily` with a capturing
decorator **inside this process only**. No production file is modified.

Diagnostics
-----------
D1 exit attribution      reason x realised R, MFE/MAE, profit given back
D2 signal -> fill funnel candidates -> valid -> risk-allowed -> proposed -> filled
D3 fill / setup quality  score, entry gap, extension, size multiplier vs outcome
D4 risk & sizing         realised risk vs 1% target, R distribution, exposure caps
D5 exposure constraints  regime size multiplier / veto flags / budget binding
D6 benchmark gap         SPY vs system, exposure-adjusted

Outputs: reports/phase4_diag_data_2026-10-01.json  (+ console summary)

Run:
  python production/tests/phase4_diagnostics.py
"""
from __future__ import annotations

import collections
import json
import math
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import production.backtest as backtest_mod
from production.backtest import ProductionBacktest
from production.config import ProductionConfig
from production.datasource import build_cached_source
from production.risk.risk import size_swing_position

START = "2024-01-01"
END = "2025-07-31"
OUT = os.path.join(_REPO_ROOT, "reports", "phase4_diag_data_2026-10-01.json")
TRADE_LOG = os.path.join(_REPO_ROOT, "reports",
                         "production_bt_2024-01-01_2025-07-31.json")

_BARS: dict = {}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _bars(cfg, ticker: str):
    if ticker in _BARS:
        return _BARS[ticker]
    path = os.path.join(cfg.cache_dir, f"{ticker}.csv")
    if not os.path.exists(path):
        _BARS[ticker] = None
        return None
    import pandas as pd
    df = pd.read_csv(path, parse_dates=["datetime"]).sort_values("datetime")
    _BARS[ticker] = df.reset_index(drop=True)
    return _BARS[ticker]


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.mean(xs), 4) if xs else None


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.median(xs), 4) if xs else None


def _pct(n, d):
    return round(100.0 * n / d, 2) if d else None


def _summ(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": round(stats.mean(vals), 4),
            "median": round(stats.median(vals), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "std": round(stats.pstdev(vals), 4) if len(vals) > 1 else 0.0}


def _run_backtest_capturing(cfg, source) -> tuple[dict, list[dict]]:
    """Run the EXISTING backtest unchanged; capture the DecisionRecords."""
    captured: list[dict] = []
    original = backtest_mod.run_daily

    def spy(*args, **kwargs):
        rec = original(*args, **kwargs)
        captured.append(rec)
        return rec

    backtest_mod.run_daily = spy
    try:
        bt = ProductionBacktest(cfg, source, START, END, verbose=False)
        cache = os.path.join(cfg.reports_dir,
                             f"production_buckets_{START}_{END}_top{cfg.screener_top_n}.json")
        res = bt.run(bucket_cache_file=cache)
    finally:
        backtest_mod.run_daily = original
    return res, captured


# ---------------------------------------------------------------------------
# D1 — exit attribution
# ---------------------------------------------------------------------------
def d1_exit_attribution(cfg, trades: list[dict]) -> dict:
    rows = []
    for t in trades:
        entry, stop = float(t["entry_price"]), float(t["stop_price"])
        risk = entry - stop
        df = _bars(cfg, t["ticker"])
        mfe = mae = None
        if df is not None and risk > 0:
            w = df[(df["datetime"] >= t["entry_date"])
                   & (df["datetime"] <= t["exit_date"])]
            if len(w):
                mfe = (float(w["high"].max()) - entry) / risk
                mae = (float(w["low"].min()) - entry) / risk
        rows.append({"ticker": t["ticker"], "reason": t["exit_reason"],
                     "r": t.get("r_multiple"), "net_pnl": t.get("net_pnl"),
                     "pnl_pct": t.get("pnl_pct"), "mfe_r": mfe, "mae_r": mae,
                     "stop_price": stop, "target_price": t.get("target_price"),
                     "entry": entry})

    by_reason = {}
    for reason in sorted({r["reason"] for r in rows}):
        g = [r for r in rows if r["reason"] == reason]
        rs = [r["r"] for r in g if r["r"] is not None]
        by_reason[reason] = {
            "n": len(g), "share_pct": _pct(len(g), len(rows)),
            "wins": sum(1 for r in rs if r > 0), "win_rate_pct": _pct(
                sum(1 for r in rs if r > 0), len(rs)),
            "sum_r": round(sum(rs), 3), "avg_r": _mean(rs),
            "sum_net_pnl": round(sum(r["net_pnl"] or 0.0 for r in g), 2),
            "avg_mfe_r": _mean([r["mfe_r"] for r in g]),
            "avg_mae_r": _mean([r["mae_r"] for r in g]),
            "avg_give_back_r": _mean([(r["mfe_r"] - r["r"])
                                      for r in g
                                      if r["mfe_r"] is not None
                                      and r["r"] is not None]),
            "pct_mfe_ge_1r": _pct(sum(1 for r in g
                                      if (r["mfe_r"] or -9) >= 1.0), len(g)),
            "pct_mfe_ge_2r": _pct(sum(1 for r in g
                                      if (r["mfe_r"] or -9) >= 2.0), len(g)),
        }

    all_r = [r["r"] for r in rows if r["r"] is not None]
    gave_back = [r for r in rows if (r["mfe_r"] or -9) >= 1.0
                 and (r["r"] or 0) <= 0]
    return {
        "n_trades": len(rows),
        "realised_r": _summ(all_r),
        "expectancy_r": _mean(all_r),
        "total_r": round(sum(all_r), 3),
        "by_reason": by_reason,
        "winners_given_back": {
            "n": len(gave_back),
            "share_pct": _pct(len(gave_back), len(rows)),
            "tickers": sorted(r["ticker"] for r in gave_back)[:20],
            "note": "reached >= +1R unrealised, then closed at <= 0R",
        },
        "stops_with_high_mfe": {
            "n": sum(1 for r in rows
                     if r["reason"] == "STOP_LOSS" and (r["mfe_r"] or -9) >= 1.0),
            "note": "STOP_LOSS trades that were >= +1R in favour first "
                    "(entry timing / stop distance, not selection)",
        },
    }


# ---------------------------------------------------------------------------
# D2 — signal -> fill funnel
# ---------------------------------------------------------------------------
def _risk_cause(reasoning: str) -> str:
    """Map the production risk engine's own rejection string to a cause."""
    r = reasoning or ""
    if "不足以買 1 股" in r:
        return "risk_budget_insufficient_for_one_share"
    if "上限夾到 0" in r:
        return "position_value_or_cash_cap_rounds_to_zero"
    if "已達最大持倉數" in r:
        return "max_open_positions_reached"
    if "cash strategy" in r:
        return "regime_cash_strategy"
    if "價格或 ATR 無效" in r:
        return "invalid_price_or_atr"
    return "other"


def d2_funnel(records: list[dict], backtest_res: dict,
              n_filled: int, cfg) -> dict:
    cand_evals = valid = risk_allowed = risk_rejected = proposed = 0
    setup_skips: collections.Counter = collections.Counter()
    risk_causes: collections.Counter = collections.Counter()
    entry_status: collections.Counter = collections.Counter()
    blocked_reasons: collections.Counter = collections.Counter()
    sessions_with_valid_setup_but_blocked = 0

    # equity/cash as seen by run_daily's entries phase = the PREVIOUS session's
    # mark-to-market values (cash is an upper bound: today's open fills are not
    # subtracted). Verified against the real risk engine in phase4_verify.py V1
    # (99.7% allow/reject agreement).
    eq_curve = backtest_res["equity_curve"]
    prev_state = {}
    for i, e in enumerate(eq_curve):
        j = i - 1 if i else 0
        prev_state[e["date"]] = (eq_curve[j]["equity"], eq_curve[j]["cash"])

    for rec in records:
        cand_evals += len(rec["setup"]["evaluations"])
        valid += rec["setup"]["n_valid"]
        for ev in rec["setup"]["evaluations"]:
            if ev.get("skipped_reason"):
                setup_skips[ev["skipped_reason"]] += 1
        ev_by_ticker = {e["ticker"]: e for e in rec["setup"]["evaluations"]}
        eq_prev, cash_prev = prev_state.get(rec["as_of"], (None, None))
        size_mult = (rec["regime"]["output"] or {}).get("position_size_mult")
        local_open = rec["positions"]["n_open"]
        for s in rec["risk"]["sizing"]:
            if s.get("allow"):
                risk_allowed += 1
                local_open += 1
                continue
            risk_rejected += 1
            ev = ev_by_ticker.get(s["ticker"], {})
            atr, price = ev.get("atr"), ev.get("signal_close")
            quality = ev.get("setup_quality_mult") or 0.0
            if not atr or not price or not eq_prev or not size_mult or not quality:
                risk_causes["unclassified"] += 1
                continue
            # authoritative cause: ask the REAL production risk engine
            replayed = size_swing_position(eq_prev, cash_prev, price, atr,
                                           size_mult, quality, local_open, cfg)
            risk_causes[_risk_cause(replayed.get("reasoning"))] += 1
            risk_causes["_risk_amount_usd_sum"] += (
                eq_prev * cfg.risk_per_trade * size_mult * quality)
        proposed += rec["entries"]["n_buys"]
        entry_status[rec["entries"]["status"]] += 1
        br = rec["entries"].get("blocked_reason")
        if br:
            key = ("max_open_positions" if "max_open_positions" in br
                   else "regime_defensive" if "regime" in br
                   else "screener_failure" if "screener" in br else br)
            blocked_reasons[key] += 1
            if rec["setup"]["n_valid"] > 0:
                sessions_with_valid_setup_but_blocked += 1

    exec_skips = collections.Counter(s.get("skip_reason") for s in
                                     backtest_res.get("skipped", []))
    n_rej = sum(v for k, v in risk_causes.items() if not k.startswith("_"))
    causes = {k: v for k, v in risk_causes.items() if not k.startswith("_")}
    return {
        "sessions": len(records),
        "funnel": {
            "candidate_ticker_evaluations": cand_evals,
            "setup_valid": valid,
            "risk_allowed": risk_allowed,
            "risk_rejected": risk_rejected,
            "buy_proposals": proposed,
            "filled_trades": n_filled,
            "rejected_at_next_open": sum(exec_skips.values()),
        },
        "conversion_pct": {
            "candidate->valid_setup": _pct(valid, cand_evals),
            "valid_setup->risk_allowed": _pct(risk_allowed, valid),
            "risk_allowed->proposal": _pct(proposed, risk_allowed),
            "proposal->filled": _pct(n_filled, proposed),
            "candidate->filled": _pct(n_filled, cand_evals),
        },
        "setup_skip_reasons": dict(setup_skips),
        "risk_rejection_causes": causes,
        "risk_rejection_causes_pct": {k: _pct(v, n_rej) for k, v in causes.items()},
        "mean_risk_budget_per_rejected_setup_usd": (
            round(risk_causes.get("_risk_amount_usd_sum", 0.0) / n_rej, 2)
            if n_rej else None),
        "risk_cause_method": (
            "AUTHORITATIVE: the production risk engine "
            "(production/risk/risk.py::size_swing_position) is re-called on the "
            "reconstructed inputs and its own rejection string is classified. "
            "Reconstruction validated in phase4_verify.py V1 (99.7% agreement). "
            "No parameter changed and nothing re-simulated"),
        "entry_session_status": dict(entry_status),
        "entry_blocked_reasons": dict(blocked_reasons),
        "sessions_with_valid_setup_but_entry_blocked":
            sessions_with_valid_setup_but_blocked,
        "next_open_skip_reasons": dict(exec_skips),
        "note": "counts only — no P&L is simulated for unfilled signals. When "
                "entries are blocked by regime/budget the pipeline never evaluates "
                "setups, so foregone signals are NOT observable from the records.",
    }


# ---------------------------------------------------------------------------
# D3 — fill / setup quality
# ---------------------------------------------------------------------------
def _bucket_report(rows, key, buckets) -> dict:
    """Bucket by a numeric key. Rows where the key is MISSING are excluded and
    counted separately — treating None as a value would silently mislabel every
    row (this happened for extension_from_pivot_pct, which is null for all 151
    trades: the first version of this table put 100% of trades in '<0%')."""
    usable = [r for r in rows if r.get(key) is not None]
    unavailable = len(rows) - len(usable)
    out = {"_rows_with_value": len(usable), "_rows_without_value": unavailable}
    for label, lo, hi in buckets:
        g = [r for r in usable if lo <= r[key] < hi]
        if not g:
            out[label] = {"n": 0}
            continue
        rs = [r["r"] for r in g if r["r"] is not None]
        out[label] = {
            "n": len(g), "share_pct": _pct(len(g), len(usable)),
            "avg_r": _mean(rs), "sum_r": round(sum(rs), 3),
            "win_rate_pct": _pct(sum(1 for r in rs if r > 0), len(rs)),
            "sum_net_pnl": round(sum(r["net_pnl"] or 0 for r in g), 2),
        }
    return out


def _group_report(rows, key) -> dict:
    """Group by an exact (non-numeric) key value."""
    out = {}
    for val in sorted({r.get(key) for r in rows}, key=lambda x: str(x)):
        g = [r for r in rows if r.get(key) == val]
        rs = [r["r"] for r in g if r["r"] is not None]
        out[str(val)] = {
            "n": len(g), "share_pct": _pct(len(g), len(rows)),
            "avg_r": _mean(rs), "sum_r": round(sum(rs), 3),
            "win_rate_pct": _pct(sum(1 for r in rs if r > 0), len(rs)),
            "sum_net_pnl": round(sum(r["net_pnl"] or 0 for r in g), 2),
        }
    return out


def d3_quality(trades: list[dict]) -> dict:
    rows = [{"r": t.get("r_multiple"), "net_pnl": t.get("net_pnl"),
             "setup_score": t.get("setup_score"),
             "gap": t.get("next_open_gap_pct"),
             "extension": t.get("extension_from_pivot_pct"),
             "size_mult": t.get("market_size_mult_at_entry"),
             "regime": t.get("entry_regime")} for t in trades]
    return {
        "by_setup_score": _bucket_report(
            rows, "setup_score",
            [("<0.60", -9, 0.60), ("0.60-0.70", 0.60, 0.70),
             ("0.70-0.80", 0.70, 0.80), ("0.80-0.90", 0.80, 0.90),
             (">=0.90", 0.90, 9)]),
        "by_entry_gap": _bucket_report(
            rows, "gap",
            [("<0%", -9, 0.0), ("0-0.5%", 0.0, 0.005), ("0.5-1%", 0.005, 0.01),
             ("1-2%", 0.01, 0.02)]),
        "by_extension": _bucket_report(
            rows, "extension",
            [("<0%", -9, 0.0), ("0-1%", 0.0, 0.01), ("1-2%", 0.01, 0.02),
             ("2-3%", 0.02, 0.03)]),
        "by_regime_at_entry": _group_report(rows, "regime"),
        "by_size_multiplier": _bucket_report(
            rows, "size_mult",
            [("0.5", 0.0, 0.75), ("1.0", 0.75, 9)]),
    }


# ---------------------------------------------------------------------------
# D4 — risk & sizing
# ---------------------------------------------------------------------------
def d4_risk(trades: list[dict], equity_by_date: dict,
            cfg) -> dict:
    risk_pcts, exposures, shares_ok = [], [], 0
    for t in trades:
        eq = equity_by_date.get(t["entry_date"])
        if not eq:
            continue
        risk_usd = float(t["shares"]) * (float(t["entry_price"])
                                         - float(t["stop_price"]))
        risk_pcts.append(risk_usd / eq)
        exposures.append((t.get("position_value") or 0.0) / eq)
        if risk_usd / eq <= cfg.risk_per_trade + 1e-6:
            shares_ok += 1
    rs = [t.get("r_multiple") for t in trades if t.get("r_multiple") is not None]

    # consecutive losses / R-sequence drawdown
    max_consec_loss = cur = 0
    for r in rs:
        cur = cur + 1 if r <= 0 else 0
        max_consec_loss = max(max_consec_loss, cur)
    peak = cum = 0.0
    worst_r_dd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        worst_r_dd = min(worst_r_dd, cum - peak)

    return {
        "risk_per_trade_target_pct": round(cfg.risk_per_trade * 100, 3),
        "realised_risk_pct_of_equity": _summ([p * 100 for p in risk_pcts]),
        "trades_at_or_below_target": shares_ok,
        "trades_above_target": len(risk_pcts) - shares_ok,
        "position_value_pct_of_equity": _summ([e * 100 for e in exposures]),
        "max_position_pct_limit": round(cfg.max_position_pct * 100, 2),
        "trades_at_position_cap": sum(
            1 for e in exposures if e >= cfg.max_position_pct - 1e-9),
        "r_distribution": {
            "mean": _mean(rs), "median": _median(rs),
            "share_positive_pct": _pct(sum(1 for r in rs if r > 0), len(rs)),
            "share_ge_1r_pct": _pct(sum(1 for r in rs if r >= 1.0), len(rs)),
            "share_le_minus1r_pct": _pct(sum(1 for r in rs if r <= -1.0), len(rs)),
            "sum_r": round(sum(rs), 3),
        },
        "max_consecutive_losing_trades": max_consec_loss,
        "worst_r_sequence_drawdown": round(worst_r_dd, 3),
    }


# ---------------------------------------------------------------------------
# D5 — exposure & binding constraints
# ---------------------------------------------------------------------------
def d5_exposure(records: list[dict], backtest_res: dict, cfg) -> dict:
    regime = collections.Counter()
    mult = collections.Counter()
    veto = collections.Counter()
    for rec in records:
        out = rec["regime"]["output"] or {}
        regime[out.get("regime_label")] += 1
        mult[out.get("position_size_mult")] += 1
        for f in (out.get("veto_flags") or []):
            veto[f] += 1

    eq = backtest_res["equity_curve"]
    npos = collections.Counter(e["n_positions"] for e in eq)
    exp_frac = [((e["equity"] - e["cash"]) / e["equity"])
                for e in eq if e["equity"]]
    sessions = len(eq)

    return {
        "sessions": sessions,
        "regime_label_counts": dict(regime),
        "position_size_mult_counts": {str(k): v for k, v in mult.items()},
        "sessions_with_mult_zero": mult.get(0.0, 0),
        "sessions_with_mult_zero_pct": _pct(mult.get(0.0, 0), sessions),
        "max_size_mult_observed": max(
            [k for k in mult if k is not None] or [0]),
        "veto_flag_counts": dict(veto),
        "positions_held_distribution": {str(k): v for k, v in sorted(npos.items())},
        "sessions_flat": npos.get(0, 0),
        "sessions_flat_pct": _pct(npos.get(0, 0), sessions),
        "sessions_at_max_open_positions": npos.get(cfg.max_open_positions, 0),
        "sessions_at_max_open_positions_pct":
            _pct(npos.get(cfg.max_open_positions, 0), sessions),
        "avg_positions_held": round(
            sum(k * v for k, v in npos.items()) / sessions, 3) if sessions else None,
        "avg_exposure_pct": round(100 * stats.mean(exp_frac), 2) if exp_frac else None,
        "max_exposure_pct": round(100 * max(exp_frac), 2) if exp_frac else None,
        "cash_drag_pct": round(100 * (1 - stats.mean(exp_frac)), 2)
                                 if exp_frac else None,
        "structural_note": (
            "size multiplier is capped: base BULL 1.0 / SIDEWAYS 0.5 / BEAR 0.0, "
            "then the Bearish Breadth Divergence cap applies min(x, 0.50) whenever "
            "the HMM label is BULL and breadth is declining"),
    }


# ---------------------------------------------------------------------------
# D6 — exposure-adjusted benchmark gap
# ---------------------------------------------------------------------------
def d6_benchmark(cfg, backtest_res: dict, spy: str = "SPY") -> dict:
    eq = backtest_res["equity_curve"]
    summary = backtest_res["summary"]
    df = _bars(cfg, spy)
    if df is None:
        return {"error": f"no cached bars for {spy}"}

    closes = {row["datetime"].strftime("%Y-%m-%d"): float(row["close"])
              for _, row in df.iterrows()}
    dates = [e["date"] for e in eq]
    spy_ret, exp_frac = [], []
    for i, d in enumerate(dates):
        if i == 0:
            spy_ret.append(0.0)
            exp_frac.append(0.0)
            continue
        p0, p1 = closes.get(dates[i - 1]), closes.get(d)
        spy_ret.append((p1 / p0 - 1.0) if (p0 and p1) else 0.0)
        e = eq[i]
        exp_frac.append((e["equity"] - e["cash"]) / e["equity"] if e["equity"] else 0.0)

    # SPY from the first to the last session in the window
    d0, d1 = dates[0], dates[-1]
    spy_total = (closes[d1] / closes[d0] - 1.0) if (closes.get(d0)
                                                    and closes.get(d1)) else None
    spy_daily_curve = 1.0
    for r in spy_ret[1:]:
        spy_daily_curve *= (1 + r)
    spy_total_from_daily = spy_daily_curve - 1.0

    # exposure-matched SPY variants (approximations, both disclosed)
    static_matched = (spy_total_from_daily * stats.mean(exp_frac)
                      if spy_total else None)
    daily_matched = 1.0
    for r, w in zip(spy_ret[1:], exp_frac[1:]):
        daily_matched *= (1 + w * r)
    daily_matched -= 1.0

    invested = [i for i, w in enumerate(exp_frac) if w > 0]
    spy_invested_only = 1.0
    for i in invested:
        if i == 0:
            continue
        spy_invested_only *= (1 + spy_ret[i])
    spy_invested_only -= 1.0

    sys_ret = summary["return_pct"] / 100.0
    return {
        "window": [d0, d1],
        "sessions": len(dates),
        "system": {"return_pct": round(sys_ret * 100, 2),
                   "cagr_pct": summary["cagr_pct"], "sharpe": summary["sharpe"],
                   "max_dd_pct": summary["max_dd_pct"],
                   "profit_factor": summary["profit_factor"],
                   "win_rate_pct": summary["win_rate_pct"]},
        "spy_buy_and_hold": {
            "return_pct": round(spy_total_from_daily * 100, 2),
            "first_close": closes.get(d0), "last_close": closes.get(d1)},
        "exposure": {
            "avg_exposure_pct": round(100 * stats.mean(exp_frac), 2),
            "sessions_invested": len(invested),
            "sessions_invested_pct": _pct(len(invested), len(dates)),
        },
        "exposure_matched_benchmarks": {
            "spy_x_avg_exposure_pct": (round(static_matched * 100, 2)
                                       if static_matched is not None else None),
            "spy_daily_exposure_weighted_pct": round(daily_matched * 100, 2),
            "spy_on_invested_days_only_pct": round(spy_invested_only * 100, 2),
        },
        "gap": {
            "system_minus_spy_pp": round((sys_ret - spy_total_from_daily) * 100, 2),
            "system_minus_daily_matched_pp":
                round((sys_ret - daily_matched) * 100, 2),
            "note": "the daily-matched figure isolates decision quality from "
                    "exposure: it applies the system's OWN daily exposure weight "
                    "to SPY. A value near 0 means the gap is exposure, not skill.",
        },
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    cfg = ProductionConfig()
    source = build_cached_source(cfg)
    print(f"=== Phase 4 diagnostics: running the existing backtest unchanged "
          f"({START}..{END}) ===")
    res, records = _run_backtest_capturing(cfg, source)
    if "error" in res:
        print("ERROR:", res["error"])
        return 1
    trades = res["trade_log"]
    equity_by_date = {e["date"]: e["equity"] for e in res["equity_curve"]}
    print(f"    captured {len(records)} DecisionRecords / {len(trades)} trades")

    out = {
        "generated": "2026-10-01",
        "window": [res["summary"]["start"], res["summary"]["end"]],
        "harness": "production/backtest.py::ProductionBacktest (unchanged) + "
                   "in-process DecisionRecord capture; no production file modified",
        "mode": "legacy (production default)",
        "system_summary": res["summary"],
        "D1_exit_attribution": d1_exit_attribution(cfg, trades),
        "D2_funnel": d2_funnel(records, res, len(trades), cfg),
        "D3_quality": d3_quality(trades),
        "D4_risk": d4_risk(trades, equity_by_date, cfg),
        "D5_exposure": d5_exposure(records, res, cfg),
        "D6_benchmark": d6_benchmark(cfg, res),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)

    # ---- console summary ----
    print("\n=== D1 exit attribution (by reason) ===")
    for k, v in out["D1_exit_attribution"]["by_reason"].items():
        print(f"  {k:14s} n={v['n']:3d} win={v['win_rate_pct']:5}% "
              f"avgR={v['avg_r']} sumR={v['sum_r']} "
              f"MFE={v['avg_mfe_r']} give_back={v['avg_give_back_r']}")
    d1 = out["D1_exit_attribution"]
    print(f"  expectancy={d1['expectancy_r']}R totalR={d1['total_r']} "
          f"winners_given_back={d1['winners_given_back']['n']} "
          f"stops_with_MFE>=1R={d1['stops_with_high_mfe']['n']}")

    print("\n=== D2 funnel ===")
    for k, v in out["D2_funnel"]["funnel"].items():
        print(f"  {k:32s}: {v}")
    print(f"  conversion: {out['D2_funnel']['conversion_pct']}")
    print(f"  risk rejection causes: {out['D2_funnel']['risk_rejection_causes']}")
    print(f"  mean rejected risk budget: "
          f"${out['D2_funnel']['mean_risk_budget_per_rejected_setup_usd']}")
    print(f"  entry blocked reasons: {out['D2_funnel']['entry_blocked_reasons']}")

    print("\n=== D4 risk ===")
    d4 = out["D4_risk"]
    print(f"  realised risk % of equity: {d4['realised_risk_pct_of_equity']}")
    print(f"  position value % of equity: {d4['position_value_pct_of_equity']}")
    print(f"  trades at position cap: {d4['trades_at_position_cap']} | "
          f"max consec losses: {d4['max_consecutive_losing_trades']} | "
          f"R-seq DD: {d4['worst_r_sequence_drawdown']}")

    print("\n=== D5 exposure ===")
    for k in ("sessions", "regime_label_counts", "position_size_mult_counts",
              "sessions_flat_pct", "sessions_at_max_open_positions_pct",
              "avg_positions_held", "avg_exposure_pct", "cash_drag_pct",
              "veto_flag_counts"):
        print(f"  {k:36s}: {out['D5_exposure'][k]}")

    print("\n=== D6 benchmark ===")
    for k, v in out["D6_benchmark"].items():
        print(f"  {k:32s}: {v}")

    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
