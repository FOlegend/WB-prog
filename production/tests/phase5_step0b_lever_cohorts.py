"""
phase5_step0b_lever_cohorts.py — lever-specific cohort check + leverage decision table
(read-only)

Two open questions from the Step-0 report:

Q1 (verification of my own verdict). Step 0 traded ALL 505 rejected setups, but a
   given lever only ADMITS A SUBSET of them. If the lever-admitted subset differs
   materially from the all-rejected average, the "cap is protective" verdict has to
   be restated. This script computes, per lever, the cohort that would newly become
   sizeable and its measured expectancy (from the Step-0 simulations — no re-run).

Q2 (the human's pending preference decision). Under-deployment was reclassified as
   a leverage choice, not alpha. This script quantifies what that choice means, using
   read-only arithmetic and the real SPY bars from the same window.

Nothing is modified, re-simulated or optimised. Input: the Step-0 JSON.

Run:
  python production/tests/phase5_step0b_lever_cohorts.py
"""
from __future__ import annotations

import json
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig

STEP0 = os.path.join(_REPO_ROOT, "reports",
                     "phase5_step0_marginal_cohort_2026-10-01.json")
OUT = os.path.join(_REPO_ROOT, "reports",
                   "phase5_step0b_lever_cohorts_2026-10-01.json")


def _summ(rs):
    rs = [r for r in rs if r is not None]
    if not rs:
        return {"n": 0}
    return {"n": len(rs), "avg_r": round(stats.mean(rs), 4),
            "median_r": round(stats.median(rs), 4),
            "sum_r": round(sum(rs), 3),
            "win_rate_pct": round(100.0 * sum(1 for r in rs if r > 0) / len(rs), 2)}


def _sizeable(row, cfg, *, risk_per_trade, size_mult_override, equity_scale):
    price, atr = row.get("signal_close"), row.get("atr")
    eq, cash = row.get("equity_prev"), row.get("cash_prev")
    quality = row.get("quality")
    sm = row.get("size_mult_at_signal")
    if not price or not atr or not eq or not cash or not quality or sm is None:
        return None
    if size_mult_override is not None:
        sm = size_mult_override(row)
    eq = eq * equity_scale
    cash = cash * equity_scale
    eff = sm * quality
    if eff <= 0:
        return 0
    risk_amount = eq * risk_per_trade * eff
    stop_distance = atr * cfg.stop_atr_mult
    raw = int(risk_amount // stop_distance) if stop_distance > 0 else 0
    by_value = int((eq * cfg.max_position_pct) // price)
    by_cash = int(cash // price)
    return max(0, min(raw, by_value, by_cash))


def _spy_shape(cfg, start, end, spy="SPY"):
    """SPY total return, max drawdown and daily-annualised Sharpe in the window."""
    path = os.path.join(cfg.cache_dir, f"{spy}.csv")
    if not os.path.exists(path):
        return {"error": "no SPY cache"}
    import pandas as pd
    df = pd.read_csv(path, parse_dates=["datetime"]).sort_values("datetime")
    df = df[(df["datetime"] >= start) & (df["datetime"] <= end)]
    px = df["close"].astype(float).tolist()
    if len(px) < 3:
        return {"error": "insufficient SPY bars"}
    rets = [px[i] / px[i - 1] - 1 for i in range(1, len(px))]
    peak, mdd = px[0], 0.0
    for p in px:
        peak = max(peak, p)
        mdd = min(mdd, p / peak - 1.0)
    sd = stats.pstdev(rets)
    sharpe = (stats.mean(rets) / sd * (252 ** 0.5)) if sd > 0 else None
    return {"sessions": len(px), "total_return_pct": round((px[-1] / px[0] - 1) * 100, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "sharpe_daily_annualised": round(sharpe, 3) if sharpe else None,
            "first_close": px[0], "last_close": px[-1]}


def main() -> int:
    cfg = ProductionConfig()
    payload = json.load(open(STEP0, encoding="utf-8"))
    mc = payload["marginal_cohort"]
    rows = mc.get("rows") or []
    sim = [r for r in rows if r.get("r") is not None]
    print(f"=== lever-specific cohorts (from the Step-0 simulations) ===")
    print(f"  rejected setups with a simulated outcome: {len(sim)}")

    levers = {
        "baseline (as recorded)": dict(risk_per_trade=cfg.risk_per_trade,
                                       size_mult_override=None,
                                       equity_scale=1.0),
        "(b) risk_per_trade 1.0% -> 1.5%": dict(risk_per_trade=0.015,
                                                size_mult_override=None,
                                                equity_scale=1.0),
        "(b2) risk_per_trade 1.0% -> 2.0%": dict(risk_per_trade=0.02,
                                                 size_mult_override=None,
                                                 equity_scale=1.0),
        "(c) divergence cap removed (size_mult -> 1.0)":
            dict(risk_per_trade=cfg.risk_per_trade,
                 size_mult_override=lambda r: 1.0, equity_scale=1.0),
        "(c2) SIDEWAYS base 0.5 -> 0.75":
            dict(risk_per_trade=cfg.risk_per_trade,
                 size_mult_override=lambda r: (0.75 if r.get("regime") == "SIDEWAYS"
                                               else r.get("size_mult_at_signal")),
                 equity_scale=1.0),
        "(e) capital base doubled": dict(risk_per_trade=cfg.risk_per_trade,
                                         size_mult_override=None,
                                         equity_scale=2.0),
    }

    baseline_sizeable = {}
    out_levers = {}
    for label, p in levers.items():
        sizeable, newly, sizes, dollars = 0, [], [], []
        for r in sim:
            s = _sizeable(r, cfg, **p)
            if s is None:
                continue
            if s > 0:
                sizeable += 1
            if label.startswith("baseline"):
                baseline_sizeable[id(r)] = s > 0
                continue
            if s > 0 and not baseline_sizeable.get(id(r), False):
                newly.append(r)
                sizes.append(s)
                stop_dist = r["atr"] * cfg.stop_atr_mult
                dollars.append(s * stop_dist)      # risk budget of the new trade
        if label.startswith("baseline"):
            out_levers[label] = {"sizeable": sizeable, "of": len(sim),
                                 "newly_admitted": 0}
            continue
        out_levers[label] = {
            "sizeable": sizeable, "of": len(sim),
            "newly_admitted": len(newly),
            "newly_admitted_expectancy": _summ([r["r"] for r in newly]),
            "newly_admitted_mean_shares": (round(stats.mean(sizes), 2)
                                           if sizes else None),
            "newly_admitted_mean_risk_usd": (round(stats.mean(dollars), 2)
                                             if dollars else None),
            "newly_admitted_by_regime": {
                reg: _summ([r["r"] for r in newly if r.get("regime") == reg])
                for reg in sorted({r.get("regime") for r in newly})},
        }
        print(f"\n  {label}")
        print(f"    sizeable {sizeable}/{len(sim)} | newly admitted "
              f"{len(newly)} | their avg R = "
              f"{out_levers[label]['newly_admitted_expectancy'].get('avg_r')} "
              f"(win {out_levers[label]['newly_admitted_expectancy'].get('win_rate_pct')}%)")
        print(f"    mean size {out_levers[label]['newly_admitted_mean_shares']} shares, "
              f"mean risk ${out_levers[label]['newly_admitted_mean_risk_usd']}")

    # ---- leverage decision table ----
    # Inputs follow the CURRENT (post-PIT) artifacts. The pre-PIT constants
    # (7.99 / -5.98 / 0.935 / 17.16) were leak-contaminated; see
    # reports/pit_correction_and_rebaseline_2026-10-01.md.
    _bt = os.path.join(_REPO_ROOT, "reports",
                       "production_bt_pitcorrected_2024-01-01_2025-07-31.json")
    if not os.path.exists(_bt):
        _bt = os.path.join(_REPO_ROOT, "reports",
                           "production_bt_2024-01-01_2025-07-31.json")
    dates = [e["date"] for e in json.load(open(_bt, encoding="utf-8"))["equity_curve"]]
    spy = _spy_shape(cfg, dates[0], dates[-1])
    _bl = os.path.join(_REPO_ROOT, "reports",
                       "research_baseline_pitcorrected_2026-10-01.json")
    if os.path.exists(_bl):
        _d = json.load(open(_bl, encoding="utf-8"))["dashboard"]
        sys_ret, sys_dd, sys_sharpe, exp = (_d["return_pct"], _d["max_dd_pct"],
                                            _d["sharpe"], _d["avg_exposure_pct"])
    else:                                             # pragma: no cover
        sys_ret, sys_dd, sys_sharpe, exp = 5.57, -10.39, 0.513, 23.39
    k_needed = (spy["total_return_pct"] / sys_ret) if spy.get("total_return_pct") else None
    table = []
    for k in (1, 2, 3, 4, 5):
        table.append({"scale_k": k,
                      "implied_return_pct": round(sys_ret * k, 2),
                      "implied_max_dd_pct": round(sys_dd * k, 2),
                      "sharpe": sys_sharpe,
                      "implied_avg_exposure_pct": round(exp * k, 2)})
    leverage = {
        "system_realised": {"return_pct": sys_ret, "max_dd_pct": sys_dd,
                            "sharpe": sys_sharpe, "avg_exposure_pct": exp},
        "spy_same_window": spy,
        "scale_to_match_spy_return": {
            "required_k": round(k_needed, 2) if k_needed else None,
            "implied_max_dd_pct": round(sys_dd * k_needed, 2) if k_needed else None,
            "note": "Sharpe is scale-invariant, so matching SPY's return by scaling "
                    "positions means accepting the implied drawdown; compare it with "
                    "SPY's own drawdown above"},
        "scaling_table_approx": table,
        "caveat": ("linear scaling is an approximation: compounding, cash drag and "
                   "the integer-share floor all change with size. The table is a "
                   "decision aid for a PREFERENCE, not a performance estimate"),
    }
    print(f"\n=== leverage / exposure preference table ===")
    print(f"  system  : return {sys_ret}%  MaxDD {sys_dd}%  Sharpe {sys_sharpe}  "
          f"avg exposure {exp}%")
    print(f"  SPY     : return {spy.get('total_return_pct')}%  "
          f"MaxDD {spy.get('max_drawdown_pct')}%  "
          f"Sharpe {spy.get('sharpe_daily_annualised')}")
    print(f"  to match SPY's return you would need k ≈ "
          f"{leverage['scale_to_match_spy_return']['required_k']}x exposure → "
          f"implied MaxDD ≈ "
          f"{leverage['scale_to_match_spy_return']['implied_max_dd_pct']}%")

    out = {"generated": "2026-10-01",
           "purpose": "lever-specific cohort verification + leverage decision table",
           "source": os.path.basename(STEP0),
           "lever_cohorts": out_levers,
           "leverage_decision": leverage}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
