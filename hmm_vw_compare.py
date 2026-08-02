"""
hmm_vw_compare.py — 比較不同 vol_window 的 HMM 分離度
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd
from config import Config
from src.data.data_fetcher import fetch_daily
from src.agents.regime_agent import compute_features, fit_hmm, decode_and_label

warnings.filterwarnings("ignore")

cfg = Config()
df = fetch_daily("SPY", period="2y")
close = df["close"].astype(float)

print(f"SPY data: {len(df)} rows")
print(f"{'VW':>3} | {'BULL ret':>10} {'BULL vol':>10} {'BULL%':>6} | "
      f"{'BEAR ret':>10} {'BEAR vol':>10} {'BEAR%':>6} | "
      f"{'SIDE ret':>10} {'SIDE vol':>10} {'SIDE%':>6} | {'spread':>8} | {'trend':>5}")
print("-" * 120)

for vw in [3, 5, 7, 10, 15, 20, 30]:
    feats = compute_features(close, vw)
    if len(feats) < cfg.hmm_min_obs:
        continue
    try:
        model = fit_hmm(feats.values, cfg)
        states, stats, labels, trending, spread = decode_and_label(model, feats, cfg)
    except Exception as e:
        print(f"  VW={vw}: ERROR {e}")
        continue

    valid = {s: v for s, v in stats.items() if v["days"] > 0}
    order = sorted(valid, key=lambda s: valid[s]["mean_return"])
    lo, mid, hi = order[0], order[1], order[-1]

    bull = valid[hi]
    bear = valid[lo]
    side = valid[mid]

    print(f"{vw:>3} | {bull['mean_return']:>+10.4f} {bull['mean_vol']:>10.4f} {bull['pct']:>5.1%} | "
          f"{bear['mean_return']:>+10.4f} {bear['mean_vol']:>10.4f} {bear['pct']:>5.1%} | "
          f"{side['mean_return']:>+10.4f} {side['mean_vol']:>10.4f} {side['pct']:>5.1%} | "
          f"{spread:>8.4f} | {'Y' if trending else 'N':>5}")

# Also check covariance type
print("\n\nCovariance type comparison (VW=10):")
feats = compute_features(close, 10)
for cov_type in ["full", "diag", "spherical", "tied"]:
    cfg2 = Config()
    cfg2.hmm_covariance_type = cov_type
    try:
        model = fit_hmm(feats.values, cfg2)
        states, stats, labels, trending, spread = decode_and_label(model, feats, cfg2)
    except Exception as e:
        print(f"  {cov_type}: ERROR {e}")
        continue

    valid = {s: v for s, v in stats.items() if v["days"] > 0}
    order = sorted(valid, key=lambda s: valid[s]["mean_return"])
    lo, mid, hi = order[0], order[1], order[-1]
    bull = valid[hi]
    bear = valid[lo]
    side = valid[mid]

    # Check persistence
    transmat = model.transmat_
    sorted_idx = sorted(range(model.n_components), key=lambda s: stats.get(s, {}).get("mean_return", 0))
    sorted_trans = transmat[np.ix_(sorted_idx, sorted_idx)]
    bear_persist = sorted_trans[0, 0]
    side_persist = sorted_trans[1, 1]
    bull_persist = sorted_trans[2, 2]

    print(f"  {cov_type:>10}: spread={spread:.4f} | BULL={bull['pct']:.1%}(persist={bull_persist:.3f}) "
          f"BEAR={bear['pct']:.1%}(persist={bear_persist:.3f}) "
          f"SIDE={side['pct']:.1%}(persist={side_persist:.3f})")
