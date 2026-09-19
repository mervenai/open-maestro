"""Parallel multi-agent swarm execution for Open Maestro.

A swarm fans a single user request out to N specialist workers that run
concurrently — each with its own capability-aware runtime/model selection, so
workers may land on different vendors/models in the same swarm — and then
synthesizes a grouped final response.

Shape (see Jira MSTRO-95):

1. Leader (optional, cheap model): condenses the triggering evidence into a
   shared digest so every worker starts from the same understanding.
2. Fan-out: workers run in parallel under a concurrency semaphore, each with
   its own target. Workers never share a write target; the planner rejects any
   plan where two workers would write the same file (sequential chain
   fallback instead). One worker's failure does not kill the swarm.
3. Consistency pass (optional, cheap model): verifies cross-references among
   the produced artifacts. WARN-only.

The swarm fires automatically inside chain mode when the task names 3+
independent targets (files, repos, epics, angles). Otherwise the sequential
:class:`~open_maestro.orchestrator.chain.ChainPlanner` path handles the task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from open_maestro.agents.registry import _agent_can_mutate
from open_maestro.config.capabilities import (
    CostLevel,
    TaskProfile,
    TaskProfiler,
)
from open_maestro.orchestrator import critic as critic_mod
from open_maestro.orchestrator.chain import (
    ChainExecutor,
    HandoffStep,
    StepResult,
)
from open_maestro.runtime.base import AgentConfig, AgentResult

if TYPE_CHECKING:
    from open_maestro.agents.definition import AgentDefinition
    from open_maestro.agents.registry import AgentRegistry
    from open_maestro.events.bus import EventBus
    from open_maestro.runtime.base import AgentRuntime

logger = logging.getLogger(__name__)

MAX_SWARM_WORKERS = 6

_FILE_PATH_RE = re.compile(r"[\w./-]+\.(?:md|py|ts|tsx|js|jsx|yaml|yml|json)")

# Path-ish tokens that may name a directory (at least one path separator).
# The is_dir() check in _extract_targets is the real gate; this only finds
# candidates such as "docs/", "/docs/intake", or "./docs".
_DIR_TOKEN_RE = re.compile(r"[./]?[\w-]+(?:/[\w.-]+)*/?")


def _extract_dir_tokens(prompt: str) -> list[str]:
    """Directory-looking tokens from the prompt, stripped of punctuation."""
    tokens: list[str] = []
    for raw in _DIR_TOKEN_RE.findall(prompt):
        token = raw.strip().rstrip(".,;:!?)").lstrip("(")
        if not token or token in tokens:
            continue
        tokens.append(token)
    return tokens


def _resolve_dir(token: str) -> Path | None:
    """Resolve a directory token to an existing directory.

    Users naturally write "/docs" to mean "the docs folder here", so a
    leading-slash (or "./") token that doesn't exist as an absolute path is
    retried relative to the working directory.
    """
    path = Path(token)
    if path.is_dir():
        return path
    if token.startswith("/"):
        rel = Path(token.lstrip("/"))
        if rel.is_dir():
            return rel
    elif token.startswith("./"):
        rel = Path(token[2:])
        if rel.is_dir():
            return rel
    return None

_SWARM_PLANNER_SYSTEM_PROMPT = """You are a multi-agent swarm planner.

Given the user's task and the available specialist agents, decide whether the
task decomposes into independent parallel workstreams (different files to
update, repos to analyze, epics to assess, angles to evaluate). If it does,
respond with **only** a JSON object in this exact shape:

{
  "leader": true,
  "consistency": true,
  "workers": [
    {"agent_id": "<id>", "purpose": "<what this worker produces>", "target_file": "<path or omit>"},
    ...
  ]
}

Rules:
- Use a leader when the task carries shared evidence every worker needs
  condensed first; use a consistency pass when workers produce artifacts that
  cross-reference each other.
- Each worker must have a distinct write target (or no write target). Two
  workers writing the same file is forbidden — leave such work sequential.
- 3 to 6 workers. Fewer than 3 independent workstreams is not a swarm.
- Each worker must use one of the listed agent IDs.
- Do not include markdown fences or any text outside the JSON.
"""  # noqa: E501


@dataclass
class SwarmWorker:
    """A single parallel worker in a swarm plan."""

    agent_id: str
    purpose: str
    target_file: str | None = None
    task_profile: TaskProfile | None = None


@dataclass
class SwarmPlan:
    """Planned decomposition of a user request into parallel workers."""

    workers: list[SwarmWorker]
    original_prompt: str
    use_leader: bool = False
    consistency_check: bool = False


class SwarmPlanner:
    """Plan a parallel swarm for a user task, or decline (return None).

    Tries an LLM-driven planner first; if that declines or fails, falls back
    to a file-path heuristic that catches the canonical "update these N docs"
    shape without needing a planner LLM.
    """

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
    ) -> SwarmPlan | None:
        """Return a swarm plan for *prompt*, or None if the task isn't a swarm."""
        llm_plan = await self._llm_plan(prompt, profile=profile)
        if llm_plan is not None:
            return llm_plan
        return self._heuristic_plan(prompt, first_agent=first_agent)

    async def _llm_plan(
        self,
        prompt: str,
        profile: TaskProfile | None = None,
    ) -> SwarmPlan | None:
        agents = self.registry.list()
        if not agents:
            return None

        lines = ["Available agents:", ""]
        for agent in agents:
            lines.append(f"- id: {agent.id}")
            lines.append(f"  name: {agent.name}")
            lines.append(f"  role: {agent.role}")
            lines.append(
                f"  description: {agent.description or agent.instructions[:200]}"
            )
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
            system_prompt=_SWARM_PLANNER_SYSTEM_PROMPT,
            model=self.model,
            max_turns=1,
            task_profile=profile,
        )
        try:
            result = await self.runtime.run("\n".join(lines), config=config)
        except Exception as exc:
            logger.warning("LLM swarm planning failed: %s", exc)
            return None

        if result.is_error:
            logger.warning("LLM swarm planning returned error: %s", result.text)
            return None

        try:
            data = json.loads(result.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        except json.JSONDecodeError:
            logger.warning(
                "Swarm planner returned non-JSON response: %s", result.text[:500]
            )
            return None
        if not isinstance(data, dict):
            return None

        raw_workers = data.get("workers")
        if not isinstance(raw_workers, list):
            return None

        workers: list[SwarmWorker] = []
        for item in raw_workers[:MAX_SWARM_WORKERS]:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id:
                continue
            try:
                self.registry.get(agent_id)
            except KeyError:
                logger.warning(
                    "LLM swarm planner chose unknown agent_id '%s'; skipping",
                    agent_id,
                )
                continue
            target = item.get("target_file")
            workers.append(
                SwarmWorker(
                    agent_id=agent_id,
                    purpose=str(item.get("purpose", "")),
                    target_file=str(target) if isinstance(target, str) else None,
                )
            )

        return self._validate(
            workers,
            use_leader=bool(data.get("leader")),
            consistency=bool(data.get("consistency")),
            prompt=prompt,
        )

    def _heuristic_plan(
        self,
        prompt: str,
        first_agent: AgentDefinition | None = None,
    ) -> SwarmPlan | None:
        """One worker per distinct target when the prompt names 3+.

        Targets come from explicit file paths in the prompt and from
        *folder expansion*: directories named in the prompt are scanned for
        markdown artifacts (docs folders), so "update whatever needs updating
        in docs/" fans out without the user listing every file.
        """
        targets = self._extract_targets(prompt)
        targets = targets[:MAX_SWARM_WORKERS]
        if len(targets) < 3:
            return None

        workers: list[SwarmWorker] = []
        for target in targets:
            agent = self._agent_for_target(target) or first_agent
            if agent is None:
                continue
            workers.append(
                SwarmWorker(
                    agent_id=agent.id,
                    purpose=f"Update {target} per the task below.",
                    target_file=target,
                )
            )
        return self._validate(workers, prompt=prompt)

    def _extract_targets(self, prompt: str) -> list[str]:
        """Distinct update targets: explicit file paths plus markdown files
        found inside directories named in the prompt (folder expansion)."""
        targets: list[str] = []
        for match in _FILE_PATH_RE.findall(prompt):
            if match not in targets:
                targets.append(match)
        for directory in _extract_dir_tokens(prompt):
            path = _resolve_dir(directory)
            if path is None:
                continue
            for child in sorted(path.rglob("*.md")):
                rel = child.as_posix()
                if rel not in targets:
                    targets.append(rel)
        return targets

    def _agent_for_target(self, target: str) -> AgentDefinition | None:
        """Pick an agent for a heuristic worker: documentation for existing
        docs, otherwise the first mutating agent."""
        agents = self.registry.list()
        if not agents:
            return None
        if Path(target).exists():
            for agent in agents:
                if agent.role.lower() == "documentation":
                    return agent
        for agent in agents:
            if _agent_can_mutate(agent):
                return agent
        return agents[0]

    @staticmethod
    def _validate(
        workers: list[SwarmWorker],
        *,
        use_leader: bool = False,
        consistency: bool = False,
        prompt: str,
    ) -> SwarmPlan | None:
        """Enforce swarm invariants; return None (→ chain fallback) if violated."""
        if len(workers) < 3:
            return None
        targets = [w.target_file for w in workers if w.target_file]
        if len(targets) != len(set(targets)):
            logger.info(
                "Swarm plan rejected: two workers share a write target; "
                "falling back to sequential chain"
            )
            return None
        return SwarmPlan(
            workers=workers,
            original_prompt=prompt,
            use_leader=use_leader,
            consistency_check=consistency,
        )

    @staticmethod
    def format_plan(plan: SwarmPlan) -> str:
        """Return a human-readable rendering of a swarm plan."""
        lines = [
            "Open Maestro swarm plan",
            "",
            f"Original task: {plan.original_prompt}",
            f"Leader digest: {'yes' if plan.use_leader else 'no'} | "
            f"Consistency pass: {'yes' if plan.consistency_check else 'no'}",
            "",
            f"Workers ({len(plan.workers)}, parallel):",
        ]
        for idx, worker in enumerate(plan.workers, start=1):
            target = f" -> {worker.target_file}" if worker.target_file else ""
            lines.append(f"  {idx}. {worker.agent_id}: {worker.purpose}{target}")
        return "\n".join(lines)


class SwarmExecutor(ChainExecutor):
    """Execute a swarm plan: optional leader, parallel fan-out, synthesis.

    Reuses :class:`ChainExecutor`'s per-agent runtime/model selection
    (:meth:`_run_agent`) so each worker independently lands on the cheapest
    capable runtime and model — workers may run on different vendors/models
    concurrently. There is deliberately no per-worker critic gate (parallel
    diffs can't be attributed to one worker); one aggregate critic pass runs
    after the fan-out when the critic gate is enabled.
    """

    async def execute(
        self,
        plan: SwarmPlan,
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
        digest = await self._run_leader(plan, base_profile=base_profile)

        # Critic baseline before fan-out so the aggregate pass can attribute
        # the combined delta (per-worker attribution is impossible in parallel).
        baseline: dict[str, int] | None = None
        before_ref: str | None = None
        if self.critic_gate:
            before_ref = critic_mod.snapshot_head(Path.cwd())
            baseline = dict(critic_mod.detect_source_changes(Path.cwd(), None))

        max_concurrent = max(1, int(os.environ.get("MAESTRO_SWARM_MAX_WORKERS", "4")))
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run_one(idx: int, worker: SwarmWorker) -> StepResult:
            async with semaphore:
                await self._emit("swarm.worker_started", {
                    "worker": idx,
                    "total": len(plan.workers),
                    "agent_id": worker.agent_id,
                    "purpose": worker.purpose,
                })
                try:
                    agent = self.registry.get(worker.agent_id)
                    step = HandoffStep(
                        agent_id=worker.agent_id,
                        purpose=worker.purpose,
                        task_profile=worker.task_profile,
                    )
                    profile = self._step_profile(step, agent, base_profile)
                    prompt = self._build_worker_prompt(
                        worker,
                        agent,
                        original_prompt,
                        digest,
                        memories=memories,
                        code_results=code_results,
                    )
                    try:
                        runtime_name, config, result = await self._run_agent(
                            agent,
                            prompt,
                            idx=idx,
                            profile=profile,
                            blocked_tools=blocked_tools,
                            allowed_tools=allowed_tools,
                            permission_mode=permission_mode,
                            deny_dangerous=deny_dangerous,
                            max_turns=max_turns,
                            mcp_servers=mcp_servers,
                        )
                    except Exception as exc:
                        logger.exception("Swarm worker %s failed", idx)
                        result = AgentResult(
                            text=f"Worker failed: {exc}",
                            is_error=True,
                        )
                        runtime_name, config = "unknown", AgentConfig()
                except Exception as exc:  # registry/planning-level failure
                    logger.exception("Swarm worker %s setup failed", idx)
                    agent = None
                    result = AgentResult(text=f"Worker failed: {exc}", is_error=True)
                    runtime_name, config = "unknown", AgentConfig()

                await self._emit("swarm.worker_completed", {
                    "worker": idx,
                    "total": len(plan.workers),
                    "agent_id": worker.agent_id,
                    "is_error": result.is_error,
                })
                return StepResult(
                    step=HandoffStep(
                        agent_id=worker.agent_id, purpose=worker.purpose
                    ),
                    agent=agent
                    if agent is not None
                    else self._fallback_agent(worker),
                    runtime_name=runtime_name,
                    model=config.model,
                    result=result,
                )

        step_results: list[StepResult] = await asyncio.gather(
            *[
                _run_one(idx, worker)
                for idx, worker in enumerate(plan.workers, start=1)
            ]
        )

        extras: list[str] = []

        if self.critic_gate and baseline is not None:
            current = dict(
                critic_mod.detect_source_changes(Path.cwd(), before_ref)
            )
            changes = [
                (path, lines - baseline.get(path, 0))
                for path, lines in current.items()
                if lines - baseline.get(path, 0) > 0
            ]
            if changes and critic_mod.should_trigger(changes):
                extras.append(
                    await self._aggregate_critic(
                        original_prompt=original_prompt,
                        changes=changes,
                        base_profile=base_profile,
                        blocked_tools=blocked_tools,
                        allowed_tools=allowed_tools,
                        permission_mode=permission_mode,
                        deny_dangerous=deny_dangerous,
                        max_turns=max_turns,
                        mcp_servers=mcp_servers,
                    )
                )

        if plan.consistency_check:
            targets = [w.target_file for w in plan.workers if w.target_file]
            if len(targets) >= 2:
                check = await self._consistency_pass(
                    targets,
                    base_profile=base_profile,
                    blocked_tools=blocked_tools,
                    allowed_tools=allowed_tools,
                    permission_mode=permission_mode,
                    deny_dangerous=deny_dangerous,
                    max_turns=max_turns,
                    mcp_servers=mcp_servers,
                )
                if check:
                    extras.append(check)

        return self._synthesize(plan, step_results, extras=extras)

    def _fallback_agent(self, worker: SwarmWorker) -> AgentDefinition:
        """Best-effort agent object for synthesis when setup failed."""
        try:
            return self.registry.get(worker.agent_id)
        except KeyError:
            agents = self.registry.list()
            if agents:
                return agents[0]
            raise

    async def _run_leader(
        self,
        plan: SwarmPlan,
        *,
        base_profile: TaskProfile | None = None,
    ) -> str:
        """Condense the triggering evidence into a shared digest. Empty on failure."""
        if not plan.use_leader:
            return ""
        leader = self._agent_by_role("research") or self._agent_by_role("researcher")
        if leader is None:
            agents = self.registry.list()
            leader = agents[0] if agents else None
        if leader is None:
            return ""

        profile = (
            replace(base_profile, cost_preference=CostLevel.LOW)
            if base_profile is not None
            else TaskProfiler.from_prompt(
                plan.original_prompt, cost_preference=CostLevel.LOW
            )
        )
        prompt = (
            "Condense the task/evidence below into a brief digest for a team of "
            "agents working on it in parallel. Cover: what changed, what is "
            "superseded, and the key facts each worker must know. Be concise.\n\n"
            f"{plan.original_prompt}"
        )
        try:
            _runtime_name, _config, result = await self._run_agent(
                leader, prompt, idx=0, profile=profile
            )
        except Exception as exc:
            logger.warning("Swarm leader digest failed: %s", exc)
            return ""
        if result.is_error:
            logger.warning("Swarm leader digest returned error: %s", result.text)
            return ""
        return result.text

    def _agent_by_role(self, role: str) -> AgentDefinition | None:
        candidates = [
            a for a in self.registry.list() if a.role.lower() == role.lower()
        ]
        return candidates[0] if candidates else None

    async def _aggregate_critic(
        self,
        *,
        original_prompt: str,
        changes: list[tuple[str, int]],
        base_profile: TaskProfile | None,
        blocked_tools: list[str] | None,
        allowed_tools: list[str] | None,
        permission_mode: str | None,
        deny_dangerous: bool,
        max_turns: int | None,
        mcp_servers: dict[str, Any] | None,
    ) -> str:
        """One review pass over the swarm's combined diff. WARN/BLOCK surfaced."""
        try:
            critic = self.registry.get("code-critic")
        except KeyError:
            logger.warning(
                "Swarm critic gate: 'code-critic' agent not in registry; skipping"
            )
            return ""
        change_lines = "\n".join(
            f"- {path} ({lines} lines)" for path, lines in changes
        )
        prompt = (
            "Review the combined changes produced by a swarm of agents working "
            "in parallel. Judge the code in the repository against the original "
            "task only.\n\n"
            f"Original task:\n{original_prompt}\n\n"
            f"Files changed across all workers:\n{change_lines}"
        )
        try:
            _runtime_name, _config, critic_result = await self._run_agent(
                critic,
                prompt,
                idx=0,
                profile=self._step_profile(
                    HandoffStep(agent_id=critic.id, purpose="code review"),
                    critic,
                    base_profile,
                ),
                blocked_tools=blocked_tools,
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                deny_dangerous=deny_dangerous,
                max_turns=max_turns,
                mcp_servers=mcp_servers,
            )
        except Exception as exc:
            logger.warning("Swarm critic gate failed: %s", exc)
            return ""
        if critic_result.is_error:
            return ""
        verdict = critic_mod.parse_verdict(critic_result.text) or "WARN"
        findings = critic_mod.extract_findings(critic_result.text)
        header = (
            "\n\n---\n⚠️ SWARM CODE REVIEW BLOCK (code-critic)"
            if verdict == "BLOCK"
            else f"\n\n---\nSwarm code review (code-critic): {verdict}"
        )
        return header + ("\n" + "\n".join(findings) if findings else "")

    async def _consistency_pass(
        self,
        targets: list[str],
        *,
        base_profile: TaskProfile | None,
        blocked_tools: list[str] | None,
        allowed_tools: list[str] | None,
        permission_mode: str | None,
        deny_dangerous: bool,
        max_turns: int | None,
        mcp_servers: dict[str, Any] | None,
    ) -> str:
        """Verify cross-references among worker artifacts. WARN-only."""
        agent = self._agent_by_role("documentation") or self._agent_by_role(
            "research"
        )
        if agent is None:
            agents = self.registry.list()
            agent = agents[0] if agents else None
        if agent is None:
            return ""
        profile = (
            replace(base_profile, cost_preference=CostLevel.LOW)
            if base_profile is not None
            else TaskProfile(cost_preference=CostLevel.LOW)
        )
        file_list = "\n".join(f"- {t}" for t in targets)
        prompt = (
            "The artifacts below were just updated in parallel by different "
            "agents. Check that cross-references between them still hold "
            "(IDs cited in one file exist and agree in the other, terminology "
            "is consistent, no contradictions). Report only real mismatches; "
            "say 'consistent' if everything checks out.\n\n"
            f"Artifacts:\n{file_list}"
        )
        try:
            _runtime_name, _config, result = await self._run_agent(
                agent,
                prompt,
                idx=0,
                profile=profile,
                blocked_tools=blocked_tools,
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                deny_dangerous=deny_dangerous,
                max_turns=max_turns,
                mcp_servers=mcp_servers,
            )
        except Exception as exc:
            logger.warning("Swarm consistency pass failed: %s", exc)
            return ""
        if result.is_error or not result.text.strip():
            return ""
        return (
            "\n\n---\nSwarm consistency check "
            f"({agent.id}):\n{result.text.strip()}"
        )

    @staticmethod
    def _build_worker_prompt(
        worker: SwarmWorker,
        agent: AgentDefinition,
        original_prompt: str,
        digest: str,
        *,
        memories: list[str] | None = None,
        code_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Assemble the prompt for one parallel worker."""
        parts: list[str] = [
            f"Original task: {original_prompt}",
            "",
            f"Your worker assignment: {worker.purpose}",
            "",
            f"You are the '{agent.name}' specialist. {agent.role}",
        ]
        if worker.target_file:
            parts.append(f"\nYour write target: {worker.target_file}")
            parts.append(
                "No other agent is writing this file, but others are updating "
                "related artifacts in parallel — stay inside your target."
            )
        if digest:
            parts.append("\nShared evidence digest (from lead agent):")
            parts.append(digest)
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
            "- Keep your response focused on your assigned worker task.\n"
            "- If you write an artifact, confirm the file path at the end."
        )
        return "\n".join(parts)

    @staticmethod
    def _synthesize(
        plan: SwarmPlan,
        step_results: list[StepResult],
        *,
        extras: list[str] | None = None,
    ) -> AgentResult:
        """Combine all worker outputs into a single grouped response."""
        if not step_results:
            return AgentResult(text="No swarm workers were executed.", is_error=True)

        lines: list[str] = [
            f"# Swarm result ({len(step_results)} workers)",
            "",
        ]
        for sr in step_results:
            lines.append(f"## {sr.agent.name} ({sr.agent.role})")
            lines.append(
                f"Runtime: {sr.runtime_name} | Model: {sr.model or 'unspecified'}"
            )
            lines.append("")
            lines.append(sr.result.text)
            lines.append("")
        for extra in extras or []:
            if extra:
                lines.append(extra)
                lines.append("")

        final_result = step_results[-1].result
        is_error = all(sr.result.is_error for sr in step_results)

        # Aggregate metrics across all workers for context monitoring.
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
                "swarm": True,
                "workers": [
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
