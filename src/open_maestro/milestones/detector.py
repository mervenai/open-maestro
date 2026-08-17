"""Artifact-based milestone detection.

Detection is rule-based by default, with an extension point for LLM-driven
inference.  The detector scans a project directory for artifact patterns,
marks artifacts as detected, and suggests milestone statuses.  Human
confirmation is required before a suggestion becomes the stored state.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from open_maestro.milestones.models import (
    Artifact,
    Epic,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class DetectionSuggestion:
    """A status suggestion for a single milestone within an epic."""

    epic_id: str
    milestone_id: str
    suggested_status: MilestoneStatus
    confidence: float  # 0.0 - 1.0
    reason: str
    detected_artifacts: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)


class MilestoneDetector:
    """Scan a project folder and suggest milestone states."""

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)

    def detect(self, plan: MilestonePlan) -> list[DetectionSuggestion]:
        """Return status suggestions for every milestone in *plan*."""
        self._last_plan_epics = sorted(plan.epics, key=lambda e: e.order)
        suggestions: list[DetectionSuggestion] = []
        for epic in self._last_plan_epics:
            for milestone in sorted(epic.milestones, key=lambda m: m.order):
                suggestions.append(self._detect_milestone(epic, milestone))
        return self._apply_ordering_constraints(suggestions)

    def _detect_milestone(self, epic: Epic, milestone: Milestone) -> DetectionSuggestion:
        detected: list[str] = []
        missing_required: list[str] = []
        required_total = 0
        required_found = 0

        for artifact in milestone.artifacts:
            matches = self._match_artifact(artifact)
            artifact.detected = bool(matches)
            if matches:
                detected.extend(matches)
            if artifact.required:
                required_total += 1
                if matches:
                    required_found += 1
                else:
                    missing_required.append(artifact.path)

        status, confidence, reason = self._infer_status(
            milestone, required_total, required_found, detected
        )

        return DetectionSuggestion(
            epic_id=epic.id,
            milestone_id=milestone.id,
            suggested_status=status,
            confidence=confidence,
            reason=reason,
            detected_artifacts=detected,
            missing_required=missing_required,
        )

    def _match_artifact(self, artifact: Artifact) -> list[str]:
        """Return matching paths (relative to project root) for an artifact pattern."""
        pattern = artifact.path
        target = self.project_path / pattern

        # Exact file or directory.
        if target.exists():
            if target.is_dir():
                # Return any non-empty content as evidence.
                try:
                    children = list(target.iterdir())
                    if children:
                        return [str(target.relative_to(self.project_path))]
                except OSError:
                    pass
            else:
                return [str(target.relative_to(self.project_path))]

        # Glob pattern (recursive if ** is present).
        try:
            matches = list(self.project_path.rglob(pattern.lstrip("/")))
            if not matches:
                matches = list(self.project_path.glob(pattern.lstrip("/")))
        except ValueError:
            matches = []

        return [str(m.relative_to(self.project_path)) for m in matches]

    def _infer_status(
        self,
        milestone: Milestone,
        required_total: int,
        required_found: int,
        detected: list[str],
    ) -> tuple[MilestoneStatus, float, str]:
        """Infer status from artifact coverage."""
        if milestone.status in (MilestoneStatus.COMPLETED, MilestoneStatus.SKIPPED):
            # Do not override terminal statuses without explicit user action.
            return milestone.status, 1.0, "Status already set explicitly."

        has_any = bool(detected)

        if required_total == 0:
            if has_any:
                return (
                    MilestoneStatus.IN_PROGRESS,
                    0.5,
                    f"Optional artifacts found: {len(detected)}.",
                )
            return (
                MilestoneStatus.NOT_STARTED,
                0.8,
                "No required artifacts defined; nothing detected.",
            )

        if required_found == required_total:
            return (
                MilestoneStatus.COMPLETED,
                0.85,
                f"All {required_total} required artifacts detected.",
            )

        if required_found > 0:
            return (
                MilestoneStatus.IN_PROGRESS,
                0.7,
                f"{required_found}/{required_total} required artifacts detected.",
            )

        if has_any:
            return (
                MilestoneStatus.IN_PROGRESS,
                0.4,
                "No required artifacts yet, but optional artifacts detected.",
            )

        return (
            MilestoneStatus.NOT_STARTED,
            0.9,
            "No artifacts detected.",
        )

    def _apply_ordering_constraints(
        self, suggestions: list[DetectionSuggestion]
    ) -> list[DetectionSuggestion]:
        """Prevent suggesting a later milestone as complete while an earlier one is not."""
        ordered = sorted(suggestions, key=lambda s: self._order_index(s.epic_id, s.milestone_id))
        seen_incomplete = False
        for suggestion in ordered:
            if suggestion.suggested_status == MilestoneStatus.COMPLETED and seen_incomplete:
                suggestion.suggested_status = MilestoneStatus.IN_PROGRESS
                suggestion.confidence = max(suggestion.confidence * 0.7, 0.4)
                suggestion.reason += " (ordering constraint: earlier milestone not complete)"
            if suggestion.suggested_status != MilestoneStatus.COMPLETED:
                seen_incomplete = True
        return suggestions

    def _order_index(self, epic_id: str, milestone_id: str) -> int:
        """Return a stable sort index for a suggestion based on epic and milestone order."""
        for e_idx, epic in enumerate(self._plan_epics):
            if epic.id != epic_id:
                continue
            for m_idx, milestone in enumerate(epic.milestones):
                if milestone.id == milestone_id:
                    return e_idx * 100 + m_idx
        return 10_000

    @property
    def _plan_epics(self) -> list[Epic]:
        """Epics from the most recent detect() call, for ordering."""
        return getattr(self, "_last_plan_epics", [])

    def apply_suggestions(
        self,
        plan: MilestonePlan,
        suggestions: list[DetectionSuggestion],
        confirmed_ids: set[str] | None = None,
    ) -> MilestonePlan:
        """Apply suggestions to *plan*.

        Only milestones in *confirmed_ids* are changed.  This preserves the
        model-suggested, human-confirmed policy.  confirmed_ids entries should
        be ``epic_id/milestone_id`` strings.
        """
        confirmed_ids = confirmed_ids or set()
        by_key = {f"{s.epic_id}/{s.milestone_id}": s for s in suggestions}
        today = date.today()

        for epic in plan.epics:
            for milestone in epic.milestones:
                key = f"{epic.id}/{milestone.id}"
                suggestion = by_key.get(key)
                if suggestion is None:
                    continue

                if key in confirmed_ids:
                    old_status = milestone.status
                    milestone.status = suggestion.suggested_status
                    if old_status != suggestion.suggested_status:
                        if milestone.started_at is None and suggestion.suggested_status != MilestoneStatus.NOT_STARTED:
                            milestone.started_at = today
                        if suggestion.suggested_status == MilestoneStatus.COMPLETED:
                            milestone.completed_at = today

                # Always reflect detected artifacts, even without confirmation.
                detected_set = set(suggestion.detected_artifacts)
                for artifact in milestone.artifacts:
                    artifact.detected = artifact.path in detected_set or bool(
                        self._match_artifact(artifact)
                    )

        plan._recompute_summary()  # noqa: SLF001
        return plan

    def git_commit_dates(self) -> list[date]:
        """Return commit dates from the project's git history, if any."""
        try:
            result = subprocess.run(
                ["git", "log", "--format=%ad", "--date=short"],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if result.returncode != 0:
                return []
            dates: list[date] = []
            for line in result.stdout.strip().splitlines():
                try:
                    dates.append(datetime.strptime(line, "%Y-%m-%d").date())  # noqa: DTZ007
                except ValueError:
                    continue
            return dates
        except Exception as exc:
            logger.debug("Could not read git history: %s", exc)
            return []


def summarize_suggestions(suggestions: list[DetectionSuggestion]) -> dict[str, Any]:
    """Return a human-readable summary of detection suggestions."""
    return {
        "suggestions": [
            {
                "epic_id": s.epic_id,
                "milestone_id": s.milestone_id,
                "suggested_status": s.suggested_status.value,
                "confidence": round(s.confidence, 2),
                "reason": s.reason,
                "detected": len(s.detected_artifacts),
                "missing_required": s.missing_required,
            }
            for s in suggestions
        ]
    }
