"""LLM-based task router.

Replaces keyword matching with a small LLM call that reads agent descriptions,
capabilities, and the task profile to pick the best specialist.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from open_maestro.config.capabilities import (
    ReasoningLevel,
    TaskProfile,
    TaskProfiler,
)
from open_maestro.runtime.base import AgentConfig, AgentResult

if TYPE_CHECKING:
    from open_maestro.agents.definition import AgentDefinition
    from open_maestro.agents.registry import AgentRegistry
    from open_maestro.runtime.base import AgentRuntime

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Result of an LLM routing call."""

    agent_id: str
    reason: str
    confidence: str = "medium"


ROUTER_SYSTEM_PROMPT = """You are a task router for a multi-agent system.
Your job is to pick exactly one specialist agent from the list below that is
best suited for the user's task.

Respond with **only** a JSON object in this exact shape:

{
  "agent_id": "<id>",
  "reason": "<one-sentence explanation>",
  "confidence": "high|medium|low"
}

Do not include markdown fences, explanations, or any text outside the JSON.
"""


def _format_task_profile(profile: TaskProfile | None) -> str:
    if profile is None:
        return "Task profile: not provided."
    return (
        f"Task profile:\n"
        f"  needs_tools: {profile.needs_tools}\n"
        f"  needs_vision: {profile.needs_vision}\n"
        f"  reasoning_depth: {profile.reasoning_depth.value}\n"
        f"  coding_strength: {profile.coding_strength.value}\n"
        f"  context_tokens_estimate: {profile.context_tokens_estimate}\n"
        f"  latency_preference: {profile.latency_preference.value}\n"
        f"  cost_preference: {profile.cost_preference.value}"
    )


def _format_required_capabilities(agent: AgentDefinition) -> str:
    caps = agent.required_capabilities
    if caps is None:
        return "  required_capabilities: none"
    parts: list[str] = []
    if caps.tool_use is not None:
        parts.append(f"tool_use={caps.tool_use}")
    if caps.vision is not None:
        parts.append(f"vision={caps.vision}")
    if caps.computer_use is not None:
        parts.append(f"computer_use={caps.computer_use}")
    if caps.reasoning is not None:
        parts.append(f"reasoning={caps.reasoning.value}")
    if caps.coding_strength is not None:
        parts.append(f"coding_strength={caps.coding_strength.value}")
    if caps.max_context_tokens is not None:
        parts.append(f"max_context_tokens={caps.max_context_tokens}")
    return f"  required_capabilities: {', '.join(parts) if parts else 'none'}"


def _build_router_prompt(
    task: str,
    agents: list[AgentDefinition],
    task_profile: TaskProfile | None = None,
) -> str:
    lines = [
        _format_task_profile(task_profile),
        "",
        "Available agents:",
        "",
    ]
    for agent in agents:
        lines.append(f"- id: {agent.id}")
        lines.append(f"  name: {agent.name}")
        lines.append(f"  role: {agent.role}")
        lines.append(f"  description: {agent.description}")
        lines.append(f"  instructions: {agent.instructions[:300]}")
        lines.append(_format_required_capabilities(agent))
        if agent.tools:
            lines.append(f"  tools: {', '.join(agent.tools)}")
        lines.append("")
    lines.append(f"Task: {task}")
    lines.append("Which agent_id is the best match?")
    return "\n".join(lines)


def _parse_routing_response(result: AgentResult) -> RoutingDecision | None:
    text = result.text.strip()
    # Strip optional markdown fences
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Router returned non-JSON response: %s", text[:500])
        return None

    agent_id = data.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        return None
    return RoutingDecision(
        agent_id=agent_id,
        reason=str(data.get("reason", "")),
        confidence=str(data.get("confidence", "medium")),
    )


def _score_agent_for_task(
    agent: AgentDefinition,
    profile: TaskProfile,
) -> int:
    """Score an agent's required capabilities against the task profile.

    Higher scores mean the agent is a better capability match.  A negative
    score means the agent is unlikely to satisfy the task requirements.
    """
    caps = agent.required_capabilities
    if caps is None:
        # No explicit requirements; neutral match.
        return 0

    score = 0

    # Reasoning: bonus if the agent explicitly supports the required depth.
    if caps.reasoning is not None:
        reasoning_rank = {ReasoningLevel.NONE: 0, ReasoningLevel.LIGHT: 1, ReasoningLevel.DEEP: 2}
        needed = reasoning_rank[profile.reasoning_depth]
        provided = reasoning_rank[caps.reasoning]
        if provided < needed:
            score -= 50
        elif provided >= needed:
            score += 10

    # Coding: same pattern.
    if caps.coding_strength is not None:
        coding_rank = {"low": 0, "medium": 1, "high": 2}
        needed_c = coding_rank[profile.coding_strength.value]
        provided_c = coding_rank[caps.coding_strength.value]
        if provided_c < needed_c:
            score -= 40
        elif provided_c >= needed_c:
            score += 10

    # Vision: hard requirement.
    if profile.needs_vision and caps.vision is False:
        score -= 100
    elif profile.needs_vision and caps.vision is True:
        score += 15

    # Tools: hard requirement.
    if profile.needs_tools and caps.tool_use is False:
        score -= 100
    elif profile.needs_tools and caps.tool_use is True:
        score += 5

    # Context window.
    if caps.max_context_tokens is not None:
        if caps.max_context_tokens < profile.context_tokens_estimate:
            score -= 60
        elif caps.max_context_tokens >= profile.context_tokens_estimate * 2:
            score += 5

    return score


class LLMTaskRouter:
    """Route tasks to agents using an LLM call through an AgentRuntime."""

    def __init__(
        self,
        runtime: AgentRuntime,
        model: str = "fast",
        fallback_to_keyword: bool = True,
    ):
        self.runtime = runtime
        self.model = model
        self.fallback_to_keyword = fallback_to_keyword

    async def select(
        self,
        task: str,
        registry: AgentRegistry,
        task_profile: TaskProfile | None = None,
    ) -> AgentDefinition:
        """Return the best agent for *task*, falling back to keyword match."""
        agents = registry.list()
        if not agents:
            raise RuntimeError("No agents available in registry")

        profile = task_profile or TaskProfiler.from_prompt(task)

        decision = await self._llm_select(task, agents, task_profile=profile)
        if decision is not None:
            try:
                return registry.get(decision.agent_id)
            except KeyError:
                logger.warning(
                    "Router chose unknown agent_id '%s'; falling back",
                    decision.agent_id,
                )

        if self.fallback_to_keyword:
            keyword_results = self._select_by_keyword(task, agents, profile)
            if keyword_results:
                logger.info(
                    "LLM routing failed or returned unknown id; using keyword fallback"
                )
                return keyword_results[0]

        if decision is not None:
            raise RuntimeError(f"Router chose unknown agent_id: {decision.agent_id}")
        raise RuntimeError(f"Could not route task: {task}")

    async def _llm_select(
        self,
        task: str,
        agents: list[AgentDefinition],
        task_profile: TaskProfile | None = None,
    ) -> RoutingDecision | None:
        prompt = _build_router_prompt(task, agents, task_profile=task_profile)
        config = AgentConfig(
            system_prompt=ROUTER_SYSTEM_PROMPT,
            model=self.model,
            max_turns=1,
            task_profile=task_profile,
        )
        try:
            result = await self.runtime.run(prompt, config=config)
        except Exception as exc:
            logger.warning("LLM routing call failed: %s", exc)
            return None

        if result.is_error:
            logger.warning("LLM routing returned error: %s", result.text)
            return None

        return _parse_routing_response(result)

    def _select_by_keyword(
        self,
        task: str,
        agents: list[AgentDefinition],
        profile: TaskProfile,
    ) -> list[AgentDefinition]:
        """Keyword match with capability compatibility as a tiebreaker."""
        from open_maestro.agents.registry import AgentRegistry

        candidates = AgentRegistry({agent.id: agent for agent in agents}).select(task)
        if not candidates:
            return []

        scored: list[tuple[int, AgentDefinition]] = []
        base_scores: dict[str, int] = {agent.id: 0 for agent in candidates}
        # Reconstruct base keyword scores from registry.select ordering.
        for idx, agent in enumerate(candidates):
            base_scores[agent.id] = max(0, len(candidates) - idx)

        for agent in candidates:
            capability_score = _score_agent_for_task(agent, profile)
            scored.append((base_scores[agent.id] * 10 + capability_score, agent))

        scored.sort(key=lambda x: x[0], reverse=True)
        logger.debug("Keyword+capability ranking: %s", [(s, a.id) for s, a in scored])
        return [agent for _, agent in scored]
