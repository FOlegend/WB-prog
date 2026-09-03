"""
screener/screener.py — Production Screener (unified, DataSource-driven)

Single 6-filter pipeline used by BOTH live briefing and the production-
equivalent backtest. Thresholds & filter semantics are unchanged (frozen in
ProductionConfig) — only the data layer is now injected via a DataSource so
the exact same screen code can run point-in-time over the cache.

  screen_from_source(source, cfg, as_of=None, apply_mcap=None, top_n=None)
      -> dict {status: "OK"|"EMPTY"|"FAILURE", ...}

Fail-loud contract (spec §5):
  * EMPTY   = valid calculation, zero candidates (no error)
  * FAILURE = calculation could not be completed (no data / SPY too short /
              universe empty) — NEVER silently treated as "no candidates".
  * A screen FAILURE or EMPTY is carried into DecisionRecord verbatim; the
    pipeline never fabricates a fallback candidate list.

NOTE: market-cap is applied ONLY for live runs (as_of=None) where we can
fetch CURRENT caps. Point-in-time screens skip mcap by construction (pulling
today's market caps for a historical date is a look-ahead bias — see
screen_as_of.py). The DecisionRecord records apply_mcap + a warning.
"""
from __future__ import annotations

import os
import sys
import math

import numpy as np
import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.indicators.technicals import atr as calc_atr, adx as calc_adx
from src.screener.screener import _compute_rs_composite, _fetch_market_caps_parallel
from production.datasource import DataSource, DataSourceError


# ---------------------------------------------------------------------------
# Unified point-in-time / live 6-filter screen on a DataSource
# ---------------------------------------------------------------------------
def screen_from_source(source: DataSource, cfg, as_of: str | None = None,
                       apply_mcap: bool | None = None,
                       top_n: int | None = None,
                       universe: list[str] | None = None) -> dict:
    """6-filter screen over `source`, sliced to <= as_of.

    as_of None  -> live semantics (source fetches latest data)
    as_of given -> point-in-time (CachedSource slice; no look-ahead)

    Returns a status dict:
      {status, tickers:[{ticker, rs_rank, rs, price, provider}...],
       n_universe_checked, top_n, apply_mcap, filters_applied,
       provider_counts:{cache|yfinance|stooq:n, missing:n},
       warnings:[], error:null}
    """
    warnings: list[str] = []
    min_dvol = cfg.screener_min_dollar_vol
    min_price = cfg.screener_min_price
    min_atr_pct = cfg.screener_min_atr_pct
    min_adx = float(getattr(cfg, "screener_min_adx", 0.0))
    min_rs = cfg.screener_min_rs_ratio
    rs_lookbacks = list(getattr(cfg, "screener_rs_lookbacks", [50]))
    rs_weights = list(getattr(cfg, "screener_rs_weights", [1.0]))
    dvol_window = int(cfg.screener_dvol_window)
    atr_period = int(cfg.screener_atr_period)
    adx_period = int(cfg.screener_adx_period)
    while len(rs_weights) < len(rs_lookbacks):
        rs_weights.append(0.0)
    max_rs_lb = max(rs_lookbacks) if rs_lookbacks else 50
    top_n = top_n if top_n is not None else int(cfg.screener_top_n)
    if apply_mcap is None:
        # default: fetch caps only when we are NOT slicing to a historical date
        apply_mcap = as_of is None
    if apply_mcap and as_of is not None:
        # caller asked for caps on a PIT screen — allow but warn loudly
        warnings.append("apply_mcap=True with point-in-time as_of — current "
                        "market caps would be a look-ahead bias")
    benchmark = source.benchmark
    filters_applied = (["DollarVol", "Price", "ATR%", "RS", "ADX"]
                       + (["MarketCap"] if apply_mcap else []))

    # ---- benchmark first: no SPY -> FAILURE (not EMPTY) ----
    spy_df, spy_prov = source.get_ohlcv(benchmark, as_of=as_of,
                                        min_bars=max_rs_lb + 1)
    if spy_df is None:
        return {
            "status": "FAILURE",
            "error": (f"benchmark {benchmark} unavailable "
                      f"({spy_prov.error or 'no data'})"),
            "tickers": [], "n_universe_checked": 0, "top_n": top_n,
            "apply_mcap": apply_mcap, "filters_applied": filters_applied,
            "provider_counts": {}, "warnings": warnings,
        }
    spy_close = spy_df["close"].astype(float)
    if len(spy_close) < max_rs_lb + 1:
        return {"status": "FAILURE",
                "error": f"{benchmark} history too short ({len(spy_close)})",
                "tickers": [], "n_universe_checked": 0, "top_n": top_n,
                "apply_mcap": apply_mcap, "filters_applied": filters_applied,
                "provider_counts": {}, "warnings": warnings}

    # ---- universe ----
    universe = universe if universe is not None else source.universe()
    universe = [t for t in universe if t != benchmark]
    if not universe:
        return {"status": "FAILURE", "error": "empty universe",
                "tickers": [], "n_universe_checked": 0, "top_n": top_n,
                "apply_mcap": apply_mcap, "filters_applied": filters_applied,
                "provider_counts": {}, "warnings": warnings}

    min_bars = max(max_rs_lb + 5, dvol_window + 5,
                   atr_period * 3, adx_period * 3)

    # ---- pre-load benchmark-aligned frame + candidate slices ----
    frames: dict[str, pd.DataFrame] = {}
    prov: dict[str, dict] = {}
    missing = 0
    for t in universe:
        df, p = source.get_ohlcv(t, as_of=as_of, min_bars=min_bars)
        if df is None or len(df) == 0:
            missing += 1
            continue
        frames[t] = df
        prov[t] = p.as_dict()
    n_loaded = len(frames)
    provider_counts: dict[str, int] = {}
    for p in prov.values():
        provider_counts[p["provider"]] = provider_counts.get(p["provider"], 0) + 1
    provider_counts["missing"] = missing
    if n_loaded == 0:
        return {"status": "FAILURE",
                "error": "no ticker data available from source",
                "tickers": [], "n_universe_checked": len(universe),
                "top_n": top_n, "apply_mcap": apply_mcap,
                "filters_applied": filters_applied,
                "provider_counts": provider_counts, "warnings": warnings}
    if missing:
        warnings.append(f"{missing}/{len(universe)} universe tickers "
                        f"unavailable (skipped)")

    # ---- per-ticker filters 2-6 (identical semantics to src/screener) ----
    rows = []
    for t in universe:
        df = frames.get(t)
        if df is None:
            continue
        try:
            close = df["close"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            volume = df["volume"].astype(float)
            price = float(close.iloc[-1])

            # Filter 3: Price > $10
            if np.isnan(price) or price <= min_price:
                continue
            # Filter 2: 20-day Avg Dollar Volume > $50M
            avg_dvol = float((close * volume).rolling(dvol_window).mean().iloc[-1])
            if np.isnan(avg_dvol) or avg_dvol <= min_dvol:
                continue
            # Filter 4: 14-day ATR% > 2.0%
            atr_series = calc_atr(df, period=atr_period)
            atr_val = float(atr_series.iloc[-1])
            if np.isnan(atr_val) or atr_val <= 0:
                continue
            atr_pct = (atr_val / price) * 100.0
            if atr_pct <= min_atr_pct:
                continue
            # Filter 5: RS vs SPY (date-aligned composite)
            composite, _ = _compute_rs_composite(close, spy_close,
                                                 rs_lookbacks, rs_weights)
            if composite is None or np.isnan(composite) or composite <= min_rs:
                continue
            # Filter 6: ADX > 20
            adx_df = calc_adx(df, period=adx_period)
            adx_val = float(adx_df["adx"].iloc[-1]) if not adx_df.empty else 0.0
            if np.isnan(adx_val):
                adx_val = 0.0
            if min_adx > 0 and adx_val < min_adx:
                continue
        except Exception:
            continue
        rows.append({"ticker": t, "rs": float(composite),
                     "price": round(price, 2),
                     "atr_pct": round(atr_pct, 2),
                     "adx": round(adx_val, 1),
                     "provider": prov[t]["provider"]})

    # ---- Filter 1: Market Cap (LIVE ONLY) ----
    if apply_mcap and rows:
        survivors = [r["ticker"] for r in rows]
        mcap = _fetch_market_caps_parallel(survivors, max_workers=8)
        min_mcap = float(cfg.screener_min_market_cap)
        rows = [r for r in rows if mcap.get(r["ticker"]) is not None
                and float(mcap[r["ticker"]]) > min_mcap]
        for r in rows:
            r["market_cap_b"] = round(mcap[r["ticker"]] / 1e9, 1)

    if not rows:
        return {"status": "EMPTY",
                "error": None,
                "tickers": [], "n_universe_checked": len(universe),
                "top_n": top_n, "apply_mcap": apply_mcap,
                "filters_applied": filters_applied,
                "provider_counts": provider_counts, "warnings": warnings}

    df_rows = pd.DataFrame(rows).sort_values("rs", ascending=False)
    if len(df_rows) > top_n:
        df_rows = df_rows.head(top_n)
    tickers = []
    for i, (_, r) in enumerate(df_rows.iterrows(), 1):
        tickers.append({"ticker": str(r["ticker"]), "rs_rank": i,
                        "rs": round(float(r["rs"]), 4),
                        "price": float(r["price"]),
                        "provider": str(r["provider"])})

    return {"status": "OK", "error": None, "tickers": tickers,
            "n_universe_checked": len(universe), "top_n": top_n,
            "apply_mcap": apply_mcap, "filters_applied": filters_applied,
            "provider_counts": provider_counts, "warnings": warnings}


# ---------------------------------------------------------------------------
# Backward-compatible name — returns the new status dict (no silent fallback)
# ---------------------------------------------------------------------------
def run_screener(cfg, source=None, as_of: str | None = None) -> dict:
    """Live screener entry point (was: DataFrame with silent fallback).

    v2: fail-loud — returns {status, tickers, ...}; NEVER falls back to a
    hard-coded top-10 when the screen fails or is empty.
    """
    from production.datasource import build_live_source
    if source is None:
        source = build_live_source(cfg)
    return screen_from_source(source, cfg, as_of=as_of, apply_mcap=True)
