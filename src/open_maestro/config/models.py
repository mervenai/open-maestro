"""Model alias resolution.

Open Maestro uses short aliases (``fast``, ``smart``, ``reasoning``) so agent
definitions stay vendor-neutral.  At runtime, aliases are resolved to
provider-specific model identifiers based on the active backend and, optionally,
a task profile that scores models by capability.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from open_maestro.config.capabilities import (
    CapabilityRegistry,
    TaskProfile,
)

logger = logging.getLogger(__name__)

# Fallback aliases used only if the capability registry cannot be loaded.
_FALLBACK_ALIASES: dict[str, dict[str, str]] = {
    "kimi-cli": {
        "default": "kimi-code/kimi-for-coding",
        "fast": "kimi-code/kimi-for-coding",
        "smart": "kimi-code/k3",
        "reasoning": "kimi-code/k3",
    },
    "claude-cli": {
        "default": "claude-sonnet-4-6",
        "fast": "claude-3-5-haiku-20241022",
        "smart": "claude-sonnet-4-6",
        "reasoning": "claude-opus-4-7",
    },
    "openai-sdk": {
        "default": "gpt-4o",
        "fast": "gpt-4o-mini",
        "smart": "gpt-4o",
        "reasoning": "o3-mini",
        # Qwen / Alibaba Cloud DashScope identifiers (OpenAI-compatible mode).
        "qwen": "qwen-plus",
        "qwen-plus": "qwen-plus",
        "qwen-max": "qwen-max",
        "qwen-coder": "qwen-coder-plus",
        "qwen-coder-plus": "qwen-coder-plus",
    },
}


class ModelResolver:
    """Resolve vendor-neutral model aliases to backend-specific identifiers."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        registry: CapabilityRegistry | None = None,
    ):
        self._config = config or {}
        if registry is None:
            try:
                registry = CapabilityRegistry.load()
            except Exception as exc:
                logger.warning(
                    "Failed to load capability registry; falling back to hardcoded aliases: %s",
                    exc,
                )
                registry = CapabilityRegistry({})
        self._registry = registry
        self._fallback = _FALLBACK_ALIASES

    def resolve(
        self,
        alias: str | None,
        backend: str,
        profile: TaskProfile | None = None,
    ) -> str | None:
        """Return the concrete model identifier for an alias and backend.

        If *alias* is already a full model identifier known to the registry, it
        is returned as-is.  If a *profile* is supplied, capability-aware scoring
        is used to pick the best model among alias matches.
        """
        if alias is None:
            alias = "default"

        # Try the capability registry first.
        resolved = self._registry.resolve(alias, backend, profile=profile)
        if resolved and resolved != alias:
            return resolved

        # If the registry has no opinion, try fallback aliases.
        if alias.lower() in self._fallback.get(backend, {}):
            return self._fallback[backend][alias.lower()]

        # Pass through concrete identifiers the registry does not know about.
        return alias

    def select_for_task(
        self,
        backend: str,
        profile: TaskProfile,
        required_alias: str | None = None,
    ) -> str | None:
        """Pick the best concrete model identifier for *backend* and *profile*."""
        model = self._registry.match(backend, profile, required_alias=required_alias)
        if model is not None:
            return model.identifier_for(backend)

        # Fallback to alias table.
        alias = required_alias or "default"
        return self._fallback.get(backend, {}).get(alias.lower())

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> ModelResolver:
        """Load resolver from a YAML file or default locations.

        Searches, in order:
        1. Explicit *path*.
        2. ``OPEN_MAESTRO_CONFIG`` env var.
        3. ``~/.open-maestro/models.yaml``.
        4. ``.open-maestro/models.yaml`` in the current working directory.
        5. Empty config (defaults only).
        """
        config = load_model_config(path)
        return cls(config)


def load_model_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load legacy model configuration from a YAML file.

    The modern source of truth is ``default_capabilities.yaml`` and user
    ``capabilities.yaml`` files.  This loader remains for backwards
    compatibility with existing ``models.yaml`` overrides.
    """
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    if os.environ.get("OPEN_MAESTRO_CONFIG"):
        candidates.append(Path(os.environ["OPEN_MAESTRO_CONFIG"]))
    candidates.append(Path.home() / ".open-maestro" / "models.yaml")
    candidates.append(Path.cwd() / ".open-maestro" / "models.yaml")

    for candidate in candidates:
        if candidate.exists():
            try:
                with candidate.open("r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load model config from {candidate}: {exc}"
                ) from exc
    return {}
