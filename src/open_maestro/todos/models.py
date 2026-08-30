"""Todo data models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TodoStatus(str, Enum):
    """Allowed todo statuses."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TodoItem(BaseModel):
    """A single todo item."""

    id: str = Field(default_factory=lambda: _new_id())
    title: str
    description: str | None = None
    status: TodoStatus = TodoStatus.PENDING
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: _now())
    updated_at: str = Field(default_factory=lambda: _now())
    agent_id: str | None = None
    turn: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TodoItem":
        return cls(**data)


def _new_id() -> str:
    """Return a short unique identifier."""
    from uuid import uuid4

    return uuid4().hex[:8]


def _now() -> str:
    return datetime.now(UTC).isoformat()
