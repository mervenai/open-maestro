"""Auto-populate work epics and a local dashboard after Intake & Discovery.

Why: Once the project process milestone ``intake-discovery`` is complete, the
project is ready to be broken into feature epics. Rather than requiring a
manual migration, this module scans the intake epic breakdown document and
creates the corresponding work epics with the standard lifecycle milestones.
What: ``maybe_populate_work_epics()`` and ``maybe_export_dashboard()`` are
called by ``MilestoneStore.update()`` whenever a milestone status changes.
Test: Mark ``intake-discovery`` complete in a single-epic project that contains
``docs/intake/epics.md``; work epics and ``dashboard.html`` appear.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from open_maestro.milestones.dashboard import export_dashboard_html
from open_maestro.milestones.models import Epic, Milestone, MilestonePlan, MilestoneStatus


# Standard process milestone IDs/names and weights used for every work epic.
_STANDARD_MILESTONES: list[tuple[tuple[str, str], int]] = [
    (("intake-discovery", "Intake & Discovery"), 10),
    (("execution-planning", "Execution Planning"), 10),
    (("design-blueprint", "Design Blueprint"), 15),
    (("build-planning", "Build Planning"), 10),
    (("implementation", "Implementation"), 30),
    (("qa-integration", "QA & Integration"), 15),
    (("demo-delivery", "Demo & Delivery"), 5),
    (("retrospective-findings", "Retrospective & Findings"), 5),
]


def _slugify(name: str) -> str:
    """Return a kebab-case slug suitable for an epic ID."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    # Collapse repeated hyphens.
    slug = re.sub(r"-+", "-", slug)
    return slug or "epic"


def _find_intake_epics_doc(project_path: Path) -> Path | None:
    """Locate the epic breakdown document produced during Intake & Discovery."""
    candidates = [
        project_path / "docs" / "intake" / "epics.md",
        project_path / "docs" / "epics.md",
        project_path / "epics.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _parse_epics(doc_path: Path) -> list[tuple[str, str]]:
    """Parse epic number/name pairs from an epic breakdown markdown file.

    Supports two common formats, in document order:
      - Table rows: ``| E1 | Epic Name | ... |``
      - Headings:   ``### E1 — Epic Name``
    """
    text = doc_path.read_text(encoding="utf-8")
    found: list[tuple[int, str, str]] = []

    heading_pattern = re.compile(
        r"^###\s*(?:\*\*)?E(\d+)(?:\*\*)?\s*[—–\-]\s*(.+?)\s*$"
    )
    table_pattern = re.compile(
        r"\|\s*(?:\*\*)?E(\d+)(?:\*\*)?\s*\|\s*(?:\*\*)?([^|]+?)(?:\*\*)?\s*\|"
    )

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        match = heading_pattern.match(stripped)
        if match:
            found.append((line_no, match.group(1), match.group(2).strip()))
            continue
        if stripped.startswith("|"):
            match = table_pattern.search(stripped)
            if match:
                found.append((line_no, match.group(1), match.group(2).strip()))

    # Deduplicate by epic number while preserving first-seen document order.
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for _, number, name in sorted(found, key=lambda item: item[0]):
        if number in seen:
            continue
        if not name or name.lower() == "epic":
            continue
        seen.add(number)
        result.append((number, name))
    return result


def _build_work_epic(order: int, number: str, name: str) -> Epic:
    """Create a work epic with the standard 8 lifecycle milestones."""
    slug = _slugify(name)
    epic_id = f"e{number}-{slug}" if slug else f"e{number}"
    milestones = [
        Milestone(
            id=milestone_id,
            name=milestone_name,
            order=milestone_order,
            weight=weight,
            client_visible=True,
            status=MilestoneStatus.NOT_STARTED,
        )
        for milestone_order, ((milestone_id, milestone_name), weight) in enumerate(
            _STANDARD_MILESTONES, start=1
        )
    ]
    return Epic(
        id=epic_id,
        name=name,
        order=order,
        status=MilestoneStatus.NOT_STARTED,
        milestones=milestones,
    )


def maybe_populate_work_epics(
    plan: MilestonePlan,
    project_path: str | Path,
) -> tuple[bool, list[str]]:
    """Create work epics if Intake & Discovery is complete and only process exists.

    Returns ``(created, list_of_epic_names)``.
    """
    if len(plan.epics) != 1:
        return False, []

    process_epic = plan.epics[0]
    intake = process_epic.get_milestone("intake-discovery")
    if intake is None or intake.status != MilestoneStatus.COMPLETED:
        return False, []

    doc = _find_intake_epics_doc(Path(project_path))
    if doc is None:
        return False, []

    parsed = _parse_epics(doc)
    if not parsed:
        return False, []

    new_epics: list[Epic] = [process_epic]
    for idx, (number, name) in enumerate(parsed, start=2):
        new_epics.append(_build_work_epic(idx, number, name))

    plan.epics = new_epics
    return True, [name for _, name in parsed]


def maybe_export_dashboard(
    plan: MilestonePlan,
    project_path: str | Path,
) -> Path | None:
    """Write ``dashboard.html`` to the project root from the current plan."""
    try:
        html = export_dashboard_html(plan)
        target = Path(project_path) / "dashboard.html"
        target.write_text(html, encoding="utf-8")
        return target
    except Exception:
        return None
