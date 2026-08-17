"""Event bus handler that feeds the live activity monitor.

Why: The monitor is just another event consumer. This handler maps event-bus
payloads into a ``MonitorState`` snapshot that a Rich Live display can render.
What: ``MonitorEventHandler`` is callable and can be attached with
``event_bus.on('*', handler)``. It is intentionally sync so it never blocks the
async event bus.
Test: Feeding it a ``runtime.started`` event updates ``state.runtime``.
"""

from __future__ import annotations

from typing import Any

from open_maestro.monitor.state import MonitorState


class MonitorEventHandler:
    """Update a shared MonitorState from event bus events."""

    def __init__(self, state: MonitorState) -> None:
        """Bind to the state snapshot that the renderer will display."""
        self.state = state

    def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        """Update state for any event type."""
        self.state.update(event_type, payload)
