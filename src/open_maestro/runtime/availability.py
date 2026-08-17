"""Runtime and model availability probing.

Maestro can arbitrate across local and network models, but only if it can tell
which ones are actually reachable right now.  This module provides lightweight
probes for CLI binaries, local servers (Ollama, vLLM, LM Studio), and cloud
SDK credentials.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from open_maestro.config.capabilities import ModelCapability

logger = logging.getLogger(__name__)


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _ollama_host() -> str:
    """Return the Ollama base URL from the environment or the default."""
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    return host


def _ollama_models(timeout: float = 2.0) -> set[str]:
    """Return the set of model names currently available in Ollama."""
    host = _ollama_host()
    try:
        response = httpx.get(
            f"{host}/api/tags",
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        return {entry.get("name", "").split(":")[0] for entry in data.get("models", [])}
    except Exception as exc:
        logger.debug("Ollama probe failed for %s: %s", host, exc)
        return set()


def _probe_url(url: str, timeout: float = 2.0) -> bool:
    """Return True if *url* responds to a lightweight HTTP probe."""
    try:
        response = httpx.get(url, timeout=timeout)
        return response.status_code < 500
    except Exception as exc:
        logger.debug("URL probe failed for %s: %s", url, exc)
        return False


def _openai_sdk_cloud_available() -> bool:
    """Return True when the openai-sdk runtime looks configured for a cloud endpoint."""
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    # A localhost/loopback base URL is a local backend, not a cloud endpoint.
    is_local_url = bool(base_url) and (
        "localhost" in base_url or "127.0.0.1" in base_url
    )
    return bool(os.environ.get("OPENAI_API_KEY") or (base_url and not is_local_url))


def _openai_sdk_local_available(identifier: str | None = None) -> bool:
    """Return True when a local OpenAI-compatible endpoint is reachable.

    If *identifier* is an Ollama model name, the Ollama /api/tags list is
    checked.  Otherwise we probe OPENAI_BASE_URL if it is set.
    """
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    is_ollama_endpoint = (
        not base_url
        or "localhost:11434" in base_url
        or "127.0.0.1:11434" in base_url
    )

    if is_ollama_endpoint:
        available = _ollama_models()
        if not available:
            # Ollama appears to be the intended local backend but is not
            # responding or has no models.
            return False
        if identifier is None:
            return True
        # Ollama names may include tags; compare the base name.
        base_id = identifier.split(":")[0]
        return base_id in available or identifier in available

    # Generic local OpenAI-compatible endpoint.
    if base_url:
        return _probe_url(base_url)

    return False


def _sdk_package_available(package: str) -> bool:
    """Return True when *package* is importable."""
    try:
        __import__(package)
        return True
    except ImportError:
        return False


def _claude_sdk_available() -> bool:
    """Return True when the claude-agent-sdk runtime looks configured."""
    if not _sdk_package_available("claude_agent_sdk"):
        return False
    # The SDK ultimately needs an Anthropic API key.
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _kimi_acp_available() -> bool:
    """Return True when the kimi-acp runtime looks configured."""
    if not _sdk_package_available("acp"):
        return False
    return _has_binary("kimi")


def is_runtime_available(runtime_name: str) -> bool:
    """Return True if the runtime backend appears to be installed/configured.

    This is a coarse check: it answers "could this runtime work?" rather than
    "is a specific model pulled and ready?".  Use *is_model_available* for the
    latter.
    """
    if runtime_name == "kimi-cli":
        return _has_binary("kimi")
    if runtime_name == "claude-cli":
        return _has_binary("claude")
    if runtime_name == "claude-sdk":
        return _claude_sdk_available()
    if runtime_name == "kimi-acp":
        return _kimi_acp_available()
    if runtime_name == "openai-sdk":
        if not _sdk_package_available("openai"):
            return False
        return _openai_sdk_cloud_available() or _openai_sdk_local_available()
    return False


def is_model_available(runtime_name: str, model: ModelCapability) -> bool:
    """Return True if *model* is reachable through *runtime_name* right now.

    The check is provider-aware:

    - CLI runtimes require the vendor binary on PATH.
    - Cloud SDK runtimes require credentials / configured endpoint.
    - Local/Ollama models require the local server to be running and the model
      to be pulled.
    """
    if not is_runtime_available(runtime_name):
        return False

    identifier = model.identifier_for(runtime_name)

    if runtime_name in ("kimi-cli", "claude-cli"):
        return True

    if runtime_name == "claude-sdk":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    if runtime_name == "kimi-acp":
        # ACP requires the kimi binary; we already checked that above.
        return True

    if runtime_name == "openai-sdk":
        provider = model.provider.lower()
        if provider in ("ollama", "local"):
            return _openai_sdk_local_available(identifier)
        return _openai_sdk_cloud_available()

    return False
