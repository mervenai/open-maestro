"""Clone-restore guardrail for pre-implementation milestones.

Background (MSTRO-109): during design-blueprint, swarm workers wrote
permission rows into cloned code-repo JSON files (M3Repo /
AccessManagementService clones). Analysis/pre-dev turns must be able to
read cloned repos but never mutate them.

What: detect nested git repositories under the project root and, around a
PM turn, snapshot their tracked working-tree state and revert any new
modifications afterwards. Untracked files are never touched (no
``git clean`` — too destructive). All git calls are wrapped so a non-repo
or git failure only logs and never breaks the turn.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from open_maestro.milestones.models import MilestoneStatus
from open_maestro.milestones.store import MilestoneStore

logger = logging.getLogger(__name__)

# Milestones where agents analyze code repos but must not mutate them.
# Everything from implementation onward is allowed to write.
PRE_IMPLEMENTATION_MILESTONES: frozenset[str] = frozenset(
    {
        "intake-discovery",
        "execution-planning",
        "design-blueprint",
        "build-planning",
    }
)

_CLONE_GUARD_ENV = "MAESTRO_CLONE_GUARD"


def clone_guard_enabled() -> bool:
    """Env escape hatch: MAESTRO_CLONE_GUARD=off/0/false/no disables the guard."""
    return os.environ.get(_CLONE_GUARD_ENV, "").strip().lower() not in {
        "off",
        "0",
        "false",
        "no",
    }


def current_inprogress_milestone_ids(project_root: Path) -> list[str]:
    """Return ids of milestones currently in progress; [] when no plan exists."""
    try:
        store = MilestoneStore(project_root)
        if not store.exists():
            return []
        plan = store.load()
    except Exception as exc:
        logger.debug("Clone guard: failed to load milestone plan: %s", exc)
        return []
    return [
        milestone.id
        for epic in plan.epics
        for milestone in epic.milestones
        if milestone.status == MilestoneStatus.IN_PROGRESS
    ]


def clone_guard_active(project_root: Path) -> bool:
    """Return True when the current phase may not mutate cloned repos.

    Active during the pre-implementation milestones. When there is no
    milestone plan at all (or nothing in progress), the guard stays
    active — the safer default for analysis turns.
    """
    if not clone_guard_enabled():
        return False
    ids = current_inprogress_milestone_ids(project_root)
    if not ids:
        return True
    return any(m in PRE_IMPLEMENTATION_MILESTONES for m in ids)


def find_clone_repos(root: Path, max_depth: int = 2) -> list[Path]:
    """Find nested git repositories under *root* at depth 1-2.

    The project root itself and hidden directories (``.open-maestro``,
    ``.git``) are excluded. A directory counts as a repo when it contains
    a ``.git`` entry (directory or worktree/submodule file).
    """
    clones: list[Path] = []
    try:
        root = root.resolve()
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, depth = stack.pop()
            if depth > 0 and (directory / ".git").exists():
                clones.append(directory)
                continue  # never descend into a repo's own tree
            if depth >= max_depth:
                continue
            try:
                children = sorted(directory.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir() or child.name.startswith("."):
                    continue
                stack.append((child, depth + 1))
    except Exception as exc:
        logger.debug("Clone guard: repo scan failed under %s: %s", root, exc)
    return clones


def _git_porcelain(clone: Path) -> set[str]:
    """Return tracked-change ``git status --porcelain`` lines for a repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(clone), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        logger.debug("Clone guard: git status failed for %s: %s", clone, exc)
        return set()
    if proc.returncode != 0:
        logger.debug(
            "Clone guard: git status rc=%s for %s: %s",
            proc.returncode,
            clone,
            proc.stderr.strip(),
        )
        return set()
    # Untracked lines (??) are ignored: the restore never cleans them.
    return {line for line in proc.stdout.splitlines() if not line.startswith("??")}


def snapshot_clones(clones: list[Path]) -> dict[Path, set[str]]:
    """Snapshot tracked-change lines for each clone before a turn runs."""
    return {clone: _git_porcelain(clone) for clone in clones}


def restore_modified_clones(
    clones: list[Path],
    before: dict[Path, set[str]],
) -> list[tuple[Path, list[str]]]:
    """Revert tracked modifications that appeared since the snapshot.

    For each clone, diff the current porcelain state against *before*;
    when new tracked modifications exist, run ``git checkout -- .`` to
    revert them (tracked files only — untracked files are left alone).
    Returns a list of ``(clone, restored_files)`` for reporting.
    """
    restored: list[tuple[Path, list[str]]] = []
    for clone in clones:
        try:
            new_lines = sorted(_git_porcelain(clone) - before.get(clone, set()))
            if not new_lines:
                continue
            proc = subprocess.run(
                ["git", "-C", str(clone), "checkout", "--", "."],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Clone guard: git checkout failed for %s: %s",
                    clone,
                    proc.stderr.strip(),
                )
                continue
            restored.append((clone, [line[3:] for line in new_lines]))
        except Exception as exc:
            logger.warning("Clone guard: restore failed for %s: %s", clone, exc)
    return restored
