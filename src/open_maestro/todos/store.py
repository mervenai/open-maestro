"""JSON-backed todo store scoped to a project directory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from open_maestro.todos.models import TodoItem, TodoStatus

logger = logging.getLogger(__name__)

_DEFAULT_FILENAME = "todos.json"


class TodoStore:
    """Persist and query project todos in ``.open-maestro/todos.json``."""

    def __init__(self, project_dir: Path | str | None = None) -> None:
        if project_dir is None:
            project_dir = Path.cwd()
        self.project_dir = Path(project_dir).resolve()
        self.path = self.project_dir / ".open-maestro" / _DEFAULT_FILENAME

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> list[TodoItem]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load todos from %s: %s", self.path, exc)
            return []
        if not isinstance(raw, list):
            return []
        items: list[TodoItem] = []
        for entry in raw:
            try:
                items.append(TodoItem.from_dict(entry))
            except Exception as exc:
                logger.warning("Skipping invalid todo entry: %s", exc)
        return items

    def save(self, items: list[TodoItem]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.to_dict() for item in items]
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add(
        self,
        title: str,
        description: str | None = None,
        tags: list[str] | None = None,
        agent_id: str | None = None,
        turn: int | None = None,
        status: TodoStatus = TodoStatus.PENDING,
    ) -> TodoItem:
        items = self.load()
        item = TodoItem(
            title=title,
            description=description,
            status=status,
            tags=list(tags or []),
            agent_id=agent_id,
            turn=turn,
        )
        items.append(item)
        self.save(items)
        return item

    def get(self, item_id: str) -> TodoItem | None:
        for item in self.load():
            if item.id == item_id:
                return item
        return None

    def list(
        self,
        status: TodoStatus | list[TodoStatus] | None = None,
        tags: list[str] | None = None,
    ) -> list[TodoItem]:
        items = self.load()
        if status is not None:
            if isinstance(status, TodoStatus):
                statuses = {status}
            else:
                statuses = set(status)
            items = [i for i in items if i.status in statuses]
        if tags:
            tag_set = set(tags)
            items = [i for i in items if tag_set & set(i.tags)]
        return items

    def update(
        self,
        item_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: TodoStatus | None = None,
        tags: list[str] | None = None,
        agent_id: str | None = None,
        turn: int | None = None,
    ) -> TodoItem | None:
        from open_maestro.todos.models import _now

        items = self.load()
        for item in items:
            if item.id == item_id:
                if title is not None:
                    item.title = title
                if description is not None:
                    item.description = description
                if status is not None:
                    item.status = status
                if tags is not None:
                    item.tags = list(tags)
                if agent_id is not None:
                    item.agent_id = agent_id
                if turn is not None:
                    item.turn = turn
                item.updated_at = _now()
                self.save(items)
                return item
        return None

    def delete(self, item_id: str) -> bool:
        items = self.load()
        new_items = [i for i in items if i.id != item_id]
        if len(new_items) == len(items):
            return False
        self.save(new_items)
        return True

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def format_open(self) -> str:
        """Return a short Markdown summary of open todos for prompt injection."""
        open_items = self.list(
            status=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS, TodoStatus.BLOCKED]
        )
        if not open_items:
            return ""
        lines = ["## Open project todos", ""]
        for item in open_items:
            tag_str = f" [{', '.join(item.tags)}]" if item.tags else ""
            desc = f" — {item.description}" if item.description else ""
            lines.append(f"- [{item.status.value}] {item.id}: {item.title}{tag_str}{desc}")
        lines.append("")
        lines.append(
            "When you complete one of these tasks, ask the user to run `/todo done <id>`."
        )
        return "\n".join(lines)
