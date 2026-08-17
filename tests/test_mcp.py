"""Tests for MCP configuration loading and tool conversion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from open_maestro.mcp.config import list_servers, load_mcp_config
from open_maestro.mcp.tools import mcp_schema_to_json_schema, mcp_tool_to_open_maestro


class TestMCPConfigLoading:
    def test_load_flat_config(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".mcp.json").write_text(
            '{"memory": {"command": "npx", "args": ["-y", "@memory/server"]}}'
        )

        config = load_mcp_config()
        assert config is not None
        servers = list_servers(config)
        assert "memory" in servers
        assert servers["memory"]["command"] == "npx"

    def test_load_nested_mcp_servers(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".open-maestro").mkdir()
        (tmp_path / ".open-maestro" / "mcp.json").write_text(
            '{"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}'
        )

        config = load_mcp_config()
        assert config is not None
        servers = list_servers(config)
        assert "fetch" in servers
        assert servers["fetch"]["command"] == "uvx"

    def test_explicit_path_overrides_discovery(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".mcp.json").write_text('{"ignored": {}}')

        explicit = tmp_path / "explicit.json"
        explicit.write_text('{"mcpServers": {"explicit": {"command": "echo"}}}')

        config = load_mcp_config(explicit)
        servers = list_servers(config)
        assert "explicit" in servers
        assert "ignored" not in servers

    def test_missing_config_returns_none(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert load_mcp_config() is None


class TestMCPToolConversion:
    def test_mcp_schema_to_json_schema_adds_defaults(self):
        schema = {"properties": {"path": {"type": "string"}}, "required": ["path"]}
        converted = mcp_schema_to_json_schema(schema)
        assert converted["type"] == "object"
        assert converted["additionalProperties"] is False
        assert converted["properties"]["path"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_mcp_tool_to_open_maestro_executes_callback(self):
        calls: list[tuple[str, str, dict[str, str]]] = []

        async def call_client(server: str, tool: str, args: dict[str, str]) -> str:
            calls.append((server, tool, args))
            return "ok"

        mcp_tool = SimpleNamespace(
            name="remember",
            description="Store a memory.",
            inputSchema={
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        )
        tool = mcp_tool_to_open_maestro("memory-server", mcp_tool, call_client)
        result = await tool.execute(note="hello")

        assert tool.name == "remember"
        assert result == "ok"
        assert calls == [("memory-server", "remember", {"note": "hello"})]
