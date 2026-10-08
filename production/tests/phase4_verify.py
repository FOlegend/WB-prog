"""
phase4_verify.py — independent verification of the Phase-4 diagnostics (read-only)

The Phase-4 report's recommendation rests on three load-bearing claims. This
script tries to BREAK them:

  V1  the risk-gate attribution ("413 of 506 rejections = the risk budget floors
      to zero shares") is a RE-DERIVATION, not a recorded field → replay every
      sizing attempt through the REAL production risk engine
      (`production.risk.risk.size_swing_position`) and measure agreement.
  V2  the benchmark-gap decomposition (+0.62 pp selection, ~28 pp exposure) is
      recomputed with three different exposure-weighting methods and split by
      sub-period, to see whether the conclusion survives method and time slicing.
  V3  counterfactual sizing: how many of the rejected setups would become
      sizeable if the effective multiplier were not halved? (pure arithmetic on
      existing records — no backtest is re-run, nothing is modified)
  V4  the entry-gap monotonicity is split by half-year to test whether it is a
      stable property or a single-period artefact.

Nothing is simulated: no parameter changes, no production path changes. Output:
reports/phase4_verify_2026-10-01.json

Run:
  python production/tests/phase4_verify.py
"""
from __future__ import annotations

import collections
import json
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

START, END = "2024-01-01", "2025-07-31"
OUT = os.path.join(_REPO_ROOT, "reports", "phase4_verify_2026-10-01.json")

_BARS: dict = {}


def _bars(cfg, ticker):
    if ticker in _BARS:
        return _BARS[ticker]
    path = os.path.join(cfg.cache_dir, f"{ticker}.csv")
    if not os.path.exists(path):
        _BARS[ticker] = None
        return None
    import pandas as pd
    _BARS[ticker] = pd.read_csv(path, parse_dates=["datetime"]).sort_values(
        "datetime").reset_index(drop=True)
    return _BARS[ticker]


def _run_capturing(cfg, source):
    captured = []
    original = backtest_mod.run_daily

    def spy(*a, **kw):
        rec = original(*a, **kw)
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
# V1 — replay every sizing attempt through the REAL risk engine
# ---------------------------------------------------------------------------
def _risk_cause(reasoning: str) -> str:
    """Classify the production risk engine's own rejection string."""
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


def v1_risk_engine_agreement(cfg, records, res):
    eq_curve = res["equity_curve"]
    prev = {}
    for i, e in enumerate(eq_curve):
        j = i - 1 if i else 0
        prev[e["date"]] = (eq_curve[j]["equity"], eq_curve[j]["cash"])

    agree = disagree_allow = 0
    n = 0
    rejects_confirmed = 0
    reject_reasons = collections.Counter()
    mismatch_examples = []
    for rec in records:
        ev_by = {e["ticker"]: e for e in rec["setup"]["evaluations"]}
        eq_p, cash_p = prev.get(rec["as_of"], (None, None))
        size_mult = (rec["regime"]["output"] or {}).get("position_size_mult")
        if eq_p is None or size_mult is None or size_mult <= 0:
            continue
        # the pipeline increments the local open counter as it accepts buys
        local_open = rec["positions"]["n_open"]
        for s in rec["risk"]["sizing"]:
            ev = ev_by.get(s["ticker"], {})
            price, atr = ev.get("signal_close"), ev.get("atr")
            quality = ev.get("setup_quality_mult") or 0.0
            if not price or not atr:
                continue
            n += 1
            replayed = size_swing_position(eq_p, cash_p, price, atr,
                                           size_mult, quality, local_open, cfg)
            if bool(replayed.get("allow")) == bool(s.get("allow")):
                agree += 1
            else:
                disagree_allow += 1
                if len(mismatch_examples) < 5:
                    mismatch_examples.append({
                        "session": rec["as_of"], "ticker": s["ticker"],
                        "recorded_allow": s.get("allow"),
                        "replayed_allow": replayed.get("allow"),
                        "recorded_shares": s.get("shares"),
                        "replayed_shares": replayed.get("shares"),
                        "reasoning": replayed.get("reasoning")})
            if not replayed.get("allow"):
                rejects_confirmed += 1
                reject_reasons[_risk_cause(replayed.get("reasoning"))] += 1
            if replayed.get("allow"):
                local_open += 1
    return {
        "sizing_attempts_replayed": n,
        "allow_matches_record": agree,
        "allow_mismatches": disagree_allow,
        "agreement_pct": round(100.0 * agree / n, 2) if n else None,
        "rejects_confirmed_by_real_engine": rejects_confirmed,
        "reject_reasons_from_real_engine": dict(reject_reasons),
        "mismatch_examples": mismatch_examples,
        "verdict": ("re-derivation CONFIRMED by the real risk engine"
                    if n and agree / n >= 0.98 else
                    "re-derivation NOT fully confirmed — investigate"),
    }


# ---------------------------------------------------------------------------
# V2 — benchmark decomposition: three methods + sub-periods
# ---------------------------------------------------------------------------
def _decompose(cfg, dates, equity, cash, closes, spy):
    rets, ws = [], []
    for i, d in enumerate(dates):
        if i == 0:
            rets.append(0.0)
            ws.append(0.0)
            continue
        p0, p1 = closes.get(dates[i - 1]), closes.get(d)
        rets.append((p1 / p0 - 1.0) if (p0 and p1) else 0.0)
        ws.append((equity[i] - cash[i]) / equity[i] if equity[i] else 0.0)
    spy_total = 1.0
    for r in rets[1:]:
        spy_total *= (1 + r)
    spy_total -= 1.0
    sys_total = equity[-1] / equity[0] - 1.0
    avg_w = stats.mean(ws[1:]) if len(ws) > 1 else 0.0
    static = spy_total * avg_w
    daily = 1.0
    for r, w in zip(rets[1:], ws[1:]):
        daily *= (1 + w * r)
    daily -= 1.0
    invested = [i for i in range(1, len(dates)) if ws[i] > 0]
    spy_inv = 1.0
    for i in invested:
        spy_inv *= (1 + rets[i])
    spy_inv -= 1.0
    return {
        "sessions": len(dates),
        "system_return_pct": round(sys_total * 100, 2),
        "spy_return_pct": round(spy_total * 100, 2),
        "avg_exposure_pct": round(avg_w * 100, 2),
        "sessions_invested": len(invested),
        "method_static_matched_pct": round(static * 100, 2),
        "method_daily_matched_pct": round(daily * 100, 2),
        "method_invested_days_only_pct": round(spy_inv * 100, 2),
        "selection_gap_static_pp": round((sys_total - static) * 100, 2),
        "selection_gap_daily_pp": round((sys_total - daily) * 100, 2),
        "raw_gap_pp": round((sys_total - spy_total) * 100, 2),
    }


def v2_decomposition(cfg, res, spy="SPY"):
    df = _bars(cfg, spy)
    closes = {r["datetime"].strftime("%Y-%m-%d"): float(r["close"])
              for _, r in df.iterrows()}
    eq = res["equity_curve"]
    dates = [e["date"] for e in eq]
    equity = [e["equity"] for e in eq]
    cash = [e["cash"] for e in eq]

    half = len(dates) // 2
    out = {"full_window": _decompose(cfg, dates, equity, cash, closes, spy)}
    out["first_half"] = _decompose(cfg, dates[:half], equity[:half], cash[:half],
                                   closes, spy)
    out["second_half"] = _decompose(cfg, dates[half:], equity[half:], cash[half:],
                                    closes, spy)
    # 2025 only (the strongest bull leg) vs 2024
    for label, year in (("calendar_2024", "2024"), ("calendar_2025", "2025")):
        idx = [i for i, d in enumerate(dates) if d.startswith(year)]
        if len(idx) > 2:
            out[label] = _decompose(cfg, [dates[i] for i in idx],
                                    [equity[i] for i in idx],
                                    [cash[i] for i in idx], closes, spy)
    out["robustness"] = {
        "selection_gap_by_window_pp": {
            k: v["selection_gap_daily_pp"] for k, v in out.items()
            if isinstance(v, dict) and "selection_gap_daily_pp" in v},
        "note": "if every window shows selection ~= 0 and a large raw gap, the "
                "Phase-4 conclusion is not a period artefact",
    }
    return out


# ---------------------------------------------------------------------------
# V3 — counterfactual sizing (arithmetic on existing records only)
# ---------------------------------------------------------------------------
def v3_sizing_counterfactual(cfg, records, res):
    eq_curve = res["equity_curve"]
    prev = {}
    for i, e in enumerate(eq_curve):
        j = i - 1 if i else 0
        prev[e["date"]] = (eq_curve[j]["equity"], eq_curve[j]["cash"])

    variants = {}
    for label, override in (("as_recorded", None),
                            ("eff_doubled_to_0.5", 0.5),
                            ("regime_mult_1.0_quality_kept", "regime1.0"),
                            ("full_1.0", 1.0)):
        sizeable = 0
        total = 0
        for rec in records:
            ev_by = {e["ticker"]: e for e in rec["setup"]["evaluations"]}
            eq_p, cash_p = prev.get(rec["as_of"], (None, None))
            size_mult = (rec["regime"]["output"] or {}).get("position_size_mult")
            if eq_p is None or not size_mult:
                continue
            for ev in rec["setup"]["evaluations"]:
                if not ev.get("valid"):
                    continue
                price, atr = ev.get("signal_close"), ev.get("atr")
                quality = ev.get("setup_quality_mult") or 0.0
                if not price or not atr or quality <= 0:
                    continue
                total += 1
                if override is None:
                    eff = size_mult * quality
                elif override == "regime1.0":
                    eff = 1.0 * quality
                elif override == 0.5:
                    eff = min(size_mult * quality * 2.0, 0.5)
                else:
                    eff = float(override)
                risk_amount = eq_p * cfg.risk_per_trade * eff
                stop_distance = atr * cfg.stop_atr_mult
                raw = int(risk_amount // stop_distance) if stop_distance > 0 else 0
                by_value = int((eq_p * cfg.max_position_pct) // price)
                by_cash = int(cash_p // price)
                if min(raw, by_value, by_cash) > 0:
                    sizeable += 1
        variants[label] = {
            "valid_setups": total, "sizeable": sizeable,
            "sizeable_pct": round(100.0 * sizeable / total, 2) if total else None,
        }
    base = variants["as_recorded"]["sizeable"]
    for k, v in variants.items():
        v["delta_vs_recorded"] = v["sizeable"] - base
    return {
        "variants": variants,
        "method": ("pure arithmetic on the captured records using the production "
                   "sizing formula; NO backtest was re-run and NO parameter was "
                   "changed — this only bounds which lever could matter"),
        "reading": ("if raising the effective multiplier barely increases the "
                    "sizeable count, the binding constraint is the ACCOUNT SIZE / "
                    "stop distance, not the multiplier"),
    }


# ---------------------------------------------------------------------------
# V4 — entry-gap stability across sub-periods
# ---------------------------------------------------------------------------
def v4_gap_stability(trades):
    def cohort(rows):
        out = {}
        for label, lo, hi in (("<0%", -9, 0.0), ("0-0.5%", 0.0, 0.005),
                              ("0.5-1%", 0.005, 0.01), ("1-2%", 0.01, 0.02)):
            g = [r for r in rows if lo <= (r.get("next_open_gap_pct") if
                                           r.get("next_open_gap_pct") is not None
                                           else -9) < hi]
            if not g:
                out[label] = {"n": 0}
                continue
            rs = [r["r_multiple"] for r in g if r.get("r_multiple") is not None]
            out[label] = {"n": len(g),
                          "avg_r": round(stats.mean(rs), 4) if rs else None,
                          "win_rate_pct": round(100.0 * sum(1 for x in rs if x > 0)
                                                / len(rs), 2) if rs else None}
        return out

    by_half = {"first_half": [], "second_half": []}
    srt = sorted(trades, key=lambda t: t["entry_date"])
    half = len(srt) // 2
    for i, t in enumerate(srt):
        by_half["first_half" if i < half else "second_half"].append(t)
    return {
        "full_window": cohort(trades),
        "by_period": {k: cohort(v) for k, v in by_half.items()},
        "by_year": {y: cohort([t for t in trades if t["entry_date"].startswith(y)])
                    for y in ("2024", "2025")},
    }


def main() -> int:
    cfg = ProductionConfig()
    source = build_cached_source(cfg)
    print(f"=== Phase-4 verification: re-running the unchanged backtest "
          f"({START}..{END}) to capture records ===")
    res, records = _run_capturing(cfg, source)
    trades = res["trade_log"]
    print(f"    {len(records)} records / {len(trades)} trades")

    out = {
        "generated": "2026-10-01",
        "purpose": "independent verification of the Phase-4 diagnostic claims",
        "V1_risk_engine_agreement": v1_risk_engine_agreement(cfg, records, res),
        "V2_decomposition": v2_decomposition(cfg, res),
        "V3_sizing_counterfactual": v3_sizing_counterfactual(cfg, records, res),
        "V4_entry_gap_stability": v4_gap_stability(trades),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)

    v1 = out["V1_risk_engine_agreement"]
    print(f"\n=== V1 real-engine agreement ===")
    print(f"  attempts={v1['sizing_attempts_replayed']} "
          f"agree={v1['allow_matches_record']} "
          f"({v1['agreement_pct']}%) mismatch={v1['allow_mismatches']}")
    print(f"  reject reasons: {v1['reject_reasons_from_real_engine']}")
    print(f"  verdict: {v1['verdict']}")

    print(f"\n=== V2 decomposition ===")
    for k, v in out["V2_decomposition"].items():
        if isinstance(v, dict) and "system_return_pct" in v:
            print(f"  {k:16s} sys={v['system_return_pct']:6}% "
                  f"spy={v['spy_return_pct']:6}% exp={v['avg_exposure_pct']:5}% "
                  f"daily-matched={v['method_daily_matched_pct']:6}% "
                  f"selection={v['selection_gap_daily_pp']:6}pp "
                  f"raw_gap={v['raw_gap_pp']:7}pp")
    print(f"  selection by window: "
          f"{out['V2_decomposition']['robustness']['selection_gap_by_window_pp']}")

    print(f"\n=== V3 sizing counterfactual ===")
    for k, v in out["V3_sizing_counterfactual"]["variants"].items():
        print(f"  {k:30s} sizeable {v['sizeable']:4d}/{v['valid_setups']} "
              f"({v['sizeable_pct']}%) delta={v['delta_vs_recorded']:+d}")

    print(f"\n=== V4 entry-gap stability ===")
    v4 = out["V4_entry_gap_stability"]
    for scope in ("by_period", "by_year"):
        for name, cohorts in v4[scope].items():
            cells = " | ".join(
                f"{c}:n={d.get('n', 0)},avgR={d.get('avg_r')}"
                for c, d in cohorts.items())
            print(f"  {scope[3:]}:{name:12s} {cells}")

    print(f"\nreport -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
