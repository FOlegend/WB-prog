# Trading Bot Control Center

The **single source of truth** for the WB-prog swing-trading codebase.

Its job is **not** to add trading functionality. It answers, without you
re-reading the whole repo:

- What is each script for?
- Which scripts are production? research? legacy? experimental?
- Who imports / calls what?
- Which of two similar scripts is canonical?
- Which legacy scripts are safe to delete?
- What is the real production pipeline?
- What is currently being migrated?

## Contents

| File | Role |
|---|---|
| `refresh_registry.py` | **Rescan the repo** → rebuild `project_control.db` + `ARCHITECTURE.*`. READ-ONLY over trading code. |
| `control_center.py` | **Streamlit UI** (10 pages). |
| `project_control.db` | SQLite registry (the single source of truth). |
| `scanner.py` | AST scanner (imports, calls, symbols, assets, git, copy diff). |
| `classify.py` | Classification rules + curated evidence + pipelines. |
| `registry_db.py` | SQLite schema + human-decision preservation. |
| `generate_docs.py` | Emits `ARCHITECTURE.md` / `ARCHITECTURE.yaml`. |
| `ARCHITECTURE.md` / `ARCHITECTURE.yaml` | GENERATED architecture map (human + machine). |
| `AGENT_WORKFLOW.md` | Mandatory before/after workflow for any AI agent. |
| `ARCHITECTURE_AUDIT_REPORT.md` | Phase 1 audit findings. |
| `requirements-control-center.txt` | Dependencies (streamlit, pandas). |

## Usage

```bash
# 1. Build / refresh the registry (READ-ONLY on trading code)
python control_center/refresh_registry.py

#    scan a different repo / diff against another copy
python control_center/refresh_registry.py --repo "<path>" --compare-repo "<other copy>"
python control_center/refresh_registry.py --no-compare

# 2. Open the Control Center
streamlit run control_center/control_center.py
```

## Safety guarantees

- **Never modifies trading code.** The only writes are to `project_control.db`
  and the generated `ARCHITECTURE.*` docs.
- **Never deletes anything.** It answers delete-safety questions instead.
- **Never overwrites human decisions.** Every `human_*` field is snapshotted
  before a refresh and restored afterwards.
- **Never guesses canonical.** Insufficient evidence → `UNKNOWN` + NEED HUMAN REVIEW.

## Pages

Dashboard · Module Registry · Dependency Explorer · Production Architecture ·
Research Architecture · Duplicate Audit · Migration Board · Config Explorer ·
Copy Divergence · Decisions
