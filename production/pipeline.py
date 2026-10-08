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

from production.config import ProductionConfig, resolve_exit_engine_mode
from production.contracts.reason_codes import (EXIT_ENGINE_LEGACY,
                                               EXIT_ENGINE_NEW,
                                               EXIT_ENGINE_SHADOW)
from production.agents.regime import compute_market_regime, load_breadth
from production.agents.setup import evaluate_setup
from production.screener.screener import screen_from_source
from production.risk.risk import size_swing_position
from production.portfolio.portfolio import exit_check, build_order_buy
from production.datasource import DataSource
from production.data_validity import (VALIDITY_INVALID, VALIDITY_MISSING,
                                      VALIDITY_STALE,
                                      VALIDITY_STALE_CONSTITUENTS,
                                      VALIDITY_TEMPORALLY_INCONSISTENT,
                                      assess as assess_regime_inputs)
from production.observability import (blocked_entry_snapshot,
                                     parse_setup_components,
                                     risk_gate_diagnostics)
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
                   "output": None, "warnings": [], "input_validity": None},
        "screener": {"mode": None, "status": ST_OK, "error": None,
                     "n_candidates": 0, "top_n": None, "apply_mcap": None,
                     "filters_applied": [], "candidates": [],
                     "n_universe_checked": 0, "provider_counts": {},
                     "warnings": []},
        "setup": {"status": ST_OK, "evaluations": [], "n_valid": 0,
                  "blocked_snapshot": None},
        "positions": {"n_open": 0, "max_open": 0, "equity": None, "cash": None,
                      "nav_mark": None, "gross_exposure": None,
                      "exposure_pct": None, "items": []},
        "exits": {"status": ST_OK, "mode": EXIT_ENGINE_LEGACY, "proposed": [],
                  "warnings": [], "shadow": [], "shadow_summary": None},
        "entries": {"status": ST_OK, "blocked_reason": None,
                    "proposed": [], "n_buys": 0,
                    "pre_trade_equity": None, "pre_trade_cash": None,
                    "regime_gate": None},
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
    # 0. EXIT-ENGINE WIRING MODE (Phase 3 — default "legacy")
    # =====================================================================
    # legacy : the legacy `_exit_check` oracle is the only decision source and
    #          the new packages are not even imported (frozen import graph).
    # shadow : legacy still decides; the new Stop/Exit Engine is evaluated,
    #          compared and recorded in `exits.shadow` ONLY.
    # new    : the ExitDecision drives the production action (never defaulted).
    exit_mode = resolve_exit_engine_mode(cfg)
    rec["exits"]["mode"] = exit_mode
    for _w in (getattr(cfg, "config_warnings", None) or []):
        rec["warnings"].append(f"CONFIG_WARNING: {_w}")

    shadow_exit_check = summarise_shadow = None
    if exit_mode == EXIT_ENGINE_SHADOW:
        from production.exits.shadow import (shadow_exit_check,
                                             summarise_shadow)
    shadow_records = []

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
    # Data-integrity diagnostic (approved 2026-10-01, spec §5): expose the ACTUAL
    # date of the breadth series tail so a run can never silently appear current
    # while using stale breadth. This is INFORMATION ONLY — it changes no
    # decision, and deliberately introduces no freshness threshold or fail-loud
    # rule (none exists in the project's data contracts; a proposal is recorded
    # in reports/pit_breadth_leak_2026-10-01.md §5).
    breadth_tail = None
    if breadth_df is not None and len(breadth_df) > 0:
        try:
            breadth_tail = pd.Timestamp(breadth_df.index.max()).strftime("%Y-%m-%d")
        except Exception:  # pragma: no cover - defensive
            breadth_tail = None
    rec["data"]["breadth"] = {
        "kind": "pit-or-current", "rows": len(breadth_df) if breadth_df is not None else 0,
        "ok": breadth_df is not None and len(breadth_df) > 0,
        "tail_date": breadth_tail,
        "tail_matches_as_of": (None if breadth_tail is None
                               else breadth_tail == str(as_of)),
        "error": breadth_warn,
    }
    data_ok = spy_df is not None
    rec["data"]["status"] = ST_OK if data_ok else ST_FAILURE

    # =====================================================================
    # 1b. REGIME INPUT VALIDITY (BS-3/4/6 — data integrity, NOT strategy)
    # =====================================================================
    # Classifies whether this Regime decision can be trusted for the date it
    # claims to be about, and records why. It never substitutes an input, never
    # degrades a stale read into a default regime, and never picks a fallback —
    # those are explicitly out of scope.
    #
    # `enforce_live_validity` is OFF by default, which keeps HISTORICAL REPLAY
    # byte-identical (task §9): in replay the breadth tail IS the as_of date, so
    # the assessment is VALID anyway. It is switched on by the live runner only.
    if getattr(cfg, "freshness_policy", None) is not None:
        validity = assess_regime_inputs(
            regime_as_of=as_of, spy_df=spy_df, breadth_df=breadth_df,
            policy=cfg.freshness_policy,
            constituent_snapshot=constituent_snapshot_date(),
            # task §10 — the price basis is a property of the DATASET, not of
            # the call, so it is declared once here rather than inferred per
            # loader. `None` until a human pins the live dataset's basis.
            price_basis=getattr(cfg, "ohlcv_price_basis", None),
            # breadth is derived: record the OHLCV tail it was built from so
            # the record can reconstruct what it measured
            breadth_ohlcv_tail=(breadth_ohlcv_tail(breadth_df, as_of)
                                if breadth_df is not None else None))
        rec["regime"]["input_validity"] = validity.as_dict()
        if not validity.decision_trustworthy:
            rec["warnings"].append(
                f"REGIME_INPUT_{validity.state}: {'/'.join(validity.reasons)} "
                f"(regime_as_of={as_of} spy_tail={validity.spy.tail_date} "
                f"breadth_tail={validity.breadth.tail_date} "
                f"constituents={validity.constituents.tail_date if validity.constituents else None} "
                f"breadth_age={validity.breadth_age_days}d "
                f"spy_age={validity.spy_age_days}d) — decision NOT valid for "
                f"this date; human review required")
            if cfg.freshness_policy.on_failure == "BLOCK_DECISION" and \
                    validity.state in (VALIDITY_STALE, VALIDITY_MISSING,
                                       VALIDITY_INVALID,
                                       VALIDITY_STALE_CONSTITUENTS,
                                       VALIDITY_TEMPORALLY_INCONSISTENT):
                # Fail loud, but ONLY for the data-validity reason: no regime is
                # produced, and no substitute is invented.
                rec["regime"]["status"] = ST_FAILURE
                rec["warnings"].append(
                    "REGIME_BLOCKED_STALE_INPUT: no regime produced — the "
                    "policy forbids a decision on inputs that are not current")
                rec["pipeline_status"] = ST_FAILURE
                rec["regime"]["output"] = None
                rec["regime"]["warnings"].append(
                    "regime suppressed: input freshness/temporal contract failed")

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
        if exit_mode == EXIT_ENGINE_NEW:
            # ONLY mode=new: the ExitDecision IS the decision. An engine failure
            # is fail-loud (no silent legacy fallback) — nothing is proposed.
            try:
                ex, err = _exit_from_new_engine(pos, bar, tech_sig, as_of, cfg), None
            except Exception as exc:
                ex, err = None, exc
            if err is not None:
                msg = (f"{t}: new Exit Engine failed ({type(err).__name__}: "
                       f"{err}) — exit NOT proposed (fail-loud in mode=new)")
                rec["exits"]["warnings"].append(msg)
                rec["warnings"].append("NEW_ENGINE_FAILURE: " + msg)
                rec["pipeline_status"] = max(rec["pipeline_status"], ST_PARTIAL,
                                             key=_severity)
                continue
        else:
            # legacy / shadow: the legacy oracle is the authoritative decision
            ex = exit_check(pos, bar, as_of, tech_sig, cfg)
            if exit_mode == EXIT_ENGINE_SHADOW:
                # observation only — the result never touches the decision
                shadow_records.append(shadow_exit_check(
                    pos, bar, tech_sig, as_of, ex, cfg))
        if ex is not None:
            proposal = {
                "ticker": t, "shares": pos["shares"],
                "exit_price": float(ex["exit_price"]),
                "fill_model": ex["fill_model"],
                "reason": ex["reason"],
                "detail": ex["detail"],
                "mark_close": bar["close"],
            }
            # mode=new carries audit extras; the legacy dict has none of these
            # keys, so the legacy proposal shape is byte-identical.
            for _k in ("engine", "stop_reference_price",
                       "target_reference_price", "stop_plan"):
                if _k in ex:
                    proposal[_k] = ex[_k]
            rec["exits"]["proposed"].append(proposal)
    if exit_eval_fail:
        msg = ("cannot evaluate exit for held "
               + ", ".join(exit_eval_fail) + " (data unavailable)")
        rec["exits"]["warnings"].append(msg)
        rec["warnings"].append("EXIT_EVAL_INCOMPLETE: " + msg)
        rec["pipeline_status"] = max(rec["pipeline_status"], ST_PARTIAL,
                                     key=_severity)
    rec["exits"]["status"] = (ST_PARTIAL if exit_eval_fail else ST_OK)

    # ---- Phase 3: shadow diagnostics live ONLY inside exits.shadow ----
    # (never in `warnings`, `proposed`, state, ledger outcome or pipeline_status)
    if exit_mode == EXIT_ENGINE_SHADOW:
        rec["exits"]["shadow"] = [r.as_dict() for r in shadow_records]
        rec["exits"]["shadow_summary"] = summarise_shadow(shadow_records)

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
    # R7 §3 — record the exact portfolio inputs the sizing engine will see, and
    # the regime gate that decides whether it is reached at all. Purely additive
    # diagnostics: no branch below reads them.
    entries["pre_trade_equity"] = st.get("equity")
    entries["pre_trade_cash"] = st.get("cash")
    entries["regime_gate"] = {
        "regime_label": regime.get("regime_label"),
        "position_size_mult": regime.get("position_size_mult"),
        "strategy_mode": regime.get("strategy_mode"),
        "composite_score": regime.get("composite_score"),
        "veto_flags": regime.get("veto_flags"),
        "allows_new_longs": regime_allows,
    }
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
                # R7 §3 — component attribution, parsed from the engine's own
                # entry_reason (Setup v1 pullback returns no `components` dict).
                ev["score_components"] = parse_setup_components(
                    setup.get("entry_reason"), setup.get("setup_type"))
                # R7 §3 — the pivot the frozen extension filter needs does not
                # exist under Pullback-Only. Record that explicitly instead of
                # substituting a look-alike number.
                ev["extension_filter_input"] = {
                    "max_extension_from_pivot_pct": cfg.max_extension_from_pivot_pct,
                    "prior_high20": setup.get("prior_high20"),
                    "status": ("available" if setup.get("prior_high20") is not None
                               else "unavailable — Setup v1 is Pullback-Only and "
                                    "produces no pivot; the frozen extension "
                                    "filter therefore cannot fire (P6)"),
                }
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
                # R7 §2/§3 — full risk-gate reasoning: risk budget, size before
                # flooring, each cap, final size, and which gate bound. The
                # engine's own return value stays authoritative; the
                # recomputation only fills the record and self-checks against it.
                rec["risk"]["sizing"].append({
                    "ticker": t, "shares": sizing.get("shares", 0),
                    "stop": sizing.get("stop_price"),
                    "take_profit": sizing.get("take_profit"),
                    "risk_reward": sizing.get("risk_reward"),
                    "eff_size_mult": round(regime["position_size_mult"] * quality, 4),
                    "allow": bool(sizing.get("allow", False)),
                    "reasoning": sizing.get("reasoning"),
                    "gate": risk_gate_diagnostics(
                        equity=st["equity"], cash=st["cash"], price=price,
                        atr=atr, size_mult=regime["position_size_mult"],
                        quality_mult=quality, n_open=n_open, cfg=cfg,
                        sizing=sizing),
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
            # R7 §2 — capacity observability. Written AFTER the loop so it can
            # never influence how many setups were evaluated or which orders
            # were produced.
            rec["setup"]["blocked_snapshot"] = _snapshot_after_evaluation(
                as_of=as_of, cands=cands, state=st, cfg=cfg, regime=regime,
                n_budget=n_budget, held_set=held_set, data_source=data_source,
                evaluations=rec["setup"]["evaluations"], buys=buys)
    # R7 §2 — sessions blocked BEFORE any setup evaluation (regime defensive /
    # max_open_positions reached). Tier A records the population and the
    # portfolio context; per-candidate setup/risk status stays explicitly
    # unavailable unless the research-only flag is on.
    if rec["setup"]["blocked_snapshot"] is None:
        rec["setup"]["blocked_snapshot"] = _snapshot_on_block(
            as_of=as_of, blocked_reason=entries["blocked_reason"],
            state=st, cfg=cfg, regime=regime, data_source=data_source,
            screen=scr, screen_empty=screen_empty)
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


def _exit_from_new_engine(pos: dict, bar: dict, tech_sig: str, as_of: str,
                          cfg) -> dict | None:
    """mode=new ONLY: the new Stop+Exit Engine produces the exit decision.

    Returns a proposal in the SAME shape the legacy path emits (so downstream
    execution/backtest code is unchanged), or None when the engine decides to
    hold. The stop level comes from the Stop Engine via the adapter — no
    trailing arithmetic happens here or in the Exit Engine.

    Imported lazily so that modes `legacy`/`shadow` never even load the new
    engine modules.
    """
    from production.exits.adapter import adapt_exit_inputs
    from production.exits.engine import evaluate_exit

    adapted = adapt_exit_inputs(pos, bar, tech_sig, as_of, cfg)
    decision = evaluate_exit(adapted.context)
    if not decision.should_exit:
        return None
    return {
        "ticker": pos["ticker"], "shares": pos["shares"],
        "exit_price": float(decision.exit_price),
        "fill_model": decision.fill_model,
        "reason": decision.exit_reason_code,
        "detail": decision.detail,
        "mark_close": bar["close"],
        # audit extras (ignored by execution/backtest, kept for the ledger)
        "engine": "exit_engine_v1",
        "stop_reference_price": decision.stop_reference_price,
        "target_reference_price": decision.target_reference_price,
        "stop_plan": adapted.stop_plan_snapshot(),
    }


def _severity(s: str) -> int:
    return {ST_OK: 0, ST_EMPTY: 1, ST_PARTIAL: 2, ST_BLOCKED: 2,
            ST_FAILURE: 3}.get(s, 1)


def constituent_snapshot_date() -> str | None:
    """The DATE of the PIT membership snapshot the breadth series is built on.

    Read-only and defensive: a breadth row is only as current as the universe it
    was measured over, and `PitMembership.members_as_of` forward-fills the last
    snapshot, so a recent breadth row can still rest on an old membership list.
    Returning None (rather than raising) means "unknown", which the validity
    model treats as a disabled check rather than a fabricated date.
    """
    try:
        from regime_dual_engine.pit_constituents import load_snapshots
        s = load_snapshots("fja")
        if s is None or not len(s):
            return None
        return pd.Timestamp(s["date"].max()).strftime("%Y-%m-%d")
    except Exception:
        return None


def breadth_ohlcv_tail(breadth_df, as_of: str) -> str | None:
    """The OHLCV tail a breadth series was built from (task §10).

    Breadth is a DERIVED observation: its own tail date says when the metric was
    computed, not how fresh its price inputs were. The two normally coincide
    (the builder clips prices to the breadth window), so when the breadth tail is
    itself at or before `as_of` it is the best available statement of the
    price tail. When it does not — i.e. the caller has a narrower breadth window
    than the underlying prices — the honest answer is the breadth tail, because
    anything later would be a claim we cannot verify from the frame we hold.

    Returns None rather than guessing when the frame carries no usable date.
    """
    try:
        from production.data_validity import effective_date
        tail = effective_date(breadth_df)
        if tail is None:
            return None
        # never claim a price tail later than the decision date
        return tail if tail <= str(as_of) else str(as_of)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# R7 §2 — capacity observability helpers (RECORD ONLY)
# ---------------------------------------------------------------------------
def _per_candidate_detail(cands, held_set, data_source, as_of, cfg, regime,
                          state, n_open, evaluations=None) -> list:
    """Tier B: evaluate setups on candidates the decision path never reached.

    RESEARCH ONLY, and only when `cfg.research_blocked_setup_eval` is true. It
    reuses the production `evaluate_setup` + `size_swing_position` unchanged, so
    the recorded verdict is the real engine's verdict, not a model of it. It
    never appends to `buys`, never touches `state`, and never runs on the live
    path. When the flag is off the caller receives `None` and the record says
    the value is unavailable.
    """
    out = []
    for c in cands:
        t = c.get("ticker")
        row = {"ticker": t, "rs_rank": c.get("rs_rank"),
               "already_held": t in held_set,
               "setup_status": "unavailable", "risk_status": "unavailable"}
        if t in held_set:
            row["setup_status"] = "skipped — already held"
            out.append(row)
            continue
        df, _prov = data_source.get_ohlcv(t, as_of=as_of, min_bars=60)
        if df is None:
            row["setup_status"] = "unavailable — insufficient data"
            out.append(row)
            continue
        try:
            setup = evaluate_setup(df, cfg, rs_rank=c.get("rs_rank"))
        except Exception as exc:
            row["setup_status"] = f"unavailable — setup error: {exc}"
            out.append(row)
            continue
        row.update({
            "setup_status": "valid" if setup.get("valid") else "invalid",
            "setup_type": setup.get("setup_type"),
            "setup_score": setup.get("setup_score"),
            "setup_quality_mult": setup.get("setup_quality_mult"),
            "signal_close": setup.get("signal_close"),
            "atr": setup.get("atr"),
            "entry_reason": setup.get("entry_reason"),
            "score_components": parse_setup_components(
                setup.get("entry_reason"), setup.get("setup_type")),
        })
        if not setup.get("valid"):
            row["risk_status"] = "not reached — setup invalid"
            out.append(row)
            continue
        price = float(df["close"].iloc[-1])
        atr = setup.get("atr") or 0.0
        quality = setup.get("setup_quality_mult", 1.0) or 0.0
        sizing = size_swing_position(state.get("equity"), state.get("cash"),
                                      price, atr,
                                      regime.get("position_size_mult", 0.0),
                                      quality, n_open, cfg)
        row["risk_status"] = "allowed" if sizing.get("allow") else "denied"
        row["risk_gate"] = risk_gate_diagnostics(
            equity=state.get("equity"), cash=state.get("cash"), price=price,
            atr=atr, size_mult=regime.get("position_size_mult"),
            quality_mult=quality, n_open=n_open, cfg=cfg, sizing=sizing)
        out.append(row)
    return out


def _snapshot_on_block(*, as_of, blocked_reason, state, cfg, regime,
                       data_source, screen, screen_empty) -> dict:
    """Entries were blocked BEFORE any setup evaluation (regime defensive,
    max_open_positions reached, regime failure or screener FAILURE)."""
    cands = [] if screen_empty else (screen.get("candidates") or [])
    n_open = len(state.get("open_positions", []))
    per_candidate = None
    if getattr(cfg, "research_blocked_setup_eval", False) and blocked_reason:
        try:
            per_candidate = _per_candidate_detail(
                cands, {p["ticker"] for p in state.get("open_positions", [])},
                data_source, as_of, cfg, regime, state, n_open)
        except Exception as exc:                       # pragma: no cover
            per_candidate = None
            blocked_reason = f"{blocked_reason} (Tier B unavailable: {exc})"
    return blocked_entry_snapshot(
        as_of=as_of, blocked_stage="pre_setup_evaluation",
        blocked_reason=blocked_reason, cands=cands, state=state, cfg=cfg,
        regime=regime, per_candidate=per_candidate)


def _snapshot_after_evaluation(*, as_of, cands, state, cfg, regime, n_budget,
                               held_set, data_source, evaluations, buys) -> dict:
    """Entries were evaluated but the session's capacity / risk budget was
    insufficient. Records what was foregone, using the evaluations the decision
    path already performed (plus Tier B only if the research flag is on)."""
    ev_by_ticker = {e.get("ticker"): e for e in evaluations if e.get("ticker")}
    per_candidate = []
    for c in cands:
        t = c.get("ticker")
        ev = ev_by_ticker.get(t)
        if ev is None:
            per_candidate.append({
                "ticker": t, "rs_rank": c.get("rs_rank"),
                "already_held": t in held_set,
                "setup_status": ("skipped — already held" if t in held_set
                                 else "unavailable — not evaluated"),
                "risk_status": "unavailable — not evaluated",
            })
            continue
        row = {
            "ticker": t, "rs_rank": c.get("rs_rank"),
            "setup_status": ("valid" if ev.get("valid") else "invalid"),
            "setup_type": ev.get("setup_type"),
            "setup_score": ev.get("setup_score"),
            "setup_quality_mult": ev.get("setup_quality_mult"),
            "signal_close": ev.get("signal_close"),
            "atr": ev.get("atr"),
            "entry_reason": ev.get("entry_reason"),
            "score_components": ev.get("score_components"),
        }
        if ev.get("valid"):
            row["risk_status"] = "not reached — session capacity budget exhausted"
        else:
            row["risk_status"] = "not reached — setup invalid"
        per_candidate.append(row)
    snap = blocked_entry_snapshot(
        as_of=as_of, blocked_stage="post_setup_evaluation_capacity_budget",
        blocked_reason=(f"session capacity budget of {n_budget} slot(s) "
                        f"exhausted by the day's own BUY proposals"),
        cands=cands, state=state, cfg=cfg, regime=regime,
        per_candidate=per_candidate)
    snap["capacity_slots_free"] = n_budget
    snap["n_buys_proposed"] = len(buys)
    return snap
