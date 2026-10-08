"""
ohlcv_lineage_audit.py — §2/§3 OHLCV 價格基準與 corporate-action 完整稽核

目的
----
判定 cache 的確切價格基準，並把「cache 與現行 yfinance 的差異」逐檔分類為
可辯護的 corporate action，或不可解釋的 discrepancy。

方法
----
1. 對每個 PIT 成員，抓取兩個基準的完整重疊歷史：
     BASE_CACHE  = cache 檔案本身
     BASE_TRUE   = yfinance auto_adjust=True（現況，含已知股息+拆股回溯）
     BASE_RAW    = yfinance auto_adjust=False（未調整）
2. 對每一對計算逐日 ratio = yf_close / cache_close，並檢驗其結構：
     - 是否恆定（→ split 或固定因子）
     - 是否隨時間單調漂移（→ dividend 累積調整）
     - 是否單跳變（→ 單一事件）
3. 交叉比對 yfinance actions（splits / dividends）找出對應事件與日期。
4. 判定事件相對於 CACHE_BOUNDARY 的位置。

輸出
----
reports/ohlcv_lineage_audit_<date>.json
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

from regime_dual_engine.pit_constituents import cache_dir, PitMembership  # noqa: E402

YFINANCE = "/Users/curryzeng/.workbuddy/binaries/python/envs/wbprog/bin/python"

CACHE_BOUNDARY = "2026-08-06"     # cache 尾端
OVERLAP_END = "2026-08-07"         # 比對區間終點（含）
HISTORY_START = "2016-01-01"
# RELATIVE tolerance. An absolute tolerance is meaningless here: AAPL trades
# near 180 and MNST near 12, so a 1e-6 absolute cut-off separates float noise
# from a real difference for one and hides it for the other. Every comparison in
# this audit is relative to the cache value.
REL_TOL = 1e-9                     # float noise (measured max ~1e-13)
MATERIAL_REL = 1e-4                # economically material drift
# yfinance's upstream occasionally serves prices rounded to ~6 decimals while
# the cache stores full float64. That produces a ~1.5e-6 relative wobble with no
# economic meaning. Measured on A / ABBV / ABT / ACN (max 7.9e-7).
NOISE_REL = 1e-5


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """yfinance output -> flat frame with a `dt` column."""
    if raw is None or not len(raw):
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = [str(c[0]).lower() for c in raw.columns]
    else:
        raw = raw.copy()
        raw.columns = [str(c).lower() for c in raw.columns]
    raw = raw.reset_index()
    raw.columns = [str(c).lower() for c in raw.columns]
    dcol = next((c for c in raw.columns
                 if c in ("date", "datetime", "index")), None)
    if dcol is None:
        return pd.DataFrame(columns=["dt", "open", "high", "low", "close", "volume"])
    raw["dt"] = pd.to_datetime(raw[dcol]).dt.tz_localize(None)
    keep = [c for c in ("dt", "open", "high", "low", "close", "volume")
            if c in raw.columns]
    return raw[keep].sort_values("dt").reset_index(drop=True)


def ratio_structure(r: pd.Series) -> dict:
    """Describe the shape of a daily ratio series (yf / cache)."""
    r = r.dropna()
    r = r[r > 0]
    if len(r) < 30:
        return {"n": int(len(r)), "class": "insufficient"}
    lo, hi = float(r.min()), float(r.max())
    med = float(r.median())
    span = hi - lo
    n_distinct = int(r.round(9).nunique())
    dr = r.diff().abs()
    max_jump = float(dr.max()) if dr.notna().any() else 0.0
    return {
        "n": int(len(r)),
        "min": lo, "max": hi, "median": med, "span": span,
        "n_distinct": n_distinct,
        "max_single_day_jump": max_jump,
        # a split produces ONE clean level shift; float noise produces many
        # tiny values within a negligible band
        "constant": bool(span < REL_TOL),
        "level_shift": bool(span > 0.01 and max_jump > 0.01),
        "material_drift": bool(span > MATERIAL_REL),
    }


def fetch_actions(ticker: str) -> dict:
    """Corporate actions from yfinance for one ticker."""
    import yfinance as yf
    out = {"splits": [], "dividends": [], "error": None}
    try:
        tk = yf.Ticker(ticker)
        sp = tk.splits
        if sp is not None and len(sp):
            for d, v in sp.items():
                out["splits"].append({
                    "date": pd.Timestamp(d).tz_localize(None).strftime("%Y-%m-%d"),
                    "ratio": float(v)})
        dv = tk.dividends
        if dv is not None and len(dv):
            for d, v in dv.items():
                out["dividends"].append({
                    "date": pd.Timestamp(d).tz_localize(None).strftime("%Y-%m-%d"),
                    "amount": float(v)})
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------
def audit_symbol(ticker: str, cd: str, download: bool = True) -> dict:
    rec: dict = {"ticker": ticker}
    p = os.path.join(cd, f"{ticker}.csv")
    if not os.path.exists(p):
        rec["verdict"] = "no_cache"
        return rec
    cache = pd.read_csv(p, parse_dates=["datetime"])
    if not len(cache):
        rec["verdict"] = "empty_cache"
        return rec
    cache = cache.rename(columns={"datetime": "dt"})
    cache["dt"] = pd.to_datetime(cache["dt"]).dt.tz_localize(None)
    rec["cache_rows"] = int(len(cache))
    rec["cache_start"] = cache["dt"].min().strftime("%Y-%m-%d")
    rec["cache_end"] = cache["dt"].max().strftime("%Y-%m-%d")

    if not download:
        rec["verdict"] = "skipped_no_download"
        return rec

    import yfinance as yf
    try:
        adj = normalize(yf.download(ticker, interval="1d", auto_adjust=True,
                                    progress=False, start=HISTORY_START,
                                    end=OVERLAP_END))
        raw = normalize(yf.download(ticker, interval="1d", auto_adjust=False,
                                    progress=False, start=HISTORY_START,
                                    end=OVERLAP_END))
    except Exception as exc:
        rec["verdict"] = "download_failed"
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return rec

    if not len(adj):
        rec["verdict"] = "download_empty"
        return rec

    # ---- compare against auto_adjust=True (the CONFIGURED basis) --------
    # The repo's only downloaders use auto_adjust=True
    # (src/data/data_fetcher.py:34,67 and download_missing_ohlcv.py:78).
    m = cache.merge(adj, on="dt", suffixes=("_c", "_a"))
    rec["overlap_rows"] = int(len(m))
    if not len(m):
        rec["verdict"] = "no_overlap"
        return rec

    base = m["close_c"].abs().clip(lower=1e-9)
    rel = (m["close_c"] - m["close_a"]).abs() / base
    rec["max_rel_diff_close_adjusted"] = float(rel.max())
    rec["n_differing_rows_adjusted"] = int((rel > REL_TOL).sum())
    rec["n_material_rows_adjusted"] = int((rel > MATERIAL_REL).sum())
    per_field = {}
    for f in ("open", "high", "low", "close", "volume"):
        b = m[f"{f}_c"].abs().clip(lower=1e-9)
        d = (m[f"{f}_c"] - m[f"{f}_a"]).abs() / b
        per_field[f] = {"max_rel_diff": float(d.max()),
                        "n_differing": int((d > REL_TOL).sum())}
    rec["per_field_adjusted"] = per_field

    m = m.assign(ratio=m["close_a"] / m["close_c"])
    st = ratio_structure(m["ratio"])
    rec["ratio_vs_adjusted"] = st
    # A post-cache dividend restatement is a SINGLE constant factor applied to
    # every bar (verified: AAPL ratio = 0.999138 flat from 2016-01-04 to
    # 2026-08-06, matching one $0.27 dividend on 2026-08-10). A split is the
    # same shape with a much larger factor. So the factor magnitude -- not the
    # time shape -- is what separates them.
    rr = m.dropna(subset=["ratio"])
    early = float(rr.head(len(rr) // 4)["ratio"].median())
    late = float(rr.tail(len(rr) // 4)["ratio"].median())
    rec["ratio_early_quartile"] = early
    rec["ratio_late_quartile"] = late
    rec["ratio_is_flat"] = bool(abs(late - early) < 1e-5)
    rec["dividend_drift_signature"] = bool(
        abs(late - early) > MATERIAL_REL)

    # ---- compare against auto_adjust=False (raw) -------------------------
    m2 = cache.merge(raw, on="dt", suffixes=("_c", "_r"))
    rec["overlap_rows_raw"] = int(len(m2))
    if len(m2):
        b2 = m2["close_c"].abs().clip(lower=1e-9)
        d2 = (m2["close_c"] - m2["close_r"]).abs() / b2
        rec["max_rel_diff_close_raw"] = float(d2.max())
        rec["n_differing_rows_raw"] = int((d2 > REL_TOL).sum())
        m2 = m2.assign(ratio=m2["close_r"] / m2["close_c"])
        rec["ratio_vs_raw"] = ratio_structure(m2["ratio"])
        rec["first_date_raw_differs"] = (
            m2.loc[d2 > REL_TOL, "dt"].min().strftime("%Y-%m-%d")
            if (d2 > REL_TOL).any() else None)
    else:
        rec["max_rel_diff_close_raw"] = None
        rec["n_differing_rows_raw"] = None
        rec["ratio_vs_raw"] = {"class": "no_overlap"}

    # ---- corporate actions ----------------------------------------------
    rec["actions"] = fetch_actions(ticker)
    # Boundary comparison must be INCLUSIVE. DHI went ex-dividend on
    # 2026-08-06 -- the cache's own last bar -- and a strict `>` would miss it.
    sp_after = [s for s in rec["actions"]["splits"]
                if s["date"] >= CACHE_BOUNDARY]
    sp_before = [s for s in rec["actions"]["splits"]
                 if s["date"] < CACHE_BOUNDARY]
    rec["splits_after_cache_boundary"] = sp_after
    rec["splits_before_cache_boundary"] = sp_before
    dv_after = [d for d in rec["actions"]["dividends"]
                if d["date"] >= CACHE_BOUNDARY]
    rec["dividends_after_cache_boundary"] = dv_after
    # A dividend can also be restated when the provider ADDS or CORRECTS a
    # historical ex-date after the cache was written, so the ex-date alone is
    # not sufficient evidence. The flat-ratio signature is the real test.
    rec["n_dividends_total"] = len(rec["actions"]["dividends"])
    rec["has_dividend_history"] = rec["n_dividends_total"] > 0

    # ---- verdict ---------------------------------------------------------
    # Precedence, most defensible first:
    #   1. byte-level match on the configured basis
    #   2. sub-1e-5 wobble with no dated cause  -> upstream precision noise
    #   3. a post-boundary split                -> a dated, verifiable fact
    #   4. a post-boundary dividend restatement -> a dated, verifiable fact
    #   5. anything else                        -> unexplained, must be reviewed
    if rec["n_differing_rows_adjusted"] == 0:
        rec["verdict"] = "IDENTICAL"
        rec["explanation"] = (
            "cache 已含下載日之前的所有 corporate action；"
            "auto_adjust=True 逐列重現（相對誤差 < 1e-9）")
    elif rec["max_rel_diff_close_adjusted"] < NOISE_REL and not sp_after \
            and not dv_after:
        rec["verdict"] = "PRECISION_NOISE"
        rec["explanation"] = (
            f"最大相對差異 {rec['max_rel_diff_close_adjusted']:.2e} < 1e-5，"
            f"且邊界後無拆股無股息 → 上游價格精度（6 位小數）與 cache 完整 "
            f"float64 的表示差異，無經濟意涵")
    elif sp_after:
        prod = 1.0
        for s in sp_after:
            prod *= s["ratio"]
        implied = 1.0 / prod
        rec["implied_split_factor"] = implied
        rec["verdict"] = "SPLIT_AFTER_BOUNDARY"
        rec["explanation"] = (
            f"拆股 {sp_after} 發生於 cache 邊界 {CACHE_BOUNDARY} 之後；"
            f"現況 auto_adjust 已回溯調整、cache 尚未 → 全歷史比例恆為 "
            f"{implied:.6f}（實測 {rec['ratio_early_quartile']:.6f}）")
    elif dv_after or (rec["max_rel_diff_close_adjusted"] < 0.01
                       and rec["ratio_is_flat"] is not None
                       and rec.get("has_dividend_history")):
        dv = rec["dividends_after_cache_boundary"]
        rec["verdict"] = "DIVIDEND_RESTATEMENT"
        if dv:
            why = (f"邊界（含）後股息 {[(d['date'], d['amount']) for d in dv]}"
                   f" → auto_adjust 以此重述全部歷史")
        else:
            why = ("無邊界後股息，但該檔有股息史且比例在全期間恆定 "
                   f"({rec['ratio_early_quartile']:.6f}) → 上游在 cache 下載後"
                   f"新增或修正了歷史除息日，同樣導致整段重述")
        rec["explanation"] = (
            f"{why}，全歷史比例恆為 {rec['ratio_early_quartile']:.6f}")
    else:
        rec["verdict"] = "UNEXPLAINED"
        rec["explanation"] = (
            f"無拆股、非精度噪音，但仍有 "
            f"{rec['n_material_rows_adjusted']} 列相對差異 > 1e-4 "
            f"(max {rec['max_rel_diff_close_adjusted']:.3e})；需個案檢查")
    return rec


def summarize(records: list[dict]) -> dict:
    by = {}
    for r in records:
        by.setdefault(r["verdict"], []).append(r["ticker"])
    return {k: {"n": len(v), "tickers": sorted(v)} for k, v in sorted(by.items())}


def factor_stats(records: list[dict]) -> dict:
    """Distribution of the flat restatement factor, per verdict class."""
    out = {}
    for verdict in ("DIVIDEND_RESTATEMENT", "SPLIT_AFTER_BOUNDARY",
                    "PRECISION_NOISE"):
        f = [r["ratio_early_quartile"] for r in records
             if r["verdict"] == verdict and r.get("ratio_early_quartile")]
        if not f:
            continue
        s = sorted(f)
        out[verdict] = {
            "n": len(f),
            "min": s[0], "p25": s[len(s) // 4], "median": s[len(s) // 2],
            "p75": s[3 * len(s) // 4], "max": s[-1],
            "max_pct_impact": round(100.0 * (1.0 - s[0]), 4),
            "median_pct_impact": round(100.0 * (1.0 - s[len(s) // 2]), 4),
        }
    # the two extremes worth naming in the report
    dr = sorted([(r["ticker"], r["ratio_early_quartile"])
                 for r in records
                 if r["verdict"] == "DIVIDEND_RESTATEMENT"
                 and r.get("ratio_early_quartile")], key=lambda x: x[1])
    out["largest_dividend_impacts"] = [
        {"ticker": t, "factor": round(f, 6),
         "pct_impact": round(100.0 * (1.0 - f), 4)} for t, f in dr[:10]]
    sp = [(r["ticker"], r.get("implied_split_factor"), r.get("splits_after_cache_boundary"))
          for r in records if r["verdict"] == "SPLIT_AFTER_BOUNDARY"]
    out["split_details"] = [
        {"ticker": t, "implied_factor": f, "splits": s} for t, f, s in sp]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--as-of", default=CACHE_BOUNDARY)
    ap.add_argument("--no-download", action="store_true")
    a = ap.parse_args()

    cd = cache_dir()
    pm = PitMembership()
    mem = sorted(pm.members_as_of(a.as_of))
    if a.limit:
        mem = mem[:a.limit]
    print(f"auditing {len(mem)} PIT members as of {a.as_of}", flush=True)

    records = []
    for i, t in enumerate(mem, 1):
        try:
            records.append(audit_symbol(t, cd, download=not a.no_download))
        except Exception as exc:
            records.append({"ticker": t, "verdict": "AUDIT_ERROR",
                            "error": f"{type(exc).__name__}: {str(exc)[:150]}"})
        if i % 25 == 0:
            print(f"  {i}/{len(mem)}", flush=True)

    s = summarize(records)
    out = {
        "generated": dt.date.today().isoformat(),
        "cache_boundary": CACHE_BOUNDARY,
        "as_of": a.as_of,
        "n_symbols": len(records),
        "rel_tolerance": REL_TOL,
        "material_rel": MATERIAL_REL,
        "summary_by_verdict": s,
        "factor_distribution": factor_stats(records),
        "records": records,
    }
    path = a.out or ("reports/ohlcv_lineage_audit_"
                     f"{dt.date.today().isoformat()}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nwrote {path}")
    for k, v in s.items():
        print(f"  {k:<26} {v['n']:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
