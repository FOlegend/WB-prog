"""scanner.py — READ-ONLY repository archaeology for the Codebase Registry.

What it does
------------
* Walks a repo and parses every ``.py`` file with the ``ast`` module:
  imports, top-level functions/classes, ``if __name__ == "__main__"`` guards,
  argparse CLI entry points, and *call-level* dependencies (function_call edges
  derived from imported names).  Nothing is imported/executed — pure static read.
* Indexes non-code assets (``.json/.md/.yaml/.toml/...``) as lightweight rows.
* Collects git metadata (branch / HEAD / dirty counts) without committing.
* Compares two repo copies (content-hash level) to surface architecture drift.

It NEVER writes to the target repo.
"""
from __future__ import annotations

import ast
import hashlib
import os
import subprocess

EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                ".idea", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
# Content dirs indexed only as an aggregate summary (never per-file).
AGGREGATE_ONLY_DIRS = {"data/cache", "data/constituents"}
PY_EXT = ".py"
ASSET_EXTS = {".json", ".yaml", ".yml", ".toml", ".md", ".txt", ".csv", ".ini", ".cfg", ".ipynb"}
MAX_HASH_BYTES = 25 * 1024 * 1024  # skip hashing files larger than this


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _norm(p: str) -> str:
    return p.replace("\\", "/")


def rel_of(root: str, abspath: str) -> str:
    return _norm(os.path.relpath(abspath, root))


def sha1_file(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_HASH_BYTES:
            return None
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def iter_repo_files(root: str):
    """Yield (relpath, abspath, ext) for every indexed file."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        rel_dir = _norm(os.path.relpath(dirpath, root))
        for fn in filenames:
            abspath = os.path.join(dirpath, fn)
            rel = rel_of(root, abspath)
            yield rel, abspath, os.path.splitext(fn)[1].lower()


def build_module_index(root: str) -> dict:
    """Map dotted module name -> relpath for every .py file."""
    index: dict[str, str] = {}
    for rel, abspath, ext in iter_repo_files(root):
        if ext != PY_EXT:
            continue
        stem = rel[:-3]
        dotted = stem.replace("/", ".")
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
        index[dotted] = rel
        # also expose the bare top-level name (e.g. "config" -> config.py)
        if "/" not in stem:
            index.setdefault(stem, rel)
    return index


def _resolve(module_str: str, module_index: dict) -> str | None:
    """Resolve a dotted import string to a repo-relative .py path (best effort)."""
    if not module_str:
        return None
    parts = module_str.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in module_index:
            return module_index[cand]
    return None


def _package_of(relpath: str) -> str:
    d = os.path.dirname(relpath)
    return d.replace("/", ".") if d else ""


# --------------------------------------------------------------------------
# Python AST scan
# --------------------------------------------------------------------------
def parse_python(abspath: str, relpath: str, module_index: dict) -> dict:
    """Static analysis of a single .py file. Never executes it."""
    out = {
        "path": relpath,
        "imports": [],        # [{module, names:[...], lineno, level}]
        "deps": [],           # [{target, dependency_type, import_name, function_used, confidence}]
        "symbols": [],        # [{name, kind, lineno, is_public, signature, docstring}]
        "has_main_guard": 0,
        "is_cli_entry": 0,
        "docstring": "",
        "lines": 0,
        "parse_error": None,
    }
    try:
        with open(abspath, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError as exc:
        out["parse_error"] = str(exc)
        return out

    out["lines"] = src.count("\n") + 1
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        out["parse_error"] = f"SyntaxError: {exc}"
        return out

    out["docstring"] = (ast.get_docstring(tree) or "").strip()

    pkg = _package_of(relpath)
    # name -> (target_relpath, original_name)
    imported_names: dict[str, tuple[str, str]] = {}
    imported_modules: dict[str, str] = {}   # alias -> target_relpath

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                target = _resolve(a.name, module_index)
                out["imports"].append({"module": a.name, "names": [], "lineno": node.lineno, "level": 0})
                if target:
                    imported_modules[a.asname or a.name.split(".")[0]] = target
                    out["deps"].append({
                        "target": target, "dependency_type": "import",
                        "import_name": a.name, "function_used": None, "confidence": "HIGH",
                    })
                    if a.name.startswith(("historical_cache", "datasource")) or "datasource" in a.name:
                        out["deps"].append({
                            "target": target, "dependency_type": "data_dependency",
                            "import_name": a.name, "function_used": None, "confidence": "MEDIUM",
                        })
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            level = node.level or 0
            if level > 0:
                base = pkg.split(".") if pkg else []
                base = base[: len(base) - (level - 1)] if level > 1 else base
                mod = ".".join([p for p in base if p] + ([mod] if mod else []))
            target = _resolve(mod, module_index)
            names = [a.name for a in node.names]
            out["imports"].append({"module": mod, "names": names, "lineno": node.lineno, "level": level})
            if target:
                out["deps"].append({
                    "target": target, "dependency_type": "import",
                    "import_name": mod, "function_used": None, "confidence": "HIGH",
                })
                if mod.startswith("config") or ".config" in mod or mod.endswith("config"):
                    out["deps"].append({
                        "target": target, "dependency_type": "config_dependency",
                        "import_name": mod, "function_used": None, "confidence": "HIGH",
                    })
                for a in node.names:
                    if a.name != "*":
                        imported_names[a.asname or a.name] = (target, a.name)
            # Submodule imports: "from pkg import submod" and "from . import submod"
            for a in node.names:
                if a.name == "*":
                    continue
                submod = f"{mod}.{a.name}" if mod else a.name
                sub_target = _resolve(submod, module_index)
                if sub_target and sub_target != target:
                    out["deps"].append({
                        "target": sub_target, "dependency_type": "import",
                        "import_name": submod, "function_used": None, "confidence": "HIGH",
                    })
                    imported_modules[a.asname or a.name] = sub_target

    # top-level symbols
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["symbols"].append({
                "name": node.name, "kind": "function", "lineno": node.lineno,
                "is_public": int(not node.name.startswith("_")),
                "signature": _sig(node), "docstring": (ast.get_docstring(node) or "").strip()[:400],
            })
        elif isinstance(node, ast.ClassDef):
            out["symbols"].append({
                "name": node.name, "kind": "class", "lineno": node.lineno,
                "is_public": int(not node.name.startswith("_")),
                "signature": "", "docstring": (ast.get_docstring(node) or "").strip()[:400],
            })
        elif isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                out["has_main_guard"] = 1

    # CLI detection: argparse present near __main__ guard
    src_has_argparse = "argparse" in src
    if out["has_main_guard"] and src_has_argparse:
        out["is_cli_entry"] = 1

    # call-level dependencies
    called: set[str] = set()
    attr_calls: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                called.add(f.id)
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                attr_calls.add((f.value.id, f.attr))

    for name in called:
        if name in imported_names:
            target, orig = imported_names[name]
            out["deps"].append({
                "target": target, "dependency_type": "function_call",
                "import_name": None, "function_used": orig, "confidence": "HIGH",
            })
    for alias, attr in attr_calls:
        if alias in imported_modules:
            out["deps"].append({
                "target": imported_modules[alias], "dependency_type": "function_call",
                "import_name": None, "function_used": attr, "confidence": "MEDIUM",
            })
    return out


def _sig(node) -> str:
    try:
        args = node.args
        names = [a.arg for a in args.posonlyargs + args.args]
        if args.vararg:
            names.append("*" + args.vararg.arg)
        names += [a.arg for a in args.kwonlyargs]
        if args.kwarg:
            names.append("**" + args.kwarg.arg)
        returns = ""
        if node.returns is not None:
            try:
                returns = " -> " + ast.unparse(node.returns)
            except Exception:
                returns = ""
        return f"({', '.join(names)}){returns}"
    except Exception:
        return ""


# --------------------------------------------------------------------------
# Asset scan
# --------------------------------------------------------------------------
def categorize_asset(relpath: str, ext: str) -> str:
    r = relpath.lower()
    if r.startswith("reports/"):
        return "report"
    if "/config" in r or os.path.basename(r).startswith("config") or ext in (".toml", ".yaml", ".yml", ".ini", ".cfg"):
        return "config"
    if r.startswith("data/") or ext == ".csv":
        return "data"
    if ext == ".md" or ext == ".txt":
        return "documentation"
    return "other"


def scan_assets(root: str) -> tuple[list[dict], list[dict]]:
    """Return (asset_rows, asset_summary_rows)."""
    rows: list[dict] = []
    agg: dict[str, dict] = {}
    for rel, abspath, ext in iter_repo_files(root):
        if ext == PY_EXT:
            continue
        if ext not in ASSET_EXTS:
            continue
        in_aggregate = any(rel.startswith(d + "/") for d in AGGREGATE_ONLY_DIRS)
        try:
            size = os.path.getsize(abspath)
            mtime = _iso(os.path.getmtime(abspath))
        except OSError:
            size, mtime = 0, None
        cat = "data(cache)" if rel.startswith("data/cache") else categorize_asset(rel, ext)
        a = agg.setdefault(cat, {"file_count": 0, "total_bytes": 0})
        a["file_count"] += 1
        a["total_bytes"] += size
        if in_aggregate:
            continue
        rows.append({
            "path": rel, "ext": ext, "category": cat,
            "size_bytes": size, "last_modified": mtime,
            "content_hash": sha1_file(abspath) if size <= MAX_HASH_BYTES else None,
        })
    summary = [{"category": k, "file_count": v["file_count"], "total_bytes": v["total_bytes"],
                "note": "aggregate only" if k == "data(cache)" else ""} for k, v in sorted(agg.items())]
    return rows, summary


def _iso(ts: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Git metadata (read-only)
# --------------------------------------------------------------------------
def git_info(root: str) -> dict:
    def run(args):
        try:
            r = subprocess.run(["git", "-C", root] + args, capture_output=True,
                               text=True, timeout=25)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    head = run(["rev-parse", "HEAD"])
    subject = run(["log", "-1", "--pretty=%s"])
    date = run(["log", "-1", "--pretty=%cI"])
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    remote = run(["remote", "get-url", "origin"])
    porcelain = run(["status", "--porcelain"])
    untracked = sum(1 for l in porcelain.splitlines() if l.startswith("??"))
    modified = sum(1 for l in porcelain.splitlines() if l and not l.startswith("??"))
    return {
        "branch": branch, "head_commit": head, "head_subject": subject,
        "head_date": date, "remote": remote,
        "is_dirty": int(bool(porcelain)), "untracked_count": untracked,
        "modified_count": modified,
    }


# --------------------------------------------------------------------------
# Whole-repo scan
# --------------------------------------------------------------------------
def scan_repo(root: str, label: str, is_primary: bool = False) -> dict:
    module_index = build_module_index(root)
    modules, all_deps, all_symbols = [], [], []
    for rel, abspath, ext in iter_repo_files(root):
        if ext != PY_EXT:
            continue
        parsed = parse_python(abspath, rel, module_index)
        stem = rel[:-3].replace("/", ".")
        if stem.endswith(".__init__"):
            stem = stem[: -len(".__init__")]
        try:
            size = os.path.getsize(abspath)
            mtime = _iso(os.path.getmtime(abspath))
        except OSError:
            size, mtime = 0, None
        modules.append({
            "path": rel, "filename": os.path.basename(rel),
            "module_name": stem, "ext": ext, "size_bytes": size,
            "last_modified": mtime, "lines": parsed["lines"],
            "content_hash": sha1_file(abspath),
            "has_main_guard": parsed["has_main_guard"],
            "is_cli_entry": parsed["is_cli_entry"],
            "docstring": parsed["docstring"][:600],
            "parse_error": parsed["parse_error"],
            "imports": parsed["imports"],
        })
        for s in parsed["symbols"]:
            all_symbols.append({"module_path": rel, **s})
        for d in parsed["deps"]:
            all_deps.append({
                "source_module": rel, "target_module": d["target"],
                "dependency_type": d["dependency_type"], "import_name": d.get("import_name"),
                "function_used": d.get("function_used"), "confidence": d["confidence"],
            })
    asset_rows, asset_summary = scan_assets(root)
    return {
        "root": root, "label": label, "is_primary": is_primary,
        "modules": modules, "deps": all_deps, "symbols": all_symbols,
        "assets": asset_rows, "asset_summary": asset_summary,
        "git": git_info(root),
    }


def _norm_text_hash(path: str) -> str | None:
    """Hash text with CRLF/BOM/trailing-space normalised, so that pure
    line-ending differences between two checkouts do not look like drift."""
    try:
        if os.path.getsize(path) > MAX_HASH_BYTES:
            return None
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    text = text.lstrip("\ufeff")
    norm = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def diff_copies(primary_root: str, secondary_root: str) -> list[dict]:
    """Content comparison of two copies of the same repo.

    Text files are compared with line endings / BOM / trailing whitespace
    normalised; a raw mismatch that disappears after normalisation is reported
    as ``line_ending_only`` (informational), not as real drift.
    """
    def maps(root):
        raw_m, norm_m = {}, {}
        for rel, abspath, ext in iter_repo_files(root):
            if ext not in (PY_EXT,) and ext not in ASSET_EXTS:
                continue
            if any(rel.startswith(d + "/") for d in AGGREGATE_ONLY_DIRS):
                continue
            raw_m[rel] = sha1_file(abspath)
            if ext in {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".csv", ".ini", ".cfg"}:
                norm_m[rel] = _norm_text_hash(abspath)
            else:
                norm_m[rel] = raw_m[rel]
        return raw_m, norm_m

    ra, na = maps(primary_root)
    rb, nb = maps(secondary_root)
    rows = []
    for rel in sorted(set(ra) | set(rb)):
        in_a, in_b = rel in ra, rel in rb
        if in_a and not in_b:
            status = "only_in_primary"
        elif in_b and not in_a:
            status = "only_in_secondary"
        elif na.get(rel) != nb.get(rel):
            status = "content_differs"
        elif ra.get(rel) != rb.get(rel):
            status = "line_ending_only"
        else:
            continue
        rows.append({"rel_path": rel, "ext": os.path.splitext(rel)[1].lower(),
                     "status": status, "hash_primary": ra.get(rel), "hash_secondary": rb.get(rel)})
    return rows
