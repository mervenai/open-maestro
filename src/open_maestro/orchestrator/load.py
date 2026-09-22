"""Source-load estimation for task routing.

A task's true weight often comes from how many artifacts and sources it
touches (docs, milestone deliverables, cloned repos, recalled memories)
rather than from keywords in the prompt.  This module measures that load and
raises the task profile's requirements accordingly, so the capability
selector excludes cheap models that cannot actually carry the work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import yaml

from open_maestro.config.capabilities import ReasoningLevel, TaskProfile

logger = logging.getLogger(__name__)


class LoadLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SourceLoad:
    """Measured sources a task touches."""

    artifacts: int = 0
    repos: int = 0
    memories: int = 0
    level: LoadLevel = LoadLevel.LOW


# Default thresholds; overridable via a `routing.load_thresholds:` section in
# ~/.open-maestro/capabilities.yaml.
DEFAULT_THRESHOLDS: dict[str, int] = {
    "medium_artifacts": 3,
    "medium_repos": 2,
    "high_artifacts": 6,
    "high_artifacts_with_repos": 3,
    "high_repos_with_artifacts": 2,
    "high_memories": 25,
}

_ARTIFACT_PATH_RE = re.compile(
    r"[\w./-]+\.(?:md|markdown|ya?ml|json|jsonl|pdf|html?|txt|docx?|csv|"
    r"py|pyi|ts|tsx|js|jsx|vb|cs|sql|sh)",
    re.IGNORECASE,
)

_MAX_REPOS = 5


def _load_thresholds() -> dict[str, int]:
    path = Path.home() / ".open-maestro" / "capabilities.yaml"
    if not path.exists():
        return DEFAULT_THRESHOLDS
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        overrides = (data.get("routing") or {}).get("load_thresholds") or {}
    except Exception as exc:
        logger.debug("Failed to load routing thresholds: %s", exc)
        return DEFAULT_THRESHOLDS
    merged = dict(DEFAULT_THRESHOLDS)
    for key, value in overrides.items():
        if key in merged and isinstance(value, int):
            merged[key] = value
    return merged


def _prompt_artifact_paths(prompt: str) -> set[str]:
    return {m.group(0) for m in _ARTIFACT_PATH_RE.finditer(prompt)}


def _milestone_artifact_paths(project_path: Path) -> set[str]:
    """Artifact paths of the plan's current milestones, if a plan exists."""
    from open_maestro.milestones.store import MilestoneStore

    store = MilestoneStore(project_path)
    if not store.exists():
        return set()
    try:
        plan = store.load()
    except Exception as exc:
        logger.debug("Failed to load milestone plan for source load: %s", exc)
        return set()

    current = set(plan.summary.current_milestone_ids)
    paths: set[str] = set()
    for epic in plan.epics:
        for milestone in epic.milestones:
            # current_milestone_ids are epic-qualified ("epic/milestone");
            # accept both forms.
            if current and (
                milestone.id not in current
                and f"{epic.id}/{milestone.id}" not in current
            ):
                continue
            for artifact in milestone.artifacts:
                paths.add(artifact.path)
    return paths


def _count_repos(project_path: Path) -> int:
    """Count git repos up to depth 2 under the project root."""
    count = 0
    try:
        for child in project_path.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            if (child / ".git").exists():
                count += 1
            else:
                for grandchild in child.iterdir():
                    if grandchild.is_dir() and (grandchild / ".git").exists():
                        count += 1
            if count >= _MAX_REPOS:
                break
    except OSError as exc:
        logger.debug("Failed to count repos under %s: %s", project_path, exc)
    return min(count, _MAX_REPOS)


def estimate_source_load(
    prompt: str,
    project_path: str | Path,
    *,
    memories: int = 0,
) -> SourceLoad:
    """Measure the artifacts, repos, and memories a task touches."""
    project_path = Path(project_path)
    artifacts = len(_prompt_artifact_paths(prompt) | _milestone_artifact_paths(project_path))
    repos = _count_repos(project_path)
    t = _load_thresholds()

    level = LoadLevel.LOW
    if (
        artifacts >= t["high_artifacts"]
        or (artifacts >= t["high_artifacts_with_repos"] and repos >= t["high_repos_with_artifacts"])
        or memories >= t["high_memories"]
    ):
        level = LoadLevel.HIGH
    elif artifacts >= t["medium_artifacts"] or repos >= t["medium_repos"]:
        level = LoadLevel.MEDIUM

    return SourceLoad(artifacts=artifacts, repos=repos, memories=memories, level=level)


def apply_source_load(
    profile: TaskProfile,
    load: SourceLoad,
    *,
    reasoning_overridden: bool = False,
) -> TaskProfile:
    """Raise the profile's requirements to match measured source load.

    Never lowers any requirement.  MEDIUM load only raises the context
    estimate; HIGH load also requires deep reasoning, which is the hard bar
    that excludes light-reasoning cheap models (e.g. GLM-5.3-Flash).
    """
    if load.level == LoadLevel.LOW:
        return profile

    context_estimate = profile.context_tokens_estimate
    reasoning = profile.reasoning_depth
    if load.level == LoadLevel.MEDIUM:
        context_estimate = max(context_estimate, 64000)
    else:  # HIGH
        context_estimate = max(context_estimate, 128000)
        if not reasoning_overridden and reasoning != ReasoningLevel.DEEP:
            reasoning = ReasoningLevel.DEEP

    return replace(
        profile,
        context_tokens_estimate=context_estimate,
        reasoning_depth=reasoning,
    )
