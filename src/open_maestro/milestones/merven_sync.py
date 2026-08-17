"""Sync epic/workstream structure from a Merven core project into Maestro's local store."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from open_maestro.milestones.models import Epic, MilestonePlan, MilestoneStatus
from open_maestro.milestones.store import MilestoneStore
from open_maestro.milestones.templates import _STANDARD_MILESTONES


class MervenSyncError(RuntimeError):
    """Raised when syncing milestones from Merven fails."""


def _slugify(text: str) -> str:
    """Convert a display name into a valid Maestro id segment."""
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")
    return cleaned or "epic"


_MERVEN_STATUS_MAP = {
    "pending": MilestoneStatus.NOT_STARTED,
    "in_progress": MilestoneStatus.IN_PROGRESS,
    "done": MilestoneStatus.COMPLETED,
    "acked": MilestoneStatus.COMPLETED,
}


def _status_from_merven(status: str | None) -> MilestoneStatus:
    """Map a Merven milestone status to the Maestro status enum."""
    return _MERVEN_STATUS_MAP.get((status or "").lower(), MilestoneStatus.NOT_STARTED)


# Advancement order for merge: Merven can only push a milestone forward, never regress it.
_STATUS_ADVANCEMENT = {
    MilestoneStatus.NOT_STARTED: 0,
    MilestoneStatus.BLOCKED: 1,
    MilestoneStatus.IN_PROGRESS: 2,
    MilestoneStatus.COMPLETED: 3,
    MilestoneStatus.SKIPPED: 3,
}


def _merge_status(local: MilestoneStatus, merven: MilestoneStatus) -> MilestoneStatus:
    """Return the more advanced of the local and Merven-mapped statuses.

    Why: Merven is the canonical source for project structure and for completions
    recorded via its API, but Maestro's local detector and ``/complete`` commands
    track day-to-day progress. Without merging, every ``maestro --sync-milestones``
    resets locally-recorded progress back to whatever Merven last knew.
    """
    return merven if _STATUS_ADVANCEMENT.get(merven, 0) > _STATUS_ADVANCEMENT.get(local, 0) else local


def _epic_from_merven(epic_view: dict[str, Any], *, order: int) -> Epic:
    """Convert a Merven epic view into a Maestro Epic with standard lifecycle milestones.

    Maestro's taxonomy is: a project contains epics (workstreams/features), and the
    8 standard lifecycle milestones live inside each epic. Merven's own epic-level
    milestone definitions are ignored; only the epic name/order and any statuses that
    can be mapped to Maestro's standard milestones are used.
    """
    epic_name = str(epic_view.get("name", ""))
    epic_id = _slugify(epic_name)

    milestones = [m.model_copy(deep=True) for m in _STANDARD_MILESTONES]

    # If Merven provides milestones that match Maestro's standard lifecycle IDs,
    # import their statuses. This is a forward-compat hook for when Merven aligns
    # its epic milestone taxonomy with Maestro's.
    merven_by_kind: dict[str, MilestoneStatus] = {}
    for ms in epic_view.get("milestones", []):
        kind = str(ms.get("kind", "")).lower()
        status = _status_from_merven(ms.get("status"))
        merven_by_kind[kind] = status

    for milestone in milestones:
        # Try matching by exact id, then by common prefixes like p1/p3/etc.
        mapped_status = merven_by_kind.get(milestone.id)
        if mapped_status is None:
            for kind, status in merven_by_kind.items():
                if kind.endswith(f"-{milestone.id}") or kind == milestone.id.replace("-", ""):
                    mapped_status = status
                    break
        if mapped_status is not None:
            milestone.status = mapped_status

    return Epic(
        id=epic_id,
        name=epic_name,
        order=order,
        status=MilestoneStatus.NOT_STARTED,
        milestones=milestones,
    )


def _plan_from_merven_payload(project_token: str, payload: dict[str, Any]) -> MilestonePlan:
    """Build a Maestro MilestonePlan from a Merven ``GET /projects/{id}`` payload."""
    epics: list[Epic] = []
    for order, epic_view in enumerate(payload.get("epics", []), start=1):
        epics.append(_epic_from_merven(epic_view, order=order))

    # If Merven returns no epics, fall back to a single default epic so the plan
    # is still usable.
    if not epics:
        from open_maestro.milestones.templates import default_software_template
        return default_software_template(
            project_id=_slugify(str(payload.get("project_id", project_token))),
            project_path="",
        )

    return MilestonePlan(
        project_id=_slugify(str(payload.get("project_id", project_token))),
        project_name=str(payload.get("name", "")),
        schema_version="2.0",
        epics=epics,
    )


def sync_from_merven(
    project_path: str,
    *,
    project_token: str | None = None,
    api_url: str | None = None,
    api_key: str | None = None,
) -> MilestonePlan:
    """Fetch the canonical epic structure from Merven and save it locally.

    Why: Merven owns the canonical project/epic structure. Maestro mirrors the
    epics from Merven and applies its standard lifecycle milestones inside each
    epic. Progress updates are aligned with the client contract while Maestro
    retains its own milestone taxonomy.

    What: Reads ``MERVEN_API_URL``, ``MERVEN_API_KEY`` (or
    ``MERVEN_TENANT_DEFAULT_API_KEY``), and ``MAESTRO_DASHBOARD_PROJECT_TOKEN``
    from the environment (or arguments), fetches ``GET /projects/{token}`` from
    Merven, converts the shaped payload to a ``MilestonePlan``, and saves it to
    ``.open-maestro/milestones.yaml``.
    """
    token = project_token or os.environ.get("MAESTRO_DASHBOARD_PROJECT_TOKEN")
    if not token:
        raise MervenSyncError(
            "No project token. Set MAESTRO_DASHBOARD_PROJECT_TOKEN or pass project_token."
        )

    url = (api_url or os.environ.get("MERVEN_API_URL", "")).rstrip("/")
    if not url:
        raise MervenSyncError("No Merven API URL. Set MERVEN_API_URL.")

    key = (
        api_key
        or os.environ.get("MERVEN_API_KEY", "")
        or os.environ.get("MERVEN_TENANT_DEFAULT_API_KEY", "")
    )
    headers: dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        response = httpx.get(f"{url}/projects/{token}", headers=headers, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MervenSyncError(
            f"Merven API returned {exc.response.status_code}: {exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise MervenSyncError(f"Merven API request failed: {exc}") from exc

    payload = response.json()
    plan = _plan_from_merven_payload(token, payload)

    store = MilestoneStore(project_path)
    if store.exists():
        existing = store.load()
        existing_by_key = {
            (e.id, m.id): m
            for e in existing.epics
            for m in e.milestones
        }
        for epic in plan.epics:
            for milestone in epic.milestones:
                local = existing_by_key.get((epic.id, milestone.id))
                if local is not None:
                    milestone.status = _merge_status(local.status, milestone.status)
                    if milestone.status == MilestoneStatus.COMPLETED and local.completed_at:
                        milestone.completed_at = local.completed_at
                    if milestone.status in (MilestoneStatus.IN_PROGRESS, MilestoneStatus.COMPLETED) and local.started_at:
                        milestone.started_at = local.started_at

    store.save(plan)
    return plan
