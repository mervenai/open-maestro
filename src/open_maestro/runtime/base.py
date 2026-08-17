"""Vendor-neutral runtime abstraction.

The ``AgentRuntime`` ABC is the heart of Open Maestro.  Every concrete backend
(CLI subprocess, SDK, HTTP API) implements this interface so orchestration code
can remain independent of the underlying model provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from open_maestro.config.capabilities import RequiredCapabilities, TaskProfile


@dataclass
class AgentResult:
    """Runtime-agnostic result from an agent execution."""

    text: str
    session_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tokens_used: int | None = None
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Runtime-agnostic agent configuration.

    Fields are intentionally generic.  Each runtime adapter maps these to
    provider-specific options (``--model``, ``model=``, ``messages=``, etc.).
    """

    system_prompt: str | None = None
    model: str | None = None
    allowed_tools: list[str] | None = None
    blocked_tools: set[str] | None = None
    permission_mode: str | None = None
    cwd: str | None = None
    max_turns: int | None = None
    max_budget_usd: float | None = None
    mcp_servers: dict[str, Any] | None = None
    timeout_seconds: float | None = None
    task_profile: TaskProfile | None = None
    required_capabilities: RequiredCapabilities | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AgentRuntime(ABC):
    """Abstract interface for agent execution backends."""

    @property
    @abstractmethod
    def runtime_name(self) -> str:
        """Return the runtime identifier (e.g., ``'kimi-cli'``, ``'claude-cli'``)."""
        ...

    @abstractmethod
    async def run(
        self,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        """Execute a one-shot prompt."""
        ...

    @abstractmethod
    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
        blocked_tools: set[str] | None = None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        """Execute with optional tool interception.

        Backends that cannot intercept tools should either raise
        ``NotImplementedError`` or fall back to ``run()`` while honouring
        ``blocked_tools`` via the system prompt or tool allow-list.
        """
        ...

    @abstractmethod
    async def resume(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        """Resume a previous session."""
        ...

    async def fork(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        """Fork from a previous session.

        Default implementation raises ``NotImplementedError``; runtimes that
        support branching history can override.
        """
        raise NotImplementedError(f"{self.runtime_name} does not support fork()")

    def is_available(self) -> bool:
        """Return True if the backend binary/SDK is installed and usable."""
        return True
