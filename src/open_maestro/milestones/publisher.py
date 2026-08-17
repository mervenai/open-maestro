"""Dashboard publishing to a remote endpoint (e.g. merven.ai)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from open_maestro.milestones.dashboard import export_dashboard_json
from open_maestro.milestones.models import MilestonePlan

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class PublishError(RuntimeError):
    """Raised when dashboard publishing fails."""


class DashboardPublisher:
    """Publish milestone dashboard snapshots to a remote receiver."""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        project_token: str | None = None,
    ):
        self.url = url or os.environ.get("MAESTRO_DASHBOARD_URL")
        self.api_key = api_key or os.environ.get("MAESTRO_DASHBOARD_API_KEY")
        self.project_token = project_token or os.environ.get(
            "MAESTRO_DASHBOARD_PROJECT_TOKEN"
        )

    def resolve_url(self, explicit_url: str | None = None) -> str:
        """Return the publish URL, raising if none is configured."""
        url = explicit_url or self.url
        if not url:
            raise PublishError(
                "No dashboard publish URL configured. "
                "Set MAESTRO_DASHBOARD_URL or pass --publish-dashboard <url>."
            )
        return url

    def _headers(self) -> dict[str, str]:
        """Build request headers including auth tokens."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.project_token:
            headers["X-Maestro-Project-Token"] = self.project_token
        return headers

    def publish(
        self,
        plan: MilestonePlan,
        *,
        url: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        """Publish the dashboard for *plan* to the remote receiver.

        Returns the JSON response from the receiver.
        """
        target = self.resolve_url(url)
        payload = export_dashboard_json(plan)
        data: dict[str, Any] = {
            "dashboard": json.loads(payload),
        }
        if extra_metadata:
            data["metadata"] = extra_metadata

        try:
            response = httpx.post(
                target,
                json=data,
                headers=self._headers(),
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PublishError(
                f"Dashboard publish failed ({exc.response.status_code}): {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise PublishError(f"Dashboard publish request failed: {exc}") from exc

        logger.info("Dashboard published to %s", target)
        try:
            return response.json()
        except Exception:
            return {"status": "ok", "text": response.text}
