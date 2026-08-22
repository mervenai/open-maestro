"""Tests for the interactive chat mode helpers."""

from __future__ import annotations

import asyncio

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.interactive import (
    InteractiveState,
    _assemble_prompt,
    _handle_command,
    _looks_like_decision,
    _resolve_suggested_prompt,
)


def _make_registry() -> AgentRegistry:
    return AgentRegistry(
        {
            "engineer": AgentDefinition(
                id="engineer",
                name="Engineer",
                role="engineer",
                instructions="Build things.",
            ),
            "researcher": AgentDefinition(
                id="researcher",
                name="Researcher",
                role="researcher",
                instructions="Research things.",
            ),
        }
    )


def _cmd(raw: str, state: InteractiveState, registry: AgentRegistry) -> str | None:
    return asyncio.run(_handle_command(raw, state, registry, memory=None))


def test_handle_command_exit() -> None:
    state = InteractiveState()
    registry = _make_registry()
    assert _cmd("/exit", state, registry) == "__EXIT__"
    assert _cmd("/quit", state, registry) == "__EXIT__"


def test_handle_command_agent_pin() -> None:
    state = InteractiveState()
    registry = _make_registry()
    assert _cmd("/agent engineer", state, registry) == (
        "Agent pinned to 'engineer' for this session."
    )
    assert state.agent_id == "engineer"


def test_handle_command_unknown_agent() -> None:
    state = InteractiveState()
    registry = _make_registry()
    result = _cmd("/agent designer", state, registry)
    assert "Unknown agent 'designer'" in result
    assert state.agent_id is None


def test_handle_command_model() -> None:
    state = InteractiveState()
    registry = _make_registry()
    assert _cmd("/model k3", state, registry) == (
        "Model override set to 'k3' for this session."
    )
    assert state.model == "k3"


def test_handle_command_toggles() -> None:
    state = InteractiveState()
    registry = _make_registry()
    assert _cmd("/reasoning", state, registry) == "Reasoning preference: on."
    assert state.reasoning is True
    assert _cmd("/reasoning", state, registry) == "Reasoning preference: off."
    assert state.reasoning is False

    assert _cmd("/fast", state, registry) == "Fast/cheap preference: on."
    assert state.fast is True

    assert state.chain is True
    assert _cmd("/chain", state, registry) == "Multi-agent chain mode: off."
    assert state.chain is False


def test_handle_command_plan_and_dry() -> None:
    state = InteractiveState()
    registry = _make_registry()
    assert _cmd("/plan", state, registry) == (
        "Next response will show the execution plan."
    )
    assert state.show_plan_next is True

    assert _cmd("/dry", state, registry) == "Next response will be a dry run."
    assert state.dry_run_next is True


def test_handle_command_reset() -> None:
    state = InteractiveState()
    state.history.append({"role": "user", "content": "hello"})
    state.session_id = "abc123"
    registry = _make_registry()
    assert _cmd("/reset", state, registry) == (
        "Conversation history and session cleared."
    )
    assert state.history == []
    assert state.session_id is None


def test_handle_command_normal_prompt_returns_none() -> None:
    state = InteractiveState()
    registry = _make_registry()
    assert _cmd("analyze this project", state, registry) is None


def test_handle_command_unknown_command() -> None:
    state = InteractiveState()
    registry = _make_registry()
    result = _cmd("/foobar", state, registry)
    assert "Unknown command '/foobar'" in result


def test_handle_command_remember_without_memory() -> None:
    state = InteractiveState()
    registry = _make_registry()
    result = _cmd("/remember this is important", state, registry)
    assert "Memory is not available" in result


def test_handle_command_memory_without_memory() -> None:
    state = InteractiveState()
    registry = _make_registry()
    result = _cmd("/memory auth", state, registry)
    assert "Memory is not available" in result


def test_assemble_prompt_without_history() -> None:
    assert _assemble_prompt("do work", []) == "Current task: do work"


def test_assemble_prompt_with_history() -> None:
    history = [
        {"role": "user", "content": "first task"},
        {"role": "assistant", "content": "first result"},
    ]
    prompt = _assemble_prompt("second task", history)
    assert "Conversation so far:" in prompt
    assert "User: first task" in prompt
    assert "Assistant: first result" in prompt
    assert "Current task: second task" in prompt


def test_looks_like_decision() -> None:
    assert _looks_like_decision("What is your recommendation?")
    assert _looks_like_decision("Decide which stack to use")
    assert not _looks_like_decision("Explain how this works")


def test_resolve_suggested_prompt_selects_by_number() -> None:
    prompts = [("First", "prompt one"), ("Second", "prompt two")]
    resolved, title = _resolve_suggested_prompt("1", prompts)
    assert resolved == "prompt one"
    assert title == "First"


def test_resolve_suggested_prompt_invalid_number() -> None:
    prompts = [("First", "prompt one")]
    resolved, title = _resolve_suggested_prompt("5", prompts)
    assert resolved == "5"
    assert title is None


def test_resolve_suggested_prompt_non_number() -> None:
    prompts = [("First", "prompt one")]
    resolved, title = _resolve_suggested_prompt("hello", prompts)
    assert resolved == "hello"
    assert title is None
