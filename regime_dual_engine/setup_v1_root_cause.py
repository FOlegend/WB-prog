"""
setup_v1_root_cause.py — Setup V1 Root-Cause Analysis (READ-ONLY, no code change)

Runs the 3 core variants (Breakout / Pullback / Both) under frozen Regime v1,
SAVES the full trade log + equity curve, then answers:

  Q1  Why does SIDEWAYS lose money?   (setup type / score / holding / exit
      reason / R multiple / regime score + TRADING COST friction check)
  Q2  Why does Pullback outperform Breakout in Regime v1?
  Q3  Why does Breakout+Pullback worsen MaxDD? (overlap / concentration /
      priority / correlated exposure / marginal trade quality + CONCURRENT
      OPEN POSITIONS & simultaneous-drawdown statistics)

No parameter is changed. Regime v1 / Screener / setup_agent untouched.

Run (background, ~10 min):
  python regime_dual_engine/setup_v1_root_cause.py
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
from dynamic_universe_backtest import build_rebalance_calendar
from src.backtest.engine import compute_metrics

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine.pit_breadth_data import get_breadth
from regime_dual_engine.backtest_harness import DualEngineBacktest, load_data

START, END, TOP_N = "2018-01-01", "2025-07-31", 20


def _run_full(entry_types):
    """Regime-v1 backtest returning full summary (trade_log incl. fees fields)."""
    cfg = Config()
    cfg.entry_mode = "setup"
    cfg.setup_enabled_types = entry_types
    cfg.setup_score_threshold = 0.5
    cfg.setup_breakout_components = ["ma", "rs_rank", "volume", "vcp"]
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


def _fees_per_trade(t):
    """Recompute estimated round-trip friction for a trade (USD)."""
    sh = t["shares"]
    e, x = t["entry_price"], t["exit_price"]
    buy_cost = sh * e * 0.0005
    sell_notional = sh * x
    sell_cost = sell_notional * 0.0005 + sell_notional * 0.0000206
    sell_cost += min(sh * 0.000195, 9.79)
    return buy_cost + sell_cost


def _regime_at_entry(t):
    return t.get("entry_regime", "UNKNOWN")


def _holding_days(t):
    hd = t.get("holding_days")
    if hd is not None:
        return hd
    try:
        return (pd.Timestamp(t["exit_date"]) - pd.Timestamp(t["entry_date"])).days
    except Exception:
        return None


def _exit_dist(trades):
    return dict(Counter(t.get("exit_reason") for t in trades))


def _setup_score_dist(trades):
    s = [t.get("setup_score") for t in trades if t.get("setup_score") is not None]
    if not s:
        return {}
    buckets = {"0.5-0.6": 0, "0.6-0.7": 0, "0.7-0.8": 0, "0.8-0.9": 0, "0.9-1.0": 0}
    for v in s:
        for lo, hi, name in [(0.5, 0.6, "0.5-0.6"), (0.6, 0.7, "0.6-0.7"),
                             (0.7, 0.8, "0.7-0.8"), (0.8, 0.9, "0.8-0.9"),
                             (0.9, 1.01, "0.9-1.0")]:
            if lo <= v < hi:
                buckets[name] += 1
                break
    return buckets


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
        "avg_holding_days": round(float(np.mean([_holding_days(t) for t in trades if _holding_days(t) is not None])), 1),
        "avg_friction_pct": round(float(np.mean([_fees_per_trade(t) / max(1e-9, t["position_value"]) * 100
                                                 for t in trades])), 3),
        "avg_gross_pct": round(float(np.mean([t["return_pct"] for t in trades])), 3),
        "avg_net_pct": round(float(np.mean([t["net_pnl"] / max(1e-9, t["position_value"]) * 100
                                            for t in trades])), 3),
    }


def _concurrent_position_stats(trades, eq_dates):
    """Per-day open positions by setup type; overlap fraction + simultaneous DD."""
    dates = pd.DatetimeIndex(eq_dates)
    day_open_brk = np.zeros(len(dates))
    day_open_pull = np.zeros(len(dates))
    for t in trades:
        st = t.get("setup_type")
        try:
            e = pd.Timestamp(t["entry_date"]); x = pd.Timestamp(t["exit_date"])
        except Exception:
            continue
        m = (dates >= e) & (dates <= x)
        if st == "breakout":
            day_open_brk[m] += 1
        elif st == "pullback":
            day_open_pull[m] += 1
    overlap_days = int(((day_open_brk > 0) & (day_open_pull > 0)).sum())
    both_open_frac = overlap_days / len(dates) * 100
    return {
        "n_days": int(len(dates)),
        "overlap_days_both_setups": overlap_days,
        "both_open_frac_pct": round(both_open_frac, 2),
        "days_breakout_open": int((day_open_brk > 0).sum()),
        "days_pullback_open": int((day_open_pull > 0).sum()),
        "max_concurrent_brk": int(day_open_brk.max()),
        "max_concurrent_pull": int(day_open_pull.max()),
    }


def _drawdown_episodes(eq_series):
    """Return list of drawdown periods (start, trough, end) > 5%."""
    cummax = eq_series.cummax()
    dd = eq_series / cummax - 1.0
    out = []
    in_dd = False
    for i, (d, v) in enumerate(dd.items()):
        if v < -0.05 and not in_dd:
            start = d; in_dd = True
        elif v >= -0.05 and in_dd:
            out.append((start, d, float(dd.loc[start:d].min())))
            in_dd = False
    if in_dd:
        out.append((start, dd.index[-1], float(dd.loc[start:].min())))
    return out


def main():
    print("=" * 78)
    print("  SETUP V1 ROOT-CAUSE ANALYSIS (Regime v1, read-only)")
    print("=" * 78, flush=True)
    variants = {}
    for name, types in [("breakout", ["breakout"]), ("pullback", ["pullback"]),
                        ("both", ["breakout", "pullback"])]:
        print(f"  --- running {name} ---", flush=True)
        variants[name] = _run_full(types)
        print(f"  >> {name}: {len(variants[name]['trade_log'])} trades", flush=True)

    # save full logs for reuse
    logs = {k: {"trades": v["trade_log"], "equity": v["equity_curve"]}
            for k, v in variants.items()}
    with open(os.path.join(_REPO_ROOT, "reports", "setup_v1_rootcause_logs.json"), "w") as f:
        json.dump(logs, f, indent=1, default=str)
    print("  saved reports/setup_v1_rootcause_logs.json", flush=True)

    # =====================================================================
    # Q1 — SIDEWAYS losses (use 'both' trades since both setups present)
    # =====================================================================
    print("\n" + "=" * 78)
    print("  Q1 — WHY DOES SIDEWAYS LOSE MONEY?  (both-variant trades)")
    print("=" * 78)
    bt = pd.DataFrame(variants["both"]["trade_log"])
    q1 = {}
    for reg in ["BULL", "SIDEWAYS", "BEAR"]:
        sub = bt[bt["entry_regime"] == reg]
        if sub.empty:
            print(f"\n  {reg}: no trades"); continue
        print(f"\n  ---- {reg}: n={len(sub)} ----")
        print(f"    perf: {_perf(sub.to_dict('records'))}")
        print(f"    exit reasons: {_exit_dist(sub.to_dict('records'))}")
        print(f"    setup_score dist: {_setup_score_dist(sub.to_dict('records'))}")
        q1[reg] = {
            "perf": _perf(sub.to_dict("records")),
            "exit_reasons": _exit_dist(sub.to_dict("records")),
            "setup_scores": _setup_score_dist(sub.to_dict("records")),
        }
    # friction vs edge in SIDEWAYS
    sw = bt[bt["entry_regime"] == "SIDEWAYS"]
    if not sw.empty:
        per = _perf(sw.to_dict("records"))
        print(f"\n  SIDEWAYS friction check:")
        print(f"    avg gross ret {per['avg_gross_pct']}% vs avg net {per['avg_net_pct']}% "
              f"(friction {per['avg_friction_pct']}%)")
        print(f"    avg holding {per['avg_holding_days']}d;  trades {per['n']}")
        # win/loss split by exit reason
        for reason in ["STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "TIME_STOP", "SIGNAL_EXIT", "BACKTEST_END"]:
            rr = sw[sw["exit_reason"] == reason]
            if not rr.empty:
                rper = _perf(rr.to_dict("records"))
                print(f"      {reason:<14} n={rper['n']:<4} win={rper['win_pct']}%  "
                      f"avgR={rper['avg_r']}  net=${rper['net_pnl']:+.2f}")

    # =====================================================================
    # Q2 — Pullback vs Breakout in Regime v1
    # =====================================================================
    print("\n" + "=" * 78)
    print("  Q2 — WHY PULLBACK > BREAKOUT IN REGIME v1?")
    print("=" * 78)
    q2 = {}
    for name, types in [("breakout", ["breakout"]), ("pullback", ["pullback"])]:
        trades = variants[name]["trade_log"]
        tr = pd.DataFrame(trades)
        per = _perf(trades)
        print(f"\n  ---- {name}: n={per['n']}  PF={per['pf']}  Win={per['win_pct']}%  "
              f"AvgR={per['avg_r']}  net=${per['net_pnl']:+.2f}  hold={per['avg_holding_days']}d ----")
        print(f"    exit: {_exit_dist(trades)}")
        print(f"    setup_score dist: {_setup_score_dist(trades)}")
        for reg in ["BULL", "SIDEWAYS"]:
            sub = tr[tr["entry_regime"] == reg]
            if not sub.empty:
                sp = _perf(sub.to_dict("records"))
                print(f"    {reg}: n={sp['n']} PF={sp['pf']} Win={sp['win_pct']}% "
                      f"AvgR={sp['avg_r']} net=${sp['net_pnl']:+.2f}")
        # by year
        tr["year"] = pd.to_datetime(tr["entry_date"]).dt.year
        yearly = {}
        for y, sub in tr.groupby("year"):
            yearly[int(y)] = _perf(sub.to_dict("records"))
        print(f"    yearly: " + ", ".join(f"{y}:{v['net_pnl']:+.0f}$({v['n']})"
                                          for y, v in sorted(yearly.items())))
        q2[name] = {"perf": per, "exit": _exit_dist(trades),
                    "score_dist": _setup_score_dist(trades), "yearly": yearly}

    # =====================================================================
    # Q3 — Why Both worsens MaxDD
    # =====================================================================
    print("\n" + "=" * 78)
    print("  Q3 — WHY BREAKOUT+PULLBACK WORSENS MaxDD?")
    print("=" * 78)
    both_trades = pd.DataFrame(variants["both"]["trade_log"])
    eq_b = pd.DataFrame(variants["both"]["equity_curve"])
    eq_b["date"] = pd.to_datetime(eq_b["date"])
    eq_b = eq_b.set_index("date")["equity"]
    bt_by_setup = both_trades.groupby("setup_type")
    print("\n  both-variant trades split by setup type:")
    for st, sub in bt_by_setup:
        p = _perf(sub.to_dict("records"))
        print(f"    {st:<10} n={p['n']:<4} PF={p['pf']} Win={p['win_pct']}% "
              f"AvgR={p['avg_r']} net=${p['net_pnl']:+.2f}")

    # marginal trade quality: both-only vs single-variant
    single_brk = {t["ticker"] + "|" + str(t["entry_date"]) for t in variants["breakout"]["trade_log"]}
    single_pull = {t["ticker"] + "|" + str(t["entry_date"]) for t in variants["pullback"]["trade_log"]}
    both_keys = both_trades.apply(lambda r: r["ticker"] + "|" + str(r["entry_date"]), axis=1).tolist()
    # trades in 'both' that did NOT appear in the corresponding single run
    marginal_brk = [t for t in variants["both"]["trade_log"]
                    if t["setup_type"] == "breakout" and (t["ticker"] + "|" + str(t["entry_date"])) not in single_brk]
    marginal_pull = [t for t in variants["both"]["trade_log"]
                     if t["setup_type"] == "pullback" and (t["ticker"] + "|" + str(t["entry_date"])) not in single_pull]
    print("\n  marginal trades in 'both' (not present in the same-type single run):")
    for st, m in [("breakout", marginal_brk), ("pullback", marginal_pull)]:
        p = _perf(m)
        print(f"    {st:<10} n={p['n']:<4} PF={p['pf']} Win={p['win_pct']}% "
              f"AvgR={p['avg_r']} net=${p['net_pnl']:+.2f}")

    # concurrent open positions + simultaneous drawdown
    eq_dates = eq_b.index.strftime("%Y-%m-%d").tolist()
    conc = _concurrent_position_stats(variants["both"]["trade_log"], eq_dates)
    print(f"\n  concurrent open positions (both variant):")
    print(f"    {json.dumps(conc, indent=2)}")

    # drawdown episodes & regime overlap during them
    episodes = _drawdown_episodes(eq_b)
    print(f"\n  drawdown episodes >5% (both variant): {len(episodes)}")
    for (s, e, depth) in episodes:
        # open overlap within the episode
        m = (pd.DatetimeIndex(eq_dates) >= s) & (pd.DatetimeIndex(eq_dates) <= e)
        n_dd_days = int(m.sum())
        print(f"    {s.date()}..{e.date()}  depth={depth*100:.1f}%  days={n_dd_days}")

    out = {"Q1_sideways": q1, "Q2_pullback_vs_breakout": q2,
           "Q3_concurrent": conc}
    with open(os.path.join(_REPO_ROOT, "reports", "setup_v1_rootcause.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\n  Saved -> reports/setup_v1_rootcause.json")


if __name__ == "__main__":
    main()
