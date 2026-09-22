"""Tests for source-load estimation and profile augmentation."""

from __future__ import annotations

from pathlib import Path

from open_maestro.config.capabilities import ReasoningLevel, TaskProfile
from open_maestro.config.models import ModelResolver
from open_maestro.orchestrator.load import (
    LoadLevel,
    SourceLoad,
    apply_source_load,
    estimate_source_load,
)


def _profile(**kwargs) -> TaskProfile:
    return TaskProfile(**kwargs)


def _write_milestones(project: Path, num_artifacts: int) -> None:
    artifacts = "\n".join(
        f"          - path: docs/artifact-{i}.md\n"
        f"            required: true\n"
        f"            detected: true"
        for i in range(num_artifacts)
    )
    (project / ".open-maestro").mkdir(parents=True, exist_ok=True)
    (project / ".open-maestro" / "milestones.yaml").write_text(
        "schema_version: '2.0'\n"
        "project_id: test\n"
        "project_path: /tmp/test\n"
        "epics:\n"
        "  - id: default\n"
        "    name: Default\n"
        "    order: 1\n"
        "    milestones:\n"
        "      - id: intake\n"
        "        name: Intake\n"
        "        order: 1\n"
        "        weight: 10\n"
        "        status: in_progress\n"
        f"        artifacts:\n{artifacts}\n"
    )


def _make_repo(project: Path, name: str) -> None:
    (project / name / ".git").mkdir(parents=True)


def test_prompt_paths_counted(tmp_path: Path) -> None:
    load = estimate_source_load(
        "Compare docs/a.md with docs/b.md and src/x.py", tmp_path
    )
    assert load.artifacts == 3
    assert load.level == LoadLevel.MEDIUM  # medium_artifacts default is 3


def test_prose_without_paths_is_low(tmp_path: Path) -> None:
    load = estimate_source_load("what is the status of the project?", tmp_path)
    assert load.artifacts == 0
    assert load.level == LoadLevel.LOW


def test_milestone_artifacts_counted(tmp_path: Path) -> None:
    _write_milestones(tmp_path, num_artifacts=3)
    load = estimate_source_load("check the artifacts for consistency", tmp_path)
    assert load.artifacts == 3
    assert load.level == LoadLevel.MEDIUM


def test_milestones_ignored_when_absent(tmp_path: Path) -> None:
    load = estimate_source_load("check the artifacts for consistency", tmp_path)
    assert load.level == LoadLevel.LOW


def test_repos_counted(tmp_path: Path) -> None:
    _make_repo(tmp_path, "repo-one")
    _make_repo(tmp_path, "repo-two")
    load = estimate_source_load("summarize the repos", tmp_path)
    assert load.repos == 2
    assert load.level == LoadLevel.MEDIUM


def test_high_load_combined(tmp_path: Path) -> None:
    _write_milestones(tmp_path, num_artifacts=3)
    _make_repo(tmp_path, "repo-one")
    _make_repo(tmp_path, "repo-two")
    load = estimate_source_load("check the artifacts", tmp_path)
    assert load.level == LoadLevel.HIGH


def test_high_load_from_memories(tmp_path: Path) -> None:
    load = estimate_source_load("any prompt", tmp_path, memories=25)
    assert load.level == LoadLevel.HIGH


def test_apply_low_is_noop() -> None:
    profile = _profile()
    out = apply_source_load(profile, SourceLoad())
    assert out is profile


def test_apply_medium_never_bumps_reasoning() -> None:
    profile = _profile(context_tokens_estimate=8000)
    out = apply_source_load(
        profile, SourceLoad(artifacts=3, level=LoadLevel.MEDIUM)
    )
    assert out.reasoning_depth == ReasoningLevel.LIGHT
    assert out.context_tokens_estimate == 64000


def test_apply_high_bumps_reasoning_and_context() -> None:
    profile = _profile(context_tokens_estimate=8000)
    out = apply_source_load(
        profile, SourceLoad(artifacts=6, level=LoadLevel.HIGH)
    )
    assert out.reasoning_depth == ReasoningLevel.DEEP
    assert out.context_tokens_estimate == 128000


def test_apply_never_lowers() -> None:
    profile = _profile(
        reasoning_depth=ReasoningLevel.DEEP, context_tokens_estimate=256000
    )
    out = apply_source_load(
        profile, SourceLoad(artifacts=6, level=LoadLevel.HIGH)
    )
    assert out.reasoning_depth == ReasoningLevel.DEEP
    assert out.context_tokens_estimate == 256000


def test_apply_respects_reasoning_override() -> None:
    profile = _profile()
    out = apply_source_load(
        profile,
        SourceLoad(artifacts=6, level=LoadLevel.HIGH),
        reasoning_overridden=True,
    )
    assert out.reasoning_depth == ReasoningLevel.LIGHT
    assert out.context_tokens_estimate == 128000


def test_thresholds_user_editable(tmp_path: Path, monkeypatch) -> None:
    cfg_dir = tmp_path / ".open-maestro"
    cfg_dir.mkdir()
    (cfg_dir / "capabilities.yaml").write_text(
        "routing:\n  load_thresholds:\n    medium_artifacts: 2\n"
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    load = estimate_source_load("see docs/a.md and docs/b.md", tmp_path)
    assert load.level == LoadLevel.MEDIUM  # would be LOW with defaults


def test_glm_flash_excluded_under_high_load() -> None:
    from open_maestro.config.capabilities import _score_model
    from open_maestro.config.models import ModelResolver

    resolver = ModelResolver()
    glm = next(
        (m for m in resolver._registry.list_models() if m.id == "glm-5-3-flash"),
        None,
    )
    assert glm is not None

    # Light profile: glm-flash passes the capability bar.
    assert _score_model(glm, _profile()) is not None

    # High-load profile: the deep-reasoning hard bar excludes it outright.
    high = apply_source_load(
        _profile(), SourceLoad(artifacts=6, level=LoadLevel.HIGH)
    )
    assert high.reasoning_depth == ReasoningLevel.DEEP
    assert _score_model(glm, high) is None
