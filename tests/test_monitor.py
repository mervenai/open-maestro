"""Tests for the live activity monitor.

Why: The monitor is a new observability surface; these tests prove that event
handlers update state correctly and the renderer produces expected output.
What: Tests ``MonitorState``, ``MonitorEventHandler``, and ``render``.
Test: ``uv run pytest tests/test_monitor.py``
"""

from __future__ import annotations

import pytest

from open_maestro.events.bus import EventBus
from open_maestro.events.monitor import MonitorEventHandler
from open_maestro.monitor.renderer import render
from open_maestro.monitor.state import MonitorState


@pytest.fixture()
def state() -> MonitorState:
    """Return a fresh monitor state."""
    return MonitorState()


def test_state_updates_agent(state: MonitorState) -> None:
    """agent.selected updates agent_id and agent_role."""
    state.update("agent.selected", {"agent_id": "researcher", "role": "Research"})
    assert state.agent_id == "researcher"
    assert state.agent_role == "Research"


def test_state_updates_runtime(state: MonitorState) -> None:
    """runtime.started updates runtime and model."""
    state.update(
        "runtime.started",
        {"runtime": "kimi-cli", "model": "kimi-code/k3", "agent_id": "researcher"},
    )
    assert state.runtime == "kimi-cli"
    assert state.model == "kimi-code/k3"


def test_state_updates_task_and_turn(state: MonitorState) -> None:
    """task.received updates prompt, status, and turn."""
    state.update(
        "task.received",
        {"prompt": "analyze the codebase", "agent_id": "researcher", "turn": 3},
    )
    assert state.status == "running"
    assert state.prompt == "analyze the codebase"
    assert state.turn == 3


def test_state_records_tool_call(state: MonitorState) -> None:
    """tool.call sets active_tool and tool.result clears it."""
    state.update("tool.call", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert state.status == "tool_call"
    assert state.active_tool == "Bash"

    state.update("tool.result", {"tool_name": "Bash", "allowed": True})
    assert state.active_tool is None


def test_state_updates_context_threshold(state: MonitorState) -> None:
    """context.threshold records threshold and tokens."""
    state.update("context.threshold", {"threshold": "warning", "tokens_used": 12000})
    assert state.context_threshold == "warning"
    assert state.tokens_used == 12000


def test_state_updates_working(state: MonitorState) -> None:
    """runtime.working sets status to working."""
    state.update("runtime.working", {"duration_ms": 12345})
    assert state.status == "working"


def test_state_truncates_recent_events(state: MonitorState) -> None:
    """Only the last 50 events are kept."""
    for i in range(60):
        state.update("tool.call", {"tool_name": f"tool-{i}"})
    assert len(state.recent_events) == 50
    assert state.recent_events[0]["payload"]["tool_name"] == "tool-10"


def test_handler_updates_state_via_event_bus() -> None:
    """MonitorEventHandler attached to the event bus updates shared state."""
    # Reset the singleton so this test is isolated from others.
    EventBus._instance = None  # type: ignore[misc]
    bus = EventBus()
    state = MonitorState()
    handler = MonitorEventHandler(state)
    bus.on("*", handler)

    async def _emit() -> None:
        await bus.emit("agent.selected", {"agent_id": "coder", "role": "Code changes"})

    import asyncio

    asyncio.run(_emit())
    assert state.agent_id == "coder"


def test_render_contains_state_values(state: MonitorState) -> None:
    """The Rich renderer embeds current state values."""
    from rich.console import Console

    state.runtime = "kimi-cli"
    state.model = "kimi-code/k3"
    state.agent_id = "researcher"
    state.status = "running"
    state.prompt = "summarize"

    panel = render(state)
    console = Console(force_terminal=False, width=120)
    with console.capture() as capture:
        console.print(panel)
    plain = capture.get()
    assert "kimi-cli" in plain
    assert "kimi-code/k3" in plain
    assert "researcher" in plain
    assert "summarize" in plain
