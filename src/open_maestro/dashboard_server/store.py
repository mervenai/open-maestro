"""Persistent storage for published dashboard snapshots."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DashboardStore:
    """Store dashboard snapshots on disk, keyed by project token.

    Each project gets a single JSON file (``<token>.json``). A new publish
    overwrites the previous snapshot, keeping the latest dashboard state.
    """

    def __init__(self, data_dir: Path | str | None = None) -> None:
        if data_dir is None:
            data_dir = Path.cwd() / ".open-maestro" / "dashboards"
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, project_token: str) -> Path:
        # Sanitize token for filesystem safety.
        safe = "".join(c for c in project_token if c.isalnum() or c in "_-.")
        if not safe:
            raise ValueError("Invalid project token")
        return self.data_dir / f"{safe}.json"

    def save(
        self,
        project_token: str,
        dashboard: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a dashboard snapshot for *project_token*."""
        payload = {
            "project_token": project_token,
            "dashboard": dashboard,
            "metadata": metadata or {},
        }
        path = self._path(project_token)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info("Saved dashboard snapshot for %s", project_token)

    def load(self, project_token: str) -> dict[str, Any] | None:
        """Return the stored snapshot for *project_token*, or None."""
        path = self._path(project_token)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load dashboard for %s: %s", project_token, exc)
            return None

    def delete(self, project_token: str) -> bool:
        """Delete the stored snapshot for *project_token*."""
        path = self._path(project_token)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_tokens(self) -> list[str]:
        """Return all stored project tokens."""
        tokens: list[str] = []
        for path in self.data_dir.glob("*.json"):
            tokens.append(path.stem)
        return sorted(tokens)
