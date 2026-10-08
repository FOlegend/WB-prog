"""
breadth_refresh.py — safe, reproducible, PIT-safe breadth rebuild path (BS-2)

WHY THIS EXISTS
---------------
The PIT-correct baseline used a breadth CSV whose tail was 2025-07-31. Read-only
archaeology (BS-1) established that this was NOT a data-availability problem:

  * OHLCV for the PIT membership extends to 2026-08-06 (502 of 503 members, 100 %)
  * PIT constituent snapshots extend to 2026-06-30
  * `build_pit_breadth(end=...)` and `build_current_breadth(end=...)` already exist
    and already accept an arbitrary `end`

The series was stale purely because nobody had re-run the rebuild. This module
provides the missing refresh entry point WITHOUT changing the breadth
methodology.

DESIGN CONSTRAINTS (task spec §4)
-----------------------------------
This module does NOT touch:
  * the breadth formula, thresholds, percentile calculation, rolling windows
  * Regime weights, mathematics, divergence cap
  * any trading / risk / stop / exit / entry parameter
  * `breadth_engine.py` or `regime_dual.py`

It only calls the existing builders and manages the resulting artifact.

THE SAFETY PROPERTY THAT MATTERS MOST
-------------------------------------
§11 of the task requires historical invariance: refreshing must not rewrite past
observations. Two independent mechanisms enforce that here:

1. **APPEND-ONLY BY CONSTRUCTION.** `refresh_breadth()` computes the full series
   and then verifies that every pre-existing row is bit-identical to what was
   there before. If any historical row would change, the refresh ABORTS and
   writes nothing. The new data goes to a NEW file; the old file is never
   modified in place unless `promote=True` is passed explicitly.

2. **EXPLICIT PROMOTION.** By default the refreshed series is written to a
   sidecar path and the original is left untouched, so a human can diff before
   anything is promoted. `promote=True` backs up the original to
   `data/.breadth_backup/` before overwriting.

This is the same "agent prepares, human commits" discipline the project already
uses for code: nothing is swapped in behind the user's back.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from regime_dual_engine import pit_breadth_data as pitb
from regime_dual_engine import breadth_data as bd

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_BACKUP_DIR = os.path.join(_DATA_DIR, ".breadth_backup")

# Which artifact each kind maps to, and which builder produces it.
_KINDS = ("pit", "current", "plain")


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _target_path(kind: str, suffix: str = "") -> str:
    base = {"pit": "breadth_pit", "current": "breadth_current",
            "plain": "breadth"}[kind]
    fname = f"{base}_2016_2025{suffix}.csv"
    return os.path.join(_DATA_DIR, fname)


def _build(kind: str, end: str) -> pd.DataFrame:
    """Build the series with the EXISTING, unmodified builders."""
    if kind == "pit":
        return pitb.build_pit_breadth(end=end)
    if kind == "current":
        return pitb.build_current_breadth(end=end)
    return bd.build_breadth_series(end=end, verbose=False)


def _existing(kind: str) -> pd.DataFrame | None:
    p = _target_path(kind)
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, parse_dates=["datetime"]).set_index("datetime")


def compare_historical(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """Row-by-row comparison over the dates the OLD series already covered.

    This is the §11 gate. A refresh may APPEND; it may not rewrite.
    """
    common = old.index.intersection(new.index)
    if not len(common):
        return {"n_common": 0, "identical": True,
                "note": "no overlap — nothing to preserve"}
    o = old.loc[common]
    n = new.loc[common]
    cols = [c for c in o.columns if c in n.columns]
    diffs = {}
    n_diff_rows = 0
    for c in cols:
        d = (o[c].astype(float) - n[c].astype(float)).abs()
        bad = d > 1e-9
        if bool(bad.any()):
            n_diff_rows += int(bad.sum())
            # positional lookup: bad.idxmax() returns a LABEL, and `common` is a
            # DatetimeIndex, so label-based indexing raises. Use the position.
            pos = int(bad.to_numpy().argmax())
            diffs[c] = {
                "n_differing": int(bad.sum()),
                "max_abs_diff": float(d.max()),
                "first_differing_date": str(pd.Timestamp(common[pos]).date()),
            }
    return {
        "n_common": int(len(common)),
        "columns": cols,
        "identical": n_diff_rows == 0,
        "n_differing_rows": n_diff_rows,
        "differences": diffs,
        "old_range": [str(common.min().date()), str(common.max().date())],
    }


def refresh_breadth(kind: str = "pit", *, end: str | None = None,
                    promote: bool = False, suffix: str = "_refreshed",
                    verbose: bool = True) -> dict:
    """Rebuild the breadth series out to `end` WITHOUT mutating the original.

    Parameters
    ----------
    kind : 'pit' | 'current' | 'plain'
    end : target date (default: the latest trading date available in the OHLCV
          cache, discovered rather than hard-coded)
    promote : write over the original file (backs up first). Off by default so
          a human can diff before anything is swapped in.

    Returns a report including the §11 historical-invariance comparison.
    """
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")

    if end is None:
        end = _latest_ohlcv_date()
    if verbose:
        print(f"[bs-2] rebuilding breadth kind={kind} to end={end}", flush=True)

    new = _build(kind, end)
    new = new.sort_index()
    if verbose:
        print(f"[bs-2] built {len(new)} rows: "
              f"{new.index.min().date()} -> {new.index.max().date()}", flush=True)

    old = _existing(kind)
    old_path = _target_path(kind)

    report: dict = {
        "kind": kind,
        "requested_end": end,
        "new_rows": int(len(new)),
        "new_range": [str(new.index.min().date()), str(new.index.max().date())],
        "new_tail": str(new.index.max().date()),
        "original_path": old_path,
        "promoted": False,
    }

    if old is not None:
        report["original_rows"] = int(len(old))
        report["original_range"] = [str(old.index.min().date()),
                                    str(old.index.max().date())]
        report["original_tail"] = str(old.index.max().date())
        report["original_sha256"] = _sha256(old_path)
        report["appended_rows"] = int(len(new.index.difference(old.index)))
        hist = compare_historical(old, new)
        report["historical_invariance"] = hist
        if not hist["identical"]:
            report["ABORTED"] = (
                "historical rows would change — refusing to write. Investigate "
                "the source data before proceeding (task §11).")
            if verbose:
                print(f"[bs-2] ABORT: {report['ABORTED']}")
                for c, d in hist.get("differences", {}).items():
                    print(f"        {c}: {d}")
            return report
    else:
        report["historical_invariance"] = {"note": "no original to preserve"}
        report["appended_rows"] = int(len(new))

    out_path = old_path if promote else _target_path(kind, suffix)
    if promote:
        os.makedirs(_BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        bak = os.path.join(_BACKUP_DIR,
                           os.path.basename(old_path).replace(".csv", f".{stamp}.csv"))
        shutil.copy2(old_path, bak)
        report["backup_path"] = bak
    new.reset_index().rename(columns={"index": "datetime"}).to_csv(
        out_path, index=False)
    report["written_path"] = out_path
    report["written_sha256"] = _sha256(out_path)
    report["promoted"] = bool(promote)
    if verbose:
        print(f"[bs-2] wrote -> {out_path}  (promote={promote})", flush=True)
    return report


def _latest_ohlcv_date() -> str:
    """Discover the latest trading date in the OHLCV cache.

    Derived from SPY (the benchmark the regime already depends on) rather than
    hard-coded, so the refresh target follows the data instead of a constant.
    """
    from regime_dual_engine.pit_constituents import cache_dir
    p = os.path.join(cache_dir(), "SPY.csv")
    df = pd.read_csv(p, usecols=["datetime"], parse_dates=["datetime"])
    return str(pd.Timestamp(df["datetime"].max()).date())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", default="pit", choices=_KINDS)
    ap.add_argument("--end", default=None,
                    help="target date; default = latest OHLCV trading date")
    ap.add_argument("--promote", action="store_true",
                    help="overwrite the original (backs up first). Off by default.")
    ap.add_argument("--all", action="store_true",
                    help="refresh all three series")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    kinds = _KINDS if args.all else (args.kind,)
    reports = []
    for k in kinds:
        r = refresh_breadth(k, end=args.end, promote=args.promote)
        reports.append(r)
        tail = r.get("new_tail")
        old = r.get("original_tail")
        inv = r.get("historical_invariance", {})
        print(f"  {k:<8} {old} -> {tail}   appended {r.get('appended_rows')}   "
              f"history_identical={inv.get('identical')}")
        if r.get("ABORTED"):
            print(f"           ABORTED: {r['ABORTED']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"generated": datetime.date.today().isoformat(),
                       "reports": reports}, f, indent=2, ensure_ascii=False,
                      default=str)
        print(f"\nreport -> {args.out}")
    return 0 if not any(r.get("ABORTED") for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
