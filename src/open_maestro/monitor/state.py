"""Live state snapshot for the Maestro activity monitor.

Why: The monitor needs a small, mutable summary of what Maestro is doing right
now so it can render a live view. Keeping the state in a dedicated dataclass
makes it easy to update from event handlers and render from a Rich Live display.
What: A thread-safe(ish) dataclass that tracks the current agent, runtime,
model, task, context usage, recent events, and active tool.
Test: ``MonitorState().update('agent.selected', {...})`` updates the agent_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class MonitorState:
    """Mutable snapshot of Maestro activity for the live monitor view."""

    status: str = "idle"
    runtime: str | None = None
    model: str | None = None
    agent_id: str | None = None
    agent_role: str | None = None
    prompt: str | None = None
    session_id: str | None = None
    turn: int = 0
    tokens_used: int | None = None
    tokens_budget: int | None = None
    context_threshold: str | None = None
    active_tool: str | None = None
    chain_step: int | None = None
    chain_total: int | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    def update(self, event_type: str, payload: dict[str, Any]) -> None:
        """Update state based on an event bus event.

        Why: Centralizes the mapping from event semantics to display fields so
        the renderer can stay dumb.
        What: Mutates relevant fields and appends a timestamped event record.
        """
        self.recent_events.append(
            {
                "ts": datetime.now(UTC).strftime("%H:%M:%S"),
                "event": event_type,
                "payload": payload,
            }
        )
        # Keep the last 50 events so the display stays bounded.
        self.recent_events = self.recent_events[-50:]

        if event_type == "task.received":
            self.status = "running"
            self.prompt = payload.get("prompt")
            self.agent_id = payload.get("agent_id") or self.agent_id
            self.turn = payload.get("turn", self.turn)
        elif event_type == "agent.selected":
            self.agent_id = payload.get("agent_id")
            self.agent_role = payload.get("role")
        elif event_type == "runtime.started":
            self.runtime = payload.get("runtime")
            self.model = payload.get("model")
        elif event_type == "runtime.completed":
            self.status = "idle" if not payload.get("is_error") else "error"
            self.active_tool = None
        elif event_type == "tool.call":
            self.status = "tool_call"
            self.active_tool = payload.get("tool_name")
        elif event_type == "tool.result":
            self.active_tool = None
        elif event_type == "context.threshold":
            self.context_threshold = payload.get("threshold")
            self.tokens_used = payload.get("tokens_used")
        elif event_type == "session.saved":
            self.session_id = payload.get("session_id") or self.session_id
        elif event_type == "runtime.working":
            self.status = "working"
        elif event_type == "chain.step_started":
            self.chain_step = payload.get("step", self.chain_step)
            self.chain_total = payload.get("total", self.chain_total)
            self.agent_id = payload.get("agent_id") or self.agent_id
        elif event_type == "chain.step_completed":
            self.chain_step = payload.get("step", self.chain_step)
