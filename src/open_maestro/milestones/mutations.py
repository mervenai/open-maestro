"""Typed, drift-guarded mutations of a project's milestone plan.

Why: the maestro MCP server (and any other out-of-band writer) mutates
``milestones.yaml`` through this module so the invariants the
milestones-authoring skill documents as prose — dated one-line state deltas,
stable blocker ids, summary recomputed from the milestone tree — are enforced
in code, not by discipline.

Every mutation follows the same three-step shape:

1. :func:`open_session` — load the plan and remember the file's mtime.
2. mutate the plan through the typed helpers below;
3. :func:`commit` — refuse to save if the file changed on disk since step 1
   (another maestro session or editor wrote in between), else recompute the
   summary and save.

Each helper returns a result dict that includes a ``memory`` line — a compact,
dated narrative suitable for the project memory store — so state and story can
advance together from a single call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from open_maestro.milestones.models import Blocker, MilestonePlan, MilestoneStatus
from open_maestro.milestones.store import MilestoneStore


class DriftError(RuntimeError):
    """The plan file changed on disk between load and save."""


class NoPlanError(RuntimeError):
    """The project has no milestone plan to mutate."""


class UnknownRefError(ValueError):
    """An epic, milestone, or blocker id did not resolve."""


@dataclass
class MutationSession:
    """A loaded plan plus the disk state it was loaded from."""

    store: MilestoneStore
    plan: MilestonePlan
    baseline_mtime: float

    @property
    def project_path(self) -> Path:
        return self.store.project_path


def open_session(project_path: str | Path) -> MutationSession:
    """Load the plan for mutation, snapshotting the file mtime.

    Unlike :meth:`MilestoneStore.load`, this does NOT create a default plan
    when none exists — out-of-band writers should not bootstrap a project.
    """
    store = MilestoneStore(Path(project_path))
    if not store.exists():
        raise NoPlanError(
            f"No milestone plan at {store.file_path}. "
            "Run maestro in the project first to initialize one."
        )
    mtime = store.file_path.stat().st_mtime
    plan = store.load()
    return MutationSession(store=store, plan=plan, baseline_mtime=mtime)


def commit(session: MutationSession) -> None:
    """Save the plan unless the file drifted on disk since the session opened."""
    current = session.store.file_path.stat().st_mtime
    if current != session.baseline_mtime:
        raise DriftError(
            f"{session.store.file_path} changed on disk since it was read "
            "(another maestro session or editor saved in between). "
            "Re-open the session and retry."
        )
    session.store.update(session.plan)  # recomputes summary, then saves
    session.baseline_mtime = session.store.file_path.stat().st_mtime


def _parse_day(when: str | date | None) -> datetime:
    if when is None:
        return datetime.combine(date.today(), datetime.min.time())
    if isinstance(when, datetime):
        return when
    if isinstance(when, date):
        return datetime.combine(when, datetime.min.time())
    return datetime.combine(date.fromisoformat(when), datetime.min.time())


def _find_blocker(
    plan: MilestonePlan, blocker_id: str
) -> tuple[str, Any, Blocker] | None:
    for epic in plan.epics:
        for milestone in epic.milestones:
            for blocker in milestone.blockers:
                if blocker.id == blocker_id:
                    return epic.id, milestone, blocker
    return None


def active_blockers(session: MutationSession) -> list[dict[str, Any]]:
    """Flattened active blockers in dashboard order."""
    rows: list[dict[str, Any]] = []
    for epic in session.plan.epics:
        for milestone in epic.milestones:
            for blocker in milestone.blockers:
                if blocker.resolved_at is None:
                    rows.append(
                        {
                            "id": blocker.id,
                            "epic": epic.id,
                            "milestone": milestone.id,
                            "description": blocker.description,
                            "created_at": blocker.created_at.date().isoformat(),
                        }
                    )
    return rows


def compact_state(session: MutationSession) -> str:
    """A context-cheap project summary for agent prompts."""
    plan = session.plan
    lines = [
        f"project: {plan.project_name or plan.project_id} "
        f"({plan.summary.overall_completion}% complete, {len(plan.epics)} epic(s))"
    ]
    if plan.summary.current_milestone_ids:
        lines.append("current: " + ", ".join(plan.summary.current_milestone_ids))
    if plan.summary.next_milestone_ids:
        lines.append("next: " + ", ".join(plan.summary.next_milestone_ids))
    for epic in plan.epics:
        parts = []
        for m in epic.milestones:
            mark = {
                MilestoneStatus.COMPLETED: "done",
                MilestoneStatus.IN_PROGRESS: "active",
                MilestoneStatus.BLOCKED: "blocked",
                MilestoneStatus.SKIPPED: "skipped",
                MilestoneStatus.NOT_STARTED: "todo",
            }[m.status]
            parts.append(f"{m.id}({mark})")
        lines.append(f"- {epic.id}: " + " ".join(parts))
    blockers = active_blockers(session)
    lines.append(f"active blockers ({len(blockers)}):")
    for b in blockers:
        lines.append(f"- [{b['epic']}/{b['milestone']}] {b['id']}: {b['description']}")
    return "\n".join(lines)


def add_blocker(
    session: MutationSession,
    epic_id: str,
    milestone_id: str,
    description: str,
) -> dict[str, Any]:
    """Record a new active blocker on a milestone (summary mirrors on commit)."""
    epic = session.plan.get_epic(epic_id)
    if epic is None:
        raise UnknownRefError(f"Unknown epic '{epic_id}'.")
    milestone = session.plan.get_milestone(epic_id, milestone_id)
    if milestone is None:
        raise UnknownRefError(
            f"Unknown milestone '{milestone_id}' in epic '{epic_id}'."
        )
    blocker = Blocker(
        description=description.strip(), epic_id=epic_id, milestone_id=milestone_id
    )
    milestone.blockers.append(blocker)
    if milestone.status == MilestoneStatus.NOT_STARTED:
        milestone.status = MilestoneStatus.BLOCKED
    day = date.today().isoformat()
    return {
        "blocker_id": blocker.id,
        "location": f"{epic_id}/{milestone_id}",
        "memory": (
            f"{session.plan.project_id}: blocker added on {epic_id}/{milestone_id} "
            f"({day}): {description.strip()}"
        ),
    }


def resolve_blocker(
    session: MutationSession,
    blocker_id: str,
    resolution: str,
    when: str | date | None = None,
) -> dict[str, Any]:
    """Mark a blocker resolved, appending the dated resolution to its description."""
    found = _find_blocker(session.plan, blocker_id)
    if found is None:
        known = sorted(
            b["id"] for b in active_blockers(session)
        )
        raise UnknownRefError(
            f"No blocker with id '{blocker_id}'. Active blocker ids: {known}"
        )
    epic_id, _milestone, blocker = found
    if blocker.resolved_at is not None:
        raise UnknownRefError(
            f"Blocker '{blocker_id}' is already resolved "
            f"({blocker.resolved_at.date().isoformat()})."
        )
    resolved_at = _parse_day(when)
    blocker.resolved_at = resolved_at
    day = resolved_at.date().isoformat()
    note = resolution.strip()
    blocker.description = f"{blocker.description} Resolved {day}: {note}"
    return {
        "blocker_id": blocker.id,
        "location": f"{epic_id}/{blocker.milestone_id}",
        "resolved_at": day,
        "memory": (
            f"{session.plan.project_id}: blocker {blocker_id} on "
            f"{epic_id}/{blocker.milestone_id} resolved ({day}): {note}"
        ),
    }


def set_milestone_status(
    session: MutationSession,
    epic_id: str,
    milestone_id: str,
    status: str,
    note: str | None = None,
    when: str | date | None = None,
) -> dict[str, Any]:
    """Flip a milestone status, appending an optional dated one-line note."""
    try:
        new_status = MilestoneStatus(status)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in MilestoneStatus)
        raise UnknownRefError(
            f"Invalid status '{status}'. Use one of: {allowed}"
        ) from exc
    milestone = session.plan.get_milestone(epic_id, milestone_id)
    if milestone is None:
        raise UnknownRefError(
            f"Unknown milestone '{milestone_id}' in epic '{epic_id}'."
        )
    day_dt = _parse_day(when)
    day = day_dt.date().isoformat()
    old_status = milestone.status
    milestone.status = new_status
    if new_status == MilestoneStatus.IN_PROGRESS and milestone.started_at is None:
        milestone.started_at = day_dt.date()
    if new_status == MilestoneStatus.COMPLETED:
        milestone.completed_at = day_dt.date()
    note_text = note.strip() if note else ""
    if note_text:
        sep = " " if milestone.notes else ""
        milestone.notes = f"{milestone.notes}{sep}[{day}] {note_text}"
    epic = session.plan.get_epic(epic_id)
    if (
        epic is not None
        and new_status == MilestoneStatus.IN_PROGRESS
        and epic.status == MilestoneStatus.NOT_STARTED
    ):
        epic.status = MilestoneStatus.IN_PROGRESS
    return {
        "epic": epic_id,
        "milestone": milestone_id,
        "old_status": old_status.value,
        "status": new_status.value,
        "memory": (
            f"{session.plan.project_id}: milestone {epic_id}/{milestone_id} "
            f"{old_status.value} -> {new_status.value} ({day})"
            + (f": {note_text}" if note_text else "")
        ),
    }
