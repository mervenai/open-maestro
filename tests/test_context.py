"""Tests for context-pressure monitoring."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.context.budget import ContextBudget
from open_maestro.context.monitor import ContextMonitor
from open_maestro.orchestrator.pm import OrchestrationContext, ProjectManager
from open_maestro.runtime.base import AgentResult, AgentRuntime
from open_maestro.session.store import SessionRecord, SessionStore


class FakeRuntime(AgentRuntime):
    @property
    def runtime_name(self) -> str:
        return "fake"

    async def run(self, prompt, config=None):
        return AgentResult(text="done")

    async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
        return AgentResult(text="done")

    async def resume(self, session_id, prompt, config=None):
        return AgentResult(text="done")


class TestContextMonitor:
    def test_stays_under_threshold_for_small_usage(self):
        monitor = ContextMonitor(ContextBudget(max_context_tokens=1000))
        result = AgentResult(text="hello", tokens_used=100)
        assert monitor.update(result) is None
        assert monitor.snapshot.tokens_used == 100

    def test_warning_threshold_triggered(self):
        monitor = ContextMonitor(ContextBudget(max_context_tokens=1000))
        result = AgentResult(text="x" * 700 * 4, tokens_used=700)
        assert monitor.update(result) == "warning"

    def test_critical_threshold_triggered(self):
        monitor = ContextMonitor(ContextBudget(max_context_tokens=1000))
        result = AgentResult(text="x" * 900 * 4, tokens_used=900)
        assert monitor.update(result) == "critical"

    def test_input_and_output_tokens_added(self):
        monitor = ContextMonitor(ContextBudget(max_context_tokens=1000))
        result = AgentResult(
            text="ok", input_tokens=100, output_tokens=50
        )
        monitor.update(result)
        assert monitor.snapshot.input_tokens == 100
        assert monitor.snapshot.output_tokens == 50
        assert monitor.snapshot.tokens_used == 150

    def test_estimates_tokens_when_not_provided(self):
        monitor = ContextMonitor(ContextBudget(max_context_tokens=1000))
        result = AgentResult(text="a" * 400)
        monitor.update(result)
        assert monitor.snapshot.tokens_used == 100


class TestResumeLog:
    def test_resume_log_includes_task_and_agent(self):
        monitor = ContextMonitor(ContextBudget(max_context_tokens=1000))
        agent = AgentDefinition(id="engineer", name="Engineer", role="engineer")
        ctx = OrchestrationContext(original_prompt="refactor parser")
        ctx.selected_agent = agent
        ctx.memories = ["memory one"]
        ctx.code_results = [{"file_path": "src/parser.py"}]
        monitor.snapshot.tokens_used = 900

        log = monitor.build_resume_log(ctx, original_prompt="refactor parser")
        assert "refactor parser" in log
        assert "engineer" in log
        assert "memory one" in log
        assert "src/parser.py" in log
        assert "900" in log


class TestProjectManagerContextSeeding:
    @pytest.mark.asyncio
    async def test_resume_seeds_context_from_previous_session(self, tmp_path):
        store = SessionStore(base_dirs=[tmp_path / "sessions"])
        store.save(
            SessionRecord(
                session_id="sess_prev",
                runtime_name="fake",
                agent_id="engineer",
                model="fast",
                prompt_summary="previous",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                tokens_used=5000,
                input_tokens=3000,
                output_tokens=2000,
            )
        )

        registry = AgentRegistry(
            {
                "engineer": AgentDefinition(
                    id="engineer", name="Engineer", role="engineer"
                )
            }
        )
        pm = ProjectManager(
            runtime=FakeRuntime(),
            registry=registry,
            session_store=store,
            context_budget=ContextBudget(max_context_tokens=10000),
        )

        await pm.handle(
            "continue",
            agent_id="engineer",
            session_id="sess_prev",
            resume=True,
            dry_run=True,
        )

        assert pm.context_monitor.snapshot.tokens_used == 5000
        assert pm.context_monitor.snapshot.input_tokens == 3000
        assert pm.context_monitor.snapshot.output_tokens == 2000
