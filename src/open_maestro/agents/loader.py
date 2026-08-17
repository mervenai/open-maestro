"""Tiered agent loading.

Open Maestro supports three agent tiers, in order of precedence:

1. Project-specific agents in ``./.open-maestro/agents/``.
2. User-level agents in ``~/.open-maestro/agents/``.
3. Bundled default agents shipped with the package.

Later tiers override earlier tiers for agents with the same id.
"""

from __future__ import annotations

from pathlib import Path

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.skills.registry import SkillRegistry
from open_maestro.sources.sync import GitSource


def _bundled_agents_dir() -> Path:
    """Return the agents directory bundled with the open-maestro package."""
    # Installed wheel layout: open_maestro/_bundled_agents/
    path = (Path(__file__).resolve().parent.parent / "_bundled_agents").resolve()
    if path.exists():
        return path
    # Development layout: project-root/agents/
    return (Path(__file__).resolve().parent.parent.parent.parent / "agents").resolve()


def _bundled_skills_dir() -> Path:
    """Return the skills directory bundled with the open-maestro package."""
    # Installed wheel layout: open_maestro/_bundled_skills/
    path = (Path(__file__).resolve().parent.parent / "_bundled_skills").resolve()
    if path.exists():
        return path
    # Development layout: project-root/skills/
    return (Path(__file__).resolve().parent.parent.parent.parent / "skills").resolve()


def _resolve_agent_dirs(
    explicit_project_dir: Path | None = None,
) -> tuple[Path | None, Path | None, Path]:
    """Return (project_dir, user_dir, bundled_dir) for tiered loading."""
    project_dir = explicit_project_dir or (Path.cwd() / ".open-maestro" / "agents")
    user_dir = Path.home() / ".open-maestro" / "agents"
    bundled_dir = _bundled_agents_dir()

    if explicit_project_dir is None and not project_dir.exists():
        project_dir = None
    if not user_dir.exists():
        user_dir = None

    return project_dir, user_dir, bundled_dir


def _resolve_skill_dirs(
    project_dir: Path | None,
    user_dir: Path | None,
    bundled_dir: Path,
    explicit_project_skills_dir: Path | None = None,
    explicit_user_skills_dir: Path | None = None,
    explicit_bundled_skills_dir: Path | None = None,
) -> tuple[Path | None, Path | None, Path]:
    """Return skill directories corresponding to the supplied agent tiers.

    When no explicit skill directory is provided, derive it from the agent
    directory by looking for a sibling ``skills`` directory.  If that does not
    exist, the tier is ignored.
    """
    if explicit_project_skills_dir is not None:
        project_skills = explicit_project_skills_dir
    elif project_dir is not None:
        project_skills = project_dir.parent / "skills"
    else:
        project_skills = None

    if explicit_user_skills_dir is not None:
        user_skills = explicit_user_skills_dir
    elif user_dir is not None:
        user_skills = user_dir.parent / "skills"
    else:
        user_skills = None

    bundled_skills = explicit_bundled_skills_dir or _bundled_skills_dir()

    if project_skills is not None and not project_skills.exists():
        project_skills = None
    if user_skills is not None and not user_skills.exists():
        user_skills = None
    if not bundled_skills.exists():
        bundled_skills = None

    return project_skills, user_skills, bundled_skills


class AgentLoader:
    """Load agents from project, user, and bundled tiers."""

    @classmethod
    def load_tiered_dirs(
        cls,
        project_dir: Path | None,
        user_dir: Path | None,
        bundled_dir: Path,
        project_skills_dir: Path | None = None,
        user_skills_dir: Path | None = None,
        bundled_skills_dir: Path | None = None,
        agent_sources: list[GitSource] | None = None,
        skill_sources: list[GitSource] | None = None,
    ) -> AgentRegistry:
        """Load agents from tiers and Git sources.

        Precedence (later overrides earlier):
        bundled < user < project < agent sources (in registry order)

        Skills referenced by agents are loaded from corresponding ``skills``
        directories and appended to each agent's system prompt.
        """
        registry: AgentRegistry = AgentRegistry()
        for directory in (bundled_dir, user_dir, project_dir):
            if directory is not None and directory.exists():
                tier = AgentRegistry.from_directory(directory)
                registry = registry.merge(tier)

        for source in agent_sources or []:
            if source.content_path.exists():
                tier = AgentRegistry.from_directory(source.content_path)
                registry = registry.merge(tier)

        registry = _resolve_inheritance(registry)

        project_skills, user_skills, bundled_skills = _resolve_skill_dirs(
            project_dir,
            user_dir,
            bundled_dir,
            project_skills_dir,
            user_skills_dir,
            bundled_skills_dir,
        )
        skill_registry = SkillRegistry.load_tiered_dirs(
            project_skills, user_skills, bundled_skills or Path("/nonexistent")
        )
        for source in skill_sources or []:
            if source.content_path.exists():
                tier = SkillRegistry.from_directory(
                    source.content_path, exclude=source.exclude
                )
                merged = dict(skill_registry._skills)
                merged.update(tier._skills)
                skill_registry = SkillRegistry(merged)
        return _resolve_skills(registry, skill_registry)

    @classmethod
    def load_defaults(cls) -> AgentRegistry:
        """Load agents using default tiered directories."""
        project_dir, user_dir, bundled_dir = _resolve_agent_dirs()
        return cls.load_tiered_dirs(project_dir, user_dir, bundled_dir)


def _resolve_inheritance(registry: AgentRegistry) -> AgentRegistry:
    """Resolve ``extends`` references and filter out abstract base agents.

    Agents whose id starts with ``base-`` are treated as abstract templates and
    are not included in the final registry unless nothing extends them.
    """
    all_agents: dict[str, AgentDefinition] = {
        agent.id: agent for agent in registry.list()
    }
    base_ids: set[str] = {
        agent_id for agent_id in all_agents if agent_id.startswith("base-")
    }
    resolved: dict[str, AgentDefinition] = {}

    def resolve(agent_id: str, visited: set[str]) -> AgentDefinition:
        if agent_id in resolved:
            return resolved[agent_id]
        if agent_id in visited:
            raise ValueError(
                f"Cyclic agent inheritance detected at '{agent_id}'"
            )
        visited.add(agent_id)

        agent = all_agents.get(agent_id)
        if agent is None:
            raise ValueError(
                f"Agent '{agent_id}' extends unknown agent '{agent.extends}'"
            )

        if agent.extends:
            base = resolve(agent.extends, visited)
            agent = AgentDefinition.merge(base, agent)

        resolved[agent_id] = agent
        return agent

    for agent_id in all_agents:
        if agent_id not in base_ids:
            resolve(agent_id, set())

    return AgentRegistry(
        {agent_id: agent for agent_id, agent in resolved.items() if agent_id not in base_ids}
    )


def _resolve_skills(
    registry: AgentRegistry, skill_registry: SkillRegistry
) -> AgentRegistry:
    """Append referenced skill content to each agent's instructions.

    Skills are loaded from the same tiered directories as agents and are
    resolved after inheritance so a child agent inherits its parent's skill
    references as well.
    """
    from dataclasses import replace

    updated: dict[str, AgentDefinition] = {}
    for agent in registry.list():
        skills = skill_registry.resolve_for_agent(agent)
        if not skills:
            updated[agent.id] = agent
            continue

        skill_blocks = ["\n".join([f"## Skill: {skill.name}", skill.content]) for skill in skills]
        extra = "\n\n".join(["# Embedded Skills", *skill_blocks])

        if agent.instructions:
            new_instructions = f"{agent.instructions}\n\n{extra}"
        else:
            new_instructions = extra
        updated[agent.id] = replace(agent, instructions=new_instructions)
    return AgentRegistry(updated)
