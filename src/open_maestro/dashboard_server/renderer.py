"""Render stored dashboard snapshots to HTML, JSON, or Markdown."""

from __future__ import annotations

from typing import Any

from open_maestro.milestones.dashboard import (
    export_dashboard_html_from_data,
    export_dashboard_json_from_data,
    export_dashboard_markdown_from_data,
)


def render_json(snapshot: dict[str, Any]) -> str:
    """Render a snapshot as JSON."""
    return export_dashboard_json_from_data(snapshot["dashboard"])


def render_html(snapshot: dict[str, Any]) -> str:
    """Render a snapshot as HTML."""
    return export_dashboard_html_from_data(snapshot["dashboard"])


def render_markdown(snapshot: dict[str, Any]) -> str:
    """Render a snapshot as Markdown."""
    return export_dashboard_markdown_from_data(snapshot["dashboard"])
