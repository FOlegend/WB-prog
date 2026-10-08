"""control_center.py — Streamlit UI for the Trading Bot Codebase Registry.

Run:
    streamlit run control_center/control_center.py

READ-ONLY with respect to trading code. The only writes it performs are to
``project_control.db`` (the registry itself), and only when you explicitly
save a human decision.

Pages
-----
Dashboard · Module Registry · Dependency Explorer · Production Architecture ·
Research Architecture · Duplicate Audit · Migration Board · Config Explorer ·
Copy Divergence · Decisions
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pandas as pd
import streamlit as st

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "project_control.db")
sys.path.insert(0, HERE)

st.set_page_config(page_title="Trading Bot Control Center", layout="wide")


# --------------------------------------------------------------------------
# DB helpers
# --------------------------------------------------------------------------
def q(sql: str, params=()) -> list[dict]:
    if not os.path.exists(DB):
        return []
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def exec_sql(sql: str, params=()) -> None:
    conn = sqlite3.connect(DB)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def df(rows) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


EFF_STATUS = "COALESCE(human_status, status)"
EFF_TYPE = "COALESCE(human_type, type)"
EFF_CANON = "COALESCE(human_canonical, canonical)"


def human_locked(path: str) -> bool:
    r = q("SELECT human_locked FROM modules WHERE path=?", (path,))
    return bool(r and r[0]["human_locked"])


# --------------------------------------------------------------------------
# Guard: DB must exist
# --------------------------------------------------------------------------
if not os.path.exists(DB):
    st.title("🏗 Trading Bot Control Center")
    st.error("Registry database not found: `control_center/project_control.db`")
    st.code("python control_center/refresh_registry.py", language="bash")
    st.stop()

meta = {r["key"]: r["value"] for r in q("SELECT * FROM meta")}
git = q("SELECT * FROM git_info")
git = git[0] if git else {}
last_scan = q("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1")
last_scan = last_scan[0] if last_scan else {}

st.sidebar.title("🏗 Control Center")
st.sidebar.caption(f"tool v{meta.get('tool_version','?')} · READ-ONLY over trading code")
PAGES = ["Dashboard", "Module Registry", "Dependency Explorer", "Production Architecture",
         "Research Architecture", "Duplicate Audit", "Migration Board", "Config Explorer",
         "Copy Divergence", "Decisions"]
page = st.sidebar.radio("Page", PAGES)

st.sidebar.divider()
st.sidebar.caption(
    f"branch `{git.get('branch','?')}` · `{(git.get('head_commit') or '?')[:8]}`\n\n"
    f"dirty: {'yes' if git.get('is_dirty') else 'no'} · "
    f"untracked: {git.get('untracked_count', 0)}"
)
if st.sidebar.button("🔄 Refresh registry (rescan repo)"):
    with st.spinner("Rescanning repository…"):
        try:
            out = subprocess.run([sys.executable, os.path.join(HERE, "refresh_registry.py")],
                                 capture_output=True, text=True, timeout=600)
            st.sidebar.success("Refresh complete") if out.returncode == 0 else st.sidebar.error("Refresh failed")
            st.sidebar.code((out.stdout or out.stderr or "")[-1500:])
            st.rerun()
        except Exception as exc:
            st.sidebar.error(f"Refresh error: {exc}")

st.sidebar.divider()
st.sidebar.caption("Human decisions are preserved across refreshes.")


# --------------------------------------------------------------------------
# 1. Dashboard
# --------------------------------------------------------------------------
def page_dashboard():
    st.title("Dashboard")
    st.caption(f"Scanned {last_scan.get('started','?')} · commit "
               f"`{(last_scan.get('commit_hash') or '?')[:8]}`")

    by_type = q(f"SELECT {EFF_TYPE} AS t, COUNT(*) n FROM modules GROUP BY t ORDER BY n DESC")
    by_status = q(f"SELECT {EFF_STATUS} AS s, COUNT(*) n FROM modules GROUP BY s ORDER BY n DESC")
    total = q("SELECT COUNT(*) n FROM modules")[0]["n"]
    prod = q("SELECT COUNT(*) n FROM modules WHERE production_used=1")[0]["n"]
    unknown = q(f"SELECT COUNT(*) n FROM modules WHERE {EFF_STATUS}='UNKNOWN'")[0]["n"]
    legacy = q(f"SELECT COUNT(*) n FROM modules WHERE {EFF_STATUS}='LEGACY'")[0]["n"]
    exp = q(f"SELECT COUNT(*) n FROM modules WHERE {EFF_STATUS}='EXPERIMENTAL'")[0]["n"]
    research = q(f"SELECT COUNT(*) n FROM modules WHERE {EFF_STATUS} IN ('RESEARCH','EXPERIMENTAL')")[0]["n"]
    review = q("SELECT COUNT(*) n FROM modules WHERE needs_review=1")[0]["n"]
    dup = q("SELECT COUNT(DISTINCT group_name) n FROM duplicates")[0]["n"]
    mig = q("SELECT COUNT(*) n FROM migrations WHERE status NOT LIKE '%FROZEN%'")[0]["n"]
    div = q("SELECT COUNT(*) n FROM copy_divergence WHERE status IN ('content_differs','only_in_primary','only_in_secondary')")[0]["n"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total modules", total)
    c2.metric("Production-used", prod)
    c3.metric("Research / Experiment", research)
    c4.metric("Legacy", legacy)
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Experimental", exp)
    c6.metric("Unknown", unknown)
    c7.metric("Potential duplicates", dup)
    c8.metric("Open migrations", mig)

    c9, c10 = st.columns(2)
    c9.metric("Needs human review", review)
    c10.metric("Copy divergence (real)", div)

    st.divider()
    a, b = st.columns(2)
    with a:
        st.subheader("Modules by type")
        st.dataframe(df(by_type).rename(columns={"t": "type", "n": "count"}),
                     width="stretch", hide_index=True)
    with b:
        st.subheader("Modules by status")
        st.dataframe(df(by_status).rename(columns={"s": "status", "n": "count"}),
                     width="stretch", hide_index=True)

    st.subheader("Repo copies")
    st.dataframe(df(q("SELECT path,label,branch,substr(head_commit,1,8) AS head_commit,py_count,is_primary FROM repo_copies")),
                 width="stretch", hide_index=True)

    st.subheader("Quick answers")
    st.markdown(
        "- **What is production?** → *Production Architecture* page\n"
        "- **Who uses module X?** → *Dependency Explorer*\n"
        "- **Can I delete X?** → *Module Registry* → open the module → delete-safety panel\n"
        "- **Which regime engine is canonical?** → *Production Architecture* → Subsystem canonical"
    )


# --------------------------------------------------------------------------
# 2. Module Registry
# --------------------------------------------------------------------------
def page_modules():
    st.title("Module Registry")
    all_mods = q("""SELECT path, filename, type, status, architecture_layer, subsystem,
                    canonical, production_used, research_used, backtest_used, legacy,
                    experimental, confidence, needs_review, lines, last_modified,
                    replacement_module, evidence, purpose,
                    COALESCE(human_status,status) AS eff_status,
                    COALESCE(human_type,type) AS eff_type,
                    COALESCE(human_canonical,canonical) AS eff_canonical,
                    human_note, human_locked
                    FROM modules ORDER BY path""")
    mdf = df(all_mods)

    c1, c2, c3, c4 = st.columns(4)
    f_type = c1.multiselect("Type", sorted(mdf["eff_type"].dropna().unique()))
    f_status = c2.multiselect("Status", sorted(mdf["eff_status"].dropna().unique()))
    f_layer = c3.multiselect("Layer", sorted(mdf["architecture_layer"].dropna().unique()))
    f_flags = c4.multiselect("Flags", ["production_used", "research_used", "backtest_used",
                                       "legacy", "experimental", "canonical", "needs_review",
                                       "Unreferenced"])
    search = st.text_input("Search path / purpose", "")

    view = mdf.copy()
    if f_type:
        view = view[view["eff_type"].isin(f_type)]
    if f_status:
        view = view[view["eff_status"].isin(f_status)]
    if f_layer:
        view = view[view["architecture_layer"].isin(f_layer)]
    for flag in f_flags:
        if flag == "Unreferenced":
            view = view[(view["production_used"] == 0) & (view["research_used"] == 0) & (view["backtest_used"] == 0)]
        else:
            view = view[view[flag] == 1]
    if search:
        s = search.lower()
        view = view[view.apply(lambda r: s in str(r["path"]).lower() or s in str(r["purpose"]).lower(), axis=1)]

    st.caption(f"{len(view)} / {len(mdf)} modules")
    st.dataframe(view[["path", "eff_type", "eff_status", "architecture_layer", "subsystem",
                       "eff_canonical", "production_used", "research_used", "backtest_used",
                       "legacy", "needs_review", "lines"]],
                 width="stretch", hide_index=True, height=420)

    st.divider()
    st.subheader("Module detail / delete-safety")
    paths = sorted(mdf["path"].tolist())
    sel = st.selectbox("Select a module", paths)
    if sel:
        render_module_detail(sel)


def render_module_detail(path: str):
    r = q("SELECT * FROM modules WHERE path=?", (path,))
    if not r:
        return
    m = r[0]
    importers = q("SELECT DISTINCT source_module, dependency_type, function_used FROM dependencies WHERE target_module=? ORDER BY source_module", (path,))
    uses = q("SELECT DISTINCT target_module, dependency_type, function_used FROM dependencies WHERE source_module=? ORDER BY target_module", (path,))
    syms = q("SELECT name,kind,lineno,signature FROM symbols WHERE module_path=? ORDER BY lineno", (path,))

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        st.markdown(f"### `{path}`")
        st.markdown(f"**Type** `{m['type']}`  ·  **Status** `{m['status']}`  ·  "
                    f"**Layer** `{m['architecture_layer']}`  ·  **Subsystem** `{m['subsystem']}`")
        st.markdown(f"**Purpose:** {m['purpose'] or '—'}")
        if m["description"]:
            with st.expander("Docstring"):
                st.text(m["description"])
        st.markdown(f"**Evidence:** {m['evidence'] or '—'}")
        if m["replacement_module"]:
            st.warning(f"Replacement / successor: `{m['replacement_module']}`")
    with c2:
        st.markdown("#### Delete-safety")
        prod = "YES" if m["production_used"] else "NO"
        res = "YES" if (m["research_used"] or m["backtest_used"]) else "NO"
        st.markdown(
            f"- Production dependency: **{prod}**\n"
            f"- Research dependency: **{res}**\n"
            f"- Known replacement: `{m['replacement_module'] or '—'}`\n"
            f"- Last modified: `{m['last_modified'] or '?'}`\n"
            f"- Confidence: **{m['confidence']}**\n"
            f"- Human review required: **{'YES' if m['needs_review'] or prod == 'YES' else 'NO'}**"
        )
        if m["type"] in ("documentation", "utility", "test"):
            st.caption("(This module type is normally safe to keep; delete-safety is informational.)")
        st.info("Runtime 'last used' is not tracked (static analysis only). Deletion is NEVER automatic.")
    with c3:
        st.metric("Lines", m["lines"])
        st.metric("Canonical", "yes" if m["canonical"] else "no")

    t1, t2, t3 = st.tabs([f"Who uses it ({len(importers)})", f"What it uses ({len(uses)})", f"Symbols ({len(syms)})"])
    with t1:
        st.dataframe(df(importers).rename(columns={"source_module": "importer"}),
                     width="stretch", hide_index=True)
    with t2:
        st.dataframe(df(uses).rename(columns={"target_module": "dependency"}),
                     width="stretch", hide_index=True)
    with t3:
        st.dataframe(df(syms), width="stretch", hide_index=True)

    with st.expander("✍️ Human decision (persists across refresh)"):
        cur_status = m["human_status"] or m["status"]
        cur_type = m["human_type"] or m["type"]
        cur_canon = bool(m["human_canonical"] if m["human_canonical"] is not None else m["canonical"])
        statuses = ["ACTIVE", "VALIDATED", "RESEARCH", "EXPERIMENTAL", "LEGACY", "DEPRECATED", "UNKNOWN"]
        types = ["production", "core", "backtest", "research", "experiment", "audit",
                 "utility", "config", "test", "documentation", "UNKNOWN"]
        ns = st.selectbox("Human status", statuses, index=statuses.index(cur_status) if cur_status in statuses else 6)
        nt = st.selectbox("Human type", types, index=types.index(cur_type) if cur_type in types else len(types) - 1)
        nc = st.checkbox("Human canonical", value=cur_canon)
        note = st.text_area("Human note", value=m["human_note"] or "", height=80)
        lock = st.checkbox("Lock (protect from future auto-recompute)", value=bool(m["human_locked"]))
        if st.button("Save human decision", key=f"save_{path}"):
            exec_sql("""UPDATE modules SET human_status=?, human_type=?, human_canonical=?,
                        human_note=?, human_locked=? WHERE path=?""",
                     (ns, nt, int(nc), note, int(lock), path))
            st.success("Saved. This override survives refreshes.")
            st.rerun()
        if st.button("Clear human override", key=f"clr_{path}"):
            exec_sql("""UPDATE modules SET human_status=NULL, human_type=NULL, human_canonical=NULL,
                        human_note=NULL, human_locked=0 WHERE path=?""", (path,))
            st.success("Override cleared.")
            st.rerun()


# --------------------------------------------------------------------------
# 3. Dependency Explorer
# --------------------------------------------------------------------------
def page_deps():
    st.title("Dependency Explorer")
    paths = [r["path"] for r in q("SELECT path FROM modules ORDER BY path")]
    sel = st.selectbox("Module", paths, index=paths.index("src/agents/regime_agent.py") if "src/agents/regime_agent.py" in paths else 0)
    if not sel:
        return
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Who uses it? (upstream importers)")
        rows = q("""SELECT DISTINCT source_module, dependency_type, function_used, confidence
                    FROM dependencies WHERE target_module=? ORDER BY source_module""", (sel,))
        st.dataframe(df(rows).rename(columns={"source_module": "importer"}), width="stretch", hide_index=True)
        if not rows:
            st.caption("No importer found — this module is not referenced by any other module.")
    with c2:
        st.subheader("What does it use? (downstream)")
        rows = q("""SELECT DISTINCT target_module, dependency_type, function_used, confidence
                    FROM dependencies WHERE source_module=? ORDER BY target_module""", (sel,))
        st.dataframe(df(rows).rename(columns={"target_module": "dependency"}), width="stretch", hide_index=True)

    st.divider()
    st.subheader("All edges (filterable)")
    edges = df(q("""SELECT source_module, dependency_type, target_module, function_used, confidence
                    FROM dependencies ORDER BY source_module, target_module"""))
    f = st.text_input("Filter edges (path substring)", "")
    if f:
        edges = edges[edges.apply(lambda r: f.lower() in str(r["source_module"]).lower()
                                  or f.lower() in str(r["target_module"]).lower(), axis=1)]
    st.dataframe(edges, width="stretch", hide_index=True, height=320)


# --------------------------------------------------------------------------
# 4. Production Architecture
# --------------------------------------------------------------------------
def page_prod():
    st.title("Production Architecture")
    st.caption("What actually runs if you execute the production bot today.")
    steps = q("SELECT * FROM production_pipeline ORDER BY step_order")
    entry = "production/main.py"
    st.markdown(f"**Entrypoint:** `{entry}`  →  **Decision function:** "
                f"`production/pipeline.py::run_daily`")
    frozen = q("SELECT path, human_note FROM modules WHERE canonical=1 AND production_used=1 ORDER BY path")
    st.markdown("**Frozen contracts:** Regime v1 (`regime_dual_engine/REGIME_V1_FREEZE.md`), "
                "Setup v1 Pullback Only (`src/agents/SETUP_V1_FREEZE.md`)")
    st.divider()
    for s in steps:
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            with c1:
                st.markdown(f"**{s['stage']}** — `{s['module']}`  \n`{s['function']}`")
            with c2:
                st.caption(f"in: {s['inputs']}\nout: {s['outputs']}\nnext: {s['next_stage']} · {s['status']}")
            st.caption(f"evidence: {s['evidence']}")

    st.divider()
    st.subheader("Subsystem canonical map")
    rows = q("""SELECT subsystem,
                       GROUP_CONCAT(CASE WHEN canonical=1 THEN path END) AS canonical,
                       GROUP_CONCAT(CASE WHEN legacy=1 THEN path END) AS legacy
                FROM modules GROUP BY subsystem ORDER BY subsystem""")
    st.dataframe(df(rows), width="stretch", hide_index=True)


# --------------------------------------------------------------------------
# 5. Research Architecture
# --------------------------------------------------------------------------
def page_res():
    st.title("Research Architecture")
    st.caption("Separate from production. Legacy research engine + production-equivalent backtest.")
    steps = q("SELECT * FROM research_pipeline ORDER BY step_order")
    for s in steps:
        with st.container(border=True):
            st.markdown(f"**{s['stage']}** — `{s['module']}`  \n`{s['function']}`  ·  next: {s['next_stage']}")
            st.caption(f"in: {s['inputs']} → out: {s['outputs']}  ({s['evidence']})")
    st.divider()
    st.subheader("Backtest engines")
    st.dataframe(df(q("""SELECT path, status, canonical, production_used, replacement_module, purpose
                         FROM modules WHERE type='backtest' ORDER BY path""")),
                 width="stretch", hide_index=True)


# --------------------------------------------------------------------------
# 6. Duplicate Audit
# --------------------------------------------------------------------------
def page_dups():
    st.title("Duplicate Audit")
    st.warning("Potential duplicates only. **Nothing is ever deleted automatically.** "
               "Each group needs a human decision.")
    groups = q("SELECT group_name, COUNT(*) n FROM duplicates GROUP BY group_name ORDER BY n DESC")
    f = st.text_input("Filter group name", "")
    if f:
        groups = [g for g in groups if f.lower() in g["group_name"].lower()]
    st.caption(f"{len(groups)} group(s)")
    for g in groups:
        members = q("""SELECT member_path, evidence, similarity, possible_canonical,
                              human_decision, human_note FROM duplicates WHERE group_name=?""",
                    (g["group_name"],))
        with st.expander(f"{g['group_name']}  ({g['n']} members)"):
            for mem in members:
                st.markdown(f"- `{mem['member_path']}` — {mem['evidence']}")
            sel = st.selectbox("Set decision for", [m["member_path"] for m in members], key=f"sel_{g['group_name']}")
            dec = st.selectbox("Decision", ["(unset)", "KEEP", "KEEP_AS_LEGACY", "MERGE_INTO", "REVIEW"],
                               key=f"dec_{g['group_name']}")
            note = st.text_input("Note", value="", key=f"note_{g['group_name']}")
            if st.button("Save", key=f"save_{g['group_name']}"):
                exec_sql("UPDATE duplicates SET human_decision=?, human_note=? WHERE group_name=? AND member_path=?",
                         (dec, note, g["group_name"], sel))
                st.success("Saved.")
                st.rerun()


# --------------------------------------------------------------------------
# 7. Migration Board
# --------------------------------------------------------------------------
def page_mig():
    st.title("Migration Board")
    cols = st.columns(5)
    stages = ["TODO", "IN PROGRESS", "VALIDATED", "READY FOR PRODUCTION", "MIGRATED"]
    rows = q("SELECT * FROM migrations ORDER BY subsystem")
    def stage_of(status: str) -> str:
        s = (status or "").upper()
        if "NOT YET" in s or "NOT_CUT" in s:
            return "IN PROGRESS"
        if "AVAILABLE" in s:
            return "READY FOR PRODUCTION"
        if "VALIDATED" in s and "WIRED" in s:
            return "VALIDATED"
        if "FROZEN" in s or "WIRED" in s:
            return "MIGRATED"
        return "TODO"
    buckets = {k: [] for k in stages}
    for r in rows:
        buckets[stage_of(r["status"])].append(r)
    for col, stg in zip(cols, stages):
        with col:
            st.markdown(f"**{stg}** ({len(buckets[stg])})")
            for r in buckets[stg]:
                with st.container(border=True):
                    st.markdown(f"**{r['subsystem']}**")
                    st.caption(f"old: `{r['old_module']}`\n\nnew: `{r['new_module']}`")
                    st.caption(f"blocker: {r['blocker'] or '—'}")
                    st.caption(f"next: {r['next_action']}")
    st.divider()
    st.dataframe(df(rows[0:0]) if not rows else df([{k: r[k] for k in
                 ("subsystem", "old_module", "new_module", "status", "blocker", "evidence")} for r in rows]),
                 width="stretch", hide_index=True)


# --------------------------------------------------------------------------
# 8. Config Explorer
# --------------------------------------------------------------------------
def page_config():
    st.title("Config Explorer")
    st.caption("Find where a parameter is defined and which configs duplicate it (drift source).")
    params = q("SELECT parameter_name, COUNT(*) n FROM config_registry GROUP BY parameter_name ORDER BY n DESC")
    sel = st.selectbox("Parameter", [p["parameter_name"] for p in params])
    if sel:
        rows = q("SELECT file, default_value, used_by, purpose, subsystem, duplicate_count FROM config_registry WHERE parameter_name=?", (sel,))
        n = rows[0]["duplicate_count"] if rows else 0
        if n:
            st.warning(f"⚠️ `{sel}` is defined in {len(rows)} config files — a known architecture-drift source.")
        else:
            st.success(f"`{sel}` is defined in only one config file.")
        st.dataframe(df(rows), width="stretch", hide_index=True)
        used = q("SELECT DISTINCT source_module, function_used FROM dependencies WHERE target_module LIKE '%config.py'")
        st.caption("Modules importing a config module:")
        st.dataframe(df(used), width="stretch", hide_index=True)

    st.divider()
    st.subheader("All duplicated parameters")
    dups = q("""SELECT parameter_name, COUNT(*) n, GROUP_CONCAT(file, ' | ') files
                FROM config_registry GROUP BY parameter_name HAVING n>1 ORDER BY n DESC, parameter_name""")
    st.dataframe(df(dups), width="stretch", hide_index=True)
    st.divider()
    st.subheader("Full config registry")
    st.dataframe(df(q("""SELECT parameter_name, file, default_value, subsystem, duplicate_count
                         FROM config_registry ORDER BY file, parameter_name""")),
                 width="stretch", hide_index=True, height=360)


# --------------------------------------------------------------------------
# 9. Copy Divergence
# --------------------------------------------------------------------------
def page_div():
    st.title("Copy Divergence")
    st.caption("Comparison of the two repo copies on this machine (line endings normalised).")
    stat = q("SELECT status, COUNT(*) n FROM copy_divergence GROUP BY status ORDER BY n DESC")
    st.dataframe(df(stat), width="stretch", hide_index=True)
    statuses = st.multiselect("Filter status", [s["status"] for s in stat],
                              default=[s["status"] for s in stat if s["status"] != "line_ending_only"])
    rows = q("SELECT rel_path, status FROM copy_divergence ORDER BY status, rel_path")
    if statuses:
        rows = [r for r in rows if r["status"] in statuses]
    st.dataframe(df(rows), width="stretch", hide_index=True, height=460)
    st.info("`line_ending_only` = identical after normalising CRLF/LF + BOM. "
            "`content_differs` = real drift. `only_in_*` = file exists in one copy only.")


# --------------------------------------------------------------------------
# 10. Decisions
# --------------------------------------------------------------------------
def page_decisions():
    st.title("Decisions / TODO")
    st.caption("Architecture-level decisions. This system is human-in-the-loop: the AI never decides.")
    rows = q("SELECT * FROM decisions ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END, issue")
    for d in rows:
        with st.container(border=True):
            st.markdown(f"**[{d['priority']}] {d['issue']}**")
            st.markdown(f"- Decision needed: {d['decision_needed']}\n"
                        f"- Current state: {d['current_state']}\n"
                        f"- Proposed: {d['proposed_action']}\n"
                        f"- Affected: `{d['affected_modules']}`\n"
                        f"- Evidence: {d['evidence']}")
            c1, c2 = st.columns([1, 3])
            with c1:
                st.caption(f"status: {d['status']}")
            with c2:
                note = st.text_input("Human decision / note", value=d["human_note"] or "", key=f"dec_note_{d['issue']}")
                stt = st.selectbox("Status", ["OPEN", "DECIDED", "DEFERRED", "REJECTED", "DONE"],
                                   index=["OPEN", "DECIDED", "DEFERRED", "REJECTED", "DONE"].index(d["status"])
                                   if d["status"] in ["OPEN", "DECIDED", "DEFERRED", "REJECTED", "DONE"] else 0,
                                   key=f"dec_st_{d['issue']}")
                if st.button("Save", key=f"dec_save_{d['issue']}"):
                    exec_sql("UPDATE decisions SET human_note=?, human_decision=? WHERE issue=?", (note, stt, d["issue"]))
                    st.success("Saved.")
                    st.rerun()


PAGES_FN = {
    "Dashboard": page_dashboard,
    "Module Registry": page_modules,
    "Dependency Explorer": page_deps,
    "Production Architecture": page_prod,
    "Research Architecture": page_res,
    "Duplicate Audit": page_dups,
    "Migration Board": page_mig,
    "Config Explorer": page_config,
    "Copy Divergence": page_div,
    "Decisions": page_decisions,
}
PAGES_FN[page]()
