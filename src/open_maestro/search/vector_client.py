"""Thin async wrapper around the mcp-vector-search CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

logger = logging.getLogger(__name__)


class VectorSearchClient:
    """Search code semantically via mcp-vector-search."""

    def __init__(self, project_root: str | None = None):
        self.project_root = project_root
        if not shutil.which("mcp-vector-search"):
            raise RuntimeError(
                "mcp-vector-search CLI is not installed or not on PATH"
            )

    async def search_code(
        self,
        query: str,
        *,
        limit: int = 5,
        language: str | None = None,
        files: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return semantic code search results.

        mcp-vector-search expects global options before the query, so we build
        the argument list accordingly.
        """
        args = ["mcp-vector-search", "search"]
        if self.project_root:
            args.extend(["--project-root", self.project_root])
        args.extend(["--limit", str(limit), "--json"])
        if language:
            args.extend(["--language", language])
        if files:
            args.extend(["--files", files])
        args.append(query)

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0:
            raise RuntimeError(f"mcp-vector-search failed: {stderr}")

        # Strip warning lines and try to parse JSON
        lines = [line for line in stdout.splitlines() if line.strip().startswith("[")]
        if not lines:
            return []

        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Could not parse mcp-vector-search output: {stdout[:500]}"
            ) from exc
