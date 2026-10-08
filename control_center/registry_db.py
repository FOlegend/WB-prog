"""registry_db.py — SQLite schema + upsert helpers for the Codebase Registry.

Design rules
------------
* SQLite only: no server, easy to back up, queryable from Streamlit.
* Every refresh is a FULL RECOMPUTE of computed columns.
* Human decisions are NEVER overwritten: any column prefixed ``human_`` is
  snapshotted before a refresh and restored afterwards (see ``snapshot_human``
  / ``restore_human``).
* Effective value = human override if present else computed value
  (see ``effective`` SQL expressions used by the UI).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime

TOOL_VERSION = "1.0.0"

DEFAULT_DB_NAME = "project_control.db"

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS repo_copies (
    path TEXT PRIMARY KEY,
    label TEXT,
    head_commit TEXT,
    branch TEXT,
    py_count INTEGER,
    is_primary INTEGER DEFAULT 0,
    scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS modules (
    repo TEXT NOT NULL,
    path TEXT NOT NULL,
    filename TEXT,
    module_name TEXT,
    ext TEXT,
    type TEXT,
    status TEXT,
    architecture_layer TEXT,
    subsystem TEXT,
    purpose TEXT,
    description TEXT,
    canonical INTEGER DEFAULT 0,
    production_used INTEGER DEFAULT 0,
    production_decision_authority TEXT DEFAULT 'NONE',
    research_used INTEGER DEFAULT 0,
    backtest_used INTEGER DEFAULT 0,
    legacy INTEGER DEFAULT 0,
    experimental INTEGER DEFAULT 0,
    replacement_module TEXT,
    confidence TEXT DEFAULT 'LOW',
    evidence TEXT,
    needs_review INTEGER DEFAULT 1,
    owner TEXT,
    created_date TEXT,
    last_modified TEXT,
    size_bytes INTEGER,
    lines INTEGER,
    content_hash TEXT,
    has_main_guard INTEGER DEFAULT 0,
    is_cli_entry INTEGER DEFAULT 0,
    docstring TEXT,
    human_type TEXT,
    human_status TEXT,
    human_canonical INTEGER,
    human_note TEXT,
    human_locked INTEGER DEFAULT 0,
    notes TEXT,
    PRIMARY KEY (repo, path)
);

CREATE TABLE IF NOT EXISTS symbols (
    repo TEXT,
    module_path TEXT,
    name TEXT,
    kind TEXT,
    lineno INTEGER,
    is_public INTEGER,
    signature TEXT,
    docstring TEXT,
    PRIMARY KEY (repo, module_path, name, kind)
);

CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT,
    source_module TEXT,
    target_module TEXT,
    dependency_type TEXT,
    import_name TEXT,
    function_used TEXT,
    discovered_from TEXT,
    confidence TEXT
);

CREATE TABLE IF NOT EXISTS config_registry (
    repo TEXT,
    parameter_name TEXT,
    file TEXT,
    default_value TEXT,
    current_value TEXT,
    used_by TEXT,
    purpose TEXT,
    subsystem TEXT,
    experimental INTEGER DEFAULT 0,
    deprecated INTEGER DEFAULT 0,
    duplicate_count INTEGER DEFAULT 0,
    human_note TEXT,
    human_locked INTEGER DEFAULT 0,
    PRIMARY KEY (repo, parameter_name, file)
);

CREATE TABLE IF NOT EXISTS production_pipeline (
    repo TEXT,
    step_order INTEGER,
    stage TEXT,
    module TEXT,
    function TEXT,
    inputs TEXT,
    outputs TEXT,
    next_stage TEXT,
    status TEXT,
    evidence TEXT,
    PRIMARY KEY (repo, step_order)
);

CREATE TABLE IF NOT EXISTS research_pipeline (
    repo TEXT,
    step_order INTEGER,
    stage TEXT,
    module TEXT,
    function TEXT,
    inputs TEXT,
    outputs TEXT,
    next_stage TEXT,
    status TEXT,
    evidence TEXT,
    PRIMARY KEY (repo, step_order)
);

CREATE TABLE IF NOT EXISTS duplicates (
    repo TEXT,
    group_name TEXT,
    member_path TEXT,
    evidence TEXT,
    similarity TEXT,
    possible_canonical TEXT,
    human_decision TEXT,
    human_note TEXT,
    PRIMARY KEY (repo, group_name, member_path)
);

CREATE TABLE IF NOT EXISTS migrations (
    repo TEXT,
    subsystem TEXT,
    old_module TEXT,
    new_module TEXT,
    status TEXT,
    next_action TEXT,
    blocker TEXT,
    evidence TEXT,
    human_decision TEXT,
    human_note TEXT,
    updated_at TEXT,
    PRIMARY KEY (repo, subsystem, old_module)
);

CREATE TABLE IF NOT EXISTS decisions (
    repo TEXT,
    issue TEXT,
    decision_needed TEXT,
    current_state TEXT,
    proposed_action TEXT,
    evidence TEXT,
    affected_modules TEXT,
    status TEXT,
    priority TEXT,
    human_decision TEXT,
    human_note TEXT,
    PRIMARY KEY (repo, issue)
);

CREATE TABLE IF NOT EXISTS assets (
    repo TEXT,
    path TEXT,
    ext TEXT,
    category TEXT,
    size_bytes INTEGER,
    last_modified TEXT,
    content_hash TEXT,
    PRIMARY KEY (repo, path)
);

CREATE TABLE IF NOT EXISTS asset_summary (
    repo TEXT,
    category TEXT,
    file_count INTEGER,
    total_bytes INTEGER,
    note TEXT,
    PRIMARY KEY (repo, category)
);

CREATE TABLE IF NOT EXISTS copy_divergence (
    rel_path TEXT PRIMARY KEY,
    ext TEXT,
    status TEXT,
    hash_primary TEXT,
    hash_secondary TEXT
);

CREATE TABLE IF NOT EXISTS git_info (
    repo TEXT PRIMARY KEY,
    branch TEXT,
    head_commit TEXT,
    head_subject TEXT,
    head_date TEXT,
    is_dirty INTEGER,
    untracked_count INTEGER,
    modified_count INTEGER,
    remote TEXT
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT,
    finished TEXT,
    primary_repo TEXT,
    secondary_repo TEXT,
    py_files INTEGER,
    other_files INTEGER,
    deps INTEGER,
    commit_hash TEXT,
    tool_version TEXT
);

CREATE INDEX IF NOT EXISTS idx_modules_type ON modules(type);
CREATE INDEX IF NOT EXISTS idx_modules_status ON modules(status);
CREATE INDEX IF NOT EXISTS idx_modules_layer ON modules(architecture_layer);
CREATE INDEX IF NOT EXISTS idx_deps_source ON dependencies(source_module);
CREATE INDEX IF NOT EXISTS idx_deps_target ON dependencies(target_module);
CREATE INDEX IF NOT EXISTS idx_config_param ON config_registry(parameter_name);
"""

# Tables whose human_* columns must survive a refresh.
HUMAN_KEYED_TABLES = {
    "modules": ("repo", "path"),
    "config_registry": ("repo", "parameter_name", "file"),
    "duplicates": ("repo", "group_name", "member_path"),
    "migrations": ("repo", "subsystem", "old_module"),
    "decisions": ("repo", "issue"),
}

HUMAN_COLUMNS = {
    "modules": ["human_type", "human_status", "human_canonical", "human_note", "human_locked"],
    "config_registry": ["human_note", "human_locked"],
    "duplicates": ["human_decision", "human_note"],
    "migrations": ["human_decision", "human_note"],
    "decisions": ["human_decision", "human_note"],
}


def db_path(control_center_dir: str) -> str:
    return os.path.join(control_center_dir, DEFAULT_DB_NAME)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_columns(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("tool_version", TOOL_VERSION),
    )
    conn.commit()


# Columns added after the first release. Kept here so an existing
# project_control.db is upgraded in place instead of being rebuilt.
ADDED_COLUMNS = {
    # Phase 3: distinguishes "the production pipeline imports/uses this"
    # from "this module's output actually decides production actions".
    "modules": {"production_decision_authority": "TEXT DEFAULT 'NONE'"},
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, cols in ADDED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


# --------------------------------------------------------------------------
# Human-decision preservation
# --------------------------------------------------------------------------
def snapshot_human(conn: sqlite3.Connection) -> dict:
    """Read every ``human_*`` value keyed by the table's natural key."""
    snap: dict = {}
    for table, key_cols in HUMAN_KEYED_TABLES.items():
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        cols = HUMAN_COLUMNS[table]
        for r in rows:
            key = tuple(r[c] for c in key_cols)
            vals = {c: r[c] for c in cols}
            if any(v not in (None, "", 0) for v in vals.values()):
                snap[(table, key)] = vals
    return snap


def restore_human(conn: sqlite3.Connection, snap: dict) -> int:
    """Re-apply snapshotted human_* values after a recompute."""
    restored = 0
    for (table, key), vals in snap.items():
        key_cols = HUMAN_KEYED_TABLES[table]
        where = " AND ".join(f"{c} = ?" for c in key_cols)
        sets = ", ".join(f"{c} = ?" for c in vals)
        sql = f"UPDATE {table} SET {sets} WHERE {where}"
        cur = conn.execute(sql, list(vals.values()) + list(key))
        restored += cur.rowcount
    conn.commit()
    return restored


# --------------------------------------------------------------------------
# Generic writers
# --------------------------------------------------------------------------
def clear_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"DELETE FROM {table}")


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    collist = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})"
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    return len(rows)


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def log_scan_run(conn: sqlite3.Connection, **kw) -> None:
    kw.setdefault("tool_version", TOOL_VERSION)
    kw.setdefault("finished", datetime.now().isoformat(timespec="seconds"))
    cols = ["started", "finished", "primary_repo", "secondary_repo", "py_files",
            "other_files", "deps", "commit_hash", "tool_version"]
    conn.execute(
        f"INSERT INTO scan_runs ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [kw.get(c) for c in cols],
    )
    conn.commit()
