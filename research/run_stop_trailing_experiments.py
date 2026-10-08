"""
research/run_stop_trailing_experiments.py — S1 / S2 / S3 runner.

Single-variable, pre-registered stop / trailing experiments on the PIT-correct
baseline. Each stage runs the whole production-equivalent backtest through the
unchanged `research/harness.py` with EXACTLY ONE config field overridden in
memory; `production/config.py` is never edited.

Why a dedicated runner rather than `run_lever_grid.py`
------------------------------------------------------
The stop / trailing question needs diagnostics a leverage grid does not produce:

* §6 asks whether stop-outs terminate trades with "meaningful subsequent
  potential". MFE over the realised window cannot answer that — the window ends
  at the stop. We therefore run a **counterfactual** on every stop-out: what did
  price do AFTER the stop filled, over a fixed forward horizon, measured in R
  from the actual entry? That is a measurement of the recorded path, not a
  simulated alternative strategy.
* §8 asks us to separate "the trailing stop truncates winners" from "a wider
  trailing stop merely lengthens losing trades". That needs a joint split of
  exit reason x MFE-bucket x holding period.
* §10 needs the full realised-R distribution, not just its mean.

The counterfactual is computed from the same OHLCV cache, sliced to the trade's
own exit date, and is never fed back into any backtest. It cannot influence a
result; it only describes trades that already happened.

Design rules honoured
---------------------
* one variable at a time; no TP x stop combined search (§15)
* frozen universe (PIT monthly bucket cache reused), frozen data / costs /
  execution / slippage / window
* the control (all-baseline) variant is re-run in every stage as the historical
  control required by §14
* no production parameter is written; overrides live in memory only

Usage
-----
    python research/run_stop_trailing_experiments.py --stage s1
    python research/run_stop_trailing_experiments.py --stage s2
    python research/run_stop_trailing_experiments.py --stage s3
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

SUBPERIODS = {
    "2024_full": ("2024-01-01", "2024-12-31"),
    "2025_jan_mar": ("2025-01-01", "2025-03-31"),
    "2025_apr_jul": ("2025-04-01", "2025-07-31"),
}

# Pre-registered grids (§5, §7, §9). Frozen before any run; extending a grid
# later is a protocol deviation and is reported as such.
GRIDS = {
    "s1": ("stop_atr_mult", 1.5, [1.2, 1.5, 1.8, 2.0]),
    "s2": ("trailing_atr_mult", 1.5, [1.5, 2.0, 2.5]),
    "s3": ("trailing_trigger_r", 1.0, [0.5, 1.0, 1.5]),
}

STAGE_TITLE = {
    "s1": "S1 — initial stop width (stop_atr_mult)",
    "s2": "S2 — trailing stop width (trailing_atr_mult)",
    "s3": "S3 — trailing activation (trailing_trigger_r)",
}

_HORIZON_SESSIONS = 20     # forward sessions inspected after a stop-out
_BARS: dict = {}


def _bars(cfg, ticker: str):
    if ticker not in _BARS:
        path = os.path.join(cfg.cache_dir, f"{ticker}.csv")
        _BARS[ticker] = H._bars(cfg, ticker) if os.path.exists(path) else None
    return _BARS[ticker]


# ---------------------------------------------------------------------------
# counterfactual: what happened AFTER a stop filled
# ---------------------------------------------------------------------------
def _post_stop_path(cfg, trade: dict, horizon: int = _HORIZON_SESSIONS) -> dict:
    """For a trade that exited, measure the price path over the next
    `horizon` sessions AFTER the exit date.

    All values are expressed in R from the ACTUAL entry, using the ACTUAL
    initial stop distance — the same R unit the backtest itself uses. This is a
    description of recorded prices, not a simulated strategy: no order, fill,
    cost or exit rule is applied, and the result is never fed back into a run.
    """
    entry = float(trade["entry_price"])
    stop = trade.get("stop_price")
    if stop is None:
        return {"status": "unavailable — no stop_price on the record"}
    risk = entry - float(stop)
    if risk <= 0:
        return {"status": "unavailable — non-positive risk unit"}
    df = _bars(cfg, trade["ticker"])
    if df is None:
        return {"status": "unavailable — no cached bars"}
    try:
        d0 = datetime.date.fromisoformat(trade["exit_date"])
    except Exception:
        return {"status": "unavailable — unparseable exit_date"}
    fut = df[df["datetime"].dt.date > d0].head(horizon)
    if not len(fut):
        return {"status": "no forward sessions in the data window",
                "n_forward_sessions": 0}
    hi = float(fut["high"].max())
    lo = float(fut["low"].min())
    return {
        "status": "ok",
        "n_forward_sessions": int(len(fut)),
        "post_stop_mfe_r": round((hi - entry) / risk, 4),
        "post_stop_mae_r": round((lo - entry) / risk, 4),
        "post_stop_close_r": round((float(fut["close"].iloc[-1]) - entry) / risk, 4),
        "reached_plus_1r": bool(hi >= entry + risk),
        "reached_plus_2r": bool(hi >= entry + 2 * risk),
        "reached_minus_1r": bool(lo <= entry - risk),
    }


def _stopout_diagnostics(cfg, res: dict) -> dict:
    """The §6 / §8 / §10 mechanism table, keyed by exit reason."""
    trades = res["trade_log"]
    out: dict = {}
    for t in trades:
        reason = t.get("exit_reason") or "UNKNOWN"
        d = out.setdefault(reason, {
            "n": 0, "sum_r": 0.0, "r_list": [], "hold": [],
            "mfe": [], "mae": [], "post": [], "reached_1r": 0,
            "reached_2r": 0, "post_ok": 0, "net": 0.0,
        })
        r = t.get("r_multiple")
        d["n"] += 1
        d["sum_r"] += (r or 0.0)
        d["net"] += (t.get("net_pnl") or 0.0)
        if r is not None:
            d["r_list"].append(r)
        if t.get("holding_days") is not None:
            d["hold"].append(t["holding_days"])
        m, a = H._mfe_mae(cfg, t)
        if m is not None:
            d["mfe"].append(m)
            d["mae"].append(a)
        p = _post_stop_path(cfg, t)
        d["post"].append(p)
        if p.get("status") == "ok":
            d["post_ok"] += 1
            d["reached_1r"] += int(p["reached_plus_1r"])
            d["reached_2r"] += int(p["reached_plus_2r"])
    table = {}
    for k, v in out.items():
        n = v["n"]
        table[k] = {
            "n": n,
            "share_pct": round(100.0 * n / len(trades), 2) if trades else None,
            "avg_r": round(v["sum_r"] / n, 4) if n else None,
            "median_r": (round(stats.median(v["r_list"]), 4)
                         if v["r_list"] else None),
            "sum_r": round(v["sum_r"], 3),
            "sum_net_pnl": round(v["net"], 2),
            "avg_holding_cal_days": (round(stats.mean(v["hold"]), 2)
                                     if v["hold"] else None),
            "mfe_r_mean": round(stats.mean(v["mfe"]), 4) if v["mfe"] else None,
            "mae_r_mean": round(stats.mean(v["mae"]), 4) if v["mae"] else None,
            "mfe_r_median": (round(stats.median(v["mfe"]), 4)
                             if v["mfe"] else None),
            "post_exit_measured": v["post_ok"],
            "post_exit_pct_reached_plus_1r": (
                round(100.0 * v["reached_1r"] / v["post_ok"], 2)
                if v["post_ok"] else None),
            "post_exit_pct_reached_plus_2r": (
                round(100.0 * v["reached_2r"] / v["post_ok"], 2)
                if v["post_ok"] else None),
            "post_exit_mfe_r_mean": (
                round(stats.mean([p["post_stop_mfe_r"] for p in v["post"]
                                  if p.get("status") == "ok"]), 4)
                if v["post_ok"] else None),
        }
    return table


def _mfe_buckets(cfg, res: dict) -> dict:
    """Win/loss quality split by realised MFE — separates 'the stop cut a
    winner' from 'the trade was never going to work'."""
    buckets = {"mfe<0R": [], "0-0.5R": [], "0.5-1R": [], "1-2R": [], ">=2R": []}
    for t in res["trade_log"]:
        m, _ = H._mfe_mae(cfg, t)
        if m is None:
            continue
        k = ("mfe<0R" if m < 0 else "0-0.5R" if m < 0.5 else "0.5-1R"
             if m < 1 else "1-2R" if m < 2 else ">=2R")
        buckets[k].append((t.get("r_multiple"), t.get("exit_reason"),
                           t.get("holding_days")))
    out = {}
    for k, rows in buckets.items():
        rs = [r for r, _, _ in rows if r is not None]
        hd = [h for _, _, h in rows if h is not None]
        out[k] = {
            "n": len(rows),
            "avg_r": round(stats.mean(rs), 4) if rs else None,
            "win_rate_pct": (round(100 * sum(1 for r in rs if r > 0) / len(rs), 1)
                             if rs else None),
            "avg_holding_days": round(stats.mean(hd), 2) if hd else None,
            "exit_reasons": {rc: sum(1 for _, x, _ in rows if x == rc)
                             for rc in sorted({x for _, x, _ in rows})},
        }
    return out


def _r_distribution(res: dict) -> dict:
    """Full realised-R distribution — the §10 requirement, and the check on
    whether a candidate merely shifts mass around or changes the shape."""
    rs = sorted(t.get("r_multiple") for t in res["trade_log"]
                if t.get("r_multiple") is not None)
    if not rs:
        return {}
    edges = [(-99, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1), (1, 1.5),
             (1.5, 2), (2, 3), (3, 99)]
    hist = {}
    for lo, hi in edges:
        label = f"{lo:g}..{hi:g}" if hi < 90 else f">={lo:g}"
        hist[label] = sum(1 for r in rs if lo <= r < hi)
    return {
        "n": len(rs),
        "min": round(rs[0], 3), "p25": round(rs[len(rs) // 4], 3),
        "median": round(stats.median(rs), 3),
        "p75": round(rs[(3 * len(rs)) // 4], 3),
        "max": round(rs[-1], 3),
        "mean": round(stats.mean(rs), 4),
        "std": round(stats.pstdev(rs), 4) if len(rs) > 1 else 0.0,
        "histogram": hist,
        "pct_positive": round(100 * sum(1 for r in rs if r > 0) / len(rs), 2),
        "top_5_pct_of_total_r": (
            round(100 * sum(sorted(rs, reverse=True)[:5]) / sum(rs), 1)
            if sum(rs) > 0 else None),
    }


def _trade_overlap(res_a: dict, res_b: dict) -> dict:
    """How much of the trade set is shared between the control and a variant.

    §7 of the spec asks for a limitations section; overlap is the honest way to
    say whether a variant's difference is a broad re-ranking or a handful of
    different trades."""
    ka = {(t["ticker"], t["entry_date"]) for t in res_a["trade_log"]}
    kb = {(t["ticker"], t["entry_date"]) for t in res_b["trade_log"]}
    return {"n_control": len(ka), "n_variant": len(kb),
            "shared": len(ka & kb), "only_control": len(ka - kb),
            "only_variant": len(kb - ka),
            "jaccard": (round(len(ka & kb) / len(ka | kb), 4)
                        if (ka | kb) else None)}


def run_stage(stage: str, out_path: str) -> dict:
    param, baseline_value, grid = GRIDS[stage]
    print(f"\n{'=' * 78}\n{STAGE_TITLE[stage]}")
    print(f"  variable      : {param}")
    print(f"  pre-registered: baseline {baseline_value} | grid {grid}")
    print(f"{'=' * 78}", flush=True)

    variants: dict = {}
    results: dict = {}
    for v in grid:
        cfg = H.load_config(**{param: v})
        eff = getattr(cfg, param)
        tag = "FROZEN BASELINE (control)" if eff == baseline_value else "candidate"
        print(f"\n--- {param} = {eff}  [{tag}] ---", flush=True)
        run = H.run_backtest(cfg)
        result = run["result"]
        if "error" in result:
            raise SystemExit(f"backtest error at {param}={eff}: {result['error']}")
        results[eff] = result
        d = H.dashboard(result, cfg)
        variants[str(eff)] = {
            param: eff,
            "is_control": eff == baseline_value,
            "dashboard": d,
            "subperiods": H.subperiods(result, SUBPERIODS),
            "exit_mechanism": _stopout_diagnostics(cfg, result),
            "mfe_buckets": _mfe_buckets(cfg, result),
            "r_distribution": _r_distribution(result),
        }
        sl = variants[str(eff)]["exit_mechanism"].get("STOP_LOSS", {})
        print(f"  return={d['return_pct']}%  maxDD={d['max_dd_pct']}%  "
              f"sharpe={d['sharpe']}  sortino={d['sortino']}  "
              f"calmar={d['calmar']}", flush=True)
        print(f"  PF={d['profit_factor']}  avgR={d['avg_r']}  "
              f"medR={d['median_r']}  trades={d['n_trades']}  "
              f"expo={d['avg_exposure_pct']}%", flush=True)
        print(f"  hold={d['avg_holding_cal_days']}d  "
              f"STOP_LOSS n={sl.get('n')} avgR={sl.get('avg_r')}  "
              f"post-stop>=+1R: {sl.get('post_exit_pct_reached_plus_1r')}%  "
              f"avgR dist std="
              f"{variants[str(eff)]['r_distribution'].get('std')}", flush=True)

    base_eff = baseline_value
    base = variants[str(base_eff)]
    for k, v in variants.items():
        v["vs_control"] = H.compare_to_baseline(v["dashboard"], base["dashboard"])
        v["trade_overlap_vs_control"] = _trade_overlap(
            results[base_eff], results[float(k)])
        v["subperiod_deltas"] = {
            sp: {"control_return_pct": base["subperiods"][sp].get("return_pct"),
                 "variant_return_pct": v["subperiods"][sp].get("return_pct"),
                 "delta_pp": (
                     round(v["subperiods"][sp]["return_pct"]
                           - base["subperiods"][sp]["return_pct"], 2)
                     if v["subperiods"][sp].get("return_pct") is not None
                     and base["subperiods"][sp].get("return_pct") is not None
                     else None),
                 "control_n_trades": base["subperiods"][sp].get("n_trades"),
                 "variant_n_trades": v["subperiods"][sp].get("n_trades")}
            for sp in SUBPERIODS}

    payload = {
        "generated": datetime.date.today().isoformat(),
        "stage": stage.upper(),
        "title": STAGE_TITLE[stage],
        "changing_variable": param,
        "control_value": baseline_value,
        "pre_registered_grid": grid,
        "grid_protocol": ("pre-registered before any run; no value outside this "
                          "list was tested and no combined search was run (§15)"),
        "unchanged": ("everything else — PIT-correct Regime v1, Setup v1, "
                      "entry, take_profit, risk_per_trade, max_open_positions, "
                      "execution, costs, slippage, universe, data window"),
        "metrics_note": {
            "sharpe": "mean/std x sqrt(252) on daily equity returns "
                      "(production/backtest.py::_summary convention)",
            "r_unit": "realised R = (exit - entry) / (entry - initial stop), "
                      "the same unit the backtest uses",
            "mfe_mae": "over the realised holding window, from daily bars",
            "post_exit": f"the next {_HORIZON_SESSIONS} sessions AFTER the exit "
                         "date, in R from the actual entry; a description of "
                         "recorded prices, NOT a simulated alternative trade. "
                         "Unavailable when the window ends first.",
        },
        "criteria": {
            "C1": "Sharpe improves over the corrected control (0.513)",
            "C2": "MaxDD does not materially worsen beyond -10.39%",
            "C3": "not driven by one short subperiod (2024 / early 2025 / "
                  "late 2025)",
            "C4": "a plausible mechanism is visible in trade-level data",
            "robustness": "smooth response + neighbour support + stable trade "
                          "count / exposure — a lone spike is not evidence",
        },
        "git": H.git_commit(),
        "variants": variants,
    }
    H.save_json(out_path, payload)

    print(f"\n{'=' * 78}\n{stage.upper()} comparison (control = {baseline_value})")
    print(f"{'value':>7} {'ret%':>7} {'maxDD%':>8} {'sharpe':>7} {'sortino':>8} "
          f"{'calmar':>7} {'avgR':>7} {'medR':>7} {'PF':>5} {'trades':>6} "
          f"{'expo%':>6} {'hold':>6} {'stopOut':>7} {'ovlp':>5}")
    for v in grid:
        x = variants[str(v)]
        d = x["dashboard"]
        sl = x["exit_mechanism"].get("STOP_LOSS", {}).get("n")
        print(f"{v:>7} {d['return_pct']:>7} {d['max_dd_pct']:>8} {d['sharpe']:>7} "
              f"{str(d['sortino']):>8} {str(d['calmar']):>7} {str(d['avg_r']):>7} "
              f"{str(d['median_r']):>7} {str(d['profit_factor']):>5} "
              f"{d['n_trades']:>6} {str(d['avg_exposure_pct']):>6} "
              f"{str(d['avg_holding_cal_days']):>6} {str(sl):>7} "
              f"{str(x['trade_overlap_vs_control']['jaccard']):>5}")
    print(f"\nreport -> {out_path}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=sorted(GRIDS))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    out = args.out or os.path.join(
        H._REPO_ROOT, "reports",
        f"stop_trailing_{args.stage}_pitcorrected_"
        f"{datetime.date.today().isoformat()}.json")
    run_stage(args.stage, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
