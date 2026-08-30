"""Interactive slash-command handlers for project todos."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from open_maestro.todos.models import TodoStatus
from open_maestro.todos.store import TodoStore


def _format_item(item: Any) -> str:
    tags = f" [{', '.join(item.tags)}]" if item.tags else ""
    desc = f"\n    {item.description}" if item.description else ""
    return f"  [{item.status.value}] {item.id}: {item.title}{tags}{desc}"


def handle_todo_command(project_dir: Path | str, args: list[str]) -> str:
    """Handle `/todo <subcommand> ...` and return a message for the user."""
    if not args or args[0] in ("help", "-h", "--help"):
        return (
            "Todo commands:\n"
            "  /todo add <title> [--desc <text>] [--tag <tag>]\n"
            "  /todo list [--all] [--tag <tag>]\n"
            "  /todo done <id>\n"
            "  /todo block <id> [--reason <text>]\n"
            "  /todo delete <id>\n"
            "  /todo clear"
        )

    store = TodoStore(project_dir)
    subcommand = args[0].lower()
    rest = args[1:]

    if subcommand == "add":
        return _handle_add(store, rest)
    if subcommand == "list":
        return _handle_list(store, rest)
    if subcommand == "done":
        return _handle_status(store, rest, TodoStatus.DONE)
    if subcommand == "block":
        return _handle_block(store, rest)
    if subcommand in ("delete", "rm"):
        return _handle_delete(store, rest)
    if subcommand == "clear":
        store.clear()
        return "All todos cleared."

    return f"Unknown todo command '/todo {subcommand}'. Type /todo help for usage."


def _handle_add(store: TodoStore, args: list[str]) -> str:
    if not args:
        return "Usage: /todo add <title> [--desc <text>] [--tag <tag>]"

    title_parts: list[str] = []
    description: str | None = None
    tags: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--desc", "-d"):
            i += 1
            if i >= len(args):
                return "Missing value for --desc."
            description = args[i]
        elif arg in ("--tag", "-t"):
            i += 1
            if i >= len(args):
                return "Missing value for --tag."
            tags.append(args[i])
        else:
            title_parts.append(arg)
        i += 1

    if not title_parts:
        return "Usage: /todo add <title> [--desc <text>] [--tag <tag>]"

    item = store.add(" ".join(title_parts), description=description, tags=tags)
    return f"Added todo {item.id}: {item.title}"


def _handle_list(store: TodoStore, args: list[str]) -> str:
    show_all = "--all" in args or "-a" in args
    tags: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--tag", "-t"):
            i += 1
            if i >= len(args):
                return "Missing value for --tag."
            tags.append(args[i])
        i += 1

    if show_all:
        items = store.list()
    else:
        items = store.list(
            status=[TodoStatus.PENDING, TodoStatus.IN_PROGRESS, TodoStatus.BLOCKED]
        )
    if tags:
        tag_set = set(tags)
        items = [i for i in items if tag_set & set(i.tags)]

    if not items:
        return "No todos."
    return "Todos:\n" + "\n".join(_format_item(i) for i in items)


def _handle_status(store: TodoStore, args: list[str], status: TodoStatus) -> str:
    if not args:
        return f"Usage: /todo {status.value} <id>"
    item_id = args[0]
    item = store.update(item_id, status=status)
    if item is None:
        return f"Todo '{item_id}' not found."
    return f"Marked {item.id} as {status.value}: {item.title}"


def _handle_block(store: TodoStore, args: list[str]) -> str:
    if not args:
        return "Usage: /todo block <id> [--reason <text>]"
    item_id = args[0]
    reason: str | None = None
    if "--reason" in args:
        idx = args.index("--reason")
        if idx + 1 < len(args):
            reason = args[idx + 1]
    description = None
    if reason:
        item = store.get(item_id)
        if item is not None and item.description:
            description = f"{item.description}\nBlocker: {reason}"
        else:
            description = f"Blocker: {reason}"
    item = store.update(item_id, status=TodoStatus.BLOCKED, description=description)
    if item is None:
        return f"Todo '{item_id}' not found."
    return f"Marked {item.id} as blocked: {item.title}"


def _handle_delete(store: TodoStore, args: list[str]) -> str:
    if not args:
        return "Usage: /todo delete <id>"
    item_id = args[0]
    if store.delete(item_id):
        return f"Deleted todo {item_id}."
    return f"Todo '{item_id}' not found."
