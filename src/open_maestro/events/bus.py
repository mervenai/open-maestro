"""Async event bus for Open Maestro observability.

The bus is a singleton so runtimes, the orchestrator, and future dashboards can
all publish and subscribe without threading a single instance through every
constructor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class EventBus:
    """Simple async event bus supporting typed and wildcard handlers."""

    _instance: EventBus | None = None

    def __new__(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        return cls._instance

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe *handler* to *event_type* (``*`` for all events)."""
        self._handlers[event_type].append(handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Unsubscribe *handler* from *event_type*."""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit *event_type* with *payload* to all matching handlers."""
        handlers = (
            self._handlers.get(event_type, [])
            + self._handlers.get("*", [])
        )
        for handler in handlers:
            try:
                result = handler(event_type, payload)
                if result is not None:
                    await result
            except Exception as exc:
                logger.warning("Event handler for %s failed: %s", event_type, exc)
