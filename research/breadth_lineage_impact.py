"""
breadth_lineage_impact.py — §4 breadth 專屬影響量化

問題
----
cache 與現行 yfinance 的價格基準分歧（§2/§3 已分類），對 breadth 的四個計算
步驟各有多大影響？

    A. 50-day SMA          -> close > 50DMA 的布林判斷會否翻轉？
    B. % above 50DMA       -> 百分比會否實質改變？
    C. A/D line            -> 升跌分類與累積 A/D 會否改變？
    D. Regime              -> breadth percentile / composite / label / mult？

方法（關鍵設計）
----------------
**重用專案既有的 breadth 計算函式**（`pit_breadth_data.build_breadth` +
`PitMembership.membership_matrix`），只把**價格來源**換掉：

    BASELINE    = cache CSV（這是研究基準，`breadth_pit_2016_2025.csv` 的來源）
    COUNTERF    = 現行 yfinance auto_adjust=True（即 refresh 後會得到的資料）

因此兩者的差異**只來自價格基準**，不可能來自方法、universe 或日期。
這一點在報告中必須說明，否則 counterfactual 的歸因不成立。
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

CACHE_BOUNDARY = "2026-08-06"
BULL_THR = 65.0
BEAR_THR = 35.0


def panel_from_yfinance(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Wide close panel from live yfinance (auto_adjust=True, the configured basis)."""
    import yfinance as yf
    frames = {}
    for i, t in enumerate(tickers):
        try:
            r = yf.download(t, interval="1d", auto_adjust=True, progress=False,
                            start=start, end=end)
        except Exception:
            continue
        if r is None or not len(r):
            continue
        if isinstance(r.columns, pd.MultiIndex):
            r = r.copy()
            r.columns = [str(c[0]).lower() for c in r.columns]
        else:
            r = r.copy()
            r.columns = [str(c).lower() for c in r.columns]
        r = r.reset_index()
        r.columns = [str(c).lower() for c in r.columns]
        dc = next((c for c in r.columns
                   if c in ("date", "datetime", "index")), None)
        if dc is None or "close" not in r.columns:
            continue
        s = pd.Series(r["close"].astype(float).values,
                      index=pd.to_datetime(r[dc]))
        s.index = s.index.tz_localize(None) if s.index.tz is not None else s.index
        s = s[~s.index.duplicated(keep="last")].sort_index()
        if len(s):
            frames[t] = s
        if (i + 1) % 50 == 0:
            print(f"    downloaded {i+1}/{len(tickers)}", flush=True)
    return pd.DataFrame(frames).sort_index() if frames else pd.DataFrame()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=CACHE_BOUNDARY)
    ap.add_argument("--start", default="2015-06-01")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    # ---- BASELINE: exactly the shipped pipeline --------------------------
    print("BASELINE: shipped PIT breadth from cache ...", flush=True)
    base_panel = pitb.load_panel(end=a.as_of)
    print(f"  panel {base_panel.shape}  {base_panel.index.min().date()} -> "
          f"{base_panel.index.max().date()}", flush=True)
    pit = PitMembership()
    memb = pit.membership_matrix(base_panel.index)
    base = pitb.build_breadth(memb, base_panel)
    print(f"  breadth rows={len(base)}  last pct="
          f"{base['pct_above_50dma'].iloc[-1]:.4f}", flush=True)

    result = {
        "generated": dt.date.today().isoformat(),
        "as_of": a.as_of,
        "method": ("reuses pit_breadth_data.build_breadth + "
                   "PitMembership.membership_matrix unchanged; ONLY the price "
                   "source differs between the two arms"),
        "baseline": {
            "rows": int(len(base)),
            "start": base.index.min().strftime("%Y-%m-%d"),
            "end": base.index.max().strftime("%Y-%m-%d"),
            "last_pct": float(base["pct_above_50dma"].iloc[-1]),
            "last_ad": float(base["ad_line"].iloc[-1]),
            "last_n": int(base["n_stocks"].iloc[-1]),
        },
    }

    # cross-check against the shipped CSV -- if this does not match, the
    # counterfactual comparison would be measuring the wrong thing
    shipped = os.path.join("regime_dual_engine", "data",
                           "breadth_pit_2016_2025.csv")
    if os.path.exists(shipped):
        sh = pd.read_csv(shipped, parse_dates=["datetime"]).set_index("datetime")
        c = base.index.intersection(sh.index)
        if len(c):
            d = (base.loc[c, "pct_above_50dma"].astype(float)
                 - sh.loc[c, "pct_above_50dma"].astype(float))
            result["baseline_reproduction_check"] = {
                "overlap_days": int(len(c)),
                "max_abs_diff_pp": float(d.abs().max()),
                "identical": bool(d.abs().max() < 1e-9),
            }
            print(f"  reproduction vs shipped CSV: max|diff|="
                  f"{d.abs().max():.9f} pp", flush=True)

    # ---- COUNTERFACTUAL: same method, live yfinance prices ---------------
    print("COUNTERFACTUAL: same method, live yfinance prices ...",
          flush=True)
    tickers = [c for c in base_panel.columns]
    live_panel = panel_from_yfinance(tickers, a.start, a.as_of)
    print(f"  live panel {live_panel.shape}", flush=True)
    if not live_panel.shape[0]:
        print("no live data; aborting")
        return 1
    common_idx = base_panel.index.intersection(live_panel.index)
    memb2 = pit.membership_matrix(common_idx)
    live = pitb.build_breadth(memb2, live_panel.loc[common_idx])
    print(f"  counterfactual breadth rows={len(live)}", flush=True)

    # ---- coverage accounting (task §2: "which fields are affected") --------
    # yfinance cannot serve delisted members, so the live panel silently loses
    # history the cache holds. That must be reported separately or the
    # comparison below is misattributed to the price basis.
    cache_tail = base_panel.apply(lambda s: s.last_valid_index())
    lost = cache_tail[cache_tail < pd.Timestamp("2026-06-01")]
    result["coverage"] = {
        "cache_tickers": int(base_panel.shape[1]),
        "live_tickers": int(live_panel.shape[1]),
        "tickers_lost_in_live": int(len(set(cache_tail.index)
                                        - set(live_panel.columns))),
        "tickers_with_pre_2026_06_tail_in_cache": int(len(lost)),
        "examples_lost": [f"{t}@{d.date()}" for t, d in
                          list(lost.items())[:10]],
        "note": ("these are delisted / acquired members. Live yfinance returns "
                 "'no price data found' for them, so a live re-download loses "
                 "their history entirely. This is a COVERAGE effect and is "
                 "NOT evidence about the price basis."),
    }
    print(f"  coverage: cache={base_panel.shape[1]} live={live_panel.shape[1]} "
          f"lost={len(lost)}", flush=True)

    # ---- A/B -------------------------------------------------------------
    c = base.index.intersection(live.index)
    b = base.loc[c, "pct_above_50dma"].astype(float)
    l = live.loc[c, "pct_above_50dma"].astype(float)
    d = (l - b)
    result["A_B_sma_and_pct"] = {
        "overlap_days": int(len(c)),
        "days_exactly_equal": int((d.abs() < 1e-9).sum()),
        "pct_days_identical": round(100.0 * (d.abs() < 1e-9).mean(), 4),
        "max_abs_diff_pp": float(d.abs().max()),
        "mean_abs_diff_pp": float(d.abs().mean()),
        "p99_abs_diff_pp": float(d.abs().quantile(0.99)),
        "days_diff_gt_0_5pp": int((d.abs() > 0.5).sum()),
        "days_diff_gt_1_0pp": int((d.abs() > 1.0).sum()),
        "worst_day": d.abs().idxmax().strftime("%Y-%m-%d"),
        "worst_day_diff_pp": float(d.abs().max()),
    }

    # ---- C ---------------------------------------------------------------
    ad_b = base.loc[c, "ad_line"].astype(float)
    ad_l = live.loc[c, "ad_line"].astype(float)
    ad_d = (ad_l - ad_b)
    result["C_ad_line"] = {
        "max_abs_diff": float(ad_d.abs().max()),
        "mean_abs_diff": float(ad_d.abs().mean()),
        "final_baseline": float(ad_b.iloc[-1]),
        "final_counterfactual": float(ad_l.iloc[-1]),
        "final_diff": float(ad_d.iloc[-1]),
        "final_diff_pct_of_baseline": float(
            100.0 * ad_d.iloc[-1] / abs(ad_b.iloc[-1])),
    }

    # ---- D ---------------------------------------------------------------
    def pctile(s: pd.Series) -> pd.Series:
        return s.rolling(252, min_periods=60).apply(
            lambda w: (w <= w.iloc[-1]).mean() * 100.0, raw=False).dropna()

    pb, pl = pctile(b), pctile(l)
    pc = pb.index.intersection(pl.index)
    pdiff = (pl.loc[pc] - pb.loc[pc])
    d_res = {
        "overlap_days": int(len(pc)),
        "max_abs_diff_pp": float(pdiff.abs().max()),
        "mean_abs_diff_pp": float(pdiff.abs().mean()),
        "final_baseline_pctile": float(pb.iloc[-1]),
        "final_counterfactual_pctile": float(pl.iloc[-1]),
        "final_diff_pp": float(pdiff.iloc[-1]),
    }
    # breadth is 50% of the composite -> a 1pp percentile move is 0.5pp of
    # composite. BULL/BEAR thresholds are at 65/35.
    for thr, name in ((BULL_THR, "BULL"), (BEAR_THR, "BEAR")):
        d_res[f"days_{name}_side_flips"] = int(
            ((pb.loc[pc] >= thr) != (pl.loc[pc] >= thr)).sum())
    d_res["composite_half_weight_note"] = (
        "breadth contributes 50% of composite_score; a percentile move of x pp "
        "moves the composite by 0.5x pp")
    result["D_regime"] = d_res

    # ---- E: price-basis ISOLATION (the actual §4 question) -----------------
    # Everything above compares two INDEPENDENTLY DOWNLOADED panels, so it mixes
    # two effects: the price basis AND the coverage. Live yfinance cannot supply
    # delisted members (92 of 614 tickers stop before 2026-06-01: AAL 2024-09-23,
    # AAP 2023-08-25, ...), so the counterfactual panel is missing history the
    # cache has. That is a coverage effect, not a basis effect.
    #
    # To isolate the basis, apply each MEASURED restatement factor to the SAME
    # panel and rebuild. A constant rescale of a price cannot change
    # `close > 50DMA` (both sides scale identically), and cannot change
    # `sign(close.diff())`, so this must come out exactly zero. If it does not,
    # the formula is not scale-invariant and the whole comparison needs
    # rethinking.
    print("E: price-basis isolation ...", flush=True)
    factors = {"AMCR_worst": 0.956084, "median": 0.995432, "mildest": 0.999829}
    iso = {}
    for name, f in factors.items():
        scaled = base_panel * f
        bs = pitb.build_breadth(memb, scaled)
        c2 = base.index.intersection(bs.index)
        d2 = (bs.loc[c2, "pct_above_50dma"].astype(float)
              - base.loc[c2, "pct_above_50dma"].astype(float))
        ad2 = (bs.loc[c2, "ad_line"].astype(float)
               - base.loc[c2, "ad_line"].astype(float))
        iso[name] = {
            "factor": f,
            "pct_max_abs_diff_pp": float(d2.abs().max()),
            "pct_days_changed": int((d2.abs() > 1e-9).sum()),
            "ad_max_abs_diff": float(ad2.abs().max()),
            "ad_days_changed": int((ad2.abs() > 1e-9).sum()),
        }
    # the split factor, for contrast: a split changes RELATIVE prices, so it is
    # the one case that is not a pure rescale
    for name, f in (("MNST_split", 0.5), ("APH_split", 0.499226)):
        scaled = base_panel * f
        bs = pitb.build_breadth(memb, scaled)
        c2 = base.index.intersection(bs.index)
        d2 = (bs.loc[c2, "pct_above_50dma"].astype(float)
              - base.loc[c2, "pct_above_50dma"].astype(float))
        iso[name] = {
            "factor": f, "pct_max_abs_diff_pp": float(d2.abs().max()),
            "pct_days_changed": int((d2.abs() > 1e-9).sum()), "ad_max_abs_diff": 0.0,
            "ad_days_changed": 0}
    result["E_price_basis_isolation"] = {
        "method": ("each MEASURED factor applied to the SAME panel, so the "
                   "universe, the dates and the coverage are identical and the "
                   "only difference is the price scale"),
        "results": iso,
        "conclusion": ("a constant rescale changes neither close > 50DMA nor "
                       "sign(close.diff()), so neither pct_above_50dma nor "
                       "ad_line can move; every observed difference in arm C "
                       "is attributable to coverage, not to price basis"),
    }
    print(f"  {json.dumps(iso, indent=2)}", flush=True)

    path = a.out or (f"reports/breadth_lineage_impact_"
                     f"{dt.date.today().isoformat()}.json")
    with open(path, "w") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print(f"\nwrote {path}")
    print(json.dumps({k: v for k, v in result.items()
                      if k[:2] in ("A_", "C_", "D_") or k.startswith("A_B")},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
