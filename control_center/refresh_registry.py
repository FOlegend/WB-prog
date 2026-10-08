"""refresh_registry.py — rescan the repo and rebuild project_control.db.

READ-ONLY with respect to the trading code: it only writes
``control_center/project_control.db`` plus the generated ``ARCHITECTURE.*`` docs.

Usage
-----
    python control_center/refresh_registry.py
    python control_center/refresh_registry.py --repo "<path to repo>"
    python control_center/refresh_registry.py --no-compare
    python control_center/refresh_registry.py --compare-repo "<other copy>"

Human decisions (any ``human_*`` column) are snapshotted before the recompute
and restored afterwards, so a refresh never overwrites them.

Note: the default output filename in the spec is ``refresh_registry.py``; it
lives in ``control_center/`` for tidiness.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import registry_db as db
import scanner
import classify
import generate_docs

DEFAULT_SECONDARY = "WB prog"  # sibling copy observed on this machine


def _find_secondary(primary_root: str) -> str | None:
    parent = os.path.dirname(primary_root)
    cand = os.path.join(parent, DEFAULT_SECONDARY)
    if os.path.isdir(cand) and os.path.abspath(cand) != os.path.abspath(primary_root):
        return cand
    return None


def refresh(repo_root: str, compare_repo: str | None, db_file: str,
            do_compare: bool = True) -> dict:
    started = datetime.now().isoformat(timespec="seconds")
    repo_label = "primary"
    print(f"[1/6] scanning primary repo: {repo_root}")
    primary = scanner.scan_repo(repo_root, repo_label, is_primary=True)
    print(f"      {len(primary['modules'])} python files, "
          f"{len(primary['deps'])} dependency edges, {len(primary['assets'])} assets")

    secondary = None
    if do_compare and compare_repo and os.path.isdir(compare_repo):
        print(f"[2/6] scanning secondary copy: {compare_repo}")
        secondary = scanner.scan_repo(compare_repo, "secondary", is_primary=False)
        print(f"      {len(secondary['modules'])} python files")
    else:
        print("[2/6] secondary copy: skipped")

    print("[3/6] classifying modules / configs / pipelines")
    module_rows = classify.classify_modules(primary)
    config_rows = classify.extract_configs(repo_root, primary)
    classify.mark_config_duplicates(config_rows)
    prod_pipe = classify.production_pipeline_rows(repo_label)
    res_pipe = classify.research_pipeline_rows(repo_label)
    dup_rows = classify.detect_duplicates(primary)
    mig_rows = [dict(repo=repo_label, **m, updated_at=datetime.now().isoformat(timespec="seconds"))
                for m in classify.MIGRATIONS]
    dec_rows = [dict(repo=repo_label, **d) for d in classify.DECISIONS]

    symbols = [dict(repo=repo_label, **s) for s in primary["symbols"]]
    deps = [dict(repo=repo_label, **{k: d[k] for k in
             ("source_module", "target_module", "dependency_type", "import_name",
              "function_used", "confidence")},
             discovered_from="ast") for d in primary["deps"]]
    assets = [dict(repo=repo_label, **a) for a in primary["assets"]]
    asset_sum = [dict(repo=repo_label, **a) for a in primary["asset_summary"]]

    print("[4/6] writing database")
    conn = db.connect(db_file)
    db.init_db(conn)
    snap = db.snapshot_human(conn)

    for table, rows in [
        ("modules", module_rows), ("symbols", symbols), ("dependencies", deps),
        ("config_registry", config_rows), ("production_pipeline", prod_pipe),
        ("research_pipeline", res_pipe), ("duplicates", dup_rows),
        ("migrations", mig_rows), ("decisions", dec_rows),
        ("assets", assets), ("asset_summary", asset_sum),
    ]:
        db.clear_table(conn, table)
        db.insert_rows(conn, table, rows)

    # repo copies + git + divergence
    copies = [dict(path=primary["root"], label="primary",
                   head_commit=primary["git"].get("head_commit"),
                   branch=primary["git"].get("branch"),
                   py_count=len(primary["modules"]), is_primary=1,
                   scanned_at=datetime.now().isoformat(timespec="seconds"))]
    if secondary:
        copies.append(dict(path=secondary["root"], label="secondary",
                           head_commit=secondary["git"].get("head_commit"),
                           branch=secondary["git"].get("branch"),
                           py_count=len(secondary["modules"]), is_primary=0,
                           scanned_at=datetime.now().isoformat(timespec="seconds")))
    db.clear_table(conn, "repo_copies")
    db.insert_rows(conn, "repo_copies", copies)

    db.clear_table(conn, "copy_divergence")
    if secondary:
        div = scanner.diff_copies(primary["root"], secondary["root"])
        db.insert_rows(conn, "copy_divergence", div)
        print(f"      copy divergence: {len(div)} differing files")
        for d in div[:12]:
            print(f"        - [{d['status']}] {d['rel_path']}")

    gi = primary["git"]
    db.clear_table(conn, "git_info")
    db.insert_rows(conn, "git_info", [dict(
        repo=repo_label, branch=gi.get("branch"), head_commit=gi.get("head_commit"),
        head_subject=gi.get("head_subject"), head_date=gi.get("head_date"),
        is_dirty=gi.get("is_dirty"), untracked_count=gi.get("untracked_count"),
        modified_count=gi.get("modified_count"), remote=gi.get("remote"))])

    restored = db.restore_human(conn, snap)
    if restored:
        print(f"      preserved {restored} human-edited row(s)")

    # Phase 3: record the production wiring mode actually in force, separately
    # from `production_used` (which only says "the pipeline imports this").
    db.set_meta(conn, "production_exit_engine_mode",
                classify.CURRENT_EXIT_ENGINE_MODE)
    db.set_meta(conn, "stop_exit_freeze_approved",
                classify.STOP_EXIT_FREEZE_APPROVED)
    db.set_meta(conn, "phase3_status", classify.PHASE3_STATUS)
    db.set_meta(conn, "decision_authority_note",
                "production_decision_authority: PRIMARY = decides production "
                "actions today; SHADOW_ONLY = evaluated/recorded, would decide "
                "only when exit_engine_mode='new'; NONE = no production "
                "decision authority")
    db.log_scan_run(
        conn, started=started, primary_repo=primary["root"],
        secondary_repo=(secondary["root"] if secondary else None),
        py_files=len(primary["modules"]), other_files=len(primary["assets"]),
        deps=len(deps), commit_hash=gi.get("head_commit"))
    conn.commit()

    print("[5/6] generating ARCHITECTURE.md / ARCHITECTURE.yaml")
    generate_docs.write_docs(conn, repo_root, db_file)

    print("[6/6] done")
    summary = {
        "modules": len(module_rows), "deps": len(deps), "configs": len(config_rows),
        "duplicates": len(dup_rows), "migrations": len(mig_rows),
        "divergence": len(scanner.diff_copies(primary["root"], secondary["root"])) if secondary else 0,
    }
    conn.close()
    return summary


def main():
    ap = argparse.ArgumentParser(description="Refresh the Codebase Registry (READ-ONLY on trading code).")
    ap.add_argument("--repo", default=os.path.dirname(_HERE),
                    help="primary repo root (default: parent of control_center/)")
    ap.add_argument("--compare-repo", default=None, help="secondary copy to diff against")
    ap.add_argument("--db", default=os.path.join(_HERE, db.DEFAULT_DB_NAME))
    ap.add_argument("--no-compare", action="store_true")
    args = ap.parse_args()

    secondary = args.compare_repo or _find_secondary(os.path.abspath(args.repo))
    s = refresh(os.path.abspath(args.repo), secondary, args.db,
                do_compare=not args.no_compare)
    print("\n=== registry summary ===")
    for k, v in s.items():
        print(f"  {k}: {v}")
    print(f"  db: {args.db}")


if __name__ == "__main__":
    main()
