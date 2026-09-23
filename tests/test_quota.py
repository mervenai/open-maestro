"""Tests for quota-error classification, the session circuit breaker, and
the graceful-degradation retry loop in the PM orchestrator."""

from __future__ import annotations

from typing import Any

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.config.capabilities import (
    CapabilityRegistry,
    Capabilities,
    CodingStrength,
    CostLevel,
    LatencyHint,
    ModelCapability,
    ReasoningLevel,
    TaskProfile,
    Tier,
)
from open_maestro.events.bus import EventBus
from open_maestro.orchestrator import pm as pm_mod
from open_maestro.orchestrator.pm import ProjectManager
from open_maestro.runtime import quota as quota_mod
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime


@pytest.fixture(autouse=True)
def _clear_circuit():
    quota_mod.clear()
    yield
    quota_mod.clear()


class _ApiError(Exception):
    """Duck-typed stand-in for openai.APIStatusError subclasses."""

    def __init__(self, message: str, status_code: int | None = None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class TestClassifyQuotaError:
    def test_401_is_quota(self):
        assert "authentication" in (
            quota_mod.classify_quota_error(_ApiError("invalid key", 401)) or ""
        )

    def test_403_is_quota(self):
        assert "authentication" in (
            quota_mod.classify_quota_error(_ApiError("forbidden", 403)) or ""
        )

    def test_402_is_quota(self):
        assert "payment required" in (
            quota_mod.classify_quota_error(_ApiError("pay up", 402)) or ""
        )

    def test_429_with_balance_message_is_quota(self):
        exc = _ApiError(
            "Error code: 429",
            429,
            body={"error": {"message": "insufficient balance"}},
        )
        assert quota_mod.classify_quota_error(exc) == "balance or quota exhausted"

    def test_plain_429_rate_limit_is_not_quota(self):
        exc = _ApiError(
            "Error code: 429",
            429,
            body={"error": {"message": "max RPM exceeded, retry later"}},
        )
        assert quota_mod.classify_quota_error(exc) is None

    def test_transport_error_is_not_quota(self):
        # ReadError-style failures carry no status code; they keep the
        # existing stream-retry behavior.
        assert quota_mod.classify_quota_error(_ApiError("connection reset")) is None

    def test_context_overflow_is_not_quota(self):
        exc = _ApiError(
            "Error code: 400 - Prompt exceeds max length",
            400,
            body={"error": {"code": "1261", "message": "Prompt exceeds max length"}},
        )
        assert quota_mod.classify_quota_error(exc) is None


class TestCircuitBreaker:
    def test_mark_and_check(self):
        assert quota_mod.is_exhausted("glm-5-3-flash") is False
        quota_mod.mark_exhausted("glm-5-3-flash")
        assert quota_mod.is_exhausted("glm-5-3-flash") is True
        assert "glm-5-3-flash" in quota_mod.exhausted()

    def test_clear(self):
        quota_mod.mark_exhausted("glm-5-3-flash")
        quota_mod.clear()
        assert quota_mod.exhausted() == set()

    def test_none_key_is_ignored(self):
        quota_mod.mark_exhausted(None)
        assert quota_mod.exhausted() == set()


def _make_model(
    model_id: str,
    identifier: str,
    *,
    reasoning: ReasoningLevel = ReasoningLevel.LIGHT,
    cost: CostLevel = CostLevel.LOW,
    latency: LatencyHint = LatencyHint.LOW,
    relative_cost: float = 0.1,
) -> ModelCapability:
    return ModelCapability(
        id=model_id,
        name=model_id,
        provider="test",
        identifiers={"openai-sdk": identifier},
        capabilities=Capabilities(
            tier=Tier.FAST,
            reasoning=reasoning,
            coding_strength=CodingStrength.MEDIUM,
            latency_hint=latency,
            cost_level=cost,
            relative_cost=relative_cost,
        ),
    )


def _light_profile() -> TaskProfile:
    return TaskProfile(
        reasoning_depth=ReasoningLevel.LIGHT,
        coding_strength=CodingStrength.MEDIUM,
        latency_preference=LatencyHint.LOW,
        cost_preference=CostLevel.LOW,
    )


class TestRegistryExclusion:
    def _registry(self) -> CapabilityRegistry:
        return CapabilityRegistry(
            {
                "glm-5-3-flash": _make_model("glm-5-3-flash", "glm-5.3-flash"),
                "kimi-k3": _make_model(
                    "kimi-k3",
                    "kimi-k3",
                    reasoning=ReasoningLevel.DEEP,
                    cost=CostLevel.MEDIUM,
                    latency=LatencyHint.MEDIUM,
                    relative_cost=1.0,
                ),
            }
        )

    def test_cheapest_model_wins_without_exclude(self):
        registry = self._registry()
        best = registry.match("openai-sdk", _light_profile())
        assert best is not None
        assert best.id == "glm-5-3-flash"

    def test_exclude_by_model_id(self):
        registry = self._registry()
        best = registry.match(
            "openai-sdk", _light_profile(), exclude={"glm-5-3-flash"}
        )
        assert best is not None
        assert best.id == "kimi-k3"

    def test_exclude_by_runtime_identifier(self):
        # The circuit may hold the runtime identifier ("glm-5.3-flash")
        # rather than the canonical id; both spellings must exclude.
        registry = self._registry()
        best = registry.match(
            "openai-sdk", _light_profile(), exclude={"glm-5.3-flash"}
        )
        assert best is not None
        assert best.id == "kimi-k3"


class _ScriptedRuntime(AgentRuntime):
    """Runtime that returns queued results, recording calls."""

    def __init__(self, responses: list[AgentResult]):
        self._responses = list(responses)
        self.calls = 0
        self.last_config: AgentConfig | None = None

    @property
    def runtime_name(self) -> str:
        return "openai-sdk"

    async def run(
        self, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        self.calls += 1
        self.last_config = config
        return self._responses.pop(0)

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

    async def fork(
        self, session_id: str, prompt: str, config: AgentConfig | None = None
    ) -> AgentResult:
        return await self.run(prompt, config)


def _quota_error_result(model: str = "glm-5.3-flash") -> AgentResult:
    return AgentResult(
        text="OpenAI API error: insufficient balance",
        is_error=True,
        metadata={
            "quota_exhausted": "balance or quota exhausted",
            "quota_exhausted_model": model,
        },
    )


def _pm_with_runtime(runtime: AgentRuntime) -> ProjectManager:
    agent = AgentDefinition(id="engineer", name="Engineer", role="engineer")
    registry = AgentRegistry({"engineer": agent})
    return ProjectManager(runtime=runtime, registry=registry, critic_gate=False)


class TestPMQuotaFallback:
    async def test_quota_error_triggers_fallback_and_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        runtime = _ScriptedRuntime([_quota_error_result(), AgentResult(text="done")])
        pm = _pm_with_runtime(runtime)

        monkeypatch.setattr(
            pm_mod,
            "select_runtime_for_task",
            lambda profile, **kwargs: ("openai-sdk", "kimi-k3"),
        )

        events: list[dict[str, Any]] = []
        bus = EventBus()

        async def _collect(event_type: str, payload: dict[str, Any]) -> None:
            events.append(payload)

        bus.on("model.quota_exhausted", _collect)
        try:
            result = await pm.handle("do something", agent_id="engineer")
        finally:
            bus.off("model.quota_exhausted", _collect)

        assert result.is_error is False
        assert result.text.startswith("done")
        assert runtime.calls == 2
        # The retry ran with the fallback model.
        assert runtime.last_config is not None
        assert runtime.last_config.model == "kimi-k3"
        # The failed model is on the session circuit.
        assert quota_mod.is_exhausted("glm-5.3-flash")
        # The warning event fired with fallback details.
        assert len(events) == 1
        assert events[0]["model"] == "glm-5.3-flash"
        assert events[0]["fallback"] == "kimi-k3"

    async def test_non_quota_errors_do_not_retry(self):
        runtime = _ScriptedRuntime(
            [AgentResult(text="boom", is_error=True), AgentResult(text="ok")]
        )
        pm = _pm_with_runtime(runtime)

        result = await pm.handle("do something", agent_id="engineer")

        assert result.is_error is True
        assert runtime.calls == 1
        assert quota_mod.exhausted() == set()

    async def test_gives_up_after_max_fallbacks(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        responses = [_quota_error_result(f"dead-{i}") for i in range(5)]
        responses.append(AgentResult(text="ok"))
        runtime = _ScriptedRuntime(responses)
        pm = _pm_with_runtime(runtime)

        monkeypatch.setattr(
            pm_mod,
            "select_runtime_for_task",
            lambda profile, **kwargs: ("openai-sdk", "other-model"),
        )

        result = await pm.handle("do something", agent_id="engineer")

        # 1 initial attempt + 3 fallbacks; the 5th response is never reached.
        assert runtime.calls == 4
        assert result.is_error is True
        assert "quota exhausted" in result.text

    async def test_no_alternative_model_warns_and_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        runtime = _ScriptedRuntime([_quota_error_result()])
        pm = _pm_with_runtime(runtime)

        def _raise(profile, **kwargs):
            raise RuntimeError("No available runtime can satisfy the task profile.")

        monkeypatch.setattr(pm_mod, "select_runtime_for_task", _raise)

        result = await pm.handle("do something", agent_id="engineer")

        assert result.is_error is True
        assert "no alternative model available" in result.text
        assert runtime.calls == 1


class TestFallbackOrderConfig:
    def test_configured_chain_is_respected(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pm = _pm_with_runtime(_ScriptedRuntime([]))
        monkeypatch.setattr(
            quota_mod, "load_fallback_order", lambda: ["glm-5-3-flash", "kimi-k3"]
        )
        monkeypatch.setattr(pm_mod, "is_model_available", lambda runtime, model: True)

        picked = pm._select_fallback(
            TaskProfile(), {"glm-5-3-flash"}, False, "glm-5-3-flash"
        )

        assert picked is not None
        runtime_name, model_id = picked
        # The chain names the canonical id; selection returns the concrete
        # runtime identifier for that model.
        assert model_id == "kimi-code/k3"
        assert runtime_name == "kimi-cli"

    def test_unknown_failed_model_falls_back_to_automatic(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        pm = _pm_with_runtime(_ScriptedRuntime([]))
        monkeypatch.setattr(
            quota_mod, "load_fallback_order", lambda: ["some-other-model"]
        )
        seen: dict[str, Any] = {}

        def _auto(profile, **kwargs):
            seen.update(kwargs)
            return ("openai-sdk", "auto-model")

        monkeypatch.setattr(pm_mod, "select_runtime_for_task", _auto)

        picked = pm._select_fallback(
            TaskProfile(), {"glm-5-3-flash"}, False, "glm-5-3-flash"
        )

        assert picked == ("openai-sdk", "auto-model")
        assert seen.get("exclude") == {"glm-5-3-flash"}


class TestChainSwarmQuotaPropagation:
    """Quota tags must survive aggregation so pm.handle can fall back."""

    def _agent(self) -> AgentDefinition:
        return AgentDefinition(id="engineer", name="Engineer", role="engineer")

    def _registry(self) -> AgentRegistry:
        return AgentRegistry({"engineer": self._agent()})

    def test_chain_synthesize_propagates_quota(self):
        from open_maestro.orchestrator.chain import (
            ChainExecutor,
            HandoffPlan,
            HandoffStep,
            StepResult,
        )

        executor = ChainExecutor(registry=self._registry(), critic_gate=False)
        step = HandoffStep(agent_id="engineer", purpose="do it")
        plan = HandoffPlan(steps=[step], original_prompt="task")
        combined = executor._synthesize(
            plan,
            [
                StepResult(
                    step=step,
                    agent=self._agent(),
                    runtime_name="openai-sdk",
                    model="glm-5.3-flash",
                    result=_quota_error_result(),
                )
            ],
        )

        assert combined.is_error is True
        assert combined.metadata["quota_exhausted"] == "balance or quota exhausted"
        assert combined.metadata["quota_exhausted_model"] == "glm-5.3-flash"

    def test_swarm_synthesize_propagates_quota(self):
        from open_maestro.orchestrator.chain import HandoffStep, StepResult
        from open_maestro.orchestrator.swarm import (
            SwarmExecutor,
            SwarmPlan,
            SwarmWorker,
        )

        executor = SwarmExecutor(registry=self._registry(), critic_gate=False)
        worker = SwarmWorker(agent_id="engineer", purpose="do it")
        plan = SwarmPlan(workers=[worker], original_prompt="task")
        combined = executor._synthesize(
            plan,
            [
                StepResult(
                    step=HandoffStep(agent_id="engineer", purpose="do it"),
                    agent=self._agent(),
                    runtime_name="openai-sdk",
                    model="glm-5.3-flash",
                    result=_quota_error_result(),
                )
            ],
        )

        assert combined.is_error is True
        assert combined.metadata["quota_exhausted"] == "balance or quota exhausted"
        assert combined.metadata["quota_exhausted_model"] == "glm-5.3-flash"

    async def test_run_agent_marks_circuit_and_excludes_on_next_selection(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from open_maestro.orchestrator.chain import ChainExecutor
        from open_maestro.runtime import factory as factory_mod

        executor = ChainExecutor(registry=self._registry(), critic_gate=False)

        seen_excludes: list[set[str]] = []

        def _select(profile, **kwargs):
            seen_excludes.append(set(kwargs.get("exclude") or set()))
            return ("openai-sdk", "kimi-k3")

        monkeypatch.setattr(factory_mod, "select_runtime_for_task", _select)
        runtime = _ScriptedRuntime(
            [_quota_error_result(), AgentResult(text="ok")]
        )
        monkeypatch.setattr(
            factory_mod, "create_runtime", lambda name, config=None: runtime
        )

        await executor._run_agent(
            self._agent(), "prompt", idx=1, profile=TaskProfile()
        )
        assert quota_mod.is_exhausted("glm-5.3-flash")

        await executor._run_agent(
            self._agent(), "prompt", idx=2, profile=TaskProfile()
        )
        # The second selection excluded the model that just died.
        assert seen_excludes[1] == {"glm-5.3-flash"}
        assert runtime.calls == 2
