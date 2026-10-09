"""Tests for MCP configuration loading and tool conversion."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from open_maestro.mcp.config import list_servers, load_mcp_config
from open_maestro.mcp.tools import mcp_schema_to_json_schema, mcp_tool_to_open_maestro


class TestMCPConfigLoading:
    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path: Path, monkeypatch):
        """Discovery must not see the developer machine's real ~/.open-maestro/mcp.json."""
        monkeypatch.setenv("HOME", str(tmp_path / "home"))

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


class TestReadOnlyMCPPolicy:
    def test_names_cover_vetted_suffixes_per_server(self):
        from open_maestro.mcp.policy import (
            READONLY_MCP_TOOL_SUFFIXES,
            readonly_mcp_tool_names,
        )

        names = readonly_mcp_tool_names(
            {"mcpServers": {"mcp-vector-search": {"command": "mvs"}}}
        )
        assert len(names) == len(READONLY_MCP_TOOL_SUFFIXES)
        assert "mcp__mcp-vector-search__search_code" in names
        assert "mcp__mcp-vector-search__kg_query" in names

    def test_mutating_tools_are_not_vetted(self):
        from open_maestro.mcp.policy import readonly_mcp_tool_names

        names = readonly_mcp_tool_names(
            {"mcpServers": {"mcp-vector-search": {"command": "mvs"}}}
        )
        for mutating in (
            "index_project",
            "embed_chunks",
            "save_report",
            "review_repository",
            "review_pull_request",
            "code_review",
            "wiki_generate",
            "kg_build",
            "story_generate",
        ):
            assert f"mcp__mcp-vector-search__{mutating}" not in names

    def test_flat_mapping_and_absent_config(self):
        from open_maestro.mcp.policy import readonly_mcp_tool_names

        flat = readonly_mcp_tool_names(
            {"srv-a": {"command": "x"}, "srv-b": {"command": "y"}}
        )
        assert any(n.startswith("mcp__srv-a__") for n in flat)
        assert any(n.startswith("mcp__srv-b__") for n in flat)
        assert readonly_mcp_tool_names(None) == []
        assert readonly_mcp_tool_names({}) == []
