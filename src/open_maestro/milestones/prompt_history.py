"""Persistent run history for suggested playbook prompts.

Why: ``/next`` suggests reusable milestone prompts. Users want to see which
prompts they have already run (and when) while still being able to rerun them.
What: A small YAML-backed store keyed by ``epic_id/milestone_id/prompt_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PromptRunRecord:
    """One prompt's run history."""

    epic_id: str
    milestone_id: str
    prompt_id: str
    prompt_title: str
    last_run_at: datetime
    run_count: int = 1
    edited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "epic_id": self.epic_id,
            "milestone_id": self.milestone_id,
            "prompt_id": self.prompt_id,
            "prompt_title": self.prompt_title,
            "last_run_at": self.last_run_at.isoformat(),
            "run_count": self.run_count,
            "edited": self.edited,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptRunRecord:
        return cls(
            epic_id=str(data.get("epic_id", "")),
            milestone_id=str(data.get("milestone_id", "")),
            prompt_id=str(data.get("prompt_id", "")),
            prompt_title=str(data.get("prompt_title", "")),
            last_run_at=datetime.fromisoformat(str(data["last_run_at"])),
            run_count=int(data.get("run_count", 1)),
            edited=bool(data.get("edited", False)),
        )


@dataclass
class PromptRunHistory:
    """YAML-backed collection of prompt run records."""

    runs: dict[str, PromptRunRecord] = field(default_factory=dict)

    @staticmethod
    def _key(epic_id: str, milestone_id: str, prompt_id: str) -> str:
        return f"{epic_id}/{milestone_id}/{prompt_id}"

    def record(
        self,
        epic_id: str,
        milestone_id: str,
        prompt_id: str,
        prompt_title: str,
        edited: bool = False,
    ) -> PromptRunRecord:
        """Record that a prompt was run. Updates run_count and last_run_at."""
        key = self._key(epic_id, milestone_id, prompt_id)
        now = datetime.now()
        if key in self.runs:
            existing = self.runs[key]
            existing.run_count += 1
            existing.last_run_at = now
            existing.edited = existing.edited or edited
            return existing

        record = PromptRunRecord(
            epic_id=epic_id,
            milestone_id=milestone_id,
            prompt_id=prompt_id,
            prompt_title=prompt_title,
            last_run_at=now,
            run_count=1,
            edited=edited,
        )
        self.runs[key] = record
        return record

    def get(
        self, epic_id: str, milestone_id: str, prompt_id: str
    ) -> PromptRunRecord | None:
        return self.runs.get(self._key(epic_id, milestone_id, prompt_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "runs": [r.to_dict() for r in self.runs.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptRunHistory:
        runs = {
            cls._key(
                r.epic_id, r.milestone_id, r.prompt_id
            ): r
            for r in (
                PromptRunRecord.from_dict(item)
                for item in data.get("runs", [])
            )
        }
        return cls(runs=runs)


class PromptHistoryStore:
    """Load and save prompt run history to ``.open-maestro/prompt_history.yaml``."""

    FILENAME = "prompt_history.yaml"

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.file_path = self.project_path / ".open-maestro" / self.FILENAME

    def exists(self) -> bool:
        return self.file_path.exists()

    def load(self) -> PromptRunHistory:
        if not self.exists():
            return PromptRunHistory()
        try:
            raw = yaml.safe_load(self.file_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load prompt history from {self.file_path}: {exc}"
            ) from exc
        return PromptRunHistory.from_dict(raw)

    def save(self, history: PromptRunHistory) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.file_path.write_text(
                yaml.safe_dump(history.to_dict(), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to save prompt history to {self.file_path}: {exc}"
            ) from exc

    def record(
        self,
        epic_id: str,
        milestone_id: str,
        prompt_id: str,
        prompt_title: str,
        edited: bool = False,
    ) -> PromptRunRecord:
        """Load, record a run, and save atomically."""
        history = self.load()
        record = history.record(
            epic_id=epic_id,
            milestone_id=milestone_id,
            prompt_id=prompt_id,
            prompt_title=prompt_title,
            edited=edited,
        )
        self.save(history)
        return record


def format_run_indicator(record: PromptRunRecord | None) -> str:
    """Return a short human-readable 'Ran' label for a prompt."""
    if record is None:
        return ""
    when = record.last_run_at.strftime("%Y-%m-%d %H:%M")
    if record.run_count > 1:
        return f" [Ran {record.run_count}x, last {when}]"
    return f" [Ran {when}]"
