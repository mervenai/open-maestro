"""Tests for the ProjectManager orchestrator."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.config.capabilities import (
    CodingStrength,
    ReasoningLevel,
    RequiredCapabilities,
    TaskProfile,
)
from open_maestro.milestones import MilestoneStatus
from open_maestro.orchestrator import critic as critic_mod
from open_maestro.orchestrator.critic import (
    extract_findings,
    parse_diff_stat,
    parse_verdict,
    should_trigger,
)
from open_maestro.orchestrator.pm import ProjectManager
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime
from open_maestro.security import clones as clone_guard_mod
from open_maestro.security.policy import _MUTATING_TOOLS
from open_maestro.session.store import SessionRecord, SessionStore


class FakeRuntime(AgentRuntime):
    """Runtime that records the config it was called with."""

    def __init__(self):
        self.last_config: AgentConfig | None = None
        self.last_prompt: str | None = None
        self.last_tool_guard = None
        self.last_blocked_tools: set[str] | None = None
        self.last_session_id: str | None = None
        self.last_method: str = "run"
        self.ran_with_hooks = False
        self.calls: list[tuple[str, str | None]] = []

    @property
    def runtime_name(self) -> str:
        return "fake"

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        self.last_method = "run"
        self.last_prompt = prompt
        self.last_config = config
        self.calls.append(("run", config.model if config else None))
        return AgentResult(text="ok", session_id="new_session", metadata={})

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard=None,
        blocked_tools=None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        self.ran_with_hooks = True
        self.last_tool_guard = tool_guard
        self.last_blocked_tools = blocked_tools
        return await self.run(prompt, config)

    async def resume(
        self, session_id: str, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        self.last_method = "resume"
        self.last_session_id = session_id
        self.last_config = config
        self.calls.append(("resume", config.model if config else None))
        return AgentResult(text="ok", session_id=session_id, metadata={})

    async def fork(
        self, session_id: str, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        self.last_method = "fork"
        self.last_session_id = session_id
        self.last_config = config
        self.calls.append(("fork", config.model if config else None))
        return AgentResult(text="ok", session_id=session_id, metadata={})


class KimiFakeRuntime(FakeRuntime):
    """Fake runtime that advertises the Kimi CLI runtime name."""

    @property
    def runtime_name(self) -> str:
        return "kimi-cli"


class TestProjectManagerCapabilities:
    async def test_agent_requirements_merge_into_profile(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            model="smart",
            required_capabilities=RequiredCapabilities(
                reasoning=ReasoningLevel.DEEP,
                coding_strength=CodingStrength.HIGH,
            ),
        )
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        # Prompt alone would produce a light-reasoning profile.
        result = await pm.handle("write a parser", agent_id="engineer")

        assert result.is_error is False
        assert runtime.last_config is not None
        assert runtime.last_config.task_profile is not None
        assert runtime.last_config.task_profile.reasoning_depth == ReasoningLevel.DEEP
        assert runtime.last_config.task_profile.coding_strength == CodingStrength.HIGH

    async def test_context_tokens_estimated_from_agent_requirement(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="architect",
            name="Architect",
            role="architect",
            required_capabilities=RequiredCapabilities(
                max_context_tokens=200000,
            ),
        )
        registry = AgentRegistry({"architect": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        await pm.handle("analyze", agent_id="architect")

        assert runtime.last_config is not None
        assert runtime.last_config.task_profile is not None
        assert runtime.last_config.task_profile.context_tokens_estimate == 200000


class TestProjectManagerModelSelection:
    async def test_task_profile_overrides_agent_default_model_alias(self):
        runtime = KimiFakeRuntime()
        agent = AgentDefinition(
            id="thinker",
            name="Thinker",
            role="architect",
            model="fast",
        )
        registry = AgentRegistry({"thinker": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle(
            "design the architecture",
            agent_id="thinker",
            task_profile=TaskProfile(reasoning_depth=ReasoningLevel.DEEP),
        )

        assert result.is_error is False
        assert runtime.last_config is not None
        assert runtime.last_config.model == "kimi-code/k3"

    async def test_explicit_model_parameter_wins_over_profile(self):
        runtime = KimiFakeRuntime()
        agent = AgentDefinition(
            id="thinker",
            name="Thinker",
            role="architect",
            model="fast",
        )
        registry = AgentRegistry({"thinker": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle(
            "design the architecture",
            agent_id="thinker",
            model="kimi-code/kimi-for-coding",
        )

        assert result.is_error is False
        assert runtime.last_config is not None
        assert runtime.last_config.model == "kimi-code/kimi-for-coding"


class TestProjectManagerDryRun:
    async def test_dry_run_returns_plan_without_invoking_runtime(self):
        runtime = KimiFakeRuntime()
        agent = AgentDefinition(
            id="coder",
            name="Coder",
            role="engineer",
            model="default",
        )
        registry = AgentRegistry({"coder": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle(
            "write a parser",
            agent_id="coder",
            dry_run=True,
        )

        assert runtime.last_config is None
        assert result.is_error is False
        assert "coder" in result.text
        # The concrete model is profile-selected from the (user-editable)
        # capability registry, so don't hardcode a name — just require that
        # the alias resolved to something concrete and it is shown in the plan.
        resolved = result.metadata.get("resolved_model")
        assert resolved and resolved != "default"
        assert resolved in result.text
        assert result.metadata.get("dry_run") is True
        assert result.metadata.get("selected_agent") == "coder"


class TestProjectManagerGuardrails:
    async def test_blocked_tools_trigger_run_with_hooks(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            blocked_tools=["Bash"],
        )
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle("refactor parser", agent_id="engineer")

        assert result.is_error is False
        assert runtime.ran_with_hooks is True
        assert runtime.last_blocked_tools == {"Bash"}
        assert runtime.last_config is not None
        assert runtime.last_config.blocked_tools == {"Bash"}
        assert "forbidden" in (runtime.last_config.system_prompt or "").lower()

    async def test_tool_guard_blocks_blocked_tool(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            blocked_tools=["Write"],
        )
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        await pm.handle("refactor parser", agent_id="engineer")

        assert runtime.last_tool_guard is not None
        allowed = await runtime.last_tool_guard("Read", {"path": "x"})
        assert allowed is True
        allowed = await runtime.last_tool_guard("Write", {"path": "x"})
        assert allowed is False

    async def test_deny_dangerous_blocks_destructive_bash(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
        )
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        await pm.handle(
            "refactor parser",
            agent_id="engineer",
            deny_dangerous=True,
        )

        assert runtime.ran_with_hooks is True
        allowed = await runtime.last_tool_guard("Bash", {"command": "echo hi"})
        assert allowed is True
        allowed = await runtime.last_tool_guard("Bash", {"command": "rm -rf /"})
        assert allowed is False

    async def test_read_only_role_blocks_mutating_tools(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="qa",
            name="QA",
            role="qa",
        )
        registry = AgentRegistry({"qa": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        await pm.handle("review the parser", agent_id="qa")

        assert runtime.ran_with_hooks is True
        allowed = await runtime.last_tool_guard("Edit", {"path": "x"})
        assert allowed is False
        allowed = await runtime.last_tool_guard("Read", {"path": "x"})
        assert allowed is True

    async def test_readonly_mcp_tools_admitted_to_allowlisted_agent(self):
        """Auditor seats (agent-declared tool allowlist) gain the vetted
        read-only vector-search tools — and only those — when an MCP config
        is loaded. Regression: the code-critic reported the protocol-referenced
        MCP tools as 'not exposed in this runtime' and fell back to grep."""
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="code-critic",
            name="Code Critic",
            role="qa",
            tools=["Read", "Grep", "Bash"],
            blocked_tools=["Write", "Edit"],
        )
        registry = AgentRegistry({"code-critic": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)
        mcp_servers = {"mcpServers": {"mcp-vector-search": {"command": "mvs"}}}

        await pm.handle(
            "review the parser",
            agent_id="code-critic",
            mcp_servers=mcp_servers,
        )

        config = runtime.last_config
        assert config is not None
        allowed = config.allowed_tools or []
        assert "mcp__mcp-vector-search__search_code" in allowed
        assert "mcp__mcp-vector-search__kg_query" in allowed
        # Mutating/agentic MCP tools stay out of a read-only seat.
        assert "mcp__mcp-vector-search__index_project" not in allowed
        assert "mcp__mcp-vector-search__save_report" not in allowed
        # Interception admits vetted tools, rejects the rest.
        assert runtime.last_tool_guard is not None
        ok = await runtime.last_tool_guard(
            "mcp__mcp-vector-search__search_code", {"query": "x"}
        )
        assert ok is True
        ok = await runtime.last_tool_guard(
            "mcp__mcp-vector-search__save_report", {"content": "x"}
        )
        assert ok is False

    async def test_no_mcp_config_leaves_allowlist_unchanged(self):
        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="code-critic",
            name="Code Critic",
            role="qa",
            tools=["Read", "Grep", "Bash"],
        )
        registry = AgentRegistry({"code-critic": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        await pm.handle("review the parser", agent_id="code-critic")

        config = runtime.last_config
        assert config is not None
        assert config.allowed_tools == ["Bash", "Grep", "Read"]


class TestProjectManagerSession:
    async def test_resume_calls_runtime_resume(self, tmp_path):
        from open_maestro.session.store import SessionStore

        store = SessionStore(base_dirs=[tmp_path / "sessions"])
        store.save(
            SessionRecord(
                session_id="sess_abc",
                runtime_name="fake",
                agent_id="engineer",
                model="smart",
                prompt_summary="first task",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

        runtime = FakeRuntime()
        agent = AgentDefinition(id="engineer", name="Engineer", role="engineer")
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry, session_store=store)

        result = await pm.handle(
            "continue refactoring",
            agent_id="engineer",
            session_id="sess_abc",
            resume=True,
        )

        assert result.is_error is False
        assert runtime.last_method == "resume"
        assert runtime.last_session_id == "sess_abc"

    async def test_fork_calls_runtime_fork(self, tmp_path):
        from open_maestro.session.store import SessionStore

        store = SessionStore(base_dirs=[tmp_path / "sessions"])
        store.save(
            SessionRecord(
                session_id="sess_abc",
                runtime_name="fake",
                agent_id="engineer",
                model="smart",
                prompt_summary="first task",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

        runtime = FakeRuntime()
        agent = AgentDefinition(id="engineer", name="Engineer", role="engineer")
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry, session_store=store)

        result = await pm.handle(
            "explore alternative",
            agent_id="engineer",
            session_id="sess_abc",
            fork=True,
        )

        assert result.is_error is False
        assert runtime.last_method == "fork"
        assert runtime.last_session_id == "sess_abc"

    async def test_session_record_saved_after_run(self, tmp_path):
        from open_maestro.session.store import SessionStore

        store = SessionStore(base_dirs=[tmp_path / "sessions"])
        runtime = FakeRuntime()
        agent = AgentDefinition(id="engineer", name="Engineer", role="engineer")
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry, session_store=store)

        await pm.handle("refactor parser", agent_id="engineer")

        sessions = store.list_recent()
        assert len(sessions) == 1
        assert sessions[0].agent_id == "engineer"
        assert sessions[0].runtime_name == "fake"
        assert sessions[0].prompt_summary == "refactor parser"


class TestProjectManagerEvents:
    async def test_emits_lifecycle_events(self, tmp_path):
        from open_maestro.events.bus import EventBus

        bus = EventBus()
        bus._handlers.clear()
        EventBus._instance = None

        received: list[tuple[str, dict]] = []

        async def handler(event_type: str, payload: dict) -> None:
            received.append((event_type, payload))

        bus.on("*", handler)

        store = SessionStore(base_dirs=[tmp_path / "sessions"])
        runtime = FakeRuntime()
        agent = AgentDefinition(id="engineer", name="Engineer", role="engineer")
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(
            runtime=runtime,
            registry=registry,
            session_store=store,
            event_bus=bus,
        )

        await pm.handle("refactor parser", agent_id="engineer")

        event_types = [e[0] for e in received]
        assert "task.received" in event_types
        assert "agent.selected" in event_types
        assert "runtime.started" in event_types
        assert "runtime.completed" in event_types
        assert "session.saved" in event_types

        agent_events = [e for e in received if e[0] == "agent.selected"]
        assert agent_events[0][1]["agent_id"] == "engineer"


class TestProjectManagerHandoff:
    async def test_write_task_with_pinned_read_only_agent_triggers_handoff(self):
        runtime = FakeRuntime()
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            tools=["Read", "Grep"],
            blocked_tools=["Write", "Edit"],
        )
        engineer = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            tools=["Read", "Edit", "Write", "Bash"],
        )
        registry = AgentRegistry({"researcher": researcher, "engineer": engineer})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle(
            "write a PRD and codebase analysis to analysis.md",
            agent_id="researcher",
        )

        assert result.is_error is False
        assert len(runtime.calls) == 2
        assert result.metadata.get("handoff_from") == "researcher"
        assert "handoff_analysis" in result.metadata

    async def test_write_task_routes_to_mutating_agent_without_handoff(self):
        runtime = FakeRuntime()
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            tools=["Read", "Grep"],
            blocked_tools=["Write", "Edit"],
        )
        engineer = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            tools=["Read", "Edit", "Write", "Bash"],
        )
        registry = AgentRegistry({"researcher": researcher, "engineer": engineer})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle(
            "write a PRD and codebase analysis to analysis.md",
        )

        assert result.is_error is False
        assert len(runtime.calls) == 1
        assert result.metadata.get("selected_agent") == "engineer"

    async def test_read_only_task_does_not_handoff(self):
        runtime = FakeRuntime()
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            tools=["Read", "Grep"],
            blocked_tools=["Write", "Edit"],
        )
        engineer = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            tools=["Read", "Edit", "Write", "Bash"],
        )
        registry = AgentRegistry({"researcher": researcher, "engineer": engineer})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle("analyze the codebase", agent_id="researcher")

        assert result.is_error is False
        assert len(runtime.calls) == 1
        assert result.metadata.get("handoff_from") is None

    async def test_writer_selection_matches_task_not_load_order(self, monkeypatch):
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: None)
        monkeypatch.setattr(critic_mod, "detect_source_changes", lambda p, ref: [])
        runtime = FakeRuntime()
        vb_engineer = AgentDefinition(
            id="visual-basic-engineer",
            name="Visual Basic Engineer",
            role="engineer",
            instructions="Expert in Visual Basic 6 and VBA macros",
            tools=["Read", "Edit", "Write", "Bash"],
        )
        ts_engineer = AgentDefinition(
            id="typescript-engineer",
            name="TypeScript Engineer",
            role="engineer",
            instructions="Expert in TypeScript and React",
            tools=["Read", "Edit", "Write", "Bash"],
        )
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            tools=["Read", "Grep"],
            blocked_tools=["Write", "Edit"],
        )
        # visual-basic-engineer first in load order — the old bug's setup.
        registry = AgentRegistry(
            {
                "researcher": researcher,
                "visual-basic-engineer": vb_engineer,
                "typescript-engineer": ts_engineer,
            }
        )
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle(
            "verify the react component architecture and write the "
            "execution plan to docs/plan.md",
            agent_id="researcher",
        )

        assert result.is_error is False
        # Researcher analyzes, then the writer must be the TypeScript engineer.
        assert result.metadata.get("handoff_from") == "researcher"
        assert result.metadata.get("selected_agent") == "typescript-engineer"


class TestProjectManagerMilestoneContext:
    async def test_prompt_includes_milestone_context(self, tmp_path, monkeypatch):
        from open_maestro.milestones import MilestoneStore

        monkeypatch.chdir(tmp_path)
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[2].status = MilestoneStatus.IN_PROGRESS
        store.update(plan)

        runtime = FakeRuntime()
        agent = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
        )
        registry = AgentRegistry({"engineer": agent})
        pm = ProjectManager(runtime=runtime, registry=registry)

        result = await pm.handle("write a spec", agent_id="engineer")

        assert result.is_error is False
        assert runtime.last_prompt is not None
        assert "Project milestone context" in runtime.last_prompt
        assert "Design Blueprint" in runtime.last_prompt


class CriticFakeRuntime(FakeRuntime):
    """Runtime that returns a structured critic verdict for review prompts."""

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        self.last_method = "run"
        self.last_prompt = prompt
        self.last_config = config
        self.calls.append(("run", config.model if config else None))
        if "Review the implementation that was just completed" in prompt:
            return AgentResult(
                text=(
                    "## Verdict: BLOCK\n\n## Findings\n\n"
                    "| Severity | File | Line | Issue | Fix |\n"
                    "|----------|------|------|-------|-----|\n"
                    "| CRITICAL | src/app.py | 42 | SQL injection | parameterize |\n"
                ),
                session_id="critic_session",
                metadata={},
            )
        return AgentResult(text="implemented", session_id="new_session", metadata={})


def _critic_registry(*, with_critic: bool = True) -> AgentRegistry:
    engineer = AgentDefinition(
        id="engineer",
        name="Engineer",
        role="engineer",
        tools=["Read", "Edit", "Write", "Bash"],
    )
    agents = {"engineer": engineer}
    if with_critic:
        agents["code-critic"] = AgentDefinition(
            id="code-critic",
            name="Code Critic",
            role="qa",
            tools=["Read", "Grep"],
            blocked_tools=["Write", "Edit", "MultiEdit", "ApplyPatch"],
        )
    return AgentRegistry(agents)


class TestCriticGate:
    def test_should_trigger_respects_thresholds(self):
        assert should_trigger([("src/app.py", 60)])  # >50 code lines
        assert should_trigger([("src/a.py", 10), ("src/b.py", 5)])  # >1 code file
        assert not should_trigger([("src/app.py", 50)])  # boundary: not >50
        assert not should_trigger([("src/app.py", 3)])  # trivial single-file fix
        assert not should_trigger([("docs/readme.md", 400)])  # docs only
        assert not should_trigger([("pyproject.toml", 200)])  # config only
        assert not should_trigger([])

    def test_parse_diff_stat_skips_summary_lines(self):
        stat = (
            " src/app.py  | 12 +++---\n"
            " docs/x.md   |  3 +\n"
            " 2 files changed, 15 insertions(+), 1 deletion(-)\n"
        )
        assert parse_diff_stat(stat) == [("src/app.py", 12), ("docs/x.md", 3)]

    def test_parse_verdict_and_extract_findings(self):
        assert parse_verdict("## Verdict: BLOCK\n...") == "BLOCK"
        assert parse_verdict("## verdict: approve") == "APPROVE"
        assert parse_verdict("no verdict here") is None
        findings = extract_findings(
            "## Findings\n\n| Severity | File |\n|---|---|\n| CRITICAL | a.py |\n"
        )
        assert any("CRITICAL" in row for row in findings)

    async def test_gate_dispatches_critic_after_mutating_turn(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "on")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        monkeypatch.setattr(
            critic_mod, "detect_source_changes", lambda p, ref: [("src/app.py", 60)]
        )
        # Isolate the source-critic gate from the artifact gate (9.6): stub
        # artifact detection to [] so the repo's own uncommitted docs/*.md
        # changes don't leak in and fire an extra artifact-critic pass.
        monkeypatch.setattr(critic_mod, "detect_artifact_changes", lambda p, ref: [])
        runtime = CriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle(
            "implement the budget import endpoint", agent_id="engineer"
        )

        assert result.is_error is False
        assert result.metadata.get("critic_verdict") == "BLOCK"
        assert "Code review (code-critic): BLOCK" in result.text
        assert "SQL injection" in result.text
        assert len(runtime.calls) == 2  # engineer + critic

    async def test_gate_skips_read_only_agent(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "on")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        monkeypatch.setattr(
            critic_mod, "detect_source_changes", lambda p, ref: [("src/app.py", 60)]
        )
        runtime = CriticFakeRuntime()
        researcher = AgentDefinition(
            id="researcher",
            name="Researcher",
            role="research",
            tools=["Read", "Grep"],
            blocked_tools=["Write", "Edit"],
        )
        registry = AgentRegistry({"researcher": researcher, **_critic_registry()._agents})
        pm = ProjectManager(runtime=runtime, registry=registry)

        await pm.handle("analyze the codebase structure", agent_id="researcher")

        assert len(runtime.calls) == 1  # no critic pass

    async def test_gate_recursion_guard_on_critic_turn(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "on")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        monkeypatch.setattr(
            critic_mod, "detect_source_changes", lambda p, ref: [("src/app.py", 60)]
        )
        runtime = CriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle("review this code", agent_id="code-critic")

        assert "critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1

    async def test_gate_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "off")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        monkeypatch.setattr(
            critic_mod, "detect_source_changes", lambda p, ref: [("src/app.py", 60)]
        )
        runtime = CriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle(
            "implement the budget import endpoint", agent_id="engineer"
        )

        assert "critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1

    async def test_gate_tolerates_missing_critic_agent(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "on")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        monkeypatch.setattr(
            critic_mod, "detect_source_changes", lambda p, ref: [("src/app.py", 60)]
        )
        runtime = CriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry(with_critic=False))

        result = await pm.handle(
            "implement the budget import endpoint", agent_id="engineer"
        )

        assert result.is_error is False
        assert "critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1

    async def test_gate_not_triggered_without_code_changes(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "on")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        monkeypatch.setattr(critic_mod, "detect_source_changes", lambda p, ref: [])
        # Isolate from the artifact gate (9.6): the repo's own uncommitted
        # docs/*.md changes would otherwise fire an extra artifact-critic pass.
        monkeypatch.setattr(critic_mod, "detect_artifact_changes", lambda p, ref: [])
        runtime = CriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle(
            "implement the budget import endpoint", agent_id="engineer"
        )

        assert "critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1


class ArtifactCriticFakeRuntime(FakeRuntime):
    """Runtime that returns a structured verdict for artifact-review prompts."""

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        self.last_method = "run"
        self.last_prompt = prompt
        self.last_config = config
        self.calls.append(("run", config.model if config else None))
        if "Review the design artifact" in prompt:
            return AgentResult(
                text=(
                    "## Verdict: WARN\n\n## Findings\n\n"
                    "| Severity | File | Line | Issue | Fix |\n"
                    "|----------|------|------|-------|-----|\n"
                    "| P1 | docs/api/contract.md | 12 | citation does not prove claim | re-verify |\n"
                ),
                session_id="critic_session",
                metadata={},
            )
        return AgentResult(text="implemented", session_id="new_session", metadata={})


class TestArtifactCriticGate:
    def _patch_detection(self, monkeypatch, artifacts):
        monkeypatch.setenv("MAESTRO_CRITIC_GATE", "on")
        monkeypatch.setattr(critic_mod, "snapshot_head", lambda p: "abc123")
        # No code changes: isolate the artifact gate from the code gate.
        monkeypatch.setattr(critic_mod, "detect_source_changes", lambda p, ref: [])
        monkeypatch.setattr(
            critic_mod, "detect_artifact_changes", lambda p, ref: list(artifacts)
        )

    async def test_artifact_gate_dispatches_critic(self, monkeypatch):
        self._patch_detection(monkeypatch, ["docs/api/contract.md"])
        runtime = ArtifactCriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle("draft the data contract", agent_id="engineer")

        assert result.is_error is False
        assert result.metadata.get("artifact_critic_verdict") == "WARN"
        assert result.metadata.get("artifact_critic_agent") == "code-critic"
        assert "Artifact review (code-critic): WARN" in result.text
        assert "citation does not prove claim" in result.text
        assert len(runtime.calls) == 2  # engineer + artifact critic
        assert "docs/api/contract.md" in runtime.last_prompt
        assert "draft the data contract" in runtime.last_prompt

    async def test_artifact_gate_skips_small_or_non_docs_artifacts(self, monkeypatch):
        # The detection function is the gate's filter; stub it to return
        # nothing as it would for <=50-line docs files or .md outside docs/.
        self._patch_detection(monkeypatch, [])
        runtime = ArtifactCriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle("tweak the readme", agent_id="engineer")

        assert "artifact_critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1

    async def test_artifact_gate_recursion_guard_on_critic_turn(self, monkeypatch):
        self._patch_detection(monkeypatch, ["docs/api/contract.md"])
        runtime = ArtifactCriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle("review this artifact", agent_id="code-critic")

        assert "artifact_critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1

    async def test_artifact_gate_disabled_by_env(self, monkeypatch):
        self._patch_detection(monkeypatch, ["docs/api/contract.md"])
        monkeypatch.setenv("MAESTRO_ARTIFACT_CRITIC", "off")
        runtime = ArtifactCriticFakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        result = await pm.handle("draft the data contract", agent_id="engineer")

        assert "artifact_critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1

    async def test_artifact_gate_tolerates_missing_critic_agent(self, monkeypatch):
        self._patch_detection(monkeypatch, ["docs/api/contract.md"])
        runtime = ArtifactCriticFakeRuntime()
        pm = ProjectManager(
            runtime=runtime, registry=_critic_registry(with_critic=False)
        )

        result = await pm.handle("draft the data contract", agent_id="engineer")

        assert result.is_error is False
        assert "artifact_critic_verdict" not in result.metadata
        assert len(runtime.calls) == 1


class TestArtifactChangeFilter:
    """Unit tests for the pure artifact filter in critic.py."""

    def test_filter_keeps_large_docs_markdown(self):
        assert critic_mod.filter_artifact_changes(
            [("docs/api/contract.md", 60)]
        ) == ["docs/api/contract.md"]
        assert critic_mod.filter_artifact_changes(
            [("docs/deep/nested/spec.md", 200), ("src/app.py", 80)]
        ) == ["docs/deep/nested/spec.md"]

    def test_filter_excludes_small_docs_and_non_docs_markdown(self):
        assert critic_mod.filter_artifact_changes(
            [("docs/api/contract.md", 50)]  # boundary: must EXCEED 50
        ) == []
        assert critic_mod.filter_artifact_changes(
            [("README.md", 400), ("notes/todo.md", 100)]
        ) == []

    def test_filter_honors_min_lines_env(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_ARTIFACT_MIN_LINES", "10")
        assert critic_mod.filter_artifact_changes(
            [("docs/api/contract.md", 12)]
        ) == ["docs/api/contract.md"]
        assert critic_mod.filter_artifact_changes([("docs/x.md", 5)]) == []

    def test_artifact_gate_enabled_env(self, monkeypatch):
        monkeypatch.delenv("MAESTRO_ARTIFACT_CRITIC", raising=False)
        assert critic_mod.artifact_gate_enabled()
        for off in ("off", "0", "false", "no"):
            monkeypatch.setenv("MAESTRO_ARTIFACT_CRITIC", off)
            assert not critic_mod.artifact_gate_enabled()


class TestSoleAgentDirective:
    """Guardrail: single-agent runs must not role-play a read-only worker."""

    def test_directive_appended(self):
        from open_maestro.orchestrator.pm import (
            _SOLE_AGENT_DIRECTIVE,
            _with_sole_agent_directive,
        )

        out = _with_sole_agent_directive("Draft the data contract.")
        assert out.startswith("Draft the data contract.")
        assert _SOLE_AGENT_DIRECTIVE in out
        assert "no\nlead agent" in out or "no lead agent" in out

    def test_directive_idempotent(self):
        from open_maestro.orchestrator.pm import _with_sole_agent_directive

        once = _with_sole_agent_directive("Do the thing.")
        assert _with_sole_agent_directive(once) == once

    def test_single_agent_run_sends_directive(self):
        """The pm single-agent path passes the prompt with the directive."""
        import asyncio

        from open_maestro.orchestrator.pm import _SOLE_AGENT_DIRECTIVE, ProjectManager

        agent = AgentDefinition(
            id="engineer", name="Engineer", role="engineer", instructions="Build things."
        )
        registry = AgentRegistry({"engineer": agent})
        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=registry)
        result = asyncio.run(pm.handle("summarize the repo", agent_id="engineer"))
        assert not result.is_error
        assert runtime.last_prompt is not None
        assert _SOLE_AGENT_DIRECTIVE in runtime.last_prompt


class MutatingRuntime(FakeRuntime):
    """Runtime that appends a line to a tracked clone file on each run."""

    def __init__(self, target: Path):
        super().__init__()
        self.target = target

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        with self.target.open("a", encoding="utf-8") as fh:
            fh.write("mutated by agent\n")
        return await super().run(prompt, config)


def _make_clone(project: Path, name: str = "M3Repo") -> Path:
    """Create a fake nested git clone with one committed tracked file."""
    clone = project / name
    clone.mkdir()
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
    tracked = clone / "appsettings.json"
    tracked.write_text('{"permissions": []}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "init",
        ],
        check=True,
    )
    return clone


class TestReadOnlyPrompts:
    """MSTRO-109: read_only=True forbids the policy's mutating tools."""

    async def test_read_only_merges_mutating_tools_into_blocked_tools(self):
        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        await pm.handle(
            "verify the drafted contract against the repos",
            agent_id="engineer",
            read_only=True,
        )

        assert runtime.last_blocked_tools is not None
        assert set(_MUTATING_TOOLS) <= set(runtime.last_blocked_tools)
        # The merged block set activates the guarded (hook-enforced) path.
        assert runtime.ran_with_hooks

    async def test_read_only_preserves_explicit_blocked_tools(self):
        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        await pm.handle(
            "verify the drafted contract",
            agent_id="engineer",
            read_only=True,
            blocked_tools=["SomeCustomTool"],
        )

        blocked = set(runtime.last_blocked_tools or ())
        assert "SomeCustomTool" in blocked
        assert "Write" in blocked

    async def test_default_turn_does_not_block_mutating_tools(self):
        runtime = FakeRuntime()
        pm = ProjectManager(runtime=runtime, registry=_critic_registry())

        await pm.handle("verify the drafted contract", agent_id="engineer")

        # The agent definition may block tools of its own; read_only=False
        # must not add the policy's mutating tools to that set.
        blocked = set(runtime.last_blocked_tools or ())
        assert not set(_MUTATING_TOOLS) <= blocked
        assert "Bash" not in blocked


class TestCloneGuard:
    """MSTRO-109: pre-implementation turns revert clone mutations."""

    async def test_pre_impl_milestone_restores_clone_modifications(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        clone = _make_clone(tmp_path)
        tracked = clone / "appsettings.json"
        monkeypatch.setattr(
            clone_guard_mod,
            "current_inprogress_milestone_ids",
            lambda p: ["design-blueprint"],
        )
        runtime = MutatingRuntime(tracked)
        pm = ProjectManager(
            runtime=runtime, registry=_critic_registry(), critic_gate=False
        )

        result = await pm.handle(
            "adversarially verify the drafted contract against the clone",
            agent_id="engineer",
        )

        assert tracked.read_text(encoding="utf-8") == '{"permissions": []}\n'
        assert result.is_error is False
        assert "Clone guard" in result.text
        assert "M3Repo" in result.text
        assert "reverted 1 file(s)" in result.text

    async def test_impl_milestone_leaves_clone_modifications(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        clone = _make_clone(tmp_path)
        tracked = clone / "appsettings.json"
        monkeypatch.setattr(
            clone_guard_mod,
            "current_inprogress_milestone_ids",
            lambda p: ["implementation"],
        )
        runtime = MutatingRuntime(tracked)
        pm = ProjectManager(
            runtime=runtime, registry=_critic_registry(), critic_gate=False
        )

        result = await pm.handle(
            "adversarially verify the drafted contract against the clone",
            agent_id="engineer",
        )

        assert "mutated by agent" in tracked.read_text(encoding="utf-8")
        assert "Clone guard" not in result.text

    async def test_no_milestone_plan_keeps_guard_active(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        clone = _make_clone(tmp_path)
        tracked = clone / "appsettings.json"
        monkeypatch.setattr(
            clone_guard_mod,
            "current_inprogress_milestone_ids",
            lambda p: [],
        )
        runtime = MutatingRuntime(tracked)
        pm = ProjectManager(
            runtime=runtime, registry=_critic_registry(), critic_gate=False
        )

        result = await pm.handle(
            "adversarially verify the drafted contract against the clone",
            agent_id="engineer",
        )

        assert tracked.read_text(encoding="utf-8") == '{"permissions": []}\n'
        assert "Clone guard" in result.text
