"""
regime_agent.py — HMM 市場 regime 分類（照 MDPI 論文 + 穩健修復）

把 hmm_regime_classification.py 的核心邏輯封裝成可重用的 analyzer。
產出 regime signal：BULL / BEAR / SIDEWAYS / RANGE_BOUND + trending flag + 後驗機率。

無 LLM、純統計。每天 fit 一次即可（日線，~2 年資料，秒級）。
"""
from __future__ import annotations
import warnings
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass

# hmmlearn 用 logging 輸出收斂訊息，靜音
logging.getLogger("hmmlearn").setLevel(logging.ERROR)
logging.getLogger("hmmlearn.hmm").setLevel(logging.ERROR)

from src.indicators.technicals import efficiency_ratio


@dataclass
class RegimeResult:
    regime: str               # BULL / BEAR / SIDEWAYS / RANGE_BOUND
    trending: bool            # 是否有可信趨勢（BULL 才 True）
    latest_prob: float        # 所屬狀態後驗機率 [0,1]
    switch_confidence: float  # 1 - latest_prob（切換疑慮）
    labels: dict              # state -> label
    stats: dict               # state -> {mean_return, mean_vol, days, pct}
    spread: float             # bull-bear 報酬差
    latest_state: int
    n_states: int


# ---------------------------------------------------------------------------
# 特徵工程（論文：日報酬% + 10日 MSE 波動度）
# ---------------------------------------------------------------------------
def compute_features(close: pd.Series, vol_window: int = 10) -> pd.DataFrame:
    ret = close.pct_change() * 100.0
    ma = close.rolling(vol_window).mean()
    vol = ((close - ma) ** 2).rolling(vol_window).mean()
    return pd.DataFrame({"return": ret, "volatility": vol}).dropna()


# ---------------------------------------------------------------------------
# HMM 訓練 / 解碼
# ---------------------------------------------------------------------------
def fit_hmm(X: np.ndarray, cfg) -> "GaussianHMM":
    from hmmlearn.hmm import GaussianHMM
    model = GaussianHMM(
        n_components=cfg.hmm_n_states,
        covariance_type=cfg.hmm_covariance_type,
        n_iter=cfg.hmm_n_iter,
        random_state=cfg.hmm_random_state,
        verbose=False,
    )
    model.fit(X)
    return model


def _regime_95ci(stats, z=1.96):
    ci = {}
    for s, v in stats.items():
        if v["days"] > 1 and not np.isnan(v["ret_std"]):
            se = v["ret_std"] / np.sqrt(v["days"])
            ci[s] = (v["mean_return"] - z * se, v["mean_return"] + z * se)
        else:
            ci[s] = (np.nan, np.nan)
    return ci


def decode_and_label(model, feats_df, cfg):
    """Viterbi 解碼 + 穩健命名（range-bound 守門 + 經濟門檻）。"""
    states = model.predict(feats_df.values)
    ret = feats_df["return"].values
    vol = feats_df["volatility"].values
    stats = {}
    for s in range(model.n_components):
        mask = states == s
        n = int(mask.sum())
        if n == 0:
            stats[s] = {"mean_return": np.nan, "ret_std": np.nan,
                        "mean_vol": np.nan, "days": 0, "pct": 0.0}
            continue
        r = ret[mask]
        stats[s] = {
            "mean_return": float(r.mean()),
            "ret_std": float(r.std(ddof=1)) if n > 1 else np.nan,
            "mean_vol": float(vol[mask].mean()),
            "days": n,
            "pct": float(mask.mean()),
        }

    valid = {s: v for s, v in stats.items() if v["days"] > 0}
    order = sorted(valid, key=lambda s: valid[s]["mean_return"])
    lo, mid, hi = order[0], order[1], order[-1]
    spread = valid[hi]["mean_return"] - valid[lo]["mean_return"]

    labels = {}
    if spread < cfg.min_regime_spread:
        for s in valid:
            labels[s] = "SIDEWAYS"
        trending = False
    else:
        labels[hi] = "BULL" if valid[hi]["mean_return"] > cfg.min_bull_ret else "SIDEWAYS"
        labels[lo] = "BEAR" if valid[lo]["mean_return"] < cfg.min_bear_ret else "SIDEWAYS"
        labels[mid] = "SIDEWAYS"
        trending = labels[hi] == "BULL"

    # 對做多為主的 swing bot：連最好狀態都過不了 BULL 門檻 → 全 SIDEWAYS（不可信）
    if not trending:
        for s in valid:
            labels[s] = "SIDEWAYS"

    return states, stats, labels, trending, spread


def analyze(close: pd.Series, cfg) -> RegimeResult | None:
    """對一段收盤價跑完整 HMM regime 分析，回傳 RegimeResult。"""
    feats = compute_features(close, cfg.hmm_vol_window)
    if len(feats) < cfg.hmm_min_obs:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = fit_hmm(feats.values, cfg)
        states, stats, labels, trending, spread = decode_and_label(model, feats, cfg)

    posteriors = model.predict_proba(feats.values)
    latest_state = int(states[-1])
    latest_prob = float(posteriors[-1, latest_state])

    # regime 命名：若 trending=False（range-bound），對外回報 RANGE_BOUND
    regime_label = labels.get(latest_state, "SIDEWAYS")
    if not trending:
        regime_label = "RANGE_BOUND"

    return RegimeResult(
        regime=regime_label,
        trending=trending,
        latest_prob=latest_prob,
        switch_confidence=1.0 - latest_prob,
        labels=labels,
        stats=stats,
        spread=spread,
        latest_state=latest_state,
        n_states=model.n_components,
    )


def regime_signal(close: pd.Series, cfg) -> dict:
    """產出符合 agent signal 契約的 regime 訊號。"""
    res = analyze(close, cfg)
    if res is None:
        return {
            "agent": "regime_agent",
            "signal": "neutral",
            "confidence": 0.0,
            "regime": "UNKNOWN",
            "trending": False,
            "reasoning": "資料不足（< min_obs），無法跑 HMM",
        }

    score_map = {"BULL": 1.0, "SIDEWAYS": 0.0, "BEAR": -1.0, "RANGE_BOUND": 0.0}
    score = score_map.get(res.regime, 0.0)
    if res.regime == "BULL":
        signal = "bullish"
    elif res.regime == "BEAR":
        signal = "bearish"
    else:
        signal = "neutral"
    conf = round(res.latest_prob * 100, 1)

    return {
        "agent": "regime_agent",
        "signal": signal,
        "confidence": conf,
        "score": score,
        "regime": res.regime,
        "trending": res.trending,
        "latest_prob": round(res.latest_prob, 3),
        "switch_confidence": round(res.switch_confidence, 3),
        "spread": round(res.spread, 4),
        "stats": {s: {"mean_ret": round(v["mean_return"], 4),
                      "mean_vol": round(v["mean_vol"], 4),
                      "pct": round(v["pct"], 3),
                      "label": res.labels.get(s, "?")}
                  for s, v in res.stats.items()},
        "reasoning": (
            f"HMM 3-state：當前 regime={res.regime}（後驗 P={res.latest_prob:.1%}，"
            f"trending={res.trending}，spread={res.spread:+.4f}%/日）。"
            f"{'BULL 允許做多' if res.regime=='BULL' else ('BEAR 禁止新多單' if res.regime=='BEAR' else '盤整/不可信，不開新多單')}"
        ),
    }
