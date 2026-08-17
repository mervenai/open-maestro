"""Streaming event handlers for live observability.

Handlers subscribe to the Open Maestro event bus and forward events to a
file-like output stream.  Two formats are supported:

* ``json``  — one JSON object per line (machine-readable).
* ``text``  — concise human-readable lines.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TextIO


@dataclass
class StreamingHandler:
    """Print events to a stream as they arrive.

    The handler is intentionally simple so it can be attached to a terminal,
    a file, or an in-memory buffer for tests.
    """

    stream: TextIO = sys.stderr
    format: str = "json"
    include_timestamp: bool = True

    def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.format == "json":
            self._emit_json(event_type, payload)
        else:
            self._emit_text(event_type, payload)

    def _emit_json(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {"event": event_type, **payload}
        if self.include_timestamp:
            record["ts"] = datetime.now(UTC).isoformat()
        self.stream.write(json.dumps(record, default=str) + "\n")
        self.stream.flush()

    def _emit_text(self, event_type: str, payload: dict[str, Any]) -> None:
        prefix = ""
        if self.include_timestamp:
            prefix = datetime.now(UTC).strftime("%H:%M:%S") + " "
        message = _format_event_text(event_type, payload)
        self.stream.write(f"{prefix}[{event_type}] {message}\n")
        self.stream.flush()


def _format_event_text(event_type: str, payload: dict[str, Any]) -> str:
    """Return a short human-readable summary for an event."""
    if event_type == "task.received":
        return payload.get("prompt", "")
    if event_type == "agent.selected":
        return f"{payload.get('agent_id')} ({payload.get('role')})"
    if event_type == "runtime.started":
        return (
            f"runtime={payload.get('runtime')} "
            f"model={payload.get('model')}"
        )
    if event_type == "runtime.completed":
        return (
            f"runtime={payload.get('runtime')} "
            f"session={payload.get('session_id')} "
            f"error={payload.get('is_error')}"
        )
    if event_type == "tool.call":
        name = payload.get("tool_name", "unknown")
        input_summary = _summarize(payload.get("tool_input"))
        return f"{name} {input_summary}"
    if event_type == "tool.result":
        name = payload.get("tool_name", "unknown")
        allowed = payload.get("allowed")
        if allowed is not None:
            return f"{name} allowed={allowed}"
        return f"{name} result received"
    if event_type == "memory.recalled":
        return f"recalled {payload.get('count', 0)} memories"
    if event_type == "search.completed":
        return f"found {payload.get('count', 0)} results"
    if event_type == "context.threshold":
        return (
            f"{payload.get('threshold')} "
            f"tokens={payload.get('tokens_used')}"
        )
    if event_type == "session.saved":
        return f"session={payload.get('session_id')}"
    return json.dumps(payload, default=str)


def _summarize(value: Any, max_len: int = 80) -> str:
    if value is None:
        return ""
    text = json.dumps(value, default=str)
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text
