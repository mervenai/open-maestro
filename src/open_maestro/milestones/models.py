"""Pydantic models for milestone-guided project lifecycle.

Taxonomy (schema version 2.0):

    MilestonePlan
    └── epics: list[Epic]          # workstreams / features / tracks
        └── milestones: list[Milestone]   # 8 standard lifecycle milestones

A project has one or more epics. Each epic contains the same lifecycle
milestones (e.g. Intake & Discovery, Implementation, QA & Integration, etc.).
There are no project-level milestones.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class MilestoneStatus(str, Enum):
    """Allowed milestone/epic statuses."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class Artifact(BaseModel):
    """A deliverable or document associated with a milestone."""

    path: str
    required: bool = True
    detected: bool = False
    description: str = ""


class Blocker(BaseModel):
    """A recorded impediment to milestone/epic progress."""

    id: str = Field(default_factory=lambda: datetime.now().isoformat())
    description: str
    epic_id: str
    milestone_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    resolved_at: datetime | None = None


class Milestone(BaseModel):
    """A single lifecycle milestone inside an epic."""

    id: str = Field(..., pattern=r"^[a-z0-9_-]+$")
    name: str
    order: int = Field(..., ge=1)
    weight: int = Field(..., ge=0, le=100)
    client_visible: bool = True
    status: MilestoneStatus = MilestoneStatus.NOT_STARTED
    progress: int | None = Field(None, ge=0, le=100)
    started_at: date | None = None
    completed_at: date | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    exit_criteria: list[str] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def _dates_consistent(self) -> Milestone:
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be before started_at")
        return self

    def completion(self) -> int:
        """Return 0-100 completion for this milestone.

        Why: a flat 50% for any started milestone overstates early work.
        What: completed=100, not_started/skipped=0; otherwise the explicit
        ``progress`` when set, else 50 (back-compat for plans without it).
        Test: in_progress with progress=12 -> 12; without progress -> 50;
        completed with a stale progress=30 -> 100.
        """
        if self.status == MilestoneStatus.COMPLETED:
            return 100
        if self.status in (MilestoneStatus.NOT_STARTED, MilestoneStatus.SKIPPED):
            return 0
        return 50 if self.progress is None else self.progress


def weighted_completion(milestones: list[Milestone]) -> int:
    """Return the weight-weighted 0-100 completion of non-skipped milestones.

    Why: epic and overall percentages must use one rule, or they disagree.
    What: floor(sum(weight * completion) / sum(weight)) over non-skipped
    milestones, flooring once; plain mean when all weights are 0; 0 if none.
    Test: weights 10 (completed) + 30 (not started) -> 25; all skipped -> 0.
    """
    active = [m for m in milestones if m.status != MilestoneStatus.SKIPPED]
    if not active:
        return 0
    total_weight = sum(m.weight for m in active)
    if total_weight == 0:
        return sum(m.completion() for m in active) // len(active)
    return sum(m.weight * m.completion() for m in active) // total_weight


class Epic(BaseModel):
    """A parallel workstream / feature track containing lifecycle milestones."""

    id: str = Field(..., pattern=r"^[a-z0-9_-]+$")
    name: str
    order: int = Field(..., ge=1)
    status: MilestoneStatus = MilestoneStatus.NOT_STARTED
    milestones: list[Milestone] = Field(default_factory=list)
    notes: str = ""

    @field_validator("milestones")
    @classmethod
    def _milestone_orders_unique(cls, milestones: list[Milestone]) -> list[Milestone]:
        orders = [m.order for m in milestones]
        if len(orders) != len(set(orders)):
            raise ValueError("Milestone orders must be unique within an epic")
        return milestones

    def completion(self) -> int:
        """Return 0-100 completion weighted by milestone weight (skipped excluded)."""
        return weighted_completion(self.milestones)

    def get_milestone(self, milestone_id: str) -> Milestone | None:
        """Return a milestone by ID within this epic."""
        for milestone in self.milestones:
            if milestone.id == milestone_id:
                return milestone
        return None


class Summary(BaseModel):
    """Computed rollup of milestone plan state."""

    overall_completion: int = Field(0, ge=0, le=100)
    current_milestone_ids: list[str] = Field(default_factory=list)
    next_milestone_ids: list[str] = Field(default_factory=list)
    active_blockers: list[Blocker] = Field(default_factory=list)
    client_ready: bool = False


class MilestonePlan(BaseModel):
    """Full milestone plan for a project."""

    project_id: str = Field(..., pattern=r"^[a-z0-9_-]+$")
    project_name: str = ""
    project_path: str = ""
    schema_version: str = "2.0"
    last_updated: datetime = Field(default_factory=datetime.now)
    epics: list[Epic] = Field(default_factory=list)
    summary: Summary = Field(default_factory=Summary)

    @field_validator("epics")
    @classmethod
    def _epic_orders_unique(cls, epics: list[Epic]) -> list[Epic]:
        orders = [e.order for e in epics]
        if len(orders) != len(set(orders)):
            raise ValueError("Epic orders must be unique")
        return epics

    @model_validator(mode="after")
    def _recompute_summary(self) -> MilestonePlan:
        self.summary = self._compute_summary()
        return self

    def _compute_summary(self) -> Summary:
        all_milestones: list[tuple[str, Milestone]] = []
        for epic in self.epics:
            for milestone in epic.milestones:
                all_milestones.append((epic.id, milestone))

        active = [
            (epic_id, m)
            for epic_id, m in all_milestones
            if m.status != MilestoneStatus.SKIPPED
        ]
        overall = weighted_completion([m for _, m in active])

        current = [
            f"{epic_id}/{m.id}"
            for epic_id, m in active
            if m.status == MilestoneStatus.IN_PROGRESS
        ]

        ordered = sorted(
            active,
            key=lambda item: (
                next((e.order for e in self.epics if e.id == item[0]), 0),
                item[1].order,
            ),
        )
        next_ids: list[str] = []
        for epic_id, m in ordered:
            if m.status == MilestoneStatus.NOT_STARTED:
                next_ids.append(f"{epic_id}/{m.id}")
                break

        active_blockers = [
            b for _, m in all_milestones for b in m.blockers if b.resolved_at is None
        ]

        return Summary(
            overall_completion=overall,
            current_milestone_ids=current,
            next_milestone_ids=next_ids,
            active_blockers=active_blockers,
            # Ready only when every non-skipped milestone is completed; derived
            # from statuses, not the floored percentage, and false when empty.
            client_ready=bool(active)
            and all(m.status == MilestoneStatus.COMPLETED for _, m in active),
        )

    def get_epic(self, epic_id: str) -> Epic | None:
        """Return an epic by ID, or None if not found."""
        for epic in self.epics:
            if epic.id == epic_id:
                return epic
        return None

    def get_milestone(self, epic_id: str, milestone_id: str) -> Milestone | None:
        """Return a milestone by epic ID and milestone ID."""
        epic = self.get_epic(epic_id)
        if epic is None:
            return None
        return epic.get_milestone(milestone_id)

    def find_milestone(self, milestone_id: str) -> tuple[Epic, Milestone] | None:
        """Find the first milestone matching *milestone_id* across all epics."""
        for epic in self.epics:
            milestone = epic.get_milestone(milestone_id)
            if milestone is not None:
                return epic, milestone
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary."""
        return self.model_dump(mode="json", exclude_none=True)
