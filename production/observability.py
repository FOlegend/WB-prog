"""
observability.py — R7 strategy-neutral diagnostics (RECORD ONLY, never a decision)

Purpose
-------
Make currently unobservable entry / capacity decisions measurable **without
changing any trading behaviour**. Every function in this module is a pure
read-only computation over values the decision path already had. Nothing here
is imported by any decision rule, and nothing here writes to `state`, produces
an order, or feeds a guard.

Two guarantees make the diagnostics trustworthy rather than merely plausible:

1. **Self-check.** `risk_gate_diagnostics` re-derives the sizing gate and
   compares the result with the share count the REAL engine
   (`src.agents.risk_manager.size_position`) actually returned. The comparison
   is written into every record as `selfcheck` ∈ {MATCH, MISMATCH,
   NOT_COMPUTABLE}. If the re-derivation ever drifts from the engine, R7 fails
   loudly instead of quietly publishing a plausible-looking number.
2. **Provenance.** Every field carries where it came from and whether it is
   exact, approximate, or unavailable. Nothing is invented: where a value
   cannot be reconstructed, the record says so.

Non-goals (deliberate)
----------------------
* No production parameter is read differently here than in the decision path.
* No value is a "proxy" for an unavailable frozen field — unavailable stays
  unavailable (see `extension_from_pivot_pct`, which stays null because Setup v1
  is Pullback-Only and therefore never produces a pivot; the research-only
  high-20 extension observation is namespaced `r7_research` and is explicitly
  NOT the frozen filter input).
* No reconstruction of anything that would require future data.

Related: reports/capacity_observability_proposal_2026-10-01.md,
reports/phase5_record_quality_proposal_2026-10-01.md (P1–P8),
production/STOP_EXIT_V1_FREEZE.md (nothing here touches it).
"""
from __future__ import annotations

import math
import re

# The frozen Setup v1 pullback scorer emits exactly these component tokens in
# `entry_reason`; see src/agents/setup_agent.py::pullback_setup.
_PULLBACK_TOKENS = ("px>50SMA", "px>200SMA", "50>200", "nearEMA",
                    "vol_contract", "reversal", "RS_strong")

_REASON_RE = re.compile(r"^(pullback|breakout):\s*(.*?)\s*\(score=([0-9.]+)\)\s*$")

UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _f(x):
    """float() that returns None instead of raising / propagating NaN-inf."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


# ---------------------------------------------------------------------------
# P2 / §2 — complete risk-gate reasoning
# ---------------------------------------------------------------------------
def risk_gate_diagnostics(*, equity, cash, price, atr, size_mult, quality_mult,
                          n_open, cfg, sizing) -> dict:
    """Re-derive the sizing gate for the record and self-check it.

    The sizing engine (`size_position`) is authoritative and is never modified.
    This function recomputes the same arithmetic from the same inputs purely so
    the record can carry *structured* gate values (risk budget, size before
    flooring, each cap, the final size, and which gate bound) instead of a
    prose `reasoning` string.

    `selfcheck` compares `recomputed_final_shares` with the engine's own
    returned `shares`; a MISMATCH means the diagnostic has drifted from the
    engine and must not be trusted.
    """
    equity, cash = _f(equity), _f(cash)
    price, atr = _f(price), _f(atr)
    size_mult, quality_mult = _f(size_mult), _f(quality_mult)
    eff = _f(size_mult) * _f(quality_mult) if (_f(size_mult) is not None
                                               and _f(quality_mult) is not None) else None
    sizing = sizing or {}
    engine_shares = sizing.get("shares")

    out = {
        "source": "recomputed from the sizing engine's own documented inputs "
                  "(record only; the engine's return value is authoritative)",
        "equity": equity,
        "cash": cash,
        "price": price,
        "atr": atr,
        "size_mult": size_mult,
        "quality_mult": quality_mult,
        "eff_size_mult": eff,
        "n_open": n_open,
        "max_open_positions": cfg.max_open_positions,
        "risk_per_trade": cfg.risk_per_trade,
        "stop_atr_mult": cfg.stop_atr_mult,
        "max_position_pct": cfg.max_position_pct,
        "take_profit_atr_mult": cfg.take_profit_atr_mult,
        "engine_allow": bool(sizing.get("allow", False)),
        "engine_shares": engine_shares,
        "engine_reasoning": sizing.get("reasoning"),
    }

    computable = None not in (equity, cash, price, atr, eff)
    if not computable:
        out.update({"risk_budget": None, "stop_distance": None,
                    "requested_shares_exact": None, "raw_shares_after_floor": None,
                    "cap_shares_by_value": None, "cap_shares_by_cash": None,
                    "recomputed_final_shares": None, "gates": [],
                    "selfcheck": "NOT_COMPUTABLE",
                    "note": "one or more sizing inputs were missing or non-finite"})
        return out

    risk_budget = equity * cfg.risk_per_trade * eff
    stop_distance = atr * cfg.stop_atr_mult
    requested_exact = (risk_budget / stop_distance) if stop_distance > 0 else None
    raw = int(risk_budget // stop_distance) if stop_distance > 0 else 0
    cap_value = int((equity * cfg.max_position_pct) // price) if price > 0 else 0
    cap_cash = int(cash // price) if cash > 0 else 0
    final = max(0, min(raw, cap_value, cap_cash)) if (price > 0 and stop_distance > 0) else 0

    # Ordered exactly as src/agents/risk_manager.py::size_position applies them.
    gates: list[str] = []
    if price <= 0 or atr <= 0:
        gates.append("INVALID_PRICE_OR_ATR")
    if eff <= 0:
        gates.append("REGIME_GATE_SIZE_MULT_ZERO")
    if n_open >= cfg.max_open_positions:
        gates.append("MAX_OPEN_POSITIONS_REACHED")
    if not gates and raw <= 0:
        gates.append("RISK_BUDGET_BELOW_ONE_SHARE")
    if not gates and final <= 0:
        gates.append("CAPS_CLAMPED_TO_ZERO")
    expected = 0 if gates else final

    out.update({
        "risk_budget": round(risk_budget, 4),
        "stop_distance": round(stop_distance, 4),
        "requested_shares_exact": (round(requested_exact, 6)
                                   if requested_exact is not None else None),
        "raw_shares_after_floor": raw,
        "cap_shares_by_value": cap_value,
        "cap_shares_by_cash": cap_cash,
        "recomputed_final_shares": final,
        "binding_cap": ("risk_budget" if (not gates and final == raw and raw > 0)
                        else "max_position_pct" if (not gates and final == cap_value
                                                    and cap_value < raw)
                        else "available_cash" if (not gates and final == cap_cash
                                                  and cap_cash < min(raw, cap_value))
                        else None),
        "gates": gates,
        "selfcheck": ("MATCH" if engine_shares is not None
                      and int(engine_shares) == int(expected) else "MISMATCH"),
        "selfcheck_detail": f"engine_shares={engine_shares} expected={expected}",
    })
    return out


# ---------------------------------------------------------------------------
# §3 — setup score components (P7)
# ---------------------------------------------------------------------------
def parse_setup_components(entry_reason, setup_type=None) -> dict:
    """Recover the component attribution from the engine's own `entry_reason`.

    Setup v1 is Pullback-Only and `pullback_setup` does not return a
    `components` dict (that dict only exists on the breakout path), so the trade
    record's `components` is permanently `{}`. The engine DOES publish which
    components fired, verbatim, inside `entry_reason`. Parsing that string is a
    faithful read of the engine's own output — not a proxy for a missing value.

    Fidelity: exact for *which named components fired*. The engine collapses
    EMA10 and EMA20 into the single token `nearEMA`, so the two cannot be told
    apart from the record; that limitation is reported in `fidelity`.
    """
    out = {"available": False, "components": {}, "fired": [],
           "source": None, "fidelity": None, "note": None}
    if not isinstance(entry_reason, str) or not entry_reason:
        out["note"] = "entry_reason unavailable in the record"
        return out
    m = _REASON_RE.match(entry_reason.strip())
    if not m:
        out["note"] = ("entry_reason is an early-return (insufficient data / MAs "
                       "not warm) or an unrecognised form; no component evaluation "
                       f"was performed: {entry_reason!r}")
        return out
    kind, tokens, score = m.group(1), m.group(2), m.group(3)
    fired = [] if tokens == "none" else [t for t in tokens.split("+") if t]
    comps = ({t: (t in fired) for t in _PULLBACK_TOKENS}
             if kind == "pullback" else {t: (t in fired) for t in fired})
    out.update({
        "available": True,
        "setup_type": kind,
        "setup_type_matches_record": (None if setup_type is None
                                      else setup_type == kind),
        "reported_score": _f(score),
        "fired": fired,
        "components": comps,
        "source": "parsed from the frozen Setup v1 engine's own entry_reason string",
        "fidelity": ("exact for which named components fired; the engine emits a "
                     "single 'nearEMA' token so EMA10-vs-EMA20 is not "
                     "distinguishable from the record"),
        "not_a_decision_input": True,
    })
    return out


# ---------------------------------------------------------------------------
# §2 — blocked-entry snapshot (capacity observability)
# ---------------------------------------------------------------------------
def blocked_entry_snapshot(*, as_of, blocked_stage, blocked_reason, cands,
                           state, cfg, regime, per_candidate=None) -> dict:
    """Describe a session on which entries were blocked before any setup ran.

    This is Tier A of
    `reports/capacity_observability_proposal_2026-10-01.md`: the candidate
    population and the portfolio context are recorded WITHOUT evaluating any
    setup, so the record answers "what was available and what stopped it" at
    essentially zero cost. `per_candidate` carries the Tier B detail when the
    research-only evaluation flag is enabled; when it is not, every per-candidate
    field is explicitly `unavailable` rather than guessed.
    """
    held = [{"ticker": p["ticker"], "shares": p.get("shares"),
             "entry_price": p.get("entry_price"),
             "entry_date": p.get("entry_date")}
            for p in state.get("open_positions", [])]
    n_open = len(held)
    n_budget = max(0, cfg.max_open_positions - n_open)
    size_mult = _f((regime or {}).get("position_size_mult"))
    equity = _f(state.get("equity"))
    cash = _f(state.get("cash"))
    risk_budget = (equity * cfg.risk_per_trade * size_mult
                   if (equity is not None and size_mult is not None) else None)

    snap = {
        "tier": "A" if per_candidate is None else "A+B",
        "as_of": as_of,
        "blocked_stage": blocked_stage,
        "blocked_reason": blocked_reason,
        "regime": {
            "regime_label": (regime or {}).get("regime_label"),
            "position_size_mult": size_mult,
            "veto_flags": (regime or {}).get("veto_flags"),
        },
        "portfolio": {
            "n_open": n_open,
            "max_open_positions": cfg.max_open_positions,
            "capacity_slots_free": n_budget,
            "current_open_positions": held,
            "positions_competing_for_capacity": [h["ticker"] for h in held],
        },
        "capital": {
            "equity": equity,
            "available_cash": cash,
            "risk_per_trade": cfg.risk_per_trade,
            "risk_budget_usd": (round(risk_budget, 4)
                                if risk_budget is not None else None),
            "risk_budget_note": ("risk_budget_usd is the SIDEWAYS/BULL budget before "
                                 "the per-setup quality multiplier; it is 0 whenever "
                                 "position_size_mult is 0 (defensive regime)"),
        },
        "candidates": {
            "n_candidates": len(cands),
            "tickers": [c.get("ticker") for c in cands],
        },
        "per_candidate_evaluated": per_candidate is not None,
    }

    if per_candidate is None:
        snap["per_candidate"] = {
            "status": UNAVAILABLE,
            "reason": ("setups are not evaluated when entries are blocked; the "
                       "research-only flag research_blocked_setup_eval is off, so "
                       "no setup status / risk budget / requested size can be "
                       "recorded for these candidates without changing the "
                       "decision path"),
        }
    else:
        snap["per_candidate"] = per_candidate
    return snap
