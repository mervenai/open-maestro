"""Agent registry and discovery."""

from __future__ import annotations

import re
from pathlib import Path

from open_maestro.agents.definition import AgentDefinition


class AgentRegistry:
    """Load and query vendor-neutral agent definitions."""

    def __init__(self, agents: dict[str, AgentDefinition] | None = None):
        self._agents: dict[str, AgentDefinition] = agents or {}

    @classmethod
    def from_directory(cls, directory: str | Path) -> AgentRegistry:
        """Load all ``.md`` agent definitions from a directory tree."""
        directory = Path(directory)
        agents: dict[str, AgentDefinition] = {}
        for path in directory.rglob("*.md"):
            try:
                agent = AgentDefinition.from_markdown(path)
                agents[agent.id] = agent
            except Exception as exc:
                raise RuntimeError(f"Failed to load agent from {path}: {exc}") from exc
        return cls(agents)

    def get(self, agent_id: str) -> AgentDefinition:
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not found")
        return self._agents[agent_id]

    def list(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def merge(self, other: AgentRegistry) -> AgentRegistry:
        """Return a new registry where *other* overrides *self* for duplicate ids."""
        merged = dict(self._agents)
        merged.update(other._agents)
        return AgentRegistry(merged)

    def by_role(self, role: str) -> list[AgentDefinition]:
        return [a for a in self._agents.values() if a.role == role]

    def select(self, task_description: str) -> list[AgentDefinition]:
        """Select agents whose role/instructions/tools match the task.

        Keyword matches are boosted by capability-aware signals: tasks that
        mention writing, editing, or creating files prefer agents whose tool
        lists include mutating tools, while purely read/analyze tasks prefer
        read-only researchers.
        """
        keywords = self._extract_keywords(task_description)
        if not keywords:
            return list(self._agents.values())[:1]

        needs_writing = _task_requires_writing(task_description)
        needs_coding = _task_requires_coding(task_description)
        task_words = set(
            re.findall(r"[a-z][a-z0-9_]*", task_description.lower())
        )
        has_research_keywords = bool(_RESEARCH_KEYWORDS & task_words)

        scores: list[tuple[int, AgentDefinition]] = []
        for agent in self._agents.values():
            score = 0
            haystack = " ".join(
                [agent.role, agent.name, agent.instructions]
                + agent.tools
                + agent.blocked_tools
            ).lower()
            for kw in keywords:
                if kw in agent.role.lower():
                    score += 3
                elif kw in haystack:
                    score += 1

            # Research-oriented tasks prefer the researcher agent even if they
            # touch on implementation details.
            if has_research_keywords and agent.role.lower() in {"research", "researcher"}:
                score += 4

            # Tool/capability tie-breakers.
            if needs_writing:
                if _agent_can_mutate(agent):
                    score += 5
                if _agent_is_read_only(agent):
                    score -= 5
            if needs_coding and _agent_can_code(agent):
                # Weaken the coding signal when research keywords dominate.
                score += 1 if has_research_keywords else 2

            if score:
                scores.append((score, agent))
        scores.sort(key=lambda x: x[0], reverse=True)
        if scores:
            return [agent for _, agent in scores]
        # Fallback: return any available agent, prioritising read-only roles.
        return sorted(
            self._agents.values(),
            key=lambda a: 0 if a.role.lower() in {"research", "researcher"} else 1,
        )[:1]

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract meaningful keywords from a task description."""
        stop_words = {
            "a", "an", "the", "and", "or", "for", "to", "in", "of", "on",
            "with", "as", "is", "are", "was", "were", "be", "been", "this",
            "that", "these", "those", "it", "its", "from", "by", "at", "use",
            "using", "how", "what", "where", "when", "why", "who", "can",
            "you", "please", "me", "my", "i", "we", "our", "should", "would",
            "could", "will", "shall", "may", "might", "must", "do", "does",
            "did", "have", "has", "had", "short", "python", "method",
        }

        words = re.findall(r"[a-z][a-z0-9_]*", text.lower())
        return {w for w in words if len(w) >= 2 and w not in stop_words}


# Keywords that suggest the task will mutate files or code.
_WRITE_KEYWORDS: set[str] = {
    "write", "create", "save", "produce", "generate", "output", "persist",
    "edit", "update", "modify", "append", "delete", "remove", "refactor",
    "implement", "build", "prd", "document", "documentation", "markdown",
}
_WRITE_PATTERNS: set[str] = {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml"}

_CODING_KEYWORDS: set[str] = {
    "code", "function", "class", "module", "test", "tests", "bug", "debug",
    "refactor", "implement", "build", "compile", "parser", "serializer",
    "endpoint", "handler", "integration", "feature",
}

_RESEARCH_KEYWORDS: set[str] = {
    "analyze",
    "analysis",
    "compare",
    "comparison",
    "evaluate",
    "evaluation",
    "research",
    "investigate",
    "explore",
    "explain",
    "understand",
    "architecture",
    "architectural",
    "overview",
    "summary",
    "summarize",
    "review",
    "how",
    "what",
    "where",
    "patterns",
}

_READ_ONLY_ROLES: set[str] = {
    "research",
    "researcher",
    "qa",
    "documentation-reviewer",
    "code-reviewer",
    "ticketing",
    "reviewer",
}

_MUTATING_TOOLS: set[str] = {
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Bash",
    "CreateTerminal",
    "WriteFile",
    "ApplyPatch",
}


def _task_requires_writing(text: str) -> bool:
    """Return True if the task likely requires mutating files."""
    lowered = text.lower()
    words = set(re.findall(r"[a-z][a-z0-9_]*", lowered))
    if bool(_WRITE_KEYWORDS & words):
        return True
    return any(pat in lowered for pat in _WRITE_PATTERNS)


def _task_requires_coding(text: str) -> bool:
    """Return True if the task is clearly about code changes."""
    words = set(re.findall(r"[a-z][a-z0-9_]*", text.lower()))
    return bool(_CODING_KEYWORDS & words)


def _agent_can_mutate(agent: AgentDefinition) -> bool:
    """Return True if the agent has at least one mutating tool available."""
    allowed = set(agent.tools or [])
    blocked = set(agent.blocked_tools or [])
    available = _MUTATING_TOOLS & allowed
    available -= blocked
    return bool(available)


def _agent_can_code(agent: AgentDefinition) -> bool:
    """Return True if the agent's required capabilities include coding."""
    caps = agent.required_capabilities
    if caps is None:
        return False
    if caps.coding_strength is None:
        return False
    return caps.coding_strength.value in {"medium", "high"}


def _agent_is_read_only(agent: AgentDefinition) -> bool:
    """Return True if the agent is restricted from mutating files."""
    if agent.role.lower() in _READ_ONLY_ROLES:
        return True
    return not _agent_can_mutate(agent)
