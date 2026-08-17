"""Tests for provider-specific CLI option mapping and agent capabilities."""

from __future__ import annotations

from pathlib import Path

from open_maestro.agents.definition import AgentDefinition
from open_maestro.config.capabilities import (
    CodingStrength,
    ReasoningLevel,
    RequiredCapabilities,
    TaskProfile,
)
from open_maestro.runtime.base import AgentConfig
from open_maestro.runtime.claude_cli import ClaudeCLIRuntime
from open_maestro.runtime.kimi_cli import KimiCLIRuntime


class TestAgentDefinitionCapabilities:
    def test_parse_required_capabilities(self, tmp_path: Path):
        md = tmp_path / "vision-agent.md"
        md.write_text(
            "---\n"
            "id: vision-agent\n"
            "name: Vision Agent\n"
            "role: specialized\n"
            "model: smart\n"
            "required_capabilities:\n"
            "  tool_use: true\n"
            "  vision: true\n"
            "  reasoning: deep\n"
            "  coding_strength: high\n"
            "  max_context_tokens: 200000\n"
            "---\n\n"
            "Primary Role\n"
        )
        agent = AgentDefinition.from_markdown(md)
        assert agent.required_capabilities.tool_use is True
        assert agent.required_capabilities.vision is True
        assert agent.required_capabilities.reasoning == ReasoningLevel.DEEP
        assert agent.required_capabilities.coding_strength == CodingStrength.HIGH
        assert agent.required_capabilities.max_context_tokens == 200000

    def test_to_config_includes_required_capabilities(self, tmp_path: Path):
        md = tmp_path / "agent.md"
        md.write_text(
            "---\n"
            "id: agent\n"
            "name: Agent\n"
            "role: engineer\n"
            "required_capabilities:\n"
            "  vision: true\n"
            "---\n\n"
            "Role\n"
        )
        agent = AgentDefinition.from_markdown(md)
        config = agent.to_config()
        assert isinstance(config["required_capabilities"], RequiredCapabilities)
        assert config["required_capabilities"].vision is True


class TestRequiredCapabilitiesMerge:
    def test_merge_overrides_profile(self):
        required = RequiredCapabilities(
            vision=True,
            reasoning=ReasoningLevel.DEEP,
            coding_strength=CodingStrength.HIGH,
            max_context_tokens=100000,
        )
        profile = TaskProfile(
            needs_vision=False,
            reasoning_depth=ReasoningLevel.LIGHT,
            coding_strength=CodingStrength.MEDIUM,
            context_tokens_estimate=8000,
        )
        merged = required.merge_into_profile(profile)
        assert merged.needs_vision is True
        assert merged.reasoning_depth == ReasoningLevel.DEEP
        assert merged.coding_strength == CodingStrength.HIGH
        assert merged.context_tokens_estimate == 100000

    def test_merge_preserves_unset_fields(self):
        required = RequiredCapabilities()
        profile = TaskProfile(reasoning_depth=ReasoningLevel.LIGHT)
        merged = required.merge_into_profile(profile)
        assert merged.reasoning_depth == ReasoningLevel.LIGHT
        assert merged.needs_vision is False


class TestClaudeCLIOptionMapping:
    def test_allowed_tools_and_blocked_tools(self):
        runtime = ClaudeCLIRuntime()
        config = AgentConfig(
            allowed_tools=["Read", "Edit", "Bash"],
            blocked_tools={"Write", "WebFetch"},
        )
        args = runtime._build_args("do it", config=config)
        assert "--allowedTools" in args
        assert "Read,Edit,Bash" in args
        assert "--disallowedTools" in args
        disallowed_index = args.index("--disallowedTools")
        assert args[disallowed_index + 1] == "WebFetch,Write"

    def test_permission_mode(self):
        runtime = ClaudeCLIRuntime()
        config = AgentConfig(permission_mode="auto")
        args = runtime._build_args("do it", config=config)
        assert "--permission-mode" in args
        mode_index = args.index("--permission-mode")
        assert args[mode_index + 1] == "auto"

    def test_mcp_config_writes_temp_file(self):
        runtime = ClaudeCLIRuntime()
        config = AgentConfig(
            mcp_servers={
                "memory": {
                    "command": "kuzu-memory",
                    "args": ["mcp", "serve"],
                }
            }
        )
        temp_files: list[str] = []
        args = runtime._build_args("do it", config=config, temp_files=temp_files)
        assert "--mcp-config" in args
        assert len(temp_files) == 1
        path = temp_files[0]
        assert Path(path).exists()
        content = Path(path).read_text()
        assert "kuzu-memory" in content
        # Clean up
        Path(path).unlink(missing_ok=True)

    def test_mcp_config_with_mcpservers_key(self):
        runtime = ClaudeCLIRuntime()
        config = AgentConfig(
            mcp_servers={
                "mcpServers": {
                    "search": {
                        "command": "mcp-vector-search",
                        "args": ["mcp", "serve"],
                    }
                }
            }
        )
        temp_files: list[str] = []
        args = runtime._build_args("do it", config=config, temp_files=temp_files)
        assert "--mcp-config" in args
        Path(temp_files[0]).unlink(missing_ok=True)


class TestKimiCLIOptionMapping:
    def test_auto_permission_mode(self):
        runtime = KimiCLIRuntime()
        config = AgentConfig(permission_mode="auto")
        args = runtime._build_args("do it", config=config)
        assert "--auto" in args

    def test_yolo_permission_mode(self):
        runtime = KimiCLIRuntime()
        config = AgentConfig(permission_mode="yolo")
        args = runtime._build_args("do it", config=config)
        assert "--yolo" in args

    def test_skills_dirs(self):
        runtime = KimiCLIRuntime()
        config = AgentConfig(extra={"skills_dirs": ["/tmp/skills", "/opt/skills"]})
        args = runtime._build_args("do it", config=config)
        assert "--skills-dir" in args
        indices = [i for i, a in enumerate(args) if a == "--skills-dir"]
        assert len(indices) == 2
        assert args[indices[0] + 1] == "/tmp/skills"
        assert args[indices[1] + 1] == "/opt/skills"

    def test_mcp_servers_logs_debug(self, caplog):
        runtime = KimiCLIRuntime()
        config = AgentConfig(mcp_servers={"foo": {}})
        with caplog.at_level("DEBUG"):
            runtime._build_args("do it", config=config)
        assert "does not support passing MCP servers" in caplog.text
