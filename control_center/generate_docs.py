"""generate_docs.py — emit ARCHITECTURE.md and ARCHITECTURE.yaml from the registry DB.

Both files are generated artefacts: re-run ``refresh_registry.py`` to update
them.  They exist so that a future AI agent reads the architecture BEFORE
touching code (see AGENT_WORKFLOW.md).
"""
from __future__ import annotations

import os
from datetime import datetime

import registry_db as db

# --------------------------------------------------------------------------
# Minimal YAML emitter (no external dependency)
# --------------------------------------------------------------------------
def _q(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or any(c in s for c in ":#{}[],&*?|-<>=!%@`\"'\n") or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def to_yaml(obj, indent: int = 0) -> list[str]:
    pad = "  " * indent
    out: list[str] = []
    if isinstance(obj, dict):
        if not obj:
            out.append(pad + "{}")
            return out
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                out.append(f"{pad}{k}:")
                out.extend(to_yaml(v, indent + 1))
            else:
                out.append(f"{pad}{k}: {_q(v)}")
        return out
    if isinstance(obj, list):
        if not obj:
            out.append(pad + "[]")
            return out
        for item in obj:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    if isinstance(v, (dict, list)) and v:
                        prefix = f"{pad}- " if first else f"{pad}  "
                        out.append(f"{prefix}{k}:")
                        out.extend(to_yaml(v, indent + 2))
                    else:
                        prefix = f"{pad}- " if first else f"{pad}  "
                        out.append(f"{prefix}{k}: {_q(v)}")
                    first = False
            else:
                out.append(f"{pad}- {_q(item)}")
        return out
    out.append(f"{pad}{_q(obj)}")
    return out


# --------------------------------------------------------------------------
# DB -> structure
# --------------------------------------------------------------------------
def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_structure(conn) -> dict:
    meta_kv = {r["key"]: r["value"] for r in _rows(conn, "SELECT * FROM meta")}
    git = _rows(conn, "SELECT * FROM git_info")
    git = git[0] if git else {}
    copies = _rows(conn, "SELECT * FROM repo_copies ORDER BY is_primary DESC")

    mods = _rows(conn, "SELECT * FROM modules")
    by_type, by_status, by_layer = {}, {}, {}
    for m in mods:
        by_type[m["type"]] = by_type.get(m["type"], 0) + 1
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
        by_layer[m["architecture_layer"]] = by_layer.get(m["architecture_layer"], 0) + 1

    prod = _rows(conn, "SELECT * FROM production_pipeline ORDER BY step_order")
    res = _rows(conn, "SELECT * FROM research_pipeline ORDER BY step_order")
    migs = _rows(conn, "SELECT * FROM migrations")
    decs = _rows(conn, "SELECT * FROM decisions")
    dups = _rows(conn, "SELECT group_name, COUNT(*) n FROM duplicates GROUP BY group_name")
    div = _rows(conn, "SELECT * FROM copy_divergence ORDER BY status, rel_path")

    canonical_by_sub = {}
    for m in mods:
        sub = m["subsystem"] or "Other"
        canonical_by_sub.setdefault(sub, {"canonical": [], "legacy": [], "research": []})
        if m["canonical"]:
            canonical_by_sub[sub]["canonical"].append(m["path"])
        if m["legacy"]:
            canonical_by_sub[sub]["legacy"].append(m["path"])
        if m["type"] in ("research", "experiment"):
            canonical_by_sub[sub]["research"].append(m["path"])

    needs_review = sorted(m["path"] for m in mods
                          if m["needs_review"] and m["type"] not in ("documentation",))
    unreferenced = sorted(m["path"] for m in mods
                          if m["type"] not in ("documentation", "utility", "test")
                          and not m["production_used"] and not m["research_used"]
                          and not m["backtest_used"])

    # Phase 3: `production_decision_authority` answers a DIFFERENT question from
    # `production_used` ("does the production pipeline import this?"). Surface
    # both so the registry never has to fudge one to express the other.
    by_authority: dict[str, int] = {}
    authority_rows = []
    for m in mods:
        auth = "NONE"
        if "production_decision_authority" in m.keys():
            auth = m["production_decision_authority"] or "NONE"
        by_authority[auth] = by_authority.get(auth, 0) + 1
        if auth != "NONE":
            authority_rows.append({"path": m["path"], "subsystem": m["subsystem"],
                                   "production_used": m["production_used"],
                                   "authority": auth})
    authority_rows.sort(key=lambda r: (r["authority"], r["path"]))
    exit_mode = meta_kv.get("production_exit_engine_mode", "unknown")

    return {
        "meta": {
            "tool_version": meta_kv.get("tool_version", "?"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "generator": "control_center/generate_docs.py",
            "note": "GENERATED FILE — do not hand-edit; run refresh_registry.py",
        },
        "git": {
            "branch": git.get("branch"), "head_commit": git.get("head_commit"),
            "head_subject": git.get("head_subject"), "head_date": git.get("head_date"),
            "remote": git.get("remote"), "is_dirty": git.get("is_dirty"),
        },
        "repo_copies": copies,
        "production": {
            "entrypoint": "production/main.py",
            "decision_function": "production/pipeline.py::run_daily",
            "exit_engine_mode": exit_mode,
            "decision_authority": authority_rows,
            "freeze_contracts": [
                "regime_dual_engine/REGIME_V1_FREEZE.md (Regime v1)",
                "src/agents/SETUP_V1_FREEZE.md (Setup v1 = Pullback Only)",
                "production/STOP_EXIT_V1_FREEZE.md (Stop + Exit v1, Phase-3 wiring)",
            ],
            "pipeline": [{"step": p["step_order"], "stage": p["stage"], "module": p["module"],
                          "function": p["function"], "next": p["next_stage"], "status": p["status"]}
                         for p in prod],
        },
        "research": {
            "entrypoint": "dynamic_universe_backtest.py",
            "production_equivalent": "production/backtest.py",
            "pipeline": [{"step": p["step_order"], "stage": p["stage"], "module": p["module"],
                          "function": p["function"], "next": p["next_stage"], "status": p["status"]}
                         for p in res],
        },
        "subsystems": canonical_by_sub,
        "modules": {
            "total": len(mods),
            "by_type": by_type, "by_status": by_status, "by_layer": by_layer,
            "by_decision_authority": by_authority,
            "needs_review": needs_review,
            "unreferenced": unreferenced,
        },
        "migrations": [{"subsystem": m["subsystem"], "old": m["old_module"], "new": m["new_module"],
                        "status": m["status"], "blocker": m["blocker"]} for m in migs],
        "decisions": [{"issue": d["issue"], "priority": d["priority"], "status": d["status"]}
                      for d in decs],
        "duplicates": {"group_count": len(dups), "members": sum(d["n"] for d in dups),
                       "groups": [d["group_name"] for d in dups]},
        "copy_divergence": {"count": len(div),
                            "items": [{"path": d["rel_path"], "status": d["status"]} for d in div]},
    }


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------
def build_markdown(conn, structure: dict) -> str:
    s = structure
    mods = _rows(conn, "SELECT * FROM modules")
    L = []
    L.append("# ARCHITECTURE — WB Swing Trading Bot")
    L.append("")
    L.append("> **GENERATED FILE** — produced by `control_center/generate_docs.py`. "
             "Do not hand-edit; run `python control_center/refresh_registry.py` instead.")
    L.append(f"> Generated {s['meta']['generated_at']} · tool v{s['meta']['tool_version']} · "
             f"commit `{(s['git'].get('head_commit') or '?')[:10]}`")
    L.append("")
    L.append("## 1. What the system does")
    L.append("")
    L.append("A **human-in-the-loop US-equity swing trading bot**. It screens large-cap "
             "stocks each day, computes a market regime, detects a trade setup, sizes the "
             "position, and emits a briefing the human reviews before placing orders at a "
             "broker. Capital is locked at 10,000 HKD (~1,282 USD).")
    L.append("")
    L.append("Two execution paths share the *same* decision function:")
    L.append("- **Production (live)**: `production/main.py` → `production/pipeline.py::run_daily`")
    L.append("- **Research (backtest)**: `dynamic_universe_backtest.py` (legacy engine) and "
             "`production/backtest.py` (production-equivalent replay)")
    L.append("")

    L.append("## 2. Production pipeline")
    L.append("")
    L.append("```")
    L.append("production/main.py  (thin CLI)")
    for p in s["production"]["pipeline"]:
        L.append(f"  └─ {p['stage']:<16} {p['module']}  ::  {p['function']}")
    L.append("     └─ production/ledger.py → reports/decision_YYYY-MM-DD.json")
    L.append("     └─ production/reporting/briefing.py → human briefing")
    L.append("```")
    L.append("")
    L.append("Frozen contracts: " + "; ".join(s["production"]["freeze_contracts"]))
    L.append("")
    L.append("| # | Stage | Module | Function | Next | Status |")
    L.append("|---|---|---|---|---|---|")
    for p in s["production"]["pipeline"]:
        L.append(f"| {p['step']} | {p['stage']} | `{p['module']}` | `{p['function']}` | {p['next']} | {p['status']} |")
    L.append("")

    L.append("## 3. Research pipeline")
    L.append("")
    L.append("| # | Stage | Module | Function | Status |")
    L.append("|---|---|---|---|---|")
    for p in s["research"]["pipeline"]:
        L.append(f"| {p['step']} | {p['stage']} | `{p['module']}` | `{p['function']}` | {p['status']} |")
    L.append("")

    L.append("## 4. Canonical / legacy / research by subsystem")
    L.append("")
    L.append("| Subsystem | Canonical | Legacy | Research / Experiment |")
    L.append("|---|---|---|---|")
    for sub, d in sorted(s["subsystems"].items()):
        can = "<br>".join(f"`{x}`" for x in d["canonical"]) or "—"
        leg = "<br>".join(f"`{x}`" for x in d["legacy"]) or "—"
        rs = "<br>".join(f"`{x}`" for x in sorted(d["research"])) or "—"
        L.append(f"| {sub} | {can} | {leg} | {rs} |")
    L.append("")

    L.append("### 4.1 Production decision authority")
    L.append("")
    L.append(f"- `production.exit_engine_mode` in force: **{s['production'].get('exit_engine_mode', 'unknown')}**")
    L.append("- `production_used` says *the production pipeline imports this*;")
    L.append("  `production_decision_authority` says *this decides production actions*:")
    L.append("  **PRIMARY** = decides today · **SHADOW_ONLY** = evaluated/recorded, would decide only in "
             "`exit_engine_mode='new'` · **NONE** = no production decision authority.")
    L.append("")
    L.append("| Module | Subsystem | production_used | decision authority |")
    L.append("|---|---|---|---|")
    for r in s["production"].get("decision_authority", []):
        L.append(f"| `{r['path']}` | {r['subsystem']} | {r['production_used']} | **{r['authority']}** |")
    L.append("")

    L.append("## 5. Module inventory")
    L.append("")
    L.append(f"- Total modules indexed: **{s['modules']['total']}**")
    L.append(f"- By type: " + ", ".join(f"{k}={v}" for k, v in sorted(s["modules"]["by_type"].items())))
    L.append(f"- By status: " + ", ".join(f"{k}={v}" for k, v in sorted(s["modules"]["by_status"].items())))
    L.append(f"- By layer: " + ", ".join(f"{k}={v}" for k, v in sorted(s["modules"]["by_layer"].items())))
    L.append(f"- By production decision authority: "
             + ", ".join(f"{k}={v}" for k, v in sorted(
                 s["modules"].get("by_decision_authority", {}).items())))
    L.append("")

    L.append("## 6. Modules with unclear ownership / needing human review")
    L.append("")
    nr = s["modules"]["needs_review"]
    L.append(f"{len(nr)} module(s) flagged (`needs_review=1`):")
    L.append("")
    for p in nr[:60]:
        L.append(f"- `{p}`")
    if len(nr) > 60:
        L.append(f"- …and {len(nr) - 60} more")
    L.append("")

    L.append("## 7. Unreferenced modules (no production/research/backtest importer)")
    L.append("")
    if s["modules"]["unreferenced"]:
        for p in s["modules"]["unreferenced"]:
            L.append(f"- `{p}`")
    else:
        L.append("_None._")
    L.append("")

    L.append("## 8. Current migrations")
    L.append("")
    L.append("| Subsystem | Old | New | Status | Blocker |")
    L.append("|---|---|---|---|---|")
    for m in s["migrations"]:
        L.append(f"| {m['subsystem']} | `{m['old']}` | `{m['new']}` | {m['status']} | {m['blocker']} |")
    L.append("")

    L.append("## 9. Known technical debt / duplicate candidates")
    L.append("")
    L.append(f"- Duplicate groups detected: **{s['duplicates']['group_count']}** "
             f"({s['duplicates']['members']} member rows). Never auto-deleted — see the "
             f"Duplicate Audit page.")
    L.append(f"- Config parameters defined in more than one config file: see "
             f"`config_registry.duplicate_count` (e.g. `setup_enabled_types`).")
    if s["copy_divergence"]["count"]:
        L.append(f"- **Repo copy divergence**: {s['copy_divergence']['count']} file(s) differ "
                 f"between the two working copies:")
        for it in s["copy_divergence"]["items"][:15]:
            L.append(f"  - `[{it['status']}]` {it['path']}")
    L.append("")

    L.append("## 10. Open architecture decisions (human-in-the-loop)")
    L.append("")
    for d in s["decisions"]:
        L.append(f"- **[{d['priority']}]** {d['issue']} — {d['status']}")
    L.append("")

    L.append("## 11. How to maintain this file")
    L.append("")
    L.append("```bash")
    L.append("python control_center/refresh_registry.py     # rescan + rebuild db + docs")
    L.append("streamlit run control_center/control_center.py")
    L.append("```")
    L.append("")
    return "\n".join(L)


def write_docs(conn, repo_root: str, db_file: str) -> None:
    structure = build_structure(conn)
    yaml_text = "# ARCHITECTURE.yaml — machine-readable map (GENERATED)\n" + \
                "\n".join(to_yaml(structure)) + "\n"
    md_text = build_markdown(conn, structure)
    base = os.path.dirname(os.path.abspath(db_file))
    with open(os.path.join(base, "ARCHITECTURE.yaml"), "w", encoding="utf-8") as f:
        f.write(yaml_text)
    with open(os.path.join(base, "ARCHITECTURE.md"), "w", encoding="utf-8") as f:
        f.write(md_text)
