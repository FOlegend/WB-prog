"""
hmm_diagnostic.py — HMM 參數診斷與重校準工具

在 SPY 上：
  1. 印出 3 個 HMM state 的 mean return / mean volatility / 占比
  2. 檢查 state separation（BULL > 0, BEAR < 0, spread 足夠）
  3. Grid search min_bull_ret / min_bear_ret / min_regime_spread / hmm_vol_window

用法：
  python hmm_diagnostic.py                 # 預設診斷
  python hmm_diagnostic.py --grid          # grid search
"""
from __future__ import annotations
import warnings
import argparse
import itertools
import numpy as np
import pandas as pd
from config import Config
from src.data.data_fetcher import fetch_daily
from src.agents.regime_agent import compute_features, fit_hmm, decode_and_label

warnings.filterwarnings("ignore")


def diagnose(cfg: Config):
    """印出 HMM 3 個 state 的統計量，檢查分離度。"""
    market_idx = getattr(cfg, "regime_market_index", "SPY")
    print(f"=== HMM 診斷（{market_idx}）===")
    print(f"  vol_window={cfg.hmm_vol_window}, n_states={cfg.hmm_n_states}, "
          f"covariance={cfg.hmm_covariance_type}, n_iter={cfg.hmm_n_iter}")
    print(f"  當前門檻: min_bull_ret={cfg.min_bull_ret}, "
          f"min_bear_ret={cfg.min_bear_ret}, min_regime_spread={cfg.min_regime_spread}")

    df = fetch_daily(market_idx, period="2y")
    if len(df) < cfg.hmm_min_obs:
        print(f"  ⚠️ 資料不足（{len(df)} < {cfg.hmm_min_obs}）")
        return

    close = df["close"].astype(float)
    feats = compute_features(close, cfg.hmm_vol_window)
    print(f"  資料：{len(feats)} 個交易日（{feats.index[0]} ~ {feats.index[-1]}）")
    print(f"  Feature 統計：return mean={feats['return'].mean():.4f}%, "
          f"std={feats['return'].std():.4f}%, "
          f"volatility mean={feats['volatility'].mean():.4f}%\n")

    model = fit_hmm(feats.values, cfg)
    states, stats, labels, trending, spread = decode_and_label(model, feats, cfg)

    # Print state statistics
    print("  ┌─────────┬──────────────┬──────────────┬──────────┬────────┬──────────┐")
    print("  │ State   │ Mean Return  │ Mean Vol     │ Days     │ %      │ Label    │")
    print("  ├─────────┼──────────────┼──────────────┼──────────┼────────┼──────────┤")
    for s in sorted(stats.keys()):
        v = stats[s]
        lbl = labels.get(s, "?")
        mr = v["mean_return"]
        mv = v["mean_vol"]
        n = v["days"]
        p = v["pct"]
        print(f"  │ State {s} │ {mr:>+12.4f} │ {mv:>+12.4f} │ {n:>8} │ {p:>5.1%} │ {lbl:<8} │")
    print("  └─────────┴──────────────┴──────────────┴──────────┴────────┴──────────┘")

    print(f"\n  Spread (BULL - BEAR): {spread:.4f}")
    print(f"  Trending: {trending}")

    # Separation check
    print("\n  === 分離度檢查 ===")
    valid = {s: v for s, v in stats.items() if v["days"] > 0}
    order = sorted(valid, key=lambda s: valid[s]["mean_return"])
    lo, mid, hi = order[0], order[1], order[-1]

    lo_ret = valid[lo]["mean_return"]
    hi_ret = valid[hi]["mean_return"]
    mid_ret = valid[mid]["mean_return"]

    checks = [
        (f"BULL mean return > 0 ({hi_ret:+.4f}%)", hi_ret > 0),
        (f"BEAR mean return < 0 ({lo_ret:+.4f}%)", lo_ret < 0),
        (f"BULL - BEAR spread > 0.05 ({spread:.4f})", spread > 0.05),
        (f"BULL - BEAR spread > 0.10 ({spread:.4f})", spread > 0.10),
        (f"SIDEWAYS between BULL and BEAR ({lo_ret:+.4f} < {mid_ret:+.4f} < {hi_ret:+.4f})",
         lo_ret < mid_ret < hi_ret),
        (f"BULL daily return > +0.05% ({hi_ret:+.4f}%)", hi_ret > 0.05),
        (f"BEAR daily return < -0.08% ({lo_ret:+.4f}%)", lo_ret < -0.08),
    ]
    for desc, passed in checks:
        print(f"  {'✅' if passed else '❌'} {desc}")

    # Label distribution
    print(f"\n  === Label 分佈 ===")
    label_counts = {}
    for s, v in stats.items():
        lbl = labels.get(s, "?")
        label_counts[lbl] = label_counts.get(lbl, 0) + v["days"]
    total = sum(label_counts.values())
    for lbl, n in sorted(label_counts.items()):
        print(f"  {lbl:>15}: {n:>5} days ({n/total:.1%})")

    # Transition matrix
    print(f"\n  === 轉移矩陣 ===")
    transmat = model.transmat_
    state_labels = [labels.get(s, f"S{s}") for s in range(model.n_components)]
    # Sort by mean return for readability
    sorted_idx = sorted(range(model.n_components), key=lambda s: stats.get(s, {}).get("mean_return", 0))
    sorted_labels = [state_labels[i] for i in sorted_idx]
    sorted_trans = transmat[np.ix_(sorted_idx, sorted_idx)]
    print("  " + "  ".join(f"{l:>10}" for l in sorted_labels))
    for i, lbl in enumerate(sorted_labels):
        row = "  ".join(f"{sorted_trans[i, j]:>10.3f}" for j in range(len(sorted_idx)))
        print(f"  {lbl:>10}  {row}")

    return stats, labels, spread, trending


def grid_search(cfg: Config):
    """Grid search HMM 參數。"""
    market_idx = getattr(cfg, "regime_market_index", "SPY")
    print(f"=== HMM Grid Search（{market_idx}）===\n")

    df = fetch_daily(market_idx, period="2y")
    close = df["close"].astype(float)

    # Parameter grid
    vol_windows = [5, 10, 15, 20]
    min_bull_rets = [0.01, 0.02, 0.03, 0.05]
    min_bear_rets = [-0.01, -0.02, -0.03, -0.05]
    min_spreads = [0.03, 0.05, 0.08, 0.10]

    results = []
    total_combos = len(vol_windows) * len(min_bull_rets) * len(min_bear_rets) * len(min_spreads)
    print(f"  測試 {total_combos} 種組合...\n")

    for vw in vol_windows:
        feats = compute_features(close, vw)
        if len(feats) < cfg.hmm_min_obs:
            continue
        try:
            model = fit_hmm(feats.values, cfg)
        except Exception:
            continue

        states = model.predict(feats.values)
        ret = feats["return"].values
        vol = feats["volatility"].values

        # Compute state stats once
        state_stats = {}
        for s in range(model.n_components):
            mask = states == s
            n = int(mask.sum())
            if n == 0:
                state_stats[s] = {"mean_return": np.nan, "mean_vol": np.nan, "days": 0, "pct": 0.0}
            else:
                state_stats[s] = {
                    "mean_return": float(ret[mask].mean()),
                    "mean_vol": float(vol[mask].mean()),
                    "days": n,
                    "pct": float(mask.mean()),
                }

        valid = {s: v for s, v in state_stats.items() if v["days"] > 0}
        if len(valid) < 3:
            continue
        order = sorted(valid, key=lambda s: valid[s]["mean_return"])
        lo_s, mid_s, hi_s = order[0], order[1], order[-1]
        actual_spread = valid[hi_s]["mean_return"] - valid[lo_s]["mean_return"]

        for mbr in min_bull_rets:
            for mber in min_bear_rets:
                for ms in min_spreads:
                    # Simulate labeling
                    if actual_spread < ms:
                        bull_pct = 0.0
                        bear_pct = 0.0
                        side_pct = 1.0
                        trending = False
                    else:
                        hi_label = "BULL" if valid[hi_s]["mean_return"] > mbr else "SIDEWAYS"
                        lo_label = "BEAR" if valid[lo_s]["mean_return"] < mber else "SIDEWAYS"
                        trending = (hi_label == "BULL")
                        if not trending:
                            hi_label = lo_label = "SIDEWAYS"

                        bull_pct = valid[hi_s]["pct"] if hi_label == "BULL" else 0.0
                        bear_pct = valid[lo_s]["pct"] if lo_label == "BEAR" else 0.0
                        side_pct = 1.0 - bull_pct - bear_pct

                    # Score: prefer configs with good separation and balanced distribution
                    separation_score = actual_spread / max(0.01, ms)
                    balance_penalty = abs(bull_pct - bear_pct)  # penalize imbalance
                    trend_days = bull_pct + bear_pct
                    score = separation_score * trend_days - balance_penalty * 0.5

                    results.append({
                        "vol_window": vw,
                        "min_bull_ret": mbr,
                        "min_bear_ret": mber,
                        "min_spread": ms,
                        "actual_spread": actual_spread,
                        "bull_pct": bull_pct,
                        "bear_pct": bear_pct,
                        "side_pct": side_pct,
                        "trending": trending,
                        "score": score,
                    })

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)

    print(f"  Top 10 configurations (by separation × trend coverage):\n")
    print(f"  {'VW':>3} {'min_bull':>8} {'min_bear':>8} {'min_spr':>7} | "
          f"{'act_spr':>7} {'BULL%':>6} {'BEAR%':>6} {'SIDE%':>6} {'trend':>5} | {'score':>6}")
    print("  " + "-" * 85)
    for r in results[:10]:
        print(f"  {r['vol_window']:>3} {r['min_bull_ret']:>+8.3f} {r['min_bear_ret']:>+8.3f} "
              f"{r['min_spread']:>7.3f} | {r['actual_spread']:>+7.4f} "
              f"{r['bull_pct']:>5.1%} {r['bear_pct']:>5.1%} {r['side_pct']:>5.1%} "
              f"{'Y' if r['trending'] else 'N':>5} | {r['score']:>+6.3f}")

    print(f"\n  建議：選擇 BULL% + BEAR% 之和高（趨勢天數多）、且 SIDE% 不超過 60% 的組合。")
    print(f"  避免選擇 SIDE% > 70% 的組合（太多天被判為盤整 → 趨勢策略無法進場）。")


def main():
    ap = argparse.ArgumentParser(description="HMM 參數診斷與重校準")
    ap.add_argument("--grid", action="store_true", help="執行 grid search")
    args = ap.parse_args()
    cfg = Config()

    if args.grid:
        grid_search(cfg)
    else:
        diagnose(cfg)


if __name__ == "__main__":
    main()
