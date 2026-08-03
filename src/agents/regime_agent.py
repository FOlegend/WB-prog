"""
regime_agent.py — Composite Regime Score Engine (v2)

Architecture (3 layers, 5 components + hard vetoes):
  Layer 1 — Regime Detection:
    • HMM (statistical: BULL/BEAR/SIDEWAYS + posterior probability)
    • MA Structure (structural: Minervini 50>150>200 SMA alignment)
  Layer 2 — Regime Quality:
    • KER (Kaufman Efficiency Ratio — trend *cleanliness*)
    • ADX (trend strength — direction-gated: bearish strong trend → LOW score)
  Layer 3 — Volume Pressure:
    • Distribution Days (IBD/O'Neil — with 5% rally reset decay)

Hard vetoes (override composite score — safety guardrails):
  • HMM BEAR with high posterior → force cash
  • Price below 200-day SMA → cap at selective
  • Distribution days ≥ 5 → cap position_size_mult

v2 fixes (per reviewer feedback):
  1. ADX directional trap: high ADX in bearish trend now scores LOW
  2. Hard vetoes: weighted average alone is not enough
  3. HMM features: rolling std of returns (price-level independent, scale-matched)
  4. SIDEWAYS score: 35-50 range (unfavorable for trend-following, not neutral)
  5. Distribution day decay: 5% rally voids a distribution day (O'Neil rule)
  6. Confidence: std-based dispersion (not crude count)
  7. Missing OHLCV guard: ADX returns neutral 50 if high/low missing
  8. n_states assert: enforce 3-state labeling
  9. Distribution day scope: market-only flag for index-level analysis
  10. Weight adjustment: dist > ADX (institutions selling > trend strength)
"""
from __future__ import annotations
import warnings
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass

logging.getLogger("hmmlearn").setLevel(logging.ERROR)
logging.getLogger("hmmlearn.hmm").setLevel(logging.ERROR)

from src.indicators.technicals import efficiency_ratio, adx as calc_adx


# ===========================================================================
# HMM Engine (one component of the composite score)
# ===========================================================================

@dataclass
class RegimeResult:
    regime: str
    trending: bool
    latest_prob: float
    switch_confidence: float
    labels: dict
    stats: dict
    spread: float
    latest_state: int
    n_states: int


def compute_features(close: pd.Series, vol_window: int = 10) -> pd.DataFrame:
    """HMM features: daily return % + rolling std of returns.

    v2 fix: replaced price-level MSE volatility `((close-ma)**2).mean()`
    with `ret.rolling(vol_window).std()`.  This fixes two issues:
    1. Scale: both features are now in % terms (return and vol-of-return)
    2. Price-level dependency: std of returns is normalized, so a $500 stock
       and a $20 stock produce comparable volatility features.
    """
    ret = close.pct_change() * 100.0
    vol = ret.rolling(vol_window).std()
    return pd.DataFrame({"return": ret, "volatility": vol}).dropna()


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
    """Viterbi 解碼 + 穩健命名（range-bound 守門 + 經濟門檻）。

    v2 fix: assert n_states == 3 (labeling logic assumes exactly 3 states).
    """
    assert cfg.hmm_n_states == 3, (
        f"HMM labeling currently supports exactly 3 states, got {cfg.hmm_n_states}. "
        "Set cfg.hmm_n_states = 3 or extend decode_and_label()."
    )

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


# ===========================================================================
# Regime Score Components (each returns 0-100)
# ===========================================================================

def _hmm_score(hmm_result: RegimeResult | None, cfg) -> float:
    """HMM posterior → 0-100 score.

    BULL with high posterior → 50-100.
    BEAR with high posterior → 0-50.
    SIDEWAYS / RANGE_BOUND → 35-50 (unfavorable for trend-following, NOT neutral).

    v2 fix: SIDEWAYS is no longer always 50. For a trend-following bot,
    range-bound markets are unfavorable — high confidence sideways → 35.
    """
    if hmm_result is None:
        return 50.0
    p = hmm_result.latest_prob  # [0, 1]
    if hmm_result.regime == "BULL":
        return 50.0 + 50.0 * p       # 50-100
    elif hmm_result.regime == "BEAR":
        return 50.0 - 50.0 * p       # 0-50
    else:  # SIDEWAYS / RANGE_BOUND
        return 50.0 - 15.0 * p       # 35-50 (high confidence sideways = more unfavorable)


def _ma_structure_score(close: pd.Series, cfg) -> float:
    """Minervini / Weinstein MA alignment → 0-100 score.

    Checks (each adds points):
    • Price > 200-day SMA (+25) — hard structural gate
    • 50-day SMA > 150-day SMA (+20)
    • 150-day SMA > 200-day SMA (+20)
    • 200-day SMA rising over 1 month (+20)
    • Price > 50-day SMA (+15)
    """
    n = len(close)
    if n < 50:
        return 50.0

    if n < cfg.regime_ma_slow:
        sma_20 = close.rolling(20).mean()
        sma_50 = close.rolling(50).mean()
        score = 0.0
        if close.iloc[-1] > sma_20.iloc[-1]:
            score += 30
        if close.iloc[-1] > sma_50.iloc[-1]:
            score += 30
        if sma_20.iloc[-1] > sma_50.iloc[-1]:
            score += 40
        return score

    sma_fast = close.rolling(cfg.regime_ma_fast).mean()
    sma_mid = close.rolling(cfg.regime_ma_mid).mean()
    sma_slow = close.rolling(cfg.regime_ma_slow).mean()

    current = float(close.iloc[-1])
    s_fast = float(sma_fast.iloc[-1])
    s_mid = float(sma_mid.iloc[-1])
    s_slow = float(sma_slow.iloc[-1])

    idx_20d = max(0, len(sma_slow) - 21)
    s_slow_20d_ago = float(sma_slow.iloc[idx_20d]) if not np.isnan(sma_slow.iloc[idx_20d]) else s_slow

    score = 0.0
    if current > s_slow:
        score += 25
    if s_fast > s_mid:
        score += 20
    if s_mid > s_slow:
        score += 20
    if s_slow > s_slow_20d_ago:
        score += 20
    if current > s_fast:
        score += 15
    return score


def _ker_score(close: pd.Series, cfg) -> float:
    """Kaufman Efficiency Ratio → 0-100 score.

    KER measures how *cleanly* price reached its destination:
    • ER ≈ 1.0 → clean linear move (low noise, high signal)
    • ER ≈ 0.0 → random walk (high noise, no signal)
    """
    er = efficiency_ratio(close, cfg.er_lookback)
    er_val = float(er.iloc[-1])
    if np.isnan(er_val) or er_val < 0:
        return 50.0

    if er_val < 0.3:
        return er_val / 0.3 * 30.0
    elif er_val < 0.6:
        return 30.0 + (er_val - 0.3) / 0.3 * 40.0
    else:
        return min(100.0, 70.0 + (er_val - 0.6) / 0.4 * 30.0)


def _adx_score(df: pd.DataFrame, cfg) -> float:
    """ADX → 0-100 score (direction-gated).

    v2 CRITICAL FIX: ADX measures trend *strength*, NOT direction.
    During a market crash, ADX spikes > 40 — but that's a strong BEARISH
    trend, not bullish.  Under the old logic, a crash scored 100 for ADX,
    artificially inflating the composite score.

    Fix: gate by +DI vs -DI direction.
    • If +DI > -DI (bullish trend): high ADX → high score (strong uptrend)
    • If -DI > +DI (bearish trend): high ADX → LOW score (strong downtrend)
    • This prevents the system from treating a clean bearish crash as attractive.

    Also guards missing OHLCV: if high/low not in df, return neutral 50.
    """
    # Guard: ADX needs high/low/close
    if "high" not in df.columns or "low" not in df.columns:
        return 50.0  # missing OHLC — neutral

    adx_df = calc_adx(df, period=cfg.adx_period)
    if adx_df.empty:
        return 50.0
    adx_val = float(adx_df["adx"].iloc[-1])
    plus_di = float(adx_df["plus_di"].iloc[-1])
    minus_di = float(adx_df["minus_di"].iloc[-1])
    if np.isnan(adx_val) or np.isnan(plus_di) or np.isnan(minus_di):
        return 50.0

    # Base score from ADX magnitude (trend strength)
    if adx_val < 15:
        base_score = adx_val / 15.0 * 15.0
    elif adx_val < 20:
        base_score = 15.0 + (adx_val - 15) / 5.0 * 25.0
    elif adx_val < 25:
        base_score = 40.0 + (adx_val - 20) / 5.0 * 20.0
    elif adx_val < 35:
        base_score = 60.0 + (adx_val - 25) / 10.0 * 20.0
    else:
        base_score = min(100.0, 80.0 + (adx_val - 35) / 15.0 * 20.0)

    # Direction gate: invert score if trend is bearish
    is_bullish = plus_di > minus_di
    if is_bullish:
        return base_score          # strong bullish trend → high score
    else:
        return 100.0 - base_score  # strong bearish trend → LOW score


def _count_distribution_days(close: pd.Series, volume: pd.Series, cfg) -> int:
    """Count IBD distribution days in last `lookback` trading days.

    v2 fix: O'Neil's 5% rally reset rule. A distribution day is voided if
    the market rallies 5%+ above that distribution day's close. This prevents
    the engine from artificially suppressing the regime score right at the
    start of a powerful new breakout.

    A distribution day = close drops > 0.2% on volume higher than previous day.
    4-6 in 4-5 weeks → market correction (O'Neil/IBD).
    """
    lookback = cfg.regime_dist_day_lookback
    drop_thr = cfg.regime_dist_day_drop
    rally_reset = getattr(cfg, "regime_dist_day_rally", 0.05)
    if len(close) < lookback + 1 or len(volume) < lookback + 1:
        return 0

    recent_close = close.iloc[-lookback:]
    recent_vol = volume.iloc[-lookback:]
    current_price = float(close.iloc[-1])

    count = 0
    for i in range(1, len(recent_close)):
        daily_ret = recent_close.iloc[i] / recent_close.iloc[i - 1] - 1.0
        if daily_ret < drop_thr and recent_vol.iloc[i] > recent_vol.iloc[i - 1]:
            # Check 5% rally reset: if price has rallied 5%+ since this
            # distribution day's close, the day is voided (O'Neil rule)
            dist_day_close = float(recent_close.iloc[i])
            if current_price >= dist_day_close * (1.0 + rally_reset):
                continue  # Voided by rally — skip this distribution day
            count += 1
    return count


def _dist_day_score(close: pd.Series, volume: pd.Series, cfg) -> float:
    """Distribution day count → 0-100 score.  Fewer = more bullish."""
    n = _count_distribution_days(close, volume, cfg)
    if n == 0:
        return 100.0
    elif n <= 2:
        return 100.0 - n * 10.0        # 80, 90
    elif n <= 4:
        return 60.0 - (n - 2) * 10.0   # 50, 40
    else:
        return max(0.0, 40.0 - (n - 4) * 10.0)  # 30, 20, 10, 0...


# ===========================================================================
# Composite Regime Score Engine (with hard vetoes)
# ===========================================================================

def regime_score_engine(df: pd.DataFrame, cfg,
                        hmm_result: RegimeResult | None = None,
                        ticker: str | None = None) -> dict:
    """Combine 5 independent signals into a composite regime score (0-100).

    v2 additions:
    • Hard vetoes: override composite score for safety (Reviewer 1)
    • Confidence via std dispersion: not crude count (Reviewer 1)
    • Distribution day market-only scope (both reviewers)
    • ADX direction-gated (both reviewers)

    Parameters
    ----------
    ticker : str | None
        Used for distribution day market-only filtering. If
        cfg.regime_dist_day_market_only is True and ticker is not in
        cfg.regime_dist_day_symbols, distribution days are skipped (neutral 50).
    """
    close = df["close"].astype(float)

    # Compute HMM if not provided
    if hmm_result is None:
        hmm_result = analyze(close, cfg)

    # --- Compute each component (0-100) ---
    components = {}
    components["hmm"] = _hmm_score(hmm_result, cfg)
    components["ma"] = _ma_structure_score(close, cfg)
    components["ker"] = _ker_score(close, cfg)
    components["adx"] = _adx_score(df, cfg)

    # Distribution days: market-only scope (both reviewers note this is more
    # meaningful on SPY/QQQ/IWM than individual stocks)
    compute_dist = True
    if getattr(cfg, "regime_dist_day_market_only", False):
        market_symbols = set(getattr(cfg, "regime_dist_day_symbols", []))
        if ticker is None or ticker.upper() not in market_symbols:
            compute_dist = False

    if compute_dist and "volume" in df.columns:
        volume = df["volume"].astype(float)
        components["dist"] = _dist_day_score(close, volume, cfg)
    else:
        components["dist"] = 50.0  # neutral if no volume data or market-only skip

    # --- Weighted composite ---
    weights = cfg.regime_score_weights
    total_weight = sum(weights.get(k, 0.0) for k in components)
    if total_weight > 0:
        regime_score = sum(components[k] * weights.get(k, 0.0)
                           for k in components) / total_weight
    else:
        regime_score = 50.0

    regime_score = max(0.0, min(100.0, regime_score))

    # --- Strategy + position size (progressive exposure) ---
    if regime_score >= cfg.regime_score_full:
        strategy = "trend_following"
        position_size_mult = 1.0
    elif regime_score >= cfg.regime_score_min:
        strategy = "selective"
        t = (regime_score - cfg.regime_score_min) / max(1.0, cfg.regime_score_full - cfg.regime_score_min)
        position_size_mult = 0.3 + 0.7 * t
    else:
        strategy = "cash"
        position_size_mult = 0.0

    # --- Hard vetoes (override composite score — safety guardrails) ---
    # These prevent the weighted average from treating a clean bearish trend
    # or structurally broken market as attractive.
    vetoes = []

    # Veto 1: HMM BEAR with high posterior → force cash
    if hmm_result and hmm_result.regime == "BEAR":
        if hmm_result.latest_prob > cfg.regime_veto_hmm_bear_prob:
            strategy = "cash"
            position_size_mult = 0.0
            vetoes.append(f"HMM BEAR P={hmm_result.latest_prob:.0%} > {cfg.regime_veto_hmm_bear_prob:.0%} → cash")

    # Veto 2: Price below 200-day SMA → cap at selective
    if len(close) >= cfg.regime_ma_slow:
        sma_slow = close.rolling(cfg.regime_ma_slow).mean()
        s_slow = float(sma_slow.iloc[-1]) if not np.isnan(sma_slow.iloc[-1]) else None
        if s_slow is not None and float(close.iloc[-1]) < s_slow:
            if strategy == "trend_following":
                strategy = "selective"
            position_size_mult = min(position_size_mult, cfg.regime_veto_below_sma_cap)
            vetoes.append(f"Price < 200SMA ({s_slow:.2f}) → cap size_mult ≤ {cfg.regime_veto_below_sma_cap}")

    # Veto 3: High distribution days → cap size_mult
    if compute_dist and "volume" in df.columns:
        volume = df["volume"].astype(float)
        n_dist = _count_distribution_days(close, volume, cfg)
        if n_dist >= cfg.regime_veto_dist_days:
            position_size_mult = min(position_size_mult, cfg.regime_veto_dist_cap)
            if strategy == "trend_following":
                strategy = "selective"
            vetoes.append(f"Dist days={n_dist} ≥ {cfg.regime_veto_dist_days} → cap size_mult ≤ {cfg.regime_veto_dist_cap}")

    # --- Confidence via std dispersion (v2: replaces crude count) ---
    # Low std = components agree → high confidence
    # High std = components conflict → low confidence
    comp_values = list(components.values())
    comp_std = float(np.std(comp_values))
    # Normalize: std of 0 = perfect agreement (confidence=1), std of 30+ = no agreement
    confidence = 1.0 - min(1.0, comp_std / 30.0)

    # --- Backward compat fields ---
    if hmm_result:
        regime_label = hmm_result.regime
        trending = hmm_result.trending
        latest_prob = hmm_result.latest_prob
    else:
        regime_label = "UNKNOWN"
        trending = False
        latest_prob = 0.0

    return {
        "regime_score": round(regime_score, 1),
        "confidence": round(confidence, 3),
        "strategy": strategy,
        "position_size_mult": round(position_size_mult, 3),
        "components": {k: round(v, 1) for k, v in components.items()},
        "vetoes": vetoes,
        # backward compat
        "regime": regime_label,
        "trending": trending,
        "latest_prob": round(latest_prob, 3),
    }


# ===========================================================================
# Agent Signal (backward-compatible interface)
# ===========================================================================

def market_regime(market_df: pd.DataFrame, cfg) -> dict:
    """Compute market-level regime score ONCE on the market index (SPY/QQQ).

    v3 structural decoupling: Regime is computed only on the market index,
    producing a global position_size_mult that applies to ALL stocks.
    Individual stocks should NOT call regime_signal() — they only provide
    technicals_signal() for entry/exit timing.

    Parameters
    ----------
    market_df : pd.DataFrame
        OHLCV data for the market index (SPY by default, configurable via
        cfg.regime_market_index).
    cfg : Config
    """
    market_idx = getattr(cfg, "regime_market_index", "SPY")
    return regime_signal(market_df, cfg, ticker=market_idx)


def regime_signal(df, cfg, ticker: str | None = None) -> dict:
    """產出符合 agent signal 契約的 regime 訊號（含 composite score + vetoes）。

    Parameters
    ----------
    df : pd.DataFrame or pd.Series
        Full OHLCV DataFrame (preferred) or close-only Series (backward compat).
    cfg : Config
    ticker : str | None
        Ticker symbol — used for distribution day market-only filtering.
    """
    # Accept both DataFrame and Series for backward compat
    if isinstance(df, pd.Series):
        close = df
        df_full = pd.DataFrame({"close": close})
    else:
        close = df["close"].astype(float)
        df_full = df

    # HMM analysis
    hmm_result = analyze(close, cfg)

    # Composite score (with vetoes)
    sr = regime_score_engine(df_full, cfg, hmm_result=hmm_result, ticker=ticker)

    # Determine raw_signal from score (for transparency)
    if sr["regime_score"] >= cfg.regime_score_full:
        raw_signal = "bullish"
    elif sr["regime_score"] < cfg.regime_score_min:
        raw_signal = "bearish"
    else:
        raw_signal = "neutral"

    # Derive FINAL signal from POST-VETO strategy (not raw score).
    # Reviewer fix: vetoes can change strategy/size_mult without changing
    # regime_score, creating contradictory outputs (e.g. signal="bullish"
    # but strategy="cash" + size_mult=0.0). Downstream agents reading only
    # `signal` would trade when the veto has forced cash. Signal must reflect
    # the final post-veto decision.
    if sr["strategy"] == "trend_following":
        signal = "bullish"
    elif sr["strategy"] == "selective":
        signal = "neutral"
    else:  # cash
        signal = "bearish"

    # Build result
    result = {
        "agent": "regime_agent",
        "signal": signal,
        "raw_signal": raw_signal,  # pre-veto signal (transparency)
        "signal_changed_by_veto": signal != raw_signal,
        "confidence": round(sr["confidence"] * 100, 1),
        # confidence = component AGREEMENT, not probability of profit.
        # High confidence means components agree on direction — they could
        # all agree on "bearish" just as easily as "bullish".
        "agreement_score": round(sr["confidence"] * 100, 1),
        "score": round((sr["regime_score"] - 50) / 50, 3),  # [0,100] → [-1,1]
        # --- Composite fields ---
        "regime_score": sr["regime_score"],
        "strategy": sr["strategy"],
        "position_size_mult": sr["position_size_mult"],
        "components": sr["components"],
        "vetoes": sr.get("vetoes", []),
        # --- Backward compat fields ---
        "regime": sr["regime"],
        "trending": sr["trending"],
        "latest_prob": sr["latest_prob"],
    }

    if hmm_result:
        result["switch_confidence"] = round(1.0 - hmm_result.latest_prob, 3)
        result["spread"] = round(hmm_result.spread, 4)
        result["stats"] = {
            s: {"mean_ret": round(v["mean_return"], 4),
                "mean_vol": round(v["mean_vol"], 4),
                "pct": round(v["pct"], 3),
                "label": hmm_result.labels.get(s, "?")}
            for s, v in hmm_result.stats.items()
        }

    # Build human-readable reasoning
    comp_str = " / ".join(f"{k}={v:.0f}" for k, v in sr["components"].items())
    veto_str = ""
    if sr.get("vetoes"):
        veto_str = "\n  ⚠️ Veto: " + "; ".join(sr["vetoes"])
    signal_note = ""
    if signal != raw_signal:
        signal_note = f"\n  ⚠️ Signal overridden by veto: {raw_signal} → {signal}"
    result["reasoning"] = (
        f"Regime Score={sr['regime_score']:.0f}/100 "
        f"(signal={signal}, strategy={sr['strategy']}, size_mult={sr['position_size_mult']:.2f}, "
        f"agreement={sr['confidence']:.0%}).\n"
        f"  Components: {comp_str}.\n"
        f"  HMM regime={sr['regime']}(P={sr['latest_prob']:.0%}), "
        f"trending={sr['trending']}.{veto_str}{signal_note}\n"
        f"  {'→ 允許做多（full size）' if sr['strategy']=='trend_following' else '→ 縮減倉位' if sr['strategy']=='selective' else '→ 現金為主，不開新多單'}"
    )

    return result
