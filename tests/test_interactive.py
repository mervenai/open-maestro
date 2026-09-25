"""Tests for the interactive chat mode helpers."""

from __future__ import annotations

import asyncio

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.session.store import SessionRecord, SessionStore
from open_maestro.interactive import (
    InteractiveState,
    _assemble_prompt,
    _handle_command,
    _looks_like_decision,
    _resolve_suggested_prompt,
    _strip_plan_prefix,
    _turn_includes_history,
    _warn_if_artifact_missing,
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


def test_strip_plan_prefix_one_line_form() -> None:
    """'/plan <prompt>' on one line must arm the flag AND keep the prompt."""
    state = InteractiveState()
    remainder = _strip_plan_prefix(
        "/plan inspect the files in /docs and update what is stale", state
    )
    assert remainder == "inspect the files in /docs and update what is stale"
    assert state.show_plan_next is True

    state = InteractiveState()
    remainder = _strip_plan_prefix("/dry summarize the milestone status", state)
    assert remainder == "summarize the milestone status"
    assert state.dry_run_next is True


def test_strip_plan_prefix_leaves_other_input_alone() -> None:
    state = InteractiveState()
    # Bare command: no remainder; _handle_command shows the acknowledgment.
    assert _strip_plan_prefix("/plan", state) is None
    assert state.show_plan_next is False

    # Not the plan command at all.
    assert _strip_plan_prefix("/planx something", state) is None
    assert _strip_plan_prefix("just a normal prompt", state) is None

    # Command-like paths in the prompt body must not match.
    assert _strip_plan_prefix("write /docs/readme.md now", state) is None
    assert state.show_plan_next is False


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


def _session_store_for(tmp_path, monkeypatch) -> SessionStore:
    """Isolate SessionStore from the real home-dir sessions."""
    store = SessionStore(base_dirs=[tmp_path / ".open-maestro" / "sessions"])
    monkeypatch.setattr(
        "open_maestro.interactive.SessionStore", lambda *a, **k: store
    )
    return store


def test_status_command_renders_sessions_and_handoff(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone

    from open_maestro.interactive import _handle_status_command

    monkeypatch.chdir(tmp_path)
    om = tmp_path / ".open-maestro"
    (om / "sessions").mkdir(parents=True)

    store = _session_store_for(tmp_path, monkeypatch)
    store.save(
        SessionRecord(
            session_id="s1",
            runtime_name="openai-sdk",
            agent_id="engineer",
            model="glm-5.3-flash",
            prompt_summary="summarize the current milestone status",
            created_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 9, 20, 12, 30, tzinfo=timezone.utc),
        )
    )

    (om / "resume-log.md").write_text(
        "# Context-pressure resume log\n\n"
        "## Mission\n"
        "Verify the PRD.\n\n"
        "## Assigned agent\n"
        "- id: researcher\n"
    )

    out = _handle_status_command(tmp_path)
    assert out.startswith("# Where you left off")
    assert "No milestone plan found" in out
    assert "summarize the current milestone status" in out
    assert "[engineer/glm-5.3-flash]" in out
    assert "Verify the PRD." in out


def test_status_command_empty_project(tmp_path, monkeypatch) -> None:
    from open_maestro.interactive import _handle_status_command

    monkeypatch.chdir(tmp_path)
    _session_store_for(tmp_path, monkeypatch)
    out = _handle_status_command(tmp_path)
    assert out.startswith("# Where you left off")
    assert "No milestone plan found" in out
    assert "No prior sessions recorded." in out


def test_turn_includes_history_no_session() -> None:
    state = InteractiveState()
    assert _turn_includes_history(state, "kimi-cli") is True


def test_turn_includes_history_runtime_mismatch() -> None:
    state = InteractiveState()
    state.session_id = "abc-123"
    state.session_runtime = "kimi-cli"
    assert _turn_includes_history(state, "claude-cli") is True


def test_turn_includes_history_resume_broken_kimi(monkeypatch) -> None:
    from open_maestro.runtime import kimi_cli

    monkeypatch.setattr(kimi_cli, "_RESUME_BROKEN", True)
    state = InteractiveState()
    state.session_id = "abc-123"
    state.session_runtime = "kimi-cli"
    assert _turn_includes_history(state, "kimi-cli") is True


def test_turn_includes_history_healthy_same_runtime_session(monkeypatch) -> None:
    from open_maestro.runtime import kimi_cli

    monkeypatch.setattr(kimi_cli, "_RESUME_BROKEN", False)
    state = InteractiveState()
    state.session_id = "abc-123"
    state.session_runtime = "kimi-cli"
    assert _turn_includes_history(state, "kimi-cli") is False


def test_turn_includes_history_non_kimi_same_runtime() -> None:
    # Non-kimi runtimes with a healthy same-runtime session resume natively
    # too, so the transcript must not be duplicated either.
    state = InteractiveState()
    state.session_id = "abc-123"
    state.session_runtime = "claude-cli"
    assert _turn_includes_history(state, "claude-cli") is False


class TestArtifactMissingWarning:
    """Guardrail: flag turns whose result is a no-write handoff."""

    def test_flags_handoff_when_file_missing(self, tmp_path):
        prompt = "Draft the contract.\nWrite the output to docs/contract.md."
        result = (
            "No artifact written (read-only worker). "
            "Handing off to the lead agent to merge into docs/contract.md."
        )
        warning = _warn_if_artifact_missing(prompt, result, tmp_path)
        assert warning is not None
        assert "docs/contract.md" in warning

    def test_silent_when_file_exists(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "contract.md").write_text("content", encoding="utf-8")
        prompt = "Draft the contract.\nWrite the output to docs/contract.md."
        result = "No artifact written (read-only worker). Handing off to the lead."
        assert _warn_if_artifact_missing(prompt, result, tmp_path) is None

    def test_silent_on_normal_completion(self, tmp_path):
        prompt = "Draft the contract.\nWrite the output to docs/contract.md."
        result = "Contract drafted and written."
        assert _warn_if_artifact_missing(prompt, result, tmp_path) is None

    def test_silent_without_artifact_target(self, tmp_path):
        prompt = "Just answer this question."
        result = "Handing off to the lead agent."
        assert _warn_if_artifact_missing(prompt, result, tmp_path) is None

    def test_silent_when_target_unresolved(self, tmp_path):
        prompt = "Draft it.\nWrite the output to docs/contract-{epic_id}.md."
        result = "No artifact written (read-only worker)."
        assert _warn_if_artifact_missing(prompt, result, tmp_path) is None
