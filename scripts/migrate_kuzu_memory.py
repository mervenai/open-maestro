#!/usr/bin/env python3
"""Migrate project memories from kuzu-memory to Basic Memory.

kuzu-memory is discontinued; its databases (``.kuzu-memory/memories.db`` per
project, ``~/.kuzu-memory/user.db`` for promoted cross-project patterns) hold
accumulated decisions that should survive as plain Markdown.

For each project:
  1. Copy the kuzu DB to a temp dir and export via the (still functional)
     ``kuzu-memory memory export`` — the live DB is often locked by stale
     kuzu-memory MCP server processes, which must not be disturbed.
  2. Write one Markdown note per memory into ``<project>/memory/`` with the
     original cognitive/knowledge types, importance, timestamps, and content
     hash preserved as frontmatter.
  3. Register the project with ``bm`` (indexes the notes) and add a
     ``.bmignore`` for heavy directories so indexing stays fast.

Rerunnable: a note whose frontmatter ``memory_hash`` matches is skipped.

Usage:
  python scripts/migrate_kuzu_memory.py --projects ~/projects/M3RFP/PMS2DR [...]
  python scripts/migrate_kuzu_memory.py --all          # scan ~/projects, ~/dev
  python scripts/migrate_kuzu_memory.py --all --dry-run
  python scripts/migrate_kuzu_memory.py --user-db      # also migrate ~/.kuzu-memory/user.db
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SCAN_ROOTS = [Path.home() / "projects", Path.home() / "dev"]
USER_DB_PROJECT = Path.home() / ".basic-memory" / "user-patterns"
BMIGNORE_DEFAULTS = [
    "M3CodeRepo/",
    ".venv/",
    "venv/",
    "node_modules/",
    "dist/",
    ".git/",
    ".pytest_cache/",
    ".ruff_cache/",
    "__pycache__/",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str, max_len: int = 48) -> str:
    out = _SLUG_RE.sub("-", text.lower()).strip("-")
    return out[:max_len].strip("-") or "memory"


def run(cmd: list[str], timeout: int = 180, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd[:5])} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def export_kuzu_db(db_path: Path, workdir: Path) -> dict:
    """Copy the DB aside and export it, dodging the live single-writer lock."""
    copy_dir = workdir / "db-copy"
    shutil.copytree(db_path.parent, copy_dir, ignore=shutil.ignore_patterns("*.wal"))
    out_dir = workdir / "export"
    out_dir.mkdir()
    run(
        [
            "kuzu-memory", "--db-path", str(copy_dir / "memories.db"),
            "memory", "export", "--include-archived", "--output", str(out_dir),
        ]
    )
    backups = sorted(out_dir.glob("*.json"))
    if not backups:
        raise RuntimeError("export produced no JSON backup")
    return json.loads(backups[0].read_text())


def note_for_memory(mem: dict) -> tuple[str, str]:
    """Return (filename, markdown) for one exported kuzu memory."""
    content = (mem.get("content") or "").strip()
    mem_id = str(mem.get("id", ""))[:8]
    first_line = next(
        (line.strip().lstrip("#").strip() for line in content.splitlines() if line.strip()),
        "memory",
    )
    title = first_line[:72]
    permalink = f"{slug(first_line, 40)}-{mem_id}"
    knowledge = mem.get("knowledge_type") or "note"
    mtype = mem.get("memory_type") or "note"

    frontmatter = [
        "---",
        f"title: {json.dumps(title)}",
        f"type: {slug(mtype, 20)}",
        f"permalink: {permalink}",
        f"tags: [kuzu-migration, {slug(knowledge, 20)}]",
        f"knowledge_type: {knowledge}",
        f"memory_type: {mtype}",
        f"importance: {round(float(mem.get('importance') or 0.5), 3)}",
        f"source: kuzu-migration",
        f"source_type: {mem.get('source_type') or 'unknown'}",
    ]
    if mem.get("session_id"):
        frontmatter.append(f"session_id: {mem['session_id']}")
    frontmatter.append(f"created: {mem.get('created_at', '')}")
    frontmatter.append(f"memory_hash: {mem.get('content_hash', '')}")
    frontmatter.append("---")
    body = content if content else "(empty memory)"
    return f"{permalink}.md", "\n".join(frontmatter) + "\n\n" + body + "\n"


def ensure_bmignore(project: Path) -> None:
    target = project / ".bmignore"
    existing = target.read_text().splitlines() if target.exists() else []
    missing = [p for p in BMIGNORE_DEFAULTS if p not in existing]
    if missing:
        target.write_text("\n".join(existing + missing) + "\n")


def project_name_for(root: Path, registered: dict[str, str]) -> str:
    base = slug(root.name)
    if registered.get(base) in {None, str(root.resolve())}:
        return base
    import hashlib

    return f"{base}-{hashlib.sha1(str(root.resolve()).encode()).hexdigest()[:8]}"


def registered_projects() -> dict[str, str]:
    out = run(["bm", "project", "list", "--local", "--json"])
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return {}
    return {
        item["name"]: str(Path(item["local_path"]).expanduser().resolve())
        for item in data.get("projects", [])
        if item.get("local_path")
    }


def migrate_project(root: Path, dry_run: bool, force: bool = False) -> dict:
    db_dir = root / ".kuzu-memory"
    stats = {"project": root.name, "memories": 0, "written": 0, "skipped": 0, "error": ""}
    if not (db_dir / "memories.db").exists():
        stats["error"] = "no .kuzu-memory/memories.db"
        return stats

    with tempfile.TemporaryDirectory(prefix="kuzu-migrate-") as td:
        try:
            export = export_kuzu_db(db_dir / "memories.db", Path(td))
        except Exception as exc:
            stats["error"] = f"export failed: {exc}"
            return stats

    memories = export.get("memories", []) + export.get("archived", [])
    stats["memories"] = len(memories)
    if dry_run:
        return stats
    if not memories and str(root.resolve()) not in set(registered_projects().values()):
        return stats  # empty legacy DB and not yet registered — leave it untouched

    memory_dir = root / "memory"
    memory_dir.mkdir(exist_ok=True)
    for mem in memories:
        filename, markdown = note_for_memory(mem)
        target = memory_dir / filename
        if target.exists():
            if f"memory_hash: {mem.get('content_hash', '')}" in target.read_text():
                stats["skipped"] += 1
                continue
            if not force:
                stats["skipped"] += 1
                continue
        target.write_text(markdown)
        stats["written"] += 1

    ensure_bmignore(root)
    registered = registered_projects()
    name = project_name_for(root, registered)
    if name not in registered:
        run(["bm", "project", "add", name, str(root.resolve()), "--no-wait"], timeout=300)
    run(["bm", "project", "index", name], timeout=600)
    return stats


def migrate_user_db(dry_run: bool) -> dict:
    db = Path.home() / ".kuzu-memory" / "user.db"
    stats = {"project": str(db), "memories": 0, "written": 0, "skipped": 0, "error": ""}
    if not db.exists():
        stats["error"] = "no user.db"
        return stats
    with tempfile.TemporaryDirectory(prefix="kuzu-migrate-") as td:
        try:
            export = export_kuzu_db(db, Path(td))
        except Exception as exc:
            stats["error"] = f"export failed: {exc}"
            return stats
    memories = export.get("memories", [])
    stats["memories"] = len(memories)
    if dry_run:
        return stats
    root = USER_DB_PROJECT
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    for mem in memories:
        filename, markdown = note_for_memory(mem)
        target = memory_dir / filename
        if target.exists():
            stats["skipped"] += 1
            continue
        target.write_text(markdown)
        stats["written"] += 1
    registered = registered_projects()
    if "user-patterns" not in registered:
        run(["bm", "project", "add", "user-patterns", str(root), "--no-wait"], timeout=120)
    run(["bm", "project", "index", "user-patterns"], timeout=300)
    return stats


def find_projects(scan_roots: list[Path]) -> list[Path]:
    found = []
    for base in scan_roots:
        if not base.exists():
            continue
        for marker in base.rglob(".kuzu-memory/memories.db"):
            found.append(marker.parent.parent)
    return sorted(set(found))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", nargs="+", type=Path, default=[])
    parser.add_argument("--all", action="store_true", help="scan ~/projects and ~/dev")
    parser.add_argument("--scan-root", nargs="+", type=Path, default=[])
    parser.add_argument("--user-db", action="store_true", help="migrate ~/.kuzu-memory/user.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite same-title notes")
    args = parser.parse_args()

    targets = list(args.projects)
    if args.all:
        targets += find_projects(DEFAULT_SCAN_ROOTS + args.scan_root)
    if not targets and not args.user_db:
        parser.error("nothing to migrate: pass --projects, --all, or --user-db")

    rows = []
    for root in targets:
        rows.append(migrate_project(root.resolve(), args.dry_run, args.force))
    if args.user_db:
        rows.append(migrate_user_db(args.dry_run))

    print(f"\n{'project':<40} {'memories':>8} {'written':>8} {'skipped':>8}  error")
    for r in rows:
        name = str(r["project"])
        print(f"{name[:40]:<40} {r['memories']:>8} {r['written']:>8} {r['skipped']:>8}  {r['error']}")
    ok = all(not r["error"] for r in rows)
    print("\nDRY RUN — nothing written." if args.dry_run else "")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
