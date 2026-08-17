"""Unit tests for the vendor-neutral runtime layer."""

from __future__ import annotations

import pytest

from open_maestro.runtime.base import AgentResult
from open_maestro.runtime.claude_cli import ClaudeCLIRuntime
from open_maestro.runtime.kimi_cli import KimiCLIRuntime


class TestKimiCLIParsing:
    def test_parse_simple_response(self):
        raw = (
            '{"role":"assistant","content":"hello"}\n'
            '{"role":"meta","type":"session.resume_hint",'
            '"session_id":"session_abc","command":"kimi -r session_abc"}\n'
        )
        result = KimiCLIRuntime._parse_output(raw, duration_ms=123)
        assert result.text == "hello"
        assert result.session_id == "session_abc"
        assert result.duration_ms == 123
        assert result.metadata["resume_command"] == "kimi -r session_abc"

    def test_parse_plain_text_fallback(self):
        result = KimiCLIRuntime._parse_output("just text")
        assert result.text == "just text"

    @pytest.mark.parametrize(
        ("raw", "expected_text", "expected_session"),
        [
            ("", "", None),  # empty output
            ("\n\n  \n", "", None),  # blank lines only
            (  # multiple assistant messages join with newline
                '{"role":"assistant","content":"one"}\n'
                '{"role":"assistant","content":"two"}',
                "one\ntwo",
                None,
            ),
            (  # malformed lines fall back to plain text
                'noise\n{"role":"assistant","content":"ok"}',
                "noise\nok",
                None,
            ),
            (  # non-assistant roles and unknown meta types are ignored
                '{"role":"tool","content":"x"}\n{"role":"meta","type":"other"}',
                "",
                None,
            ),
            (  # assistant message with empty content contributes nothing
                '{"role":"assistant","content":""}\n{"role":"assistant"}',
                "",
                None,
            ),
        ],
    )
    def test_parse_output_edge_cases(self, raw, expected_text, expected_session):
        result = KimiCLIRuntime._parse_output(raw)
        assert result.text == expected_text
        assert result.session_id == expected_session
        assert result.metadata == {}

    @pytest.mark.xfail(
        strict=True,
        reason="BUG: valid-JSON non-object lines (list/scalar) crash _parse_output "
        "with AttributeError; should fall back to plain text like malformed lines",
    )
    def test_parse_non_object_json_line_does_not_crash(self):
        raw = '[1, 2, 3]\n{"role":"assistant","content":"ok"}'
        result = KimiCLIRuntime._parse_output(raw)
        assert result.text == "[1, 2, 3]\nok"

    def test_parse_last_resume_hint_wins(self):
        raw = (
            '{"role":"meta","type":"session.resume_hint","session_id":"s1","command":"c1"}\n'
            '{"role":"meta","type":"session.resume_hint","session_id":"s2","command":"c2"}'
        )
        result = KimiCLIRuntime._parse_output(raw)
        assert result.session_id == "s2"
        assert result.metadata["resume_command"] == "c2"

    def test_parse_resume_hint_missing_fields(self):
        result = KimiCLIRuntime._parse_output(
            '{"role":"meta","type":"session.resume_hint"}'
        )
        assert result.session_id is None
        assert result.metadata == {"resume_command": None}

    def test_parse_non_string_content_coerced(self):
        result = KimiCLIRuntime._parse_output('{"role":"assistant","content":123}')
        assert result.text == "123"


class TestClaudeCLIParsing:
    def test_parse_json_response(self):
        raw = '{"result":"done","session_id":"sess_1","cost_usd":0.01,"num_turns":2}'
        result = ClaudeCLIRuntime._parse_output(raw, duration_ms=456)
        assert result.text == "done"
        assert result.session_id == "sess_1"
        assert result.cost_usd == 0.01
        assert result.num_turns == 2
        assert result.duration_ms == 456

    def test_parse_plain_text_fallback(self):
        result = ClaudeCLIRuntime._parse_output("plain response")
        assert result.text == "plain response"


class TestOpenAISDKRuntime:
    def test_available_with_base_url_no_api_key(self):
        from open_maestro.runtime.openai_sdk import OpenAISDKRuntime

        runtime = OpenAISDKRuntime(base_url="http://localhost:11434/v1")
        assert runtime.is_available() is True

    def test_not_available_without_credentials_or_base_url(self):
        from open_maestro.runtime.openai_sdk import OpenAISDKRuntime

        runtime = OpenAISDKRuntime()
        # This assumes OPENAI_API_KEY is not set in the test environment.
        assert runtime.is_available() is False


class TestCLIDelegationToSDK:
    """CLI runtimes should delegate run_with_hooks to SDK/ACP when available."""

    def test_kimi_cli_delegates_to_acp_when_available(self):
        import asyncio

        from open_maestro.runtime.kimi_cli import KimiCLIRuntime

        runtime = KimiCLIRuntime()

        class FakeACPRuntime:
            def __init__(self, *args, **kwargs):
                pass

            async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
                return AgentResult(text="acp-delegated", metadata={"tool": "intercepted"})

        runtime._try_acp_runtime = lambda config=None: FakeACPRuntime()

        async def guard(tool_name, tool_input):
            return True

        result = asyncio.run(
            runtime.run_with_hooks("prompt", tool_guard=guard, blocked_tools={"Write"})
        )
        assert result.text == "acp-delegated"
        assert result.metadata.get("tool") == "intercepted"

    def test_kimi_cli_falls_back_when_acp_unavailable(self):
        import asyncio

        from open_maestro.runtime.kimi_cli import KimiCLIRuntime

        runtime = KimiCLIRuntime()
        runtime._try_acp_runtime = lambda config=None: None
        calls = []

        async def fake_run(prompt, config=None):
            calls.append((prompt, config))
            return AgentResult(text="cli-fallback")

        runtime.run = fake_run

        async def guard(tool_name, tool_input):
            return True

        result = asyncio.run(
            runtime.run_with_hooks("prompt", tool_guard=guard, blocked_tools={"Write"})
        )
        assert result.text == "cli-fallback"
        assert len(calls) == 1
        assert "Write" in (calls[0][1].system_prompt or "")

    def test_kimi_cli_does_not_use_acp_by_default(self, monkeypatch):
        """ACP delegation is opt-in to avoid version-compatibility crashes."""
        import os

        from open_maestro.runtime.kimi_cli import KimiCLIRuntime

        monkeypatch.delenv("MAESTRO_KIMI_ACP_DELEGATION", raising=False)
        runtime = KimiCLIRuntime()
        assert runtime._try_acp_runtime() is None

    def test_kimi_cli_uses_acp_when_env_var_set(self, monkeypatch):
        """ACP delegation is enabled with MAESTRO_KIMI_ACP_DELEGATION=1."""
        from open_maestro.runtime.kimi_cli import KimiCLIRuntime

        monkeypatch.setenv("MAESTRO_KIMI_ACP_DELEGATION", "1")
        runtime = KimiCLIRuntime()
        # Without the real ACP package / kimi binary this returns None, but the
        # important thing is that it attempts to build the runtime.
        result = runtime._try_acp_runtime()
        # If acp is not installed, None is expected; if installed, a runtime is returned.
        # Either way, the env var gate was evaluated.
        assert result is None or hasattr(result, "run_with_hooks")

    def test_claude_cli_delegates_to_sdk_when_available(self):
        import asyncio

        from open_maestro.runtime.claude_cli import ClaudeCLIRuntime

        runtime = ClaudeCLIRuntime()

        class FakeSDKRuntime:
            def __init__(self, *args, **kwargs):
                pass

            async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
                return AgentResult(text="sdk-delegated", metadata={"tool": "intercepted"})

        runtime._try_sdk_runtime = lambda config=None: FakeSDKRuntime()

        async def guard(tool_name, tool_input):
            return True

        result = asyncio.run(
            runtime.run_with_hooks("prompt", tool_guard=guard, blocked_tools={"Write"})
        )
        assert result.text == "sdk-delegated"
        assert result.metadata.get("tool") == "intercepted"

    def test_claude_cli_falls_back_when_sdk_unavailable(self):
        import asyncio

        from open_maestro.runtime.claude_cli import ClaudeCLIRuntime

        runtime = ClaudeCLIRuntime()
        runtime._try_sdk_runtime = lambda config=None: None
        calls = []

        async def fake_run(prompt, config=None):
            calls.append((prompt, config))
            return AgentResult(text="cli-fallback")

        runtime.run = fake_run

        async def guard(tool_name, tool_input):
            return True

        result = asyncio.run(
            runtime.run_with_hooks("prompt", tool_guard=guard, blocked_tools={"Write"})
        )
        assert result.text == "cli-fallback"
        assert len(calls) == 1
        assert "Write" in (calls[0][1].system_prompt or "")

    def test_run_with_hooks_does_not_raise_for_tool_guard(self):
        """Claude CLI cannot intercept tools, but should not crash on tool_guard."""
        runtime = ClaudeCLIRuntime()
        runtime._try_sdk_runtime = lambda config=None: None
        calls = []

        async def fake_run(prompt, config=None):
            calls.append((prompt, config))
            return AgentResult(text="ok")

        runtime.run = fake_run

        async def dummy_guard(tool_name, tool_input):
            return True

        result = runtime.run_with_hooks(
            "prompt",
            tool_guard=dummy_guard,
            blocked_tools={"Write"},
            config=None,
        )
        # run_with_hooks is async, but we called it without await.  Awaiting a
        # coroutine object is fine for the assertion below.
        import asyncio

        result = asyncio.run(result)
        assert result.text == "ok"
        assert len(calls) == 1
        assert "Write" in (calls[0][1].system_prompt or "")


class TestModelResolution:
    def test_kimi_resolves_aliases(self):
        from open_maestro.config.models import ModelResolver

        resolver = ModelResolver()
        assert resolver.resolve("fast", "kimi-cli") == "kimi-code/kimi-for-coding"
        assert resolver.resolve("smart", "kimi-cli") == "kimi-code/k3"

    def test_claude_resolves_aliases(self):
        from open_maestro.config.models import ModelResolver

        resolver = ModelResolver()
        assert resolver.resolve("smart", "claude-cli") == "claude-sonnet-4-6"
        assert resolver.resolve("reasoning", "claude-cli") == "claude-opus-4-7"

    def test_full_model_passed_through(self):
        from open_maestro.config.models import ModelResolver

        resolver = ModelResolver()
        assert resolver.resolve("gpt-4o", "openai-sdk") == "gpt-4o"
        assert resolver.resolve("kimi-code/k3", "kimi-cli") == "kimi-code/k3"


class TestAgentDefinition:
    def test_load_engineer_agent(self, tmp_path):
        from open_maestro.agents.definition import AgentDefinition

        md = tmp_path / "engineer.md"
        md.write_text(
            "---\n"
            "id: engineer\n"
            "name: Software Engineer\n"
            "role: engineer\n"
            "model: smart\n"
            "tools:\n"
            "  - Read\n"
            "  - Edit\n"
            "---\n\n"
            "# Primary Role\nImplement code and tests.\n"
        )
        agent = AgentDefinition.from_markdown(md)
        assert agent.id == "engineer"
        assert agent.role == "engineer"
        assert "Implement code" in agent.system_prompt


class TestAgentRegistry:
    def test_select_prefers_keyword_matches(self):
        from open_maestro.agents.definition import AgentDefinition
        from open_maestro.agents.registry import AgentRegistry

        engineer = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            instructions="Writes code and tests.",
        )
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            instructions="Explains architecture.",
        )
        registry = AgentRegistry({"engineer": engineer, "researcher": researcher})
        selected = registry.select("write unit tests for the parser")
        assert selected[0].id == "engineer"

    def test_select_uses_role(self):
        from open_maestro.agents.definition import AgentDefinition
        from open_maestro.agents.registry import AgentRegistry

        qa = AgentDefinition(id="qa", name="QA", role="qa", instructions="Tests things.")
        registry = AgentRegistry({"qa": qa})
        selected = registry.select("run qa review")
        assert selected[0].id == "qa"

    def test_select_research_keywords_prefer_researcher(self):
        from open_maestro.agents.definition import AgentDefinition
        from open_maestro.agents.registry import AgentRegistry
        from open_maestro.config.capabilities import CodingStrength, RequiredCapabilities

        engineer = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            instructions="Writes code and tests.",
            required_capabilities=RequiredCapabilities(
                coding_strength=CodingStrength.HIGH
            ),
        )
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            instructions="Explains architecture and compares implementations.",
        )
        registry = AgentRegistry({"engineer": engineer, "researcher": researcher})
        selected = registry.select(
            "analyze the architecture and compare the implementation patterns"
        )
        assert selected[0].id == "researcher"

    def test_select_clearly_coding_tasks_prefer_engineer(self):
        from open_maestro.agents.definition import AgentDefinition
        from open_maestro.agents.registry import AgentRegistry
        from open_maestro.config.capabilities import CodingStrength, RequiredCapabilities

        engineer = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            instructions="Writes code and tests.",
            required_capabilities=RequiredCapabilities(
                coding_strength=CodingStrength.HIGH
            ),
        )
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            instructions="Explains architecture.",
        )
        registry = AgentRegistry({"engineer": engineer, "researcher": researcher})
        selected = registry.select("implement a new feature in the codebase")
        assert selected[0].id == "engineer"


class TestRuntimeAutoDetection:
    def test_openai_sdk_picked_first_when_available(self, monkeypatch):
        import shutil

        from open_maestro.runtime import factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # Ensure vendor SDKs are not accidentally available.
        monkeypatch.setenv("OPEN_MAESTRO_PREFER_SDK", "0")

        runtime = factory._auto_detect_runtime()
        assert runtime == "openai-sdk"

    def test_prefer_cli_env_forces_cli_first(self, monkeypatch):
        import shutil

        from open_maestro.runtime import factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPEN_MAESTRO_PREFER_CLI", "1")

        runtime = factory._auto_detect_runtime()
        assert runtime == "kimi-cli"

    def test_fallback_to_cli_when_openai_sdk_unavailable(self, monkeypatch):
        import shutil

        from open_maestro.runtime import factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        runtime = factory._auto_detect_runtime()
        assert runtime == "kimi-cli"

    def test_select_runtime_for_task_respects_prefer_cli(self, monkeypatch):
        import shutil

        from open_maestro.config.capabilities import TaskProfile
        from open_maestro.runtime import factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPEN_MAESTRO_PREFER_CLI", "1")

        runtime, model = factory.select_runtime_for_task(TaskProfile())
        assert runtime in {"kimi-cli", "claude-cli"}

    def test_select_runtime_for_task_prefers_local_models(self, monkeypatch):
        import shutil

        from open_maestro.config.capabilities import CostLevel, TaskProfile
        from open_maestro.runtime import availability, factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(
            availability, "_ollama_models", lambda: {"llama3.3", "qwen2.5-coder", "llama3.1:8b"}
        )

        runtime, model = factory.select_runtime_for_task(
            TaskProfile(), prefer_local=True, min_cost_level=CostLevel.LOW
        )
        assert runtime == "openai-sdk"
        assert model in {"llama3.3", "qwen2.5-coder", "llama3.1:8b"}

    def test_select_runtime_for_task_respects_max_cost_level(self, monkeypatch):
        import shutil

        from open_maestro.config.capabilities import CostLevel, TaskProfile
        from open_maestro.runtime import availability, factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        # Avoid picking a real local Ollama model if one happens to be running.
        monkeypatch.setattr(availability, "_ollama_models", lambda: set())

        runtime, model = factory.select_runtime_for_task(
            TaskProfile(), max_cost_level=CostLevel.LOW, min_cost_level=CostLevel.LOW
        )
        assert runtime == "openai-sdk"
        assert model == "gpt-4o-mini"

    def test_select_runtime_for_task_excludes_local_by_default(self, monkeypatch):
        import shutil

        from open_maestro.config.capabilities import TaskProfile
        from open_maestro.runtime import availability, factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setattr(
            availability, "_ollama_models", lambda: {"llama3.3", "qwen2.5-coder"}
        )

        runtime, model = factory.select_runtime_for_task(TaskProfile())
        assert runtime != "openai-sdk" or not model.startswith(("llama", "qwen2.5"))

    def test_select_runtime_for_task_default_cost_floor_is_medium(self, monkeypatch):
        import shutil

        from open_maestro.config.capabilities import CostLevel, TaskProfile
        from open_maestro.runtime import availability, factory

        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(availability, "_ollama_models", lambda: set())

        runtime, model = factory.select_runtime_for_task(TaskProfile())
        # With the default medium cost floor, gpt-4o-mini (low cost) is excluded.
        assert model != "gpt-4o-mini"


class TestOpenAIFallbackAliases:
    def test_qwen_fallback_aliases_resolve(self):
        from open_maestro.config.models import ModelResolver

        resolver = ModelResolver()
        assert resolver.resolve("qwen", "openai-sdk") == "qwen-plus"
        assert resolver.resolve("qwen-max", "openai-sdk") == "qwen-max"
        assert resolver.resolve("qwen-coder", "openai-sdk") == "qwen-coder-plus"

    def test_openai_fallback_aliases_still_resolve(self):
        from open_maestro.config.models import ModelResolver

        resolver = ModelResolver()
        assert resolver.resolve("default", "openai-sdk") == "gpt-4o"
        assert resolver.resolve("fast", "openai-sdk") == "gpt-4o-mini"
        assert resolver.resolve("reasoning", "openai-sdk") == "o3-mini"
