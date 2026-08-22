"""Presentable real-time streaming for CLI runtime subprocess output.

Why: Subprocess CLI output piped through ``asyncio.subprocess.PIPE`` is plain
 text, often raw JSON, and can look like an unreadable blob. This module
turns it into styled terminal output with colored prefixes, lightweight
parsing of known formats (Kimi stream-json), and Markdown/code rendering where
appropriate.
What: ``StreamPrinter`` reads lines from a subprocess stream and prints them
via Rich with consistent formatting.
Test: ``StreamPrinter`` can be exercised by feeding it sample lines.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text


class StreamPrinter:
    """Print subprocess output lines in a presentable way."""

    def __init__(self, label: str, color: str = "cyan", *, use_stderr: bool = False) -> None:
        self.label = label
        self.color = color
        self.use_stderr = use_stderr
        self._console = Console(
            stderr=use_stderr,
            soft_wrap=True,
            force_terminal=True,
        )
        self._buffer: list[str] = []

    def _emit(self, line: str, *, style: str | None = None) -> None:
        """Print a single line with a colored label prefix."""
        prefix = Text(f"[{self.label}] ", style=f"bold {self.color}")
        if style:
            prefix.append(line.rstrip("\n"), style=style)
        else:
            prefix.append(line.rstrip("\n"))
        self._console.print(prefix)

    def _try_kimi_stream_json(self, line: str) -> bool:
        """Parse Kimi stream-json and print it nicely.

        Returns True if the line was handled, False if it should be printed raw.
        """
        stripped = line.strip()
        if not stripped:
            return True  # consume empty lines silently
        try:
            msg: dict[str, Any] = json.loads(stripped)
        except json.JSONDecodeError:
            return False

        role = msg.get("role")
        if role == "assistant":
            content = msg.get("content")
            if content:
                self._emit(str(content), style="default")
            return True

        if role == "tool":
            name = msg.get("name") or msg.get("tool_name") or "tool"
            input_data = msg.get("input") or msg.get("tool_input") or {}
            self._emit(f"→ {name}: {input_data}", style="dim")
            return True

        if role == "meta":
            msg_type = msg.get("type")
            if msg_type == "session.resume_hint":
                self._emit(f"session hint: {msg.get('command')}", style="dim")
            else:
                self._emit(f"meta: {msg_type}", style="dim")
            return True

        # Unknown JSON shape: print compactly rather than as a blob.
        self._emit(json.dumps(msg, ensure_ascii=False), style="dim")
        return True

    def _try_render_markdown(self, line: str) -> bool:
        """If a line looks like Markdown, render it."""
        stripped = line.strip()
        markdown_indicators = ("# ", "## ", "- ", "* ", "```", "| ", "**", "`")
        if not any(stripped.startswith(ind) for ind in markdown_indicators):
            return False
        try:
            md = Markdown(stripped)
            prefix = Text(f"[{self.label}] ", style=f"bold {self.color}")
            self._console.print(prefix, md)
            return True
        except Exception:
            return False

    def write(self, line: str) -> None:
        """Process and print one line of subprocess output."""
        self._buffer.append(line)

        # Kimi uses stream-json; decode it for readability.
        if self.label.lower() == "kimi":
            if self._try_kimi_stream_json(line):
                return

        # Try to render obvious Markdown.
        if self._try_render_markdown(line):
            return

        # Default: plain line with label prefix.
        self._emit(line.rstrip("\n"), style="default")

    def get_buffer(self) -> str:
        """Return everything that has been written."""
        return "".join(self._buffer)

    def flush(self) -> None:
        """No-op for compatibility with file-like objects."""


def create_printer(label: str, *, use_stderr: bool = False) -> StreamPrinter:
    """Return a configured StreamPrinter for a runtime label."""
    color = "cyan"
    if label.lower() == "claude":
        color = "magenta"
    elif label.lower() == "kimi":
        color = "green"
    return StreamPrinter(label=label, color=color, use_stderr=use_stderr)
