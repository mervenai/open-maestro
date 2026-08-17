"""Shared helpers for SDK-based runtime adapters.

SDK runtimes (Claude Agent SDK, Kimi ACP) run agents programmatically instead
of spawning a CLI subprocess.  This module provides common scaffolding for
optional SDK imports and tool-guard wrapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


def _noop_tool_guard(_tool_name: str, _tool_input: dict[str, Any]) -> bool:
    """Default tool guard that allows every tool call."""
    return True


async def _async_tool_guard(
    guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]],
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    """Await an async tool guard safely."""
    return await guard(tool_name, tool_input)
