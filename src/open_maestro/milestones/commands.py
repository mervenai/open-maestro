"""Interactive slash-command handlers for milestone management."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from open_maestro.milestones import MilestoneStatus
from open_maestro.milestones.detector import MilestoneDetector
from open_maestro.milestones.models import Blocker, Milestone, MilestonePlan
from open_maestro.milestones.playbook import (
    format_prompt_list,
    get_prompts_for_milestone,
)
from open_maestro.milestones.store import MilestoneStore


def get_current_or_next_milestone_prompts(
    project_path: Path,
) -> tuple[list[tuple[Any, str]], str | None]:
    """Return prompts for the milestone /next would focus on, plus its ID.

    This is used by interactive mode to let users select a suggested prompt by
    number after running /next.
    """
    store = MilestoneStore(project_path)
    plan = store.load()

    target: tuple[str, Milestone] | None = None
    current: list[tuple[str, Milestone]] = []
    for epic in plan.epics:
        for milestone in epic.milestones:
            if milestone.status == MilestoneStatus.IN_PROGRESS:
                current.append((epic.id, milestone))

    if current:
        # If multiple milestones are in progress, use the first ordered one.
        target = sorted(current, key=lambda x: x[1].order)[0]
    else:
        for epic in sorted(plan.epics, key=lambda e: e.order):
            for milestone in sorted(epic.milestones, key=lambda m: m.order):
                if milestone.status == MilestoneStatus.NOT_STARTED:
                    target = (epic.id, milestone)
                    break
            if target is not None:
                break

    if target is None:
        return [], None

    epic_id, milestone = target
    prompts = get_prompts_for_milestone(
        project_path, milestone.id, plan=plan, epic_id=epic_id
    )
    return prompts, milestone.id


def _resolve_milestone(plan: MilestonePlan, ref: str) -> tuple[str, Milestone] | None:
    """Resolve a milestone reference of the form ``epic_id/milestone_id`` or just ``milestone_id``."""
    if "/" in ref:
        epic_id, milestone_id = ref.split("/", 1)
        milestone = plan.get_milestone(epic_id, milestone_id)
        if milestone is not None:
            return epic_id, milestone
        return None

    found = plan.find_milestone(ref)
    if found is not None:
        return found[0].id, found[1]
    return None


def _format_missing(milestone: Milestone, suggestion: Any) -> str:
    """List missing required artifacts for a milestone."""
    if not suggestion or not suggestion.missing_required:
        return ""
    return "\nMissing required artifacts:\n" + "\n".join(
        f"  - {m}" for m in suggestion.missing_required
    )


def handle_next_command(project_path: Path) -> str:
    """Suggest the next concrete action based on milestone state."""
    store = MilestoneStore(project_path)
    plan = store.load()

    current: list[tuple[str, Milestone]] = []
    for epic in plan.epics:
        for milestone in epic.milestones:
            if milestone.status == MilestoneStatus.IN_PROGRESS:
                current.append((epic.id, milestone))

    detector = MilestoneDetector(project_path)
    suggestions = {
        (s.epic_id, s.milestone_id): s
        for s in detector.detect(plan)
    }

    if current:
        lines = ["Current milestones in progress:"]
        for epic_id, milestone in sorted(current, key=lambda x: x[1].order):
            lines.append(f"\n## {milestone.name} ({epic_id})")
            lines.append("Exit criteria:")
            for criterion in milestone.exit_criteria:
                lines.append(f"  - {criterion}")
            missing = _format_missing(milestone, suggestions.get((epic_id, milestone.id)))
            if missing:
                lines.append(missing)
            prompt_pairs = get_prompts_for_milestone(
                project_path, milestone.id, plan=plan, epic_id=epic_id
            )
            if prompt_pairs:
                lines.append("")
                lines.append(format_prompt_list(prompt_pairs, max_prompts=3))
        return "\n".join(lines)

    next_item: tuple[str, Milestone] | None = None
    for epic in sorted(plan.epics, key=lambda e: e.order):
        for milestone in sorted(epic.milestones, key=lambda m: m.order):
            if milestone.status == MilestoneStatus.NOT_STARTED:
                next_item = (epic.id, milestone)
                break
        if next_item is not None:
            break

    if next_item is None:
        return "No remaining milestones. Project appears complete."

    epic_id, next_milestone = next_item
    lines = [
        f"Next milestone: {next_milestone.name} ({epic_id})",
        "",
        "Exit criteria:",
    ]
    for criterion in next_milestone.exit_criteria:
        lines.append(f"  - {criterion}")

    prompt_pairs = get_prompts_for_milestone(
        project_path, next_milestone.id, plan=plan, epic_id=epic_id
    )
    if prompt_pairs:
        lines.append("")
        lines.append(format_prompt_list(prompt_pairs, max_prompts=3))
    return "\n".join(lines)


def handle_complete_command(project_path: Path, args: list[str]) -> str:
    """Mark a milestone as completed (with optional force override)."""
    if not args:
        return "Usage: /complete <epic-id>/<milestone-id> [--force] or /complete <milestone-id> [--force]"

    ref = args[0]
    force = "--force" in args[1:]

    store = MilestoneStore(project_path)
    plan = store.load()
    resolved = _resolve_milestone(plan, ref)
    if resolved is None:
        return f"Unknown milestone '{ref}'. Type /milestones to see IDs."

    epic_id, milestone = resolved
    detector = MilestoneDetector(project_path)
    suggestions = {
        (s.epic_id, s.milestone_id): s
        for s in detector.detect(plan)
    }
    suggestion = suggestions.get((epic_id, milestone.id))

    if not force and suggestion and suggestion.suggested_status != MilestoneStatus.COMPLETED:
        missing = ", ".join(suggestion.missing_required) or "some required artifacts"
        return (
            f"Not all required artifacts are detected for '{milestone.name}' in {epic_id}.\n"
            f"Missing: {missing}\n"
            f"Run `/complete {epic_id}/{milestone.id} --force` to mark it complete anyway."
        )

    milestone.status = MilestoneStatus.COMPLETED
    milestone.completed_at = date.today()
    if milestone.started_at is None:
        milestone.started_at = milestone.completed_at
    store.update(plan)
    return f"Marked '{milestone.name}' in {epic_id} as completed."


def handle_blocker_command(project_path: Path, args: list[str]) -> str:
    """Record a blocker against a milestone."""
    if len(args) < 2:
        return "Usage: /blocker <epic-id>/<milestone-id> <reason> or /blocker <milestone-id> <reason>"

    ref = args[0]
    reason = " ".join(args[1:])

    store = MilestoneStore(project_path)
    plan = store.load()
    resolved = _resolve_milestone(plan, ref)
    if resolved is None:
        return f"Unknown milestone '{ref}'. Type /milestones to see IDs."

    epic_id, milestone = resolved
    milestone.status = MilestoneStatus.BLOCKED
    milestone.blockers.append(
        Blocker(description=reason, epic_id=epic_id, milestone_id=milestone.id)
    )
    store.update(plan)
    return f"Recorded blocker on '{milestone.name}' in {epic_id}: {reason}"


def handle_track_command(project_path: Path, args: list[str]) -> str:
    """Update the status of a milestone inside an epic.

    This command previously updated a legacy ``Track`` inside a milestone. The
    taxonomy has changed: milestones now live inside epics, so this command
    updates a milestone status directly.
    """
    if len(args) < 2:
        return (
            "Usage: /track <epic-id>/<milestone-id> <status>\n"
            "Statuses: not_started, in_progress, blocked, completed, skipped"
        )

    ref = args[0]
    status_str = args[1]
    try:
        status = MilestoneStatus(status_str)
    except ValueError:
        return f"Invalid status '{status_str}'. Use one of: not_started, in_progress, blocked, completed, skipped"

    store = MilestoneStore(project_path)
    plan = store.load()
    resolved = _resolve_milestone(plan, ref)
    if resolved is None:
        return f"Unknown milestone '{ref}'. Type /milestones to see IDs."

    epic_id, milestone = resolved
    milestone.status = status
    if status == MilestoneStatus.IN_PROGRESS and milestone.started_at is None:
        milestone.started_at = date.today()
    if status == MilestoneStatus.COMPLETED:
        milestone.completed_at = date.today()
    store.update(plan)
    return f"Updated milestone '{milestone.name}' in {epic_id} to {status.value}."


def handle_prompts_command(project_path: Path, args: list[str]) -> str:
    """List all playbook prompts for a milestone.

    Usage: /prompts <milestone-id> [epic-id]
    """
    if not args:
        return "Usage: /prompts <milestone-id> [epic-id]"

    milestone_id = args[0]
    epic_id = args[1] if len(args) > 1 else None

    store = MilestoneStore(project_path)
    plan = store.load()

    prompt_pairs = get_prompts_for_milestone(
        project_path, milestone_id, plan=plan, epic_id=epic_id
    )
    if not prompt_pairs:
        return f"No prompts found for milestone '{milestone_id}'."

    lines = [f"Prompts for '{milestone_id}':"]
    for idx, (template, rendered) in enumerate(prompt_pairs, start=1):
        agent = f" [{template.agent_hint}]" if template.agent_hint else ""
        tags = f" ({', '.join(template.tags)})" if template.tags else ""
        lines.append(f"\n{idx}. {template.title}{agent}{tags}")
        lines.append(rendered)
    return "\n".join(lines)


def format_prompt_context(project_path: Path) -> str:
    """Return a concise milestone context string for agent prompts."""
    try:
        store = MilestoneStore(project_path)
        if not store.exists():
            return ""
        plan = store.load()
    except Exception:
        return ""

    current: list[tuple[str, Milestone]] = []
    for epic in plan.epics:
        for milestone in epic.milestones:
            if milestone.status == MilestoneStatus.IN_PROGRESS:
                current.append((epic.id, milestone))

    if not current:
        return ""

    lines = [
        "",
        "## Project milestone context",
        f"Overall completion: {plan.summary.overall_completion}%",
        "Current milestone(s):",
    ]
    for epic_id, milestone in sorted(current, key=lambda x: x[1].order):
        lines.append(f"- {milestone.name} ({epic_id}, {milestone.completion()}% complete)")
        if milestone.exit_criteria:
            lines.append("  Exit criteria:")
            for criterion in milestone.exit_criteria:
                lines.append(f"    - {criterion}")

    if plan.summary.active_blockers:
        lines.append("Active blockers:")
        for blocker in plan.summary.active_blockers:
            lines.append(f"  - [{blocker.epic_id}/{blocker.milestone_id}] {blocker.description}")

    lines.append(
        "Produce artifacts appropriate for the current milestone and place them "
        "in the standard project subfolders."
    )
    return "\n".join(lines)
