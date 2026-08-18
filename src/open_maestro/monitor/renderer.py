"""Rich-based live renderer for the Maestro activity monitor.

Why: Rich gives us a compact, auto-refreshing terminal UI without adding a
heavy TUI framework. It is already useful for progress indicators elsewhere in
Maestro.
What: ``MonitorRenderer`` takes a ``MonitorState`` and returns a Rich ``Panel``
containing the current agent, runtime/model, task, context pressure, recent
events, and active tool.
Test: ``render(state)`` produces a Rich renderable with the agent id in text.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from open_maestro.monitor.state import MonitorState


def _truncate(value: str | None, max_len: int = 120) -> str:
    """Return a truncated string with an ellipsis if too long."""
    if value is None:
        return "—"
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _format_event(event: dict[str, Any]) -> Text:
    """Format one recent event as a colored Rich Text line."""
    event_type = event.get("event", "unknown")
    ts = event.get("ts", "")
    payload = event.get("payload", {})

    message = ""
    if event_type == "agent.selected":
        message = f"{payload.get('agent_id')} ({payload.get('role')})"
    elif event_type == "runtime.started":
        message = f"runtime={payload.get('runtime')} model={payload.get('model')}"
    elif event_type == "tool.call":
        message = f"tool={payload.get('tool_name')}"
    elif event_type == "tool.result":
        allowed = payload.get("allowed")
        suffix = f" allowed={allowed}" if allowed is not None else ""
        message = f"tool={payload.get('tool_name', 'unknown')} result{suffix}"
    elif event_type == "memory.recalled":
        message = f"recalled {payload.get('count', 0)} memories"
    elif event_type == "search.completed":
        message = f"found {payload.get('count', 0)} results"
    elif event_type == "context.threshold":
        message = f"{payload.get('threshold')} tokens={payload.get('tokens_used')}"
    elif event_type == "runtime.completed":
        message = f"error={payload.get('is_error')}"
    elif event_type == "chain.step_started":
        message = f"step {payload.get('step')}/{payload.get('total')} {payload.get('agent_id')}"
    elif event_type == "chain.step_completed":
        message = f"step {payload.get('step')}/{payload.get('total')} error={payload.get('is_error')}"
    else:
        message = str(payload)[:80]

    color = {
        "task.received": "cyan",
        "agent.selected": "green",
        "runtime.started": "blue",
        "runtime.completed": "blue",
        "tool.call": "yellow",
        "tool.result": "yellow",
        "memory.recalled": "magenta",
        "search.completed": "magenta",
        "context.threshold": "red",
        "session.saved": "dim",
    }.get(event_type, "white")

    return Text.assemble(
        (f"{ts} ", "dim"),
        (f"[{event_type}]", f"bold {color}"),
        (f" {message}", ""),
    )


def render(state: MonitorState) -> Panel:
    """Render the current monitor state as a Rich Panel.

    Why: Keeps the layout logic isolated from the state machine and event bus.
    What: Returns a Panel containing a status table and a recent-events table.
    """
    status_table = Table(show_header=False, box=None, padding=(0, 2))
    status_table.add_column("label", style="bold cyan", justify="right")
    status_table.add_column("value", style="white")

    status_table.add_row("Status", state.status or "idle")
    status_table.add_row("Runtime", state.runtime or "—")
    status_table.add_row("Model", state.model or "—")
    status_table.add_row(
        "Agent",
        f"{state.agent_id or '—'}"
        + (f" ({state.agent_role})" if state.agent_role else ""),
    )
    status_table.add_row("Turn", str(state.turn))
    if state.chain_total is not None:
        status_table.add_row(
            "Chain",
            f"step {state.chain_step or 0}/{state.chain_total}",
        )
    status_table.add_row("Session", _truncate(state.session_id, 40))

    context_line = "—"
    if state.tokens_used is not None:
        context_line = f"{state.tokens_used}"
        if state.tokens_budget:
            context_line += f" / {state.tokens_budget}"
        if state.context_threshold:
            context_line += f" [{state.context_threshold}]"
    status_table.add_row("Context", context_line)

    if state.active_tool:
        status_table.add_row("Active tool", state.active_tool)

    events_table = Table(show_header=False, box=None, padding=(0, 1))
    events_table.add_column("event")
    for event in state.recent_events[-8:]:
        events_table.add_row(_format_event(event))

    content = Group(
        Text("Current task", style="bold underline"),
        Text(_truncate(state.prompt, 200)),
        Text(""),
        status_table,
        Text(""),
        Text("Recent activity", style="bold underline"),
        events_table,
    )

    return Panel(
        content,
        title="[bold]Maestro Monitor[/bold]",
        border_style="cyan",
        padding=(1, 2),
    )
