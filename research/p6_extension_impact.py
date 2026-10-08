"""
research/p6_extension_impact.py — would the INERT extension filter have mattered?

P6 is a contract-accuracy finding: `max_extension_from_pivot_pct = 0.03` is
documented as an active Setup v1 clause, but it can never fire, because
`prior_high20` is produced only on the breakout path and Setup v1 is
Pullback-Only. The freeze audit has flagged it INERT since 2026-10-01.

The open question is whether that is a documentation nit or a material omission.
If wiring the filter would have rejected nothing, the clause is cosmetic and the
cheapest resolution is to record it as inert. If it would have rejected a
meaningful number of trades, the contract gap hides a real risk control that the
system believes it has and does not.

This script answers that WITHOUT touching frozen code, by replaying the filter
counterfactually over the trades that were actually taken:

    extension = (next_open - prior_high20) / prior_high20

`prior_high20` is reconstructed exactly as `breakout_setup` defines it —
`high.shift(1).rolling(20).max()` evaluated on the signal-date frame — so the
number is the one the frozen code would have seen, not an approximation. The
filter is then applied to the recorded fills to count what it would have blocked,
and those blocked trades are scored to show what would have been lost.

PIT safety
----------
`prior_high20` uses only bars at or before the signal date (`.shift(1)` removes
the signal bar itself), and the entry price is the next session's open, exactly
as the live path uses. No future bar enters the reconstruction.

This is a MEASUREMENT. It does not wire the filter, does not change any
parameter, and does not alter any recorded trade.
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

THRESHOLD = 0.03          # frozen max_extension_from_pivot_pct
_BARS: dict = {}


def _bars(cfg, ticker: str):
    if ticker not in _BARS:
        _BARS[ticker] = H._bars(cfg, ticker)
    return _BARS[ticker]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        _REPO_ROOT, "reports",
        f"p6_extension_impact_pitcorrected_"
        f"{datetime.date.today().isoformat()}.json"))
    args = ap.parse_args()

    cfg = H.load_config()
    assert abs(cfg.max_extension_from_pivot_pct - THRESHOLD) < 1e-12
    res = H.run_backtest(cfg)["result"]
    trades = res["trade_log"]

    rows, unavailable = [], 0
    for t in trades:
        sig, entry_date = t.get("signal_date"), t.get("entry_date")
        ticker = t["ticker"]
        if not sig or not entry_date:
            unavailable += 1
            continue
        df = _bars(cfg, ticker)
        if df is None:
            unavailable += 1
            continue
        # exact reconstruction of breakout_setup's prior_high20, PIT-safe:
        # evaluated on the signal-date frame, shift(1) excludes the signal bar
        sig_rows = df[df["datetime"] <= sig]
        if len(sig_rows) < 21:
            unavailable += 1
            continue
        ph20 = sig_rows["high"].shift(1).rolling(20).max().iloc[-1]
        if ph20 is None or ph20 != ph20 or float(ph20) <= 0:
            unavailable += 1
            continue
        entry_rows = df[df["datetime"] == entry_date]
        if not len(entry_rows):
            unavailable += 1
            continue
        entry_open = float(entry_rows["open"].iloc[-1])
        ext = (entry_open - float(ph20)) / float(ph20)
        rows.append({
            "ticker": ticker, "signal_date": sig, "entry_date": entry_date,
            "entry_open": round(entry_open, 4),
            "prior_high20": round(float(ph20), 4),
            "extension_from_pivot_pct": round(ext, 6),
            "would_block": bool(ext > THRESHOLD),
            "r_multiple": t.get("r_multiple"),
            "net_pnl": t.get("net_pnl"),
            "exit_reason": t.get("exit_reason"),
        })

    blocked = [r for r in rows if r["would_block"]]
    kept = [r for r in rows if not r["would_block"]]
    exts = [r["extension_from_pivot_pct"] for r in rows]

    def _m(xs):
        return round(stats.mean(xs), 4) if xs else None

    # what would the blocked trades have cost or saved?
    b_r = [r["r_multiple"] for r in blocked if r["r_multiple"] is not None]
    k_r = [r["r_multiple"] for r in kept if r["r_multiple"] is not None]
    b_pnl = sum(r["net_pnl"] or 0 for r in blocked)
    k_pnl = sum(r["net_pnl"] or 0 for r in kept)

    # near-miss: within 1pp of the threshold — how sharp is the cut?
    near = [r for r in rows
            if THRESHOLD <= r["extension_from_pivot_pct"] <= THRESHOLD + 0.01]

    payload = {
        "generated": datetime.date.today().isoformat(),
        "deliverable": "P6 materiality assessment — would the inert "
                       "max_extension_from_pivot_pct filter have changed anything?",
        "threshold": THRESHOLD,
        "method": {
            "prior_high20": "reconstructed EXACTLY as src/agents/setup_agent.py"
                            "::breakout_setup defines it — high.shift(1)"
                            ".rolling(20).max() on the signal-date frame",
            "pit_safety": "shift(1) excludes the signal bar; only bars <= "
                          "signal_date and the entry session's open are used",
            "applied_to": "the trades the system actually took, replayed "
                          "counterfactually",
            "no_production_change": True,
        },
        "n_trades_total": len(trades),
        "n_measured": len(rows),
        "n_unavailable": unavailable,
        "unavailable_reason": ("no signal_date / no cached bars / fewer than 21 "
                               "bars before the signal / missing entry bar"),
        "would_block_count": len(blocked),
        "would_block_pct": (round(100 * len(blocked) / len(rows), 2)
                            if rows else None),
        "extension_stats": {
            "min": round(min(exts), 6) if exts else None,
            "p10": round(sorted(exts)[len(exts) // 10], 6) if exts else None,
            "median": round(stats.median(exts), 6) if exts else None,
            "p90": round(sorted(exts)[9 * len(exts) // 10], 6) if exts else None,
            "max": round(max(exts), 6) if exts else None,
            "mean": _m(exts),
        },
        "near_miss_within_1pp": {
            "n": len(near),
            "note": "how sharp the cut is; a large near-miss population would "
                    "mean the threshold sits inside the distribution",
        },
        "blocked_vs_kept": {
            "blocked_avg_r": _m(b_r), "kept_avg_r": _m(k_r),
            "blocked_sum_net_pnl": round(b_pnl, 2),
            "kept_sum_net_pnl": round(k_pnl, 2),
            "blocked_exit_reasons": {
                k: sum(1 for r in blocked if r["exit_reason"] == k)
                for k in sorted({r["exit_reason"] for r in blocked})},
        },
        "rows": rows,
        "caveats": [
            "This reconstructs what the breakout path's pivot WOULD have been "
            "for a pullback setup. That is the exact quantity the frozen filter "
            "consumes, but it was never computed by the production setup, so the "
            "counterfactual is a measurement of the filter's potential effect, "
            "not a claim about what Setup v1 'meant'.",
            "The reconstruction uses each trade's own signal date, so the gap "
            "and extension are evaluated exactly where the live path would.",
        ],
        "git": H.git_commit(),
    }
    H.save_json(args.out, payload)

    n = payload["n_measured"]
    print("=== P6 — materiality of the INERT extension filter ===")
    print(f"  trades measured            : {n} / {payload['n_trades_total']} "
          f"(unavailable {unavailable})")
    s = payload["extension_stats"]
    print(f"  extension distribution     : min {s['min']}  p10 {s['p10']}  "
          f"median {s['median']}  p90 {s['p90']}  max {s['max']}")
    print(f"  threshold                  : {THRESHOLD}")
    print(f"  WOULD BLOCK                : {payload['would_block_count']} "
          f"({payload['would_block_pct']}%)")
    print(f"  near-miss (within 1pp)     : {payload['near_miss_within_1pp']['n']}")
    bk = payload["blocked_vs_kept"]
    print(f"  blocked avg R / kept avg R : {bk['blocked_avg_r']} / "
          f"{bk['kept_avg_r']}")
    print(f"  blocked net P&L            : ${bk['blocked_sum_net_pnl']}  "
          f"(kept ${bk['kept_sum_net_pnl']})")
    print(f"  blocked exit reasons       : {bk['blocked_exit_reasons']}")
    print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
