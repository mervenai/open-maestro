"""OpenAI-compatible API runtime adapter.

Works with OpenAI, Azure OpenAI, Kimi's OpenAI-compatible endpoint, and any
other provider exposing the standard chat-completions API.  This adapter
implements a full tool loop so it can execute Read/Write/Bash/Grep locally and
invoke the async ``tool_guard`` before each tool execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from open_maestro.config.capabilities import TaskProfile
from open_maestro.config.models import ModelResolver
from open_maestro.events.bus import EventBus
from open_maestro.mcp.client import MCPClient
from open_maestro.runtime.base import AgentResult, AgentRuntime
from open_maestro.runtime.tools import ToolRegistry, parse_tool_input

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from openai.types.chat import ChatCompletionMessage

    from open_maestro.runtime.base import AgentConfig

logger = logging.getLogger(__name__)


def _import_openai() -> Any:
    try:
        import openai

        return openai
    except ImportError as exc:
        raise RuntimeError(
            "The 'openai' package is required for the openai-sdk runtime. "
            "Install it with: pip install openai"
        ) from exc


class OpenAISDKRuntime(AgentRuntime):
    """Execute agents via an OpenAI-compatible HTTP API with local tool support."""

    def __init__(
        self,
        system_prompt: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: float | None = None,
        extra: dict[str, Any] | None = None,
        tool_registry: ToolRegistry | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._model = model
        self._api_key = api_key
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self._max_turns = max_turns or 32
        self._timeout_seconds = timeout_seconds
        # api_key and base_url are client-level settings; do not pass them to
        # chat.completions.create() as request kwargs.
        self._extra = {k: v for k, v in (extra or {}).items() if k not in ("api_key", "base_url")}
        self._resolver = ModelResolver()
        self._client: Any | None = None
        self._tool_registry = tool_registry or ToolRegistry.default()
        self._event_bus = event_bus or EventBus()

    @classmethod
    def from_config(cls, config: AgentConfig) -> OpenAISDKRuntime:
        return cls(
            system_prompt=config.system_prompt,
            model=config.model,
            api_key=(config.extra or {}).get("api_key"),
            base_url=(config.extra or {}).get("base_url"),
            max_turns=config.max_turns,
            timeout_seconds=config.timeout_seconds,
            extra=config.extra,
        )

    @property
    def runtime_name(self) -> str:
        return "openai-sdk"

    def is_available(self) -> bool:
        """Available when the OpenAI package is installed and credentials/endpoint are set.

        An explicit API key (``OPENAI_API_KEY``) or base URL (``OPENAI_BASE_URL``)
        is required.  Local Ollama detection is handled by the availability module
        so that runtime detection stays hermetic in tests.
        """
        import importlib.util

        if importlib.util.find_spec("openai") is None:
            return False
        return bool(
            getattr(self, "_api_key", None)
            or getattr(self, "_base_url", None)
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENAI_BASE_URL")
        )

    def _ensure_client(self) -> Any:
        if self._client is None:
            openai = _import_openai()
            kwargs: dict[str, Any] = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            if self._base_url:
                kwargs["base_url"] = self._base_url
            # Local OpenAI-compatible endpoints (Ollama, vLLM, LM Studio) do not
            # require a real API key, but the OpenAI client refuses to initialize
            # without one.  Pass a placeholder when we are targeting localhost.
            if not self._api_key and self._base_url and (
                "localhost" in self._base_url or "127.0.0.1" in self._base_url
            ):
                kwargs["api_key"] = "not-needed"
            if self._timeout_seconds:
                kwargs["timeout"] = self._timeout_seconds
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    def _resolve_model(
        self, model: str | None, profile: TaskProfile | None = None
    ) -> str | None:
        return self._resolver.resolve(model, self.runtime_name, profile=profile)

    def _build_messages(
        self, prompt: str, config: AgentConfig | None = None
    ) -> list[dict[str, Any]]:
        system_prompt = self._system_prompt
        if config is not None and config.system_prompt is not None:
            system_prompt = config.system_prompt

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _select_tools(
        self,
        config: AgentConfig | None = None,
        extra_tools: list[Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return OpenAI tool schemas and a name->tool mapping."""
        allowed = None
        blocked: set[str] = set()
        if config is not None:
            if config.allowed_tools:
                allowed = set(config.allowed_tools)
            if config.blocked_tools:
                blocked = set(config.blocked_tools)

        tools = self._tool_registry.filter(allowed=allowed, blocked=blocked)
        if extra_tools:
            tools = tools + [
                tool
                for tool in extra_tools
                if (allowed is None or tool.name in allowed)
                and tool.name not in blocked
            ]
        schemas = [tool.to_openai_schema() for tool in tools]
        mapping = {tool.name: tool for tool in tools}
        return schemas, mapping

    @staticmethod
    def _message_to_dict(message: ChatCompletionMessage) -> dict[str, Any]:
        """Convert a ChatCompletionMessage to a serialisable dict."""
        result: dict[str, Any] = {
            "role": message.role,
            "content": message.content,
        }
        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        return result

    async def run(
        self,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._run_with_tools(prompt, config=config)

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
        blocked_tools: set[str] | None = None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        if blocked_tools and config is not None:
            existing = set(config.blocked_tools or set())
            config = self._replace_config(config, blocked_tools=existing | set(blocked_tools))

        return await self._run_with_tools(prompt, config=config, tool_guard=tool_guard)

    async def _run_with_tools(
        self,
        prompt: str,
        config: AgentConfig | None = None,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
    ) -> AgentResult:
        mcp_servers = (config.mcp_servers if config is not None else None) or {}
        mcp_server_list = mcp_servers.get("mcpServers", mcp_servers)

        if mcp_server_list:
            try:
                async with MCPClient(mcp_server_list) as mcp_client:
                    return await self._run_tool_loop(
                        prompt,
                        config=config,
                        tool_guard=tool_guard,
                        extra_tools=mcp_client.list_tools(),
                    )
            except RuntimeError as exc:
                logger.warning("MCP client unavailable: %s", exc)
                return AgentResult(
                    text=f"MCP setup failed: {exc}",
                    is_error=True,
                )

        return await self._run_tool_loop(
            prompt, config=config, tool_guard=tool_guard
        )

    async def _run_tool_loop(
        self,
        prompt: str,
        config: AgentConfig | None = None,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
        extra_tools: list[Any] | None = None,
    ) -> AgentResult:
        model = self._model
        if config is not None and config.model is not None:
            model = config.model
        profile = config.task_profile if config is not None else None
        resolved = self._resolve_model(model, profile=profile)
        if not resolved or resolved.lower() == "default":
            # Fall back to the best registry match for this runtime/profile,
            # then to the hardcoded alias table, before giving up.
            resolved = (
                self._resolver.select_for_task(self.runtime_name, profile)
                or "gpt-4o"
            )

        client = self._ensure_client()
        messages = self._build_messages(prompt, config)
        tool_schemas, tool_map = self._select_tools(config, extra_tools=extra_tools)

        start = time.monotonic()
        tool_calls_record: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        turns = 0

        try:
            while turns < self._max_turns:
                turns += 1
                kwargs: dict[str, Any] = dict(self._extra)
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                    kwargs["tool_choice"] = "auto"

                turn_start = time.monotonic()
                heartbeat = asyncio.create_task(self._heartbeat(turn_start))
                try:
                    response = await client.chat.completions.create(
                        model=resolved,
                        messages=messages,
                        **kwargs,
                    )
                finally:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except asyncio.CancelledError:
                        pass

                choice = response.choices[0]
                message = choice.message
                usage = getattr(response, "usage", None)
                if usage:
                    total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    total_output_tokens += getattr(usage, "completion_tokens", 0) or 0

                if not message.tool_calls:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    return AgentResult(
                        text=message.content or "",
                        session_id=getattr(response, "id", None),
                        cost_usd=self._extract_cost(response),
                        num_turns=turns,
                        duration_ms=duration_ms,
                        input_tokens=total_input_tokens,
                        output_tokens=total_output_tokens,
                        tokens_used=total_input_tokens + total_output_tokens,
                        tool_calls=tool_calls_record,
                        metadata={"finish_reason": choice.finish_reason},
                    )

                messages.append(self._message_to_dict(message))

                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_input = parse_tool_input(tool_call.function.arguments)
                    tool_calls_record.append(
                        {
                            "id": tool_call.id,
                            "name": tool_name,
                            "input": tool_input,
                        }
                    )
                    await self._event_bus.emit(
                        "tool.call",
                        {"tool_name": tool_name, "tool_input": tool_input},
                    )

                    allowed = True
                    if tool_guard is not None:
                        try:
                            allowed = await tool_guard(tool_name, tool_input)
                        except Exception as exc:
                            logger.warning("tool_guard raised %s; denying tool", exc)
                            allowed = False

                    tool = tool_map.get(tool_name)
                    if tool is None:
                        result_text = f"Error: tool '{tool_name}' is not available."
                    elif not allowed:
                        result_text = (
                            f"Error: use of tool '{tool_name}' was denied by the orchestrator. "
                            "Stop and ask the user how to proceed."
                        )
                    else:
                        try:
                            result_text = await tool.execute(**tool_input)
                        except Exception as exc:
                            logger.warning("Tool %s failed: %s", tool_name, exc)
                            result_text = f"Error executing {tool_name}: {exc}"

                    await self._event_bus.emit(
                        "tool.result",
                        {
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "allowed": allowed,
                            "result": result_text,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result_text,
                        }
                    )

            # Exceeded max turns.
            duration_ms = int((time.monotonic() - start) * 1000)
            return AgentResult(
                text="Reached the maximum number of tool turns without a final response.",
                duration_ms=duration_ms,
                num_turns=turns,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                tokens_used=total_input_tokens + total_output_tokens,
                tool_calls=tool_calls_record,
                is_error=True,
            )

        except Exception as exc:
            logger.exception("OpenAI API call failed")
            return AgentResult(
                text=f"OpenAI API error: {exc}",
                is_error=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def _heartbeat(self, start: float, interval: float = 30.0) -> None:
        """Emit periodic runtime.working events while waiting for the LLM."""
        while True:
            await asyncio.sleep(interval)
            duration_ms = int((time.monotonic() - start) * 1000)
            await self._event_bus.emit(
                "runtime.working", {"duration_ms": duration_ms}
            )

    async def resume(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        """The OpenAI API is stateless; session_id is ignored."""
        return await self.run(prompt, config=config)

    def _replace_config(
        self,
        config: AgentConfig,
        *,
        blocked_tools: set[str],
    ) -> AgentConfig:
        from dataclasses import replace

        return replace(config, blocked_tools=blocked_tools)

    @staticmethod
    def _extract_cost(response: Any) -> float | None:
        usage = getattr(response, "usage", None)
        if not usage:
            return None
        return getattr(usage, "total_cost", None)
