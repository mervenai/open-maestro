"""Prompt playbook schema and loader for milestone-guided experiences.

Why: A playbook is a reusable set of prompt templates tied to lifecycle
milestones. It lets Maestro suggest the next prompt for the current milestone
instead of leaving the user to remember what to ask.
What: ``PromptPlaybook`` models, ``load_playbook()``, and placeholder
resolution.
Test: ``load_playbook(project_path)`` returns a playbook with resolved defaults.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from open_maestro.milestones.models import MilestonePlan


@dataclass
class PromptTemplate:
    """A single reusable prompt inside a playbook."""

    id: str
    title: str
    order: int
    prompt: str
    agent_hint: str = ""
    tags: list[str] = None  # type: ignore[assignment]
    artifact_target: str = ""
    example_from: str = ""

    def __post_init__(self) -> None:
        if self.tags is None:
            self.tags = []

    def render(self, context: dict[str, str]) -> str:
        """Resolve placeholders in the prompt text."""
        text = self.prompt
        for key, value in context.items():
            text = text.replace(f"{{{key}}}", value)
        return text


@dataclass
class MilestonePromptDeck:
    """All prompts for one milestone."""

    milestone_id: str
    prompts: list[PromptTemplate]


@dataclass
class PromptPlaybook:
    """Full prompt playbook for a project type."""

    playbook_id: str
    version: str
    source_project: str
    decks: dict[str, MilestonePromptDeck]
    placeholders: dict[str, str]

    def prompts_for(self, milestone_id: str) -> list[PromptTemplate]:
        """Return prompts for a milestone, sorted by order."""
        deck = self.decks.get(milestone_id)
        if deck is None:
            return []
        return sorted(deck.prompts, key=lambda p: p.order)


def _default_context(plan: MilestonePlan | None, epic_id: str | None = None) -> dict[str, str]:
    """Build default placeholder values from the milestone plan."""
    today = date.today().isoformat()
    context: dict[str, str] = {
        "date": today,
        "epic_id": epic_id or "default",
        "epic_name": "Default Track",
    }
    if plan is not None:
        context["project_id"] = plan.project_id
        if epic_id:
            epic = plan.get_epic(epic_id)
            if epic is not None:
                context["epic_name"] = epic.name
    return context


def _resolve_artifact_target(prompt: PromptTemplate, context: dict[str, str]) -> dict[str, str]:
    """Add artifact_target to the context if the prompt defines one."""
    ctx = dict(context)
    target = prompt.artifact_target
    if target:
        for key, value in ctx.items():
            target = target.replace(f"{{{key}}}", value)
        ctx["artifact_target"] = target
    return ctx


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-") or "epic"


def load_playbook(
    project_path: Path,
    plan: MilestonePlan | None = None,
) -> PromptPlaybook:
    """Load the prompt playbook for a project.

    Priority:
    1. ``.open-maestro/playbook.yaml`` inside the project.
    2. The bundled default ``software-consulting`` playbook.
    """
    project_override = project_path / ".open-maestro" / "playbook.yaml"
    if project_override.exists():
        source = project_override
    else:
        source = Path(__file__).parent / "playbooks" / "software-consulting.yaml"

    raw = yaml.safe_load(source.read_text(encoding="utf-8"))

    decks: dict[str, MilestonePromptDeck] = {}
    for milestone_id, prompts in raw.get("milestone_prompts", {}).items():
        templates = []
        for item in prompts:
            templates.append(
                PromptTemplate(
                    id=item["id"],
                    title=item["title"],
                    order=item.get("order", 0),
                    prompt=item["prompt"],
                    agent_hint=item.get("agent_hint", ""),
                    tags=item.get("tags", []),
                    artifact_target=item.get("artifact_target", ""),
                    example_from=item.get("example_from", ""),
                )
            )
        decks[milestone_id] = MilestonePromptDeck(
            milestone_id=milestone_id,
            prompts=templates,
        )

    placeholders = {
        p["name"]: p.get("description", "")
        for p in raw.get("placeholders", [])
    }

    return PromptPlaybook(
        playbook_id=raw.get("playbook_id", "unknown"),
        version=raw.get("version", "0.0.0"),
        source_project=raw.get("source_project", ""),
        decks=decks,
        placeholders=placeholders,
    )


def get_prompts_for_milestone(
    project_path: Path,
    milestone_id: str,
    plan: MilestonePlan | None = None,
    epic_id: str | None = None,
) -> list[tuple[PromptTemplate, str]]:
    """Return rendered prompts for a milestone/epic.

    Returns a list of (template, rendered_prompt) tuples.
    """
    playbook = load_playbook(project_path, plan=plan)
    context = _default_context(plan, epic_id=epic_id)
    result: list[tuple[PromptTemplate, str]] = []
    for template in playbook.prompts_for(milestone_id):
        ctx = _resolve_artifact_target(template, context)
        rendered = template.render(ctx)
        result.append((template, rendered))
    return result


def format_prompt_list(
    prompts: list[tuple[PromptTemplate, str]],
    max_prompts: int = 3,
) -> str:
    """Format a list of prompts for display in interactive mode."""
    if not prompts:
        return "No suggested prompts for this milestone."
    lines = ["Suggested prompts:"]
    for idx, (template, rendered) in enumerate(prompts[:max_prompts], start=1):
        title = template.title
        agent = f" [{template.agent_hint}]" if template.agent_hint else ""
        lines.append(f"\n  {idx}. {title}{agent}")
        # Show a one-line preview of the rendered prompt.
        preview = " ".join(rendered.split())[:160]
        if len(rendered) > 160:
            preview += "..."
        lines.append(f"     {preview}")
    if len(prompts) > max_prompts:
        lines.append(f"\n  ...and {len(prompts) - max_prompts} more. Use /prompts to browse.")
    return "\n".join(lines)
