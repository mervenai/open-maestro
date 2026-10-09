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

import asyncio
import json
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

# Claude stream-json event types that are internal noise and swallowed.
_CLAUDE_SILENT_EVENTS = {
    "rate_limit_event",
    "file_history_snapshot",
    "attachment",
    "queue_operation",
}

# Tool-input keys worth showing, most specific first.
_TOOL_INPUT_KEYS = (
    "file_path",
    "path",
    "file",
    "command",
    "pattern",
    "glob",
    "query",
    "prompt",
    "url",
)


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    """Normalize a message content field to a list of content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _summarize_tool_input(tool_input: Any, limit: int = 100) -> str:
    """One-line summary of a tool_use input (file path, command, ...)."""
    if not isinstance(tool_input, dict):
        return str(tool_input)[:limit]
    for key in _TOOL_INPUT_KEYS:
        value = tool_input.get(key)
        if value:
            return f"{key}={str(value).splitlines()[0][:limit]}"
    return json.dumps(tool_input, ensure_ascii=False)[:limit]


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

    def _try_claude_stream_json(self, line: str) -> bool:
        """Parse Claude stream-json (NDJSON) events and print concise progress.

        Claude's plain ``json`` output format stays silent until the run ends
        and then dumps one blob; ``stream-json`` emits these events as they
        happen, which is what makes file edits and tool calls visible live.

        Returns True if the line was handled, False if it should be printed raw.
        The raw line is still buffered by ``write`` regardless, so the final
        ``{"type": "result"}`` event remains available for result parsing.
        """
        stripped = line.strip()
        if not stripped:
            return True  # consume empty lines silently
        try:
            event: dict[str, Any] = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        if not isinstance(event, dict):
            return False

        etype = event.get("type")
        if etype == "assistant":
            for block in _content_blocks((event.get("message") or {}).get("content")):
                btype = block.get("type")
                if btype == "text":
                    if str(block.get("text", "")).strip():
                        self._emit(str(block.get("text", "")), style="default")
                elif btype == "tool_use":
                    name = block.get("name") or "tool"
                    self._emit(
                        f"→ {name}: {_summarize_tool_input(block.get('input'))}",
                        style="dim",
                    )
            return True

        if etype == "user":
            for block in _content_blocks((event.get("message") or {}).get("content")):
                if block.get("type") != "tool_result" or not block.get("is_error"):
                    continue
                # Permission denials and failed tools surface here; they are
                # the difference between "still working" and "blocked".
                detail = block.get("content")
                if isinstance(detail, list):
                    detail = " ".join(
                        str(b.get("text", "")) for b in detail if isinstance(b, dict)
                    )
                self._emit(f"← tool_result ERROR: {str(detail)[:160]}", style="bold red")
            return True

        if etype == "system":
            if event.get("subtype") == "init":
                self._emit(
                    f"session {event.get('session_id')} model {event.get('model')}",
                    style="dim",
                )
            # Other system subtypes (thinking_tokens, hook responses, ...)
            # are internal noise.
            return True

        if etype == "result":
            cost = event.get("total_cost_usd")
            cost_s = f"${cost:.2f}" if isinstance(cost, (int, float)) else "?"
            self._emit(f"done: {event.get('num_turns')} turn(s), {cost_s}", style="dim")
            return True

        if etype in _CLAUDE_SILENT_EVENTS:
            return True

        # Unknown event type: print compactly rather than as a blob.
        self._emit(json.dumps(event, ensure_ascii=False), style="dim")
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

        # Kimi and Claude use stream-json; decode it for readability.
        if self.label.lower() == "kimi":
            if self._try_kimi_stream_json(line):
                return
        if self.label.lower() == "claude":
            if self._try_claude_stream_json(line):
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


async def pump_stream(stream: asyncio.StreamReader | None, printer: Any) -> None:
    """Relay a subprocess stream to a printer, line by line.

    Reads fixed-size chunks and splits on newlines ourselves instead of using
    ``StreamReader.readline()``, which enforces asyncio's 64 KiB per-line
    limit and raises ``LimitOverrunError`` ("Separator is found, but chunk is
    longer than limit") when the subprocess emits a longer single line. The
    CLI runtimes do exactly that: stream-json tool calls can carry a whole
    file's content as one line.
    """
    if stream is None:
        return
    pending = b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            printer.write(line.decode(errors="replace") + "\n")
    if pending:
        printer.write(pending.decode(errors="replace"))
