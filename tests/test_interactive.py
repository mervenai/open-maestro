"""Tests for the interactive chat mode helpers."""

from __future__ import annotations

import asyncio
import sys
import types

from prompt_toolkit import HTML
from prompt_toolkit.formatted_text import to_formatted_text

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.session.store import SessionRecord, SessionStore
import pytest

from open_maestro.interactive import (
    InteractiveState,
    _assemble_prompt,
    _echo_user_prompt,
    _extract_remote_urls,
    _handle_command,
    _looks_like_decision,
    _maybe_clarify_repo_path,
    _resolve_suggested_prompt,
    _handoff_excerpt,
    _is_execution_follow_up,
    _restore_latest_session,
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
    prompts = [
        ("id-1", "First", "prompt one"),
        ("id-2", "Second", "prompt two"),
    ]
    resolved, title, prompt_id = _resolve_suggested_prompt("1", prompts)
    assert resolved == "prompt one"
    assert title == "First"
    assert prompt_id == "id-1"


def test_resolve_suggested_prompt_invalid_number() -> None:
    prompts = [("id-1", "First", "prompt one")]
    resolved, title, prompt_id = _resolve_suggested_prompt("5", prompts)
    assert resolved == "5"
    assert title is None
    assert prompt_id is None


def test_resolve_suggested_prompt_non_number() -> None:
    prompts = [("id-1", "First", "prompt one")]
    resolved, title, prompt_id = _resolve_suggested_prompt("hello", prompts)
    assert resolved == "hello"
    assert title is None
    assert prompt_id is None


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


class TestEchoUserPrompt:
    """Bold/colored echo of user prompts, with plain fallbacks."""

    def test_plain_output_when_not_tty(self, monkeypatch, capsys):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        _echo_user_prompt("hello world")
        out = capsys.readouterr().out
        assert out == "> hello world\n"
        assert "\x1b" not in out

    def test_plain_output_when_no_color_set(self, monkeypatch, capsys):
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        monkeypatch.setenv("NO_COLOR", "1")
        _echo_user_prompt("hello world")
        out = capsys.readouterr().out
        assert out == "> hello world\n"
        assert "\x1b" not in out

    def test_bold_colored_output_when_tty(self, monkeypatch):
        calls: list = []

        def fake_print_formatted_text(formatted_text, **_kwargs):
            calls.append(formatted_text)

        fake_pt = types.SimpleNamespace(
            HTML=HTML, print_formatted_text=fake_print_formatted_text
        )
        monkeypatch.setitem(sys.modules, "prompt_toolkit", fake_pt)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        monkeypatch.delenv("NO_COLOR", raising=False)
        _echo_user_prompt("ship it <now>")
        assert len(calls) == 1
        fragments = to_formatted_text(calls[0])
        tokens = {tok for style, _ in fragments for tok in style.split(",")}
        text = "".join(fragment for _, fragment in fragments)
        # prompt_toolkit maps <b> to the style token "b".
        assert "b" in tokens
        assert "class:ansiyellow" in tokens
        assert text.startswith("> ")
        assert "ship it" in text
        # HTML metacharacters in the input must not break the markup.
        assert "<now>" in text

    def test_color_override_via_env(self, monkeypatch):
        calls: list = []

        def fake_print_formatted_text(formatted_text, **_kwargs):
            calls.append(formatted_text)

        fake_pt = types.SimpleNamespace(
            HTML=HTML, print_formatted_text=fake_print_formatted_text
        )
        monkeypatch.setitem(sys.modules, "prompt_toolkit", fake_pt)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        monkeypatch.setenv("MAESTRO_PROMPT_COLOR", "ansigreen")
        monkeypatch.delenv("NO_COLOR", raising=False)
        _echo_user_prompt("hello")
        assert len(calls) == 1
        fragments = to_formatted_text(calls[0])
        tokens = {tok for style, _ in fragments for tok in style.split(",")}
        assert "class:ansigreen" in tokens


class TestExtractRemoteUrls:
    def test_linear_document_url_is_ignored(self):
        assert (
            _extract_remote_urls(
                "comments go to "
                "https://linear.app/merven-ai/document/AM-123-blueprint-notes"
            )
            == []
        )

    def test_google_docs_url_is_ignored(self):
        assert (
            _extract_remote_urls(
                "see https://docs.google.com/document/d/abc123-def-456 for details"
            )
            == []
        )

    def test_github_url_is_kept(self):
        assert _extract_remote_urls("check https://github.com/org/repo please") == [
            "https://github.com/org/repo"
        ]

    def test_github_url_with_git_suffix_is_kept(self):
        assert (
            _extract_remote_urls("clone https://github.com/org/repo.git now")
            == ["https://github.com/org/repo.git"]
        )

    def test_scp_style_git_url_is_kept(self):
        assert (
            _extract_remote_urls("use git@github.com:org/repo.git here")
            == ["git@github.com:org/repo.git"]
        )

    def test_mixed_prompt_keeps_only_git_remote(self):
        prompt = (
            "compare the drafts and file the result at "
            "https://linear.app/merven-ai/document/AM-123-notes while cloning "
            "https://github.com/org/repo"
        )
        assert _extract_remote_urls(prompt) == ["https://github.com/org/repo"]


def test_clarify_repo_path_ignores_document_url():
    # MSTRO-110: a local comparison task that merely *links* a Linear document
    # as a destination for comments must not trigger repo clarification.
    prompt = (
        "Compare the new 3.1 version of the blueprint with the adversarial "
        "assessment and write up this analysis in a md file. Afterwards, share "
        "the summary as a comment on "
        "https://linear.app/merven-ai/document/AM-123-blueprint-notes"
    )
    result = asyncio.run(_maybe_clarify_repo_path(prompt, [], None))
    assert result == (prompt, None)


def test_clarify_repo_path_ignores_adversarial_review_noun():
    # MSTRO-110: the user's verbatim prompt — "adversarial review" uses
    # "review" as a noun and the Linear link is a document, not a git remote;
    # no repo clarification may fire.
    prompt = (
        "with the new 3.1 version of the blueprint compare to the adversarial "
        "review provided here. For sections C and D, specifically log which "
        "items are agreed upon and which items are contested and why. Write "
        "up this analysis in a md file. Later this md file will be used to "
        "provide in-line comments on the file: "
        "https://linear.app/merven-ai/document/am-resolution-pack-for-blueprint-v3-mer-11-63b550481705"
    )
    result = asyncio.run(_maybe_clarify_repo_path(prompt, [], None))
    assert result == (prompt, None)


def test_clarify_repo_path_proceeds_for_git_remote(monkeypatch):
    # A real git remote URL in the prompt must still lead to clarification
    # (i.e. the questionary prompt), not an immediate (prompt, None) return.
    class _ClarificationReached(Exception):
        pass

    fake_questionary = types.ModuleType("questionary")
    fake_questionary.Choice = lambda **kwargs: kwargs
    fake_questionary.select = lambda *args, **kwargs: (_ for _ in ()).throw(
        _ClarificationReached()
    )
    monkeypatch.setitem(sys.modules, "questionary", fake_questionary)

    prompt = "Please analyze https://github.com/org/repo and summarize it"
    with pytest.raises(_ClarificationReached):
        asyncio.run(_maybe_clarify_repo_path(prompt, [], None))


def _make_record(session_id: str, runtime: str, **kwargs) -> SessionRecord:
    from datetime import datetime, timezone

    return SessionRecord(
        session_id=session_id,
        runtime_name=runtime,
        agent_id="ticketing",
        model="claude-haiku-4-5",
        prompt_summary="task",
        created_at=kwargs.get("created_at", datetime.now(timezone.utc)),
        updated_at=kwargs.get("updated_at", datetime.now(timezone.utc)),
    )


def test_restore_latest_session_sets_state(tmp_path):
    # MSTRO-111: the most recent project session is restored into state so
    # the next turn resumes the backend session across maestro restarts.
    store = SessionStore(base_dirs=[tmp_path])
    store.save(_make_record("sess-1", "claude-cli"))
    state = InteractiveState()
    record = _restore_latest_session(state, store)
    assert record is not None
    assert state.session_id == "sess-1"
    assert state.session_runtime == "claude-cli"
    # And the restored session makes the turn a native resume (no history
    # re-injection) for a matching runtime.
    assert _turn_includes_history(state, "claude-cli") is False


def test_restore_latest_session_prefers_most_recent(tmp_path):
    from datetime import datetime, timezone

    store = SessionStore(base_dirs=[tmp_path])
    older = datetime(2026, 9, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 29, tzinfo=timezone.utc)
    store.save(_make_record("sess-old", "kimi-cli", updated_at=older))
    store.save(_make_record("sess-new", "claude-cli", updated_at=newer))
    state = InteractiveState()
    _restore_latest_session(state, store)
    assert state.session_id == "sess-new"


def test_restore_latest_session_empty_store(tmp_path):
    store = SessionStore(base_dirs=[tmp_path])
    state = InteractiveState()
    assert _restore_latest_session(state, store) is None
    assert state.session_id is None
    assert state.session_runtime is None


def test_execution_follow_up_matcher() -> None:
    assert _is_execution_follow_up("ok, proceed with recommendation next steps")
    assert _is_execution_follow_up("go ahead")
    assert _is_execution_follow_up("continue with the remaining numbers")
    assert _is_execution_follow_up("execute the 6 rebasing tasks")
    # Long prompts carry their own context.
    assert not _is_execution_follow_up(
        "proceed with the next steps " + "and also analyze " * 100
    )
    # Questions about prior work are not execution requests.
    assert not _is_execution_follow_up("where did the document editing leave off?")


def test_handoff_excerpt_prefers_recommendations_section() -> None:
    prior = (
        "# Report\n\nlots of findings text here.\n\n"
        "## Recommendations\n\n1. Regenerate quotedText verbatim\n"
        "2. Post the 10 critical comments first\n"
    )
    excerpt = _handoff_excerpt(prior)
    assert excerpt.startswith("## Recommendations")
    assert "Regenerate quotedText verbatim" in excerpt


def test_handoff_excerpt_falls_back_to_tail() -> None:
    prior = "x" * 10000
    excerpt = _handoff_excerpt(prior)
    assert len(excerpt) == 4000
    assert excerpt == prior[-4000:]


def test_restore_latest_session_keeps_prior_output(tmp_path):
    from datetime import datetime, timezone

    store = SessionStore(base_dirs=[tmp_path])
    store.save(
        _make_record(
            "sess-out",
            "kimi-cli",
            updated_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )
    )
    rec = store.get("sess-out")
    rec.last_output = "## Recommendations\n\n1. Do the thing\n"
    store.save(rec)
    state = InteractiveState()
    _restore_latest_session(state, store)
    assert state.prior_turn_output is not None
    assert "Do the thing" in state.prior_turn_output
