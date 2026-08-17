"""MCP server configuration discovery and loading.

Open Maestro looks for MCP server definitions in the same tiered order as agents
and skills:

1. ``./.open-maestro/mcp.json`` or ``./.open-maestro/mcp.yaml``
2. ``~/.open-maestro/mcp.json`` or ``~/.open-maestro/mcp.yaml``
3. ``./.mcp.json`` (common Claude Code convention)
4. ``OPEN_MAESTRO_MCP_CONFIG`` environment variable

The config may be either a flat mapping of server names to their settings or
already wrapped in ``{ "mcpServers": { ... } }``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _candidate_paths(explicit: Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    if os.environ.get("OPEN_MAESTRO_MCP_CONFIG"):
        candidates.append(Path(os.environ["OPEN_MAESTRO_MCP_CONFIG"]))
    candidates.extend(
        [
            Path.cwd() / ".open-maestro" / "mcp.json",
            Path.cwd() / ".open-maestro" / "mcp.yaml",
            Path.home() / ".open-maestro" / "mcp.json",
            Path.home() / ".open-maestro" / "mcp.yaml",
            Path.cwd() / ".mcp.json",
            Path.cwd() / ".mcp.yaml",
        ]
    )
    return candidates


def load_mcp_config(explicit_path: Path | None = None) -> dict[str, Any] | None:
    """Load the first available MCP config file.

    Returns ``None`` if no config is found.  The returned dict is always in the
    ``{ "mcpServers": { name: settings, ... } }`` shape.
    """
    for candidate in _candidate_paths(explicit_path):
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8")
            if candidate.suffix in (".yaml", ".yml"):
                data = yaml.safe_load(raw) or {}
            else:
                data = json.loads(raw)
        except Exception as exc:
            logger.warning("Failed to load MCP config from %s: %s", candidate, exc)
            continue

        if not isinstance(data, dict):
            logger.warning("MCP config %s is not a JSON object", candidate)
            continue

        if "mcpServers" in data:
            return {"mcpServers": dict(data["mcpServers"])}

        # Treat a flat mapping as mcpServers.
        return {"mcpServers": dict(data)}

    return None


def list_servers(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``mcpServers`` mapping from a config, or an empty dict."""
    return config.get("mcpServers", {}) if isinstance(config, dict) else {}
