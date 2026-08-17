"""Runtime factory and discovery."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from open_maestro.config.capabilities import (
    _COST_RANK,
    CostLevel,
    LatencyHint,
    ReasoningLevel,
    Tier,
)
from open_maestro.runtime.availability import is_model_available, is_runtime_available
from open_maestro.runtime.latency import LatencyCache, model_latency_score

if TYPE_CHECKING:
    from open_maestro.config.capabilities import TaskProfile
    from open_maestro.runtime.base import AgentConfig, AgentRuntime

logger = logging.getLogger(__name__)


_RUNTIMES: dict[str, str] = {
    "kimi-cli": "open_maestro.runtime.kimi_cli.KimiCLIRuntime",
    "kimi-acp": "open_maestro.runtime.kimi_acp.KimiACPRuntime",
    "claude-cli": "open_maestro.runtime.claude_cli.ClaudeCLIRuntime",
    "claude-sdk": "open_maestro.runtime.claude_sdk.ClaudeSDKRuntime",
    "openai-sdk": "open_maestro.runtime.openai_sdk.OpenAISDKRuntime",
}


def _load_class(dotted: str):
    module_name, class_name = dotted.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def list_runtimes() -> dict[str, bool]:
    """Return runtime names and whether each backend appears available."""
    result: dict[str, bool] = {}
    for name in _RUNTIMES:
        try:
            result[name] = is_runtime_available(name)
        except Exception:
            result[name] = False
    return result


def create_runtime(
    runtime_type: str | None = None,
    config: AgentConfig | None = None,
) -> AgentRuntime:
    """Create a concrete ``AgentRuntime``.

    Args:
        runtime_type: One of ``kimi-cli``, ``claude-cli``, ``openai-sdk``.
            If None, resolved from ``OPEN_MAESTRO_RUNTIME`` env var, then
            auto-detected from installed CLIs.
        config: Optional runtime-agnostic configuration.

    Returns:
        A configured ``AgentRuntime`` instance.
    """
    if runtime_type is None:
        runtime_type = os.environ.get("OPEN_MAESTRO_RUNTIME", "").strip()

    if not runtime_type:
        runtime_type = _auto_detect_runtime()

    runtime_type = runtime_type.lower()

    if runtime_type not in _RUNTIMES:
        raise ValueError(
            f"Unknown runtime type: {runtime_type!r}. "
            f"Available: {', '.join(_RUNTIMES)}"
        )

    cls = _load_class(_RUNTIMES[runtime_type])
    return cls.from_config(config) if config is not None else cls()


def _auto_detect_runtime() -> str:
    """Pick the first available runtime.

    Order:
    1. OpenAI-compatible SDK if credentials/endpoint are set.  It has the best
       tool interception, guardrails, and observability.
    2. CLI adapters if the corresponding binary is on PATH.
    3. Vendor SDK adapters if installed.

    Set ``OPEN_MAESTRO_PREFER_SDK=1`` to try vendor SDK adapters before CLI
    adapters.  Set ``OPEN_MAESTRO_PREFER_CLI=1`` to force CLI adapters before
    the OpenAI-compatible SDK.
    """
    import shutil

    prefer_sdk = os.environ.get("OPEN_MAESTRO_PREFER_SDK", "").lower() in (
        "1",
        "true",
        "yes",
    )
    prefer_cli = os.environ.get("OPEN_MAESTRO_PREFER_CLI", "").lower() in (
        "1",
        "true",
        "yes",
    )

    def _sdk_available(dotted: str) -> bool:
        try:
            cls = _load_class(dotted)
            return bool(cls.__new__(cls).is_available())
        except Exception:
            return False

    # 1. OpenAI-compatible SDK first when available and not explicitly deprioritised.
    if not prefer_cli and _sdk_available(_RUNTIMES["openai-sdk"]):
        logger.info("Auto-selected runtime: openai-sdk")
        return "openai-sdk"

    sdk_order = ["kimi-acp", "claude-sdk"]
    cli_order = ["kimi-cli", "claude-cli"]

    first_order = sdk_order if prefer_sdk else cli_order
    second_order = cli_order if prefer_sdk else sdk_order

    for runtime_name in first_order:
        if runtime_name in ("kimi-cli", "claude-cli"):
            binary = "kimi" if runtime_name == "kimi-cli" else "claude"
            if shutil.which(binary):
                logger.info("Auto-selected runtime: %s", runtime_name)
                return runtime_name
        elif _sdk_available(_RUNTIMES[runtime_name]):
            logger.info("Auto-selected runtime: %s", runtime_name)
            return runtime_name

    for runtime_name in second_order:
        if runtime_name in ("kimi-cli", "claude-cli"):
            binary = "kimi" if runtime_name == "kimi-cli" else "claude"
            if shutil.which(binary):
                logger.info("Auto-selected runtime: %s", runtime_name)
                return runtime_name
        elif _sdk_available(_RUNTIMES[runtime_name]):
            logger.info("Auto-selected runtime: %s", runtime_name)
            return runtime_name

    raise RuntimeError(
        "No supported runtime found. Install kimi, claude, claude-agent-sdk, "
        "agent-client-protocol, or openai."
    )


def select_runtime_for_task(
    profile: TaskProfile,
    runtime_type: str | None = None,
    latency_tolerance: float = 1.2,
    max_cost_level: CostLevel | None = None,
    min_cost_level: CostLevel | None = CostLevel.MEDIUM,
    prefer_local: bool = False,
) -> tuple[str, str | None]:
    """Pick the cheapest capable model that is not much slower than the fastest.

    Selection steps:
    1. Enumerate every available (runtime, model) pair that satisfies *profile*.
    2. Exclude local/self-hosted models unless *prefer_local* is True.
    3. Enforce the cost floor/ceiling from *min_cost_level* and *max_cost_level*.
    4. Compute a latency score for each model (measured throughput first, then
       the declared latency hint as a fallback).
    5. Keep only models within *latency_tolerance* of the fastest score.
    6. Return the cheapest remaining model, breaking ties by tier and latency.
       For deep-reasoning tasks, tier is sorted ahead of cost so frontier
       models are preferred.

    Args:
        profile: Task requirements and preferences.
        runtime_type: If provided, only consider this runtime.
        latency_tolerance: Maximum allowed latency ratio vs. the fastest model
            (default 1.2, i.e. up to 20% slower).
        max_cost_level: If provided, exclude models more expensive than this.
        min_cost_level: Minimum cost level to consider (default MEDIUM). Set to
            LOW to allow cheap/local models, HIGH to restrict to frontier models.
        prefer_local: If True, only consider local/self-hosted models.
    """
    from open_maestro.config.capabilities import (
        CapabilityRegistry,
        _score_model,
    )

    registry = CapabilityRegistry.load()
    latency_cache = LatencyCache.load()

    runtimes = list_runtimes()
    prefer_cli = os.environ.get("OPEN_MAESTRO_PREFER_CLI", "").lower() in (
        "1",
        "true",
        "yes",
    )
    cli_runtimes = {"kimi-cli", "claude-cli"}
    local_providers = {"ollama", "local"}

    candidates: list[tuple[float, int, int, float, bool, str, str]] = []

    for name, available in runtimes.items():
        if not available:
            continue
        if runtime_type is not None and name != runtime_type:
            continue
        if runtime_type is None and prefer_cli and name not in cli_runtimes:
            continue

        for model_entry in registry.list_models(runtime=name):
            capability = model_entry.capabilities

            if not prefer_local and model_entry.provider in local_providers:
                logger.debug(
                    "Skipping %s / %s: local model and --prefer-local not set",
                    name,
                    model_entry.id,
                )
                continue

            if prefer_local and model_entry.provider not in local_providers:
                logger.debug(
                    "Skipping %s / %s: not a local model", name, model_entry.id
                )
                continue

            if max_cost_level is not None and _COST_RANK.get(
                capability.cost_level, 99
            ) > _COST_RANK.get(max_cost_level, 99):
                logger.debug(
                    "Skipping %s / %s: cost level exceeds %s",
                    name,
                    model_entry.id,
                    max_cost_level,
                )
                continue

            if min_cost_level is not None and _COST_RANK.get(
                capability.cost_level, 99
            ) < _COST_RANK.get(min_cost_level, 0):
                logger.debug(
                    "Skipping %s / %s: cost level below %s",
                    name,
                    model_entry.id,
                    min_cost_level,
                )
                continue

            if _score_model(model_entry, profile) is None:
                logger.debug(
                    "Skipping %s / %s: does not satisfy task profile",
                    name,
                    model_entry.id,
                )
                continue

            model_id = model_entry.identifier_for(name)
            if model_id is None:
                continue

            if not is_model_available(name, model_entry):
                logger.debug(
                    "Skipping %s / %s: model not available", name, model_entry.id
                )
                continue

            measured = latency_cache.tokens_per_second(model_entry.id) is not None
            latency_score = model_latency_score(
                model_entry.id, capability, cache=latency_cache
            )

            candidates.append(
                (
                    capability.relative_cost,
                    _tier_rank(capability.tier),
                    _latency_rank(capability.latency_hint),
                    latency_score,
                    measured,
                    name,
                    model_id,
                )
            )

    if not candidates:
        if runtime_type:
            raise RuntimeError(
                f"No model found for runtime '{runtime_type}' and task profile."
            )
        raise RuntimeError("No available runtime can satisfy the task profile.")

    # Only enforce a strict latency window when we have real measurements.
    # Declared latency hints are too coarse to exclude cheaper models before
    # we have measured their actual throughput.
    has_measurements = any(c[4] for c in candidates)
    if has_measurements:
        fastest = min(c[3] for c in candidates)
        max_allowed_latency = fastest * max(latency_tolerance, 1.0)
        candidates = [c for c in candidates if c[3] <= max_allowed_latency]

    # For deep-reasoning tasks, prefer higher-tier models even if more expensive.
    # Otherwise sort by cost first so "cheapest capable model" wins.
    if profile.reasoning_depth == ReasoningLevel.DEEP:
        candidates.sort(key=lambda c: (-c[1], c[0], c[2], c[5], c[6]))
    else:
        candidates.sort(key=lambda c: (c[0], c[1], c[2], c[5], c[6]))
    selected_runtime, selected_model = candidates[0][5], candidates[0][6]
    logger.debug(
        "Selected cheapest runtime/model within %.2fx latency: %s / %s",
        latency_tolerance,
        selected_runtime,
        selected_model,
    )
    return selected_runtime, selected_model


def _tier_rank(tier: Tier) -> int:
    mapping = {Tier.FAST: 0, Tier.STANDARD: 1, Tier.PREMIUM: 2, Tier.REASONING: 3}
    return mapping.get(tier, 1)


def _latency_rank(latency: LatencyHint) -> int:
    mapping = {LatencyHint.LOW: 0, LatencyHint.MEDIUM: 1, LatencyHint.HIGH: 2}
    return mapping.get(latency, 1)
