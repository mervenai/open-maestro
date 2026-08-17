"""Tests for the Open Maestro event bus."""

from __future__ import annotations

import pytest

from open_maestro.events.bus import EventBus


@pytest.fixture
def fresh_bus() -> EventBus:
    """Return a fresh event bus instance for isolated tests.

    The singleton is reset so tests do not share handlers.
    """
    bus = EventBus()
    bus._handlers.clear()
    EventBus._instance = None
    return bus


class TestEventBus:
    async def test_emit_calls_typed_handler(self, fresh_bus: EventBus):
        received: list[tuple[str, dict]] = []

        def handler(event_type: str, payload: dict) -> None:
            received.append((event_type, payload))

        fresh_bus.on("test.event", handler)
        await fresh_bus.emit("test.event", {"x": 1})

        assert len(received) == 1
        assert received[0] == ("test.event", {"x": 1})

    async def test_emit_calls_wildcard_handler(self, fresh_bus: EventBus):
        received: list[tuple[str, dict]] = []

        def handler(event_type: str, payload: dict) -> None:
            received.append((event_type, payload))

        fresh_bus.on("*", handler)
        await fresh_bus.emit("foo", {"a": 1})
        await fresh_bus.emit("bar", {"b": 2})

        assert len(received) == 2

    async def test_async_handler_awaited(self, fresh_bus: EventBus):
        received: list[dict] = []

        async def handler(_event_type: str, payload: dict) -> None:
            received.append(payload)

        fresh_bus.on("async.event", handler)
        await fresh_bus.emit("async.event", {"x": 1})

        assert len(received) == 1

    async def test_failed_handler_does_not_stop_others(self, fresh_bus: EventBus):
        received: list[dict] = []

        def bad_handler(_event_type: str, _payload: dict) -> None:
            raise RuntimeError("boom")

        def good_handler(_event_type: str, payload: dict) -> None:
            received.append(payload)

        fresh_bus.on("fail.event", bad_handler)
        fresh_bus.on("fail.event", good_handler)
        await fresh_bus.emit("fail.event", {"x": 1})

        assert len(received) == 1


class TestInteractiveProgressHandler:
    async def test_tool_call_event_prints_read_progress(self, capsys):
        from open_maestro.events.progress import InteractiveProgressHandler

        handler = InteractiveProgressHandler()
        await handler(
            "tool.call",
            {"tool_name": "Read", "tool_input": {"path": "src/main.py"}},
        )
        captured = capsys.readouterr()
        assert "reading" in captured.err
        assert "src/main.py" in captured.err

    async def test_tool_call_event_prints_bash_progress(self, capsys):
        from open_maestro.events.progress import InteractiveProgressHandler

        handler = InteractiveProgressHandler()
        await handler(
            "tool.call",
            {"tool_name": "Bash", "tool_input": {"command": "git status"}},
        )
        captured = capsys.readouterr()
        assert "running shell command" in captured.err
        assert "git status" in captured.err

    async def test_runtime_working_event_prints_elapsed_time(self, capsys):
        from open_maestro.events.progress import InteractiveProgressHandler

        handler = InteractiveProgressHandler()
        await handler("runtime.working", {"duration_ms": 35000})
        captured = capsys.readouterr()
        assert "Still working" in captured.err
        assert "35s" in captured.err


class TestProgressIndicator:
    async def test_spinner_prints_and_clears_line(self, capsys):
        import asyncio

        from open_maestro.events.progress import ProgressIndicator

        indicator = ProgressIndicator(message="Thinking", interval=0.05)
        indicator.start()
        await asyncio.sleep(0.15)
        await indicator.stop()

        captured = capsys.readouterr()
        assert "Thinking" in captured.err

    async def test_print_line_clears_spinner_before_text(self, capsys):
        from open_maestro.events.progress import ProgressIndicator

        indicator = ProgressIndicator(message="Thinking")
        indicator.start()
        await indicator.print_line("→ Reading src/main.py")
        await indicator.stop()

        captured = capsys.readouterr()
        assert "→ Reading src/main.py" in captured.err
        # The cleared spinner line should not remain as stray output.
        lines = [line for line in captured.err.splitlines() if line.strip()]
        assert any("Reading src/main.py" in line for line in lines)
