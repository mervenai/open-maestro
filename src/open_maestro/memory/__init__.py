"""Memory integration for project context.

Two backends implement the same surface — ``recall``/``enhance``/
``store``/``ensure_initialized``:

- :class:`BasicMemoryClient` (preferred) — Basic Memory (``bm`` CLI);
  memories live as Markdown notes in ``<project>/memory/``.
- :class:`KuzuMemoryClient` (legacy) — the discontinued kuzu-memory CLI;
  kept so existing installs keep working until migration.

:func:`create_memory_client` picks a backend: ``MAESTRO_MEMORY_BACKEND``
(``basic-memory`` | ``kuzu`` | ``none``) overrides, otherwise ``bm`` wins
when installed, falling back to ``kuzu-memory``.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Protocol, runtime_checkable

from open_maestro.memory.basic_memory_client import BasicMemoryClient
from open_maestro.memory.kuzu_client import KuzuMemoryClient

__all__ = [
    "BasicMemoryClient",
    "KuzuMemoryClient",
    "MemoryClient",
    "create_memory_client",
]

logger = logging.getLogger(__name__)


@runtime_checkable
class MemoryClient(Protocol):
    """The backend-agnostic surface maestro uses."""

    def is_initialized(self) -> bool: ...

    async def ensure_initialized(self) -> bool: ...

    async def recall(self, prompt: str, limit: int = 5) -> list[str]: ...

    async def enhance(self, prompt: str) -> str: ...

    async def store(
        self,
        content: str,
        memory_type: str = "note",
        metadata: dict[str, Any] | None = None,
    ) -> str | None: ...


def create_memory_client(project_root: str | None = None) -> MemoryClient | None:
    """Return a project memory client for the best available backend.

    Returns None when memory is disabled (``MAESTRO_MEMORY_BACKEND=none``)
    or no backend is installed.
    """
    backend = os.environ.get("MAESTRO_MEMORY_BACKEND", "").strip().lower()
    if backend == "none":
        return None
    if backend == "basic-memory":
        return BasicMemoryClient(project_root)
    if backend == "kuzu":
        return KuzuMemoryClient(project_root)
    if shutil.which("bm"):
        return BasicMemoryClient(project_root)
    if shutil.which("kuzu-memory"):
        return KuzuMemoryClient(project_root)
    return None
