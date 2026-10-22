"""Tests for the LLM-based task router."""

from __future__ import annotations

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.registry import AgentRegistry
from open_maestro.config.capabilities import (
    CodingStrength,
    ReasoningLevel,
    RequiredCapabilities,
    TaskProfile,
)
from open_maestro.orchestrator.router import LLMTaskRouter
from open_maestro.runtime.base import AgentResult, AgentRuntime


class FakeRuntime(AgentRuntime):
    """Runtime that returns canned routing responses, one per call.

    Accepts a single response string or a list; the list is consumed in
    order (the last entry repeats once exhausted) so tests can script a
    first response plus a retry response.
    """

    def __init__(self, response_text: str | list[str], is_error: bool = False):
        if isinstance(response_text, list):
            self.responses = list(response_text)
        else:
            self.responses = [response_text]
        self.is_error = is_error
        self.last_prompt = ""
        self.calls = 0

    @property
    def runtime_name(self) -> str:
        return "fake"

    async def run(self, prompt: str, config=None):
        self.last_prompt = prompt
        text = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return AgentResult(text=text, is_error=self.is_error)

    async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
        return await self.run(prompt, config)

    async def resume(self, session_id, prompt, config=None):
        return await self.run(prompt, config)


@pytest.fixture
def sample_registry():
    return AgentRegistry(
        {
            "engineer": AgentDefinition(
                id="engineer",
                name="Engineer",
                role="engineer",
                instructions="Writes code, tests, and refactors.",
            ),
            "researcher": AgentDefinition(
                id="researcher",
                name="Researcher",
                role="research",
                instructions="Explains architecture and investigates code.",
            ),
        }
    )


class TestLLMTaskRouter:
    async def test_selects_agent_from_json_response(self, sample_registry):
        runtime = FakeRuntime(
            '{"agent_id": "researcher", '
            '"reason": "task asks for explanation", '
            '"confidence": "high"}'
        )
        router = LLMTaskRouter(runtime=runtime)
        agent = await router.select("explain how the budget import works", sample_registry)
        assert agent.id == "researcher"
        assert "explain how the budget import works" in runtime.last_prompt
        assert "engineer" in runtime.last_prompt
        assert "researcher" in runtime.last_prompt

    async def test_falls_back_to_keyword_on_invalid_json(self, sample_registry):
        runtime = FakeRuntime("not json")
        router = LLMTaskRouter(runtime=runtime)
        agent = await router.select("write tests for the parser", sample_registry)
        assert agent.id == "engineer"

    async def test_falls_back_to_keyword_on_error_response(self, sample_registry):
        runtime = FakeRuntime("error", is_error=True)
        router = LLMTaskRouter(runtime=runtime)
        agent = await router.select("write tests for the parser", sample_registry)
        assert agent.id == "engineer"

    async def test_falls_back_when_router_picks_unknown_id(self, sample_registry):
        runtime = FakeRuntime('{"agent_id": "missing"}')
        router = LLMTaskRouter(runtime=runtime)
        agent = await router.select("write tests for the parser", sample_registry)
        assert agent.id == "engineer"

    async def test_retries_once_with_constrained_prompt_on_unknown_id(
        self, sample_registry
    ):
        # First response invents a plausible-but-nonexistent id (the observed
        # real-world drift); the constrained retry must recover a valid pick.
        runtime = FakeRuntime(
            [
                '{"agent_id": "Plan", "reason": "planning task", "confidence": "high"}',
                '{"agent_id": "researcher", "reason": "investigation", "confidence": "high"}',
            ]
        )
        router = LLMTaskRouter(runtime=runtime)
        agent = await router.select("create a phased implementation plan", sample_registry)
        assert agent.id == "researcher"
        assert runtime.calls == 2
        assert "EXACTLY one of these ids" in runtime.last_prompt
        assert "engineer" in runtime.last_prompt
        assert "researcher" in runtime.last_prompt

    async def test_falls_back_when_retry_is_also_unknown(self, sample_registry):
        runtime = FakeRuntime(
            [
                '{"agent_id": "Plan", "reason": "planning", "confidence": "high"}',
                '{"agent_id": "Architect", "reason": "design", "confidence": "high"}',
            ]
        )
        router = LLMTaskRouter(runtime=runtime)
        agent = await router.select("write tests for the parser", sample_registry)
        assert agent.id == "engineer"
        assert runtime.calls == 2

    async def test_raises_when_no_fallback_and_unknown_id(self, sample_registry):
        runtime = FakeRuntime('{"agent_id": "missing"}')
        router = LLMTaskRouter(runtime=runtime, fallback_to_keyword=False)
        with pytest.raises(RuntimeError, match="unknown agent_id"):
            await router.select("write tests for the parser", sample_registry)

    async def test_raises_when_no_agents_available(self):
        runtime = FakeRuntime('{"agent_id": "engineer"}')
        router = LLMTaskRouter(runtime=runtime)
        with pytest.raises(RuntimeError, match="No agents available"):
            await router.select("task", AgentRegistry({}))


class TestLLMTaskRouterCapabilities:
    async def test_llm_prompt_includes_task_profile(self, sample_registry):
        runtime = FakeRuntime('{"agent_id": "engineer", "reason": "ok"}')
        router = LLMTaskRouter(runtime=runtime)
        profile = TaskProfile(
            needs_vision=True,
            reasoning_depth=ReasoningLevel.DEEP,
            coding_strength=CodingStrength.HIGH,
        )
        await router.select("design the architecture", sample_registry, task_profile=profile)
        assert "needs_vision: True" in runtime.last_prompt
        assert "reasoning_depth: deep" in runtime.last_prompt
        assert "coding_strength: high" in runtime.last_prompt

    async def test_llm_prompt_includes_agent_capabilities(self, sample_registry):
        runtime = FakeRuntime('{"agent_id": "engineer", "reason": "ok"}')
        router = LLMTaskRouter(runtime=runtime)
        engineer = sample_registry.get("engineer")
        engineer.required_capabilities = RequiredCapabilities(
            reasoning=ReasoningLevel.DEEP,
            coding_strength=CodingStrength.HIGH,
            vision=True,
        )
        await router.select("task", sample_registry)
        assert "reasoning=deep" in runtime.last_prompt
        assert "coding_strength=high" in runtime.last_prompt
        assert "vision=True" in runtime.last_prompt

    async def test_keyword_fallback_prefers_capability_match(self):
        vision_agent = AgentDefinition(
            id="visionary",
            name="Visionary",
            role="vision",
            instructions="Analyzes screenshots and images.",
            required_capabilities=RequiredCapabilities(vision=True),
        )
        coder = AgentDefinition(
            id="engineer",
            name="Engineer",
            role="engineer",
            instructions="Writes code and tests.",
        )
        registry = AgentRegistry({"visionary": vision_agent, "engineer": coder})
        runtime = FakeRuntime("not json")
        router = LLMTaskRouter(runtime=runtime)

        profile = TaskProfile(needs_vision=True)
        selected = await router.select(
            "explain this screenshot", registry, task_profile=profile
        )
        assert selected.id == "visionary"

    async def test_keyword_fallback_penalizes_missing_reasoning(self):
        architect = AgentDefinition(
            id="architect",
            name="Architect",
            role="architect",
            instructions="Designs systems and architecture.",
            required_capabilities=RequiredCapabilities(
                reasoning=ReasoningLevel.DEEP,
                coding_strength=CodingStrength.HIGH,
            ),
        )
        helper = AgentDefinition(
            id="helper",
            name="Helper",
            role="assistant",
            instructions="General help and summaries.",
        )
        registry = AgentRegistry({"architect": architect, "helper": helper})
        runtime = FakeRuntime("not json")
        router = LLMTaskRouter(runtime=runtime)

        profile = TaskProfile(
            reasoning_depth=ReasoningLevel.DEEP,
            coding_strength=CodingStrength.HIGH,
        )
        selected = await router.select(
            "redesign the async worker architecture", registry, task_profile=profile
        )
        assert selected.id == "architect"
