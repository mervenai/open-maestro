"""Dossier open-items discovery and prompt formatting.

Why: A drafted artifact once claimed "six gates, none changes frozen structure"
while the project dossier still had open items (B3/B4/B5/H1/H2/H6) that do
change structure — the dossier was never part of the prompt context.
What: Locate dossier/decision markdown files under ``docs/``, extract
open/pending items, and render a compact ``## Open items from dossier``
block for agent prompts.
Test: ``uv run pytest tests/test_dossier.py``
"""

from __future__ import annotations

import re
from pathlib import Path

_SKIP_DIRS = {".open-maestro", "node_modules", "__pycache__"}
_OPEN_WORDS = ("open", "pending", "tbd", "unresolved", "needs decision")
_OPEN_HEADING = re.compile(r"open items|open questions|pending decisions", re.IGNORECASE)
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")
_CHECKBOX = re.compile(r"^\[(?P<box>[ xX])\]\s*")
_MAX_ITEM_CHARS = 200


def discover_dossier_files(project_root: Path) -> list[Path]:
    """Return sorted dossier/decision markdown files under ``docs/``.

    Matches ``docs/intake/dossier*.md``, ``docs/*dossier*.md``,
    ``docs/*decision*.md``, and ``docs/intake/*decision*.md`` (any case),
    while skipping ``.open-maestro`` and ``node_modules`` directories.
    Returns an empty list when nothing is found.
    """
    docs = Path(project_root) / "docs"
    if not docs.is_dir():
        return []
    found: set[Path] = set()
    for path in docs.rglob("*.md"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        name = path.name.lower()
        if "dossier" in name or "decision" in name:
            found.add(path)
    return sorted(found)


def extract_open_items(path: Path) -> list[str]:
    """Return open/pending items from a dossier markdown file.

    An item is a bullet or heading whose text contains "open", "pending",
    "TBD", "unresolved", or "needs decision" (case-insensitive), or any
    bullet under a heading matching ``open items|open questions|pending
    decisions``. Checked checkbox bullets (``[x]``) are treated as resolved
    and skipped. Each item is kept to its first line and truncated to
    ~200 characters. Returns an empty list when nothing is open.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    items: list[str] = []
    in_open_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(1).strip()
            if _OPEN_HEADING.search(title):
                in_open_section = True
                continue
            in_open_section = False
            if _contains_open_word(title):
                items.append(_clean_item(title))
            continue
        bullet = _BULLET.match(raw)
        if not bullet:
            continue
        content = line[bullet.end():].strip()
        checkbox = _CHECKBOX.match(content)
        if checkbox:
            if checkbox.group("box").lower() == "x":
                continue  # resolved
            content = content[checkbox.end():].strip()
        if in_open_section or _contains_open_word(content):
            items.append(_clean_item(content))
    return items


def format_dossier_context(project_root: Path, max_chars: int = 2000) -> str:
    """Render open dossier items as a markdown block for agent prompts.

    Returns an empty string when no dossier files or open items exist.
    The block is truncated to ``max_chars`` by dropping whole trailing
    lines, never splitting a line mid-item.
    """
    lines = ["## Open items from dossier"]
    for path in discover_dossier_files(project_root):
        rel = path.relative_to(Path(project_root))
        for item in extract_open_items(path):
            lines.append(f"- {item} ({rel})")
    if len(lines) == 1:
        return ""

    header = lines[0]
    budget = max_chars - len(header)
    kept: list[str] = []
    for line in lines[1:]:
        cost = len(line) + 1  # plus newline
        if cost > budget:
            break
        kept.append(line)
        budget -= cost
    if not kept:
        return ""
    return "\n".join([header, *kept])


def _contains_open_word(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _OPEN_WORDS)


def _clean_item(text: str) -> str:
    item = text.splitlines()[0].strip()
    if len(item) > _MAX_ITEM_CHARS:
        item = item[:_MAX_ITEM_CHARS].rstrip()
    return item
