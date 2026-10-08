"""
research/verify_r7_equivalence.py — R7 acceptance gate.

Proves that the R7 observability work changed what the record SAYS and nothing
about what the system DOES, by diffing two full backtest runs of the SAME window:

    pre  = the run captured before any R7 code existed  (/tmp/r7_pre_baseline.json)
    post = the run with all R7 fields live              (/tmp/r7_post_baseline.json)

The comparison is field-level, not summary-level, because a summary can hide a
compensating pair of errors. For every trade and every session it checks the
decision-bearing fields byte-for-byte, and it separately enumerates the fields
R7 ADDED (those must be the only difference).

Usage:
    python research/verify_r7_equivalence.py \
        --pre /tmp/r7_pre_baseline.json --post /tmp/r7_post_baseline.json \
        --out reports/r7_equivalence_2026-10-02.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Fields R7 is allowed to ADD. Anything outside this list that differs between
# the two runs is a behaviour change and fails the gate.
ADDED_FIELDS = {
    "holding_days", "signal_close", "entry_open", "next_open_gap_pct",
    "max_entry_gap_pct", "entry_atr", "entry_atr_pct_of_price", "prior_high20",
    "extension_from_pivot_pct", "extension_filter_status", "entry_phase_equity",
    "entry_phase_cash_after_fill", "entry_regime_size_mult",
    "entry_setup_quality_mult", "entry_eff_size_mult", "risk_budget_usd",
    "stop_distance_usd", "r_unit_usd", "shares_requested_before_flooring",
    "shares_final", "position_value", "score_components", "reasoning", "gate",
    "veto_flags",
}

# The exact equality set the spec requires, checked first and reported first.
CRITICAL_TRADE_FIELDS = ("ticker", "shares", "entry_price", "exit_price",
                         "exit_date", "exit_reason", "exit_fill_model",
                         "net_pnl", "gross_pnl", "return_pct", "r_multiple",
                         "stop_price", "target_price", "entry_date",
                         "entry_regime")
CRITICAL_CURVE_FIELDS = ("date", "equity", "cash", "n_positions")


def strip_added(obj):
    """Recursively drop R7-added keys so the remainder is directly comparable."""
    if isinstance(obj, dict):
        return {k: strip_added(v) for k, v in obj.items()
                if k not in ADDED_FIELDS}
    if isinstance(obj, list):
        return [strip_added(v) for v in obj]
    return obj


def diff_dicts(a, b, path=""):
    """Yield (path, pre, post) for every leaf that differs."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in ADDED_FIELDS:
                continue
            if k not in a or k not in b:
                yield (f"{path}.{k}", a.get(k, "<absent>"), b.get(k, "<absent>"))
            else:
                yield from diff_dicts(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield (f"{path}[len]", len(a), len(b))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                yield from diff_dicts(x, y, f"{path}[{i}]")
    elif a != b:
        yield (path, a, b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", required=True)
    ap.add_argument("--post", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pre = json.load(open(args.pre, encoding="utf-8"))
    post = json.load(open(args.post, encoding="utf-8"))

    out: dict = {"pre": args.pre, "post": args.post}
    failures: list[str] = []

    # ---- 1. summary (the §1 headline criterion) --------------------------
    s_pre, s_post = pre["summary"], post["summary"]
    summary_diff = {k: (s_pre.get(k), s_post.get(k))
                    for k in sorted(set(s_pre) | set(s_post))
                    if s_pre.get(k) != s_post.get(k)}
    out["summary_identical"] = not summary_diff
    out["summary_diff"] = summary_diff
    out["summary_pre"] = s_pre
    out["summary_post"] = s_post
    if summary_diff:
        failures.append(f"summary differs: {summary_diff}")

    # ---- 2. the exact trade-level fields the spec names ------------------
    t_pre, t_post = pre["trade_log"], post["trade_log"]
    out["n_trades"] = {"pre": len(t_pre), "post": len(t_post)}
    if len(t_pre) != len(t_post):
        failures.append(f"trade count {len(t_pre)} -> {len(t_post)}")
    mism = []
    for i, (a, b) in enumerate(zip(t_pre, t_post)):
        for k in CRITICAL_TRADE_FIELDS:
            if a.get(k) != b.get(k):
                mism.append({"trade": i, "ticker": a.get("ticker"),
                             "field": k, "pre": a.get(k), "post": b.get(k)})
    out["critical_trade_field_mismatches"] = mism
    out["critical_trade_fields_checked"] = list(CRITICAL_TRADE_FIELDS)
    if mism:
        failures.append(f"{len(mism)} critical trade-field mismatches")

    # ---- 3. whole trade_log / curve / skipped, R7 fields removed ---------
    for label, key in (("trade_log", "trade_log"),
                       ("equity_curve", "equity_curve"),
                       ("skipped", "skipped"),
                       ("screens", "screens"),
                       ("regime_log", "regime_log")):
        d = list(diff_dicts(strip_added(pre.get(key, [])),
                            strip_added(post.get(key, []))))
        out[f"{label}_identical_ignoring_added_fields"] = not d
        out[f"{label}_diffs"] = d[:20]
        if d:
            failures.append(f"{label} differs in {len(d)} place(s) "
                            f"(e.g. {d[0][0]}: {d[0][1]!r} -> {d[0][2]!r})")

    # ---- 4. what R7 actually added --------------------------------------
    def added_keys(a, b, prefix=""):
        got = set()
        if isinstance(a, dict) and isinstance(b, dict):
            for k in b:
                if k in ADDED_FIELDS and k not in a:
                    got.add(f"{prefix}.{k}")
                if k in a and k in b:
                    got |= added_keys(a[k], b[k], f"{prefix}.{k}")
        elif isinstance(a, list) and isinstance(b, list):
            for x, y in zip(a, b):
                got |= added_keys(x, y, f"{prefix}[]")
        return got

    added = sorted(added_keys(pre.get("trade_log", []), post.get("trade_log", [])))
    out["fields_added_to_trade_log"] = added
    hd = [t.get("holding_days") for t in t_post]
    out["holding_days_populated"] = {
        "n_trades": len(hd),
        "n_null": sum(1 for x in hd if x is None),
        "min": min((x for x in hd if x is not None), default=None),
        "max": max((x for x in hd if x is not None), default=None),
        "mean": (round(sum(x for x in hd if x is not None)
                       / max(1, sum(1 for x in hd if x is not None)), 2)),
    }
    sc = [t.get("score_components") for t in t_post]
    out["score_components_populated"] = {
        "n_with_components": sum(1 for c in sc if c and c.get("available")),
        "n_components_empty_dict_still": sum(
            1 for t in t_post if t.get("components") == {}),
    }
    veto = [len(r.get("veto_flags") or []) for r in post.get("regime_log", [])]
    out["regime_log_veto_flags"] = {
        "n_sessions": len(veto),
        "n_with_veto": sum(1 for v in veto if v),
    }

    out["PASS"] = not failures
    out["failures"] = failures
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)

    print("=== R7 behavioural equivalence ===")
    print(f"  summary identical                     : {out['summary_identical']}")
    print(f"  trades                                : "
          f"{out['n_trades']['pre']} -> {out['n_trades']['post']}")
    print(f"  critical trade fields checked         : "
          f"{len(CRITICAL_TRADE_FIELDS)} x {out['n_trades']['post']} trades")
    print(f"  critical trade-field mismatches       : "
          f"{len(out['critical_trade_field_mismatches'])}")
    for label in ("trade_log", "equity_curve", "skipped", "screens", "regime_log"):
        print(f"  {label:<38}: "
              f"{out[f'{label}_identical_ignoring_added_fields']}")
    print(f"  holding_days populated                : "
          f"{out['holding_days_populated']['n_trades'] - out['holding_days_populated']['n_null']}"
          f"/{out['holding_days_populated']['n_trades']} "
          f"(mean {out['holding_days_populated']['mean']}d)")
    print(f"  score_components available            : "
          f"{out['score_components_populated']['n_with_components']}"
          f"/{out['n_trades']['post']}")
    print(f"  trade_log `components` still {{}}      : "
          f"{out['score_components_populated']['n_components_empty_dict_still']}"
          f"  <- frozen Setup v1 contract unchanged")
    print(f"  regime_log veto flags captured        : "
          f"{out['regime_log_veto_flags']['n_with_veto']}"
          f"/{out['regime_log_veto_flags']['n_sessions']} sessions")
    print(f"\n  VERDICT: {'PASS — strategy-neutral' if out['PASS'] else 'FAIL'}")
    for f_ in failures:
        print(f"    - {f_}")
    print(f"\nreport -> {args.out}")
    return 0 if out["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
