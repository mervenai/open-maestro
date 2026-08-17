"""Convert MCP tools into Open Maestro's vendor-neutral ``Tool`` objects."""

from __future__ import annotations

from typing import Any

from open_maestro.runtime.tools import Tool


def mcp_schema_to_json_schema(mcp_schema: dict[str, Any]) -> dict[str, Any]:
    """Convert an MCP input schema to a JSON Schema accepted by OpenAI.

    MCP schemas are already JSON Schemas, but older servers may omit the
    top-level ``type`` and ``additionalProperties`` keys.
    """
    schema = dict(mcp_schema)
    if "type" not in schema:
        schema["type"] = "object"
    if "additionalProperties" not in schema:
        schema["additionalProperties"] = False
    return schema


def mcp_tool_to_open_maestro(
    server_name: str,
    mcp_tool: Any,
    call_client: Any,
) -> Tool:
    """Wrap an MCP tool as an Open Maestro ``Tool``."""
    tool_name = getattr(mcp_tool, "name", "unknown")
    description = getattr(mcp_tool, "description", "") or ""
    input_schema = getattr(mcp_tool, "inputSchema", {}) or {}

    async def execute(**kwargs: Any) -> str:
        return await call_client(server_name, tool_name, kwargs)

    return Tool(
        name=tool_name,
        description=description,
        parameters=mcp_schema_to_json_schema(input_schema),
        execute=execute,
    )
