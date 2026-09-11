"""Supabase dashboard publishing for the Lovable-hosted frontend (merven.ai).

Why: the Merven-core receiver is being retired. The new mechanism (see
``docs/dashboard-infra-setup.md``) stores dashboard snapshots in a Supabase
``maestro_dashboards`` table; the merven.ai Lovable app renders them at
``/dashboard/<project_token>``. Publishing uses the Supabase REST API so no
extra dependency is needed beyond httpx.
What: ``SupabaseDashboardPublisher`` — upserts a snapshot keyed by a random
per-project token persisted in ``.open-maestro/config.yaml``.
Test: first publish generates and persists a >=32-char token; later publishes
reuse it; the upsert hits ``/rest/v1/maestro_dashboards?on_conflict=project_token``.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from open_maestro.milestones.dashboard import export_dashboard_json
from open_maestro.milestones.models import MilestonePlan
from open_maestro.milestones.publish_history import DashboardPublishHistoryStore
from open_maestro.milestones.publisher import PublishError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_PUBLIC_BASE = "https://merven.ai/dashboard"
TABLE = "maestro_dashboards"


def _project_config_path(project_path: Path) -> Path:
    return Path(project_path) / ".open-maestro" / "config.yaml"


def load_project_token(project_path: Path) -> str | None:
    """Return the persisted dashboard project token, if any."""
    path = _project_config_path(project_path)
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        token = raw.get("dashboard", {}).get("project_token")
        return token if isinstance(token, str) and token else None
    except Exception as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def save_project_token(project_path: Path, token: str) -> None:
    """Persist the dashboard project token, preserving other config keys."""
    path = _project_config_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("Could not parse %s, starting fresh: %s", path, exc)
            raw = {}
    raw.setdefault("dashboard", {})
    raw["dashboard"]["project_token"] = token
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def resolve_project_token(project_path: Path | None) -> str:
    """Return the existing token or generate a new random one (>=32 chars)."""
    if project_path:
        existing = load_project_token(Path(project_path))
        if existing:
            return existing
    token = secrets.token_urlsafe(24)  # 32 url-safe characters
    if project_path:
        save_project_token(Path(project_path), token)
        logger.info("Generated new dashboard project token for %s", project_path)
    return token


class SupabaseDashboardPublisher:
    """Publish milestone dashboard snapshots to Supabase.

    Credentials come from ``MAESTRO_SUPABASE_URL`` and
    ``MAESTRO_SUPABASE_SERVICE_KEY`` (or constructor args). The public render
    URL is ``<public_base>/<project_token>`` where public_base defaults to
    ``https://merven.ai/dashboard`` and can be overridden with
    ``MAESTRO_DASHBOARD_PUBLIC_BASE``.
    """

    def __init__(
        self,
        url: str | None = None,
        service_key: str | None = None,
        public_base: str | None = None,
    ):
        self.url = (url or os.environ.get("MAESTRO_SUPABASE_URL") or "").rstrip("/")
        self.service_key = service_key or os.environ.get(
            "MAESTRO_SUPABASE_SERVICE_KEY"
        )
        self.public_base = (
            public_base
            or os.environ.get("MAESTRO_DASHBOARD_PUBLIC_BASE")
            or DEFAULT_PUBLIC_BASE
        ).rstrip("/")

    def _require_credentials(self) -> None:
        missing = []
        if not self.url:
            missing.append("MAESTRO_SUPABASE_URL")
        if not self.service_key:
            missing.append("MAESTRO_SUPABASE_SERVICE_KEY")
        if missing:
            raise PublishError(
                "Supabase publishing is not configured. Set "
                + " and ".join(missing)
                + ". See docs/dashboard-infra-setup.md."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "apikey": self.service_key or "",
            "Authorization": f"Bearer {self.service_key}",
            # Merge on project_token conflict and return the upserted row.
            "Prefer": "resolution=merge-duplicates,return=representation",
        }

    def publish(
        self,
        plan: MilestonePlan,
        *,
        project_token: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Upsert the dashboard snapshot for *plan* into Supabase.

        Returns ``{"status": "ok", "project_token": ..., "public_url": ...}``.
        """
        self._require_credentials()
        token = project_token or resolve_project_token(
            Path(plan.project_path) if plan.project_path else None
        )

        metadata = {"source": "maestro-cli"}
        if extra_metadata:
            metadata.update(extra_metadata)
        now = datetime.now(UTC).isoformat()

        row = {
            "project_token": token,
            "dashboard_json": json.loads(export_dashboard_json(plan)),
            "metadata": metadata,
            "published_at": now,
            "updated_at": now,
        }

        endpoint = f"{self.url}/rest/v1/{TABLE}?on_conflict=project_token"
        try:
            response = httpx.post(
                endpoint, json=row, headers=self._headers(), timeout=timeout
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PublishError(
                f"Supabase publish failed ({exc.response.status_code}): "
                f"{exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise PublishError(f"Supabase publish request failed: {exc}") from exc

        logger.info("Dashboard published to Supabase (token=%s...)", token[:8])
        try:
            if plan.project_path:
                history_store = DashboardPublishHistoryStore(plan.project_path)
                history_store.record(url=endpoint, project_token=token)
        except Exception as exc:
            logger.debug("Failed to record dashboard publish history: %s", exc)

        return {
            "status": "ok",
            "project_token": token,
            "public_url": f"{self.public_base}/{token}",
        }
