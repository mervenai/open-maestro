"""Tests for dossier open-items discovery and prompt formatting.

Why: Agents must never silently ignore open dossier decisions (MSTRO-104);
these tests prove dossier files are discovered, open items extracted, and
the prompt block formatted and truncated safely.
What: Tests ``discover_dossier_files``, ``extract_open_items``,
``format_dossier_context``, and the ``_build_prompt`` injection.
Test: ``uv run pytest tests/test_dossier.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.milestones import (
    discover_dossier_files,
    extract_open_items,
    format_dossier_context,
)
from open_maestro.orchestrator.pm import OrchestrationContext, ProjectManager

DOSSIER = """\
# Intake Dossier

## Resolved items
- B1: intake scope approved (resolved)
- [x] B2: stakeholder list confirmed

## Open items
- B3: contract schema TBD, changes frozen structure
- B4: pending vendor decision on export format
- B5: gateway count still open

### Decided
- H1: naming convention settled
"""


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary project path."""
    return tmp_path


def _write_dossier(project: Path, content: str = DOSSIER) -> Path:
    dossier = project / "docs" / "intake" / "dossier.md"
    dossier.parent.mkdir(parents=True, exist_ok=True)
    dossier.write_text(content)
    return dossier


class TestDiscoverDossierFiles:
    def test_finds_dossier_and_decision_files(self, tmp_project: Path) -> None:
        dossier = _write_dossier(tmp_project)
        decision = tmp_project / "docs" / "DECISIONS.md"
        decision.write_text("# Decisions\n")
        unrelated = tmp_project / "docs" / "readme.md"
        unrelated.write_text("# Readme\n")

        found = discover_dossier_files(tmp_project)

        assert found == sorted([dossier, decision])

    def test_ignores_unrelated_docs_and_skipped_dirs(self, tmp_project: Path) -> None:
        dossier = _write_dossier(tmp_project)
        (tmp_project / "docs" / "notes.md").write_text("# Notes\n")
        skipped = tmp_project / "docs" / "node_modules" / "pkg" / "dossier.md"
        skipped.parent.mkdir(parents=True)
        skipped.write_text("# Package dossier\n")

        found = discover_dossier_files(tmp_project)

        assert found == [dossier]

    def test_returns_empty_when_no_docs_dir(self, tmp_project: Path) -> None:
        assert discover_dossier_files(tmp_project) == []


class TestExtractOpenItems:
    def test_pulls_pending_items_and_skips_resolved(self, tmp_project: Path) -> None:
        items = extract_open_items(_write_dossier(tmp_project))

        assert any("B3" in item for item in items)
        assert any("B4" in item for item in items)
        assert any("B5" in item for item in items)
        assert not any("B1" in item for item in items)
        assert not any("B2" in item for item in items)
        assert not any("H1" in item for item in items)

    def test_heading_with_open_word_is_an_item(self, tmp_project: Path) -> None:
        path = tmp_project / "docs" / "decision-log.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "## Pending decision: canonical export format\n"
            "Some prose under the heading.\n"
        )

        assert extract_open_items(path) == [
            "Pending decision: canonical export format"
        ]

    def test_bullets_under_open_questions_heading_match_any_text(
        self, tmp_project: Path
    ) -> None:
        path = tmp_project / "docs" / "decision-log.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "## Open questions\n"
            "- Gateway naming\n"
            "- 2026-09-26 review scheduled\n"
        )

        items = extract_open_items(path)
        assert "Gateway naming" in items
        assert "2026-09-26 review scheduled" in items

    def test_long_items_are_truncated(self, tmp_project: Path) -> None:
        path = tmp_project / "docs" / "dossier.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"- open: {'x' * 400}\n")

        (item,) = extract_open_items(path)
        assert len(item) <= 200

    def test_returns_empty_when_nothing_open(self, tmp_project: Path) -> None:
        path = tmp_project / "docs" / "dossier.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("## Decided\n- B1: done (resolved)\n")

        assert extract_open_items(path) == []

    def test_missing_file_returns_empty(self, tmp_project: Path) -> None:
        assert extract_open_items(tmp_project / "docs" / "nope.md") == []


class TestFormatDossierContext:
    def test_empty_when_no_files(self, tmp_project: Path) -> None:
        assert format_dossier_context(tmp_project) == ""

    def test_empty_when_files_have_no_open_items(self, tmp_project: Path) -> None:
        _write_dossier(tmp_project, "## Decided\n- B1: done (resolved)\n")
        assert format_dossier_context(tmp_project) == ""

    def test_formats_items_with_relative_path(self, tmp_project: Path) -> None:
        _write_dossier(tmp_project)

        block = format_dossier_context(tmp_project)

        assert block.startswith("## Open items from dossier\n")
        assert "- B3: contract schema TBD, changes frozen structure" in block
        assert "(docs/intake/dossier.md)" in block

    def test_respects_max_chars_by_dropping_whole_lines(
        self, tmp_project: Path
    ) -> None:
        _write_dossier(tmp_project)

        # Header is 26 chars; the first item line is 77. A 104-char budget
        # fits exactly one item line and must drop the rest whole.
        block = format_dossier_context(tmp_project, max_chars=104)

        assert len(block) <= 104
        assert block.splitlines()[0] == "## Open items from dossier"
        lines = block.splitlines()
        assert len(lines) == 2
        assert "B3" in lines[1]


class TestBuildPromptInjection:
    def _build(self) -> str:
        ctx = OrchestrationContext(original_prompt="draft data contract")
        ctx.selected_agent = AgentDefinition(
            id="engineer", name="Engineer", role="engineer"
        )
        return ProjectManager._build_prompt(ctx)

    def test_prompt_includes_dossier_block(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_dossier(tmp_project)
        monkeypatch.chdir(tmp_project)

        prompt = self._build()

        assert "## Open items from dossier" in prompt
        assert "B3: contract schema TBD" in prompt

    def test_prompt_omits_dossier_block_without_open_items(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_project)

        prompt = self._build()

        assert "## Open items from dossier" not in prompt
