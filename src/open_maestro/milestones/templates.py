"""Default milestone templates for common project types."""

from __future__ import annotations

from open_maestro.milestones.models import (
    Artifact,
    Epic,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
)


# The 8 standard software-consulting lifecycle milestones derived from the
# M3BudgetUpload project lifecycle. They live inside every epic/workstream.
_STANDARD_MILESTONES = [
    Milestone(
        id="intake-discovery",
        name="Intake & Discovery",
        order=1,
        weight=10,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="requirements/*", required=True, detected=False),
            Artifact(path="docs/intake/*", required=False, detected=False),
            Artifact(path="docs/*synthesis*.md", required=False, detected=False),
        ],
        exit_criteria=[
            "PRD/RFP read and summarized",
            "Scope IN/OUT documented",
            "Risk register created",
            "Sprint-blocking open questions identified",
        ],
    ),
    Milestone(
        id="execution-planning",
        name="Execution Planning",
        order=2,
        weight=10,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="docs/execution-plan*.md", required=True, detected=False),
            Artifact(path="docs/*architecture*.md", required=False, detected=False),
            Artifact(path="docs/*decisions*.md", required=False, detected=False),
        ],
        exit_criteria=[
            "Architecture verified against real repos",
            "Design decisions ratified",
            "Repo/service impact map documented",
        ],
    ),
    Milestone(
        id="design-blueprint",
        name="Design Blueprint",
        order=3,
        weight=15,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="docs/blueprint*.md", required=True, detected=False),
            Artifact(path="docs/*spec*.md", required=True, detected=False),
            Artifact(path="docs/*contract*.md", required=True, detected=False),
            Artifact(path="docs/jira*.csv", required=False, detected=False),
        ],
        exit_criteria=[
            "Data/API contracts frozen and signed off",
            "UX/template/spec artifacts approved",
            "Jira stories with BDD acceptance criteria created",
        ],
    ),
    Milestone(
        id="build-planning",
        name="Build Planning",
        order=4,
        weight=10,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="docs/build-plan*.md", required=True, detected=False),
            Artifact(path="tests/fixtures/", required=True, detected=False),
            Artifact(path="docs/*traceability*.md", required=False, detected=False),
        ],
        exit_criteria=[
            "Phased implementation plan created",
            "Traceability matrix FR → file/test",
            "Test fixtures prepared",
            "Dev environments smoke-tested",
        ],
    ),
    Milestone(
        id="implementation",
        name="Implementation",
        order=5,
        weight=30,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="src/", required=False, detected=False),
            Artifact(path="Core/", required=False, detected=False),
            Artifact(path="ProfitStrategyService/", required=False, detected=False),
            Artifact(path="tests/unit/", required=False, detected=False),
        ],
        exit_criteria=[
            "All P0 stories implemented",
            "Unit/integration tests passing",
            "Code merged to feature branch",
        ],
    ),
    Milestone(
        id="qa-integration",
        name="QA & Integration",
        order=6,
        weight=15,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="docs/qa/", required=True, detected=False),
            Artifact(path="docs/*test-report*.md", required=True, detected=False),
            Artifact(path="docs/*uat*.md", required=True, detected=False),
            Artifact(path="tests/e2e*/", required=False, detected=False),
        ],
        exit_criteria=[
            "E2E tests pass",
            "Security/scope verified",
            "UAT playbook executed",
            "Sign-off report produced",
        ],
    ),
    Milestone(
        id="demo-delivery",
        name="Demo & Delivery",
        order=7,
        weight=5,
        client_visible=True,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="docs/demo/", required=True, detected=False),
            Artifact(path="docs/*demo*.md", required=False, detected=False),
            Artifact(path="build/*.pptx", required=False, detected=False),
        ],
        exit_criteria=[
            "Demo delivered",
            "Handoff docs complete",
            "Stakeholder sign-off obtained",
        ],
    ),
    Milestone(
        id="retrospective-findings",
        name="Retrospective & Findings",
        order=8,
        weight=5,
        client_visible=False,
        status=MilestoneStatus.NOT_STARTED,
        artifacts=[
            Artifact(path="docs/findings/", required=False, detected=False),
            Artifact(path="docs/*retrospective*.md", required=False, detected=False),
            Artifact(path="docs/new-*-playbook.md", required=False, detected=False),
        ],
        exit_criteria=[
            "Lessons learned documented",
            "Known issues captured",
            "Playbooks updated",
            "Team debrief complete",
        ],
    ),
]


def default_software_template(project_id: str, project_path: str = "") -> MilestonePlan:
    """Return the default software consulting milestone template.

    The plan contains a single default epic so the project is usable immediately.
    Use ``maestro --sync-milestones`` to replace the default epic with the
    canonical epics/workstreams provisioned in Merven.
    """
    return MilestonePlan(
        project_id=project_id,
        project_path=project_path,
        schema_version="2.0",
        epics=[
            Epic(
                id="default",
                name="Default Track",
                order=1,
                status=MilestoneStatus.NOT_STARTED,
                milestones=[m.model_copy(deep=True) for m in _STANDARD_MILESTONES],
            )
        ],
    )


def software_template_with_epics(
    project_id: str,
    epic_names: list[str],
    project_path: str = "",
) -> MilestonePlan:
    """Return a software template with multiple named epics.

    Each epic receives the standard 8 lifecycle milestones.
    """
    import re

    def _slugify(name: str) -> str:
        return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "epic"

    epics: list[Epic] = []
    for order, name in enumerate(epic_names, start=1):
        epics.append(
            Epic(
                id=_slugify(name),
                name=name,
                order=order,
                status=MilestoneStatus.NOT_STARTED,
                milestones=[m.model_copy(deep=True) for m in _STANDARD_MILESTONES],
            )
        )

    return MilestonePlan(
        project_id=project_id,
        project_path=project_path,
        schema_version="2.0",
        epics=epics,
    )
