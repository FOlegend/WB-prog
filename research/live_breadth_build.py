"""
live_breadth_build.py — §12/§14/§15/§16 Live Dataset B：版本化 breadth 重建

用途
----
在**不触碰**任何凍結研究資料的前提下，用「已審核的候選來源 + 本地 OHLCV +
未改動的 breadth 公式」建構 live breadth 產物，並記錄完整 provenance。

強制原則
--------
1. **不寫入** `regime_dual_engine/data/breadth_pit_2016_2025*.csv`（凍結研究產物）。
2. **不改寫** `data/constituents/*`（凍結來源）。
3. **不修改** `build_breadth` / `membership_matrix`（凍結公式）——只提供資料。
4. 輸出帶版本資訊，`members_as_of` 只用 <= as_of 的快照（機械性保證無前視）。
5. 每次建構都寫 membership hash，使日後可重建同一份 universe。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.request
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from regime_dual_engine import pit_breadth_data as pitb  # noqa: E402
from regime_dual_engine.pit_constituents import (PitMembership,  # noqa: E402
                                             norm)

OUT_DIR = os.path.join("data", "live", "breadth")
CACHE = os.path.join(os.path.expanduser("~"), "Library", "Caches",
                     "wbprog_const_audit")

# Candidate source under evaluation (see reports/constituent_source_audit_*).
SOURCE_OWNER = "chinobing"
SOURCE_REPO = "historical_sp500_constituents"
SOURCE_FILE = "sp_500_historical_components.csv"
# The candidate's history is only usable from this date onward; before it the
# universe size falls to 442-478, which is not a real S&P 500.
TRUSTED_FROM = "2017-01-01"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_snapshots() -> tuple[str, str]:
    """Download (with cache) and return (path, sha256)."""
    os.makedirs(CACHE, exist_ok=True)
    dest = os.path.join(CACHE, f"{SOURCE_OWNER}__{SOURCE_FILE}")
    url = (f"https://raw.githubusercontent.com/{SOURCE_OWNER}/"
           f"{SOURCE_REPO}/main/{SOURCE_FILE}")
    if not os.path.exists(dest) or os.path.getsize(dest) < 1000:
        req = urllib.request.Request(url, headers={"User-Agent": "wbprog-live"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
        if len(data) < 1000:
            raise RuntimeError(f"{url} returned {len(data)} bytes")
        with open(dest, "wb") as fh:
            fh.write(data)
    return dest, sha256_file(dest)


def load_live_membership(path: str, trusted_from: str = TRUSTED_FROM) -> dict:
    """snapshot_date -> frozenset, restricted to the trusted window.

    The restriction is a DEFECT BOUNDARY, not a policy: before `trusted_from`
    the candidate's universe is materially incomplete, and silently including
    it would produce a live breadth that is internally consistent and wrong.
    """
    d = pd.read_csv(path)
    d["date"] = pd.to_datetime(d["date"])
    d = d[d["date"] >= pd.Timestamp(trusted_from)]
    d = d.sort_values("date").reset_index(drop=True)
    return {r["date"]: frozenset(str(r["tickers"]).split(","))
            for _, r in d.iterrows()}, d


def members_as_of(mapping: dict, dates: list, as_of: str) -> frozenset:
    """Latest snapshot at or before as_of. Never looks forward."""
    i = int(np.searchsorted(np.array(dates),
                            pd.Timestamp(as_of).to_datetime64(),
                            side="right")) - 1
    return mapping[dates[i]] if i >= 0 else frozenset()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None,
                    help="default: the OHLCV tail actually available")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--allow-untrusted-history", action="store_true",
                    help="diagnostic only: build across the full candidate "
                         "history to quantify what the defect window costs")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    print("fetching candidate constituent source ...", flush=True)
    path, digest = fetch_snapshots()
    print(f"  {SOURCE_OWNER}/{SOURCE_REPO}/{SOURCE_FILE}")
    print(f"  sha256 = {digest[:32]}", flush=True)

    trusted_from = "1996-01-02" if a.allow_untrusted_history else TRUSTED_FROM
    mapping, snaps = load_live_membership(path, trusted_from)
    dates = list(mapping)
    print(f"  snapshots in trusted window: {len(mapping)} "
          f"({dates[0].date()} -> {dates[-1].date()})", flush=True)

    # ---- OHLCV panel (unchanged local cache; NOT rewritten) --------------
    as_of = a.as_of or "2026-08-06"
    panel = pitb.load_panel(end=as_of)
    print(f"  OHLCV panel {panel.shape} -> {panel.index.max().date()}",
          flush=True)
    ohlcv_tail = str(panel.index.max().date())

    # ---- build the live universe matrix ---------------------------------
    idx = panel.index
    cols = [c for c in panel.columns]
    memb = pd.DataFrame(False, index=idx, columns=cols)
    # Days with NO snapshot at or before them must be marked, not silently
    # left False. build_breadth computes pct = above.sum() / n_stocks * 100,
    # and with an all-False row that is 0/0 -> the pandas default of 50.0 --
    # a plausible-looking number computed from no data at all. Emitting those
    # rows would be exactly the "internally consistent but wrong" artefact
    # this whole exercise exists to prevent.
    no_snapshot = pd.Series(False, index=idx)
    # The candidate lists Wikipedia-style tickers (BRK.B) while the cache uses
    # yfinance-style names (BRK-B). The project already has `norm()` for exactly
    # this; without it BF.B and BRK.B read as missing constituents that are in
    # fact present, and the live universe silently drops two members.
    unmatched: set[str] = set()
    # Track separately: a ticker that was a member years ago and has since been
    # acquired has no cache file and that is CORRECT (its price history ended
    # with the company). Only a CURRENT member without a cache file is a gap.
    current_universe = {norm(x) for x in mapping[dates[-1]]}
    for d in idx:
        m = {norm(x) for x in members_as_of(mapping, dates, str(d.date()))}
        if not m:
            no_snapshot.loc[d] = True
            continue
        unmatched |= (m - set(cols))
        for c in cols:
            if c in m:
                memb.loc[d, c] = True
    unmatched_all = set(unmatched)
    missing_current = sorted(current_universe - set(cols))
    historical_only = sorted(unmatched_all - current_universe)
    print(f"  CURRENT universe tickers with no cache file "
          f"(this blocks today's breadth): {missing_current}", flush=True)
    print(f"  historical-only members with no cache file "
          f"(acquired/delisted, expected): {len(historical_only)}",
          flush=True)
    n_missing = int(no_snapshot.sum())
    print(f"  membership matrix built; days with no snapshot: {n_missing}",
          flush=True)
    if n_missing:
        first_ok = idx[~no_snapshot.to_numpy()][0]
        print(f"  first valid day = {first_ok.date()}; the {n_missing} earlier "
              f"panel days will be emitted as NaN, not as a computed value",
              flush=True)

    breadth = pitb.build_breadth(memb, panel)
    # drop the unusable prefix explicitly rather than letting a 0/0 default
    # survive into the artifact
    breadth = breadth.loc[~no_snapshot.reindex(breadth.index).fillna(True)]
    print(f"  live breadth rows={len(breadth)} "
          f"{breadth.index.min().date()} -> {breadth.index.max().date()}",
          flush=True)
    print(f"  last pct={breadth['pct_above_50dma'].iloc[-1]:.4f} "
          f"ad={breadth['ad_line'].iloc[-1]:.0f} "
          f"n={int(breadth['n_stocks'].iloc[-1])}", flush=True)

    # ---- §12 versioning -------------------------------------------------
    last_snap = dates[-1]
    universe = mapping[last_snap]
    mhash = hashlib.sha256(
        ",".join(sorted(universe)).encode()).hexdigest()
    prov = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of": as_of,
        "breadth_date": str(breadth.index.max().date()),
        "breadth_rows": int(len(breadth)),
        "breadth_first_date": str(breadth.index.min().date()),
        "constituent_source": f"github.com/{SOURCE_OWNER}/{SOURCE_REPO}",
        "constituent_file": SOURCE_FILE,
        "constituent_sha256": digest,
        "constituent_snapshot_date": str(last_snap.date()),
        "constituent_snapshot_count": len(universe),
        "constituent_trusted_from": trusted_from,
        "constituent_trusted_from_reason": (
            "the candidate's pre-2017 snapshots carry 442-478 members where "
            "the index had ~498; those rows are EXCLUDED, not repaired"),
        "panel_days_without_snapshot_excluded": n_missing,
        "current_universe_without_cache_file": missing_current,
        "historical_only_tickers_without_cache_count": len(historical_only),
        "ticker_normalisation": ("regime_dual_engine.pit_constituents.norm "
                                 "(Wikipedia BRK.B -> cache BRK-B)"),
        "membership_hash": mhash,
        "ohlcv_source": "local data/cache/equities (UNCHANGED; not rewritten)",
        "ohlcv_tail_date": ohlcv_tail,
        "price_basis": "auto_adjust=True (fully adjusted: splits + dividends)",
        "breadth_formula": ("regime_dual_engine.pit_breadth_data."
                             "build_breadth — UNMODIFIED"),
        "universe_source": "candidate constituent snapshots (this build)",
        "frozen_research_artifacts_touched": [],
        "not_validated": [
            "the candidate's date semantics (announcement vs effective date) "
            "differ from the frozen source by 0-2 days on 80% of events; the "
            "audit could not determine which is correct from data alone",
            "the candidate's 2017+ history has not been validated against any "
            "independent source for the events after 2026-06-30",
        ],
    }

    csv_path = os.path.join(a.out_dir, "breadth_live.csv")
    breadth.to_csv(csv_path)
    prov_path = os.path.join(a.out_dir, "breadth_live_provenance.json")
    with open(prov_path, "w") as fh:
        json.dump(prov, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {csv_path}")
    print(f"wrote {prov_path}")
    print(json.dumps(prov, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
