"""
constituent_source_audit.py — §4/§5/§6/§7/§8/§9/§10 候選來源稽核

對每個候選 PIT constituent 來源，回答四個問題：
  1. 這個日期代表什麼（公告日 / 生效日 / 快照日）？
  2. 它與現有凍結來源的 重疊 期是否一致？
  3. 它能不能 PIT 重建（members_as_of 無前視）？
  4. 它的歷史是否可信（成員數是否接近真實指數）？

設計原則：**只讀**。不下載進專案資料目錄、不寫任何生產資料集。
所有輸出為報告與 JSON，供人工審核。
"""
from __future__ import annotations

import argparse
import ast
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

CACHE = os.path.join(os.path.expanduser("~"), "Library", "Caches",
                     "wbprog_const_audit")

SOURCES = {
    "chinobing": {
        "repo": "https://github.com/chinobing/historical_sp500_constituents",
        "events": "sp500_changes_since_1996.csv",
        "snapshots": "sp_500_historical_components.csv",
        "current": "sp500_constituents.csv",
    },
    "leandroloi": {
        "repo": "https://github.com/leandroloi/historical_sp500_constituents",
        "events": "sp500_changes_since_1996.csv",
        "snapshots": "sp_500_historical_components.csv",
        "current": "sp500_constituents.csv",
    },
}

# The S&P 500 has never had fewer than ~485 or more than ~520 securities in the
# modern era. A snapshot outside this band is a defect, not a rebalance.
PLAUSIBLE_MIN = 480
PLAUSIBLE_MAX = 525


def fetch(source: str, kind: str) -> str:
    """Download one file into a user-level cache (never into the repo).

    The raw URL uses the OWNER name, which for these two candidates is the
    same string as the source key -- but that coincidence is not assumed; the
    mapping is explicit so a future candidate cannot silently resolve to the
    wrong repository.
    """
    os.makedirs(CACHE, exist_ok=True)
    fname = SOURCES[source][kind]
    dest = os.path.join(CACHE, f"{source}__{fname}")
    if not os.path.exists(dest) or os.path.getsize(dest) < 200:
        owner = SOURCES[source]["repo"].split("/")[-2]
        url = (f"https://raw.githubusercontent.com/{owner}/"
               f"historical_sp500_constituents/main/{fname}")
        req = urllib.request.Request(url, headers={"User-Agent": "wbprog-audit"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        if len(data) < 200:
            raise RuntimeError(f"{url} returned {len(data)} bytes")
        with open(dest, "wb") as fh:
            fh.write(data)
    return dest


def load_snapshots(source: str) -> pd.DataFrame:
    p = fetch(source, "snapshots")
    d = pd.read_csv(p)
    # A 404 from raw.githubusercontent comes back as a tiny text file, and
    # pd.read_csv then produces a frame with none of the expected columns.
    # Validating here turns a confusing downstream KeyError into a clear
    # statement that this candidate cannot supply the file.
    for col in ("date", "tickers"):
        if col not in d.columns:
            raise RuntimeError(
                f"{source}: snapshots file lacks column {col!r} "
                f"(has {list(d.columns)}; first bytes: "
                f"{open(p, 'rb').read(120)!r})")
    d["date"] = pd.to_datetime(d["date"])
    d["members"] = d["tickers"].apply(
        lambda x: frozenset(str(x).split(",")))
    return d.sort_values("date").reset_index(drop=True)


def load_events(source: str) -> pd.DataFrame:
    p = fetch(source, "events")
    d = pd.read_csv(p)
    for col in ("date", "added_tickers", "removed_tickers"):
        if col not in d.columns:
            raise RuntimeError(
                f"{source}: events file lacks column {col!r} "
                f"(has {list(d.columns)})")
    d["date"] = pd.to_datetime(d["date"])
    parse = lambda x: [] if pd.isna(x) else list(ast.literal_eval(str(x)))
    d["add"] = d["added_tickers"].apply(parse)
    d["rem"] = d["removed_tickers"].apply(parse)
    return d.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
def audit_date_semantics(events: pd.DataFrame, snaps: pd.DataFrame) -> dict:
    """Do the event dates and the snapshot dates agree? (§5)

    If the two representations disagree on WHEN a change took effect, the
    source does not have a single coherent date semantics, and every PIT
    query built on it inherits the ambiguity.
    """
    ev = events[["date"]].copy()
    sn = snaps[["date"]].copy()
    ev_set = set(ev["date"])
    sn_set = set(sn["date"])
    both = ev_set & sn_set
    return {
        "n_event_dates": len(ev_set),
        "n_snapshot_dates": len(sn_set),
        "dates_in_both": len(both),
        "event_only": len(ev_set - sn_set),
        "snapshot_only": len(sn_set - ev_set),
        "event_only_examples": [str(d.date()) for d in
                                sorted(ev_set - sn_set)[-8:]],
        "interpretation": (
            "an event stream and a snapshot stream that share dates describe "
            "the SAME effective-date convention; where they diverge the two "
            "files were written by different runs and the date semantics are "
            "not verifiable from the data alone"),
    }


def audit_history_plausibility(snaps: pd.DataFrame) -> dict:
    """Does the historical universe size look like the real index? (§11)"""
    n = snaps["members"].apply(len)
    out = {
        "min_n": int(n.min()), "max_n": int(n.max()),
        "median_n": float(n.median()),
        "first_date": str(snaps["date"].min().date()),
        "last_date": str(snaps["date"].max().date()),
        "plausible_band": [PLAUSIBLE_MIN, PLAUSIBLE_MAX],
    }
    bad = snaps[(n < PLAUSIBLE_MIN) | (n > PLAUSIBLE_MAX)]
    out["n_implausible_snapshots"] = int(len(bad))
    out["implausible_range"] = (
        None if not len(bad)
        else [str(bad["date"].min().date()), str(bad["date"].max().date())])
    out["worst_undershoot"] = int(PLAUSIBLE_MIN - n.min()) if n.min() < \
        PLAUSIBLE_MIN else 0
    by_year = {}
    for y, g in snaps.assign(n=n).groupby(snaps["date"].dt.year):
        by_year[int(y)] = {"median_n": float(g["n"].median()),
                           "min_n": int(g["n"].min()),
                           "max_n": int(g["n"].max())}
    out["by_year"] = by_year
    return out


def audit_internal_consistency(snaps: pd.DataFrame,
                               events: pd.DataFrame) -> dict:
    """Do the two representations of the SAME source agree? (§10)"""
    idx = {d: i for i, d in enumerate(snaps["date"])}
    mismatches = []
    checked = 0
    for _, e in events.iterrows():
        d = e["date"]
        if d not in idx:
            continue
        i = idx[d]
        if i == 0:
            continue
        prev = set(snaps["members"].iloc[i - 1])
        cur = set(snaps["members"].iloc[i])
        if cur == prev:
            continue
        implied_add = cur - prev
        implied_rem = prev - cur
        checked += 1
        if implied_add != set(e["add"]) or implied_rem != set(e["rem"]):
            mismatches.append({
                "date": str(d.date()),
                "event_added": sorted(e["add"]),
                "event_removed": sorted(e["rem"]),
                "snapshot_implied_added": sorted(implied_add),
                "snapshot_implied_removed": sorted(implied_rem),
            })
    return {
        "events_checked": checked,
        "mismatches": len(mismatches),
        "mismatch_rate": (mismatches and len(mismatches) / checked) or 0.0,
        "examples": mismatches[:10],
    }


def audit_anomalies(events: pd.DataFrame, snaps: pd.DataFrame) -> dict:
    """Structural impossibilities in the event stream. (§11 completeness)"""
    from collections import defaultdict
    tl = defaultdict(list)
    for _, e in events.iterrows():
        for t in e["add"]:
            tl[t].append((e["date"], "ADD"))
        for t in e["rem"]:
            tl[t].append((e["date"], "REM"))
    flip = []
    rapid = []
    for t, evs in tl.items():
        evs = sorted(evs)
        for i in range(2, len(evs)):
            if (evs[i - 2][1] == "ADD" and evs[i - 1][1] == "REM"
                    and evs[i][1] == "ADD"):
                flip.append({"ticker": t,
                             "added": str(evs[i - 2][0].date()),
                             "removed": str(evs[i - 1][0].date()),
                             "re_added": str(evs[i][0].date()),
                             "span_days": int((evs[i][0] - evs[i - 2][0]).days)})
        adds = [d for d, k in evs if k == "ADD"]
        for i in range(1, len(adds)):
            gap = int((adds[i] - adds[i - 1]).days)
            if gap < 120:
                rapid.append({"ticker": t,
                              "first": str(adds[i - 1].date()),
                              "again": str(adds[i].date()), "gap_days": gap})
    flip.sort(key=lambda x: x["re_added"], reverse=True)
    # how many snapshots keep the universe size constant through a flip?
    snap_map = {r["date"]: set(r["members"]) for _, r in snaps.iterrows()}
    sizes = []
    for f in flip[:12]:
        for d in (f["added"], f["removed"], f["re_added"]):
            k = pd.Timestamp(d)
            prior = [x for x in snap_map if x <= k]
            if prior:
                sizes.append(len(snap_map[max(prior)]))
    return {
        "n_add_remove_add": len(flip),
        "flips": flip[:15],
        "n_rapid_re_add_under_120d": len(rapid),
        "rapid_examples": rapid[:10],
        "universe_size_during_flips": sorted(set(sizes)),
        "interpretation": (
            "an index that swaps the same names in and out twice inside a "
            "month, while the universe size never changes, is not a market "
            "event -- it is a scraping artefact written into permanent "
            "history"),
    }


def pit_test(snaps: pd.DataFrame, probes: list[str],
             test_dates: list[str]) -> dict:
    """members_as_of with no look-ahead. (§9)"""
    dates = list(snaps["date"])
    arr = np.array(dates)

    def members_as_of(d: str) -> frozenset:
        ts = pd.Timestamp(d)
        i = int(np.searchsorted(arr, ts.to_datetime64(), side="right")) - 1
        return snaps["members"].iloc[i] if i >= 0 else frozenset()

    rows = []
    for d in test_dates:
        m = members_as_of(d)
        rows.append({"as_of": d, "n": len(m),
                     "probes": {p: (p in m) for p in probes}})
    # look-ahead probe: a name added on the LAST date must be absent earlier
    last = snaps.iloc[-1]
    last_date = last["date"]
    prev_date = snaps.iloc[-2]["date"]
    newest = sorted(set(last["members"]) - set(snaps["members"].iloc[-2]))
    leak = [t for t in newest
            if t in members_as_of(str(prev_date.date()))]
    return {
        "rows": rows,
        "latest_snapshot": str(last_date.date()),
        "newest_members": newest[:20],
        "lookahead_leak": leak,
        "lookahead_clean": len(leak) == 0,
    }


def overlap_comparison(snaps: pd.DataFrame, current_map: dict) -> dict:
    """Candidate vs the frozen local source over the shared history. (§7)"""
    rows = []
    for _, r in snaps.iterrows():
        d = r["date"]
        if d not in current_map:
            continue
        a = current_map[d]
        b = set(r["members"])
        u = len(a | b)
        rows.append({
            "date": d, "cur_n": len(a), "cand_n": len(b),
            "inter": len(a & b), "jaccard": (len(a & b) / u) if u else 0.0,
            "only_cur": sorted(a - b), "only_cand": sorted(b - a),
        })
    df = pd.DataFrame(rows)
    if not len(df):
        return {"overlap_days": 0}
    by_year = {}
    for y, g in df.groupby(df["date"].dt.year):
        by_year[int(y)] = {
            "n": int(len(g)),
            "jaccard_median": float(g["jaccard"].median()),
            "jaccard_min": float(g["jaccard"].min()),
            "days_with_diff": int(((g["only_cur"].apply(len) > 0) |
                                   (g["only_cand"].apply(len) > 0)).sum()),
        }
    worst = df.nlargest(8, "jaccard", keep="all").nsmallest(8, "jaccard")
    return {
        "overlap_days": int(len(df)),
        "jaccard_median": float(df["jaccard"].median()),
        "jaccard_min": float(df["jaccard"].min()),
        "identical_days": int((df["jaccard"] > 0.9999).sum()),
        "first_disagreement": (
            str(df[df["only_cur"].apply(len) > 0]["date"].min().date())
            if (df["only_cur"].apply(len) > 0).any() else None),
        "by_year": by_year,
        "worst_days": [
            {"date": str(r["date"].date()), "cur_n": int(r["cur_n"]),
             "cand_n": int(r["cand_n"]), "jaccard": round(r["jaccard"], 4),
             "n_only_cur": len(r["only_cur"]), "n_only_cand": len(r["only_cand"]),
             "sample_only_cur": r["only_cur"][:12]}
            for _, r in worst.iterrows()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--sources", default="chinobing,leandroloi")
    a = ap.parse_args()

    # NOTE: imported under an explicit alias. A bare
    # `from ...pit_constituents import load_snapshots` here would shadow this
    # module's own candidate-source loader of the same name, and the candidate
    # audit would then silently re-audit the LOCAL frozen source against
    # itself. That failure is silent -- every column check still passes -- so it
    # is worth preventing by name rather than by convention.
    from regime_dual_engine.pit_constituents import (
        load_snapshots as load_local_snapshots)
    cur_df = load_local_snapshots("fja")
    cur_map = {r["date"]: set(r["tickers"]) for _, r in cur_df.iterrows()}
    local_current = set(pd.read_csv(
        "data/constituents/sp500_current.csv")["Symbol"].astype(str).str.strip())

    out = {
        "generated": dt.date.today().isoformat(),
        "local_frozen_source": {
            "name": "fja05680/sp500 (local data/constituents)",
            "snapshots": int(len(cur_df)),
            "first": str(cur_df["date"].min().date()),
            "last": str(cur_df["date"].max().date()),
            "role": "AUTHORITATIVE for the frozen research baseline; not to "
                    "be rewritten",
        },
        "plausible_band": [PLAUSIBLE_MIN, PLAUSIBLE_MAX],
        "candidates": {},
    }

    for name in a.sources.split(","):
        print(f"=== auditing {name} ===", flush=True)
        try:
            snaps = load_snapshots(name)
            events = load_events(name)
        except Exception as exc:
            out["candidates"][name] = {
                "status": "FETCH_FAILED",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            print(f"  FAILED: {exc}", flush=True)
            continue

        rec = {
            "status": "FETCHED",
            "repo": SOURCES[name]["repo"],
            "snapshots": int(len(snaps)),
            "events": int(len(events)),
            "snapshot_range": [str(snaps["date"].min().date()),
                               str(snaps["date"].max().date())],
            "event_range": [str(events["date"].min().date()),
                            str(events["date"].max().date())],
            "A_date_semantics": audit_date_semantics(events, snaps),
            "B_history_plausibility": audit_history_plausibility(snaps),
            "C_internal_consistency": audit_internal_consistency(snaps, events),
            "D_anomalies": audit_anomalies(events, snaps),
        }
        rec["E_pit"] = pit_test(
            snaps,
            probes=["P", "BE", "ILMN", "BLDR", "TTD", "TAP", "FERG", "EA"],
            test_dates=["2024-01-31", "2025-07-31", "2026-06-30",
                        "2026-08-06", "2026-08-18", "2026-09-21"])
        rec["F_overlap_vs_frozen"] = overlap_comparison(snaps, cur_map)

        # current universe
        latest = snaps.iloc[-1]
        cand_now = set(latest["members"])
        rec["G_current_universe"] = {
            "as_of": str(latest["date"].date()),
            "n": len(cand_now),
            "duplicates": int(len(latest["tickers"].split(","))
                              - len(cand_now)),
            "vs_local_current": {
                "local_n": len(local_current),
                "intersection": len(cand_now & local_current),
                "only_candidate": sorted(cand_now - local_current),
                "only_local": sorted(local_current - cand_now),
            },
        }
        rec["membership_hash"] = hashlib.sha256(
            ",".join(sorted(cand_now)).encode()).hexdigest()[:16]
        out["candidates"][name] = rec
        print(f"  snapshots={rec['snapshots']} events={rec['events']} "
              f"last={rec['snapshot_range'][1]} "
              f"implausible={rec['B_history_plausibility']['n_implausible_snapshots']} "
              f"flips={rec['D_anomalies']['n_add_remove_add']} "
              f"lookahead_clean={rec['E_pit']['lookahead_clean']}", flush=True)

    path = a.out or (f"reports/constituent_source_audit_raw_"
                     f"{dt.date.today().isoformat()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False, default=str)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
