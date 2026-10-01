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
    _current_task_text,
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

    async def test_leading_slash_means_relative_folder(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        """Users write '/docs' to mean the docs folder in the project, not the
        filesystem root — the token must resolve relative to cwd."""
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in ("a.md", "b.md", "c.md"):
            (docs / name).write_text("# doc\n")
        monkeypatch.chdir(tmp_path)

        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan(
            "inspect if any of the files in the /docs folder need to be "
            "updated after the latest analysis, and update them"
        )
        assert plan is not None
        assert sorted(w.target_file for w in plan.workers) == [
            "docs/a.md",
            "docs/b.md",
            "docs/c.md",
        ]


class TestCurrentTaskText:
    """MSTRO-109 fix 1: heuristic extraction must see the current task only,
    not file-path tokens from an interactive transcript's history."""

    def test_prompt_without_marker_is_returned_unchanged(self):
        prompt = "update docs/a.md, docs/b.md, and docs/c.md"
        assert _current_task_text(prompt) == prompt

    def test_transcript_prefix_is_stripped(self):
        prompt = (
            "Conversation so far:\n"
            "User: fix DefaultRole_SystemAdministrator.js\n"
            "Assistant: done\n\n"
            "Current task: summarize the milestone status"
        )
        assert _current_task_text(prompt) == "summarize the milestone status"

    def test_history_paths_yield_no_targets(self):
        prompt = (
            "Conversation so far:\n"
            "User: rewrite DefaultRole_SystemAdministrator.js and docs/history.md\n"
            "Assistant: ok\n\n"
            "Current task: summarize the milestone status"
        )
        planner = SwarmPlanner.__new__(SwarmPlanner)
        assert planner._extract_targets(_current_task_text(prompt)) == []

    def test_current_task_paths_are_extracted(self, tmp_path, monkeypatch):
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in ("a.md", "b.md", "c.md"):
            (docs / name).write_text("# doc\n")
        monkeypatch.chdir(tmp_path)
        prompt = (
            "Conversation so far:\n"
            "User: fix DefaultRole_SystemAdministrator.js\n"
            "Assistant: done\n\n"
            "Current task: update docs/a.md, docs/b.md, and docs/c.md"
        )
        planner = SwarmPlanner.__new__(SwarmPlanner)
        assert planner._extract_targets(_current_task_text(prompt)) == [
            "docs/a.md",
            "docs/b.md",
            "docs/c.md",
        ]


class TestHeuristicCurrentTaskOnly:
    """Assembled-transcript regression: history must not feed the heuristic."""

    async def test_history_only_paths_do_not_swarm(self, swarm_registry):
        """Before the fix, the two history paths + the .js hallucination would
        fan out; with current-task-only extraction there are no targets."""
        prompt = (
            "Conversation so far:\n"
            "User: please update docs/impact.md and docs/map.md and fix "
            "DefaultRole_SystemAdministrator.js\n"
            "Assistant: I updated DefaultRole_SystemAdministrator.js\n\n"
            "Current task: give me a quick summary of where we are"
        )
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        assert await planner.plan(prompt) is None

    async def test_doc_only_current_task_swarms_without_history_target(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in ("a.md", "b.md", "c.md"):
            (docs / name).write_text("# doc\n")
        monkeypatch.chdir(tmp_path)
        prompt = (
            "Conversation so far:\n"
            "User: fix DefaultRole_SystemAdministrator.js\n"
            "Assistant: done\n\n"
            "Current task: update docs/a.md, docs/b.md, and docs/c.md"
        )
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan(prompt)
        assert plan is not None
        targets = [w.target_file for w in plan.workers]
        assert sorted(targets) == ["docs/a.md", "docs/b.md", "docs/c.md"]
        assert not any("DefaultRole_SystemAdministrator" in t for t in targets)


class TestWriteTargetValidation:
    """MSTRO-109 fix 2: _is_allowed_write_target gates heuristic targets."""

    def test_existing_file_inside_nested_git_repo_rejected(
        self, tmp_path, monkeypatch
    ):
        clone = tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)
        (clone / "src").mkdir()
        (clone / "src" / "app.js").write_text("// x\n")
        monkeypatch.chdir(tmp_path)
        assert (
            SwarmPlanner._is_allowed_write_target("clone/src/app.js") is False
        )

    def test_nonexistent_path_inside_nested_git_repo_rejected(
        self, tmp_path, monkeypatch
    ):
        clone = tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert SwarmPlanner._is_allowed_write_target("clone/new.md") is False

    def test_nonexistent_under_docs_allowed(self, tmp_path, monkeypatch):
        (tmp_path / "docs").mkdir()
        monkeypatch.chdir(tmp_path)
        assert SwarmPlanner._is_allowed_write_target("docs/new.md") is True

    def test_nonexistent_at_repo_root_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert SwarmPlanner._is_allowed_write_target("new.md") is False

    def test_existing_file_at_repo_root_allowed(self, tmp_path, monkeypatch):
        (tmp_path / "a.md").write_text("# a\n")
        monkeypatch.chdir(tmp_path)
        assert SwarmPlanner._is_allowed_write_target("a.md") is True

    def test_project_root_git_dir_does_not_count(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        (tmp_path / "a.md").write_text("# a\n")
        monkeypatch.chdir(tmp_path)
        assert SwarmPlanner._is_allowed_write_target("a.md") is True


class TestHeuristicWriteTargetValidation:
    async def test_three_doc_targets_swarm(self, swarm_registry, tmp_path, monkeypatch):
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in ("a.md", "b.md", "c.md"):
            (docs / name).write_text("# doc\n")
        monkeypatch.chdir(tmp_path)
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan("update docs/a.md, docs/b.md, and docs/c.md")
        assert plan is not None
        assert len(plan.workers) == 3

    async def test_code_repo_target_dropped_below_three(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        """2 doc targets + 1 real code file inside a cloned repo: the code
        worker is dropped, leaving fewer than 3 → no swarm."""
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in ("a.md", "b.md"):
            (docs / name).write_text("# doc\n")
        clone = tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)
        (clone / "app.js").write_text("// x\n")
        monkeypatch.chdir(tmp_path)
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan("update docs/a.md, docs/b.md, and clone/app.js")
        assert plan is None

    async def test_folder_expansion_inside_code_clone_rejected(
        self, swarm_registry, tmp_path, monkeypatch
    ):
        """Folder expansion over a cloned repo's markdown must be validated
        the same way as explicit file targets."""
        clone = tmp_path / "clone"
        (clone / ".git").mkdir(parents=True)
        for name in ("a.md", "b.md", "c.md"):
            (clone / name).write_text("# doc\n")
        monkeypatch.chdir(tmp_path)
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        assert await planner.plan(f"update the markdown in {clone}") is None


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

def _resilience_plan(workers, **kwargs):
    return SwarmPlan(
        workers=workers,
        original_prompt="propagate findings into analysis docs",
        **kwargs,
    )


class TestSwarmResilience:
    async def test_failed_worker_reseated_once_and_recovers(self, swarm_registry):
        """MSTRO-125: a failed slice is re-run once and replaces the failure."""
        plan = _resilience_plan(
            [
                SwarmWorker("researcher", "check assumptions"),
                SwarmWorker("engineer", "assess code impact"),
                SwarmWorker("documentation", "update synthesis"),
            ]
        )
        executor = SwarmExecutor(registry=swarm_registry, critic_gate=False)

        calls = {"n": 0}
        state = {"failed": False}

        def _create_runtime(name, config=None):
            runtime = EchoRuntime(marker=f"rt{calls['n']}")
            calls["n"] += 1
            return runtime

        real_run = EchoRuntime.run

        async def _fail_once(self, prompt, config=None):
            if "assess code impact" in prompt and not state["failed"]:
                state["failed"] = True
                return AgentResult(text="boom", is_error=True)
            return await real_run(self, prompt, config)

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            side_effect=_create_runtime,
        ), patch.object(EchoRuntime, "run", _fail_once):
            result = await executor.execute(
                plan, original_prompt="propagate findings into analysis docs"
            )

        by_agent = {w["agent_id"]: w for w in result.metadata["workers"]}
        assert by_agent["engineer"]["is_error"] is False
        assert "swarm_degraded" not in result.metadata
        assert "SWARM DEGRADED" not in result.text

    async def test_degraded_signaling_when_reseat_also_fails(self, swarm_registry):
        """MSTRO-125: unrecoverable partial failure is loud, not silent."""
        plan = _resilience_plan(
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

        async def _always_fail(self, prompt, config=None):
            if "assess code impact" in prompt:
                return AgentResult(
                    text="OpenAI API error: 429 insufficient balance",
                    is_error=True,
                    metadata={
                        "quota_exhausted": "balance or quota exhausted",
                        "quota_exhausted_model": "glm-5.3-flash",
                    },
                )
            return AgentResult(text="worker ok")

        with patch(
            "open_maestro.runtime.factory.select_runtime_for_task",
            return_value=("echo", "model-x"),
        ), patch(
            "open_maestro.runtime.factory.create_runtime",
            side_effect=_create_runtime,
        ), patch.object(EchoRuntime, "run", _always_fail):
            result = await executor.execute(
                plan, original_prompt="propagate findings into analysis docs"
            )

        assert result.is_error is False  # partial: 2 of 3 succeeded
        assert result.metadata["swarm_degraded"] is True
        assert "SWARM DEGRADED" in result.text
        failed = result.metadata["swarm_failed_workers"]
        assert [w["agent_id"] for w in failed] == ["engineer"]
        # Quota signal must propagate even for partial failure.
        assert result.metadata["quota_exhausted_model"] == "glm-5.3-flash"

    def test_consistency_failure_flagged(self, swarm_registry):
        """MSTRO-125: a 'not consistent' consistency pass is surfaced."""
        from open_maestro.orchestrator.chain import HandoffStep, StepResult

        plan = _resilience_plan(
            [
                SwarmWorker("researcher", "a"),
                SwarmWorker("engineer", "b"),
            ]
        )
        executor = SwarmExecutor(registry=swarm_registry, critic_gate=False)
        results = []
        for agent_id, purpose in (("researcher", "a"), ("engineer", "b")):
            results.append(
                StepResult(
                    step=HandoffStep(agent_id=agent_id, purpose=purpose),
                    agent=swarm_registry.get(agent_id),
                    runtime_name="echo",
                    model="m",
                    result=AgentResult(text="ok"),
                )
            )

        result = executor._synthesize(
            plan,
            results,
            extras=["## Consistency\n**Summary:** Not consistent."],
        )
        assert result.metadata["swarm_consistency"] == "failed"
        assert "CONSISTENCY CHECK DID NOT PASS" in result.text


class TestExplicitOutputRespect:
    def test_detect_explicit_output_variants(self):
        from open_maestro.orchestrator.swarm import detect_explicit_output

        assert detect_explicit_output(
            "Draft the contract. Write the output to "
            "docs/blueprint-design-and-data-contract.md."
        ) == "docs/blueprint-design-and-data-contract.md"
        assert detect_explicit_output(
            "Save the summary as docs/intake/synthesis.md"
        ) == "docs/intake/synthesis.md"
        assert detect_explicit_output("Output file: reports/out.csv") == (
            "reports/out.csv"
        )
        # Not output designations:
        assert detect_explicit_output("update docs/a.md and docs/b.md") is None
        assert detect_explicit_output("cite docs/a.md for evidence") is None
        assert (
            detect_explicit_output("Write the output to https://example.com/x.md")
            is None
        )

    async def test_llm_plan_appends_merge_worker(self, swarm_registry, tmp_path, monkeypatch):
        """MSTRO-126: fragment layout is fine, but the explicit output file
        must still be produced — via an appended merge worker."""
        monkeypatch.chdir(tmp_path)
        payload = json.dumps(
            {
                "leader": False,
                "consistency": True,
                "workers": [
                    {"agent_id": "researcher", "purpose": "draft dtos",
                     "target_file": "docs/_contract/dtos.md"},
                    {"agent_id": "engineer", "purpose": "draft endpoints",
                     "target_file": "docs/_contract/endpoints.md"},
                    {"agent_id": "documentation", "purpose": "draft events",
                     "target_file": "docs/_contract/events.md"},
                ],
            }
        )
        planner = SwarmPlanner(runtime=FakeRuntime(payload), registry=swarm_registry)
        plan = await planner.plan(
            "Draft the data contract. Write the output to "
            "docs/blueprint-design-and-data-contract.md."
        )
        assert plan is not None
        merge = [
            w
            for w in plan.workers
            if w.target_file == "docs/blueprint-design-and-data-contract.md"
        ]
        assert len(merge) == 1
        assert "merge" in merge[0].purpose.lower()

    async def test_llm_plan_respects_direct_target(self, swarm_registry, tmp_path, monkeypatch):
        """A worker already targeting the explicit file: nothing appended."""
        monkeypatch.chdir(tmp_path)
        payload = json.dumps(
            {
                "workers": [
                    {"agent_id": "researcher", "purpose": "a"},
                    {"agent_id": "engineer", "purpose": "b"},
                    {"agent_id": "documentation", "purpose": "write final",
                     "target_file": "docs/blueprint-design-and-data-contract.md"},
                ],
            }
        )
        planner = SwarmPlanner(runtime=FakeRuntime(payload), registry=swarm_registry)
        plan = await planner.plan(
            "Draft the contract. Write the output to "
            "docs/blueprint-design-and-data-contract.md."
        )
        assert plan is not None
        assert len(plan.workers) == 3

    async def test_heuristic_plan_appends_merge_worker(self, swarm_registry, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        for name in ("a.md", "b.md", "c.md"):
            (docs / name).write_text("# doc\n")
        planner = SwarmPlanner(
            runtime=FakeRuntime("not json"), registry=swarm_registry
        )
        plan = await planner.plan(
            "Update docs/a.md, docs/b.md, and docs/c.md. "
            "Write the output to build/combined.md"
        )
        assert plan is not None
        targets = [w.target_file for w in plan.workers]
        assert "build/combined.md" in targets
