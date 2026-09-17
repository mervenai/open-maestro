"""Tests for the repo-path clarification gate in interactive mode."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

from open_maestro.interactive import (
    _cwd_has_source_files,
    _maybe_clarify_repo_path,
    _prompt_needs_code,
)

CODE_NEEDING_PROMPT = (
    "Read the PRD/RFP in the requirements/ folder and any linked documents. "
    "Produce a concise synthesis that covers:"
    "- Business goal and user value proposition\n"
    "- Scope IN/OUT (what is explicitly included and excluded)\n"
    "- Superseded decisions\n"
    "- Top 5-8 open questions that could block design or implementation\n"
    "- Any additional open questions (brief, uncapped — do not drop them)\n"
    "- Top 5-8 risks with proposed mitigations\n"
    "- Reuse opportunities in existing codebase or services."
)

PLAIN_PROMPT = (
    "Read the PRD/RFP in the requirements/ folder and produce a concise "
    "synthesis of the business goal, scope, open questions, and risks."
)


class TestPromptNeedsCode:
    def test_codebase_keyword_triggers(self):
        assert _prompt_needs_code("Search the codebase for existing capabilities")

    def test_reuse_keyword_triggers(self):
        assert _prompt_needs_code("List reuse opportunities in existing services")

    def test_repository_keyword_triggers(self):
        assert _prompt_needs_code("Verify assumptions against the repository")

    def test_plain_prd_prompt_does_not_trigger(self):
        assert not _prompt_needs_code(PLAIN_PROMPT)


class TestCwdHasSourceFiles:
    def test_empty_dir_has_no_source(self, tmp_path):
        assert _cwd_has_source_files(tmp_path) is False

    def test_docs_only_dir_has_no_source(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "readme.md").write_text("# docs")
        (tmp_path / "Requirements").mkdir()
        (tmp_path / "Requirements" / "PRD.md").write_text("prd")
        assert _cwd_has_source_files(tmp_path) is False

    def test_source_file_detected(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('hi')")
        assert _cwd_has_source_files(tmp_path) is True

    def test_nested_source_detected(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (nested / "app.ts").write_text("export {}")
        assert _cwd_has_source_files(tmp_path) is True

    def test_code_in_skipped_dirs_ignored(self, tmp_path):
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "site.py").write_text("# fake")
        node = tmp_path / "node_modules" / "pkg"
        node.mkdir(parents=True)
        (node / "index.js").write_text("// fake")
        assert _cwd_has_source_files(tmp_path) is False

    def test_non_code_extensions_ignored(self, tmp_path):
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "style.css").write_text("body {}")
        (tmp_path / "page.html").write_text("<html></html>")
        assert _cwd_has_source_files(tmp_path) is False


def _fake_questionary(result: str):
    """Minimal questionary stand-in returning *result* from any prompt."""

    class _Choice:
        def __init__(self, title=None, value=None, **kwargs):
            self.title = title
            self.value = value

    def _make_question(_message, **kwargs):
        question = types.SimpleNamespace()
        question.application = types.SimpleNamespace(
            run_async=AsyncMock(return_value=result),
            key_bindings=None,
        )
        return question

    module = types.SimpleNamespace(
        select=_make_question, text=_make_question, Choice=_Choice
    )
    return module


class TestPlaybookGate:
    async def test_playbook_code_prompt_in_docs_only_dir_asks(
        self, tmp_path, monkeypatch
    ):
        other = tmp_path / "elsewhere"
        other.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "questionary", _fake_questionary(str(other)))
        updated, path = await _maybe_clarify_repo_path(
            CODE_NEEDING_PROMPT, history=[], memory=None, from_playbook=True
        )
        assert path == other
        assert "[Clarified repo location:" in updated

    async def test_playbook_code_prompt_with_source_does_not_ask(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "app.py").write_text("print('hi')")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "questionary", _fake_questionary("/tmp/x"))
        updated, path = await _maybe_clarify_repo_path(
            CODE_NEEDING_PROMPT, history=[], memory=None, from_playbook=True
        )
        assert path is None
        assert updated == CODE_NEEDING_PROMPT

    async def test_playbook_plain_prompt_in_docs_only_dir_does_not_ask(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "questionary", _fake_questionary("/tmp/x"))
        updated, path = await _maybe_clarify_repo_path(
            PLAIN_PROMPT, history=[], memory=None, from_playbook=True
        )
        assert path is None
        assert updated == PLAIN_PROMPT

    async def test_non_playbook_repo_analysis_still_asks(
        self, tmp_path, monkeypatch
    ):
        other = tmp_path / "elsewhere"
        other.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "questionary", _fake_questionary(str(other)))
        updated, path = await _maybe_clarify_repo_path(
            "analyze the codebase", history=[], memory=None, from_playbook=False
        )
        assert path == other

    async def test_follow_up_still_skips_even_in_docs_only_dir(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setitem(sys.modules, "questionary", _fake_questionary("/tmp/x"))
        updated, path = await _maybe_clarify_repo_path(
            "why did you only clone the Core repo?",
            history=[{"role": "user", "content": "analyze the codebase"}],
            memory=None,
            from_playbook=False,
        )
        assert path is None
