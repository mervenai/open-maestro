#!/usr/bin/env python3
"""Spot-check bm recall against kuzu recall for migrated projects.

Runs the same query through both backends and reports overlap: bm is healthy
for a project when its top results cover what kuzu recalled for the same
query. kuzu runs against a temp copy of the DB (the live DB is lock-held by
stale MCP servers).

Usage: python3 scripts/spotcheck_memory.py <project_root> [query ...]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

QUERIES_DEFAULT = [
    "bundle kickoff rulings",
    "ephemeral dev environment",
    "design decision D-9 authorization",
    "merge discipline standing branch",
    "gateway CustomerIdHeader",
]


def run(cmd: list[str], timeout: int = 120, cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def kuzu_recall(root: Path, query: str, limit: int = 5) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="kuzu-check-") as td:
        copy = Path(td) / "db"
        shutil.copytree(root / ".kuzu-memory", copy, ignore=shutil.ignore_patterns("*.wal"))
        out = run(
            ["kuzu-memory", "--db-path", str(copy / "memories.db"),
             "memory", "recall", "--max-memories", str(limit), query]
        )
    lines = [line.strip().lstrip("- ") for line in out.splitlines() if line.strip()]
    return [line for line in lines if not line.startswith("No memories")][:limit]


def bm_recall(root: Path, query: str, limit: int = 5) -> list[str]:
    projects = json.loads(run(["bm", "project", "list", "--local", "--json"]))
    name = next(
        (p["name"] for p in projects.get("projects", [])
         if p.get("local_path") and Path(p["local_path"]).expanduser().resolve() == root.resolve()),
        None,
    )
    if name is None:
        return ["<project not registered>"]
    out = run(
        ["bm", "tool", "search", query, "--project", name,
         "--page-size", str(limit), "--json"]
    )
    data = json.loads(out)
    return [
        (item.get("matched_chunk") or item.get("content") or "").strip().replace("\n", " ")[:120]
        for item in (data.get("results") or [])[:limit]
    ]


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    queries = sys.argv[2:] or QUERIES_DEFAULT
    print(f"# {root.name}\n")
    for query in queries:
        print(f"## {query}")
        kz = kuzu_recall(root, query)
        bm = bm_recall(root, query)
        print(f"  kuzu ({len(kz)}):")
        for item in kz:
            print(f"    K {item[:110]}")
        print(f"  bm   ({len(bm)}):")
        for item in bm:
            print(f"    B {item[:110]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
