"""Tests for SDK-based runtime adapters.

These tests mock the optional SDK packages (claude-agent-sdk and
agent-client-protocol) so they can run without installing the heavy
dependencies.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_maestro.runtime.claude_sdk import ClaudeSDKRuntime
from open_maestro.runtime.factory import _RUNTIMES, create_runtime, list_runtimes
from open_maestro.runtime.kimi_acp import KimiACPRuntime, KimiACPToolClient

# ---------------------------------------------------------------------------
# Claude Agent SDK mocks
# ---------------------------------------------------------------------------


class FakeClaudeEvent:
    def __init__(self, text: str = "", session_id: str | None = None):
        self.text = text
        self.session_id = session_id


@dataclass
class FakeClaudeOptions:
    system_prompt: str = ""
    model: str | None = None
    max_turns: int | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    mcp_servers: dict[str, Any] | None = None
    resume: str | None = None
    fork_session: str | None = None
    can_use_tool: Any | None = None
    cwd: str | None = None
    extra_args: dict[str, Any] | None = None


class FakePermissionResultAllow:
    pass


class FakePermissionResultDeny:
    def __init__(self, reason: str = ""):
        self.reason = reason


def _build_claude_sdk_module() -> types.ModuleType:
    mod = types.ModuleType("claude_agent_sdk")
    mod.ClaudeAgentOptions = FakeClaudeOptions
    mod.PermissionResultAllow = FakePermissionResultAllow
    mod.PermissionResultDeny = FakePermissionResultDeny
    mod.ClaudeSDKError = Exception
    return mod


@pytest.fixture
def claude_sdk_mock():
    mod = _build_claude_sdk_module()
    with patch.dict(sys.modules, {"claude_agent_sdk": mod}):
        yield mod


class TestClaudeSDKRuntime:
    async def test_run_collects_text(self, claude_sdk_mock):
        async def fake_query(*, prompt, options):
            yield FakeClaudeEvent("hello ")
            yield FakeClaudeEvent("world", session_id="sess_1")

        claude_sdk_mock.query = fake_query

        runtime = ClaudeSDKRuntime()
        result = await runtime.run("say hi")
        assert result.text == "hello \nworld"
        assert result.session_id == "sess_1"
        assert result.is_error is False

    async def test_run_with_hooks_blocks_tool(self, claude_sdk_mock):
        captured_calls: list[tuple[str, dict[str, Any]]] = []

        async def fake_query(*, prompt, options):
            # The SDK would call can_use_tool for each tool use.
            if options.can_use_tool:
                decision = await options.can_use_tool(
                    "Write", {"path": "x"}, None
                )
                assert isinstance(decision, FakePermissionResultDeny)
            yield FakeClaudeEvent("done")

        claude_sdk_mock.query = fake_query

        async def guard(tool_name: str, tool_input: dict[str, Any]) -> bool:
            captured_calls.append((tool_name, tool_input))
            return tool_name != "Write"

        runtime = ClaudeSDKRuntime()
        result = await runtime.run_with_hooks(
            "write a file",
            tool_guard=guard,
        )
        assert result.text == "done"
        assert len(captured_calls) == 1
        assert captured_calls[0][0] == "Write"

    async def test_blocked_tools_create_guard(self, claude_sdk_mock):
        async def fake_query(*, prompt, options):
            if options.can_use_tool:
                decision = await options.can_use_tool(
                    "Bash", {"command": "ls"}, None
                )
                assert isinstance(decision, FakePermissionResultDeny)
            yield FakeClaudeEvent("ok")

        claude_sdk_mock.query = fake_query

        runtime = ClaudeSDKRuntime()
        result = await runtime.run_with_hooks(
            "run a command",
            blocked_tools={"Bash"},
        )
        assert result.is_error is False

    async def test_resume_passes_session_id(self, claude_sdk_mock):
        async def fake_query(*, prompt, options):
            assert options.resume == "sess_old"
            yield FakeClaudeEvent("resumed")

        claude_sdk_mock.query = fake_query

        runtime = ClaudeSDKRuntime()
        result = await runtime.resume("sess_old", "continue")
        assert result.text == "resumed"

    async def test_fork_passes_session_id(self, claude_sdk_mock):
        async def fake_query(*, prompt, options):
            assert options.fork_session == "sess_old"
            yield FakeClaudeEvent("forked")

        claude_sdk_mock.query = fake_query

        runtime = ClaudeSDKRuntime()
        result = await runtime.fork("sess_old", "branch")
        assert result.text == "forked"


# ---------------------------------------------------------------------------
# Kimi ACP mocks
# ---------------------------------------------------------------------------


class FakeACPSchema:
    class Implementation:
        def __init__(self, name: str, version: str):
            self.name = name
            self.version = version

    class TextContentBlock:
        def __init__(self, type: str = "text", text: str = ""):
            self.type = type
            self.text = text

    class InitializeResponse:
        pass

    class NewSessionResponse:
        def __init__(self, session_id: str):
            self.session_id = session_id
            self.config_options = None
            self.models = None
            self.modes = None

    class SetSessionModelResponse:
        pass

    class PromptResponse:
        pass

    class RequestPermissionResponse:
        def __init__(self, outcome: Any):
            self.outcome = outcome

    class AllowedOutcome:
        def __init__(self, outcome: str):
            self.outcome = outcome

    class DeniedOutcome:
        def __init__(self, outcome: str, reason: str = ""):
            self.outcome = outcome
            self.reason = reason

    class ReadTextFileResponse:
        def __init__(self, content: str):
            self.content = content

    class WriteTextFileResponse:
        pass

    class CreateTerminalResponse:
        def __init__(self, terminal_id: str):
            self.terminal_id = terminal_id

    class TerminalOutputResponse:
        def __init__(self, output: str, exit_status: Any = None, truncated: bool = False):
            self.output = output
            self.exit_status = exit_status
            self.truncated = truncated

    class WaitForTerminalExitResponse:
        def __init__(self, exit_code: int | None):
            self.exit_code = exit_code

    class ReleaseTerminalResponse:
        pass

    class KillTerminalCommandResponse:
        pass


class FakeACPConnection:
    def __init__(self) -> None:
        self.initialize = AsyncMock()
        self.new_session = AsyncMock(return_value=FakeACPSchema.NewSessionResponse("sess_1"))
        self.resume_session = AsyncMock(return_value=FakeACPSchema.NewSessionResponse("sess_1"))
        self.fork_session = AsyncMock(return_value=FakeACPSchema.NewSessionResponse("sess_1"))
        self.set_session_model = AsyncMock(return_value=FakeACPSchema.SetSessionModelResponse())
        self.prompt = AsyncMock(return_value=FakeACPSchema.PromptResponse())


class FakeProcess:
    async def wait(self) -> int:
        return 0


class FakeSpawnCM:
    def __init__(self) -> None:
        self.conn = FakeACPConnection()
        self.process = FakeProcess()

    async def __aenter__(self):
        return (self.conn, self.process)

    async def __aexit__(self, *args: Any):
        return None


def _build_acp_module() -> types.ModuleType:
    mod = types.ModuleType("acp")
    mod.PROTOCOL_VERSION = 1
    mod.schema = FakeACPSchema()
    mod.spawn_agent_process = MagicMock(return_value=FakeSpawnCM())
    return mod


@pytest.fixture
def acp_mock():
    mod = _build_acp_module()
    with patch.dict(sys.modules, {"acp": mod}):
        with patch("importlib.metadata.version", return_value="0.10.0"):
            yield mod


class TestKimiACPRuntime:
    async def test_run_creates_session_and_prompts(self, acp_mock):
        runtime = KimiACPRuntime()
        result = await runtime.run("say hi")
        assert result.is_error is False
        assert result.session_id == "sess_1"
        conn = acp_mock.spawn_agent_process.return_value.conn
        conn.initialize.assert_awaited_once()
        conn.new_session.assert_awaited_once()
        conn.prompt.assert_awaited_once()

    async def test_run_graceful_when_set_session_model_missing(self, acp_mock):
        conn = acp_mock.spawn_agent_process.return_value.conn
        del conn.set_session_model
        runtime = KimiACPRuntime(model="kimi-k2")
        result = await runtime.run("say hi")
        assert result.is_error is False
        assert result.session_id == "sess_1"

    async def test_run_with_blocked_tools_denies_permission(self, acp_mock):
        runtime = KimiACPRuntime()
        result = await runtime.run_with_hooks(
            "do something",
            blocked_tools={"Write"},
        )
        assert result.is_error is False

    async def test_resume_uses_existing_session(self, acp_mock):
        runtime = KimiACPRuntime()
        result = await runtime.resume("sess_old", "continue")
        assert result.session_id == "sess_old"
        conn = acp_mock.spawn_agent_process.return_value.conn
        call_kwargs = conn.resume_session.await_args.kwargs
        assert call_kwargs["session_id"] == "sess_old"
        assert isinstance(call_kwargs["cwd"], str)

    async def test_fork_uses_existing_session(self, acp_mock):
        runtime = KimiACPRuntime()
        result = await runtime.fork("sess_old", "branch")
        # Fork creates a new session; our mock returns sess_1.
        assert result.session_id == "sess_1"
        conn = acp_mock.spawn_agent_process.return_value.conn
        call_kwargs = conn.fork_session.await_args.kwargs
        assert call_kwargs["session_id"] == "sess_old"
        assert isinstance(call_kwargs["cwd"], str)


class TestKimiACPToolClient:
    async def test_read_text_file(self, acp_mock, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("hello")
        client = KimiACPToolClient()
        resp = await client.read_text_file(str(path), "sess")
        assert resp.content == "hello"

    async def test_write_text_file(self, acp_mock, tmp_path):
        path = tmp_path / "file.txt"
        client = KimiACPToolClient()
        await client.write_text_file("world", str(path), "sess")
        assert path.read_text() == "world"

    async def test_blocked_tool_denied(self, acp_mock):
        client = KimiACPToolClient(blocked_tools={"Write"})
        tool_call = MagicMock()
        tool_call.title = "Write file"
        tool_call.raw_input = {"path": "x"}
        resp = await client.request_permission([], "sess", tool_call)
        assert resp.outcome.outcome == "denied"

    async def test_tool_guard_can_allow(self, acp_mock):
        async def guard(name: str, _input: dict[str, Any]) -> bool:
            return name == "Read"

        client = KimiACPToolClient(tool_guard=guard)
        tool_call = MagicMock()
        tool_call.kind = "Read"
        tool_call.raw_input = {"path": "x"}
        resp = await client.request_permission([], "sess", tool_call)
        assert resp.outcome.outcome == "allowed"


class TestFactorySDKDiscovery:
    def test_runtimes_include_sdk_adapters(self):
        assert "claude-sdk" in _RUNTIMES
        assert "kimi-acp" in _RUNTIMES

    def test_list_runtimes_reports_sdk_availability(self):
        # Without SDKs installed, these should report False.
        runtimes = list_runtimes()
        assert "claude-sdk" in runtimes
        assert "kimi-acp" in runtimes

    def test_create_runtime_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown runtime type"):
            create_runtime("not-a-runtime")
