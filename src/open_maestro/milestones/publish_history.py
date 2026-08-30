"""Local record of the last dashboard publish for a project.

This is separate from the remote dashboard receiver; it just lets the CLI show
when a project dashboard was last published and to which URL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PublishHistoryRecord:
    """One published snapshot record."""

    url: str
    project_token: str = ""
    published_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "project_token": self.project_token,
            "published_at": self.published_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublishHistoryRecord":
        return cls(
            url=str(data.get("url", "")),
            project_token=str(data.get("project_token", "")),
            published_at=datetime.fromisoformat(str(data["published_at"])),
        )


class DashboardPublishHistoryStore:
    """Persist last dashboard publish metadata to the project directory."""

    FILENAME = "dashboard_publish_history.yaml"

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.file_path = self.project_path / ".open-maestro" / self.FILENAME

    def exists(self) -> bool:
        return self.file_path.exists()

    def load(self) -> PublishHistoryRecord | None:
        if not self.exists():
            return None
        try:
            raw = yaml.safe_load(self.file_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        try:
            return PublishHistoryRecord.from_dict(raw)
        except Exception:
            return None

    def save(self, record: PublishHistoryRecord) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            yaml.safe_dump(record.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def record(self, url: str, project_token: str = "") -> PublishHistoryRecord:
        rec = PublishHistoryRecord(url=url, project_token=project_token)
        self.save(rec)
        return rec

    def format_last(self) -> str | None:
        """Return a short human-readable line for the interactive banner."""
        rec = self.load()
        if rec is None:
            return None
        when = rec.published_at.strftime("%Y-%m-%d %H:%M")
        token = f" ({rec.project_token})" if rec.project_token else ""
        return f"Dashboard last published{token} at {when} to {rec.url}"
