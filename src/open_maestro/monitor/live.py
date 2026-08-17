"""Context manager that runs a Rich Live display around Maestro activity.

Why: Both one-shot CLI and interactive mode should be able to show a monitor
with the same lifecycle: start Live display, run the main logic, stop Live
display. This module provides that reusable wrapper.
What: ``Monitor`` holds a ``MonitorState``, attaches a ``MonitorEventHandler``
to the event bus, and refreshes a Rich ``Live`` renderable.
Test: Used as an async context manager around a ProjectManager call.
"""

from __future__ import annotations

from types import TracebackType

from rich.live import Live

from open_maestro.events.bus import EventBus
from open_maestro.events.monitor import MonitorEventHandler
from open_maestro.monitor.renderer import render
from open_maestro.monitor.state import MonitorState


class Monitor:
    """Live Rich monitor for Maestro activity.

    Why: Encapsulates the event-handler registration and Live display lifecycle
    so callers only need ``async with Monitor(event_bus): ...``.
    What: Attaches a handler to the singleton event bus, renders ``MonitorState``
    via Rich, and refreshes it as events arrive.
    """

    def __init__(
        self,
        event_bus: EventBus,
        state: MonitorState | None = None,
        refresh_per_second: float = 4.0,
        transient: bool = True,
    ) -> None:
        """Create a monitor bound to an event bus.

        Args:
            event_bus: The Maestro event bus to subscribe to.
            state: Optional pre-created state snapshot.
            refresh_per_second: Live display refresh rate.
            transient: Whether the display clears on exit (default True).
        """
        self.event_bus = event_bus
        self.state = state or MonitorState()
        self._handler = MonitorEventHandler(self.state)
        self._refresh_per_second = refresh_per_second
        self._transient = transient
        self._live: Live | None = None

    async def __aenter__(self) -> Monitor:
        """Start the live display and subscribe to events."""
        self.event_bus.on("*", self._handler)
        self._live = Live(
            render(self.state),
            refresh_per_second=self._refresh_per_second,
            transient=self._transient,
            auto_refresh=True,
        )
        self._live.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Stop the live display and unsubscribe from events."""
        self.event_bus.off("*", self._handler)
        if self._live is not None:
            self._live.stop()
            self._live = None

    def refresh(self) -> None:
        """Force the display to refresh now.

        Why: Rich Live auto-refreshes, but some callers may want an explicit
        refresh after a burst of events.
        """
        if self._live is not None:
            self._live.update(render(self.state))
