"""
research/r2_entry_cohorts.py — R2: do entry/setup cohorts separate edge from drag?

R2 was BLOCKED until R7 landed, because `components` was permanently `{}` and
`extension_from_pivot_pct` was null on all 168 trades. R7 supplies
`score_components` (parsed from the frozen engine's own `entry_reason`) on
168/168 trades, so cohort attribution is finally possible.

The question
------------
Setup v1 scores a pullback as a weighted SUM of seven components and accepts
anything >= 0.5. The freeze document describes the pullback as a conjunction of
four conditions. R7's component counts show the two descriptions disagree badly:
`reversal` fires in 7.1 % of trades while the three trend-structure components
fire in >94 %. So the score is, in practice, mostly a trend-structure filter.

This script asks whether that matters for the OUTCOME: do trades that fired a
given component earn a different realised R than trades that did not?

Discipline
----------
The central risk in a cohort study is finding a split that looks good by
accident. Three guards, all applied below:

1. **Pre-registered cohorts.** Only the seven engine components, the score
   bands, and the entry-quality fields R7 added are tested. No cohort is
   invented after seeing the result.
2. **Multiple-comparison control.** Every cohort test reports n, and the
   Benjamini-Hochberg adjusted q-value across the whole family. A cohort with
   n < 20 is reported but never called a finding.
3. **Sub-period stability.** A cohort that only separates in 2024 is reported as
   unstable, matching the C3 criterion used in S1-S3.

Also included: a per-trade table so any cohort can be re-derived independently,
and a placebo/permutation check on the strongest apparent effect.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics as stats
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from research import harness as H

COMPONENTS = ("px>50SMA", "px>200SMA", "50>200", "nearEMA", "vol_contract",
              "RS_strong", "reversal")

SUBPERIODS = {
    "2024_full": ("2024-01-01", "2024-12-31"),
    "2025_jan_mar": ("2025-01-01", "2025-03-31"),
    "2025_apr_jul": ("2025-04-01", "2025-07-31"),
}

MIN_N = 20          # below this a cohort is reported, never called a finding
MIN_AVG_R_GAP = 0.10   # a cohort must separate by at least this much avg R


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.mean(xs), 4) if xs else None


def _welch(a: list, b: list) -> float:
    """|mean difference| normalised by pooled SE. A descriptive effect size, not
    a hypothesis test — the permutation check below is the guard."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = stats.variance(a), stats.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return abs(stats.mean(a) - stats.mean(b)) / se if se > 0 else 0.0


import math  # noqa: E402  (used by _welch)


def _bh_qvalues(pvals: dict) -> dict:
    """Benjamini-Hochberg adjusted q-values (Benjamini-Hochberg 1995).

    Cohorts whose permutation test is undefined (too few members to relabel)
    return NaN. They are EXCLUDED from the family count `m` rather than being
    sorted into it — a NaN would otherwise corrupt the monotone step-up that
    makes BH valid — and their q-value is reported as None.
    """
    valid = {k: v for k, v in pvals.items() if v == v}      # drop NaN
    items = sorted(valid.items(), key=lambda kv: kv[1])
    m = len(items)
    out = {k: None for k in pvals}
    if not m:
        return out
    prev = 1.0
    for rank, (k, p) in enumerate(reversed(items), start=1):
        i = m - rank + 1
        q = min(prev, p * m / i)
        out[k] = round(q, 4)
        prev = q
    return out


def _permutation_p(trades: list, flag_key, n_iter: int = 5000,
                   seed: int = 20261002) -> float:
    """Two-sided permutation p-value for the mean-R difference between the
    flagged and un-flagged subsets, under random relabelling."""
    import random
    rs = [t["r"] for t in trades]
    groups = [t[flag_key] for t in trades]
    n1 = sum(1 for g in groups if g)
    if n1 < 2 or n1 > len(rs) - 2:
        return float("nan")
    obs = abs(stats.mean([r for r, g in zip(rs, groups) if g])
              - stats.mean([r for r, g in zip(rs, groups) if not g]))
    rng = random.Random(seed)
    pool = list(rs)
    hits = 0
    for _ in range(n_iter):
        rng.shuffle(pool)
        d = abs(stats.mean(pool[:n1]) - stats.mean(pool[n1:]))
        if d >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_iter + 1)


def _cohort_table(trades: list, name: str, pred, note: str) -> dict:
    a = [t["r"] for t in trades if pred(t)]
    b = [t["r"] for t in trades if not pred(t)]
    sa = [t for t in trades if pred(t)]
    sb = [t for t in trades if not pred(t)]
    out = {
        "cohort": name, "note": note,
        "n_flagged": len(a), "n_unflagged": len(b),
        "avg_r_flagged": _mean(a), "avg_r_unflagged": _mean(b),
        "delta_avg_r": (round(stats.mean(a) - stats.mean(b), 4)
                        if a and b else None),
        "median_r_flagged": (round(stats.median(a), 4) if a else None),
        "pf_flagged": (round(sum(x for x in a if x > 0)
                             / abs(sum(x for x in a if x <= 0)), 3)
                       if a and any(x <= 0 for x in a) else None),
        "win_rate_flagged_pct": (round(100 * sum(1 for x in a if x > 0) / len(a), 1)
                                if a else None),
        "avg_hold_flagged": _mean([t["hold"] for t in sa]),
        "effect_size": round(_welch(a, b), 4) if a and b else None,
        "sum_r_flagged": round(sum(a), 3) if a else 0.0,
        "sufficient_n": len(a) >= MIN_N,
        "subperiods": {},
    }
    for sp, (lo, hi) in SUBPERIODS.items():
        sa_sp = [t["r"] for t in sa if lo <= t["date"] <= hi]
        sb_sp = [t["r"] for t in sb if lo <= t["date"] <= hi]
        out["subperiods"][sp] = {
            "n_flagged": len(sa_sp), "n_unflagged": len(sb_sp),
            "avg_r_flagged": _mean(sa_sp), "avg_r_unflagged": _mean(sb_sp),
            "delta": (round(stats.mean(sa_sp) - stats.mean(sb_sp), 4)
                      if sa_sp and sb_sp else None),
        }
    deltas = [v["delta"] for v in out["subperiods"].values() if v["delta"] is not None]
    out["stable_across_subperiods"] = (
        all(d > 0 for d in deltas) or all(d < 0 for d in deltas)) if len(deltas) >= 2 else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"r2_entry_cohorts_pitcorrected_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    base = H.load_config()
    res = H.run_backtest(base)["result"]
    raw = res["trade_log"]

    trades = []
    for t in raw:
        sc = t.get("score_components") or {}
        fired = sc.get("fired") or []
        row = {
            "ticker": t["ticker"], "date": t["entry_date"],
            "exit_date": t["exit_date"], "r": t.get("r_multiple"),
            "setup_score": t.get("setup_score"),
            "setup_type": t.get("setup_type"),
            "entry_regime": t.get("entry_regime"),
            "exit_reason": t.get("exit_reason"),
            "hold": t.get("holding_days"),
            "gap": t.get("next_open_gap_pct"),
            "atr_pct": t.get("entry_atr_pct_of_price"),
            "signal_close": t.get("signal_close"),
            "quality_mult": t.get("entry_setup_quality_mult"),
            "size_mult": t.get("entry_regime_size_mult"),
            "components_available": sc.get("available"),
            "n_components": len(fired),
            "fired": fired,
        }
        for c in COMPONENTS:
            row[f"c_{c}"] = c in fired
        trades.append(row)

    cohorts: list = []
    # --- family 1: each engine component present vs absent -----------------
    for c in COMPONENTS:
        cohorts.append(_cohort_table(
            trades, f"component:{c}", (lambda cc: (lambda t: t[f"c_{cc}"]))(c),
            "parsed from the frozen engine's own entry_reason"))
    # --- family 2: how many components fired -------------------------------
    cohorts.append(_cohort_table(
        trades, "n_components>=4", lambda t: t["n_components"] >= 4,
        "4+ of 7 components fired"))
    cohorts.append(_cohort_table(
        trades, "n_components<=3", lambda t: t["n_components"] <= 3,
        "3 or fewer of 7 components fired"))
    cohorts.append(_cohort_table(
        trades, "n_components<=2", lambda t: t["n_components"] <= 2,
        "2 or fewer of 7 — the bare trend-structure floor"))
    # --- family 3: the conjunction the freeze document describes ----------
    # the four conditions it names: nearEMA, vol_contract, reversal, RS_strong
    four = ("nearEMA", "vol_contract", "reversal", "RS_strong")
    cohorts.append(_cohort_table(
        trades, "conjunction:nearEMA+vol+RS", 
        lambda t: t["c_nearEMA"] and t["c_vol_contract"] and t["c_RS_strong"],
        "the three of the four freeze-named conditions that are observable"))
    cohorts.append(_cohort_table(
        trades, "conjunction:all_four",
        lambda t: all(t[f"c_{x}"] for x in four),
        "all four freeze-named conditions fired (reversal included)"))
    # --- family 4: setup score bands --------------------------------------
    cohorts.append(_cohort_table(
        trades, "score>=0.75", lambda t: (t["setup_score"] or 0) >= 0.75,
        "top score band (quality_mult 1.0)"))
    cohorts.append(_cohort_table(
        trades, "score<0.75", lambda t: (t["setup_score"] or 0) < 0.75,
        "lower score bands"))
    # --- family 5: entry-quality fields R7 added --------------------------
    cohorts.append(_cohort_table(
        trades, "gap>0", lambda t: (t["gap"] or 0) > 0, "opened above signal close"))
    cohorts.append(_cohort_table(
        trades, "atr_pct>=3", lambda t: (t["atr_pct"] or 0) >= 3.0,
        "ATR >= 3 % of price at entry"))
    cohorts.append(_cohort_table(
        trades, "atr_pct<3", lambda t: (t["atr_pct"] or 0) < 3.0,
        "ATR < 3 % of price at entry"))
    # --- family 6: regime x setup interaction ----------------------------
    for reg in ("BULL", "SIDEWAYS", "BEAR"):
        cohorts.append(_cohort_table(
            trades, f"regime:{reg}", (lambda rr: (lambda t: t["entry_regime"] == rr))(reg),
            f"entered in {reg}"))
    cohorts.append(_cohort_table(
        trades, "regime:BULL+score>=0.75",
        lambda t: t["entry_regime"] == "BULL" and (t["setup_score"] or 0) >= 0.75,
        "regime x setup interaction"))

    # multiple-comparison control across the whole family
    pvals = {}
    for ch in cohorts:
        key = ch["cohort"]
        p = _permutation_p([dict(t, flag=_pred_flag(ch, t)) for t in trades],
                           "flag")
        ch["permutation_p"] = (None if p != p else round(p, 5))
        pvals[key] = ch["permutation_p"] if ch["permutation_p"] is not None else float("nan")
    qs = _bh_qvalues(pvals)
    for ch in cohorts:
        ch["q_value_bh"] = qs[ch["cohort"]]
        ch["permutation_tested"] = ch["permutation_p"] is not None
        ch["significant_at_10pct"] = (ch["q_value_bh"] is not None
                                      and ch["q_value_bh"] <= 0.10
                                      and ch["sufficient_n"])

    # strongest effects, with the permutation guard
    ranked = sorted([c for c in cohorts if c["avg_r_flagged"] is not None
                     and c["avg_r_unflagged"] is not None],
                    key=lambda c: -abs(c["delta_avg_r"] or 0))
    findings = [c for c in ranked
                if c["sufficient_n"] and abs(c["delta_avg_r"] or 0) >= MIN_AVG_R_GAP
                and c["significant_at_10pct"]]

    payload = {
        "generated": datetime.date.today().isoformat(),
        "deliverable": "R2 — entry/setup cohort attribution on the PIT-correct "
                       "baseline (unblocked by R7)",
        "n_trades": len(trades),
        "pre_registration": {
            "cohorts_tested": [c["cohort"] for c in cohorts],
            "note": "cohorts are defined from the engine's own components and "
                    "R7's entry-quality fields BEFORE looking at outcomes; no "
                    "cohort was added after seeing a result",
            "min_n": MIN_N,
            "min_avg_r_gap": MIN_AVG_R_GAP,
            "multiple_comparison": "Benjamini-Hochberg across all cohorts with a "
                                   "defined permutation test; a cohort is a "
                                   "finding only at q <= 0.10 with n >= 20. "
                                   "Cohorts too small to relabel (n < 2) are "
                                   "excluded from the family count and their q "
                                   "is reported as null.",
        },
        "component_frequency": {
            c: {"n": sum(1 for t in trades if t[f"c_{c}"]),
                "pct": round(100 * sum(1 for t in trades if t[f"c_{c}"])
                             / len(trades), 1)}
            for c in COMPONENTS},
        "n_components_distribution": {
            str(k): sum(1 for t in trades if t["n_components"] == k)
            for k in range(0, len(COMPONENTS) + 1)},
        "score_distribution": {
            str(s): sum(1 for t in trades if (t["setup_score"] or 0) == s)
            for s in sorted({t["setup_score"] for t in trades})},
        "cohorts": cohorts,
        "ranked_by_effect": [
            {"cohort": c["cohort"], "delta_avg_r": c["delta_avg_r"],
             "n_flagged": c["n_flagged"], "q_value_bh": c["q_value_bh"],
             "permutation_p": c["permutation_p"]} for c in ranked],
        "surviving_findings": [
            {"cohort": c["cohort"], "delta_avg_r": c["delta_avg_r"],
             "n_flagged": c["n_flagged"], "avg_r_flagged": c["avg_r_flagged"],
             "avg_r_unflagged": c["avg_r_unflagged"],
             "q_value_bh": c["q_value_bh"],
             "stable_across_subperiods": c["stable_across_subperiods"]}
            for c in findings],
        "caveats": [
            "Cohort attribution is OBSERVATIONAL. A cohort that separates here "
            "is a hypothesis, not a rule: acting on it would require a forward "
            "test with a pre-registered acceptance criterion, exactly as S1-S3 "
            "were run.",
            "Cohorts overlap heavily (components are summed by one engine, and "
            "the three trend-structure components co-fire in >94 % of trades), "
            "so cohort comparisons are not independent.",
            "168 trades across 19 months; the 2025 sub-periods contain few "
            "trades and the baseline is known to be a 2024 effect.",
        ],
        "per_trade": trades,
        "git": H.git_commit(),
    }
    H.save_json(args.out, payload)

    print("=== R2 — entry/setup cohort attribution ===")
    print(f"  trades: {len(trades)}   cohorts tested: {len(cohorts)}")
    print(f"\n  component frequency:")
    for c in COMPONENTS:
        f = payload["component_frequency"][c]
        print(f"    {c:<16} {f['n']:>4}  ({f['pct']}%)")
    print(f"\n  n_components distribution: {payload['n_components_distribution']}")
    print(f"\n  {'cohort':<32} {'n':>4} {'avgR_flag':>10} {'avgR_un':>8} "
          f"{'delta':>7} {'q':>7} {'stable':>8}")
    for c in ranked:
        print(f"  {c['cohort']:<32} {c['n_flagged']:>4} "
              f"{str(c['avg_r_flagged']):>10} {str(c['avg_r_unflagged']):>8} "
              f"{str(c['delta_avg_r']):>7} {str(c['q_value_bh']):>7} "
              f"{str(c['stable_across_subperiods']):>8}")
    print(f"\n  surviving findings (q<=0.10, n>=20, |delta|>=0.10): "
          f"{len(findings)}")
    for f in findings:
        print(f"    - {f['cohort']}: delta={f['delta_avg_r']} "
              f"q={f['q_value_bh']} stable={f['stable_across_subperiods']}")
    print(f"\nreport -> {args.out}")
    return 0


def _pred_flag(cohort: dict, t: dict) -> bool:
    """Re-derive a cohort row's membership for the permutation check."""
    name = cohort["cohort"]
    if name.startswith("component:"):
        return t[f"c_{name.split(':', 1)[1]}"]
    if name == "n_components>=4":
        return t["n_components"] >= 4
    if name == "n_components<=3":
        return t["n_components"] <= 3
    if name == "n_components<=2":
        return t["n_components"] <= 2
    if name == "conjunction:nearEMA+vol+RS":
        return t["c_nearEMA"] and t["c_vol_contract"] and t["c_RS_strong"]
    if name == "conjunction:all_four":
        return all(t[f"c_{x}"] for x in
                   ("nearEMA", "vol_contract", "reversal", "RS_strong"))
    if name == "score>=0.75":
        return (t["setup_score"] or 0) >= 0.75
    if name == "score<0.75":
        return (t["setup_score"] or 0) < 0.75
    if name == "gap>0":
        return (t["gap"] or 0) > 0
    if name == "atr_pct>=3":
        return (t["atr_pct"] or 0) >= 3.0
    if name == "atr_pct<3":
        return (t["atr_pct"] or 0) < 3.0
    if name.startswith("regime:"):
        rest = name.split(":", 1)[1]
        if "+" in rest:
            reg, band = rest.split("+")
            return (t["entry_regime"] == reg
                    and (t["setup_score"] or 0) >= float(band.split(">=")[1]))
        return t["entry_regime"] == rest
    return False


if __name__ == "__main__":
    sys.exit(main())
