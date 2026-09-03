"""
pipeline.py — Canonical daily decision pipeline (Production V2 unified)

run_daily(as_of, state, data_source, cfg, **screen_opts) -> DecisionRecord

The SINGLE deterministic decision entry point used by BOTH:

  A. live production briefing  (YFinanceSource, screen_mode="live")
  B. historical backtest        (CachedSource,   screen_mode="bucket")

It replays the frozen components in the frozen order:

  Data → Regime v1 → Screener → Setup v1 (Pullback) → Risk
       → Entry / Exit → Portfolio → DecisionRecord

Contract
--------
* Deterministic & serializable: pure function over (as_of, state, source,
  cfg); never reads the clock; no global mutable state; the input `state`
  dict is never mutated (deep-copied).
* Fail-loud (spec §5): EMPTY ≠ FAILURE. A screener or data FAILURE blocks new
  BUY recommendations but existing positions are STILL evaluated for exits
  whenever their data is available.
* Regime v1 / Setup v1 are called through their frozen production wrappers —
  nothing here re-implements or modifies them.

DecisionRecord sections: meta, data, regime, screener, setup, positions,
exits, entries, risk, portfolio, warnings, recommendations.
"""
from __future__ import annotations

import copy
import os
import sys

import pandas as pd

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from production.config import ProductionConfig
from production.agents.regime import compute_market_regime, load_breadth
from production.agents.setup import evaluate_setup
from production.screener.screener import screen_from_source
from production.risk.risk import size_swing_position
from production.portfolio.portfolio import exit_check, build_order_buy
from production.datasource import DataSource
from src.agents.technicals_agent import technicals_signal

SCHEMA_VERSION = "1.0"

# status values used across the record
ST_OK = "OK"
ST_EMPTY = "EMPTY"
ST_FAILURE = "FAILURE"
ST_PARTIAL = "PARTIAL"
ST_BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# DecisionRecord skeleton
# ---------------------------------------------------------------------------
def _skeleton(as_of: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "generated_by": "production.pipeline.run_daily",
        "pipeline_status": ST_OK,
        "data": {"benchmark": None, "breadth": None, "candidate_providers": {},
                 "status": ST_OK},
        "regime": {"status": ST_OK, "input_rows": None, "breadth_rows": None,
                   "output": None, "warnings": []},
        "screener": {"mode": None, "status": ST_OK, "error": None,
                     "n_candidates": 0, "top_n": None, "apply_mcap": None,
                     "filters_applied": [], "candidates": [],
                     "n_universe_checked": 0, "provider_counts": {},
                     "warnings": []},
        "setup": {"status": ST_OK, "evaluations": [], "n_valid": 0},
        "positions": {"n_open": 0, "max_open": 0, "equity": None, "cash": None,
                      "nav_mark": None, "gross_exposure": None,
                      "exposure_pct": None, "items": []},
        "exits": {"status": ST_OK, "proposed": [], "warnings": []},
        "entries": {"status": ST_OK, "blocked_reason": None,
                    "proposed": [], "n_buys": 0},
        "risk": {"sizing": [], "warnings": []},
        "portfolio": {"n_open_after": 0, "max_open": 0, "constraints": [],
                      "warnings": []},
        "warnings": [],
        "recommendations": {"summary": "", "buys": [], "sells": []},
    }


# ---------------------------------------------------------------------------
# Canonical daily function
# ---------------------------------------------------------------------------
def run_daily(as_of: str, state: dict, data_source: DataSource,
              cfg: ProductionConfig,
              *, screen_mode: str = "live",
              candidate_tickers: list[str] | None = None,
              screen_result: dict | None = None,
              universe: list[str] | None = None,
              verbose: bool = False) -> dict:
    """Produce the complete DecisionRecord for one date.

    Parameters
    ----------
    as_of : str            decision date "YYYY-MM-DD" (all data sliced <= it)
    state : dict          portfolio state (cash/equity/open_positions/...)
    data_source : DataSource   CachedSource (backtest) or YFinanceSource (live)
    cfg : ProductionConfig
    screen_mode : "live" | "bucket" | "provided" | "held_only"
        live     -> run the 6-filter screen on the source (network allowed)
        bucket   -> candidate_tickers = this month's pre-screened bucket
                    (screen already ran PIT at the rebalance date)
        provided -> candidate_tickers given directly (tests / manual)
        held_only-> no new candidates, exits only (--no-screen path)
    """
    rec = _skeleton(as_of)
    st = copy.deepcopy(state)          # never mutate caller's state
    held = [p["ticker"] for p in st["open_positions"]]
    held_set = set(held)
    rec["positions"]["max_open"] = cfg.max_open_positions

    # =====================================================================
    # 1. DATA
    # =====================================================================
    spy_df, spy_p = data_source.get_ohlcv(cfg.regime_market_index, as_of=as_of,
                                          min_bars=60)
    rec["data"]["benchmark"] = {"ticker": cfg.regime_market_index,
                                **spy_p.as_dict()}
    breadth_df = None
    breadth_warn = None
    try:
        breadth_df = load_breadth(cfg, end=as_of)
        if breadth_df is None or len(breadth_df) == 0:
            breadth_warn = "breadth series empty for end=" + as_of
    except Exception as exc:  # pragma: no cover - defensive
        breadth_warn = f"breadth load failed: {exc}"
    rec["data"]["breadth"] = {
        "kind": "pit-or-current", "rows": len(breadth_df) if breadth_df is not None else 0,
        "ok": breadth_df is not None and len(breadth_df) > 0,
        "error": breadth_warn,
    }
    data_ok = spy_df is not None
    rec["data"]["status"] = ST_OK if data_ok else ST_FAILURE

    # =====================================================================
    # 2. REGIME v1 (frozen wrapper)
    # =====================================================================
    regime = None
    regime_failure = None
    if not data_ok:
        regime_failure = f"benchmark {cfg.regime_market_index} data unavailable"
    else:
        try:
            regime = compute_market_regime(spy_df, breadth_df, cfg)
            rec["regime"]["input_rows"] = len(spy_df)
            rec["regime"]["breadth_rows"] = (len(breadth_df)
                                             if breadth_df is not None else 0)
            # public schema only (drop private _diag from the ledger output)
            rec["regime"]["output"] = {k: v for k, v in regime.items()
                                       if k != "_diag"}
            if breadth_warn:
                rec["regime"]["warnings"].append(breadth_warn)
        except Exception as exc:
            regime_failure = f"regime computation failed: {exc}"
    if regime_failure:
        rec["regime"]["status"] = ST_FAILURE
        rec["regime"]["warnings"].append(regime_failure)
        rec["warnings"].append(f"REGIME_FAILURE: {regime_failure}")
        rec["pipeline_status"] = ST_FAILURE
    if regime is None:
        regime = {"regime_label": "UNKNOWN", "composite_score": None,
                  "position_size_mult": 0.0, "strategy_mode": "unknown",
                  "veto_flags": []}

    # =====================================================================
    # 3. SCREENER (fail-loud: EMPTY vs FAILURE)
    # =====================================================================
    scr = rec["screener"]
    scr["mode"] = screen_mode
    if screen_mode == "held_only":
        scr["status"] = ST_EMPTY
        scr["error"] = "held-only mode (no new candidates requested)"
    elif screen_mode in ("bucket", "provided") and candidate_tickers is None:
        scr["status"] = ST_FAILURE
        scr["error"] = f"screen_mode={screen_mode} requires candidate_tickers"
    elif screen_mode == "bucket" and screen_result is not None:
        scr["status"] = screen_result.get("status", ST_FAILURE)
        scr["error"] = screen_result.get("error")
        scr["filters_applied"] = screen_result.get("filters_applied", [])
        scr["apply_mcap"] = screen_result.get("apply_mcap")
        scr["n_universe_checked"] = screen_result.get("n_universe_checked", 0)
        scr["provider_counts"] = screen_result.get("provider_counts", {})
        scr["warnings"] = screen_result.get("warnings", [])
        scr["candidates"] = screen_result.get("tickers", [])
        scr["n_candidates"] = len(scr["candidates"])
    elif screen_mode == "bucket" or screen_mode == "provided":
        # bucket passed as plain ticker list -> rank = list order (RS-sorted)
        scr["status"] = ST_OK
        scr["candidates"] = [{"ticker": t, "rs_rank": i + 1, "rs": None}
                             for i, t in enumerate(candidate_tickers)]
        scr["n_candidates"] = len(scr["candidates"])
        scr["warnings"].append(f"{screen_mode}: candidates supplied externally "
                               "(rs_rank = list order)")
    else:  # live
        try:
            screen_result = screen_from_source(data_source, cfg, as_of=as_of,
                                               universe=universe)
            scr["status"] = screen_result["status"]
            scr["error"] = screen_result.get("error")
            scr["filters_applied"] = screen_result["filters_applied"]
            scr["apply_mcap"] = screen_result["apply_mcap"]
            scr["n_universe_checked"] = screen_result["n_universe_checked"]
            scr["provider_counts"] = screen_result["provider_counts"]
            scr["warnings"] = screen_result["warnings"]
            scr["candidates"] = screen_result["tickers"]
            scr["n_candidates"] = len(screen_result["tickers"])
        except Exception as exc:
            scr["status"] = ST_FAILURE
            scr["error"] = f"screener crashed: {exc}"
            rec["warnings"].append("SCREENER_FAILURE: " + str(exc))

    rec["screener"]["top_n"] = cfg.screener_top_n
    rec["data"]["candidate_providers"] = scr["provider_counts"]

    screen_blocked = scr["status"] in (ST_FAILURE,)
    screen_empty = scr["status"] == ST_EMPTY
    if scr["status"] == ST_FAILURE:
        rec["warnings"].append(f"SCREENER_FAILURE: {scr['error']}")
        rec["pipeline_status"] = max(rec["pipeline_status"] or ST_OK,
                                     ST_PARTIAL, key=_severity)

    # =====================================================================
    # 4/5/6. POSITIONS + EXITS (independent of screener/regime success)
    # =====================================================================
    mark_prices: dict[str, float] = {}
    exit_eval_fail = []
    for pos in st["open_positions"]:
        t = pos["ticker"]
        df, prov = data_source.get_ohlcv(t, as_of=as_of, min_bars=60)
        if df is None:
            exit_eval_fail.append(t)
            continue
        bar = {"open": float(df["open"].iloc[-1]),
               "high": float(df["high"].iloc[-1]),
               "low": float(df["low"].iloc[-1]),
               "close": float(df["close"].iloc[-1])}
        mark_prices[t] = bar["close"]
        try:
            tech_sig = technicals_signal(df, cfg)["signal"]
        except Exception:
            rec["exits"]["warnings"].append(
                f"{t}: technicals_signal failed — using neutral (STOP/TARGET/"
                f"TRAILING/TIME still evaluated)")
            tech_sig = "neutral"
        ex = exit_check(pos, bar, as_of, tech_sig, cfg)
        if ex is not None:
            rec["exits"]["proposed"].append({
                "ticker": t, "shares": pos["shares"],
                "exit_price": float(ex["exit_price"]),
                "fill_model": ex["fill_model"],
                "reason": ex["reason"],
                "detail": ex["detail"],
                "mark_close": bar["close"],
            })
    if exit_eval_fail:
        msg = ("cannot evaluate exit for held "
               + ", ".join(exit_eval_fail) + " (data unavailable)")
        rec["exits"]["warnings"].append(msg)
        rec["warnings"].append("EXIT_EVAL_INCOMPLETE: " + msg)
        rec["pipeline_status"] = max(rec["pipeline_status"], ST_PARTIAL,
                                     key=_severity)
    rec["exits"]["status"] = (ST_PARTIAL if exit_eval_fail else ST_OK)

    # =====================================================================
    # NAV / portfolio snapshot (mark-to-market, deterministic)
    # =====================================================================
    try:
        from src.state.state import mark_to_market
        nav = mark_to_market(st, mark_prices)
        pos_value = sum(p["shares"] * mark_prices.get(p["ticker"],
                                                      p["entry_price"])
                        for p in st["open_positions"])
    except Exception:
        nav = st.get("equity", 0.0)
        pos_value = 0.0
    rec["positions"].update({
        "n_open": len(st["open_positions"]), "equity": round(nav, 2),
        "cash": round(st.get("cash", 0.0), 2),
        "nav_mark": round(nav, 2),
        "gross_exposure": round(pos_value, 2),
        "exposure_pct": round(pos_value / nav * 100.0, 2) if nav else 0.0,
        "items": [
            {"ticker": p["ticker"], "shares": p["shares"],
             "entry_price": p["entry_price"], "entry_date": p["entry_date"],
             "stop_price": p.get("stop_price"),
             "take_profit": p.get("take_profit"),
             "highest_since_entry": p.get("highest_since_entry"),
             "mark_price": mark_prices.get(p["ticker"]),
             "unrealized_pnl": round(
                 (mark_prices.get(p["ticker"], p["entry_price"]) - p["entry_price"])
                 * p["shares"], 2)}
            for p in st["open_positions"]
        ],
    })

    # =====================================================================
    # 7. ENTRIES (Setup v1 + Risk) — only when screen & regime data are OK
    # =====================================================================
    regime_allows = bool(regime.get("position_size_mult", 0.0) > 0)
    entries = rec["entries"]
    buys = []
    if regime_failure:
        entries["status"] = ST_BLOCKED
        entries["blocked_reason"] = "regime unavailable — no new longs"
    elif screen_blocked:
        entries["status"] = ST_BLOCKED
        entries["blocked_reason"] = "screener FAILURE — no new BUY from incomplete data"
    elif not regime_allows:
        entries["status"] = ST_EMPTY
        entries["blocked_reason"] = (f"regime {regime['regime_label']} — "
                                     "no new longs (defensive/cash)")
    else:
        n_open = len(st["open_positions"])
        n_budget = max(0, cfg.max_open_positions - n_open)
        if n_budget <= 0:
            entries["status"] = ST_EMPTY
            entries["blocked_reason"] = "max_open_positions reached"
        else:
            cands = scr.get("candidates", []) if not screen_empty else []
            for c in cands:
                t = c["ticker"]
                if t in held_set:
                    continue
                df, prov = data_source.get_ohlcv(t, as_of=as_of, min_bars=60)
                if df is None:
                    rec["setup"]["evaluations"].append({
                        "ticker": t, "skipped_reason": "insufficient data",
                        "provider": prov.provider})
                    continue
                try:
                    setup = evaluate_setup(df, cfg, rs_rank=c.get("rs_rank"))
                except Exception as exc:
                    rec["setup"]["evaluations"].append({
                        "ticker": t, "skipped_reason": f"setup crashed: {exc}"})
                    continue
                ev = {"ticker": t, "provider": prov.provider,
                      "valid": bool(setup.get("valid", False)),
                      "setup_type": setup.get("setup_type"),
                      "setup_score": setup.get("setup_score"),
                      "setup_quality_mult": setup.get("setup_quality_mult"),
                      "signal_close": setup.get("signal_close"),
                      "atr": setup.get("atr"),
                      "entry_reason": setup.get("entry_reason"),
                      "rs_rank": c.get("rs_rank")}
                rec["setup"]["evaluations"].append(ev)
                if not ev["valid"]:
                    continue
                rec["setup"]["n_valid"] += 1
                price = float(df["close"].iloc[-1])
                atr = setup.get("atr") or 0.0
                quality = setup.get("setup_quality_mult", 1.0) or 0.0
                sizing = size_swing_position(
                    st["equity"], st["cash"], price, atr,
                    regime["position_size_mult"], quality, n_open, cfg)
                rec["risk"]["sizing"].append({
                    "ticker": t, "shares": sizing.get("shares", 0),
                    "stop": sizing.get("stop_price"),
                    "take_profit": sizing.get("take_profit"),
                    "risk_reward": sizing.get("risk_reward"),
                    "eff_size_mult": round(regime["position_size_mult"] * quality, 4),
                    "allow": bool(sizing.get("allow", False)),
                })
                if not sizing.get("allow"):
                    continue
                order = build_order_buy(t, price, sizing, setup, regime,
                                        reason=setup.get("entry_reason", ""))
                # execution context needed by the backtest replay
                order["signal_close"] = setup.get("signal_close")
                order["prior_high20"] = setup.get("prior_high20")
                order["atr"] = atr
                order["quality_mult"] = quality
                order["size_mult"] = regime["position_size_mult"]
                order["regime_label"] = regime.get("regime_label")
                buys.append(order)
                n_open += 1
                if len(buys) >= n_budget:
                    break
    if buys:
        entries["status"] = ST_OK
        entries["proposed"] = buys
        entries["n_buys"] = len(buys)
    elif entries["status"] == ST_OK:
        entries["status"] = ST_EMPTY

    # =====================================================================
    # 8. PORTFOLIO CONSTRAINTS + RECOMMENDATIONS
    # =====================================================================
    sells = rec["exits"]["proposed"]
    port = rec["portfolio"]
    port["n_open_after"] = (rec["positions"]["n_open"]
                            - len(sells) + len(buys))
    port["max_open"] = cfg.max_open_positions
    port["constraints"] = [
        f"max_open_positions={cfg.max_open_positions}",
        f"max_position_pct={cfg.max_position_pct}",
        f"risk_per_trade={cfg.risk_per_trade}",
    ]
    if screen_blocked:
        port["warnings"].append("screen FAILURE -> exits evaluated, "
                                "no new BUY recommendations")
    if rec["exits"]["warnings"]:
        port["warnings"].extend(rec["exits"]["warnings"])

    # ---- human-review summary ----
    parts = []
    if sells:
        parts.append(f"{len(sells)} exit(s)")
    if buys:
        parts.append(f"{len(buys)} BUY proposal(s)")
    elif not sells:
        parts.append("no action")
    if screen_blocked:
        parts.append("screener FAILURE (no new BUY)")
    elif entries["status"] == ST_BLOCKED and entries["blocked_reason"]:
        parts.append(entries["blocked_reason"])
    rec["recommendations"] = {
        "summary": " | ".join(parts),
        "buys": [{k: o.get(k) for k in ("ticker", "shares", "price", "stop",
                                        "take_profit", "risk_reward",
                                        "setup_score", "regime")} for o in buys],
        "sells": [{"ticker": s["ticker"], "reason": s["reason"]}
                  for s in sells],
    }
    rec["portfolio"] = port
    return rec


def _severity(s: str) -> int:
    return {ST_OK: 0, ST_EMPTY: 1, ST_PARTIAL: 2, ST_BLOCKED: 2,
            ST_FAILURE: 3}.get(s, 1)
