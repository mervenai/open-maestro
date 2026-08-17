"""Console progress indicator for long-running orchestration steps."""

from __future__ import annotations

import asyncio
import sys
from typing import Any


class InteractiveProgressHandler:
    """Print concise, high-level progress messages during interactive mode.

    The handler subscribes to orchestration events and writes short status
    lines to stderr so the user knows what is happening while the LLM call is
    in flight.  It intentionally avoids verbose internal details.
    """

    _TOOL_NAME_ALIASES: dict[str, str] = {
        "Read": "reading",
        "Edit": "editing",
        "Write": "writing",
        "Bash": "running shell command",
        "Grep": "searching code",
        "Glob": "listing files",
        "Agent": "delegating to sub-agent",
    }

    def __init__(
        self,
        file: Any | None = None,
        indicator: ProgressIndicator | None = None,
    ) -> None:
        self.file = file or sys.stderr
        self._indicator = indicator
        self._printed: set[str] = set()
        self._active_tool_calls: set[str] = set()

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        message = self._message_for(event_type, payload)
        if message and message not in self._printed:
            self._printed.add(message)
            if self._indicator is not None:
                await self._indicator.print_line(message)
            else:
                print(message, file=self.file, flush=True)

    def _message_for(
        self, event_type: str, payload: dict[str, Any]
    ) -> str | None:
        if event_type == "memory.recalled":
            count = payload.get("count", 0)
            if count:
                return f"→ Recalled {count} relevant memory/ies"
            return None

        if event_type == "search.completed":
            count = payload.get("count", 0)
            return f"→ Searched codebase ({count} result/s)"

        if event_type == "agent.selected":
            agent_id = payload.get("agent_id")
            return f"→ Selected '{agent_id}' agent"

        if event_type == "runtime.started":
            agent_id = payload.get("agent_id")
            runtime = payload.get("runtime")
            return f"→ Delegating to '{agent_id}' via {runtime}"

        if event_type == "runtime.working":
            duration_ms = payload.get("duration_ms")
            if duration_ms is not None:
                seconds = int(duration_ms / 1000)
                return f"→ Still working... ({seconds}s elapsed)"
            return "→ Still working..."

        if event_type == "tool.call":
            tool_name = payload.get("tool_name", "tool")
            tool_input = payload.get("tool_input") or {}
            verb = self._tool_verb(tool_name)
            detail = self._tool_detail(tool_name, tool_input)
            if detail:
                return f"→ {verb}: {detail}"
            return f"→ {verb}"

        if event_type == "tool.result":
            tool_name = payload.get("tool_name", "tool")
            allowed = payload.get("allowed", True)
            if not allowed:
                return f"→ Blocked {tool_name}"
            return None

        return None

    def _tool_verb(self, tool_name: str) -> str:
        return self._TOOL_NAME_ALIASES.get(tool_name, f"using {tool_name}")

    def _tool_detail(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        if tool_name in {"Read", "Edit", "Write"}:
            path = tool_input.get("path") or tool_input.get("file_path")
            if path:
                return str(path)
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if command:
                # Truncate long commands.
                first_line = command.splitlines()[0]
                if len(first_line) > 60:
                    first_line = first_line[:57] + "..."
                return first_line
        if tool_name == "Grep":
            pattern = tool_input.get("pattern")
            if pattern:
                return f"'{pattern}'"
        if tool_name == "Glob":
            pattern = tool_input.get("pattern")
            if pattern:
                return f"'{pattern}'"
        return None


class ProgressIndicator:
    """A terminal spinner that coordinates with other stderr output.

    Runs an asyncio task that prints a spinner on one line.  Callers can
    print lines safely while the spinner is active; the spinner will resume
    on the next line.
    """

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    _LINE_WIDTH = 80

    def __init__(
        self,
        message: str = "Working",
        file: Any | None = None,
        interval: float = 0.5,
    ) -> None:
        self._message = message
        self._file = file or sys.stderr
        self._interval = interval
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        """Start the spinner task."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the spinner and clear the line."""
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        async with self._lock:
            self._clear_line()

    def set_message(self, message: str) -> None:
        """Update the spinner message."""
        self._message = message

    async def print_line(self, text: str) -> None:
        """Print a line of text, clearing the spinner line first."""
        async with self._lock:
            self._clear_line()
            print(text, file=self._file, flush=True)

    async def _loop(self) -> None:
        idx = 0
        while not self._stop_event.is_set():
            async with self._lock:
                self._render(self._FRAMES[idx])
            idx = (idx + 1) % len(self._FRAMES)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._interval
                )
            except TimeoutError:
                pass

    def _render(self, frame: str) -> None:
        line = f"{frame} {self._message}"
        padding = max(0, self._LINE_WIDTH - len(line))
        print(f"\r{line}{' ' * padding}", end="", file=self._file, flush=True)

    def _clear_line(self) -> None:
        print("\r" + " " * self._LINE_WIDTH + "\r", end="", file=self._file, flush=True)
