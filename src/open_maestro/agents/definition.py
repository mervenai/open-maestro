"""Vendor-neutral agent definition model."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from open_maestro.config.capabilities import RequiredCapabilities


@dataclass
class AgentDefinition:
    """A vendor-neutral agent definition.

    Agents are stored as Markdown files with YAML frontmatter.  The content
    after the frontmatter becomes the agent's system prompt / instructions.
    """

    id: str
    name: str
    role: str
    model: str = "default"
    extends: str | None = None
    tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    max_turns: int | None = None
    required_capabilities: RequiredCapabilities = field(
        default_factory=RequiredCapabilities
    )
    description: str = ""
    instructions: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def system_prompt(self) -> str:
        """Return the full system prompt for the agent."""
        parts: list[str] = []
        if self.description:
            parts.append(self.description)
        if self.instructions:
            parts.append(self.instructions)
        return "\n\n".join(parts)

    @staticmethod
    def merge(base: AgentDefinition, child: AgentDefinition) -> AgentDefinition:
        """Return a new agent inheriting from *base* and overridden by *child*.

        Child values win for scalars. Lists are unioned. Required capabilities
        are merged field-by-field. System prompts are concatenated with the
        base prompt first.
        """
        base_sp = base.system_prompt
        child_sp = child.system_prompt
        merged_instructions = (
            f"{base_sp}\n\n{child_sp}"
            if base_sp and child_sp
            else (child_sp or base_sp)
        )

        return AgentDefinition(
            id=child.id,
            name=child.name or base.name,
            role=child.role or base.role,
            model=base.model if child.model == "default" else child.model,
            extends=None,
            tools=_union_lists(base.tools, child.tools),
            blocked_tools=_union_lists(base.blocked_tools, child.blocked_tools),
            skills=_union_lists(base.skills, child.skills),
            max_turns=(
                child.max_turns if child.max_turns is not None else base.max_turns
            ),
            required_capabilities=base.required_capabilities.merge(
                child.required_capabilities
            ),
            description=child.description or base.description,
            instructions=merged_instructions,
            extra={**base.extra, **child.extra},
        )

    def to_config(self) -> dict[str, Any]:
        """Return a dictionary suitable for building an AgentConfig."""
        return {
            "system_prompt": self.system_prompt,
            "model": self.model,
            "allowed_tools": self.tools or None,
            "blocked_tools": set(self.blocked_tools),
            "max_turns": self.max_turns,
            "required_capabilities": self.required_capabilities,
            "extra": self.extra,
        }

    @classmethod
    def from_markdown(cls, path: str | Path) -> AgentDefinition:
        """Parse an agent definition from a Markdown file with YAML frontmatter."""
        path = Path(path)
        raw = path.read_text(encoding="utf-8")

        if raw.startswith("---"):
            _, frontmatter, body = raw.split("---", 2)
        else:
            frontmatter = ""
            body = raw

        meta = yaml.safe_load(frontmatter) or {}
        instructions = _extract_instructions(body)

        return cls(
            id=meta.get("id", path.stem),
            name=meta.get("name", path.stem),
            role=meta.get("role", "specialized"),
            model=meta.get("model", "default"),
            extends=meta.get("extends"),
            tools=_normalize_list(meta.get("tools")),
            blocked_tools=_normalize_list(meta.get("blocked_tools")),
            skills=_normalize_list(meta.get("skills")),
            max_turns=meta.get("max_turns"),
            required_capabilities=RequiredCapabilities.from_dict(
                meta.get("required_capabilities")
            ),
            description=meta.get("description", ""),
            instructions=instructions,
            extra=meta.get("extra", {}),
        )


def _extract_instructions(body: str) -> str:
    """Extract the instructions section from the agent markdown body."""
    # Strip leading whitespace and common headers, keep the rest as instructions.
    body = body.strip()
    if not body:
        return ""
    # Remove a leading '# Title' line if present
    body = re.sub(r"^#\s+.*\n?", "", body, count=1)
    return body.strip()


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _union_lists(base: list[str], override: list[str]) -> list[str]:
    """Return the union of two lists, preserving order and giving *override* priority."""
    seen: set[str] = set()
    result: list[str] = []
    for item in override + base:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
