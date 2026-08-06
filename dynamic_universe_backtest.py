"""
dynamic_universe_backtest.py — Layer 4 + Layer 5: Dynamic Screening Backtest

Implements the reviewer's recommended "dynamic screening backtest": at every
historical rebalance date we re-select the candidate universe using ONLY data
known as of that date (via screen_as_of), then trade that universe in the
following window. This removes the look-ahead bias of the earlier static
"screen today, backtest 2024" approach.

Architecture (5 layers):
  Layer 1  Historical Data Cache      -> historical_cache.py (get_all_data)
  Layer 2  Rebalance Calendar         -> build_rebalance_calendar()  [monthly v1]
  Layer 3  Point-in-Time Screener     -> screen_as_of.screen_as_of()
  Layer 4  Dynamic Backtest Engine    -> DynamicBacktestEngine.run()
  Layer 5  Performance Comparison      -> run_comparison()  (4 variants)

Key design decisions (documented for reviewers):
  * ENTRY universe is the active month's screened bucket ONLY. Existing open
    positions are held and managed through month boundaries (TP/SL/trailing/
    time-stop/signal-exit) even if the stock drops off the next screen — this
    mirrors real swing trading and avoids artificial month-end churn.
  * Regime is computed daily on SPY (global position_size_mult), identical to
    the static v3 engine. The dynamic part is purely the STOCK universe.
  * Indicator lag: a signal known at date D's close can only be acted on at
    D's close (we simulate close-to-close fills, same as the static engine).
    No next-day open assumption is needed because we never trade inside the
    same bar we screen — screening happens at month-end, trading next month.
  * Rebalance dates: last trading day of each month. The bucket active at a
    given date is the most recent rebalance date <= that date (so early-Jan
    trades use the late-Dec screen).

Run:
  python dynamic_universe_backtest.py            # MVP + 4-variant comparison
  python dynamic_universe_backtest.py --no-cache-build  # skip cache refresh
"""
from __future__ import annotations

import os
import sys
import json
import bisect
import warnings
import base64
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import Config
from historical_cache import get_all_data, build_universe
from screen_as_of import screen_as_of, screen_all_buckets

from src.agents.regime_agent import (fit_hmm, compute_features, decode_and_label,
                                      RegimeResult, regime_score_engine)
from src.agents.technicals_agent import technicals_signal
from src.agents.risk_manager import size_position
from src.portfolio.portfolio_manager import _exit_check
from src.state.state import default_state, mark_to_market, close_position, open_position
from src.backtest.engine import compute_metrics

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Layer 2 — Rebalance Calendar
# ---------------------------------------------------------------------------
def build_rebalance_calendar(all_data: dict[str, pd.DataFrame],
                              benchmark: str = "SPY") -> list:
    """Monthly rebalance: last trading day of every month in the benchmark
    calendar. Returns sorted list of pd.Timestamp."""
    bench = all_data.get(benchmark)
    if bench is None or len(bench) == 0:
        return []
    dates = bench["datetime"].sort_values()
    s = pd.Series(dates.values)
    groups = s.groupby([s.dt.year, s.dt.month])
    last = groups.max()
    return sorted(pd.Timestamp(d) for d in last.tolist())


# ---------------------------------------------------------------------------
# Layer 4 — Dynamic Backtest Engine
# ---------------------------------------------------------------------------
class DynamicBacktestEngine:
    def __init__(self, cfg: Config, all_data: dict, rebalance_dates: list,
                 bucket_selections: dict, top_n: int = 20, start: str = None,
                 end: str = None, no_regime: bool = False, benchmark: str = None,
                 progress: bool = True):
        self.cfg = cfg
        self.all_data = all_data
        self.rebalance_dates = rebalance_dates          # list[Timestamp]
        self.bucket_selections = bucket_selections      # {rd_str: [tickers]}
        self.top_n = top_n
        self.start = start or cfg.backtest_start
        self.end = end or cfg.backtest_end
        self.no_regime = no_regime
        self.benchmark = benchmark or getattr(cfg, "regime_market_index", "SPY")
        self.market_index = self.benchmark
        self.progress = progress

        self.equity_curve = []
        self.regime_log = []
        self.rebalance_log = []   # diagnostics: {date, selected, n}

    # ---- rebalance bucket mapping --------------------------------------
    def _build_active_bucket_map(self, all_dates: list) -> dict:
        rb_ts = sorted(pd.Timestamp(r) for r in self.rebalance_dates)
        rb_str = [r.strftime("%Y-%m-%d") for r in rb_ts]
        m = {}
        for d in all_dates:
            idx = bisect.bisect_right(rb_ts, d) - 1
            m[d.strftime("%Y-%m-%d")] = rb_str[idx] if idx >= 0 else None
        return m

    # ---- regime (copied logic from static engine; global on SPY) -------
    def _market_regime_for(self, spy_df: pd.DataFrame, date_idx: int,
                           cache: dict, cfg) -> dict | None:
        close = spy_df["close"].astype(float)
        feats = compute_features(close, cfg.hmm_vol_window)
        if len(feats) < cfg.hmm_min_obs:
            return None

        market_idx = self.market_index
        cached = cache.get(market_idx)
        need_fit = (cached is None) or ((date_idx - cached["last_fit_idx"]) >= cfg.regime_refit_days)
        if need_fit:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    model = fit_hmm(feats.values, cfg)
                    states, stats, labels, trending, spread = decode_and_label(model, feats, cfg)
                except Exception:
                    return None
            posteriors = model.predict_proba(feats.values)
            cache[market_idx] = {
                "model": model, "labels": labels, "trending": trending,
                "last_fit_idx": date_idx, "posteriors": posteriors, "states": states,
            }
        else:
            c = cache[market_idx]
            model = c["model"]
            labels = c["labels"]
            trending = c["trending"]
            try:
                states = model.predict(feats.values)
                posteriors = model.predict_proba(feats.values)
            except Exception:
                return None

        latest_state = int(states[-1])
        latest_prob = float(posteriors[-1, latest_state])
        regime_label = labels.get(latest_state, "SIDEWAYS")
        if not trending:
            regime_label = "RANGE_BOUND"

        hmm_result = RegimeResult(
            regime=regime_label, trending=trending, latest_prob=latest_prob,
            switch_confidence=1.0 - latest_prob, labels=labels, stats={},
            spread=0.0, latest_state=latest_state, n_states=model.n_components,
        )
        sr = regime_score_engine(spy_df, cfg, hmm_result=hmm_result, ticker=market_idx)
        score_normalized = (sr["regime_score"] - 50.0) / 50.0
        return {
            "regime": regime_label, "trending": trending,
            "latest_prob": latest_prob,
            "regime_score": sr["regime_score"],
            "score": round(score_normalized, 3),
            "position_size_mult": sr["position_size_mult"],
            "strategy": sr["strategy"],
            "components": sr["components"],
            "vetoes": sr.get("vetoes", []),
        }

    # ---- main daily loop ------------------------------------------------
    def run(self) -> dict:
        cfg = self.cfg
        benchmark = self.benchmark
        market_data = self.all_data.get(benchmark)
        if market_data is None or len(market_data) < 60:
            print("  ⚠️ benchmark data insufficient")
            return {}

        start_ts = pd.Timestamp(self.start)
        end_ts = pd.Timestamp(self.end) if self.end else pd.Timestamp.max
        all_dates = sorted(d for d in market_data["datetime"].tolist()
                           if start_ts <= d <= end_ts)
        if not all_dates:
            print("  ⚠️ no trading dates in range")
            return {}

        active_bucket = self._build_active_bucket_map(all_dates)

        # Pre-screen already done externally; bucket_selections is the dict.
        # (Screening is independent of regime weights, so it is shared across
        #  all comparison variants — done once in run_comparison.)

        state = default_state(cfg.capital_usd)
        self.state = state  # capture for _summary()
        hmm_cache: dict = {}
        n_total = len(all_dates)

        for di, date in enumerate(all_dates):
            date_str = date.strftime("%Y-%m-%d")
            rd = active_bucket.get(date_str)
            allowed = set(self.bucket_selections.get(rd, [])) if rd else set()

            # Tickers we need prices for: this month's candidates + anything held
            held = {p["ticker"] for p in state["open_positions"]}
            needed = allowed | held
            prices = {}
            slices = {}
            for t in needed:
                df = self.all_data.get(t)
                if df is None:
                    continue
                sub = df[df["datetime"] <= date]
                if len(sub) < 60:
                    continue
                prices[t] = float(sub["close"].iloc[-1])
                slices[t] = sub

            if not slices:
                self.equity_curve.append({
                    "date": date_str, "equity": round(state["equity"], 2),
                    "cash": round(state["cash"], 2),
                    "n_positions": len(state["open_positions"]),
                })
                continue

            mark_to_market(state, prices)

            # ---- Global market regime (SPY) ----
            spy_sub = market_data[market_data["datetime"] <= date]
            if self.no_regime:
                global_size_mult = 1.0
                global_strategy = "trend_following"
                global_score = 100.0
                global_regime_s = 1.0
                global_regime_label = "BULL"
                self.regime_log.append({
                    "date": date_str, "market_index": benchmark,
                    "regime": "BULL(no_filter)", "score": 100.0,
                    "strategy": "trend_following", "size_mult": 1.0,
                    "components": {},
                })
            else:
                info = self._market_regime_for(spy_sub, di, hmm_cache, cfg)
                if info is None:
                    global_size_mult = 0.0
                    global_strategy = "cash"
                    global_score = 0.0
                    global_regime_s = -1.0
                    global_regime_label = "UNKNOWN"
                else:
                    global_size_mult = info["position_size_mult"]
                    global_strategy = info["strategy"]
                    global_score = info["regime_score"]
                    global_regime_s = info["score"]
                    global_regime_label = info["regime"]
                    self.regime_log.append({
                        "date": date_str, "market_index": benchmark,
                        "regime": info["regime"], "score": global_score,
                        "strategy": global_strategy,
                        "size_mult": global_size_mult,
                        "components": info.get("components", {}),
                    })

            # ---- Exit checks (all held positions) ----
            i = 0
            while i < len(state["open_positions"]):
                pos = state["open_positions"][i]
                t = pos["ticker"]
                if t not in slices:
                    i += 1
                    continue
                price = prices[t]
                tech_sig = "neutral"
                try:
                    tsig = technicals_signal(slices[t], cfg)
                    tech_sig = tsig["signal"]
                except Exception:
                    pass
                ex = _exit_check(pos, price, date_str, tech_sig, cfg)
                if ex is not None:
                    close_position(state, i, price, date_str, ex["reason"], prices)
                    if self.state["trade_log"]:
                        try:
                            held_days = (datetime.strptime(date_str, "%Y-%m-%d") -
                                         datetime.strptime(state["trade_log"][-1]["entry_date"],
                                                           "%Y-%m-%d")).days
                            state["trade_log"][-1]["holding_days"] = held_days
                        except Exception:
                            pass
                else:
                    i += 1

            # ---- Entries (only from active bucket, if regime allows) ----
            if global_size_mult > 0:
                for t in allowed:
                    if t not in slices:
                        continue
                    if t in held:
                        continue
                    try:
                        tsig = technicals_signal(slices[t], cfg)
                    except Exception:
                        continue
                    tech_score = tsig.get("score", 0.0)
                    net = cfg.regime_weight * global_regime_s + cfg.technicals_weight * tech_score
                    if net <= cfg.entry_threshold:
                        continue
                    atr_val = tsig.get("atr", 0.0)
                    n_open = len(state["open_positions"])
                    sizing = size_position(state["equity"], state["cash"],
                                           prices[t], atr_val, global_size_mult, n_open, cfg)
                    if not sizing.get("allow"):
                        continue
                    pos = {
                        "ticker": t, "direction": "LONG", "shares": sizing["shares"],
                        "entry_price": prices[t], "entry_date": date_str,
                        "atr_at_entry": atr_val, "stop_price": sizing["stop_price"],
                        "take_profit": sizing["take_profit"], "highest_since_entry": prices[t],
                        "entry_regime": global_regime_label if not self.no_regime else "N/A(no_regime)",
                        "entry_regime_score": global_score,
                        "entry_market_strategy": global_strategy,
                        "entry_reasoning": f"net={net:.3f}, tech={tsig['signal']}, "
                                           f"market_score={global_score:.0f}, size_mult={global_size_mult:.2f}",
                    }
                    open_position(state, pos, prices[t], cfg)

            self.equity_curve.append({
                "date": date_str, "equity": round(state["equity"], 2),
                "cash": round(state["cash"], 2),
                "n_positions": len(state["open_positions"]),
            })

            # ---- Rebalance diagnostics ----
            if date_str in self.bucket_selections:
                self.rebalance_log.append({
                    "date": date_str,
                    "selected": sorted(self.bucket_selections[date_str]),
                    "n": len(self.bucket_selections[date_str]),
                })

            if self.progress and (di % 60 == 0 or di == n_total - 1):
                print(f"    [{date_str}] equity=${state['equity']:.0f} "
                      f"pos={len(state['open_positions'])} bucket={len(allowed)}")

        # Close remaining at end
        last_prices = {t: float(self.all_data[t]["close"].iloc[-1])
                       for t in {p["ticker"] for p in state["open_positions"]}
                       if t in self.all_data}
        last_date = all_dates[-1].strftime("%Y-%m-%d")
        i = 0
        while i < len(state["open_positions"]):
            pos = state["open_positions"][i]
            px = last_prices.get(pos["ticker"], pos["entry_price"])
            close_position(state, i, px, last_date, "BACKTEST_END", last_prices)

        return self._summary()

    def _summary(self) -> dict:
        return {
            "equity_curve": self.equity_curve,
            "trade_log": self.state["trade_log"],
            "regime_log": self.regime_log,
            "rebalance_log": self.rebalance_log,
            "final_equity": self.state["equity"],
            "starting_equity": self.cfg.capital_usd,
            "no_regime": self.no_regime,
            "tickers": sorted({t for v in self.bucket_selections.values() for t in v}),
        }


# ---------------------------------------------------------------------------
# Layer 5 — Comparison
# ---------------------------------------------------------------------------
def _exposure_pct(equity_curve: list) -> float:
    if not equity_curve:
        return 0.0
    n = len(equity_curve)
    inv = sum(1 for e in equity_curve if e["n_positions"] > 0)
    return round(inv / n * 100, 1)


REGIME_VARIANTS = {
    "No Regime Filter": {"no_regime": True, "weights": None},
    "Current Weights":  {"no_regime": False,
                         "weights": {"hmm": 0.20, "ma": 0.30, "ker": 0.18,
                                     "adx": 0.10, "dist": 0.22}},
    "Reviewer Weights": {"no_regime": False,
                         "weights": {"hmm": 0.25, "ma": 0.30, "ker": 0.20,
                                     "adx": 0.10, "dist": 0.15}},
    "Equal Weights":    {"no_regime": False,
                         "weights": {"hmm": 0.20, "ma": 0.20, "ker": 0.20,
                                     "adx": 0.20, "dist": 0.20}},
}


def run_comparison(start: str = "2024-01-01", end: str = "2025-07-31",
                   top_n: int = 20, benchmark: str = "SPY",
                   build_cache: bool = True, verbose: bool = True,
                   cache_start: str = "2018-01-01") -> dict:
    """MVP + 4-variant comparison on the dynamic (monthly re-screened) universe.

    cache_start: how far back to fetch OHLCV (must predate `start` by enough for
    HMM warmup [200d] and screener RS lookback). Use "2016-01-01" for a backtest
    starting 2018-01-01 so the regime engine + screener have full warmup.
    """
    if verbose:
        print("=== Dynamic Screening Backtest — Layer 1: cache ===")
    all_data = get_all_data(start=cache_start, end=end, verbose=verbose) \
        if build_cache else get_all_data(start=cache_start, end=end,
                                         force_refresh=False, verbose=verbose)

    if benchmark not in all_data:
        raise RuntimeError(f"Benchmark {benchmark} missing from cache")

    # Layer 2: rebalance calendar
    if verbose:
        print("=== Layer 2: rebalance calendar ===")
    all_rd = build_rebalance_calendar(all_data, benchmark)
    # Only screen buckets relevant to the backtest window (+1 month before start)
    start_ts = pd.Timestamp(start)
    screen_from = start_ts - pd.Timedelta(days=40)
    end_ts = pd.Timestamp(end)
    rd_to_screen = [r for r in all_rd if r <= end_ts and r >= screen_from]
    if verbose:
        print(f"  {len(all_rd)} month-ends total; screening "
              f"{len(rd_to_screen)} relevant buckets")

    # Layer 3: screen all buckets ONCE (independent of regime weights)
    # Cache to disk so re-runs (e.g. adding a variant) skip the expensive scan.
    buckets_cache = os.path.join(
        _REPO_ROOT, "reports",
        f"dynamic_buckets_{start}_{end}_top{top_n}.json")
    if os.path.exists(buckets_cache):
        if verbose:
            print(f"=== Layer 3: loading cached buckets ({buckets_cache}) ===")
        with open(buckets_cache) as f:
            bucket_selections = json.load(f)
    else:
        if verbose:
            print("=== Layer 3: point-in-time screening (once, shared) ===")
        bucket_selections = screen_all_buckets(all_data, rd_to_screen, cfg=Config(),
                                                top_n=top_n, benchmark=benchmark,
                                                verbose=verbose)
        os.makedirs(os.path.dirname(buckets_cache), exist_ok=True)
        with open(buckets_cache, "w") as f:
            json.dump(bucket_selections, f, indent=1)

    # Layer 4+5: run each variant
    if verbose:
        print("=== Layer 4+5: backtest variants ===")
    results = {}
    spy_eq = _spy_buyhold_equity(all_data[benchmark], start, end,
                                 Config().capital_usd)
    for name, spec in REGIME_VARIANTS.items():
        cfg = Config()
        if spec["weights"] is not None:
            cfg.regime_score_weights = spec["weights"]
        eng = DynamicBacktestEngine(
            cfg, all_data, all_rd, bucket_selections, top_n=top_n,
            start=start, end=end, no_regime=spec["no_regime"],
            benchmark=benchmark, progress=verbose,
        )
        if verbose:
            print(f"\n--- Variant: {name} ---")
        summary = eng.run()
        metrics = compute_metrics(summary, cfg)
        metrics["exposure_pct"] = _exposure_pct(summary["equity_curve"])
        metrics["n_buckets"] = len(eng.rebalance_log)
        metrics["universe_size"] = len(summary["tickers"])
        results[name] = {
            "summary": summary, "metrics": metrics,
            "rebalance_log": eng.rebalance_log,
        }
        if verbose:
            m = metrics
            print(f"  Return={m['total_return_pct']:+.2f}%  Sharpe={m['sharpe']:.2f}  "
                  f"MaxDD={m['max_drawdown_pct']:.2f}%  PF={m['profit_factor']}  "
                  f"Win={m['win_rate']*100:.1f}%  Trades={m['total_trades']}  "
                  f"Exp={m['exposure_pct']}%")

    results["_spy_buyhold"] = spy_eq
    results["_bucket_selections"] = bucket_selections
    results["_meta"] = {
        "start": start, "end": end, "top_n": top_n, "benchmark": benchmark,
        "n_buckets_total": len(rd_to_screen), "cache_start": cache_start,
    }
    return results


def _spy_buyhold_equity(spy_df: pd.DataFrame, start: str, end: str,
                        capital: float) -> dict:
    sub = spy_df[(spy_df["datetime"] >= pd.Timestamp(start)) &
                 (spy_df["datetime"] <= pd.Timestamp(end))].copy()
    if len(sub) < 2:
        return {"equity_curve": [], "total_return_pct": 0.0}
    sub = sub.sort_values("datetime").reset_index(drop=True)
    close0 = float(sub["close"].iloc[0])
    curve = []
    for _, row in sub.iterrows():
        curve.append({
            "date": row["datetime"].strftime("%Y-%m-%d"),
            "equity": round(capital * float(row["close"]) / close0, 2),
        })
    ret = (float(sub["close"].iloc[-1]) / close0 - 1) * 100
    return {"equity_curve": curve, "total_return_pct": round(ret, 2)}


# ===========================================================================
# Graduation analysis: full-period + regime-segmented + decision table
# ===========================================================================
# Market phases spanning 2018-2025. Stress periods are explicitly labelled so
# the segmented report can show whether the regime engine earns its keep in
# BAD markets (its actual job), not just bull runs.
MARKET_PHASES = [
    ("2018 Q4 Selloff",     "2018-10-01", "2018-12-31", "stress"),
    ("2019 Bull",           "2019-01-01", "2019-12-31", "normal"),
    ("2020 COVID Crash",    "2020-02-19", "2020-04-30", "stress"),
    ("2020-21 Recovery",    "2020-05-01", "2021-12-31", "normal"),
    ("2022 Bear",           "2022-01-01", "2022-10-12", "stress"),
    ("2022-23 Recovery",    "2022-10-13", "2023-12-31", "normal"),
    ("2024 AI Bull",        "2024-01-01", "2024-12-31", "normal"),
    ("2025 (to Jul)",       "2025-01-01", "2025-07-31", "normal"),
]


def _phase_metrics(summary: dict, start: str, end: str) -> dict:
    """Compute Return/Sharpe/MaxDD/PF/Win/Trades/Exposure/Cash% for a sub-period."""
    s_ts = pd.Timestamp(start)
    e_ts = pd.Timestamp(end)
    eq = [e for e in summary["equity_curve"] if s_ts <= pd.Timestamp(e["date"]) <= e_ts]
    trades = [t for t in summary["trade_log"]
              if s_ts <= pd.Timestamp(t.get("entry_date", "")) <= e_ts]
    rlog = [r for r in summary.get("regime_log", [])
            if s_ts <= pd.Timestamp(r["date"]) <= e_ts]

    out = {"start": start, "end": end, "trades": len(trades),
           "return_pct": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0,
           "win_rate": 0.0, "pf": 0.0, "exposure_pct": 0.0, "cash_pct": 0.0}
    if len(eq) < 2:
        return out
    eq_df = pd.DataFrame(eq)
    ret = (eq_df["equity"].iloc[-1] / eq_df["equity"].iloc[0] - 1) * 100
    out["return_pct"] = round(ret, 2)
    eq_df["r"] = eq_df["equity"].pct_change()
    rets = eq_df["r"].dropna()
    if len(rets) > 1 and rets.std() > 0:
        out["sharpe"] = round(float(np.sqrt(252) * rets.mean() / rets.std()), 2)
    rolling = eq_df["equity"].cummax()
    dd = ((eq_df["equity"] - rolling) / rolling * 100).min()
    out["max_dd_pct"] = round(float(dd), 2)
    if trades:
        pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        out["win_rate"] = round(len(wins) / len(trades), 3)
        gw, gl = sum(wins), abs(sum(losses))
        out["pf"] = round(gw / gl, 2) if gl > 0 else float("inf")
    inv = sum(1 for e in eq if e["n_positions"] > 0)
    out["exposure_pct"] = round(inv / len(eq) * 100, 1)
    if rlog:
        cash = sum(1 for r in rlog if r["strategy"] == "cash")
        out["cash_pct"] = round(cash / len(rlog) * 100, 1)
    return out


def segmented_breakdown(results: dict) -> dict:
    """{variant: [phase_metrics per MARKET_PHASES]}."""
    out = {}
    for name in REGIME_VARIANTS:
        if name not in results:
            continue
        out[name] = [_phase_metrics(results[name]["summary"], s, e)
                     for (_, s, e, _) in MARKET_PHASES]
    return out


def decision_table(results: dict) -> list:
    """Engineering decision table (full-period). Each row: question/criterion/result/detail."""
    m = {v: results[v]["metrics"] for v in REGIME_VARIANTS if v in results}
    if not all(k in m for k in ("No Regime Filter", "Current Weights",
                                "Reviewer Weights", "Equal Weights")):
        return [{"question": "incomplete", "result": "N/A"}]
    nr, cu, rv, eq = (m["No Regime Filter"], m["Current Weights"],
                      m["Reviewer Weights"], m["Equal Weights"])

    rows = []
    # Q1: regime reduces MaxDD
    cu_better = cu["max_drawdown_pct"] > nr["max_drawdown_pct"]  # less negative = greater
    rv_better = rv["max_drawdown_pct"] > nr["max_drawdown_pct"]
    rows.append({
        "question": "Regime 是否降低 MaxDD？",
        "criterion": "Current & Reviewer MaxDD > No Regime MaxDD（較不負）",
        "result": "PASS" if (cu_better and rv_better) else
                  ("PARTIAL" if (cu_better or rv_better) else "FAIL"),
        "detail": f"NoRegime={nr['max_drawdown_pct']:.2f}%  "
                  f"Current={cu['max_drawdown_pct']:.2f}%  "
                  f"Reviewer={rv['max_drawdown_pct']:.2f}%",
    })
    # Q2: regime raises PF
    def _pf(x):
        return 0.0 if x["profit_factor"] == "inf" else float(x["profit_factor"])
    cu_pf, rv_pf, nr_pf = _pf(cu), _pf(rv), _pf(nr)
    rows.append({
        "question": "Regime 是否提高 PF？",
        "criterion": "Current & Reviewer PF > No Regime PF",
        "result": "PASS" if (cu_pf > nr_pf and rv_pf > nr_pf) else "FAIL",
        "detail": f"NoRegime={nr_pf:.2f}  Current={cu_pf:.2f}  Reviewer={rv_pf:.2f}",
    })
    # Q3: equal weights behind (weights matter)
    eq_behind = eq["total_return_pct"] < max(cu["total_return_pct"], rv["total_return_pct"])
    rows.append({
        "question": "Equal weights 是否落後（權重設計有價值）？",
        "criterion": "Equal return < max(Current, Reviewer) return",
        "result": "PASS" if eq_behind else "FAIL",
        "detail": f"Equal={eq['total_return_pct']:+.2f}%  "
                  f"Current={cu['total_return_pct']:+.2f}%  "
                  f"Reviewer={rv['total_return_pct']:+.2f}%",
    })
    # Q4: reviewer worth switching (>=3 of return/sharpe/MaxDD/PF better)
    cu_wins = sum([
        cu["total_return_pct"] > rv["total_return_pct"],
        cu["sharpe"] > rv["sharpe"],
        cu["max_drawdown_pct"] > rv["max_drawdown_pct"],
        cu_pf > rv_pf,
    ])
    rv_wins = 4 - cu_wins
    rows.append({
        "question": "Reviewer 是否值得切換？",
        "criterion": "Reviewer 在 4 指標中贏 >=3",
        "result": "YES" if rv_wins >= 3 else ("TIE" if rv_wins == 2 else "NO"),
        "detail": f"Reviewer 贏 {rv_wins}/4（return/sharpe/MaxDD/PF）；"
                  f"Current 贏 {cu_wins}/4",
    })
    # Q5: exposure reasonable
    exp = cu["exposure_pct"]
    rows.append({
        "question": "Exposure 是否合理（非全空也非全滿）？",
        "criterion": "30% <= Current exposure <= 95%",
        "result": "PASS" if 30 <= exp <= 95 else "FAIL",
        "detail": f"Current exposure={exp}%",
    })
    return rows


def screener_diagnostics(results: dict) -> list:
    """Per-bucket diagnostics: selected tickers, regime context, next-month activity.

    Uses 'Current Weights' as the reference variant for regime context (regime is
    identical across weighted variants on the same day except No-Regime).
    """
    ref = results.get("Current Weights")
    if ref is None:
        return []
    regime_log = {r["date"]: r for r in ref["summary"].get("regime_log", [])}
    trade_log = ref["summary"]["trade_log"]
    eq = ref["summary"]["equity_curve"]
    eq_map = {e["date"]: e["equity"] for e in eq}
    buckets = results.get("_bucket_selections", {})
    rd_sorted = sorted(buckets.keys())
    out = []
    for i, rd in enumerate(rd_sorted):
        next_rd = rd_sorted[i + 1] if i + 1 < len(rd_sorted) else None
        rl = regime_log.get(rd, {})
        # next-month trades + return
        nm_trades = 0
        if next_rd:
            nm_trades = sum(1 for t in trade_log
                            if rd < t.get("entry_date", "") <= next_rd)
        else:
            nm_trades = sum(1 for t in trade_log if t.get("entry_date", "") > rd)
        nm_ret = 0.0
        if next_rd and rd in eq_map and next_rd in eq_map:
            nm_ret = round((eq_map[next_rd] / eq_map[rd] - 1) * 100, 2)
        out.append({
            "rebalance_date": rd,
            "selected_tickers": buckets[rd],
            "n_selected": len(buckets[rd]),
            "regime_score": rl.get("score"),
            "strategy": rl.get("strategy"),
            "size_mult": rl.get("size_mult"),
            "next_month_trades": nm_trades,
            "next_month_return_pct": nm_ret,
        })
    return out


def build_segmented_html(results: dict, segmented: dict, decision: list,
                         out_path: str) -> str:
    meta = results["_meta"]
    spy = results["_spy_buyhold"]

    # decision table
    dec_html = ""
    for d in decision:
        color = {"PASS": "#c8e6c9", "FAIL": "#ffcdd2", "PARTIAL": "#fff9c4",
                 "YES": "#c8e6c9", "NO": "#ffcdd2", "TIE": "#fff9c4",
                 "N/A": "#eeeeee"}.get(d["result"], "#eeeeee")
        dec_html += (f"<tr><td>{d['question']}</td><td>{d['criterion']}</td>"
                     f"<td style='background:{color};text-align:center'><b>{d['result']}</b></td>"
                     f"<td>{d['detail']}</td></tr>")

    # segmented table: one block per variant
    seg_html = ""
    for name in REGIME_VARIANTS:
        if name not in segmented:
            continue
        rows = ""
        for (pname, s, e, ptype), pm in zip(MARKET_PHASES, segmented[name]):
            tag = " <span class='stress'>⚠ stress</span>" if ptype == "stress" else ""
            pf = pm["pf"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            rows += (f"<tr><td>{pname}{tag}</td>"
                     f"<td>{pm['return_pct']:+.2f}</td>"
                     f"<td>{pm['sharpe']:.2f}</td>"
                     f"<td>{pm['max_dd_pct']:.2f}</td>"
                     f"<td>{pf_s}</td>"
                     f"<td>{pm['win_rate']*100:.0f}</td>"
                     f"<td>{pm['trades']}</td>"
                     f"<td>{pm['exposure_pct']}</td>"
                     f"<td>{pm['cash_pct']}</td></tr>")
        seg_html += (f"<h3>{name}</h3><table>"
                     f"<tr><th>Phase</th><th>Ret%</th><th>Sharpe</th><th>MaxDD%</th>"
                     f"<th>PF</th><th>Win%</th><th>Trades</th><th>Exp%</th>"
                     f"<th>Cash%</th></tr>{rows}</table>")

    # full-period summary table
    full_html = ""
    for name in REGIME_VARIANTS:
        if name not in results:
            continue
        m = results[name]["metrics"]
        pf = m["profit_factor"]
        pf_s = "inf" if pf == "inf" else f"{pf:.2f}"
        full_html += (f"<tr><td>{name}</td>"
                      f"<td>{m['total_return_pct']:+.2f}</td>"
                      f"<td>{m['sharpe']:.2f}</td>"
                      f"<td>{m['max_drawdown_pct']:.2f}</td>"
                      f"<td>{pf_s}</td>"
                      f"<td>{m['win_rate']*100:.1f}</td>"
                      f"<td>{m['total_trades']}</td>"
                      f"<td>{m['exposure_pct']}</td></tr>")
    full_html += (f"<tr style='background:#f0f0f0'><td>SPY Buy&Hold</td>"
                  f"<td>{spy['total_return_pct']:+.2f}</td><td>-</td><td>-</td>"
                  f"<td>-</td><td>-</td><td>-</td><td>100</td></tr>")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Graduation Report — 2018-2025 Full Cycle</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 32px; color:#222; }}
 h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 30px; }} h3 {{ font-size: 14px; margin: 14px 0 4px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 8px 0 18px; font-size: 12.5px; }}
 th, td {{ border: 1px solid #ccc; padding: 5px 8px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 th {{ background: #fafafa; }}
 .stress {{ color:#c00; font-size: 11px; font-weight: bold; }}
 .note {{ background:#fff8e1; border-left:4px solid #ffc107; padding:10px 14px; margin:14px 0; font-size:13px; }}
</style></head>
<body>
<h1>Graduation Report — Regime Engine Across Full Market Cycle</h1>
<p>Period <b>{meta['start']} ~ {meta['end']}</b> · Monthly rebalance · Top {meta['top_n']} ·
Benchmark {meta['benchmark']} · {meta['n_buckets_total']} buckets · cache from {meta.get('cache_start','?')}.
<br><b>NO parameter changes</b> — this is a freeze-and-test run (champion=Current, challenger=Reviewer,
baseline=Equal, control=No-Regime).</p>

<h2>1. Full-Period Summary</h2>
<table><tr><th>Variant</th><th>Ret%</th><th>Sharpe</th><th>MaxDD%</th><th>PF</th>
<th>Win%</th><th>Trades</th><th>Exp%</th></tr>
{full_html}</table>

<h2>2. Decision Table</h2>
<table><tr><th style='text-align:left'>Question</th><th style='text-align:left'>Pass Criterion</th>
<th>Result</th><th style='text-align:left'>Detail</th></tr>
{dec_html}</table>

<h2>3. Regime-Segmented Breakdown (per variant)</h2>
<p>Stress phases (2018 Q4, COVID, 2022 Bear) are where the regime engine is supposed to earn its keep.</p>
{seg_html}

<div class="note"><b>Read this as:</b> does the regime engine cut MaxDD and raise PF <i>especially in
the ⚠ stress rows</i>? A bull-only win (2024-25) is necessary but not sufficient — robustness means
the stress phases don't blow up.</div>

<div class="note"><b>Known biases (v1):</b> survivorship bias (current index constituents only),
market-cap approximation (no historical mcap), point-in-time membership assumption. NOT
survivorship-bias-free.</div>
</body></html>"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def run_graduation(start: str = "2018-01-01", end: str = "2025-07-31",
                   top_n: int = 20, out_dir: str = None, verbose: bool = True) -> dict:
    """Full-cycle graduation run: 4 variants, segmented + decision table + diagnostics."""
    if out_dir is None:
        out_dir = os.path.join(_REPO_ROOT, "reports")
    os.makedirs(out_dir, exist_ok=True)

    results = run_comparison(start=start, end=end, top_n=top_n,
                             build_cache=False, verbose=verbose,
                             cache_start="2016-01-01")
    segmented = segmented_breakdown(results)
    decision = decision_table(results)
    diag = screener_diagnostics(results)

    # Report 1: full-period comparison (reuse existing builder)
    r1 = os.path.join(out_dir, "graduation_full_period.html")
    build_html_report(results, r1)
    # Report 2: segmented + decision table
    r2 = os.path.join(out_dir, "graduation_segmented.html")
    build_segmented_html(results, segmented, decision, r2)
    # Report 3: screener diagnostics (JSON)
    r3 = os.path.join(out_dir, "graduation_screener_diagnostics.json")
    with open(r3, "w") as f:
        json.dump({"meta": results["_meta"], "decision_table": decision,
                   "phases": [p[0] for p in MARKET_PHASES],
                   "screener_diagnostics": diag}, f, indent=1, default=str)

    if verbose:
        print("\n=== DECISION TABLE ===")
        for d in decision:
            print(f"  [{d['result']}] {d['question']}  ({d['detail']})")
        print(f"\n✅ Report 1 (full):   {r1}")
        print(f"✅ Report 2 (segment):{r2}")
        print(f"✅ Report 3 (diag):   {r3}")
    return {"results": results, "segmented": segmented,
            "decision": decision, "diagnostics": diag,
            "reports": [r1, r2, r3]}


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------
def _equity_chart_png(results: dict, start: str, end: str) -> str:
    """Render overlaid equity curves (4 variants + SPY B&H) -> base64 PNG."""
    fig, ax = plt.subplots(figsize=(11, 4.5))
    spy = results.get("_spy_buyhold", {}).get("equity_curve", [])
    if spy:
        xs = [pd.Timestamp(e["date"]) for e in spy]
        ys = [e["equity"] for e in spy]
        ax.plot(xs, ys, color="#888888", lw=1.3, ls="--", label="SPY Buy&Hold")

    colors = {"No Regime Filter": "#d62728", "Current Weights": "#1f77b4",
              "Reviewer Weights": "#2ca02c", "Equal Weights": "#ff7f0e"}
    for name, col in colors.items():
        if name not in results:
            continue
        eq = results[name]["summary"]["equity_curve"]
        if not eq:
            continue
        xs = [pd.Timestamp(e["date"]) for e in eq]
        ys = [e["equity"] for e in eq]
        ax.plot(xs, ys, color=col, lw=1.4, label=name)

    ax.set_title(f"Dynamic Universe Backtest — Equity Curves ({start} ~ {end})",
                 fontsize=12)
    ax.set_ylabel("Equity (USD)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_html_report(results: dict, out_path: str) -> str:
    meta = results["_meta"]
    spy = results["_spy_buyhold"]
    chart_b64 = _equity_chart_png(results, meta["start"], meta["end"])

    variants = [v for v in REGIME_VARIANTS.keys() if v in results]
    # table
    headers = ["Variant", "Return%", "Sharpe", "MaxDD%", "PF", "Win%",
               "Trades", "Exposure%", "Universe"]
    rows_html = ""
    for v in variants:
        m = results[v]["metrics"]
        pf = m["profit_factor"]
        pf_s = "inf" if pf == "inf" else f"{pf:.2f}"
        rows_html += (
            f"<tr><td>{v}</td>"
            f"<td>{m['total_return_pct']:+.2f}</td>"
            f"<td>{m['sharpe']:.2f}</td>"
            f"<td>{m['max_drawdown_pct']:.2f}</td>"
            f"<td>{pf_s}</td>"
            f"<td>{m['win_rate']*100:.1f}</td>"
            f"<td>{m['total_trades']}</td>"
            f"<td>{m['exposure_pct']}</td>"
            f"<td>{m['universe_size']}</td></tr>"
        )
    spy_row = (
        f"<tr style='background:#f0f0f0'><td>SPY Buy&amp;Hold</td>"
        f"<td>{spy['total_return_pct']:+.2f}</td><td>-</td><td>-</td>"
        f"<td>-</td><td>-</td><td>-</td><td>100</td><td>1</td></tr>"
    )

    # rebalance sample (first 6 buckets)
    rb_html = ""
    for v in variants:
        rl = results[v].get("rebalance_log", [])
        if not rl:
            continue
        sample = rl[:6]
        items = "".join(
            f"<li><b>{b['date']}</b>: {', '.join(b['selected'][:12])}"
            f"{' …' if len(b['selected']) > 12 else ''} ({b['n']})</li>"
            for b in sample
        )
        rb_html += f"<h4>{v} — first rebalances</h4><ul>{items}</ul>"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Dynamic Screening Backtest Report</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 32px; color:#222; }}
 h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 28px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }}
 th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: right; }}
 th:first-child, td:first-child {{ text-align: left; }}
 th {{ background: #fafafa; }}
 .note {{ background:#fff8e1; border-left:4px solid #ffc107; padding:10px 14px; margin:14px 0; font-size:13px; }}
 .chart {{ margin: 18px 0; }}
 code {{ background:#f4f4f4; padding:1px 4px; border-radius:3px; }}
</style></head>
<body>
<h1>Dynamic Screening Backtest Report</h1>
<p>Period <b>{meta['start']} ~ {meta['end']}</b> · Monthly rebalance ·
Top {meta['top_n']} candidates · Benchmark {meta['benchmark']} ·
{results['_meta']['n_buckets_total']} rebalance buckets.</p>

<div class="chart"><img src="data:image/png;base64,{chart_b64}" style="width:100%"></div>

<h2>Performance Comparison (4 regime variants on the SAME dynamic universe)</h2>
<table>
<tr><th>{' </th><th>'.join(headers)}</tr>
{rows_html}
{spy_row}
</table>

<div class="note">
<b>What this removes vs the static test:</b> The earlier "screener top 20"
backtest screened <i>today</i> then backtested 2024 — that is look-ahead bias
(the 2024 strategy "knew" which stocks were strong in 2025). Here, at every
month-end we re-screen using <b>only data ≤ that date</b>, and trade the result
next month. The stock universe is therefore point-in-time.
</div>

<div class="note">
<b>Remaining known biases (v1, per reviewer):</b>
<ul>
<li><b>Survivorship bias:</b> universe = <i>current</i> S&P 500 + NASDAQ 100.
Delisted/acquired names are absent → optimistic.</li>
<li><b>Market-cap approximation:</b> no historical market cap fetched; index
membership is the large-cap proxy. Market-cap filter is intentionally skipped.</li>
<li><b>Point-in-time index membership:</b> we assume today's constituents existed
throughout. A stock added to the index in 2023 is still screened in 2019
(extra history just makes it pass RS more often).</li>
</ul>
</div>

<h2>Rebalance selection sample (point-in-time)</h2>
{rb_html}

<h2>How to read this</h2>
<p>Compare variants on <b>MaxDD</b> (did the regime filter reduce drawdown?),
<b>PF</b> (edge quality), and <b>Exposure%</b> (how often was capital deployed).
Higher return alone is not the goal — the regime engine's job is risk control
during bad markets, which a bull-market window (2024-2025) under-tests.</p>
</body></html>"""
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Dynamic screening backtest")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-07-31")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--no-cache-build", action="store_true",
                    help="Skip cache refresh (use existing cache files)")
    ap.add_argument("--out", default=None, help="HTML report output path")
    ap.add_argument("--full", action="store_true",
                    help="Run full-cycle graduation (2018-2025, segmented + "
                         "decision table + screener diagnostics). Freezes all params.")
    args = ap.parse_args()

    if args.full:
        run_graduation(start=args.start, end=args.end, top_n=args.top_n,
                       verbose=True)
    else:
        results = run_comparison(
            start=args.start, end=args.end, top_n=args.top_n,
            build_cache=not args.no_cache_build, verbose=True,
        )
        out = args.out or os.path.join(_REPO_ROOT, "reports", "dynamic_backtest_report.html")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        build_html_report(results, out)

        json_out = os.path.join(_REPO_ROOT, "reports", "dynamic_backtest_results.json")
        dump = {
            "meta": results["_meta"],
            "spy_buyhold_return_pct": results["_spy_buyhold"]["total_return_pct"],
            "variants": {
                v: {"metrics": results[v]["metrics"],
                    "rebalance_log": results[v]["rebalance_log"]}
                for v in REGIME_VARIANTS if v in results
            },
        }
        with open(json_out, "w") as f:
            json.dump(dump, f, indent=2, default=str)
        print(f"\n✅ Report: {out}")
        print(f"✅ JSON : {json_out}")
