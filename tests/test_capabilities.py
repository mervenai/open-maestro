"""Tests for the capability-aware model registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_maestro.config.capabilities import (
    Capabilities,
    CapabilityRegistry,
    CodingStrength,
    CostLevel,
    LatencyHint,
    ModelCapability,
    ReasoningLevel,
    TaskProfile,
    TaskProfiler,
    Tier,
)
from open_maestro.config.models import ModelResolver


@pytest.fixture
def sample_registry() -> CapabilityRegistry:
    return CapabilityRegistry(
        {
            "kimi-k3": ModelCapability(
                id="kimi-k3",
                name="Kimi K3",
                provider="kimi",
                aliases=["smart", "reasoning"],
                identifiers={"kimi-cli": "kimi-code/k3"},
                capabilities=Capabilities(
                    tier=Tier.PREMIUM,
                    reasoning=ReasoningLevel.DEEP,
                    coding_strength=CodingStrength.HIGH,
                    latency_hint=LatencyHint.MEDIUM,
                    cost_level=CostLevel.MEDIUM,
                ),
            ),
            "kimi-fast": ModelCapability(
                id="kimi-fast",
                name="Kimi Fast",
                provider="kimi",
                aliases=["fast", "default", "smart"],
                identifiers={"kimi-cli": "kimi-code/kimi-for-coding"},
                capabilities=Capabilities(
                    tier=Tier.FAST,
                    reasoning=ReasoningLevel.LIGHT,
                    coding_strength=CodingStrength.HIGH,
                    latency_hint=LatencyHint.LOW,
                    cost_level=CostLevel.LOW,
                ),
            ),
            "claude-sonnet": ModelCapability(
                id="claude-sonnet",
                name="Claude Sonnet",
                provider="anthropic",
                aliases=["smart", "default"],
                identifiers={"claude-cli": "claude-sonnet-4-6"},
                capabilities=Capabilities(
                    tier=Tier.STANDARD,
                    reasoning=ReasoningLevel.LIGHT,
                    coding_strength=CodingStrength.HIGH,
                    latency_hint=LatencyHint.MEDIUM,
                    cost_level=CostLevel.MEDIUM,
                ),
            ),
        }
    )


class TestCapabilityRegistry:
    def test_resolve_concrete_identifier(self, sample_registry: CapabilityRegistry):
        assert (
            sample_registry.resolve("kimi-code/k3", "kimi-cli")
            == "kimi-code/k3"
        )

    def test_resolve_canonical_id(self, sample_registry: CapabilityRegistry):
        assert (
            sample_registry.resolve("kimi-k3", "kimi-cli")
            == "kimi-code/k3"
        )

    def test_resolve_alias_without_profile(
        self, sample_registry: CapabilityRegistry
    ):
        resolved = sample_registry.resolve("smart", "kimi-cli")
        assert resolved in {"kimi-code/k3", "claude-sonnet-4-6"}

    def test_resolve_alias_with_profile_prefers_reasoning(
        self, sample_registry: CapabilityRegistry
    ):
        profile = TaskProfile(reasoning_depth=ReasoningLevel.DEEP)
        resolved = sample_registry.resolve("smart", "kimi-cli", profile=profile)
        assert resolved == "kimi-code/k3"

    def test_resolve_alias_with_profile_prefers_fast(
        self, sample_registry: CapabilityRegistry
    ):
        profile = TaskProfile(
            latency_preference=LatencyHint.LOW,
            cost_preference=CostLevel.LOW,
        )
        resolved = sample_registry.resolve("smart", "kimi-cli", profile=profile)
        assert resolved == "kimi-code/kimi-for-coding"

    def test_match_filters_by_runtime(self, sample_registry: CapabilityRegistry):
        profile = TaskProfile()
        model = sample_registry.match("claude-cli", profile)
        assert model is not None
        assert model.id == "claude-sonnet"

    def test_hard_requirement_excludes_models(self):
        registry = CapabilityRegistry(
            {
                "vision-model": ModelCapability(
                    id="vision-model",
                    name="Vision Model",
                    provider="openai",
                    aliases=["smart"],
                    identifiers={"openai-sdk": "gpt-4o"},
                ),
                "no-vision-model": ModelCapability(
                    id="no-vision-model",
                    name="No Vision",
                    provider="openai",
                    aliases=["smart"],
                    identifiers={"openai-sdk": "o3-mini"},
                ),
            }
        )
        # Force vision requirement
        registry.models["no-vision-model"].capabilities.vision = False
        registry.models["vision-model"].capabilities.vision = True
        registry._rebuild_indexes()

        profile = TaskProfile(needs_vision=True)
        model = registry.match("openai-sdk", profile)
        assert model is not None
        assert model.id == "vision-model"


class TestTaskProfiler:
    def test_detects_vision(self):
        profile = TaskProfiler.from_prompt("explain this screenshot")
        assert profile.needs_vision is True

    def test_detects_reasoning(self):
        profile = TaskProfiler.from_prompt("design the architecture")
        assert profile.reasoning_depth == ReasoningLevel.LIGHT

    def test_detects_deep_reasoning(self):
        profile = TaskProfiler.from_prompt("redesign the complex algorithm")
        assert profile.reasoning_depth == ReasoningLevel.DEEP

    def test_detects_coding(self):
        profile = TaskProfiler.from_prompt("write a parser")
        assert profile.coding_strength == CodingStrength.MEDIUM

    def test_detects_high_coding(self):
        profile = TaskProfiler.from_prompt("refactor the core performance logic")
        assert profile.coding_strength == CodingStrength.HIGH

    def test_overrides_take_precedence(self):
        profile = TaskProfiler.from_prompt(
            "quick summary",
            reasoning_depth=ReasoningLevel.DEEP,
            needs_vision=True,
            coding_strength=CodingStrength.HIGH,
        )
        assert profile.reasoning_depth == ReasoningLevel.DEEP
        assert profile.needs_vision is True
        assert profile.coding_strength == CodingStrength.HIGH


class TestModelResolverCapabilities:
    def test_resolver_loads_default_registry(self):
        resolver = ModelResolver()
        # Should resolve default aliases using the bundled capabilities.
        assert resolver.resolve("fast", "kimi-cli") == "kimi-code/kimi-for-coding"
        assert resolver.resolve("smart", "kimi-cli") == "kimi-code/k3"
        assert resolver.resolve("reasoning", "claude-cli") == "claude-opus-4-7"

    def test_resolver_uses_profile_for_alias(self):
        resolver = ModelResolver()
        profile = TaskProfile(reasoning_depth=ReasoningLevel.DEEP)
        resolved = resolver.resolve("smart", "kimi-cli", profile=profile)
        assert resolved == "kimi-code/k3"

    def test_resolver_select_for_task(self):
        resolver = ModelResolver()
        profile = TaskProfile(
            needs_vision=True,
            reasoning_depth=ReasoningLevel.LIGHT,
            coding_strength=CodingStrength.HIGH,
            cost_preference=CostLevel.MEDIUM,
        )
        resolved = resolver.select_for_task("openai-sdk", profile)
        assert resolved == "gpt-4o"


class TestCapabilityRegistryMerge:
    def test_user_override_changes_identifier(self, tmp_path: Path):
        default_file = tmp_path / "default.yaml"
        default_file.write_text(
            "models:\n"
            "  my-model:\n"
            "    name: My Model\n"
            "    provider: test\n"
            "    aliases: [smart]\n"
            "    identifiers:\n"
            "      kimi-cli: original-id\n"
            "    capabilities:\n"
            "      tier: standard\n"
        )
        user_file = tmp_path / "user.yaml"
        user_file.write_text(
            "models:\n"
            "  my-model:\n"
            "    identifiers:\n"
            "      kimi-cli: overridden-id\n"
        )
        registry = CapabilityRegistry.load(
            default_path=default_file,
            user_paths=[user_file],
        )
        assert registry.resolve("smart", "kimi-cli") == "overridden-id"

    def test_user_override_merges_capabilities(self, tmp_path: Path):
        default_file = tmp_path / "default.yaml"
        default_file.write_text(
            "models:\n"
            "  my-model:\n"
            "    name: My Model\n"
            "    provider: test\n"
            "    aliases: [smart]\n"
            "    identifiers:\n"
            "      kimi-cli: id\n"
            "    capabilities:\n"
            "      tier: standard\n"
            "      vision: false\n"
        )
        user_file = tmp_path / "user.yaml"
        user_file.write_text(
            "models:\n"
            "  my-model:\n"
            "    capabilities:\n"
            "      vision: true\n"
        )
        registry = CapabilityRegistry.load(
            default_path=default_file,
            user_paths=[user_file],
        )
        model = registry.models["my-model"]
        assert model.capabilities.tier.value == "standard"
        assert model.capabilities.vision is True

    def test_merge_preserves_order(self, tmp_path: Path):
        default_file = tmp_path / "default.yaml"
        default_file.write_text(
            "models:\n"
            "  first:\n"
            "    name: First\n"
            "    provider: test\n"
            "    identifiers:\n"
            "      openai-sdk: first-id\n"
            "  second:\n"
            "    name: Second\n"
            "    provider: test\n"
            "    identifiers:\n"
            "      openai-sdk: second-id\n"
        )
        user_file = tmp_path / "user.yaml"
        user_file.write_text(
            "models:\n"
            "  third:\n"
            "    name: Third\n"
            "    provider: test\n"
            "    identifiers:\n"
            "      openai-sdk: third-id\n"
        )
        registry = CapabilityRegistry.load(
            default_path=default_file,
            user_paths=[user_file],
        )
        assert list(registry.models.keys()) == ["first", "second", "third"]
