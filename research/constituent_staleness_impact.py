"""
constituent_staleness_impact.py — §6 constituent 過期的實際影響量測

問題
----
PIT constituent snapshots 止於 2026-06-30，而 OHLCV 已到 2026-08-06（甚至
來源可到 2026-10-02）。`members_as_of` 無條件 forward-fill，因此最近 26 個
交易日的 breadth 是用 **37 天前的宇宙**算出來的。

那個過期宇宙對 breadth 影響多大？這決定架構選項的權重。

方法
----
三個層級，全部 PIT-safe（只用 <= 目標日的資料）：

  T1  邊際影響（最壞情況的量級）
      把最後 N 個成分（按 50DMA 距離排序）從宇宙中移除，看 pct 上/下限。
      這回答「如果宇宙差很多，breadth 會差多少」。

  T2 歷史類比（實際發生過的幅度）
      對研究窗內每個 snapshot 邊界，量測「若成分名單凍結 37/60/90 天，
      pct_above_50dma 會偏移多少」。用真實的歷史成分變動做樣本。

  T3 現況（2026-06-30 之後）
      2026-07-01 → 2026-08-06 期間，用 2026-06-30 名單算出的 pct，
      與「若用當時真實名單」的差距 —— 但真實名單不可得，故以 T2 的
      分布作為參考區間。
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

from regime_dual_engine.pit_constituents import PitMembership, cache_dir  # noqa: E402
from regime_dual_engine import pit_breadth_data as pitb  # noqa: E402

STALE_DATES = ["2026-07-01", "2026-07-15", "2026-08-06"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default="2026-08-06")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cd = cache_dir()
    pm = PitMembership()

    print("loading panel ...", flush=True)
    panel = pitb.load_panel(end=a.as_of)
    print(f"  panel {panel.shape}", flush=True)

    out = {"generated": dt.date.today().isoformat(),
           "as_of": a.as_of,
           "last_snapshot": str(pd.Timestamp(pm.dates[-1]).date()),
           "panel_shape": list(panel.shape)}

    # ---- T1: marginal impact of dropping N names -----------------------
    print("T1: marginal impact of universe perturbation ...", flush=True)
    last = panel.index[-1]
    mem = pm.members_as_of(a.as_of)
    px = panel.loc[:last]
    sma = px.rolling(50, min_periods=50).mean()
    above = (px > sma).iloc[-1]
    valid = px.notna().iloc[-1]
    mem_v = [t for t in mem if t in above.index and bool(valid.get(t, False))]
    a_mem = above.loc[mem_v]
    n = len(mem_v)
    base_pct = float(a_mem.mean() * 100.0)
    # rank by |close - 50DMA| / 50DMA  -> closest to the boundary first
    dist = ((px[mem_v].iloc[-1] - sma[mem_v].iloc[-1]).abs()
            / sma[mem_v].iloc[-1].iloc[-1]).sort_values()
    t1 = {"n_members": n, "pct_at_as_of": base_pct,
          "drop_k_nearest_boundary": {}}
    for k in (5, 10, 20, 30, 50):
        drop = dist.index[:k]
        remain = [t for t in mem_v if t not in set(drop)]
        p = float(above.loc[remain].mean() * 100.0)
        t1["drop_k_nearest_boundary"][str(k)] = {
            "pct": round(p, 4), "delta_pp": round(p - base_pct, 4)}
    out["T1_marginal"] = t1
    print(f"  base pct={base_pct:.4f}  T1={t1['drop_k_nearest_boundary']}",
          flush=True)

    # ---- T2: historical analogue of a frozen universe -------------------
    # DESIGN NOTE (a bug this file previously had, recorded so it is not
    # reintroduced): the comparison window must be the HORIZON ITSELF. Slicing
    # the panel from 2016 and averaging over every day since then dilutes the
    # carry-forward effect with two decades of unrelated membership churn, and
    # produces a mean that DECREASES as the horizon grows -- an impossibility.
    # Only days inside (snapshot, snapshot + horizon] measure the question.
    print("T2: historical analogue of carrying a stale universe ...", flush=True)
    snaps = [pd.Timestamp(d) for d in pm.dates]
    snaps = [s for s in snaps if pd.Timestamp("2018-01-01") <= s
             <= pd.Timestamp(a.as_of)]
    rows = []
    for s in snaps:
        for horizon in (37, 60, 90):
            tgt = s + pd.Timedelta(days=horizon)
            if tgt > panel.index[-1]:
                continue
            win = panel.loc[(panel.index > s) & (panel.index <= tgt)]
            if len(win) < 5:
                continue
            # true: membership varies day by day
            m_true = pm.membership_matrix(win.index)
            b_true = pitb.build_breadth(m_true, win)
            # frozen: every day uses the membership in effect at s
            m_frozen = pd.DataFrame(False, index=win.index, columns=win.columns)
            mem_s = pm.members_as_of(str(s.date()))
            cols = [c for c in mem_s if c in win.columns]
            m_frozen[cols] = True
            b_frozen = pitb.build_breadth(m_frozen, win)
            c = b_true.index.intersection(b_frozen.index)
            if not len(c):
                continue
            d = (b_frozen.loc[c, "pct_above_50dma"].astype(float)
                 - b_true.loc[c, "pct_above_50dma"].astype(float))
            # Count membership churn by TICKER NAME. Two earlier defects are
            # recorded here so they are not reintroduced: (a) comparing
            # positional indices across two arms whose column spaces differ
            # reports almost every name as changed; (b) `m_true` is built over
            # the FULL panel columns while `win` is a subset, so indices must
            # be resolved against each frame's OWN column list.
            wcols = list(win.columns)
            base_set = {wcols[i] for i in np.where(m_frozen.values[0])[0]}
            tcols = list(m_true.columns)
            n_changed = max(
                len(base_set.symmetric_difference(
                    {tcols[i] for i in np.where(m_true.loc[day].values)[0]}))
                for day in c)
            rows.append({"snapshot": str(s.date()), "horizon_days": horizon,
                         "days": int(len(c)),
                         "n_members_changed": int(n_changed),
                         "max_abs_diff_pp": float(d.abs().max()),
                         "mean_abs_diff_pp": float(d.abs().mean()),
                         "final_diff_pp": float(d.iloc[-1])})
    t2 = pd.DataFrame(rows)
    if len(t2):
        summ = {}
        for h, g in t2.groupby("horizon_days"):
            summ[str(h)] = {
                "n_snapshots": int(len(g)),
                "median_max_abs_diff_pp": float(g["max_abs_diff_pp"].median()),
                "p90_max_abs_diff_pp": float(g["max_abs_diff_pp"].quantile(0.9)),
                "max_observed_pp": float(g["max_abs_diff_pp"].max()),
                "median_mean_abs_diff_pp": float(g["mean_abs_diff_pp"].median()),
                "p90_mean_abs_diff_pp": float(g["mean_abs_diff_pp"].quantile(0.9)),
                "median_abs_final_diff_pp": float(g["final_diff_pp"].abs().median()),
                "p90_abs_final_diff_pp": float(g["final_diff_pp"].abs().quantile(0.9)),
                "max_abs_final_diff_pp": float(g["final_diff_pp"].abs().max()),
                "median_members_changed": float(g["n_members_changed"].median()),
                "p90_members_changed": float(g["n_members_changed"].quantile(0.9)),
                "worst_snapshot": g.loc[g["max_abs_diff_pp"].idxmax(), "snapshot"],
            }
        out["T2_historical_analogue"] = summ
        # sanity: the effect must grow with the horizon. If it does not, the
        # measurement is still contaminated and must not be reported.
        ms = [summ[str(h)]["median_mean_abs_diff_pp"]
              for h in (37, 60, 90) if str(h) in summ]
        out["T2_monotonicity_check"] = {
            "median_mean_by_horizon": {
                str(h): summ[str(h)]["median_mean_abs_diff_pp"]
                for h in (37, 60, 90) if str(h) in summ},
            "increases_with_horizon": bool(
                len(ms) == 3 and ms[0] < ms[1] < ms[2]),
        }
        print(f"  monotonicity: {out['T2_monotonicity_check']}", flush=True)
        for h, v in summ.items():
            print(f"    {h}d: n={v['n_snapshots']}  members changed median="
                  f"{v['median_members_changed']:.0f} p90={v['p90_members_changed']:.0f}"
                  f"  |  pct mean diff median={v['median_mean_abs_diff_pp']:.3f}pp"
                  f" p90={v['p90_mean_abs_diff_pp']:.3f}pp"
                  f"  final p90={v['p90_abs_final_diff_pp']:.3f}pp", flush=True)
    else:
        out["T2_historical_analogue"] = {}

    # ---- T3: current stale segment --------------------------------------
    # NOTE: the stale segment alone cannot be re-breadth-ed -- build_breadth
    # needs 50 bars of 50DMA warm-up. The segment is measured with the panel
    # sliced to the segment START, and n_stocks / snapshot are read off the
    # membership matrix directly rather than from a breadth frame.
    print("T3: current stale segment ...", flush=True)
    seg_start = pd.Timestamp(out["last_snapshot"]) + pd.Timedelta(days=1)
    seg = panel.loc[seg_start:]
    if len(seg) > 0:
        m = pm.membership_matrix(seg.index)
        # rebuild the frozen membership explicitly so the count is explicit
        mem_last = pm.members_as_of(out["last_snapshot"])
        cols = [c for c in mem_last if c in seg.columns]
        out["T3_current_segment"] = {
            "from": seg.index.min().strftime("%Y-%m-%d"),
            "to": seg.index.max().strftime("%Y-%m-%d"),
            "trading_days": int(len(seg)),
            "snapshot_used": out["last_snapshot"],
            "carry_forward_days": int((seg.index[-1]
                                       - pd.Timestamp(out["last_snapshot"])).days),
            "frozen_members": len(cols),
            "members_with_data": int(m.loc[seg.index[-1]].sum()),
            "note": ("this segment cannot be re-breadth-ed in isolation "
                     "(50DMA warm-up needs 50 bars, the segment has "
                     f"{len(seg)}); its pct_above_50dma was produced by "
                     "breadth_refresh using the full panel, so it IS "
                     "computable in production -- what is unknown is the "
                     "TRUE universe for these dates"),
        }
        print(f"  {out['T3_current_segment']}", flush=True)

    path = a.out or (f"reports/constituent_staleness_impact_"
                     f"{dt.date.today().isoformat()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
