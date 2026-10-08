"""
live_breadth_crosscheck.py — §16 新舊 live breadth 交叉比對

目的
----
新 live breadth 與既有 breadth 的差異，必須能拆解成三種來源之一：

    (1) universe difference   — 成分名單不同
    (2) price-data difference — 價格基準或覆蓋不同
    (3) calculation difference — 公式不同（應為零）

(3) 必須為零：公式是凍結的。若不為零，代表有實作錯誤而非資料差異。
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

from regime_dual_engine import pit_breadth_data as pitb  # noqa: E402
from regime_dual_engine.pit_constituents import PitMembership  # noqa: E402

STALE_FROM = pd.Timestamp("2026-07-01")   # local source's last event


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2026-08-06")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    live_path = os.path.join("data", "live", "breadth", "breadth_live.csv")
    if not os.path.exists(live_path):
        print(f"missing {live_path}; run live_breadth_build.py first")
        return 1
    live = pd.read_csv(live_path, parse_dates=["datetime"]).set_index("datetime")

    # frozen research breadth (read-only)
    frozen = pitb.get_breadth("pit", end=a.as_of)
    panel = pitb.load_panel(end=a.as_of)

    out = {
        "generated": dt.date.today().isoformat(),
        "as_of": a.as_of,
        "frozen_source": "regime_dual_engine/data/breadth_pit_2016_2025.csv",
        "live_source": live_path,
    }

    c = live.index.intersection(frozen.index)
    out["overlap_days"] = int(len(c))

    # ---- (3) calculation difference: identical universe must give 0 -------
    # The FROZEN breadth over the window where BOTH sources agree on the
    # universe is the control. If the formula were the cause of a difference,
    # it would show up here too.
    pm = PitMembership()
    local_map = {}
    for _, r in pm._load().iterrows() if hasattr(pm, "_load") else []:
        pass
    # use the public path
    from regime_dual_engine.pit_constituents import load_snapshots
    cur_snap = load_snapshots("fja")
    cur_map = {r["date"]: set(r["tickers"]) for _, r in cur_snap.iterrows()}
    live_snap = live.attrs  # not used; rebuild from provenance instead

    # ---- (1) universe difference over the stale window --------------------
    seg = c[c >= STALE_FROM]
    out["stale_window"] = {
        "from": str(seg.min().date()) if len(seg) else None,
        "to": str(seg.max().date()) if len(seg) else None,
        "days": int(len(seg)),
    }
    if len(seg):
        rows = []
        for d in seg:
            f = float(frozen.loc[d, "pct_above_50dma"])
            l = float(live.loc[d, "pct_above_50dma"])
            rows.append({"date": str(d.date()),
                         "frozen_pct": f, "live_pct": l, "diff_pp": l - f,
                         "frozen_n": int(frozen.loc[d, "n_stocks"]),
                         "live_n": int(live.loc[d, "n_stocks"])})
        df = pd.DataFrame(rows)
        out["stale_window_stats"] = {
            "mean_abs_diff_pp": float(df["diff_pp"].abs().mean()),
            "max_abs_diff_pp": float(df["diff_pp"].abs().max()),
            "last_diff_pp": float(df["diff_pp"].iloc[-1]),
            "days_identical": int((df["diff_pp"].abs() < 1e-9).sum()),
        }

    # ---- the agreed window: universe identical -> difference must be 0 ----
    ok = c[c < STALE_FROM]
    df_all = []
    for d in ok:
        df_all.append({
            "date": d,
            "fd": float(frozen.loc[d, "pct_above_50dma"]),
            "ld": float(live.loc[d, "pct_above_50dma"])})
    dfa = pd.DataFrame(df_all)
    if len(dfa):
        dfa["diff"] = dfa["ld"] - dfa["fd"]
        out["agreed_window"] = {
            "days": int(len(dfa)),
            "mean_abs_diff_pp": float(dfa["diff"].abs().mean()),
            "max_abs_diff_pp": float(dfa["diff"].abs().max()),
            "pct_days_within_1e_9": float(
                (dfa["diff"].abs() < 1e-9).mean() * 100),
        }

    # ---- decompose the stale-window difference by universe delta ----------
    # the local frozen universe vs the candidate universe, per day
    from research.live_breadth_build import (fetch_snapshots,
                                             load_live_membership,
                                             members_as_of)
    path, digest = fetch_snapshots()
    mapping, snaps = load_live_membership(path)
    ldates = list(mapping)
    uni_rows = []
    for d in seg:
        ts = pd.Timestamp(d)
        a_local = min((x for x in cur_map if x <= ts), default=None)
        a_live = members_as_of(mapping, ldates, str(ts.date()))
        if a_local is None:
            continue
        s_loc = cur_map[a_local]
        uni_rows.append({
            "date": str(ts.date()),
            "local_snap": str(a_local.date()),
            "only_local": sorted(s_loc - set(a_live)),
            "only_candidate": sorted(set(a_live) - s_loc),
        })
    if uni_rows:
        u = pd.DataFrame(uni_rows)
        out["universe_delta"] = {
            "days": int(len(u)),
            "mean_only_local": float(u["only_local"].apply(len).mean()),
            "mean_only_candidate": float(u["only_candidate"].apply(len).mean()),
            "last_day": {
                "date": u["date"].iloc[-1],
                "local_snap": u["local_snap"].iloc[-1],
                "only_local": u["only_local"].iloc[-1],
                "only_candidate": u["only_candidate"].iloc[-1],
            },
        }

    # ---- headline: the last day the two series can be compared -----------
    # The frozen breadth CSV was never promoted past 2026-07-31 (the refresh
    # writes *_refreshed.csv and leaves the original in place), so the
    # comparison must be made on the last COMMON date, not on each series' own
    # tail. Comparing at live's own tail would silently treat "the frozen file
    # has not been promoted" as a data difference.
    common_last = c.max() if len(c) else None
    out["series_tails"] = {
        "frozen_tail": str(frozen.index.max().date()),
        "live_tail": str(live.index.max().date()),
        "last_common": str(common_last.date()) if common_last is not None
        else None,
        "note": ("the frozen research breadth stops at 2026-07-31 because the "
                 "refresh was never promoted; that is the intended state, not "
                 "a data gap"),
    }
    if common_last is not None:
        out["latest_observation"] = {
            "date": str(common_last.date()),
            "frozen_pct": float(frozen.loc[common_last, "pct_above_50dma"]),
            "live_pct": float(live.loc[common_last, "pct_above_50dma"]),
            "frozen_n": int(frozen.loc[common_last, "n_stocks"]),
            "live_n": int(live.loc[common_last, "n_stocks"]),
            "frozen_ad": float(frozen.loc[common_last, "ad_line"]),
            "live_ad": float(live.loc[common_last, "ad_line"]),
        }
        # and the live-only extension the frozen file cannot show
        ext = live.index[live.index > common_last]
        if len(ext):
            out["live_only_extension"] = {
                "from": str(ext.min().date()), "to": str(ext.max().date()),
                "days": int(len(ext)),
                "pct_first": float(live.loc[ext[0], "pct_above_50dma"]),
                "pct_last": float(live.loc[ext[-1], "pct_above_50dma"]),
                "pct_min": float(live.loc[ext, "pct_above_50dma"].min()),
                "pct_max": float(live.loc[ext, "pct_above_50dma"].max()),
            }

    path_out = a.out or (f"reports/live_breadth_crosscheck_"
                         f"{dt.date.today().isoformat()}.json")
    with open(path_out, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False, default=str)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    print(f"\nwrote {path_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
