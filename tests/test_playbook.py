"""Tests for milestone prompt playbook support.

Why: The playbook is a new surface for milestone guidance; these tests prove
that prompts load, render placeholders, and integrate with command handlers.
What: Tests ``load_playbook``, ``get_prompts_for_milestone``, and command
formatting.
Test: ``uv run pytest tests/test_playbook.py``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from open_maestro.milestones.playbook import (
    PromptPlaybook,
    PromptTemplate,
    format_prompt_list,
    get_prompts_for_milestone,
    load_playbook,
)


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary project path."""
    return tmp_path


def test_load_default_playbook(tmp_project: Path) -> None:
    """Loading a project without an override returns the bundled playbook."""
    playbook = load_playbook(tmp_project)
    assert playbook.playbook_id == "software-consulting"
    assert "intake-discovery" in playbook.decks
    assert "implementation" in playbook.decks


def test_playbook_prompts_for_milestone_sorted() -> None:
    """prompts_for returns prompts sorted by order."""
    playbook = PromptPlaybook(
        playbook_id="test",
        version="1.0.0",
        source_project="",
        decks={
            "m1": type("Deck", (), {
                "milestone_id": "m1",
                "prompts": [
                    PromptTemplate(id="p2", title="Second", order=2, prompt="b"),
                    PromptTemplate(id="p1", title="First", order=1, prompt="a"),
                ],
            })()
        },
        placeholders={},
    )
    prompts = playbook.prompts_for("m1")
    assert [p.id for p in prompts] == ["p1", "p2"]


def test_prompt_template_renders_placeholders() -> None:
    """Prompt placeholders are replaced at render time."""
    template = PromptTemplate(
        id="t1",
        title="Test",
        order=1,
        prompt="Write {artifact_target} for {epic_name} on {date}.",
        artifact_target="docs/out-{date}.md",
    )
    rendered = template.render({
        "epic_name": "Import Flow",
        "date": "2026-08-22",
        "artifact_target": "docs/out-2026-08-22.md",
    })
    assert "Import Flow" in rendered
    assert "2026-08-22" in rendered


def test_get_prompts_for_milestone(tmp_project: Path) -> None:
    """get_prompts_for_milestone returns rendered (template, prompt) pairs."""
    pairs = get_prompts_for_milestone(
        tmp_project, "intake-discovery", plan=None, epic_id="import-flow"
    )
    assert len(pairs) > 0
    template, rendered = pairs[0]
    assert isinstance(template, PromptTemplate)
    assert isinstance(rendered, str)
    # artifact_target placeholder should be resolved in the rendered text.
    assert "{artifact_target}" not in rendered
    assert "{date}" not in rendered


def test_format_prompt_list_caps_and_shows_preview() -> None:
    """format_prompt_list respects max_prompts and includes a preview."""
    pairs = [
        (PromptTemplate(id=f"p{i}", title=f"T{i}", order=i, prompt=f"Do thing {i}."), f"Do thing {i}.")
        for i in range(1, 5)
    ]
    output = format_prompt_list(pairs, max_prompts=2)
    assert "1. T1" in output
    assert "2. T2" in output
    assert "and 2 more" in output


def test_format_prompt_list_empty() -> None:
    """format_prompt_list returns a friendly message when no prompts exist."""
    assert "No suggested prompts" in format_prompt_list([])
