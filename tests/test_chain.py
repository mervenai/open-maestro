"""Tests for multi-agent chain planning and execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import json

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.config.capabilities import ReasoningLevel, TaskProfile
from open_maestro.orchestrator.chain import (
    ChainExecutor,
    ChainPlanner,
    HandoffPlan,
    HandoffStep,
    MAX_CHAIN_STEPS,
)
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime


class FakeRuntime(AgentRuntime):
    """Runtime that returns canned responses for planning tests."""

    def __init__(self, response_text: str = "{}", is_error: bool = False):
        self.response_text = response_text
        self.is_error = is_error
        self.last_prompt = ""

    @property
    def runtime_name(self) -> str:
        return "fake"

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        self.last_prompt = prompt
        return AgentResult(text=self.response_text, is_error=self.is_error)

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard=None,
        blocked_tools=None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self.run(prompt, config)

    async def resume(
        self, session_id: str, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        return await self.run(prompt, config)


class EchoRuntime(AgentRuntime):
    """Runtime that echoes a marker of the agent it was called for."""

    def __init__(self, marker: str = "echo"):
        self.marker = marker
        self.calls: list[tuple[str, str]] = []

    @property
    def runtime_name(self) -> str:
        return "echo"

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        agent_id = config.extra.get("agent_id", "unknown") if config else "unknown"
        self.calls.append((agent_id, prompt))
        return AgentResult(
            text=f"{self.marker}:{agent_id}",
            cost_usd=0.01,
            tokens_used=100,
            input_tokens=50,
            output_tokens=50,
            duration_ms=100,
        )

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard=None,
        blocked_tools=None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self.run(prompt, config)

    async def resume(
        self, session_id: str, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        return await self.run(prompt, config)


@pytest.fixture
def sample_registry():
    return AgentRegistry(
        {
            "researcher": AgentDefinition(
                id="researcher",
                name="Researcher",
                role="research",
                instructions="Investigates and explains.",
            ),
            "engineer": AgentDefinition(
                id="engineer",
                name="Engineer",
                role="engineer",
                instructions="Writes code and tests.",
                tools=["Write", "Edit", "Bash"],
            ),
            "qa": AgentDefinition(
                id="qa",
                name="QA Specialist",
                role="qa",
                instructions="Reviews and tests.",
            ),
            "documentation": AgentDefinition(
                id="documentation",
                name="Documentation Writer",
                role="documentation",
                instructions="Writes docs and reports.",
                tools=["Write"],
            ),
        }
    )


class TestChainPlanner:
    async def test_predefined_chain_for_implement(self, sample_registry):
        runtime = FakeRuntime()
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = planner._predefined_chain(
            "implement the budget import feature", first_agent=None
        )
        assert [s.agent_id for s in plan.steps] == ["researcher", "engineer", "qa"]

    async def test_predefined_chain_for_fix(self, sample_registry):
        runtime = FakeRuntime()
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = planner._predefined_chain("debug the login bug", first_agent=None)
        assert [s.agent_id for s in plan.steps] == ["researcher", "engineer", "qa"]

    async def test_predefined_chain_for_analyze(self, sample_registry):
        runtime = FakeRuntime()
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = planner._predefined_chain(
            "analyze the project architecture", first_agent=None
        )
        assert [s.agent_id for s in plan.steps] == ["researcher", "documentation"]

    async def test_llm_plan_parses_json(self, sample_registry):
        runtime = FakeRuntime(
            '{"steps": [{"agent_id": "engineer", "purpose": "write code"}]}'
        )
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = await planner.plan("build a parser")
        assert len(plan.steps) == 1
        assert plan.steps[0].agent_id == "engineer"

    async def test_llm_plan_falls_back_on_invalid_json(self, sample_registry):
        runtime = FakeRuntime("not json")
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = await planner.plan("implement the parser")
        assert [s.agent_id for s in plan.steps] == ["researcher", "engineer", "qa"]

    async def test_llm_plan_skips_unknown_agent_ids(self, sample_registry):
        runtime = FakeRuntime(
            '{"steps": [{"agent_id": "missing"}, {"agent_id": "engineer", "purpose": "x"}]}'
        )
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = await planner._llm_plan("task")
        assert [s.agent_id for s in plan.steps] == ["engineer"]

    async def test_plan_caps_at_max_steps(self, sample_registry):
        steps = [
            {"agent_id": "researcher", "purpose": f"step {i}"}
            for i in range(MAX_CHAIN_STEPS + 3)
        ]
        runtime = FakeRuntime(json.dumps({"steps": steps}))
        planner = ChainPlanner(runtime=runtime, registry=sample_registry)
        plan = await planner._llm_plan("task")
        assert len(plan.steps) == MAX_CHAIN_STEPS


class TestChainExecutor:
    async def test_executes_all_steps(self, sample_registry):
        plan = HandoffPlan(
            steps=[
                HandoffStep(agent_id="researcher", purpose="research"),
                HandoffStep(agent_id="engineer", purpose="implement"),
            ],
            original_prompt="build a parser",
        )
        executor = ChainExecutor(registry=sample_registry)

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            return_value=EchoRuntime(marker="echo"),
        ):
            result = await executor.execute(plan, original_prompt="build a parser")

        assert result.is_error is False
        assert "Chain result" in result.text
        assert "## Researcher (research)" in result.text
        assert "## Engineer (engineer)" in result.text
        assert result.tokens_used == 200
        assert result.cost_usd == 0.02

    async def test_stops_on_error(self, sample_registry):
        plan = HandoffPlan(
            steps=[
                HandoffStep(agent_id="researcher", purpose="research"),
                HandoffStep(agent_id="engineer", purpose="implement"),
            ],
            original_prompt="build a parser",
        )
        executor = ChainExecutor(registry=sample_registry)

        failing_runtime = EchoRuntime(marker="fail")
        failing_runtime.run = AsyncMock(
            return_value=AgentResult(text="bad", is_error=True)
        )

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            return_value=failing_runtime,
        ):
            result = await executor.execute(plan, original_prompt="build a parser")

        assert result.is_error is True
        assert len(result.metadata["steps"]) == 1

    async def test_dry_run_format(self, sample_registry):
        plan = HandoffPlan(
            steps=[
                HandoffStep(agent_id="researcher", purpose="research"),
                HandoffStep(agent_id="engineer", purpose="implement"),
            ],
            original_prompt="build a parser",
        )
        text = ChainExecutor.format_plan(plan)
        assert "chain plan" in text.lower()
        assert "researcher" in text
        assert "engineer" in text

    async def test_no_steps_returns_error(self, sample_registry):
        plan = HandoffPlan(steps=[], original_prompt="build a parser")
        executor = ChainExecutor(registry=sample_registry)
        result = await executor.execute(plan, original_prompt="build a parser")
        assert result.is_error is True


class TestProjectManagerChainIntegration:
    async def test_handle_chain_dry_run(self, sample_registry):
        from open_maestro.orchestrator.pm import ProjectManager

        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=sample_registry)
        result = await pm.handle(
            "implement a parser",
            agent_id="engineer",
            chain=True,
            dry_run=True,
        )
        assert result.is_error is False
        assert result.metadata.get("chain") is True
        assert result.metadata.get("dry_run") is True
        assert "chain plan" in result.text.lower()

    async def test_handle_chain_executes(self, sample_registry):
        from open_maestro.orchestrator.pm import ProjectManager

        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=sample_registry)

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            return_value=EchoRuntime(marker="chain"),
        ):
            result = await pm.handle(
                "implement a parser",
                agent_id="engineer",
                chain=True,
            )

        assert result.is_error is False
        assert result.metadata.get("chain") is True
        assert "## Researcher (research)" in result.text
        assert "## Engineer (engineer)" in result.text


class GateRuntime(AgentRuntime):
    """Runtime that distinguishes step runs from critic-pass runs by prompt."""

    def __init__(self):
        self.step_calls = 0
        self.critic_calls: list[str] = []

    @property
    def runtime_name(self) -> str:
        return "echo"

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        if "Review the implementation" in prompt:
            self.critic_calls.append(prompt)
            return AgentResult(
                text="## Verdict: BLOCK\n\n## Findings\n- hardcoded secret in src/app.py:12"
            )
        self.step_calls += 1
        return AgentResult(text="step output")

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard=None,
        blocked_tools=None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self.run(prompt, config)

    async def resume(
        self, session_id: str, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        return await self.run(prompt, config)


@pytest.fixture
def gate_registry():
    return AgentRegistry(
        {
            "researcher": AgentDefinition(
                id="researcher",
                name="Researcher",
                role="research",
                instructions="Investigates and explains.",
            ),
            "engineer": AgentDefinition(
                id="engineer",
                name="Engineer",
                role="engineer",
                instructions="Writes code and tests.",
                tools=["Write", "Edit", "Bash"],
            ),
            "code-critic": AgentDefinition(
                id="code-critic",
                name="Code Critic",
                role="critic",
                instructions="Adversarial code review.",
                tools=["Read", "Grep"],
            ),
        }
    )


async def _run_chain(gate_registry, steps, runtime, **executor_kwargs):
    plan = HandoffPlan(steps=steps, original_prompt="build a parser")
    executor = ChainExecutor(registry=gate_registry, **executor_kwargs)
    patches = [
        patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ),
        patch(
            "open_maestro.runtime.factory.create_runtime",
            return_value=runtime,
        ),
        patch(
            "open_maestro.orchestrator.chain.critic_mod.snapshot_head",
            return_value="deadbeef",
        ),
    ]
    with patches[0], patches[1], patches[2]:
        return await executor.execute(plan, original_prompt="build a parser")


class TestChainCriticGate:
    async def test_mutating_step_triggers_critic(self, gate_registry):
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[[], [("src/app.py", 80)]],
        ):
            result = await _run_chain(
                gate_registry,
                [HandoffStep(agent_id="engineer", purpose="implement the parser")],
                runtime,
            )
        assert runtime.step_calls == 1
        assert len(runtime.critic_calls) == 1
        assert "CODE REVIEW BLOCK" in result.text
        assert "hardcoded secret" in result.text

    async def test_critic_spec_uses_step_purpose(self, gate_registry):
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[[], [("src/app.py", 80), ("src/lexer.py", 30)]],
        ):
            await _run_chain(
                gate_registry,
                [HandoffStep(agent_id="engineer", purpose="implement the parser")],
                runtime,
            )
        assert len(runtime.critic_calls) == 1
        assert "implement the parser" in runtime.critic_calls[0]

    async def test_doc_only_step_skips_critic(self, gate_registry):
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[[], [("docs/readme.md", 300)]],
        ):
            result = await _run_chain(
                gate_registry,
                [HandoffStep(agent_id="engineer", purpose="write docs")],
                runtime,
            )
        assert runtime.step_calls == 1
        assert runtime.critic_calls == []
        assert "CODE REVIEW BLOCK" not in result.text

    async def test_trivial_change_skips_critic(self, gate_registry):
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[[], [("src/app.py", 3)]],
        ):
            await _run_chain(
                gate_registry,
                [HandoffStep(agent_id="engineer", purpose="tweak")],
                runtime,
            )
        assert runtime.critic_calls == []

    async def test_read_only_step_skips_baseline_and_critic(self, gate_registry):
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.snapshot_head"
        ) as snapshot, patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[[], [("src/app.py", 80)]],
        ):
            await _run_chain(
                gate_registry,
                [HandoffStep(agent_id="researcher", purpose="investigate")],
                runtime,
            )
        snapshot.assert_not_called()
        assert runtime.critic_calls == []

    async def test_gate_disabled_skips_everything(self, gate_registry):
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.snapshot_head"
        ) as snapshot, patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[[], [("src/app.py", 80)]],
        ):
            await _run_chain(
                gate_registry,
                [HandoffStep(agent_id="engineer", purpose="implement")],
                runtime,
                critic_gate=False,
            )
        snapshot.assert_not_called()
        assert runtime.critic_calls == []

    async def test_prior_step_changes_not_recounted(self, gate_registry):
        # Step 1 changes a file; step 2 makes no new changes. Only the delta
        # is offered to the critic, so step 2 must not trigger a review and
        # step 1 triggers exactly one.
        runtime = GateRuntime()
        with patch(
            "open_maestro.orchestrator.chain.critic_mod.detect_source_changes",
            side_effect=[
                [],  # step 1 baseline
                [("src/app.py", 80)],  # step 1 post
                [("src/app.py", 80)],  # step 2 baseline
                [("src/app.py", 80)],  # step 2 post (no delta)
            ],
        ):
            result = await _run_chain(
                gate_registry,
                [
                    HandoffStep(agent_id="engineer", purpose="implement"),
                    HandoffStep(agent_id="engineer", purpose="polish docs"),
                ],
                runtime,
            )
        assert len(runtime.critic_calls) == 1
        assert "CODE REVIEW BLOCK" in result.text
