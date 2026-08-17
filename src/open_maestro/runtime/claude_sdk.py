"""Claude Agent SDK runtime adapter.

This adapter runs agents via the ``claude-agent-sdk`` Python package, which
enables real async tool interception through the ``can_use_tool`` callback and
streaming event observation.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from open_maestro.config.capabilities import TaskProfile
from open_maestro.config.models import ModelResolver
from open_maestro.events.bus import EventBus
from open_maestro.runtime.base import AgentResult, AgentRuntime

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from open_maestro.runtime.base import AgentConfig

logger = logging.getLogger(__name__)


def _import_sdk() -> Any:
    try:
        import claude_agent_sdk

        return claude_agent_sdk
    except ImportError as exc:
        raise RuntimeError(
            "The 'claude-agent-sdk' package is required for the claude-sdk runtime. "
            "Install it with: pip install claude-agent-sdk"
        ) from exc


class ClaudeSDKRuntime(AgentRuntime):
    """Execute agents via the Claude Agent SDK."""

    def __init__(
        self,
        system_prompt: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._model = model
        self._cwd = cwd
        self._max_turns = max_turns
        self._timeout_seconds = timeout_seconds
        self._extra = extra or {}
        self._resolver = ModelResolver()

    @classmethod
    def from_config(cls, config: AgentConfig) -> ClaudeSDKRuntime:
        return cls(
            system_prompt=config.system_prompt,
            model=config.model,
            cwd=config.cwd,
            max_turns=config.max_turns,
            timeout_seconds=config.timeout_seconds,
            extra=config.extra,
        )

    @property
    def runtime_name(self) -> str:
        return "claude-sdk"

    def is_available(self) -> bool:
        try:
            _import_sdk()
            return True
        except RuntimeError:
            return False

    def _resolve_model(
        self, model: str | None, profile: TaskProfile | None = None
    ) -> str | None:
        return self._resolver.resolve(model, self.runtime_name, profile=profile)

    def _build_options(
        self,
        user_prompt: str,
        config: AgentConfig | None = None,
        *,
        resume_session: str | None = None,
        fork_session: str | None = None,
    ) -> Any:
        sdk = _import_sdk()

        system_prompt = self._system_prompt
        if config is not None and config.system_prompt is not None:
            system_prompt = config.system_prompt

        model = self._model
        if config is not None and config.model is not None:
            model = config.model
        resolved_model = self._resolve_model(
            model, profile=config.task_profile if config else None
        )

        max_turns = self._max_turns
        if config is not None and config.max_turns is not None:
            max_turns = config.max_turns

        options_kwargs: dict[str, Any] = {
            "system_prompt": system_prompt or "",
            "cwd": self._cwd if config is None or config.cwd is None else config.cwd,
            "extra_args": dict(self._extra),
        }

        if resolved_model:
            options_kwargs["model"] = resolved_model
        if max_turns is not None:
            options_kwargs["max_turns"] = max_turns
        if config is not None and config.allowed_tools:
            options_kwargs["allowed_tools"] = list(config.allowed_tools)
        if config is not None and config.blocked_tools:
            options_kwargs["disallowed_tools"] = list(config.blocked_tools)
        if config is not None and config.permission_mode:
            options_kwargs["permission_mode"] = config.permission_mode
        if config is not None and config.mcp_servers:
            options_kwargs["mcp_servers"] = config.mcp_servers
        if resume_session:
            options_kwargs["resume"] = resume_session
        if fork_session:
            options_kwargs["fork_session"] = fork_session

        # Merge any extra SDK-specific options the user provided.
        if config is not None and config.extra:
            for key, value in config.extra.items():
                if key not in ("skills_dirs",):
                    options_kwargs.setdefault(key, value)

        return sdk.ClaudeAgentOptions(**options_kwargs)

    @staticmethod
    def _extract_text(events: list[Any]) -> str:
        parts: list[str] = []
        for event in events:
            text = getattr(event, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_session_id(events: list[Any]) -> str | None:
        for event in reversed(events):
            sid = getattr(event, "session_id", None)
            if sid:
                return str(sid)
        return None

    @staticmethod
    async def _emit_tool_event(event_bus: EventBus, event: Any) -> None:
        tool_name = getattr(event, "tool_name", None) or getattr(
            event, "name", None
        )
        tool_input = getattr(event, "tool_input", None) or getattr(
            event, "input", None
        )
        if tool_name:
            payload: dict[str, Any] = {"tool_name": str(tool_name)}
            if tool_input is not None:
                payload["tool_input"] = tool_input
            await event_bus.emit("tool.call", payload)
            return

        tool_result = getattr(event, "tool_result", None) or getattr(
            event, "output", None
        )
        tool_error = getattr(event, "tool_error", None) or getattr(
            event, "error", None
        )
        if tool_result is not None or tool_error is not None:
            await event_bus.emit(
                "tool.result",
                {
                    "result": tool_result,
                    "error": tool_error,
                },
            )

    async def _execute(
        self,
        prompt: str,
        config: AgentConfig | None = None,
        *,
        resume_session: str | None = None,
        fork_session: str | None = None,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
    ) -> AgentResult:
        sdk = _import_sdk()
        options = self._build_options(
            prompt,
            config,
            resume_session=resume_session,
            fork_session=fork_session,
        )

        if tool_guard is not None:
            options = self._attach_tool_guard(options, tool_guard)

        start = time.monotonic()
        events: list[Any] = []
        event_bus = EventBus()
        try:
            async for event in sdk.query(prompt=prompt, options=options):
                events.append(event)
                await self._emit_tool_event(event_bus, event)
        except Exception as exc:
            logger.exception("Claude SDK query failed")
            return AgentResult(
                text=f"Claude SDK error: {exc}",
                is_error=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        return AgentResult(
            text=self._extract_text(events),
            session_id=self._extract_session_id(events),
            duration_ms=duration_ms,
            metadata={"event_count": len(events)},
        )

    @staticmethod
    def _attach_tool_guard(
        options: Any,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]],
    ) -> Any:
        sdk = _import_sdk()

        async def can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            _context: Any,
        ) -> Any:
            allowed = await tool_guard(tool_name, tool_input)
            if allowed:
                return sdk.PermissionResultAllow()
            return sdk.PermissionResultDeny(
                reason="blocked by Open Maestro tool guard"
            )

        # If the SDK options object is a frozen dataclass, replace it.
        kwargs = {k: getattr(options, k) for k in options.__dataclass_fields__}
        kwargs["can_use_tool"] = can_use_tool
        return sdk.ClaudeAgentOptions(**kwargs)

    async def run(
        self,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._execute(prompt, config)

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
        blocked_tools: set[str] | None = None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        if tool_guard is None and blocked_tools:
            blocked = set(blocked_tools)

            async def guard(tool_name: str, _tool_input: dict[str, Any]) -> bool:
                return tool_name not in blocked

            tool_guard = guard

        return await self._execute(prompt, config, tool_guard=tool_guard)

    async def resume(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._execute(
            prompt, config, resume_session=session_id
        )

    async def fork(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._execute(
            prompt, config, fork_session=session_id
        )
