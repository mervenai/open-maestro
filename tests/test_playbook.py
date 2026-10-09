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

from open_maestro.milestones.commands import get_current_or_next_milestone_prompts
from open_maestro.milestones.models import MilestoneStatus
from open_maestro.milestones.playbook import (
    PromptPlaybook,
    PromptTemplate,
    format_prompt_list,
    get_prompts_for_milestone,
    load_playbook,
)
from open_maestro.milestones.prompt_history import PromptHistoryStore
from open_maestro.milestones.store import MilestoneStore


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


def test_default_track_only_prompts_hidden_for_work_epics(tmp_project: Path) -> None:
    """Project-wide prompts are not offered under a work epic track."""
    pairs = get_prompts_for_milestone(
        tmp_project, "design-blueprint", plan=None, epic_id="ce1-data-foundation"
    )
    assert pairs == []

    # execution-planning keeps its per-epic prompt but hides plan-002.
    pairs = get_prompts_for_milestone(
        tmp_project, "execution-planning", plan=None, epic_id="ce1-data-foundation"
    )
    ids = [template.id for template, _ in pairs]
    assert "plan-001" in ids
    assert "plan-002" not in ids


def test_default_track_only_prompts_kept_for_default_track(tmp_project: Path) -> None:
    """The default track still sees every prompt, including flagged ones."""
    pairs = get_prompts_for_milestone(
        tmp_project, "design-blueprint", plan=None, epic_id="default"
    )
    ids = [template.id for template, _ in pairs]
    assert ids == [
        "design-001",
        "design-002",
        "design-003",
        "design-004",
        "design-005",
        "design-006",
    ]


def test_default_track_only_prompts_kept_without_epic(tmp_project: Path) -> None:
    """No epic context means no filtering."""
    pairs = get_prompts_for_milestone(tmp_project, "design-blueprint", plan=None)
    ids = [template.id for template, _ in pairs]
    assert "design-001" in ids


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


PLAYBOOK_WITH_AFTER = """\
playbook_id: test-after
version: 1.0.0
milestone_prompts:
  design-blueprint:
    - id: design-001
      title: Draft contract
      order: 1
      artifact_target: docs/blueprint-design-and-data-contract.md
      prompt: |
        Draft the contract.
    - id: design-002
      title: Adversarially verify the drafted contract
      order: 2
      after: design-001
      artifact_target: docs/adversarial-review.md
      prompt: |
        Review the contract.
"""


@pytest.fixture()
def gated_project(tmp_path: Path) -> Path:
    """Tmp project whose design milestone has an ``after``-gated prompt."""
    config_dir = tmp_path / ".open-maestro"
    config_dir.mkdir()
    (config_dir / "playbook.yaml").write_text(PLAYBOOK_WITH_AFTER, encoding="utf-8")
    store = MilestoneStore(tmp_path)
    plan = store.load()
    _epic, milestone = plan.find_milestone("design-blueprint")
    assert milestone is not None
    milestone.status = MilestoneStatus.IN_PROGRESS
    store.update(plan)
    return tmp_path


def test_load_playbook_populates_after_field(tmp_project: Path) -> None:
    """The optional ``after`` field populates PromptTemplate.after; default is None."""
    config_dir = tmp_project / ".open-maestro"
    config_dir.mkdir()
    (config_dir / "playbook.yaml").write_text(PLAYBOOK_WITH_AFTER, encoding="utf-8")
    playbook = load_playbook(tmp_project)
    prompts = {p.id: p for p in playbook.prompts_for("design-blueprint")}
    assert prompts["design-001"].after is None
    assert prompts["design-002"].after == "design-001"


def test_after_gated_prompt_hidden_until_prerequisite_runs(gated_project: Path) -> None:
    """No run record and no artifact: the gated prompt is excluded from /next."""
    prompts, epic_id, milestone_id = get_current_or_next_milestone_prompts(gated_project)
    assert epic_id == "default"
    assert milestone_id == "design-blueprint"
    assert [template.id for template, _ in prompts] == ["design-001"]


def test_after_gated_prompt_shown_with_run_record(gated_project: Path) -> None:
    """A run record for the prerequisite prompt ungates the gated prompt."""
    history = PromptHistoryStore(gated_project)
    history.record("default", "design-blueprint", "design-001", "Draft contract")
    prompts, _epic_id, _milestone_id = get_current_or_next_milestone_prompts(
        gated_project, prompt_history=history
    )
    assert [template.id for template, _ in prompts] == ["design-001", "design-002"]


def test_after_gated_prompt_shown_with_existing_artifact(gated_project: Path) -> None:
    """An existing artifact file for the prerequisite prompt ungates the gated prompt."""
    (gated_project / "docs").mkdir()
    (gated_project / "docs" / "blueprint-design-and-data-contract.md").write_text(
        "contract", encoding="utf-8"
    )
    history = PromptHistoryStore(gated_project)
    prompts, _epic_id, _milestone_id = get_current_or_next_milestone_prompts(
        gated_project, prompt_history=history
    )
    assert [template.id for template, _ in prompts] == ["design-001", "design-002"]


PLAYBOOK_WITH_READ_ONLY = """\
playbook_id: test-read-only
version: 1.0.0
milestone_prompts:
  design-blueprint:
    - id: design-002
      title: Adversarially verify the drafted contract
      order: 1
      read_only: true
      prompt: |
        Review the contract.
  implementation:
    - id: impl-001
      title: Scaffold endpoints
      order: 1
      prompt: |
        Implement the endpoints.
"""


def test_load_playbook_populates_read_only_field(tmp_project: Path) -> None:
    """The optional ``read_only`` field loads; default is False (MSTRO-109)."""
    config_dir = tmp_project / ".open-maestro"
    config_dir.mkdir()
    (config_dir / "playbook.yaml").write_text(
        PLAYBOOK_WITH_READ_ONLY, encoding="utf-8"
    )
    playbook = load_playbook(tmp_project)
    design = {p.id: p for p in playbook.prompts_for("design-blueprint")}
    impl = {p.id: p for p in playbook.prompts_for("implementation")}
    assert design["design-002"].read_only is True
    assert impl["impl-001"].read_only is False


def test_default_playbook_tags_analysis_prompts_read_only(tmp_project: Path) -> None:
    """The bundled playbook marks analysis/pre-dev prompts as read-only."""
    playbook = load_playbook(tmp_project)
    by_id = {
        p.id: p
        for deck in playbook.decks.values()
        for p in deck.prompts
    }
    assert by_id["intake-001"].read_only is True  # PRD synthesis
    assert by_id["intake-002"].read_only is True  # reuse assessment
    assert by_id["intake-003"].read_only is True  # risk register
    assert by_id["plan-001"].read_only is True  # verify architecture
    assert by_id["design-002"].read_only is False  # blueprint diagram (writes docs/diagrams/)
    assert by_id["design-006"].read_only is True  # final adversarial verification
    # Build/implementation/QA prompts stay mutating.
    assert by_id["intake-004"].read_only is False
    assert by_id["impl-001"].read_only is False
    assert by_id["qa-001"].read_only is False
