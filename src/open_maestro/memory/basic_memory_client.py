"""Thin async wrapper around the Basic Memory CLI (``bm``).

Basic Memory stores knowledge as plain Markdown notes inside the project
directory (``memory/`` by convention) with a local SQLite index — project
memory stays human-readable, git-committable, and independent of any single
vendor's database format.  This client implements the same surface maestro
previously sourced from kuzu-memory (``recall``/``enhance``/``store``), so
the rest of the codebase is backend-agnostic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NOTES_FOLDER = "memory"
_SEARCH_TIMEOUT = 60.0
_WRITE_TIMEOUT = 45.0
_PROJECT_TIMEOUT = 180.0

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, max_len: int = 48) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "memory"


class BasicMemoryClient:
    """Recall and store project memories via Basic Memory.

    The client is bound to a project root directory.  The project is
    registered with ``bm`` on first use (``ensure_initialized``); memory
    notes land in ``<project_root>/memory/``.
    """

    def __init__(self, project_root: str | None = None):
        self.project_root = project_root
        if not shutil.which("bm"):
            raise RuntimeError("Basic Memory CLI ('bm') is not installed or not on PATH")
        self._project_name: str | None = None

    # -- project registration ---------------------------------------------

    def _root_path(self) -> Path | None:
        return Path(self.project_root).expanduser().resolve() if self.project_root else None

    async def _registered_projects(self) -> dict[str, str]:
        """Return {project_name: resolved local path} for local projects."""
        stdout = await self._run(
            ["bm", "project", "list", "--local", "--json"],
            timeout=_PROJECT_TIMEOUT,
        )
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return {}
        projects: dict[str, str] = {}
        for item in data.get("projects", []):
            path = item.get("local_path") or ""
            if path:
                projects[item["name"]] = str(Path(path).expanduser().resolve())
        return projects

    async def _resolve_project_name(self, register: bool) -> str | None:
        if self._project_name:
            return self._project_name
        root = self._root_path()
        if root is None:
            return None
        projects = await self._registered_projects()
        root_str = str(root)
        for name, path in projects.items():
            if path == root_str:
                self._project_name = name
                return name
        if not register:
            return None
        base = _slug(root.name)
        name = base
        if name in projects:  # same name bound to a different directory
            name = f"{base}-{hashlib.sha1(str(root).encode()).hexdigest()[:8]}"
        await self._run(
            ["bm", "project", "add", name, str(root), "--no-wait"],
            timeout=_PROJECT_TIMEOUT,
        )
        self._project_name = name
        return name

    def is_initialized(self) -> bool:
        """True when the project is registered and has a memory directory.

        Synchronous by necessity (matches the legacy client contract used by
        the MCP server, which may run inside an event loop); uses a blocking
        subprocess, so keep it off hot paths.
        """
        try:
            root = self._root_path()
            if root is None or not (root / NOTES_FOLDER).is_dir():
                return False
            proc = subprocess.run(
                ["bm", "project", "list", "--local", "--json"],
                capture_output=True,
                timeout=30,
            )
            data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
            for item in data.get("projects", []):
                path = item.get("local_path") or ""
                if path and Path(path).expanduser().resolve() == root:
                    self._project_name = item["name"]
                    return True
            return False
        except Exception as exc:  # never break callers on a memory check
            logger.debug("basic-memory is_initialized failed: %s", exc)
            return False

    async def ensure_initialized(self) -> bool:
        """Register the project with bm and create its memory directory."""
        try:
            root = self._root_path()
            if root is not None:
                (root / NOTES_FOLDER).mkdir(parents=True, exist_ok=True)
            name = await self._resolve_project_name(register=True)
            return name is not None
        except Exception as exc:
            logger.warning("Failed to initialize project memory: %s", exc)
            return False

    # -- memory operations --------------------------------------------------

    async def recall(self, prompt: str, limit: int = 5) -> list[str]:
        """Return memory texts most relevant to *prompt*."""
        name = await self._resolve_project_name(register=False)
        if name is None:
            return []
        stdout = await self._run(
            [
                "bm", "tool", "search", prompt,
                "--project", name,
                "--page-size", str(limit),
                "--json",
            ],
            timeout=_SEARCH_TIMEOUT,
        )
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        results = data.get("results") or []
        memories: list[str] = []
        for item in results[:limit]:
            text = (item.get("matched_chunk") or item.get("content") or "").strip()
            if text:
                memories.append(text)
        return memories

    async def enhance(self, prompt: str) -> str:
        """Return *prompt* prefixed with relevant project memories."""
        memories = await self.recall(prompt)
        if not memories:
            return prompt
        block = "\n".join(f"- {text}" for text in memories)
        return f"## Relevant project memories\n{block}\n\n{prompt}"

    async def store(
        self,
        content: str,
        memory_type: str = "note",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Store a memory note; idempotent on identical content."""
        name = await self._resolve_project_name(register=True)
        if name is None:
            return None
        content_hash = hashlib.sha256(content.lower().strip().encode()).hexdigest()
        meta = dict(metadata or {})
        meta["memory_hash"] = content_hash
        meta.setdefault("source", "maestro")
        title = self._title_for(content)
        try:
            return await self._write(name, title, content, memory_type, meta)
        except RuntimeError as exc:
            if "already exists" not in str(exc).lower():
                raise
        # Same title, different content (or a repeat store): dedupe on hash.
        existing = await self._run(
            [
                "bm", "find", "--meta", f"memory_hash={content_hash}",
                "--project", name, "--json",
            ],
            timeout=_SEARCH_TIMEOUT,
        )
        try:
            hits = json.loads(existing).get("results") or json.loads(existing).get("files") or []
        except json.JSONDecodeError:
            hits = []
        if hits:
            return None  # identical memory already stored
        suffix = hashlib.sha1(content.encode()).hexdigest()[:8]
        return await self._write(
            name, f"{title[:40]}-{suffix}", content, memory_type, meta
        )

    def _title_for(self, content: str) -> str:
        first_line = next(
            (line.strip() for line in content.splitlines() if line.strip()),
            "memory",
        )
        first_line = first_line.lstrip("#").strip()
        return _slug(first_line, max_len=60)

    async def _write(
        self,
        project: str,
        title: str,
        content: str,
        memory_type: str,
        meta: dict[str, Any],
    ) -> str:
        args = [
            "bm", "tool", "write",
            "--title", title,
            "--directory", NOTES_FOLDER,
            "--note-type", _slug(memory_type, max_len=20) or "note",
            "--metadata", json.dumps(meta),
            "--content", content,
            "--project", project,
            "--json",
        ]
        return (await self._run(args, timeout=_WRITE_TIMEOUT)).strip() or None

    # -- subprocess ---------------------------------------------------------

    async def _run(self, args: list[str], timeout: float) -> str:
        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        root = self._root_path()
        if root is not None:
            kwargs["cwd"] = str(root)
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"bm timed out after {timeout:.0f}s: {' '.join(args[:4])}")
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise RuntimeError(f"bm failed: {stderr.strip() or stdout.strip()}")
        return stdout.strip()
