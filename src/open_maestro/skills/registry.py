"""Skill registry and discovery.

Skills are reusable instruction modules stored as Markdown files with YAML
frontmatter.  Agents declare the skills they need via the ``skills:`` field in
their frontmatter; the loader appends the full skill content to the agent's
system prompt.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from open_maestro.agents.definition import AgentDefinition


@dataclass
class Skill:
    """A reusable skill / instruction module."""

    id: str
    name: str
    tags: list[str]
    content: str

    @property
    def entry(self) -> str:
        """Return the first non-empty paragraph as a short summary."""
        for line in self.content.splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    @classmethod
    def from_markdown(cls, path: str | Path) -> Skill:
        """Parse a skill definition from a Markdown file with YAML frontmatter."""
        path = Path(path)
        raw = path.read_text(encoding="utf-8")

        if raw.startswith("---"):
            _, frontmatter, body = raw.split("---", 2)
        else:
            frontmatter = ""
            body = raw

        meta = yaml.safe_load(frontmatter) or {}
        content = _extract_skill_content(body)

        return cls(
            id=meta.get("id", path.stem),
            name=meta.get("name", path.stem),
            tags=_normalize_str_list(meta.get("tags", [])),
            content=content,
        )


class SkillRegistry:
    """Load and query vendor-neutral skill definitions."""

    def __init__(self, skills: dict[str, Skill] | None = None):
        self._skills: dict[str, Skill] = skills or {}

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        exclude: list[str] | None = None,
    ) -> SkillRegistry:
        """Load all ``.md`` skill definitions from a directory tree.

        Paths matching any glob in *exclude* (interpreted relative to
        *directory*) are skipped.
        """
        directory = Path(directory)
        excludes = exclude or []
        skills: dict[str, Skill] = {}
        for path in directory.rglob("*.md"):
            rel = path.relative_to(directory)
            if any(_matches_glob(rel, pattern) for pattern in excludes):
                continue
            try:
                skill = Skill.from_markdown(path)
                skills[skill.id] = skill
            except Exception as exc:
                raise RuntimeError(f"Failed to load skill from {path}: {exc}") from exc
        return cls(skills)

    @classmethod
    def load_tiered_dirs(
        cls,
        project_dir: Path | None,
        user_dir: Path | None,
        bundled_dir: Path,
    ) -> SkillRegistry:
        """Load skills from the three tiers with project > user > bundled precedence."""
        registry: SkillRegistry = cls()
        for directory in (bundled_dir, user_dir, project_dir):
            if directory is not None and directory.exists():
                tier = cls.from_directory(directory)
                merged = dict(registry._skills)
                merged.update(tier._skills)
                registry = cls(merged)
        return registry

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def resolve_for_agent(self, agent: AgentDefinition) -> list[Skill]:
        """Return the skills referenced by *agent*, in declaration order."""
        skills: list[Skill] = []
        seen: set[str] = set()
        for skill_id in agent.skills:
            if skill_id in seen:
                continue
            seen.add(skill_id)
            skill = self.get(skill_id)
            if skill is not None:
                skills.append(skill)
        return skills

    def list(self) -> list[Skill]:
        return list(self._skills.values())


def _matches_glob(rel: Path, pattern: str) -> bool:
    """Return True if *rel* matches *pattern* using shell glob semantics.

    ``**`` matches zero or more whole path components, while ``*`` and ``?``
    match only within a single component.
    """
    rel_parts = rel.parts
    pat_parts = pattern.split("/")

    def _match_component(name: str, pat: str) -> bool:
        return fnmatch.fnmatchcase(name, pat)

    def match(i: int, j: int) -> bool:
        while j < len(pat_parts):
            pat = pat_parts[j]
            if pat == "**":
                # Try consuming zero or more path components.
                for k in range(i, len(rel_parts) + 1):
                    if match(k, j + 1):
                        return True
                return False
            if i >= len(rel_parts):
                return False
            if not _match_component(rel_parts[i], pat):
                return False
            i += 1
            j += 1
        return i == len(rel_parts)

    return match(0, 0)


def _extract_skill_content(body: str) -> str:
    """Strip leading whitespace and a title line, keep the rest as content."""
    body = body.strip()
    if not body:
        return ""
    body = re.sub(r"^#\s+.*\n?", "", body, count=1)
    return body.strip()


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]
