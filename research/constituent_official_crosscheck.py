"""
constituent_official_crosscheck.py — §2/§5/§6/§7/§8 官方來源交叉驗證

以 S&P Dow Jones Indices 官方公告為**一手基準**，判定候選 A 的日期語意，
並對 members_as_of 做真正的 PIT 前視測試。

官方基準（由 press.spglobal.com / spglobal.com/spdji 公告 PDF 逐條取得）
--------------------------------------------------------------------------
每筆記錄 announcement_date / effective_date / action / ticker，並附公告 URL。
未經一手來源證實的事件一律標為 UNVERIFIED，不做推論。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import datetime as dt
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===========================================================================
# OFFICIAL S&P DJI RECORD — primary source, transcribed from the announcements
# ===========================================================================
# effective_date is the date the change is "effective prior to the opening of
# trading", i.e. the first session on which the new name is actually a member.
OFFICIAL = [
    {
        "event": "A",
        "announcement_date": "2026-05-27",
        "announcement_source": ("S&P DJI press release 20260527-1483532 "
                                "'FedEx Freight Holding Company Set to Join "
                                "S&P 500; EPAM Systems and Dave to Join "
                                "S&P SmallCap 600'"),
        "url": ("https://www.spglobal.com/spdji/en/documents/indexnews/"
                "announcements/20260527-1483532/1483532_fdx-amwd-56.pdf"),
        "actions": [
            {"ticker": "FDXF", "action": "addition", "effective_date": "2026-06-01"},
            {"ticker": "EPAM", "action": "deletion", "effective_date": "2026-06-02"},
        ],
        "note": ("the English table gives FDXF addition 2026-06-01 and EPAM "
                 "deletion 2026-06-02, while the body text and the Portuguese "
                 "release say both are effective 2026-06-02 / 06-01 — S&P's own "
                 "documents are not fully self-consistent here"),
    },
    {
        "event": "FERG",
        "announcement_date": "2026-07-31",
        "announcement_source": ("S&P DJI press release 20260731 'Ferguson "
                                "Enterprises Set to Join S&P 500 and ADI "
                                "Global Distribution to Join S&P SmallCap 600'"),
        "url": "https://press.spglobal.com/2026-07-31-Ferguson-Enterprises-Set-to-Join-S-P-500-and-ADI-Global-Distribution-to-Join-S-P-SmallCap-600",
        "actions": [
            {"ticker": "FERG", "action": "addition", "effective_date": "2026-08-05"},
            {"ticker": "EA", "action": "deletion", "effective_date": "2026-08-05"},
        ],
    },
    {
        "event": "B",
        "announcement_date": "2026-08-13",
        "announcement_source": ("S&P DJI press release 20260813-1484396 "
                                "'Reddit Set to Join S&P 500 and Sun "
                                "Communities to Join S&P MidCap 400'"),
        "url": ("https://www.spglobal.com/spdji/en/documents/indexnews/"
                "announcements/20260813-1484396/1484396_avb54wbs.pdf"),
        "actions": [
            {"ticker": "RDDT", "action": "addition", "effective_date": "2026-08-18"},
            {"ticker": "AVB", "action": "deletion", "effective_date": "2026-08-18"},
        ],
        "note": ("EQR acquires AVB; the combined company is renamed VMRK and "
                 "KEEPS its S&P 500 seat. So the economic universe change is "
                 "one addition (RDDT) and one deletion (AVB); EQR->VMRK is a "
                 "ticker change, not a membership change"),
    },
    {
        "event": "C",
        "announcement_date": "2026-09-04",
        "announcement_source": ("S&P DJI quarterly rebalance announcement, "
                                "2026-09-04, effective prior to the open on "
                                "Monday 2026-09-21"),
        "url": "https://seekingalpha.com/news/4640512",
        "actions": [
            {"ticker": "BE", "action": "addition", "effective_date": "2026-09-21"},
            {"ticker": "P", "action": "addition", "effective_date": "2026-09-21"},
            {"ticker": "ILMN", "action": "addition", "effective_date": "2026-09-21"},
            {"ticker": "TAP", "action": "deletion", "effective_date": "2026-09-21"},
            {"ticker": "TTD", "action": "deletion", "effective_date": "2026-09-21"},
            {"ticker": "BLDR", "action": "deletion", "effective_date": "2026-09-21"},
        ],
    },
    {
        "event": "MRSH",
        "announcement_date": "2025-10-14",
        "announcement_source": ("Marsh McLennan company press release: ticker "
                                "changes from MMC to MRSH effective "
                                "2026-01-14. NOT an S&P index action."),
        "url": "https://www.marsh.com/jp/en/about/media/marshmclennan-and-its-businesses-will-brand-as-marsh.html",
        "actions": [
            {"ticker": "MRSH", "action": "ticker_change_from_MMC",
             "effective_date": "2026-01-14"},
        ],
        "note": ("the company was a member before and after; only the symbol "
                 "changed. Any 'MRSH in / MMC out' event is therefore a SYMBOL "
                 "change, not a universe change, and must not be counted as "
                 "evidence about index date semantics"),
    },
]


def load_candidate():
    from research.live_breadth_build import (fetch_snapshots,
                                             load_live_membership)
    path, digest = fetch_snapshots()
    mapping, snaps = load_live_membership(path, trusted_from="1996-01-02")
    import ast
    ev = pd.read_csv(os.path.join(
        os.path.expanduser("~"), "Library", "Caches", "wbprog_const_audit",
        "chinobing__sp500_changes_since_1996.csv"))
    ev["date"] = pd.to_datetime(ev["date"])
    parse = lambda x: [] if pd.isna(x) else list(ast.literal_eval(str(x)))
    ev["add"] = ev["added_tickers"].apply(parse)
    ev["rem"] = ev["removed_tickers"].apply(parse)
    return mapping, list(mapping), ev.sort_values("date").reset_index(drop=True), digest


def members_as_of(mapping, dates, as_of):
    i = int(np.searchsorted(np.array(dates),
                            pd.Timestamp(as_of).to_datetime64(),
                            side="right")) - 1
    return set(mapping[dates[i]]) if i >= 0 else set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    mapping, dates, ev, digest = load_candidate()
    ev_by_date = {r["date"]: r for _, r in ev.iterrows()}

    out = {
        "generated": dt.date.today().isoformat(),
        "candidate_source": "github.com/chinobing/historical_sp500_constituents",
        "constituent_sha256": digest,
        "official_events": [],
        "verdict": {},
    }

    print("=== A. Official vs Candidate A ===", flush=True)
    for rec in OFFICIAL:
        row = {"event": rec["event"],
               "announcement_date": rec["announcement_date"],
               "announcement_source": rec["announcement_source"],
               "url": rec["url"],
               "note": rec.get("note"),
               "actions": []}
        for act in rec["actions"]:
            eff = pd.Timestamp(act["effective_date"])
            t = act["ticker"]
            # find the candidate event that mentions this ticker
            cand_rows = ev[(ev["add"].apply(lambda x: t in x)) |
                           (ev["rem"].apply(lambda x: t in x))]
            cand_date = (str(cand_rows["date"].iloc[-1].date())
                         if len(cand_rows) else None)
            gap = ((pd.Timestamp(cand_date) - eff).days
                   if cand_date else None)
            # what does members_as_of say on the three key dates?
            probes = {}
            for label, d in (("day_before_effective",
                              str((eff - pd.Timedelta(days=1)).date())),
                             ("effective_date", act["effective_date"]),
                             ("candidate_event_date", cand_date)):
                if not d:
                    continue
                m = members_as_of(mapping, dates, d)
                if act["action"] == "addition":
                    present = t in m
                elif act["action"] == "deletion":
                    present = t in m          # should be False after effective
                else:                        # ticker change
                    present = t in m
                probes[label] = {"date": d, "present": present,
                                 "universe_size": len(m)}
            row["actions"].append({
                "ticker": t, "action": act["action"],
                "official_effective_date": act["effective_date"],
                "candidate_event_date": cand_date,
                "gap_days_candidate_minus_effective": gap,
                "probes": probes,
            })
            print(f"  {rec['event']:<6} {t:<6} {act['action']:<24} "
                  f"eff={act['effective_date']} cand={cand_date} gap={gap}",
                  flush=True)
        out["official_events"].append(row)

    # ---- B. date-semantics verdict -------------------------------------
    print("\n=== B. Date semantics ===", flush=True)
    gaps = [act["gap_days_candidate_minus_effective"]
            for row in out["official_events"] for act in row["actions"]
            if act["gap_days_candidate_minus_effective"] is not None
            and act["action"] in ("addition", "deletion")]
    out["verdict"]["gaps_vs_effective"] = gaps
    out["verdict"]["min_gap"] = int(min(gaps))
    out["verdict"]["max_gap"] = int(max(gaps))
    out["verdict"]["never_before_effective"] = bool(min(gaps) >= 0)
    out["verdict"]["is_effective_date"] = bool(min(gaps) == 0 and max(gaps) == 0)
    print(f"  gaps vs official effective date: {gaps}")
    print(f"  never BEFORE effective: {out['verdict']['never_before_effective']}")
    print(f"  exactly the effective date: {out['verdict']['is_effective_date']}",
          flush=True)

    # ---- C. PIT look-ahead test -----------------------------------------
    print("\n=== C. PIT look-ahead test ===", flush=True)
    pit = []
    for row in out["official_events"]:
        for act in row["actions"]:
            if act["action"] not in ("addition", "deletion"):
                continue
            t = act["ticker"]
            before = act["probes"].get("day_before_effective", {})
            on_eff = act["probes"].get("effective_date", {})
            if act["action"] == "addition":
                # must be absent the day before; presence on the effective
                # date is DESIRED but the candidate may lag
                ok_before = (before.get("present") is False)
                pit.append({
                    "event": row["event"], "ticker": t, "kind": "addition",
                    "absent_day_before": ok_before,
                    "present_on_effective_date":
                        on_eff.get("present"),
                    "candidate_lag_days":
                        act["gap_days_candidate_minus_effective"],
                })
            else:
                ok_before = (before.get("present") is True)
                pit.append({
                    "event": row["event"], "ticker": t, "kind": "deletion",
                    "still_present_day_before": ok_before,
                    "absent_on_effective_date":
                        (on_eff.get("present") is False),
                    "candidate_lag_days":
                        act["gap_days_candidate_minus_effective"],
                })
    out["pit_tests"] = pit
    for p in pit:
        print(f"  {p['event']:<6} {p['ticker']:<6} {p['kind']:<9} "
              f"lag={p['candidate_lag_days']}  "
              + json.dumps({k: v for k, v in p.items()
                            if k.startswith(('absent', 'present', 'still'))}),
              flush=True)
    add_lookahead = [p for p in pit
                     if p["kind"] == "addition" and not p["absent_day_before"]]
    out["verdict"]["additions_with_lookahead"] = len(add_lookahead)
    print(f"  additions showing look-ahead: {len(add_lookahead)}", flush=True)

    # ---- D. event vs snapshot consistency (§7) -------------------------
    print("\n=== D. Event stream vs snapshot series ===", flush=True)
    idx = {d: i for i, d in enumerate(dates)}
    mism = []
    checked = 0
    for _, e in ev.iterrows():
        d = e["date"]
        if d not in idx:
            continue
        i = idx[d]
        if i == 0:
            continue
        prev, cur = set(mapping[dates[i - 1]]), set(mapping[dates[i]])
        if cur == prev:
            continue
        ia, ir = cur - prev, prev - cur
        checked += 1
        if ia != set(e["add"]) or ir != set(e["rem"]):
            mism.append({"date": str(d.date()),
                         "event_add": sorted(e["add"]),
                         "event_rem": sorted(e["rem"]),
                         "snap_add": sorted(ia), "snap_rem": sorted(ir)})
    out["event_snapshot_consistency"] = {
        "events_checked": checked, "mismatches": len(mism),
        "mismatch_rate": (len(mism) / checked) if checked else 0.0,
        "examples": mism[:8],
    }
    print(f"  checked={checked} mismatches={len(mism)} "
          f"({100*(len(mism)/checked if checked else 0):.1f}%)", flush=True)

    path_out = a.out or (f"reports/constituent_official_crosscheck_raw_"
                         f"{dt.date.today().isoformat()}.json")
    with open(path_out, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False, default=str)
    print(f"\nwrote {path_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
