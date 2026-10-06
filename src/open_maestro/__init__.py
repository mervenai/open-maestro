"""Open Maestro: vendor-agnostic multi-agent orchestration runtime."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: pyproject.toml. (A hardcoded __version__ here
    # went stale — 2.1.3 — through nine pyproject bumps.)
    __version__ = _pkg_version("open-maestro")
except PackageNotFoundError:  # source tree without an installed dist
    __version__ = "0.0.0"

from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime
from open_maestro.runtime.factory import create_runtime, list_runtimes

__all__ = ["AgentConfig", "AgentResult", "AgentRuntime", "create_runtime", "list_runtimes"]
