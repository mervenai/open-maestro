"""Vendor-neutral built-in tools for SDK runtimes.

The tool definitions here are intentionally simple and synchronous/async-safe.
Each tool exposes a JSON-schema description for providers that support function
calling (OpenAI, Azure, etc.) and an async ``execute`` method that the runtime
invokes after passing any guard callback.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


@dataclass
class Tool:
    """A vendor-neutral tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[..., Coroutine[Any, Any, str]]

    def to_openai_schema(self) -> dict[str, Any]:
        """Return an OpenAI-compatible function tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
                "strict": False,
            },
        }

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Return an Anthropic-style tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class ToolRegistry:
    """Registry of available tools."""

    def __init__(self, tools: dict[str, Tool] | None = None):
        self._tools: dict[str, Tool] = tools or {}

    @classmethod
    def default(cls) -> ToolRegistry:
        """Return the default set of file-system and shell tools."""
        return cls(
            {
                "Read": _read_tool(),
                "Write": _write_tool(),
                "Bash": _bash_tool(),
                "Grep": _grep_tool(),
            }
        )

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def filter(self, allowed: set[str] | None, blocked: set[str] | None) -> list[Tool]:
        """Return tools filtered by allow-list and block-list."""
        result: list[Tool] = []
        for tool in self._tools.values():
            if allowed is not None and tool.name not in allowed:
                continue
            if blocked and tool.name in blocked:
                continue
            result.append(tool)
        return result


def _read_tool() -> Tool:
    async def execute(path: str, limit: int | None = None, offset: int | None = None) -> str:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            if offset is not None:
                lines = lines[max(0, offset - 1) :]
            if limit is not None:
                lines = lines[:limit]
            return "\n".join(lines)
        except Exception as exc:
            return f"Error reading {path}: {exc}"

    return Tool(
        name="Read",
        description="Read the contents of a text file.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path."},
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to return.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based starting line number.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def _write_tool() -> Tool:
    async def execute(path: str, content: str) -> str:
        try:
            target = Path(path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} characters to {path}."
        except Exception as exc:
            return f"Error writing {path}: {exc}"

    return Tool(
        name="Write",
        description="Write text content to a file, creating parent directories if needed.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path."},
                "content": {"type": "string", "description": "Text content to write."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def _bash_tool() -> Tool:
    async def execute(
        command: str,
        timeout: int = 60,
        cwd: str | None = None,
    ) -> str:
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                ),
                timeout=timeout,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            text = stdout.decode("utf-8", errors="replace")
            prefix = f"exit code: {proc.returncode}\n" if proc.returncode != 0 else ""
            return f"{prefix}{text}"
        except TimeoutError:
            return f"Error: command timed out after {timeout}s"
        except Exception as exc:
            return f"Error running command: {exc}"

    return Tool(
        name="Bash",
        description="Run a shell command and return its stdout/stderr combined output.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {
                    "type": "integer",
                    "description": "Maximum seconds to wait for the command.",
                    "default": 60,
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def _grep_tool() -> Tool:
    async def execute(pattern: str, path: str | None = None, glob: str = "*.py") -> str:
        try:
            if path is not None and shutil.which("rg"):
                proc = await asyncio.create_subprocess_exec(
                    "rg",
                    "--line-number",
                    "--with-filename",
                    "--color=never",
                    pattern,
                    path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                stdout, _ = await proc.communicate()
                return stdout.decode("utf-8", errors="replace")[:4000]

            search_root = Path(path).expanduser() if path else Path.cwd()
            matches: list[str] = []
            for file_path in search_root.rglob(glob):
                try:
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), start=1):
                        if pattern in line:
                            matches.append(f"{file_path}:{i}:{line}")
                            if len(matches) >= 50:
                                break
                except Exception:
                    continue
                if len(matches) >= 50:
                    break
            return "\n".join(matches) or "No matches found."
        except Exception as exc:
            return f"Error searching: {exc}"

    return Tool(
        name="Grep",
        description="Search for a pattern in files. Prefers ripgrep if available.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search. Defaults to current directory.",
                },
                "glob": {
                    "type": "string",
                    "description": "File glob to restrict the search. Defaults to '*.py'.",
                    "default": "*.py",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        execute=execute,
    )


def parse_tool_input(raw: str) -> dict[str, Any]:
    """Parse a tool call argument string as JSON, returning an empty dict on failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
