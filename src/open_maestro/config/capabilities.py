"""Capability-aware model registry.

Open Maestro keeps agent definitions vendor-neutral by using aliases such as
``fast`` or ``smart``.  The capability registry maps those aliases to concrete
model identifiers per runtime, and scores models against a task profile so the
orchestrator can pick the right model for a specific job.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class Tier(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    PREMIUM = "premium"
    REASONING = "reasoning"


class ReasoningLevel(StrEnum):
    NONE = "none"
    LIGHT = "light"
    DEEP = "deep"


class CodingStrength(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LatencyHint(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CostLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


_REASONING_RANK = {
    ReasoningLevel.NONE: 0,
    ReasoningLevel.LIGHT: 1,
    ReasoningLevel.DEEP: 2,
}

_CODING_RANK = {
    CodingStrength.LOW: 0,
    CodingStrength.MEDIUM: 1,
    CodingStrength.HIGH: 2,
}

_LATENCY_RANK = {
    LatencyHint.LOW: 0,
    LatencyHint.MEDIUM: 1,
    LatencyHint.HIGH: 2,
}

_COST_RANK = {
    CostLevel.LOW: 0,
    CostLevel.MEDIUM: 1,
    CostLevel.HIGH: 2,
}


@dataclass
class Capabilities:
    """A capability snapshot for a single model."""

    tier: Tier = Tier.STANDARD
    tool_use: bool = True
    vision: bool = False
    computer_use: bool = False
    reasoning: ReasoningLevel = ReasoningLevel.LIGHT
    coding_strength: CodingStrength = CodingStrength.MEDIUM
    max_context_tokens: int = 128000
    max_output_tokens: int = 8192
    latency_hint: LatencyHint = LatencyHint.MEDIUM
    cost_level: CostLevel = CostLevel.MEDIUM
    relative_cost: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Capabilities:
        return cls(
            tier=_parse_enum(data.get("tier", "standard"), Tier),
            tool_use=bool(data.get("tool_use", True)),
            vision=bool(data.get("vision", False)),
            computer_use=bool(data.get("computer_use", False)),
            reasoning=_parse_enum(
                data.get("reasoning", "light"), ReasoningLevel
            ),
            coding_strength=_parse_enum(
                data.get("coding_strength", "medium"), CodingStrength
            ),
            max_context_tokens=int(
                data.get("max_context_tokens", 128000)
            ),
            max_output_tokens=int(data.get("max_output_tokens", 8192)),
            latency_hint=_parse_enum(
                data.get("latency_hint", "medium"), LatencyHint
            ),
            cost_level=_parse_enum(
                data.get("cost_level", "medium"), CostLevel
            ),
            relative_cost=float(data.get("relative_cost", 0.0)),
        )


@dataclass
class RequiredCapabilities:
    """Minimum capabilities an agent needs from its model.

    All fields are optional.  When set, they override the values inferred from
    the user's prompt so that an agent's requirements always influence model
    selection.
    """

    tool_use: bool | None = None
    vision: bool | None = None
    computer_use: bool | None = None
    reasoning: ReasoningLevel | None = None
    coding_strength: CodingStrength | None = None
    max_context_tokens: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RequiredCapabilities:
        if not data:
            return cls()
        return cls(
            tool_use=_optional_bool(data.get("tool_use")),
            vision=_optional_bool(data.get("vision")),
            computer_use=_optional_bool(data.get("computer_use")),
            reasoning=_optional_enum(
                data.get("reasoning"), ReasoningLevel
            ),
            coding_strength=_optional_enum(
                data.get("coding_strength"), CodingStrength
            ),
            max_context_tokens=_optional_int(
                data.get("max_context_tokens")
            ),
        )

    def merge(self, other: RequiredCapabilities) -> RequiredCapabilities:
        """Return a new RequiredCapabilities with *other* overriding *self*."""
        return RequiredCapabilities(
            tool_use=other.tool_use if other.tool_use is not None else self.tool_use,
            vision=other.vision if other.vision is not None else self.vision,
            computer_use=other.computer_use
            if other.computer_use is not None
            else self.computer_use,
            reasoning=other.reasoning if other.reasoning is not None else self.reasoning,
            coding_strength=other.coding_strength
            if other.coding_strength is not None
            else self.coding_strength,
            max_context_tokens=other.max_context_tokens
            if other.max_context_tokens is not None
            else self.max_context_tokens,
        )

    def merge_into_profile(self, profile: TaskProfile) -> TaskProfile:
        """Return a new profile with agent requirements applied."""
        return TaskProfile(
            needs_tools=(
                self.tool_use
                if self.tool_use is not None
                else profile.needs_tools
            ),
            needs_vision=(
                self.vision
                if self.vision is not None
                else profile.needs_vision
            ),
            reasoning_depth=(
                self.reasoning
                if self.reasoning is not None
                else profile.reasoning_depth
            ),
            coding_strength=(
                self.coding_strength
                if self.coding_strength is not None
                else profile.coding_strength
            ),
            context_tokens_estimate=max(
                self.max_context_tokens or 0, profile.context_tokens_estimate
            ),
            latency_preference=profile.latency_preference,
            cost_preference=profile.cost_preference,
        )


@dataclass
class ModelCapability:
    """A vendor-neutral model entry with per-runtime identifiers."""

    id: str
    name: str
    provider: str
    aliases: list[str] = field(default_factory=list)
    identifiers: dict[str, str] = field(default_factory=dict)
    capabilities: Capabilities = field(default_factory=Capabilities)

    @classmethod
    def from_dict(cls, model_id: str, data: dict[str, Any]) -> ModelCapability:
        return cls(
            id=model_id,
            name=str(data.get("name", model_id)),
            provider=str(data.get("provider", "unknown")),
            aliases=_normalize_str_list(data.get("aliases", [])),
            identifiers=dict(data.get("identifiers", {})),
            capabilities=Capabilities.from_dict(data.get("capabilities", {})),
        )

    def identifier_for(self, runtime: str) -> str | None:
        """Return the concrete model identifier for *runtime*, if known."""
        return self.identifiers.get(runtime)

    def supports_runtime(self, runtime: str) -> bool:
        return runtime in self.identifiers


@dataclass
class TaskProfile:
    """Requirements and preferences extracted from a user task."""

    needs_tools: bool = True
    needs_vision: bool = False
    reasoning_depth: ReasoningLevel = ReasoningLevel.LIGHT
    coding_strength: CodingStrength = CodingStrength.MEDIUM
    context_tokens_estimate: int = 8000
    latency_preference: LatencyHint = LatencyHint.MEDIUM
    cost_preference: CostLevel = CostLevel.MEDIUM


class TaskProfiler:
    """Extract a task profile from a prompt plus explicit CLI overrides."""

    _VISION_KEYWORDS = {
        "screenshot",
        "image",
        "images",
        "diagram",
        "photo",
        "picture",
        "png",
        "jpg",
        "jpeg",
        "webp",
        "ui",
        "mockup",
        "wireframe",
        "visual",
    }

    _REASONING_KEYWORDS = {
        "architecture",
        "architectural",
        "design",
        "trade-off",
        "tradeoff",
        "trade-offs",
        "tradeoffs",
        "root cause",
        "analyze deeply",
        "deep analysis",
        "reasoning",
        "plan",
        "strategy",
        "evaluate",
        "compare",
        "comparison",
        "impact analysis",
        "decision",
        "thorough",
        "thoroughly",
        "cross-reference",
        "cross reference",
        "map against",
        "compare against",
        "repos impacted",
        "services impacted",
    }

    _DEEP_REASONING_KEYWORDS = {
        "redesign",
        "rearchitect",
        "complex",
        "intricate",
        "algorithm design",
        "formal",
        "proof",
        "thorough analysis",
        "multiple repos",
        "many repos",
        "across repos",
        "across services",
        "level of effort",
    }

    _CODING_KEYWORDS = {
        "implement",
        "refactor",
        "write code",
        "function",
        "class",
        "module",
        "test",
        "tests",
        "bug",
        "debug",
        "fix",
        "feature",
        "integration",
        "build",
        "compile",
        "migrate",
        "parser",
        "parse",
        "validate",
        "serializer",
        "endpoint",
        "handler",
    }

    _HIGH_CODING_KEYWORDS = {
        "refactor",
        "rewrite",
        "performance",
        "optimization",
        "critical",
        "core",
        "complex logic",
    }

    _FAST_KEYWORDS = {
        "quick",
        "quickly",
        "brief",
        "briefly",
        "short",
        "one-liner",
        "summarize",
        "summary",
        "tldr",
        "fast",
    }

    _LONG_CONTEXT_KEYWORDS = {
        "entire codebase",
        "whole repo",
        "whole project",
        "all files",
        "large codebase",
        "many files",
        "across the project",
        "cross-cutting",
        "every file",
        "multiple repos",
        "many repos",
        "across repos",
        "git repo",
        "github",
    }

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        *,
        needs_tools: bool | None = None,
        needs_vision: bool | None = None,
        reasoning_depth: ReasoningLevel | None = None,
        coding_strength: CodingStrength | None = None,
        context_tokens_estimate: int | None = None,
        latency_preference: LatencyHint | None = None,
        cost_preference: CostLevel | None = None,
    ) -> TaskProfile:
        lowered = prompt.lower()
        words = set(re.findall(r"[a-z][a-z0-9_]*", lowered))  # noqa: F821

        # Vision keywords are short (e.g. "ui") and must be matched as whole
        # words to avoid false positives such as "building" or "guideline".
        detected_vision = bool(cls._VISION_KEYWORDS & words)
        detected_reasoning = ReasoningLevel.LIGHT
        if any(kw in lowered for kw in cls._DEEP_REASONING_KEYWORDS):
            detected_reasoning = ReasoningLevel.DEEP
        elif any(kw in lowered for kw in cls._REASONING_KEYWORDS):
            detected_reasoning = ReasoningLevel.LIGHT

        detected_coding = CodingStrength.MEDIUM
        if any(kw in lowered for kw in cls._HIGH_CODING_KEYWORDS):
            detected_coding = CodingStrength.HIGH
        elif not (cls._CODING_KEYWORDS & words):
            detected_coding = CodingStrength.LOW

        detected_latency = LatencyHint.MEDIUM
        if any(kw in lowered for kw in cls._FAST_KEYWORDS):
            detected_latency = LatencyHint.LOW
        if detected_reasoning == ReasoningLevel.DEEP:
            detected_latency = LatencyHint.HIGH

        detected_cost = CostLevel.MEDIUM
        if detected_latency == LatencyHint.LOW:
            detected_cost = CostLevel.LOW
        if detected_reasoning == ReasoningLevel.DEEP:
            detected_cost = CostLevel.HIGH

        context_estimate = 8000
        if any(kw in lowered for kw in cls._LONG_CONTEXT_KEYWORDS):
            context_estimate = 128000
        elif detected_reasoning == ReasoningLevel.DEEP:
            context_estimate = 64000

        return TaskProfile(
            needs_tools=needs_tools if needs_tools is not None else True,
            needs_vision=needs_vision if needs_vision is not None else detected_vision,
            reasoning_depth=reasoning_depth or detected_reasoning,
            coding_strength=coding_strength or detected_coding,
            context_tokens_estimate=context_tokens_estimate or context_estimate,
            latency_preference=latency_preference or detected_latency,
            cost_preference=cost_preference or detected_cost,
        )


@dataclass
class CapabilityRegistry:
    """User-editable registry of model capabilities."""

    models: dict[str, ModelCapability]

    def __post_init__(self) -> None:
        self._identifier_to_id: dict[tuple[str, str], str] = {}
        self._alias_to_ids: dict[str, list[str]] = {}
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        self._identifier_to_id.clear()
        self._alias_to_ids.clear()
        for model in self.models.values():
            for runtime, identifier in model.identifiers.items():
                self._identifier_to_id[(runtime, identifier)] = model.id
            for alias in model.aliases:
                self._alias_to_ids.setdefault(alias, []).append(model.id)

    @classmethod
    def load(
        cls,
        default_path: str | Path | None = None,
        user_paths: list[str | Path] | None = None,
    ) -> CapabilityRegistry:
        """Load the default registry and merge user overrides on top.

        User override locations (in order, later wins):
        1. ``OPEN_MAESTRO_CAPABILITIES`` env var.
        2. ``~/.open-maestro/capabilities.yaml``.
        3. ``.open-maestro/capabilities.yaml`` in the current working directory.
        """
        if default_path is None:
            default_path = Path(__file__).with_name("default_capabilities.yaml")

        default_data = _load_yaml(default_path)
        merged = _deep_merge_models(
            default_data.get("models", {}),
            {},
        )

        candidates: list[Path] = []
        if user_paths:
            candidates.extend(Path(p) for p in user_paths)
        if os.environ.get("OPEN_MAESTRO_CAPABILITIES"):
            candidates.append(Path(os.environ["OPEN_MAESTRO_CAPABILITIES"]))
        candidates.append(Path.home() / ".open-maestro" / "capabilities.yaml")
        candidates.append(Path.cwd() / ".open-maestro" / "capabilities.yaml")

        for candidate in candidates:
            if candidate.exists():
                user_data = _load_yaml(candidate)
                merged = _deep_merge_models(merged, user_data.get("models", {}))

        models = {
            model_id: ModelCapability.from_dict(model_id, data)
            for model_id, data in merged.items()
        }
        return cls(models)

    def list_models(
        self, runtime: str | None = None
    ) -> list[ModelCapability]:
        """Return all models, optionally filtered to those supporting *runtime*."""
        models = list(self.models.values())
        if runtime:
            models = [m for m in models if m.supports_runtime(runtime)]
        return models

    def models_for_alias(
        self, alias: str, runtime: str | None = None
    ) -> list[ModelCapability]:
        """Return models matching *alias*, optionally filtered by runtime."""
        ids = self._alias_to_ids.get(alias, [])
        models = [self.models[mid] for mid in ids if mid in self.models]
        if runtime:
            models = [m for m in models if m.supports_runtime(runtime)]
        return models

    def resolve(
        self,
        alias_or_id: str,
        runtime: str,
        profile: TaskProfile | None = None,
    ) -> str | None:
        """Resolve an alias or concrete identifier to a runtime model ID.

        Resolution order:
        1. If *alias_or_id* is already a known concrete identifier for *runtime*,
           return it unchanged.
        2. If *alias_or_id* is a known canonical model id, return its identifier
           for *runtime* (or the id itself if no mapping exists).
        3. If *alias_or_id* is an alias, score matching models against *profile*
           and return the best identifier for *runtime*.
        4. Otherwise pass the value through unchanged.
        """
        lowered = alias_or_id.lower()

        # 1. Already a concrete identifier for this runtime.
        if (runtime, alias_or_id) in self._identifier_to_id:
            return alias_or_id

        # 2. Canonical model id.
        if lowered in self.models:
            model = self.models[lowered]
            return model.identifier_for(runtime) or alias_or_id

        # 3. Alias lookup.
        candidates = self.models_for_alias(lowered, runtime=runtime)
        if candidates:
            if profile is None:
                return candidates[0].identifier_for(runtime)
            best = self._score_and_pick(candidates, profile)
            if best is not None:
                return best.identifier_for(runtime)

        # 4. Pass through.
        return alias_or_id

    def match(
        self,
        runtime: str,
        profile: TaskProfile,
        required_alias: str | None = None,
    ) -> ModelCapability | None:
        """Pick the best model for *runtime* matching *profile*."""
        candidates = self.list_models(runtime=runtime)
        if required_alias:
            candidates = [
                m for m in candidates if required_alias.lower() in m.aliases
            ]
        return self._score_and_pick(candidates, profile)

    @staticmethod
    def _score_and_pick(
        candidates: list[ModelCapability], profile: TaskProfile
    ) -> ModelCapability | None:
        if not candidates:
            return None
        scored: list[tuple[int, ModelCapability]] = []
        for model in candidates:
            score = _score_model(model, profile)
            if score is not None:
                scored.append((score, model))
        if not scored:
            return None
        scored.sort(key=lambda x: x[0], reverse=True)
        logger.debug(
            "Capability scoring: top=%s score=%s",
            scored[0][1].id,
            scored[0][0],
        )
        return scored[0][1]


def _score_model(
    model: ModelCapability, profile: TaskProfile
) -> int | None:
    """Score a model against a profile.  Returns None if it cannot satisfy hard requirements.

    The scorer treats reasoning and coding as minimum capabilities: a model that
    cannot meet the required level is excluded so that "cheapest for the job"
    never means "too weak for the job".  Among qualifying models, latency,
    cost, and tier preferences break the tie.
    """
    cap = model.capabilities

    # Hard requirements.
    if profile.needs_tools and not cap.tool_use:
        return None
    if profile.needs_vision and not cap.vision:
        return None
    if cap.max_context_tokens < profile.context_tokens_estimate:
        return None

    # Reasoning and coding are minimum bars: a model must be able to do the work.
    if _REASONING_RANK[cap.reasoning] < _REASONING_RANK[profile.reasoning_depth]:
        return None
    if _CODING_RANK[cap.coding_strength] < _CODING_RANK[profile.coding_strength]:
        return None

    score = 0

    # Small bonus for an exact capability fit.
    if cap.reasoning == profile.reasoning_depth:
        score += 5
    if cap.coding_strength == profile.coding_strength:
        score += 5

    # Latency preference (strong weight).
    score += (
        _LATENCY_RANK[profile.latency_preference]
        - _LATENCY_RANK[cap.latency_hint]
    ) * 25

    # Cost preference.
    score += (
        _COST_RANK[profile.cost_preference] - _COST_RANK[cap.cost_level]
    ) * 20

    # Tier preference reinforces latency/cost/reasoning signals.
    tier_rank = {Tier.FAST: 0, Tier.STANDARD: 1, Tier.PREMIUM: 2, Tier.REASONING: 3}
    if profile.reasoning_depth == ReasoningLevel.DEEP:
        score += tier_rank[cap.tier] * 10
    elif profile.latency_preference == LatencyHint.LOW:
        score -= tier_rank[cap.tier] * 10
    else:
        # Balanced: slight preference for standard/premium.
        if cap.tier in (Tier.STANDARD, Tier.PREMIUM):
            score += 5

    # Context headroom.
    if cap.max_context_tokens >= profile.context_tokens_estimate * 2:
        score += 5

    return score


def _parse_enum(value: Any, enum_cls: type[StrEnum]) -> Any:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).lower())
    except ValueError as exc:
        raise ValueError(
            f"Invalid {enum_cls.__name__} value: {value!r}"
        ) from exc


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_enum(value: Any, enum_cls: type[StrEnum]) -> Any | None:
    if value is None:
        return None
    return _parse_enum(value, enum_cls)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        raise RuntimeError(f"Failed to load YAML from {path}: {exc}") from exc


def _deep_merge_models(
    base: dict[str, Any], override: dict[str, Any]
) -> dict[str, Any]:
    """Merge user model definitions over defaults.

    Aliases and identifiers are replaced outright; capabilities are merged
    field-by-field so users can override a single flag without restating the
    whole model.
    """
    result: dict[str, Any] = {}
    # Preserve insertion order: base entries first, then new override entries.
    for key in base:
        if key in override:
            merged = dict(base[key])
            user = dict(override[key])
            for field_name, value in user.items():
                if field_name == "capabilities" and isinstance(value, dict):
                    merged["capabilities"] = {
                        **(merged.get("capabilities") or {}),
                        **value,
                    }
                else:
                    merged[field_name] = value
            result[key] = merged
        else:
            result[key] = dict(base[key])
    for key in override:
        if key not in base:
            result[key] = dict(override[key])
    return result
