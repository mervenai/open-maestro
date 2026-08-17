"""Simple Project Manager orchestrator.

The PM receives a user task, recalls relevant project context, optionally
searches the codebase, selects a specialist agent, and delegates execution
through a vendor-neutral ``AgentRuntime``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import (
    AgentRegistry,
    _agent_can_mutate,
    _agent_is_read_only,
    _task_requires_writing,
)
from open_maestro.config.capabilities import TaskProfile, TaskProfiler
from open_maestro.config.models import ModelResolver
from open_maestro.context.budget import ContextBudget
from open_maestro.context.monitor import ContextMonitor, ContextSnapshot
from open_maestro.events.bus import EventBus
from open_maestro.milestones import format_prompt_context
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime
from open_maestro.runtime.latency import record_result
from open_maestro.security.policy import PermissionPolicy, evaluate
from open_maestro.session.store import SessionRecord, SessionStore

if TYPE_CHECKING:
    from open_maestro.memory.kuzu_client import KuzuMemoryClient
    from open_maestro.orchestrator.router import LLMTaskRouter
    from open_maestro.search.vector_client import VectorSearchClient

logger = logging.getLogger(__name__)

_RUNTIME_VENDOR_LABELS: dict[str, str] = {
    "kimi-cli": "Kimi",
    "kimi-acp": "Kimi",
    "claude-cli": "Anthropic Claude",
    "claude-sdk": "Anthropic Claude",
    "openai-sdk": "OpenAI / OpenAI-compatible",
}


def _vendor_label(runtime_name: str) -> str:
    return _RUNTIME_VENDOR_LABELS.get(runtime_name, runtime_name)


@dataclass
class OrchestrationContext:
    """Context assembled for a delegated task."""

    original_prompt: str
    memories: list[str] = field(default_factory=list)
    code_results: list[dict[str, Any]] = field(default_factory=list)
    selected_agent: AgentDefinition | None = None
    enriched_prompt: str = ""


class ProjectManager:
    """Minimal vendor-agnostic PM orchestrator."""

    def __init__(
        self,
        runtime: AgentRuntime,
        registry: AgentRegistry,
        memory: KuzuMemoryClient | None = None,
        search: VectorSearchClient | None = None,
        router: LLMTaskRouter | None = None,
        session_store: SessionStore | None = None,
        context_budget: ContextBudget | None = None,
        event_bus: EventBus | None = None,
    ):
        self.runtime = runtime
        self.registry = registry
        self.memory = memory
        self.search = search
        self.router = router
        self.session_store = session_store or SessionStore()
        self.context_budget = context_budget or ContextBudget()
        self.context_monitor = ContextMonitor(budget=self.context_budget)
        self.event_bus = event_bus or EventBus()

    async def handle(
        self,
        prompt: str,
        *,
        agent_id: str | None = None,
        use_memory: bool = True,
        use_search: bool = False,
        search_query: str | None = None,
        task_profile: TaskProfile | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        blocked_tools: list[str] | None = None,
        permission_mode: str | None = None,
        deny_dangerous: bool = False,
        max_turns: int | None = None,
        mcp_servers: dict[str, Any] | None = None,
        session_id: str | None = None,
        resume: bool = False,
        fork: bool = False,
        dry_run: bool = False,
    ) -> AgentResult:
        """Handle a user task by delegating to the best specialist agent."""
        profile = task_profile or TaskProfiler.from_prompt(prompt)
        ctx = OrchestrationContext(original_prompt=prompt)

        # Seed context monitor from a previous session when resuming or forking.
        if session_id and (resume or fork):
            self._seed_context_from_session(session_id)

        await self.event_bus.emit(
            "task.received",
            {"prompt": prompt, "agent_id": agent_id, "runtime": self.runtime.runtime_name},
        )

        # 1. Recall project memory
        if use_memory and self.memory is not None:
            try:
                ctx.memories = await self.memory.recall(prompt)
                logger.debug("Recalled %s memories", len(ctx.memories))
                await self.event_bus.emit(
                    "memory.recalled",
                    {"count": len(ctx.memories)},
                )
            except Exception as exc:
                logger.warning("Memory recall failed: %s", exc)

        # 2. Search codebase if requested
        if use_search and self.search is not None:
            query = search_query or prompt
            try:
                ctx.code_results = await self.search.search_code(query)
                logger.debug("Code search returned %s results", len(ctx.code_results))
                await self.event_bus.emit(
                    "search.completed",
                    {"count": len(ctx.code_results)},
                )
            except Exception as exc:
                logger.warning("Code search failed: %s", exc)

        # 3. Select agent
        if agent_id:
            ctx.selected_agent = self.registry.get(agent_id)
        elif self.router is not None:
            try:
                ctx.selected_agent = await self.router.select(
                    prompt, self.registry, task_profile=profile
                )
            except Exception as exc:
                logger.warning("LLM routing failed: %s", exc)
                candidates = self.registry.select(ctx.original_prompt)
                ctx.selected_agent = candidates[0] if candidates else None
        else:
            candidates = self.registry.select(ctx.original_prompt)
            ctx.selected_agent = candidates[0] if candidates else None

        if ctx.selected_agent is None:
            return AgentResult(
                text="No suitable agent found for this task.",
                is_error=True,
            )

        await self.event_bus.emit(
            "agent.selected",
            {
                "agent_id": ctx.selected_agent.id,
                "role": ctx.selected_agent.role,
                "model": ctx.selected_agent.model,
            },
        )

        # 4. Merge agent capability requirements into the task profile so model
        #    selection respects what the agent actually needs.
        if ctx.selected_agent.required_capabilities is not None:
            profile = ctx.selected_agent.required_capabilities.merge_into_profile(profile)

        # 5. Resolve the concrete model for this runtime and profile.
        #    The task profile (including explicit CLI flags) overrides the
        #    agent's default model alias unless the user supplied an explicit
        #    --model value.
        resolver = ModelResolver()
        if model is not None:
            resolved_model = model
        else:
            resolved_model = resolver.select_for_task(
                self.runtime.runtime_name, profile
            )
            if resolved_model is None:
                resolved_model = resolver.resolve(
                    ctx.selected_agent.model,
                    self.runtime.runtime_name,
                    profile=profile,
                )

        # 6. Assemble enriched prompt
        ctx.enriched_prompt = self._build_prompt(ctx)

        # 7. Dry run: return the plan without invoking the runtime.
        if dry_run:
            plan = self._format_plan(
                ctx, profile, resolved_model, self.runtime.runtime_name
            )
            return AgentResult(
                text=plan,
                metadata={
                    "selected_agent": ctx.selected_agent.id,
                    "resolved_model": resolved_model,
                    "runtime": self.runtime.runtime_name,
                    "dry_run": True,
                },
            )

        # 8. Handoff: if the task requires writing but the selected agent is
        #    read-only, run the read-only agent first for analysis, then delegate
        #    the writing step to a mutating agent.
        needs_handoff = (
            not dry_run
            and _task_requires_writing(prompt)
            and _agent_is_read_only(ctx.selected_agent)
        )
        if needs_handoff:
            result, config = await self._run_with_handoff(
                ctx,
                profile,
                resolved_model,
                prompt=prompt,
                allowed_tools=allowed_tools,
                blocked_tools=blocked_tools,
                permission_mode=permission_mode,
                deny_dangerous=deny_dangerous,
                max_turns=max_turns,
                mcp_servers=mcp_servers,
                session_id=session_id,
                resume=resume,
                fork=fork,
            )
        else:
            result, config = await self._execute_agent(
                ctx,
                profile,
                resolved_model,
                prompt=ctx.enriched_prompt,
                agent=ctx.selected_agent,
                allowed_tools=allowed_tools,
                blocked_tools=blocked_tools,
                permission_mode=permission_mode,
                deny_dangerous=deny_dangerous,
                max_turns=max_turns,
                mcp_servers=mcp_servers,
                session_id=session_id,
                resume=resume,
                fork=fork,
                dry_run=dry_run,
            )

        # 9. Credit the vendor and model used for this turn.
        if not dry_run and not result.is_error:
            result.text += (
                f"\n\n---\n"
                f"Vendor: {_vendor_label(self.runtime.runtime_name)}\n"
                f"Model: {config.model or 'unspecified'}\n"
                f"Runtime: {self.runtime.runtime_name}"
            )

        # 10. Monitor context pressure and persist session state.
        threshold: str | None = None
        if not dry_run:
            threshold = self.context_monitor.update(result)
            if threshold == "warning":
                logger.warning(
                    "Context usage passed warning threshold: %s tokens",
                    self.context_monitor.snapshot.tokens_used,
                )
            elif threshold == "critical":
                logger.warning(
                    "Context usage passed critical threshold: %s tokens",
                    self.context_monitor.snapshot.tokens_used,
                )

            if threshold:
                await self.event_bus.emit(
                    "context.threshold",
                    {
                        "threshold": threshold,
                        "tokens_used": self.context_monitor.snapshot.tokens_used,
                        "max_context_tokens": self.context_monitor.budget.max_context_tokens,
                    },
                )

            await self._persist_session(
                prompt=prompt,
                session_id=session_id,
                resume=resume,
                fork=fork,
                agent=ctx.selected_agent,
                config=config,
                result=result,
            )
            await self.event_bus.emit(
                "session.saved",
                {
                    "session_id": result.session_id or session_id,
                    "agent_id": ctx.selected_agent.id,
                },
            )

            if threshold == "critical":
                resume_log = self.context_monitor.build_resume_log(
                    ctx, original_prompt=prompt
                )
                return AgentResult(
                    text=resume_log,
                    session_id=result.session_id,
                    is_error=False,
                    metadata={
                        "selected_agent": ctx.selected_agent.id,
                        "context_threshold": "critical",
                        "context_snapshot": vars(self.context_monitor.snapshot),
                    },
                )

        return result

    def _seed_context_from_session(self, session_id: str) -> None:
        """Load previous token usage so the budget is cumulative across resumes."""
        try:
            previous = self.session_store.get(session_id)
        except Exception as exc:
            logger.warning("Failed to load session %s for context seed: %s", session_id, exc)
            return

        if previous is None:
            return

        self.context_monitor.snapshot = ContextSnapshot(
            tokens_used=previous.tokens_used or 0,
            input_tokens=previous.input_tokens or 0,
            output_tokens=previous.output_tokens or 0,
            cost_usd=previous.cost_usd or 0.0,
            num_turns=previous.num_turns or 0,
        )
        logger.debug(
            "Seeded context monitor from session %s: %s tokens",
            session_id,
            self.context_monitor.snapshot.tokens_used,
        )

    async def _persist_session(
        self,
        prompt: str,
        session_id: str | None,
        resume: bool,
        fork: bool,
        agent: AgentDefinition,
        config: AgentConfig,
        result: AgentResult,
    ) -> None:
        now = datetime.now(UTC)
        effective_id = result.session_id or session_id or uuid.uuid4().hex

        existing = None
        if session_id:
            try:
                existing = self.session_store.get(session_id)
            except Exception as exc:
                logger.warning("Failed to load existing session: %s", exc)

        created_at = now
        resumed_from: str | None = None
        forked_from: str | None = None
        if existing is not None:
            created_at = existing.created_at
            resumed_from = existing.resumed_from
            forked_from = existing.forked_from

        if resume and session_id:
            resumed_from = resumed_from or session_id
        elif fork and session_id:
            forked_from = session_id

        record = SessionRecord(
            session_id=effective_id,
            runtime_name=self.runtime.runtime_name,
            agent_id=agent.id,
            model=config.model,
            prompt_summary=prompt[:240],
            created_at=created_at,
            updated_at=now,
            resumed_from=resumed_from,
            forked_from=forked_from,
            cost_usd=result.cost_usd,
            num_turns=result.num_turns,
            tokens_used=self.context_monitor.snapshot.tokens_used,
            input_tokens=self.context_monitor.snapshot.input_tokens,
            output_tokens=self.context_monitor.snapshot.output_tokens,
            metadata={
                "is_error": result.is_error,
                "duration_ms": result.duration_ms,
            },
        )
        try:
            self.session_store.save(record)
            logger.debug("Persisted session %s", effective_id)
        except Exception as exc:
            logger.warning("Failed to persist session: %s", exc)

    @staticmethod
    def _build_prompt(ctx: OrchestrationContext) -> str:
        project_root = Path.cwd()
        parts: list[str] = [
            f"Task: {ctx.original_prompt}",
            "",
            f"Project root: {project_root}",
            "Place generated artifacts inside this project. Use existing subfolders "
            "when they exist and make sense: docs/ for documentation and PRDs, "
            "requirements/ for requirements documents, scripts/ for tooling, "
            "tests/ for test files, src/ for source code. Create a new folder only "
            "when the artifact clearly belongs there and no relevant folder exists.",
        ]

        milestone_context = format_prompt_context(project_root)
        if milestone_context:
            parts.append(milestone_context)
            parts.append(
                "\nMilestone instruction: Use ONLY the predefined milestones listed above. "
                "Do not invent new milestone names or reorganize the plan. "
                "When reporting progress, update the status of existing milestones in "
                "`.open-maestro/milestones.yaml` and keep milestone IDs unchanged."
            )

        if ctx.memories:
            parts.append("\nRelevant project context:")
            for memory in ctx.memories:
                parts.append(f"- {memory}")

        if ctx.code_results:
            parts.append("\nRelevant code snippets:")
            for result in ctx.code_results[:5]:
                path = result.get("file_path", "unknown")
                snippet = result.get("content", "")[:500]
                parts.append(f"\n{path}:\n{snippet}")

        parts.append(
            f"\nYou are the '{ctx.selected_agent.name}' specialist. "
            "Use your assigned role and tools to complete the task."
        )
        parts.append(
            "\n# Output formatting rules\n"
            "- Use clear Markdown hierarchy: a single top-level `#` title, "
            "then `##` sections, `###` subsections.\n"
            "- Format tables with proper Markdown syntax: a header row, "
            "a separator line `|---|---|`, and aligned cells.\n"
            "- Keep table rows concise; wrap or split cells that would exceed "
            "~60 characters.\n"
            "- Prefer bullet lists over dense paragraphs for sets of items, "
            "findings, or options.\n"
            "- Use inline code for file paths, identifiers, command names, "
            "and short code snippets.\n"
            "- Use fenced code blocks only for multi-line code or configuration.\n"
            "- Avoid emoji unless the user uses them first.\n"
            "- End with a brief 'Bottom line' or 'Next steps' section when appropriate."
        )
        return "\n".join(parts)

    @staticmethod
    def _format_plan(
        ctx: OrchestrationContext,
        profile: TaskProfile,
        resolved_model: str | None,
        runtime_name: str,
    ) -> str:
        agent = ctx.selected_agent
        lines: list[str] = [
            "Open Maestro execution plan",
            "",
        ]
        if agent is not None:
            lines.extend(
                [
                    f"Agent:        {agent.id} ({agent.name})",
                    f"Role:         {agent.role}",
                ]
            )
        lines.extend(
            [
                f"Runtime:      {runtime_name}",
                f"Vendor:       {_vendor_label(runtime_name)}",
                f"Model:        {resolved_model or 'unspecified'}",
                "",
                "Task profile:",
                f"  needs_tools:        {profile.needs_tools}",
                f"  needs_vision:       {profile.needs_vision}",
                f"  reasoning_depth:    {profile.reasoning_depth.value}",
                f"  coding_strength:    {profile.coding_strength.value}",
                f"  context_tokens:     {profile.context_tokens_estimate}",
                f"  latency_preference: {profile.latency_preference.value}",
                f"  cost_preference:    {profile.cost_preference.value}",
                "",
                "Enriched prompt:",
                ctx.enriched_prompt,
            ]
        )
        return "\n".join(lines)

    async def _execute_agent(
        self,
        ctx: OrchestrationContext,
        profile: TaskProfile,
        resolved_model: str | None,
        *,
        prompt: str,
        agent: AgentDefinition,
        allowed_tools: list[str] | None,
        blocked_tools: list[str] | None,
        permission_mode: str | None,
        deny_dangerous: bool,
        max_turns: int | None,
        mcp_servers: dict[str, Any] | None,
        session_id: str | None,
        resume: bool,
        fork: bool,
        dry_run: bool,
    ) -> tuple[AgentResult, AgentConfig]:
        """Run a single agent through the runtime and return its result."""
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

        agent_read_only = (
            agent.role.lower()
            in {r.lower() for r in policy.read_only_roles}
        )
        needs_guard = bool(blocked) or policy.is_active() or agent_read_only

        await self.event_bus.emit(
            "runtime.started",
            {
                "runtime": self.runtime.runtime_name,
                "agent_id": agent.id,
                "model": config.model,
                "session_id": session_id,
                "resume": resume,
                "fork": fork,
            },
        )

        if dry_run:
            result = AgentResult(text="", metadata={"dry_run": True})
        elif resume and session_id:
            logger.info(
                "Resuming session '%s' via runtime '%s'",
                session_id,
                self.runtime.runtime_name,
            )
            result = await self.runtime.resume(
                session_id, prompt, config=config
            )
        elif fork and session_id:
            logger.info(
                "Forking session '%s' via runtime '%s'",
                session_id,
                self.runtime.runtime_name,
            )
            result = await self.runtime.fork(
                session_id, prompt, config=config
            )
        elif needs_guard:
            guard_text = policy.guard_text(blocked)
            if guard_text:
                new_system_prompt = (
                    f"{config.system_prompt or ''}\n\n{guard_text}"
                ).strip()
                config = replace(config, system_prompt=new_system_prompt)

            async def tool_guard(
                tool_name: str, tool_input: dict[str, Any]
            ) -> bool:
                return await evaluate(
                    tool_name, tool_input, agent, policy
                )

            logger.info(
                "Delegating to agent '%s' via runtime '%s' with guardrails",
                agent.id,
                self.runtime.runtime_name,
            )
            result = await self.runtime.run_with_hooks(
                prompt,
                tool_guard=tool_guard,
                blocked_tools=blocked,
                config=config,
            )
        else:
            logger.info(
                "Delegating to agent '%s' via runtime '%s'",
                agent.id,
                self.runtime.runtime_name,
            )
            result = await self.runtime.run(prompt, config=config)

        result.metadata["selected_agent"] = agent.id

        await self.event_bus.emit(
            "runtime.completed",
            {
                "runtime": self.runtime.runtime_name,
                "agent_id": agent.id,
                "session_id": result.session_id,
                "is_error": result.is_error,
                "duration_ms": result.duration_ms,
            },
        )

        # Record measured throughput so future arbitration can prefer models
        # that have proven fast on this machine.
        if not result.is_error:
            record_result(
                config.model or "unknown",
                duration_ms=result.duration_ms,
                output_tokens=result.output_tokens,
            )

        return result, config

    async def _run_with_handoff(
        self,
        ctx: OrchestrationContext,
        profile: TaskProfile,
        resolved_model: str | None,
        *,
        prompt: str,
        allowed_tools: list[str] | None,
        blocked_tools: list[str] | None,
        permission_mode: str | None,
        deny_dangerous: bool,
        max_turns: int | None,
        mcp_servers: dict[str, Any] | None,
        session_id: str | None,
        resume: bool,
        fork: bool,
    ) -> tuple[AgentResult, AgentConfig]:
        """Run a read-only agent for analysis, then hand off to a writer."""
        read_only_agent = ctx.selected_agent
        assert read_only_agent is not None

        logger.info(
            "Handoff: running read-only agent '%s' before delegating write step",
            read_only_agent.id,
        )
        first_result, first_config = await self._execute_agent(
            ctx,
            profile,
            resolved_model,
            prompt=ctx.enriched_prompt,
            agent=read_only_agent,
            allowed_tools=allowed_tools,
            blocked_tools=blocked_tools,
            permission_mode=permission_mode,
            deny_dangerous=deny_dangerous,
            max_turns=max_turns,
            mcp_servers=mcp_servers,
            session_id=session_id,
            resume=resume,
            fork=fork,
            dry_run=False,
        )

        writer = self._select_writer_agent()
        if writer is None:
            logger.warning(
                "No mutating agent available for handoff; returning analysis only"
            )
            return first_result, first_config

        logger.info(
            "Handoff: delegating write step to agent '%s'",
            writer.id,
        )
        handoff_prompt = (
            f"Original request: {ctx.original_prompt}\n\n"
            f"The following analysis was produced by {read_only_agent.name}:\n\n"
            f"{first_result.text}\n\n"
            "Please complete the original request. If it asks you to write "
            "output to a file (e.g., a PRD, analysis, or report), use the "
            "analysis above and create the file(s) now."
        )

        ctx.selected_agent = writer
        ctx.enriched_prompt = handoff_prompt

        final_result, writer_config = await self._execute_agent(
            ctx,
            profile,
            resolved_model,
            prompt=handoff_prompt,
            agent=writer,
            allowed_tools=allowed_tools,
            blocked_tools=blocked_tools,
            permission_mode=permission_mode,
            deny_dangerous=deny_dangerous,
            max_turns=max_turns,
            mcp_servers=mcp_servers,
            session_id=session_id,
            resume=False,
            fork=False,
            dry_run=False,
        )

        # Preserve the handoff trail in metadata.
        final_result.metadata["handoff_from"] = read_only_agent.id
        final_result.metadata["handoff_analysis"] = first_result.text
        return final_result, writer_config

    def _select_writer_agent(self) -> AgentDefinition | None:
        """Pick the best agent to receive a write-step handoff."""
        candidates = [
            a for a in self.registry.list()
            if _agent_can_mutate(a) and not _agent_is_read_only(a)
        ]
        if not candidates:
            return None
        # Prefer engineer, then any mutating agent.
        for agent in candidates:
            if agent.role.lower() == "engineer":
                return agent
        return candidates[0]
