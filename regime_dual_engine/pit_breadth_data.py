"""
pit_breadth_data.py — Reviewer Task 1: Point-in-Time Market Breadth

Builds TWO breadth series from the same OHLCV panel and compares them:

  current : breadth over the CURRENT-constituent universe (survivorship bias —
            every cached ticker counts over its ENTIRE history).
  pit     : breadth over the point-in-time S&P 500 membership — a ticker only
            counts on dates when it was actually an index member (PIT
            membership from pit_constituents.PitMembership), and only while we
            hold OHLCV for it (residual coverage limitation, reported).

Daily metrics (same as breadth_data.py):
  pct_above_50dma : % of member stocks with close > 50-day SMA (0-100)
  ad_line         : cumulative advance/decline line across member stocks
  n_stocks        : number of members with valid close that day

Comparison report:
  * correlation (Pearson / Spearman) of the two series
  * mean absolute difference of pct_above_50dma
  * regime-label differences (dual-engine daily decision)
  * divergence-difference (BEARISH_BREADTH_DIVERGENCE days)
  * thrust differences (BREADTH_THRUST days)

Output: regime_dual_engine/data/breadth_pit_2016_2025.csv (+ current for ref)
"""
from __future__ import annotations

import os
import sys
import json

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from regime_dual_engine.pit_constituents import PitMembership, cache_dir, norm

_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PIT_OUT = os.path.join(_OUT_DIR, "breadth_pit_2016_2025.csv")
_CUR_OUT = os.path.join(_OUT_DIR, "breadth_current_2016_2025.csv")

EXCLUDE = {"SPY", "QQQ", "IWM"}


def load_panel(end: str = "2025-07-31") -> pd.DataFrame:
    """Wide close panel (index=datetime, cols=tickers) from the cache."""
    frames = {}
    n = 0
    for f in sorted(os.listdir(cache_dir())):
        if not f.endswith(".csv") or "-K1" in f:
            continue
        t = f[:-4]
        if t in EXCLUDE:
            continue
        try:
            df = pd.read_csv(os.path.join(cache_dir(), f), parse_dates=["datetime"])
            if len(df) < 60 or "close" not in df.columns:
                continue
            s = df.set_index("datetime")["close"].astype(float)
            s = s[~s.index.duplicated(keep="last")]
            frames[t] = s
            n += 1
        except Exception:
            continue
    panel = pd.DataFrame(frames).sort_index()
    if end:
        panel = panel[panel.index <= pd.Timestamp(end)]
    return panel


def build_breadth(membership: pd.DataFrame, panel: pd.DataFrame,
                  sma_period: int = 50) -> pd.DataFrame:
    """Breadth series over a boolean membership matrix (dates x tickers).

    membership: DataFrame bool, True = ticker counts on that date.
    panel     : wide closes (dates x tickers). Only columns in `membership`
                AND with data are considered.
    """
    cols = [c for c in membership.columns if c in panel.columns]
    m = membership[cols].astype(bool)
    p = panel[cols]
    sma = p.rolling(sma_period).mean()
    above = (p > sma) & m
    valid = p.notna() & m
    n_valid = valid.sum(axis=1).replace(0, np.nan)
    pct = (above.sum(axis=1) / n_valid * 100.0).fillna(50.0)

    diff = p.diff()
    adv = ((diff > 0) & m).fillna(False).astype(int)
    decl = ((diff < 0) & m).fillna(False).astype(int)
    net = (adv - decl).sum(axis=1)
    ad_line = net.cumsum()

    out = pd.DataFrame({
        "pct_above_50dma": pct.round(3),
        "ad_line": ad_line.round(3),
        "n_stocks": n_valid.fillna(0).astype(int),
    })
    return out


def build_pit_breadth(end: str = "2025-07-31") -> pd.DataFrame:
    """Point-in-time breadth (PIT S&P 500 membership ∩ available OHLCV)."""
    pit = PitMembership()
    panel = load_panel(end=end)
    trading_dates = panel.index
    memb = pit.membership_matrix(trading_dates)
    return build_breadth(memb, panel)


def load_current_universe() -> list[str]:
    """CURRENT index constituents = current S&P 500 (sp500_current.csv) UNION
    current NASDAQ 100 (last snapshot of nasdaq100_thuningxu.csv). This is the
    'current-constituent' universe the reviewer asks to compare against."""
    const_dir = os.path.join(_REPO_ROOT, "data", "constituents")
    cur = pd.read_csv(os.path.join(const_dir, "sp500_current.csv"))
    sp500 = [str(s).strip() for s in cur["Symbol"].astype(str).tolist() if str(s).strip() != "nan"]
    nq = pd.read_csv(os.path.join(const_dir, "nasdaq100_thuningxu.csv"))
    nq100 = nq["tickers"].iloc[-1].split(",")
    return sorted(set(sp500 + nq100))


def build_current_breadth(end: str = "2025-07-31") -> pd.DataFrame:
    """Current-constituent breadth: current S&P500 + NASDAQ100 members count
    over their FULL history (this is the survivorship-biased behavior: a stock
    added in 2023 counts for 2019 as well)."""
    panel = load_panel(end=end)
    univ = [norm(t) for t in load_current_universe()]
    cols = [c for c in panel.columns if c in set(univ)]
    memb = pd.DataFrame(False, index=panel.index, columns=cols)
    memb[cols] = True
    return build_breadth(memb, panel)


def get_breadth(kind: str, rebuild: bool = False, end: str = "2025-07-31") -> pd.DataFrame:
    """Load (or build + cache) a breadth series. kind in {'pit','current'}."""
    path = _PIT_OUT if kind == "pit" else _CUR_OUT
    if os.path.exists(path) and not rebuild:
        df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
        return df
    os.makedirs(_OUT_DIR, exist_ok=True)
    df = build_pit_breadth(end=end) if kind == "pit" else build_current_breadth(end=end)
    df.reset_index().rename(columns={"index": "datetime"}).to_csv(path, index=False)
    return df


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def compare_breadth(current: pd.DataFrame, pit: pd.DataFrame) -> dict:
    """Full current-vs-PIT comparison (Task 1 report)."""
    from scipy import stats as sps
    df = current.join(pit, lsuffix="_cur", rsuffix="_pit").dropna(subset=[
        "pct_above_50dma_cur", "pct_above_50dma_pit"])
    a, b = df["pct_above_50dma_cur"], df["pct_above_50dma_pit"]
    pearson = float(sps.pearsonr(a, b)[0])
    spearman = float(sps.spearmanr(a, b)[0])
    mad = float((a - b).abs().mean())
    n_miss = int((df["n_stocks_pit"] < df["n_stocks_cur"]).sum())

    # ---- daily dual-engine decisions on each breadth ----
    from regime_dual_engine.daily_signals import build_daily_signals
    cur_sig = build_daily_signals(current)
    pit_sig = build_daily_signals(pit)

    common = cur_sig.index.intersection(pit_sig.index)
    reg_agree = (cur_sig.loc[common, "regime_label"] == pit_sig.loc[common, "regime_label"]).mean()
    cur_div = int((cur_sig["veto_flags"].apply(lambda v: "BEARISH_BREADTH_DIVERGENCE" in v)).sum())
    pit_div = int((pit_sig["veto_flags"].apply(lambda v: "BEARISH_BREADTH_DIVERGENCE" in v)).sum())
    cur_thr = int((cur_sig["veto_flags"].apply(lambda v: "BREADTH_THRUST" in v)).sum())
    pit_thr = int((pit_sig["veto_flags"].apply(lambda v: "BREADTH_THRUST" in v)).sum())
    cur_ge5 = int((cur_sig["dist_count"] >= 5).sum())
    pit_ge5 = int((pit_sig["dist_count"] >= 5).sum())  # dist days don't depend on breadth

    # regime label contingency
    from collections import Counter
    labels_cur = cur_sig.loc[common, "regime_label"]
    labels_pit = pit_sig.loc[common, "regime_label"]
    transitions = Counter(
        f"{c}->{p}" for c, p in zip(labels_cur, labels_pit)
        if c != p)

    return {
        "n_common_days": int(len(common)),
        "pearson": round(pearson, 4),
        "spearman": round(spearman, 4),
        "mean_abs_diff_pct": round(mad, 3),
        "mean_abs_diff_pct_p95": round(float(np.percentile((a - b).abs(), 95)), 3),
        "max_abs_diff_pct": round(float((a - b).abs().max()), 3),
        "days_pit_n_below_cur": n_miss,
        "regime_label_agreement_pct": round(float(reg_agree) * 100, 2),
        "regime_label_mismatches": dict(transitions.most_common(8)),
        "divergence_days_current": cur_div,
        "divergence_days_pit": pit_div,
        "thrust_days_current": cur_thr,
        "thrust_days_pit": pit_thr,
        "dist_ge5_days": cur_ge5,  # index-derived, breadth-independent
        "cur_series": current.reset_index().to_dict("records"),
        "pit_series": pit.reset_index().to_dict("records"),
    }


if __name__ == "__main__":
    print("Building current-constituent breadth...", flush=True)
    cur = get_breadth("current", rebuild=True)
    print(f"  {len(cur)} days  ({cur.index.min().date()} -> {cur.index.max().date()})")
    print("Building point-in-time breadth...", flush=True)
    pit = get_breadth("pit", rebuild=True)
    print(f"  {len(pit)} days  ({pit.index.min().date()} -> {pit.index.max().date()})")
    print("Comparing...", flush=True)
    cmp = compare_breadth(cur, pit)
    for k in ["n_common_days", "pearson", "spearman", "mean_abs_diff_pct",
              "mean_abs_diff_pct_p95", "max_abs_diff_pct", "regime_label_agreement_pct",
              "divergence_days_current", "divergence_days_pit",
              "thrust_days_current", "thrust_days_pit", "dist_ge5_days"]:
        print(f"  {k}: {cmp[k]}")
    print("  regime mismatches:", cmp["regime_label_mismatches"])
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "dual_engine_breadth_compare.json"), "w") as f:
        json.dump({k: v for k, v in cmp.items() if not k.endswith("_series")},
                  f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_breadth_compare.json")
