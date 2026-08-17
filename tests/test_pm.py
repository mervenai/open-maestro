"""Tests for the ProjectManager orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.config.capabilities import (
    CodingStrength,
    ReasoningLevel,
    RequiredCapabilities,
    TaskProfile,
)
from open_maestro.milestones import MilestoneStatus
from open_maestro.orchestrator.pm import ProjectManager
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime
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
        assert "kimi-code/kimi-for-coding" in result.text
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
