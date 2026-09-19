"""Tests for swarm planning and parallel execution (MSTRO-95)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.orchestrator.swarm import (
    MAX_SWARM_WORKERS,
    SwarmExecutor,
    SwarmPlanner,
    SwarmPlan,
    SwarmWorker,
)
from open_maestro.runtime.base import AgentResult

from test_chain import EchoRuntime, FakeRuntime


@pytest.fixture
def swarm_registry():
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
            "documentation": AgentDefinition(
                id="documentation",
                name="Documentation Writer",
                role="documentation",
                instructions="Writes docs and reports.",
                tools=["Write"],
            ),
            "qa": AgentDefinition(
                id="qa",
                name="QA Specialist",
                role="qa",
                instructions="Reviews and tests.",
            ),
        }
    )


def _llm_payload(workers, **flags):
    return json.dumps({"workers": workers, **flags})


class TestSwarmPlannerLLM:
    async def test_llm_plan_parses_and_validates(self, swarm_registry):
        runtime = FakeRuntime(
            _llm_payload(
                [
                    {"agent_id": "researcher", "purpose": "analyze repos"},
                    {"agent_id": "engineer", "purpose": "assess impact", "target_file": "docs/a.md"},
                    {"agent_id": "documentation", "purpose": "update synthesis", "target_file": "docs/b.md"},
                ],
                leader=True,
                consistency=True,
            )
        )
        planner = SwarmPlanner(runtime=runtime, registry=swarm_registry)
        plan = await planner.plan("analyze each epic in parallel")
        assert plan is not None
        assert len(plan.workers) == 3
        assert plan.use_leader is True
        assert plan.consistency_check is True

    async def test_unknown_agent_ids_dropped_below_three_returns_none(self, swarm_registry):
        runtime = FakeRuntime(
            _llm_payload(
                [
                    {"agent_id": "missing-1", "purpose": "x"},
                    {"agent_id": "missing-2", "purpose": "y"},
                    {"agent_id": "engineer", "purpose": "z"},
                ]
            )
        )
        planner = SwarmPlanner(runtime=runtime, registry=swarm_registry)
        assert await planner._llm_plan("task") is None

    async def test_duplicate_target_rejects_plan(self, swarm_registry):
        runtime = FakeRuntime(
            _llm_payload(
                [
                    {"agent_id": "engineer", "purpose": "a", "target_file": "docs/same.md"},
                    {"agent_id": "documentation", "purpose": "b", "target_file": "docs/same.md"},
                    {"agent_id": "researcher", "purpose": "c"},
                ]
            )
        )
        planner = SwarmPlanner(runtime=runtime, registry=swarm_registry)
        assert await planner._llm_plan("task") is None

    async def test_workers_capped_at_max(self, swarm_registry):
        workers = [
            {"agent_id": "researcher", "purpose": f"w{i}"}
            for i in range(MAX_SWARM_WORKERS + 3)
        ]
        runtime = FakeRuntime(_llm_payload(workers))
        planner = SwarmPlanner(runtime=runtime, registry=swarm_registry)
        plan = await planner._llm_plan("task")
        assert plan is not None
        assert len(plan.workers) == MAX_SWARM_WORKERS

    async def test_non_json_declines_then_heuristic(self, swarm_registry):
        """LLM returns garbage; heuristic then decides (no paths here → None)."""
        runtime = FakeRuntime("not json at all")
        planner = SwarmPlanner(runtime=runtime, registry=swarm_registry)
        assert await planner.plan("analyze the architecture") is None


class TestSwarmPlannerHeuristic:
    async def test_three_distinct_files_becomes_swarm(self, swarm_registry):
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan(
            "Propagate the prototype findings into docs/design-decisions.md, "
            "docs/repo-impact-map.md, and docs/synthesis.md"
        )
        assert plan is not None
        assert [w.target_file for w in plan.workers] == [
            "docs/design-decisions.md",
            "docs/repo-impact-map.md",
            "docs/synthesis.md",
        ]
        # Non-existent targets go to the first mutating agent (engineer).
        assert all(w.agent_id == "engineer" for w in plan.workers)

    async def test_existing_files_go_to_documentation(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        for name in ("a.md", "b.md", "c.md"):
            (tmp_path / name).write_text("# doc\n")
        monkeypatch.chdir(tmp_path)
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan("Update a.md, b.md, and c.md with findings")
        assert plan is not None
        assert all(w.agent_id == "documentation" for w in plan.workers)

    async def test_two_files_not_a_swarm(self, swarm_registry):
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan("update docs/a.md and docs/b.md")
        assert plan is None

    async def test_no_files_not_a_swarm(self, swarm_registry):
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        assert await planner.plan("summarize the milestone status") is None

    async def test_folder_expansion_fans_out_over_markdown(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        """A named directory expands to one worker per markdown artifact,
        so 'update whatever needs updating in docs/' swarms without an
        explicit file list."""
        docs = tmp_path / "docs"
        (docs / "intake").mkdir(parents=True)
        (docs / "synthesis.md").write_text("# synthesis\n")
        (docs / "design-decisions.md").write_text("# decisions\n")
        (docs / "intake" / "reuse.md").write_text("# reuse\n")
        monkeypatch.chdir(tmp_path)

        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan(
            f"inspect the files in {docs} and update whatever needs updating"
        )
        assert plan is not None
        assert sorted(w.target_file for w in plan.workers) == [
            f"{docs}/design-decisions.md",
            f"{docs}/intake/reuse.md",
            f"{docs}/synthesis.md",
        ]

    async def test_folder_and_subfolder_dedup(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        """Naming both a folder and its subfolder must not duplicate workers."""
        docs = tmp_path / "docs"
        (docs / "intake").mkdir(parents=True)
        (docs / "a.md").write_text("# a\n")
        (docs / "intake" / "b.md").write_text("# b\n")
        (docs / "intake" / "c.md").write_text("# c\n")
        monkeypatch.chdir(tmp_path)

        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan(
            f"update the files in {docs} and {docs}/intake with the latest"
        )
        assert plan is not None
        assert len(plan.workers) == 3
        assert len({w.target_file for w in plan.workers}) == 3

    async def test_nonexistent_folder_is_not_a_swarm(self, swarm_registry):
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        assert await planner.plan("update the files in docs/missing-folder") is None


class TestSwarmExecutor:
    def _plan(self, workers, **kwargs):
        return SwarmPlan(
            workers=workers,
            original_prompt="propagate findings into analysis docs",
            **kwargs,
        )

    async def test_all_workers_run_and_synthesize(self, swarm_registry):
        plan = self._plan(
            [
                SwarmWorker("researcher", "check assumptions"),
                SwarmWorker("engineer", "assess code impact"),
                SwarmWorker("documentation", "update synthesis"),
            ]
        )
        executor = SwarmExecutor(registry=swarm_registry, critic_gate=False)

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            return_value=EchoRuntime(marker="swarm"),
        ):
            result = await executor.execute(
                plan, original_prompt="propagate findings into analysis docs"
            )

        assert result.is_error is False
        assert "# Swarm result (3 workers)" in result.text
        assert "## Researcher (research)" in result.text
        assert "## Engineer (engineer)" in result.text
        assert "## Documentation Writer (documentation)" in result.text
        assert result.metadata["swarm"] is True
        assert len(result.metadata["workers"]) == 3
        assert all(w["runtime"] == "echo" for w in result.metadata["workers"])
        # Metrics aggregate across workers.
        assert result.tokens_used == 300

    async def test_worker_failure_isolated(self, swarm_registry):
        plan = self._plan(
            [
                SwarmWorker("researcher", "check assumptions"),
                SwarmWorker("engineer", "assess code impact"),
                SwarmWorker("documentation", "update synthesis"),
            ]
        )
        executor = SwarmExecutor(registry=swarm_registry, critic_gate=False)

        calls = {"n": 0}

        def _create_runtime(name, config=None):
            runtime = EchoRuntime(marker=f"rt{calls['n']}")
            calls["n"] += 1
            return runtime

        # Make the second created runtime fail.
        real_run = EchoRuntime.run

        async def _maybe_fail(self, prompt, config=None):
            if "assess code impact" in prompt:
                return AgentResult(text="boom", is_error=True)
            return await real_run(self, prompt, config)

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            side_effect=_create_runtime,
        ), patch.object(EchoRuntime, "run", _maybe_fail):
            result = await executor.execute(
                plan, original_prompt="propagate findings into analysis docs"
            )

        assert result.is_error is False  # one failure doesn't fail the swarm
        by_agent = {w["agent_id"]: w for w in result.metadata["workers"]}
        assert by_agent["engineer"]["is_error"] is True
        assert by_agent["researcher"]["is_error"] is False
        assert by_agent["documentation"]["is_error"] is False
        assert "## Researcher (research)" in result.text

    async def test_leader_failure_workers_still_run(self, swarm_registry):
        plan = self._plan(
            [
                SwarmWorker("researcher", "check assumptions"),
                SwarmWorker("engineer", "assess impact"),
                SwarmWorker("documentation", "update synthesis"),
            ],
            use_leader=True,
        )
        executor = SwarmExecutor(registry=swarm_registry, critic_gate=False)

        calls = {"n": 0}

        async def _leader_fails(self, prompt, config=None):
            calls["n"] += 1
            if "Condense the task/evidence" in prompt:
                return AgentResult(text="leader boom", is_error=True)
            return AgentResult(text="worker ok")

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            return_value=EchoRuntime(marker="swarm"),
        ), patch.object(EchoRuntime, "run", _leader_fails):
            result = await executor.execute(
                plan, original_prompt="propagate findings into analysis docs"
            )

        assert result.is_error is False
        assert len(result.metadata["workers"]) == 3

    async def test_format_plan_lists_workers(self, swarm_registry):
        plan = self._plan(
            [
                SwarmWorker("researcher", "analyze", target_file="docs/a.md"),
                SwarmWorker("engineer", "assess", target_file="docs/b.md"),
                SwarmWorker("documentation", "write", target_file="docs/c.md"),
            ],
            use_leader=True,
        )
        text = SwarmPlanner.format_plan(plan)
        assert "swarm plan" in text.lower()
        assert "docs/a.md" in text
        assert "Leader digest: yes" in text


class TestProjectManagerSwarmIntegration:
    async def test_handle_swarm_dry_run(self, swarm_registry):
        from open_maestro.orchestrator.pm import ProjectManager

        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=swarm_registry)
        result = await pm.handle(
            "Propagate the findings into docs/design-decisions.md, "
            "docs/repo-impact-map.md, and docs/synthesis.md",
            agent_id="documentation",
            chain=True,
            dry_run=True,
        )
        assert result.is_error is False
        assert result.metadata.get("swarm") is True
        assert result.metadata.get("dry_run") is True
        assert "swarm plan" in result.text.lower()
        assert "docs/design-decisions.md" in result.text

    async def test_handle_swarm_disabled_falls_back_to_chain(self, swarm_registry):
        from open_maestro.orchestrator.pm import ProjectManager

        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=swarm_registry)
        result = await pm.handle(
            "Propagate the findings into docs/design-decisions.md, "
            "docs/repo-impact-map.md, and docs/synthesis.md",
            agent_id="documentation",
            chain=True,
            swarm=False,
            dry_run=True,
        )
        assert result.metadata.get("swarm") is not True
        assert result.metadata.get("chain") is True
