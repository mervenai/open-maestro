"""Thin async wrapper around the kuzu-memory CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class KuzuMemoryClient:
    """Recall and store project memories via kuzu-memory.

    The client is bound to a project root.  If the project does not yet have a
    kuzu-memory database, :meth:`ensure_initialized` will create one.
    """

    def __init__(self, project_root: str | None = None):
        self.project_root = project_root
        if not shutil.which("kuzu-memory"):
            raise RuntimeError("kuzu-memory CLI is not installed or not on PATH")

    def _base_args(self) -> list[str]:
        args = ["kuzu-memory"]
        if self.project_root:
            args.extend(["--project-root", self.project_root])
        return args

    def _db_path(self) -> Path | None:
        if not self.project_root:
            return None
        return Path(self.project_root) / ".kuzu-memory" / "memories.db"

    def is_initialized(self) -> bool:
        """Return True if the project already has a kuzu-memory database."""
        db_path = self._db_path()
        if db_path is None:
            # No explicit project root; rely on kuzu-memory's cwd auto-detection.
            return True
        return db_path.exists()

    async def ensure_initialized(self) -> bool:
        """Create a project memory database if one does not exist.

        Returns True if a database now exists (either previously or after init).
        """
        if self.is_initialized():
            return True
        try:
            await self._run([*self._base_args(), "init"])
            return self.is_initialized()
        except Exception as exc:
            logger.warning("Failed to initialize project memory: %s", exc)
            return False

    async def recall(self, prompt: str, limit: int = 5) -> list[str]:
        """Return a list of memory texts relevant to *prompt*."""
        args = [
            *self._base_args(),
            "memory",
            "recall",
            "--max-memories",
            str(limit),
            prompt,
        ]
        stdout = await self._run(args)

        # kuzu-memory recall prints either plain text lines or JSON.
        # Heuristic: try JSON first, otherwise split lines.
        stdout = stdout.strip()
        if not stdout or stdout == f"No memories found for: '{prompt}'":
            return []

        try:
            data = json.loads(stdout)
            if isinstance(data, list):
                return [str(item) for item in data]
            if isinstance(data, dict):
                return [str(data.get("content", data))]
        except json.JSONDecodeError:
            pass

        # Plain text fallback
        return [line.strip("- ") for line in stdout.splitlines() if line.strip()]

    async def enhance(self, prompt: str) -> str:
        """Return *prompt* enhanced with relevant memories."""
        args = [*self._base_args(), "memory", "enhance", prompt]
        return await self._run(args)

    async def store(
        self,
        content: str,
        memory_type: str = "note",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Store a memory synchronously."""
        args = [*self._base_args(), "memory", "store", "--source", memory_type]
        if metadata:
            if "session_id" in metadata:
                args.extend(["--session-id", str(metadata["session_id"])])
            if "agent_id" in metadata:
                args.extend(["--agent-id", str(metadata["agent_id"])])
            args.extend(["--metadata", json.dumps(metadata)])
        args.append(content)
        return (await self._run(args)).strip() or None

    async def _run(self, args: list[str]) -> str:
        kwargs: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if self.project_root:
            kwargs["cwd"] = self.project_root
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise RuntimeError(f"kuzu-memory failed: {stderr}")

        # Strip common CLI decoration/warnings
        return stdout.strip()
