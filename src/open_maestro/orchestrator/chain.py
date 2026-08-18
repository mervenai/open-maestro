"""Multi-agent chain execution for Open Maestro.

A chain decomposes a single user request into a sequence of specialist-agent
steps.  The planner can be LLM-driven or fall back to predefined common chains;
the executor runs each step sequentially, selects the cheapest capable model
for that step, and synthesizes a grouped final response.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from open_maestro.agents.registry import _task_requires_writing
from open_maestro.config.capabilities import (
    ReasoningLevel,
    RequiredCapabilities,
    TaskProfile,
    TaskProfiler,
)
from open_maestro.runtime.base import AgentConfig, AgentResult

if TYPE_CHECKING:
    from open_maestro.agents.definition import AgentDefinition
    from open_maestro.agents.registry import AgentRegistry
    from open_maestro.events.bus import EventBus
    from open_maestro.runtime.base import AgentRuntime

logger = logging.getLogger(__name__)

MAX_CHAIN_STEPS = 5

_CHAIN_KEYWORDS: dict[str, list[str]] = {
    "implement": ["implement", "build", "create", "write", "add feature", "develop"],
    "fix": ["fix", "debug", "repair", "resolve", "bug", "error", "broken"],
    "analyze": ["analyze", "analysis", "evaluate", "compare", "assess", "report"],
}

_DEFAULT_CHAIN_TEMPLATES: dict[str, list[str]] = {
    "implement": ["research", "engineer", "qa"],
    "fix": ["research", "engineer", "qa"],
    "analyze": ["research", "documentation"],
}

_PLANNER_SYSTEM_PROMPT = """You are a multi-agent workflow planner.

Given the user's task and the available specialist agents, break the task into a
short chain of agent steps (max 5). Each step must use one of the listed agent
IDs. Respond with **only** a JSON object in this exact shape:

{
  "steps": [
    {"agent_id": "<id>", "purpose": "<what this step should produce>"},
    ...
  ]
}

Keep the chain as short as possible while producing a complete result. If the
task can be handled well by a single agent, return one step. Do not include
markdown fences or any text outside the JSON.
"""  # noqa: E501


@dataclass
class HandoffStep:
    """A single step in a multi-agent chain."""

    agent_id: str
    purpose: str
    task_profile: TaskProfile | None = None


@dataclass
class HandoffPlan:
    """Planned decomposition of a user request into agent steps."""

    steps: list[HandoffStep]
    original_prompt: str


@dataclass
class StepResult:
    """Result of executing one chain step."""

    step: HandoffStep
    agent: AgentDefinition
    runtime_name: str
    model: str | None
    result: AgentResult


class ChainPlanner:
    """Plan a multi-agent chain for a user task."""

    def __init__(
        self,
        runtime: AgentRuntime,
        registry: AgentRegistry,
        model: str = "fast",
    ):
        self.runtime = runtime
        self.registry = registry
        self.model = model

    async def plan(
        self,
        prompt: str,
        *,
        first_agent: AgentDefinition | None = None,
        profile: TaskProfile | None = None,
    ) -> HandoffPlan:
        """Return a chain plan for *prompt*.

        Tries an LLM-driven planner first; if that fails or returns no usable
        steps, falls back to a keyword-driven predefined chain.
        """
        llm_plan = await self._llm_plan(prompt, profile=profile)
        if llm_plan is not None and llm_plan.steps:
            return llm_plan

        predefined = self._predefined_chain(prompt, first_agent=first_agent)
        if predefined.steps:
            return predefined

        # Last resort: run the first agent as a single-step chain.
        agent = first_agent or self._pick_default_agent()
        if agent is None:
            return HandoffPlan(steps=[], original_prompt=prompt)
        return HandoffPlan(
            steps=[HandoffStep(agent_id=agent.id, purpose=prompt)],
            original_prompt=prompt,
        )

    async def _llm_plan(
        self,
        prompt: str,
        profile: TaskProfile | None = None,
    ) -> HandoffPlan | None:
        agents = self.registry.list()
        if not agents:
            return None

        lines = [
            "Available agents:",
            "",
        ]
        for agent in agents:
            lines.append(f"- id: {agent.id}")
            lines.append(f"  name: {agent.name}")
            lines.append(f"  role: {agent.role}")
            lines.append(f"  description: {agent.description or agent.instructions[:200]}")
            lines.append("")
        lines.append(f"Task: {prompt}")
        if profile is not None:
            lines.append("")
            lines.append(
                f"Task profile: reasoning={profile.reasoning_depth.value}, "
                f"coding={profile.coding_strength.value}, "
                f"tools={profile.needs_tools}, vision={profile.needs_vision}"
            )

        config = AgentConfig(
            system_prompt=_PLANNER_SYSTEM_PROMPT,
            model=self.model,
            max_turns=1,
            task_profile=profile,
        )
        try:
            result = await self.runtime.run("\n".join(lines), config=config)
        except Exception as exc:
            logger.warning("LLM chain planning failed: %s", exc)
            return None

        if result.is_error:
            logger.warning("LLM chain planning returned error: %s", result.text)
            return None

        steps = _parse_plan_response(result.text)
        if not steps:
            return None

        # Validate and cap length.
        valid_steps: list[HandoffStep] = []
        for step in steps[:MAX_CHAIN_STEPS]:
            try:
                self.registry.get(step.agent_id)
                valid_steps.append(step)
            except KeyError:
                logger.warning(
                    "LLM chain planner chose unknown agent_id '%s'; skipping",
                    step.agent_id,
                )
        return HandoffPlan(steps=valid_steps, original_prompt=prompt)

    def _predefined_chain(
        self,
        prompt: str,
        first_agent: AgentDefinition | None = None,
    ) -> HandoffPlan:
        """Return a keyword-driven chain for common workflows."""
        lowered = prompt.lower()
        matched_template: list[str] | None = None
        for intent, keywords in _CHAIN_KEYWORDS.items():
            if any(kw in lowered for kw in keywords):
                matched_template = _DEFAULT_CHAIN_TEMPLATES.get(intent)
                break

        if not matched_template:
            # No common pattern matched; keep the single selected agent.
            agent = first_agent or self._pick_default_agent()
            if agent is None:
                return HandoffPlan(steps=[], original_prompt=prompt)
            return HandoffPlan(
                steps=[HandoffStep(agent_id=agent.id, purpose=prompt)],
                original_prompt=prompt,
            )

        steps: list[HandoffStep] = []
        for role in matched_template:
            agent = self._agent_by_role(role) or self._agent_by_role("engineer")
            if agent is None:
                continue
            purpose = self._purpose_for_role(role, prompt)
            steps.append(HandoffStep(agent_id=agent.id, purpose=purpose))

        # Deduplicate consecutive identical agents while preserving order.
        deduped: list[HandoffStep] = []
        for step in steps:
            if deduped and deduped[-1].agent_id == step.agent_id:
                continue
            deduped.append(step)

        return HandoffPlan(steps=deduped[:MAX_CHAIN_STEPS], original_prompt=prompt)

    def _pick_default_agent(self) -> AgentDefinition | None:
        agents = self.registry.list()
        if not agents:
            return None
        # Prefer a mutating engineer for writing tasks, researcher otherwise.
        for agent in agents:
            if agent.role.lower() == "engineer":
                return agent
        return agents[0]

    def _agent_by_role(self, role: str) -> AgentDefinition | None:
        candidates = [
            a for a in self.registry.list() if a.role.lower() == role.lower()
        ]
        return candidates[0] if candidates else None

    @staticmethod
    def _purpose_for_role(role: str, prompt: str) -> str:
        role = role.lower()
        if role in {"research", "researcher"}:
            return f"Research and analyze the task: {prompt}"
        if role == "engineer":
            return f"Implement the requested changes for: {prompt}"
        if role == "qa":
            return f"Review, test, and verify the work for: {prompt}"
        if role == "documentation":
            return f"Document findings and produce the final output for: {prompt}"
        return prompt


def _parse_plan_response(text: str) -> list[HandoffStep]:
    """Parse the LLM planner JSON response into HandoffStep objects."""
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Chain planner returned non-JSON response: %s", text[:500])
        return []

    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []

    result: list[HandoffStep] = []
    for item in steps:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("agent_id")
        purpose = item.get("purpose", "")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        result.append(HandoffStep(agent_id=agent_id, purpose=str(purpose)))
    return result


class ChainExecutor:
    """Execute a planned chain of agent steps sequentially."""

    def __init__(
        self,
        registry: AgentRegistry,
        event_bus: EventBus | None = None,
        base_config: AgentConfig | None = None,
    ):
        self.registry = registry
        self.event_bus = event_bus
        self.base_config = base_config or AgentConfig()

    async def execute(
        self,
        plan: HandoffPlan,
        *,
        original_prompt: str,
        base_profile: TaskProfile | None = None,
        memories: list[str] | None = None,
        code_results: list[dict[str, Any]] | None = None,
        allowed_tools: list[str] | None = None,
        blocked_tools: list[str] | None = None,
        permission_mode: str | None = None,
        deny_dangerous: bool = False,
        max_turns: int | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Run each step of *plan* and return grouped output."""
        from open_maestro.config.models import ModelResolver
        from open_maestro.runtime.factory import (
            create_runtime,
            select_runtime_for_task,
        )
        from open_maestro.security.policy import PermissionPolicy, evaluate

        step_results: list[StepResult] = []
        prior_outputs: list[str] = []

        for idx, step in enumerate(plan.steps, start=1):
            await self._emit("chain.step_started", {
                "step": idx,
                "total": len(plan.steps),
                "agent_id": step.agent_id,
                "purpose": step.purpose,
            })

            agent = self.registry.get(step.agent_id)

            # Resolve per-step task profile.
            profile = self._step_profile(step, agent, base_profile)

            # Pick the cheapest capable runtime/model for this step.
            try:
                runtime_name, model_id = select_runtime_for_task(profile)
            except Exception as exc:
                logger.warning("Chain step %s runtime selection failed: %s", idx, exc)
                runtime_name = self.base_config.extra.get("runtime_name", "openai-sdk")
                model_id = None

            runtime = create_runtime(
                runtime_name,
                config=AgentConfig(
                    extra={
                        "api_key": self.base_config.extra.get("api_key"),
                        "base_url": self.base_config.extra.get("base_url"),
                    }
                ),
            )

            resolved_model = model_id
            if resolved_model is None:
                resolver = ModelResolver()
                resolved_model = resolver.resolve(
                    agent.model,
                    runtime_name,
                    profile=profile,
                )

            step_prompt = self._build_step_prompt(
                step,
                agent,
                original_prompt,
                prior_outputs,
                memories=memories,
                code_results=code_results,
            )

            agent_config = agent.to_config()
            agent_config["model"] = resolved_model
            if permission_mode:
                agent_config["permission_mode"] = permission_mode
            if max_turns is not None:
                agent_config["max_turns"] = max_turns
            if mcp_servers is not None:
                agent_config["mcp_servers"] = mcp_servers

            blocked = set(agent_config.get("blocked_tools") or set())
            if blocked_tools:
                blocked.update(blocked_tools)
                agent_config["blocked_tools"] = blocked

            allowed = set(agent_config.get("allowed_tools") or set())
            if allowed_tools:
                allowed.update(allowed_tools)
                agent_config["allowed_tools"] = sorted(allowed)

            config = AgentConfig(
                **agent_config,
                task_profile=profile,
            )

            policy = PermissionPolicy(
                mode=permission_mode or "allow",
                dangerous_checks_enabled=deny_dangerous,
                blocked_tools=blocked,
                allowed_tools=allowed if allowed else None,
            )

            await self._emit("agent.selected", {
                "agent_id": agent.id,
                "role": agent.role,
                "model": config.model,
            })
            await self._emit("runtime.started", {
                "runtime": runtime_name,
                "agent_id": agent.id,
                "model": config.model,
            })

            try:
                if blocked or policy.is_active():
                    guard_text = policy.guard_text(blocked)
                    if guard_text:
                        config = self._with_guard_text(config, guard_text)

                    async def tool_guard(
                        tool_name: str, tool_input: dict[str, Any]
                    ) -> bool:
                        return await evaluate(tool_name, tool_input, agent, policy)

                    result = await runtime.run_with_hooks(
                        step_prompt,
                        tool_guard=tool_guard,
                        blocked_tools=blocked,
                        config=config,
                    )
                else:
                    result = await runtime.run(step_prompt, config=config)
            except Exception as exc:
                logger.exception("Chain step %s failed", idx)
                result = AgentResult(
                    text=f"Step failed: {exc}",
                    is_error=True,
                )

            result.metadata["selected_agent"] = agent.id
            await self._emit("runtime.completed", {
                "runtime": runtime_name,
                "agent_id": agent.id,
                "is_error": result.is_error,
            })
            await self._emit("chain.step_completed", {
                "step": idx,
                "total": len(plan.steps),
                "agent_id": step.agent_id,
                "is_error": result.is_error,
            })

            step_results.append(
                StepResult(
                    step=step,
                    agent=agent,
                    runtime_name=runtime_name,
                    model=config.model,
                    result=result,
                )
            )
            prior_outputs.append(
                f"## {agent.name} ({agent.role})\n\n{result.text}"
            )

            if result.is_error:
                break

        return self._synthesize(plan, step_results)

    def _step_profile(
        self,
        step: HandoffStep,
        agent: AgentDefinition,
        base_profile: TaskProfile | None,
    ) -> TaskProfile:
        """Build a task profile for a single chain step."""
        profile = step.task_profile or base_profile or TaskProfiler.from_prompt(step.purpose)
        if agent.required_capabilities is not None:
            profile = agent.required_capabilities.merge_into_profile(profile)
        return profile

    @staticmethod
    def _build_step_prompt(
        step: HandoffStep,
        agent: AgentDefinition,
        original_prompt: str,
        prior_outputs: list[str],
        memories: list[str] | None = None,
        code_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Assemble the prompt for one chain step."""
        parts: list[str] = [
            f"Original task: {original_prompt}",
            "",
            f"Your step: {step.purpose}",
            "",
            f"You are the '{agent.name}' specialist. {agent.role}",
        ]

        if prior_outputs:
            parts.append("\nOutputs from previous steps:\n")
            parts.append("\n\n".join(prior_outputs))

        if memories:
            parts.append("\nRelevant project context:")
            for memory in memories:
                parts.append(f"- {memory}")

        if code_results:
            parts.append("\nRelevant code snippets:")
            for result in code_results[:5]:
                path = result.get("file_path", "unknown")
                snippet = result.get("content", "")[:500]
                parts.append(f"\n{path}:\n{snippet}")

        parts.append(
            "\n# Output formatting rules\n"
            "- Use clear Markdown hierarchy.\n"
            "- Keep your response focused on your assigned step.\n"
            "- Include enough detail for the next step (if any) to continue."
        )
        return "\n".join(parts)

    @staticmethod
    def _with_guard_text(config: AgentConfig, guard_text: str) -> AgentConfig:
        from dataclasses import replace

        new_system_prompt = (
            f"{config.system_prompt or ''}\n\n{guard_text}"
        ).strip()
        return replace(config, system_prompt=new_system_prompt)

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.event_bus is not None:
            await self.event_bus.emit(event_type, payload)

    @staticmethod
    def format_plan(plan: HandoffPlan) -> str:
        """Return a human-readable rendering of a chain plan."""
        lines = [
            "Open Maestro chain plan",
            "",
            f"Original task: {plan.original_prompt}",
            "",
            f"Steps ({len(plan.steps)}):",
        ]
        for idx, step in enumerate(plan.steps, start=1):
            lines.append(f"  {idx}. {step.agent_id}: {step.purpose}")
        return "\n".join(lines)

    @staticmethod
    def _synthesize(plan: HandoffPlan, step_results: list[StepResult]) -> AgentResult:
        """Combine all step outputs into a single grouped response."""
        if not step_results:
            return AgentResult(
                text="No chain steps were executed.",
                is_error=True,
            )

        lines: list[str] = [
            f"# Chain result ({len(step_results)} step(s))",
            "",
        ]
        for sr in step_results:
            lines.append(f"## {sr.agent.name} ({sr.agent.role})")
            lines.append(f"Runtime: {sr.runtime_name} | Model: {sr.model or 'unspecified'}")
            lines.append("")
            lines.append(sr.result.text)
            lines.append("")

        final_result = step_results[-1].result
        is_error = any(sr.result.is_error for sr in step_results)

        # Aggregate metrics across all steps for context monitoring.
        total_cost = 0.0
        total_tokens = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_duration_ms = 0
        for sr in step_results:
            if sr.result.cost_usd is not None:
                total_cost += sr.result.cost_usd
            if sr.result.tokens_used is not None:
                total_tokens += sr.result.tokens_used
            if sr.result.input_tokens is not None:
                total_input_tokens += sr.result.input_tokens
            if sr.result.output_tokens is not None:
                total_output_tokens += sr.result.output_tokens
            if sr.result.duration_ms is not None:
                total_duration_ms += sr.result.duration_ms

        return AgentResult(
            text="\n".join(lines),
            session_id=final_result.session_id,
            cost_usd=total_cost or None,
            tokens_used=total_tokens or None,
            input_tokens=total_input_tokens or None,
            output_tokens=total_output_tokens or None,
            duration_ms=total_duration_ms or None,
            is_error=is_error,
            metadata={
                "chain": True,
                "steps": [
                    {
                        "agent_id": sr.step.agent_id,
                        "runtime": sr.runtime_name,
                        "model": sr.model,
                        "is_error": sr.result.is_error,
                    }
                    for sr in step_results
                ],
            },
        )
