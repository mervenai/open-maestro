"""Session persistence for Open Maestro.

Sessions are stored as individual YAML files under project- and user-level
directories so resumed or forked runs can pick up where a previous run left off.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SessionRecord:
    """A persisted snapshot of an agent session."""

    session_id: str
    runtime_name: str
    agent_id: str | None
    model: str | None
    prompt_summary: str
    created_at: datetime
    updated_at: datetime
    resumed_from: str | None = None
    forked_from: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        data["updated_at"] = self.updated_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRecord:
        kwargs = dict(data)
        kwargs["created_at"] = datetime.fromisoformat(kwargs["created_at"])
        kwargs["updated_at"] = datetime.fromisoformat(kwargs["updated_at"])
        return cls(**kwargs)


class SessionStore:
    """Load and save session records to local YAML files."""

    def __init__(self, base_dirs: list[Path] | None = None) -> None:
        if base_dirs is None:
            base_dirs = [
                Path.cwd() / ".open-maestro" / "sessions",
                Path.home() / ".open-maestro" / "sessions",
            ]
        self._base_dirs = [Path(d).expanduser().resolve() for d in base_dirs]

    def _record_path(self, session_id: str, base_dir: Path) -> Path:
        # Slugify the session id to avoid path traversal.
        safe_id = (
            session_id.replace("/", "_")
            .replace("\\", "_")
            .replace("..", "_")
            .strip(".")
        )
        return base_dir / f"{safe_id}.yaml"

    def _find_existing(self, session_id: str) -> Path | None:
        for base_dir in self._base_dirs:
            path = self._record_path(session_id, base_dir)
            if path.exists():
                return path
        return None

    def _writable_dir(self) -> Path:
        """Return the first base directory that exists or can be created."""
        for base_dir in self._base_dirs:
            try:
                base_dir.mkdir(parents=True, exist_ok=True)
                return base_dir
            except OSError:
                logger.warning("Cannot create session directory %s", base_dir)
                continue
        raise RuntimeError(
            f"Cannot create any session storage directory: {self._base_dirs}"
        )

    def save(self, record: SessionRecord) -> Path:
        """Persist *record* atomically, creating or updating as needed."""
        existing_path = self._find_existing(record.session_id)
        base_dir = existing_path.parent if existing_path else self._writable_dir()
        path = self._record_path(record.session_id, base_dir)

        if existing_path is None:
            record.created_at = record.updated_at

        try:
            # Atomic write: create a temp file in the same directory, then rename.
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".yaml",
                prefix=f".{record.session_id}-",
                dir=base_dir,
                delete=False,
                encoding="utf-8",
            ) as f:
                yaml.safe_dump(record.to_dict(), f, sort_keys=False)
                temp_path = Path(f.name)
            temp_path.replace(path)
        except OSError as exc:
            logger.warning("Failed to save session %s: %s", record.session_id, exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        logger.debug("Saved session %s to %s", record.session_id, path)
        return path

    def get(self, session_id: str) -> SessionRecord | None:
        """Load a session record by id, or None if not found."""
        path = self._find_existing(session_id)
        if path is None:
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return SessionRecord.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load session %s: %s", session_id, exc)
            return None

    def list_recent(self, limit: int = 10) -> list[SessionRecord]:
        """Return the most recently updated sessions across all base dirs."""
        records: list[SessionRecord] = []
        seen: set[str] = set()
        for base_dir in self._base_dirs:
            if not base_dir.exists():
                continue
            for path in base_dir.glob("*.yaml"):
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    record = SessionRecord.from_dict(data)
                except Exception as exc:
                    logger.warning("Skipping unreadable session %s: %s", path, exc)
                    continue
                if record.session_id in seen:
                    continue
                seen.add(record.session_id)
                records.append(record)
        records.sort(key=lambda r: r.updated_at, reverse=True)
        return records[:limit]

    def prune(self, keep: int = 50) -> list[Path]:
        """Remove old session files, keeping the *keep* most recent across all dirs.

        Returns the paths of removed files.
        """
        all_records = self.list_recent(limit=max(keep, 0) + 1)
        keep_ids = {r.session_id for r in all_records[:keep]}
        removed: list[Path] = []
        for base_dir in self._base_dirs:
            if not base_dir.exists():
                continue
            for path in base_dir.glob("*.yaml"):
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    session_id = data.get("session_id", path.stem)
                except Exception as exc:
                    logger.warning("Skipping unreadable session %s: %s", path, exc)
                    continue
                if session_id not in keep_ids:
                    try:
                        path.unlink()
                        removed.append(path)
                    except OSError as exc:
                        logger.warning("Failed to prune session %s: %s", path, exc)
        return removed
