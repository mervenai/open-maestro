"""Tests for streaming event handlers."""

from __future__ import annotations

import io
import json

import pytest

from open_maestro.events.bus import EventBus
from open_maestro.events.stream import StreamingHandler


@pytest.fixture
def fresh_bus() -> EventBus:
    """Return a fresh event bus instance for isolated tests."""
    bus = EventBus()
    bus._handlers.clear()
    EventBus._instance = None
    return bus


class TestStreamingHandler:
    def test_json_format_emits_one_line_per_event(self, fresh_bus):
        stream = io.StringIO()
        fresh_bus.on("*", StreamingHandler(stream=stream, format="json"))

        import asyncio

        asyncio.run(fresh_bus.emit("agent.selected", {"agent_id": "engineer"}))
        asyncio.run(fresh_bus.emit("tool.call", {"tool_name": "Read"}))

        lines = [line for line in stream.getvalue().splitlines() if line]
        assert len(lines) == 2
        record = json.loads(lines[0])
        assert record["event"] == "agent.selected"
        assert record["agent_id"] == "engineer"
        assert "ts" in record

    def test_text_format_emits_human_readable_lines(self, fresh_bus):
        stream = io.StringIO()
        fresh_bus.on("*", StreamingHandler(stream=stream, format="text"))

        import asyncio

        asyncio.run(fresh_bus.emit("agent.selected", {"agent_id": "engineer", "role": "engineer"}))

        output = stream.getvalue()
        assert "[agent.selected]" in output
        assert "engineer" in output

    def test_timestamp_can_be_disabled(self, fresh_bus):
        stream = io.StringIO()
        fresh_bus.on(
            "*",
            StreamingHandler(
                stream=stream, format="json", include_timestamp=False
            ),
        )

        import asyncio

        asyncio.run(fresh_bus.emit("task.received", {"prompt": "hello"}))

        record = json.loads(stream.getvalue().strip())
        assert "ts" not in record
        assert record["event"] == "task.received"

    def test_failed_stream_handler_is_isolated(self, fresh_bus):
        stream = io.StringIO()

        def bad_handler(_event_type: str, _payload: dict) -> None:
            raise RuntimeError("boom")

        fresh_bus.on("test", bad_handler)
        fresh_bus.on("test", StreamingHandler(stream=stream, format="json"))

        import asyncio

        asyncio.run(fresh_bus.emit("test", {"x": 1}))

        record = json.loads(stream.getvalue().strip())
        assert record["x"] == 1

    def test_text_format_summarizes_tool_call(self, fresh_bus):
        stream = io.StringIO()
        fresh_bus.on("*", StreamingHandler(stream=stream, format="text"))

        import asyncio

        asyncio.run(
            fresh_bus.emit(
                "tool.call",
                {"tool_name": "Read", "tool_input": {"path": "src/main.py"}},
            )
        )

        output = stream.getvalue()
        assert "[tool.call]" in output
        assert "Read" in output
        assert "src/main.py" in output
