"""Sync epic/workstream structure from a Merven core project into Maestro's local store."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from open_maestro.milestones.models import Epic, Milestone, MilestonePlan, MilestoneStatus
from open_maestro.milestones.store import MilestoneStore
from open_maestro.milestones.templates import _STANDARD_MILESTONES

logger = logging.getLogger(__name__)


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


def _milestone_template(
    existing: MilestonePlan | None,
) -> list[Milestone] | None:
    """Return a deep copy of the milestone definitions from an existing local plan.

    Why: Merven owns the canonical epic list, but Maestro owns the milestone
    taxonomy inside each epic. If the user has already defined custom milestones,
    syncing should keep those definitions and only add/remove epics or update
    statuses. This is a fallback for epics that Merven returns without milestone
    details.
    """
    if existing is None or not existing.epics:
        return None
    for epic in existing.epics:
        if epic.milestones:
            return [m.model_copy(deep=True) for m in epic.milestones]
    return None


# Default weights for the Merven P-milestones when Merven provides them.
_P_MILESTONE_WEIGHTS: dict[str, int] = {
    "p1": 10,
    "p3": 10,
    "p4": 15,
    "p5": 10,
    "p6": 30,
    "p8": 15,
    "p9": 5,
    "p10": 5,
}


def _milestone_from_merven(ms: dict[str, Any], order: int) -> Milestone:
    """Convert a single Merven epic milestone into a Maestro Milestone."""
    kind = str(ms.get("kind", "")).lower()
    title = str(ms.get("title", "")).strip()
    name = title or kind.upper()
    return Milestone(
        id=kind,
        name=name,
        order=order,
        weight=_P_MILESTONE_WEIGHTS.get(kind, 10),
        client_visible=True,
        status=_status_from_merven(ms.get("status")),
        artifacts=[],
        exit_criteria=[],
        blockers=[],
        notes="",
    )


def _status_for_milestone(
    milestone_id: str, merven_by_kind: dict[str, MilestoneStatus]
) -> MilestoneStatus | None:
    """Map a Merven milestone kind to a status for *milestone_id*.

    Matching order:
    1. Exact kind == milestone id
    2. Kind ends with "-<milestone_id>"
    3. Normalized kind (no dashes) == normalized milestone id
    4. Kind starts with milestone id followed by a delimiter
    """
    lowered = milestone_id.lower()
    normalized = lowered.replace("-", "")
    candidates = [lowered, f"p{lowered}"]
    for kind, status in merven_by_kind.items():
        kind_norm = kind.replace("-", "")
        if kind in candidates or kind.endswith(f"-{lowered}"):
            return status
        if kind_norm == normalized:
            return status
        if (
            kind.startswith(lowered)
            and len(kind) > len(lowered)
            and kind[len(lowered)] in "-_|:"
        ):
            return status
    return None


def _epic_from_merven(
    epic_view: dict[str, Any],
    *,
    order: int,
    milestone_template: list[Milestone] | None = None,
) -> Epic:
    """Convert a Merven epic view into a Maestro Epic.

    Merven is the canonical source for both the epic list and the milestone
    lifecycle inside each epic. When Merven returns milestones for an epic, those
    are used directly (kind → id, title → name, status mapped). When Merven does
    not return milestones, the local template or the built-in 8 standard lifecycle
    milestones are used as a fallback.
    """
    epic_name = str(epic_view.get("name", ""))
    epic_id = _slugify(epic_name)

    merven_milestones = epic_view.get("milestones", [])
    if merven_milestones:
        milestones = [
            _milestone_from_merven(ms, order=idx)
            for idx, ms in enumerate(merven_milestones, start=1)
        ]
    else:
        source_milestones = milestone_template or _STANDARD_MILESTONES
        milestones = [m.model_copy(deep=True) for m in source_milestones]
        # Reset statuses from the template so sync does not carry stale state into
        # new epics; locally-merged statuses will be applied next.
        for milestone in milestones:
            milestone.status = MilestoneStatus.NOT_STARTED

        # If Merven provides milestones that match Maestro's fallback ids, import
        # their statuses. This is a forward-compat hook for when Merven aligns its
        # epic milestone taxonomy with Maestro's fallback template.
        merven_by_kind: dict[str, MilestoneStatus] = {}
        for ms in merven_milestones:
            kind = str(ms.get("kind", "")).lower()
            status = _status_from_merven(ms.get("status"))
            merven_by_kind[kind] = status

        for milestone in milestones:
            mapped_status = _status_for_milestone(milestone.id, merven_by_kind)
            if mapped_status is not None:
                milestone.status = mapped_status

    return Epic(
        id=epic_id,
        name=epic_name,
        order=order,
        status=MilestoneStatus.NOT_STARTED,
        milestones=milestones,
    )


def _plan_from_merven_payload(
    project_token: str,
    payload: dict[str, Any],
    milestone_template: list[Milestone] | None = None,
) -> MilestonePlan:
    """Build a Maestro MilestonePlan from a Merven ``GET /projects/{id}`` payload."""
    epics: list[Epic] = []
    for order, epic_view in enumerate(payload.get("epics", []), start=1):
        epics.append(
            _epic_from_merven(
                epic_view, order=order, milestone_template=milestone_template
            )
        )

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


def _normalize_api_url(url: str) -> str:
    """Return the Merven core API base URL.

    Older documentation told users to set ``MERVEN_API_URL`` to
    ``https://api.staging.merven.ai/maestro``. The core engagement endpoints
    (``/projects``) live at the API root, so strip a trailing ``/maestro``
    segment and warn.
    """
    url = url.rstrip("/")
    if url.endswith("/maestro"):
        logger.warning(
            "MERVEN_API_URL ends with /maestro; using the core API root instead."
        )
        url = url[: -len("/maestro")]
    return url


def _resolve_project_id_from_dashboard(
    core_url: str,
    project_token: str,
    headers: dict[str, str],
    dashboard_url: str | None = None,
) -> str | None:
    """Try to map a project token to a project ID via the dashboard snapshot."""
    dashboard_base = (dashboard_url or os.environ.get("MAESTRO_DASHBOARD_URL", "")).rstrip("/")
    if not dashboard_base:
        dashboard_base = f"{core_url}/maestro/dashboard"

    try:
        response = httpx.get(
            f"{dashboard_base}/{project_token}",
            headers=headers,
            timeout=30.0,
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:
        return None

    # The dashboard payload is either {dashboard: {...}} or {...} directly.
    dashboard = data.get("dashboard") if isinstance(data, dict) else None
    if dashboard is None:
        dashboard = data
    if isinstance(dashboard, dict):
        project_id = dashboard.get("project_id")
        if project_id:
            return str(project_id)
    return None


def sync_from_merven(
    project_path: str,
    *,
    project_id: str | None = None,
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
    ``MERVEN_TENANT_DEFAULT_API_KEY``), and ``MAESTRO_PROJECT_ID`` /
    ``MAESTRO_DASHBOARD_PROJECT_TOKEN`` from the environment (or arguments),
    fetches ``GET /projects/{project_id}`` from Merven, converts the shaped
    payload to a ``MilestonePlan``, and saves it to ``.open-maestro/milestones.yaml``.
    """
    token_or_id = (
        project_id
        or project_token
        or os.environ.get("MAESTRO_PROJECT_ID")
        or os.environ.get("MAESTRO_DASHBOARD_PROJECT_TOKEN")
    )
    if not token_or_id:
        raise MervenSyncError(
            "No project ID or token. Set MAESTRO_PROJECT_ID or MAESTRO_DASHBOARD_PROJECT_TOKEN."
        )

    url = _normalize_api_url(
        api_url or os.environ.get("MERVEN_API_URL", "")
    )
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

    resolved_id: str | None = None
    try:
        response = httpx.get(
            f"{url}/projects/{token_or_id}", headers=headers, timeout=30.0
        )
        if response.status_code == 404:
            # The supplied value may be a dashboard project token. Try to
            # resolve it to a project ID via the dashboard snapshot.
            resolved_id = _resolve_project_id_from_dashboard(
                url, token_or_id, headers
            )
            if resolved_id and resolved_id != token_or_id:
                logger.info(
                    "Resolved project token %s to project ID %s", token_or_id, resolved_id
                )
                response = httpx.get(
                    f"{url}/projects/{resolved_id}", headers=headers, timeout=30.0
                )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise MervenSyncError(
            f"Merven API returned {exc.response.status_code}: {exc.response.text}"
        ) from exc
    except httpx.RequestError as exc:
        raise MervenSyncError(f"Merven API request failed: {exc}") from exc

    payload = response.json()
    effective_project_id = str(payload.get("project_id") or resolved_id or token_or_id)

    store = MilestoneStore(project_path)
    existing = store.load() if store.exists() else None
    template = _milestone_template(existing)
    merven_plan = _plan_from_merven_payload(
        effective_project_id, payload, milestone_template=template
    )

    # Merven owns the canonical project/epic list, but Maestro owns the milestone
    # taxonomy inside each epic. Preserve locally-defined epics (and their
    # milestone definitions) while merging forward any statuses Merven reports.
    if existing is None:
        plan = merven_plan
    else:
        final_epics: list[Epic] = []
        existing_by_id = {e.id: e for e in existing.epics}
        merven_by_id = {e.id: e for e in merven_plan.epics}

        # 1. Start from local epics in their current order. Update statuses from
        #    Merven for matching (epic, milestone) pairs but keep local names,
        #    weights, artifacts, exit criteria, blockers, and dates.
        for existing_epic in existing.epics:
            epic = existing_epic.model_copy(deep=True)
            merven_epic = merven_by_id.get(epic.id)
            if merven_epic is not None:
                merven_status_by_kind = {
                    ms.id: ms.status for ms in merven_epic.milestones
                }
                for milestone in epic.milestones:
                    mapped = _status_for_milestone(milestone.id, merven_status_by_kind)
                    if mapped is not None:
                        milestone.status = _merge_status(milestone.status, mapped)
            final_epics.append(epic)

        # 2. Append any epics Merven knows about that do not exist locally.
        for merven_epic in merven_plan.epics:
            if merven_epic.id not in existing_by_id:
                final_epics.append(merven_epic.model_copy(deep=True))

        # 3. Recompute order numbers so they stay contiguous.
        for idx, epic in enumerate(final_epics, start=1):
            epic.order = idx
            for m_idx, milestone in enumerate(epic.milestones, start=1):
                milestone.order = m_idx

        plan = MilestonePlan(
            project_id=merven_plan.project_id,
            project_name=merven_plan.project_name or existing.project_name,
            project_path=existing.project_path,
            schema_version="2.0",
            epics=final_epics,
        )

    store.save(plan)
    return plan
