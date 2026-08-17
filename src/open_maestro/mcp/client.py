"""MCP client for the OpenAI SDK runtime.

Manages stdio connections to MCP servers, lists their tools, and dispatches
tool calls back to the correct server.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from open_maestro.mcp.tools import mcp_tool_to_open_maestro
from open_maestro.runtime.tools import Tool

logger = logging.getLogger(__name__)


class MCPClient:
    """Async context manager that connects to MCP servers and exposes their tools."""

    def __init__(self, mcp_servers: dict[str, Any]) -> None:
        self._mcp_servers = mcp_servers
        self._sessions: dict[str, Any] = {}
        self._tools: dict[str, Tool] = {}
        self._exit_stack = AsyncExitStack()

    async def __aenter__(self) -> MCPClient:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError(
                "The 'mcp' package is required for MCP support in the openai-sdk runtime. "
                "Install it with: pip install mcp"
            ) from exc

        for server_name, server_config in self._mcp_servers.items():
            command = server_config.get("command")
            args = server_config.get("args", [])
            env = server_config.get("env")
            if not command:
                logger.warning("MCP server '%s' has no command; skipping", server_name)
                continue

            params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
            )
            try:
                read_stream, write_stream = await self._exit_stack.enter_async_context(
                    stdio_client(params)
                )
                session = await self._exit_stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                await session.initialize()
                self._sessions[server_name] = session

                tools_result = await session.list_tools()
                for mcp_tool in tools_result.tools:
                    tool = mcp_tool_to_open_maestro(
                        server_name,
                        mcp_tool,
                        self._call_tool,
                    )
                    if tool.name in self._tools:
                        logger.warning(
                            "Duplicate MCP tool name '%s' from server '%s'; ignoring",
                            tool.name,
                            server_name,
                        )
                        continue
                    self._tools[tool.name] = tool
                    logger.debug(
                        "Registered MCP tool '%s' from server '%s'",
                        tool.name,
                        server_name,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to connect to MCP server '%s': %s", server_name, exc
                )

        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._exit_stack.aclose()
        self._sessions.clear()
        self._tools.clear()

    def list_tools(self) -> list[Tool]:
        """Return all tools exposed by connected MCP servers."""
        return list(self._tools.values())

    async def _call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        session = self._sessions.get(server_name)
        if session is None:
            return f"Error: MCP server '{server_name}' is not connected."
        try:
            result = await session.call_tool(tool_name, arguments)
            contents = []
            for item in result.content:
                text = getattr(item, "text", None)
                if text is not None:
                    contents.append(str(text))
            return "\n".join(contents) if contents else "(empty result)"
        except Exception as exc:
            logger.warning("MCP tool call %s/%s failed: %s", server_name, tool_name, exc)
            return f"Error calling MCP tool {tool_name}: {exc}"
