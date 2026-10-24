"""Wire the maestro MCP server into a project's ``.mcp.json``.

``.mcp.json`` at the project root is the project-level MCP config read by both
Claude Code and Kimi, so one merge serves every agent interface. ``maestro
--mcp-init`` adds (or refreshes) the ``maestro`` stdio server entry without
touching other servers already configured.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

CONFIG_NAME = ".mcp.json"
SERVER_KEY = "maestro"


def _maestro_command() -> str:
    """Absolute path to the maestro console script."""
    found = shutil.which("maestro")
    if found:
        return str(Path(found).resolve())
    return str(Path(sys.argv[0]).resolve())


def write_mcp_config(project_path: str | Path) -> dict[str, Any]:
    """Merge the maestro stdio server entry into ``<project>/.mcp.json``.

    Returns a report: the config path, whether it was created, and the sorted
    server keys now present.
    """
    root = Path(project_path)
    config_path = root / CONFIG_NAME
    created = not config_path.exists()

    if created:
        data: dict[str, Any] = {"mcpServers": {}}
    else:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{config_path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{config_path} must contain a JSON object.")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{config_path}: 'mcpServers' must be an object.")

    servers[SERVER_KEY] = {
        "type": "stdio",
        "command": _maestro_command(),
        "args": ["--mcp-serve"],
    }

    config_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "path": str(config_path),
        "created": created,
        "servers": sorted(servers),
    }
