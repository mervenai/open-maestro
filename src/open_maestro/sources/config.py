"""Configuration/registry for agent and skill sources."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from open_maestro.sources.sync import GitSource

logger = logging.getLogger(__name__)

# Default MIT-licensed skill source shipped with Open Maestro.
CLAUDE_MPM_SKILLS_URL = "https://github.com/bobmatnyc/claude-mpm-skills"
DEFAULT_SKILL_SOURCE_NAME = "claude-mpm-skills"
DEFAULT_SKILL_SOURCE_EXCLUDES = [
    "docs/**/*.md",
    ".bundles/**/*.md",
    ".github/**/*.md",
    "scripts/**/*.md",
    "**/README.md",
    "**/CLAUDE.md",
    "**/BUNDLE.md",
]


def _default_skill_source() -> GitSource:
    return GitSource(
        name=DEFAULT_SKILL_SOURCE_NAME,
        url=CLAUDE_MPM_SKILLS_URL,
        kind="skills",
        exclude=list(DEFAULT_SKILL_SOURCE_EXCLUDES),
    )


@dataclass
class SourceRegistry:
    """User's configured agent and skill sources."""

    sources: list[GitSource] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        path: Path | None = None,
    ) -> SourceRegistry:
        """Load the source registry from disk."""
        if path is None:
            path = Path.home() / ".open-maestro" / "sources.yaml"
        path = path.expanduser()

        if not path.exists():
            return cls([_default_skill_source()])

        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("Failed to load source registry %s: %s", path, exc)
            return cls()

        sources: list[GitSource] = []
        for item in data.get("sources", []):
            try:
                sources.append(_source_from_dict(item))
            except Exception as exc:
                logger.warning("Skipping invalid source entry: %s", exc)

        return cls(sources)

    def save(self, path: Path | None = None) -> None:
        """Persist the source registry to disk."""
        if path is None:
            path = Path.home() / ".open-maestro" / "sources.yaml"
        path = path.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "sources": [_source_to_dict(source) for source in self.sources],
        }
        try:
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to save source registry %s: %s", path, exc)
            raise

    def list(self, kind: str | None = None) -> list[GitSource]:
        """Return sources, optionally filtered by kind."""
        if kind is None:
            return list(self.sources)
        return [s for s in self.sources if s.kind == kind]

    def add(self, source: GitSource) -> None:
        """Add or replace a source by name and kind."""
        self.sources = [
            s for s in self.sources if not (s.name == source.name and s.kind == source.kind)
        ]
        self.sources.append(source)

    def remove(self, name: str, kind: str) -> GitSource | None:
        """Remove a source by name and kind, returning it if found."""
        for idx, source in enumerate(self.sources):
            if source.name == name and source.kind == kind:
                return self.sources.pop(idx)
        return None

    def get(self, name: str, kind: str) -> GitSource | None:
        """Return a source by name and kind."""
        for source in self.sources:
            if source.name == name and source.kind == kind:
                return source
        return None


def _source_from_dict(data: dict[str, Any]) -> GitSource:
    return GitSource(
        name=str(data["name"]),
        url=str(data["url"]),
        kind=str(data["kind"]),
        ref=str(data.get("ref", "main")),
        subdir=str(data.get("subdir", "")),
        exclude=list(data.get("exclude", [])),
        last_sync=_parse_datetime(data.get("last_sync")),
        last_remote_head=data.get("last_remote_head"),
        extra=dict(data.get("extra", {})),
    )


def _source_to_dict(source: GitSource) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": source.name,
        "url": source.url,
        "kind": source.kind,
        "ref": source.ref,
    }
    if source.subdir:
        result["subdir"] = source.subdir
    if source.exclude:
        result["exclude"] = list(source.exclude)
    if source.last_sync is not None:
        result["last_sync"] = source.last_sync.isoformat()
    if source.last_remote_head:
        result["last_remote_head"] = source.last_remote_head
    if source.extra:
        result["extra"] = source.extra
    return result


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def default_source_name(url: str, kind: str) -> str:
    """Derive a short source name from a Git URL."""
    # Strip .git suffix and trailing slashes.
    base = url.rstrip("/").removesuffix(".git")
    # Take the last path component.
    name = base.split("/")[-1] or "source"
    # If the repo is the generic claude-mpm-skills/agents, namespace by kind.
    if name in ("claude-mpm-skills", "claude-mpm-agents"):
        return name.replace("claude-mpm-", "")
    return name
