"""
validation_orthogonality.py — Validation Test 1: Input Orthogonality

Measures whether Engine A (HMM bull probability) and Engine B (breadth)
are redundant or complementary across ~10 years (2016-2025):

  - Pearson / Spearman correlation of levels
  - correlation of daily changes
  - incremental predictive info: future 20-day return ~ HMM + Breadth
    (partial regression — does breadth add info after controlling HMM, and
    vice versa)

Interpretation (levels):
  < 0.65  strong orthogonality
  0.65-0.80 acceptable / investigate
  > 0.80  redundancy warning
  > 0.85  strong redundancy warning

NOTE: HMM is fitted once on the full sample for this structural test (level
correlation is a property of the signals, not of their point-in-time values).
The backtest/validations that matter (ablation, walk-forward) are point-in-time.
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

from regime_dual_engine.config import DualEngineConfig
from regime_dual_engine import hmm_engine
from regime_dual_engine.breadth_data import get_breadth_series

CFG = DualEngineConfig()


def _ols_partial(y, x1, x2):
    """Partial contribution of x1 after controlling x2 (and vice versa).

    Returns dict of standardized coefficients via manual OLS on standardized vars.
    """
    X = np.column_stack([np.ones(len(x1)), x1, x2])
    try:
        beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    except Exception:
        return {"beta_x1": None, "beta_x2": None, "r2": None}
    resid = y - X @ beta
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - np.sum(resid ** 2) / ss_tot if ss_tot > 0 else None
    return {"beta_x1": float(beta[1]), "beta_x2": float(beta[2]), "r2": float(r2)}


def main(breadth=None, breadth_label="current-constituent", out_suffix=""):
    print("=" * 70)
    print(f"  VALIDATION TEST 1 — INPUT ORTHOGONALITY  [{breadth_label} breadth]")
    print("=" * 70)

    # Load data
    spy = pd.read_csv(os.path.join(
        os.path.dirname(_REPO_ROOT), "data", "cache", "equities", "SPY.csv"),
        parse_dates=["datetime"]).set_index("datetime")["close"].astype(float)
    if breadth is None:
        from regime_dual_engine.breadth_data import get_breadth_series
        breadth = get_breadth_series(verbose=False)
    pct_above = breadth["pct_above_50dma"] / 100.0  # 0-1

    # Align on SPY calendar
    df = pd.DataFrame({"spy": spy, "breadth": pct_above}).dropna()
    print(f"  aligned {len(df)} trading days ({df.index.min().date()} -> {df.index.max().date()})")

    # HMM bull probability series (fit once on full sample for structural test)
    state = hmm_engine.fit_state_labels(df["spy"], CFG)
    hmm_series = []
    for i in range(len(state["posteriors"])):
        # reconstruct bull prob per row
        labels = state["labels"]
        bull_state = next((s for s, lbl in labels.items() if lbl == "BULL"), None)
        p = state["posteriors"][i, bull_state] if bull_state is not None else CFG.hmm_neutral_bull_prob
        hmm_series.append(p)
    hmm = pd.Series(hmm_series, index=state["posteriors"].shape[0] and df.index[-len(hmm_series):])

    # Actually: posteriors align with features (which start after vol_window)
    feats_index = df.index[-len(hmm_series):]
    hmm = pd.Series(hmm_series, index=feats_index, name="hmm")
    joined = pd.DataFrame({"hmm": hmm, "breadth": df["breadth"].reindex(feats_index)}).dropna()

    # ---- Level correlations ----
    pearson = joined["hmm"].corr(joined["breadth"], method="pearson")
    spearman = joined["hmm"].corr(joined["breadth"], method="spearman")

    # ---- Daily change correlations ----
    dh = joined["hmm"].diff().dropna()
    db = joined["breadth"].diff().dropna()
    dcorr = dh.corr(db, method="pearson") if len(dh) == len(db) else np.nan

    def _interpret(r):
        if r > 0.85:
            return "STRONG REDUNDANCY WARNING"
        if r > 0.80:
            return "redundancy warning"
        if r > 0.65:
            return "acceptable / investigate"
        return "strong evidence of orthogonality"

    print(f"\n  Pearson level correlation   : {pearson:.3f}  ({_interpret(abs(pearson))})")
    print(f"  Spearman level correlation  : {spearman:.3f}  ({_interpret(abs(spearman))})")
    print(f"  Daily-change correlation    : {dcorr:.3f}")

    # ---- Incremental predictive info ----
    # Future 20-day SPY return
    fut = df["spy"].shift(-20) / df["spy"] - 1.0
    reg = pd.DataFrame({"fut": fut, "hmm": joined["hmm"],
                        "breadth": joined["breadth"]}).dropna()
    reg = reg.iloc[::5]  # non-overlapping-ish sample every 5 days
    partial = _ols_partial(reg["fut"].values, reg["hmm"].values, reg["breadth"].values)
    print(f"\n  Future 20d return ~ HMM + Breadth (OLS, standardized):")
    print(f"    beta_hmm     = {partial['beta_x1']:.4f}")
    print(f"    beta_breadth = {partial['beta_x2']:.4f}")
    print(f"    R2           = {partial['r2']:.4f}")

    # Breadth adds info after HMM? -> partial corr of breadth residual
    print(f"\n  Interpretation: both |beta| > 0 and independent signs indicate")
    print(f"  complementary predictive info. If one beta ~ 0, it is redundant.")

    out = {"pearson": round(pearson, 3), "spearman": round(spearman, 3),
           "daily_change_corr": round(dcorr, 3) if not np.isnan(dcorr) else None,
           "partial": partial, "n": len(joined)}
    out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"dual_engine_orthogonality{out_suffix}.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n  Saved -> reports/dual_engine_orthogonality{out_suffix}.json")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "pit":
        from regime_dual_engine.pit_breadth_data import get_breadth
        pit = get_breadth("pit", rebuild=False)
        main(breadth=pit, breadth_label="PIT (point-in-time S&P500)", out_suffix="_pit")
    else:
        main()
