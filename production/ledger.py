"""
ledger.py — Machine-readable daily decision ledger

Writes a DecisionRecord (from pipeline.run_daily) to a JSON file:

    reports/decision_YYYY-MM-DD.json

Purpose: auditability & debugging — enough information to reconstruct WHY the
system produced each recommendation. Not discretionary reasoning.
"""
from __future__ import annotations

import json
import os


def save_ledger(record: dict, cfg, out_dir: str | None = None) -> str:
    """Persist a DecisionRecord as reports/decision_<as_of>.json."""
    as_of = record.get("as_of", "unknown")
    out_dir = out_dir or getattr(cfg, "reports_dir", None) or "reports"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"decision_{as_of}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False, default=str)
    return path
