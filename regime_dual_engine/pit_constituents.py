"""
pit_constituents.py — Point-in-Time S&P 500 Membership (reviewer Task 1)

Reconstructs PIT membership from historical constituent data:

  Primary source : fja05680/sp500  (sp500_changes.csv — dated full membership
                   lists on change dates; sp500_ticker_start_end.csv — per-
                   ticker membership windows; sp500_current.csv — today's list)
  Cross-check    : thuningxu/sp500nq100 (sp500_components_history.csv) and
                   pierrebrunelle/sp500-historical-constituents (monthly files)

Rules (per reviewer spec):
  * For each trading date D use the LATEST constituent snapshot with date <= D.
  * Handle ticker start/end correctly (a ticker may leave and re-enter).
  * Never use future constituents for earlier dates.
  * Dotted tickers (BF.B) are normalized to cache naming (BF-B).
  * Report ticker changes / missing OHLCV / delistings / data limitations.

No changes are made to the dual-engine core (regime_dual.py etc.) — this module
only produces the membership layer consumed by pit_breadth_data.py.
"""
from __future__ import annotations

import os
import json
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONST_DIR = os.path.join(_REPO_ROOT, "data", "constituents")


def cache_dir() -> str:
    """OHLCV cache lives next to the repo (OneDrive .../data/cache/equities),
    falling back to repo/data/cache/equities."""
    d = os.path.join(os.path.dirname(_REPO_ROOT), "data", "cache", "equities")
    if not os.path.isdir(d):
        d = os.path.join(_REPO_ROOT, "data", "cache", "equities")
    return d


def norm(t: str) -> str:
    """Normalize a constituent ticker to cache naming (BF.B -> BF-B)."""
    return str(t).replace(".", "-").replace("^", "").strip()


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_snapshots(source: str = "fja") -> pd.DataFrame:
    """Dated full-membership lists (change dates only).

    Returns DataFrame[sorted by date] with columns: date, tickers (list).
    """
    path = os.path.join(_CONST_DIR, "sp500_changes.csv")
    if source == "thuningxu":
        path = os.path.join(_CONST_DIR, "sp500_thuningxu.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df["tickers"] = df["tickers"].str.split(",")
    return df.sort_values("date").reset_index(drop=True)


def load_ticker_windows() -> pd.DataFrame:
    """Per-ticker membership windows from sp500_ticker_start_end.csv.

    Multiple rows per ticker = left and re-entered (e.g. AAL, AMD).
    end_date NaN = still a member at dataset end.
    """
    te = pd.read_csv(os.path.join(_CONST_DIR, "sp500_ticker_start_end.csv"))
    te["start_date"] = pd.to_datetime(te["start_date"])
    te["end_date"] = pd.to_datetime(te["end_date"])
    return te


def load_current_members() -> list[str]:
    """Today's S&P 500 constituents (sp500_current.csv, Symbol column)."""
    cur = pd.read_csv(os.path.join(_CONST_DIR, "sp500_current.csv"))
    return [str(s).strip() for s in cur["Symbol"].astype(str).tolist() if str(s).strip() != "nan"]


# ---------------------------------------------------------------------------
# Point-in-time membership
# ---------------------------------------------------------------------------
class PitMembership:
    """Maps arbitrary dates to the S&P 500 membership valid on that date.

    membership(D) = tickers of the latest snapshot with date <= D.
    """

    def __init__(self, snapshots: pd.DataFrame | None = None):
        snap = load_snapshots("fja") if snapshots is None else snapshots
        self.dates = snap["date"].to_numpy()
        self.sets = [set(tks) for tks in snap["tickers"].tolist()]
        self._norm_cache = {}

    def _norm_set(self, i: int) -> set[str]:
        if i not in self._norm_cache:
            self._norm_cache[i] = {norm(t) for t in self.sets[i]}
        return self._norm_cache[i]

    def members_as_of(self, d) -> list[str]:
        """Tickers in the index on date d (latest snapshot <= d)."""
        ts = pd.Timestamp(d)
        idx = int(np.searchsorted(self.dates, ts.to_datetime64(), side="right")) - 1
        if idx < 0:
            return []
        return sorted(self._norm_set(idx))

    def membership_matrix(self, trading_dates) -> pd.DataFrame:
        """Boolean DataFrame (index=trading_dates, cols=all unique tickers)."""
        trading_dates = pd.DatetimeIndex(trading_dates)
        dv = trading_dates.to_numpy()
        idx = np.searchsorted(self.dates, dv, side="right") - 1
        all_tickers = sorted({t for s in self.sets for t in {norm(x) for x in s}})
        cols = {t: i for i, t in enumerate(all_tickers)}
        mat = np.zeros((len(dv), len(all_tickers)), dtype=bool)
        for t, ci in cols.items():
            active = np.array([t in s for s in self.sets], dtype=bool)
            mat[:, ci] = active[idx]
        return pd.DataFrame(mat, index=trading_dates, columns=all_tickers)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_membership(snapshots: pd.DataFrame) -> dict:
    """Sanity checks on the raw dataset: counts per snapshot, gaps, dupes."""
    counts = snapshots["tickers"].apply(len)
    gaps = snapshots["date"].diff().dt.days
    dup_dates = snapshots["date"].duplicated().sum()
    return {
        "n_snapshots": len(snapshots),
        "date_range": [str(snapshots["date"].min().date()), str(snapshots["date"].max().date())],
        "count_per_snapshot": {
            "mean": round(float(counts.mean()), 1),
            "min": int(counts.min()), "max": int(counts.max()),
            "q05": int(counts.quantile(0.05)), "q95": int(counts.quantile(0.95)),
        },
        "max_snapshot_gap_days": int(gaps.max()) if len(gaps) > 1 else 0,
        "median_snapshot_gap_days": int(gaps.median()) if len(gaps) > 1 else 0,
        "duplicate_dates": int(dup_dates),
        "n_unique_tickers": len({t for s in snapshots["tickers"] for t in s}),
    }


def cross_check_sources() -> dict:
    """fja05680 vs thuningxu membership on sample dates (Jaccard similarity)."""
    fja = PitMembership(load_snapshots("fja"))
    thu = PitMembership(load_snapshots("thuningxu"))
    sample_dates = pd.date_range("2016-01-04", "2025-07-31", freq="QS")
    rows = []
    for d in sample_dates:
        a = set(fja.members_as_of(d))
        b = set(thu.members_as_of(d))
        inter = len(a & b)
        union = len(a | b)
        rows.append({
            "date": str(pd.Timestamp(d).date()),
            "n_fja": len(a), "n_thu": len(b),
            "only_fja": sorted(a - b)[:8], "only_thu": sorted(b - a)[:8],
            "jaccard": round(inter / union, 4) if union else 1.0,
        })
    mism = [r for r in rows if r["jaccard"] < 0.99]
    return {
        "sample_n": len(rows),
        "mean_jaccard": round(float(np.mean([r["jaccard"] for r in rows])), 4),
        "min_jaccard": round(float(np.min([r["jaccard"] for r in rows])), 4),
        "mismatched_snapshots": len(mism),
        "samples": rows,
    }


def coverage_report(cache: str | None = None, end: str = "2026-06-30") -> dict:
    """Per-date fraction of PIT members that have OHLCV in the cache."""
    cache = cache or cache_dir()
    cache = {norm(f[:-4]) for f in os.listdir(cache)
             if f.endswith(".csv") and "-K1" not in f}
    pit = PitMembership()
    ch = load_snapshots("fja")
    sub = ch[(ch["date"] >= "2015-06-01") & (ch["date"] <= end)]
    fracs = []
    for _, row in sub.iterrows():
        tks = [norm(t) for t in row["tickers"]]
        have = sum(1 for t in tks if t in cache)
        fracs.append(have / len(tks))
    return {
        "cache_tickers": len(cache),
        "n_snapshots": len(sub),
        "per_date_coverage": {
            "mean_pct": round(float(np.mean(fracs)) * 100, 2),
            "min_pct": round(float(np.min(fracs)) * 100, 2),
            "max_pct": round(float(np.max(fracs)) * 100, 2),
            "q10_pct": round(float(np.percentile(fracs, 10)) * 100, 2),
        },
    }


if __name__ == "__main__":
    print("=== PIT membership validation ===")
    snap = load_snapshots("fja")
    v = validate_membership(snap)
    print(json.dumps(v, indent=1))
    print("\n=== fja vs thuningxu cross-check ===")
    cc = cross_check_sources()
    print(f"mean Jaccard={cc['mean_jaccard']}  min={cc['min_jaccard']}  "
          f"mismatched={cc['mismatched_snapshots']}/{cc['sample_n']}")
    for r in cc["samples"][:4]:
        print(f"  {r['date']}: fja={r['n_fja']} thu={r['n_thu']} only_fja={r['only_fja'][:5]} "
              f"only_thu={r['only_thu'][:5]}")
    cache = cache_dir()
    print("\n=== OHLCV coverage ===")
    print(json.dumps(coverage_report(cache), indent=1))
