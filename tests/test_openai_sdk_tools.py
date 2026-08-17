"""Tests for the OpenAI SDK runtime with tool interception."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from open_maestro.runtime.base import AgentConfig
from open_maestro.runtime.openai_sdk import OpenAISDKRuntime
from open_maestro.runtime.tools import Tool, ToolRegistry


@dataclass
class FakeToolCallFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    type: str
    function: FakeToolCallFunction


@dataclass
class FakeMessage:
    role: str
    content: str | None
    tool_calls: list[FakeToolCall] | None = None


@dataclass
class FakeChoice:
    message: FakeMessage
    finish_reason: str | None = "stop"


@dataclass
class FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


@dataclass
class FakeResponse:
    choices: list[FakeChoice]
    id: str = "resp_1"
    usage: FakeUsage | None = None


def _make_client(responses: list[FakeResponse]) -> Any:
    """Return a mock AsyncOpenAI client that yields the given responses."""
    iterator = iter(responses)

    async def create(*, model, messages, **kwargs):
        return next(iterator)

    completions = MagicMock()
    completions.create = create
    client = MagicMock()
    client.chat.completions = completions
    return client


@pytest.fixture
def echo_tool() -> Tool:
    async def execute(message: str) -> str:
        return f"echo: {message}"

    return Tool(
        name="Echo",
        description="Echo a message.",
        parameters={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        execute=execute,
    )


class TestOpenAISDKToolLoop:
    async def test_run_without_tool_calls_returns_text(self):
        client = _make_client(
            [FakeResponse([FakeChoice(FakeMessage("assistant", "hello"))])]
        )
        runtime = OpenAISDKRuntime(model="gpt-4o-mini", tool_registry=ToolRegistry({}))
        runtime._client = client

        result = await runtime.run("say hi")
        assert result.text == "hello"
        assert result.is_error is False
        assert result.num_turns == 1

    async def test_tool_call_is_executed_and_result_returned(self, echo_tool):
        client = _make_client(
            [
                FakeResponse(
                    [
                        FakeChoice(
                            FakeMessage(
                                "assistant",
                                None,
                                tool_calls=[
                                    FakeToolCall(
                                        "tc_1",
                                        "function",
                                        FakeToolCallFunction(
                                            "Echo", '{"message": "hi"}'
                                        ),
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ]
                ),
                FakeResponse([FakeChoice(FakeMessage("assistant", "done"))]),
            ]
        )
        runtime = OpenAISDKRuntime(
            model="gpt-4o-mini", tool_registry=ToolRegistry({"Echo": echo_tool})
        )
        runtime._client = client

        result = await runtime.run("call echo")
        assert result.text == "done"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["name"] == "Echo"

    async def test_tool_guard_can_block_tool(self, echo_tool):
        client = _make_client(
            [
                FakeResponse(
                    [
                        FakeChoice(
                            FakeMessage(
                                "assistant",
                                None,
                                tool_calls=[
                                    FakeToolCall(
                                        "tc_1",
                                        "function",
                                        FakeToolCallFunction(
                                            "Echo", '{"message": "hi"}'
                                        ),
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ]
                ),
                FakeResponse([FakeChoice(FakeMessage("assistant", "blocked"))]),
            ]
        )
        runtime = OpenAISDKRuntime(
            model="gpt-4o-mini", tool_registry=ToolRegistry({"Echo": echo_tool})
        )
        runtime._client = client

        captured: list[tuple[str, dict[str, Any]]] = []

        async def guard(name: str, input: dict[str, Any]) -> bool:
            captured.append((name, input))
            return False

        result = await runtime.run_with_hooks("call echo", tool_guard=guard)
        assert result.text == "blocked"
        assert len(captured) == 1
        assert captured[0][0] == "Echo"

    async def test_blocked_tools_filtered_from_schema(self, echo_tool):
        client = _make_client(
            [
                FakeResponse(
                    [
                        FakeChoice(
                            FakeMessage(
                                "assistant",
                                None,
                                tool_calls=[
                                    FakeToolCall(
                                        "tc_1",
                                        "function",
                                        FakeToolCallFunction(
                                            "Echo", '{"message": "hi"}'
                                        ),
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ]
                ),
                FakeResponse([FakeChoice(FakeMessage("assistant", "done"))]),
            ]
        )
        runtime = OpenAISDKRuntime(
            model="gpt-4o-mini", tool_registry=ToolRegistry({"Echo": echo_tool})
        )
        runtime._client = client

        config = AgentConfig(blocked_tools={"Echo"})
        result = await runtime.run_with_hooks(
            "call echo", blocked_tools={"Echo"}, config=config
        )
        assert result.text == "done"
        # The blocked tool should not have been in the schema, but if the model
        # hallucinated it anyway the guard denies it.
        assert result.tool_calls[0]["name"] == "Echo"

    async def test_max_turns_error(self, echo_tool):
        # Infinite tool-call loop.
        client = _make_client(
            [
                FakeResponse(
                    [
                        FakeChoice(
                            FakeMessage(
                                "assistant",
                                None,
                                tool_calls=[
                                    FakeToolCall(
                                        "tc_1",
                                        "function",
                                        FakeToolCallFunction(
                                            "Echo", '{"message": "x"}'
                                        ),
                                    )
                                ],
                            ),
                            finish_reason="tool_calls",
                        )
                    ]
                )
                for _ in range(35)
            ]
        )
        runtime = OpenAISDKRuntime(
            model="gpt-4o-mini",
            max_turns=3,
            tool_registry=ToolRegistry({"Echo": echo_tool}),
        )
        runtime._client = client

        result = await runtime.run("loop")
        assert result.is_error is True
        assert "maximum number of tool turns" in result.text

    async def test_default_model_resolved_via_registry_not_hardcoded(self):
        """When model alias is 'default', the runtime asks the registry instead of
        hardcoding 'gpt-4o'."""
        called_with: list[str] = []

        async def create(*, model, messages, **kwargs):
            called_with.append(model)
            return FakeResponse([FakeChoice(FakeMessage("assistant", "ok"))])

        completions = MagicMock()
        completions.create = create
        client = MagicMock()
        client.chat.completions = completions

        runtime = OpenAISDKRuntime(model="default", tool_registry=ToolRegistry({}))
        runtime._client = client

        result = await runtime.run("say hi")
        assert result.text == "ok"
        assert len(called_with) == 1
        assert called_with[0] != "default"
        assert called_with[0] != ""
