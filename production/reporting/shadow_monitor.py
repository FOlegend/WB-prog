"""
shadow_monitor.py — shadow coverage monitoring + cut-over gate (Phase 3)

Reads shadow diagnostics that the production pipeline already writes (no new
data collection, no trading side effects) and answers one question:

    has the shadow wiring been observed for long enough, with zero divergence,
    on positions we actually held, to consider a cut-over?

Accepted inputs
---------------
* a backtest coverage file      -> `production/tests/shadow_coverage.py` output
  (per-session `exits.shadow_summary` aggregates from the production-equivalent
  backtest)
* a directory of decision ledgers -> `reports/decision_YYYY-MM-DD.json`
  (`exits.shadow_summary` from real runs)

Gate (Phase-3 spec §10)
-----------------------
    20 consecutive trading sessions
    + 0 divergence
    + the new engine actually evaluated real held positions
    + historical replay 0 divergence
    + unit tests green

A session counts as QUALIFYING only when the new Exit Engine was actually
evaluated on at least one held position (`n_evaluated >= 1`). Scheduling runs
without holdings does NOT count — that is exactly the failure mode the spec
warns about.

This module never decides anything about trading; it only reports coverage.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

REQUIRED_CONSECUTIVE_SESSIONS = 20

MONITOR_NAME = "production/reporting/shadow_monitor.py"

GATE_PASS = "PASS"
GATE_PENDING = "PENDING"
GATE_FAIL = "FAIL"


def _empty_counts() -> dict:
    return {"n_evaluated": 0, "n_agree": 0, "n_diverged": 0, "n_error": 0,
            "divergence_types": {}}


def _normalise(record: dict) -> dict:
    """Coerce one raw session record into the monitor's shape."""
    out = {"date": record.get("date") or record.get("session")
           or record.get("as_of") or "?",
           "mode": record.get("mode")}
    for k, v in _empty_counts().items():
        out[k] = record.get(k, v) if record.get(k) is not None else v
    if not isinstance(out["divergence_types"], dict):
        out["divergence_types"] = {}
    return out


def load_sessions_from_coverage(path: str) -> list[dict]:
    """From a `shadow_coverage.py` file (`{"sessions": [...]}` or a list)."""
    with open(path) as f:
        payload = json.load(f)
    raw = payload.get("sessions", []) if isinstance(payload, dict) else payload
    return [_normalise(r) for r in raw]


def load_sessions_from_ledgers(ledger_dir: str) -> list[dict]:
    """From `reports/decision_YYYY-MM-DD.json` ledgers."""
    out = []
    for path in sorted(glob.glob(os.path.join(ledger_dir, "decision_*.json"))):
        try:
            with open(path) as f:
                rec = json.load(f)
        except Exception:
            continue
        exits = rec.get("exits") or {}
        summary = exits.get("shadow_summary")
        if not summary:
            continue
        out.append(_normalise({"date": rec.get("as_of"),
                               "mode": exits.get("mode"), **summary}))
    return out


def _max_consecutive_qualifying(sessions: list[dict]) -> int:
    best = run = 0
    for s in sessions:
        if s["n_evaluated"] >= 1 and s["n_diverged"] == 0 and s["n_error"] == 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def evaluate_gate(sessions: list[dict], *,
                  required_consecutive: int = REQUIRED_CONSECUTIVE_SESSIONS,
                  historical_replay_ok: bool = False,
                  unit_tests_ok: bool = False,
                  live_sessions: int = 0) -> dict:
    """Evaluate the Phase-3 cut-over gate. Reports, never decides."""
    sessions = [_normalise(s) for s in sessions]
    sessions.sort(key=lambda s: s["date"])

    total = len(sessions)
    qualifying = sum(1 for s in sessions if s["n_evaluated"] >= 1)
    divergences = sum(s["n_diverged"] for s in sessions)
    errors = sum(s["n_error"] for s in sessions)
    evaluations = sum(s["n_evaluated"] for s in sessions)
    streak = _max_consecutive_qualifying(sessions)
    by_type: dict[str, int] = {}
    for s in sessions:
        for t, n in (s["divergence_types"] or {}).items():
            by_type[t] = by_type.get(t, 0) + n

    criteria = {
        f"consecutive_qualifying_sessions >= {required_consecutive}":
            {"ok": streak >= required_consecutive, "value": streak},
        "zero_divergence": {"ok": divergences == 0, "value": divergences},
        "zero_engine_errors": {"ok": errors == 0, "value": errors},
        "real_held_position_coverage": {"ok": qualifying > 0,
                                        "value": qualifying},
        "historical_replay_zero_divergence":
            {"ok": bool(historical_replay_ok)},
        "unit_tests_green": {"ok": bool(unit_tests_ok)},
        # The §10 gate is about the PRODUCTION shadow stream. Backtest coverage
        # is necessary evidence but it is not the same thing: 20 qualifying
        # sessions must be observed in live/paper operation.
        f"live_paper_sessions_observed >= {required_consecutive}":
            {"ok": live_sessions >= required_consecutive, "value": live_sessions},
    }

    if divergences or errors:
        gate = GATE_FAIL
    elif all(c["ok"] for c in criteria.values()):
        gate = GATE_PASS
    else:
        gate = GATE_PENDING

    reasons = [k for k, v in criteria.items() if not v["ok"]]
    return {
        "monitor": MONITOR_NAME,
        "gate": gate,
        "required_consecutive_sessions": required_consecutive,
        "sessions_total": total,
        "sessions_with_evaluation": qualifying,
        "sessions_without_evaluation": total - qualifying,
        "max_consecutive_qualifying_sessions": streak,
        "position_evaluations": evaluations,
        "divergences": divergences,
        "engine_errors": errors,
        "divergence_types": by_type,
        "criteria": criteria,
        "outstanding": reasons,
        "live_sessions_observed": live_sessions,
        "note": ("A session qualifies only if the new Exit Engine was actually "
                 "evaluated on >= 1 real held position."),
    }


def summarise_gate(gate: dict) -> str:
    lines = [
        f"shadow cut-over gate : {gate['gate']}",
        f"  sessions total     : {gate['sessions_total']}"
        f"  (with evaluation: {gate['sessions_with_evaluation']})",
        f"  consecutive streak : {gate['max_consecutive_qualifying_sessions']}"
        f" / {gate['required_consecutive_sessions']} required",
        f"  position evals     : {gate['position_evaluations']}",
        f"  divergences        : {gate['divergences']}"
        f"   errors: {gate['engine_errors']}",
    ]
    if gate["divergence_types"]:
        lines.append(f"  divergence types   : {gate['divergence_types']}")
    if gate["outstanding"]:
        lines.append("  outstanding        :")
        for r in gate["outstanding"]:
            lines.append(f"    - {r}")
    if gate["live_sessions_observed"] == 0:
        lines.append("  NOTE: no live/paper sessions observed yet — the live "
                     "20-session gate is PENDING, not failed.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="shadow coverage monitor (Phase 3)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--coverage", help="shadow_coverage.py output JSON")
    src.add_argument("--ledger-dir", help="directory of decision_*.json ledgers")
    ap.add_argument("--historical-replay-ok", action="store_true")
    ap.add_argument("--unit-tests-ok", action="store_true")
    ap.add_argument("--live-sessions", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    sessions = (load_sessions_from_coverage(args.coverage) if args.coverage
                else load_sessions_from_ledgers(args.ledger_dir))
    gate = evaluate_gate(sessions,
                         historical_replay_ok=args.historical_replay_ok,
                         unit_tests_ok=args.unit_tests_ok,
                         live_sessions=args.live_sessions)
    print(summarise_gate(gate))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"gate": gate, "sessions": sessions}, f, indent=2,
                      default=str)
        print(f"\nreport -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
