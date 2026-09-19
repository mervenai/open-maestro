"""OpenAI-compatible API runtime adapter.

Works with OpenAI, Azure OpenAI, Kimi's OpenAI-compatible endpoint, and any
other provider exposing the standard chat-completions API.  This adapter
implements a full tool loop so it can execute Read/Write/Bash/Grep locally and
invoke the async ``tool_guard`` before each tool execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from open_maestro.config.capabilities import CapabilityRegistry, TaskProfile
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

# Total attempts (initial + retries) for one streaming request. Mid-stream
# failures (httpx.ReadTimeout, connection drops) cannot be resumed, so the
# whole request is re-issued.
_MAX_STREAM_ATTEMPTS = 3

_STREAM_RETRY_ERRORS: tuple[type[BaseException], ...] | None = None


def _stream_retry_errors() -> tuple[type[BaseException], ...]:
    """Exception types that justify re-issuing a failed streaming request."""
    global _STREAM_RETRY_ERRORS
    if _STREAM_RETRY_ERRORS is None:
        errors: list[type[BaseException]] = [TimeoutError]
        try:
            import httpx

            errors.extend([httpx.TimeoutException, httpx.TransportError])
        except ImportError:
            pass
        try:
            import openai

            errors.extend(
                [
                    openai.APIConnectionError,
                    openai.RateLimitError,
                    openai.InternalServerError,
                ]
            )
        except ImportError:
            pass
        _STREAM_RETRY_ERRORS = tuple(errors)
    return _STREAM_RETRY_ERRORS


def _client_timeout(timeout_seconds: float | None) -> Any:
    """Timeout policy for the OpenAI client.

    A configured ``timeout_seconds`` is honored as-is. Otherwise use an
    explicit ``httpx.Timeout`` whose read window (30 min) is much larger than
    the OpenAI SDK default (600 s): streaming providers (z.ai, Ollama) can go
    silent for many minutes while generating, and the default per-operation
    read timeout firing mid-stream killed the whole turn.
    """
    if timeout_seconds:
        return timeout_seconds
    import httpx

    return httpx.Timeout(600.0, connect=10.0, read=1800.0)


_OVERFLOW_MARKERS = (
    "max length",
    "maximum length",
    "too long",
    "context length",
    "context window",
    "reduce the length",
    "prompt exceeds",
    "exceeds the",
)


def _looks_like_context_overflow(exc: BaseException) -> bool:
    """Heuristic: does this API error mean the prompt was too long?

    Provider plan tiers can cap request length far below a model's published
    context window (Z.ai's coding plan rejects with code 1261 "Prompt exceeds
    max length"), so a 400 with a length-related message means compaction,
    not a client bug.
    """
    text = str(exc).lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)


def _compact_for_overflow(
    messages: list[dict[str, Any]],
    keep_recent: int = 10,
    max_chars: int = 4000,
) -> list[dict[str, Any]]:
    """Shrink a message list that overflowed the provider's prompt limit.

    System messages and the most recent *keep_recent* messages are kept (with
    any single oversized content head/tail-truncated). Older messages are
    replaced with placeholders; assistant messages lose their ``tool_calls``
    so no orphaned tool-call/tool-result pairing remains.
    """
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "system" or i >= len(messages) - keep_recent:
            m = dict(m)
            content = m.get("content")
            if isinstance(content, str) and len(content) > max_chars:
                half = max_chars // 2
                m["content"] = (
                    content[:half]
                    + f"\n...[middle {len(content) - max_chars} chars "
                    "omitted to fit context]...\n"
                    + content[-half:]
                )
            out.append(m)
            continue
        if role == "tool":
            # Paired assistant message was stripped of tool_calls above.
            continue
        placeholder = dict(m)
        placeholder.pop("tool_calls", None)
        placeholder["content"] = (
            f"[Earlier {role} content omitted to fit context: "
            f"{len(str(m.get('content', '')))} chars]"
        )
        out.append(placeholder)

    # Boundary case: a tool result can fall inside the kept window while its
    # assistant message (one slot older) was dropped. OpenAI-compatible APIs
    # reject such orphaned tool messages, so drop them too.
    live_call_ids = {
        tc.get("id")
        for m in out
        for tc in (m.get("tool_calls") or [])
    }
    return [
        m
        for m in out
        if m.get("role") != "tool" or m.get("tool_call_id") in live_call_ids
    ]


def _import_openai() -> Any:
    try:
        import openai

        return openai
    except ImportError as exc:
        raise RuntimeError(
            "The 'openai' package is required for the openai-sdk runtime. "
            "Install it with: pip install openai"
        ) from exc


def _extract_json_objects_with_spans(text: str) -> list[tuple[int, int, Any]]:
    """Return all top-level JSON objects found in *text* with byte spans."""
    objects: list[tuple[int, int, Any]] = []
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        # Find the matching closing brace, accounting for nested objects and
        # strings. This is intentionally simple: it does not handle escaped
        # braces inside strings perfectly, but json.loads will reject false
        # positives.
        depth = 0
        in_string = False
        escape = False
        start = i
        for j in range(i, len(text)):
            ch = text[j]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        objects.append(
                            (start, j + 1, json.loads(text[start : j + 1]))
                        )
                    except json.JSONDecodeError:
                        pass
                    i = j
                    break
        i += 1
    return objects


def _ollama_host() -> str:
    """Return the Ollama server URL from the environment or the default."""
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    return host


def _ollama_api_base() -> str | None:
    """Return the OpenAI-compatible Ollama base URL if the server is reachable."""
    host = _ollama_host()
    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            f"{host}/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status < 500:
                return f"{host}/v1"
    except Exception:
        pass
    return None


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
        # Auto-detect a local Ollama endpoint when no explicit cloud credentials
        # or base URL are provided. This keeps the runtime consistent with the
        # availability probe that already lets Ollama models be selected.
        if (
            not self._api_key
            and not self._base_url
            and not os.environ.get("OPENAI_API_KEY")
        ):
            ollama_base = _ollama_api_base()
            if ollama_base:
                self._base_url = ollama_base
        self._max_turns = max_turns or 32
        self._timeout_seconds = timeout_seconds
        # api_key and base_url are client-level settings; do not pass them to
        # chat.completions.create() as request kwargs.
        self._extra = {k: v for k, v in (extra or {}).items() if k not in ("api_key", "base_url")}
        self._resolver = ModelResolver()
        self._client: Any | None = None
        # Per-endpoint clients for models that declare their own base URL
        # (e.g. Z.ai), keyed by base_url.
        self._endpoint_clients: dict[str, Any] = {}
        self._registry: CapabilityRegistry | None = None
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
        is required for cloud endpoints.  Local Ollama endpoints are detected
        automatically so that models selected by the availability probe can be
        used without extra configuration.
        """
        import importlib.util

        if importlib.util.find_spec("openai") is None:
            return False
        if (
            getattr(self, "_api_key", None)
            or getattr(self, "_base_url", None)
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENAI_BASE_URL")
        ):
            return True
        from open_maestro.runtime.availability import (
            _openai_sdk_cloud_available,
        )

        return _openai_sdk_cloud_available() or _ollama_api_base() is not None

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
            kwargs["timeout"] = _client_timeout(self._timeout_seconds)
            self._client = openai.AsyncOpenAI(**kwargs)
        return self._client

    def _client_for_model(self, resolved_model: str) -> Any:
        """Return the API client to use for *resolved_model*.

        Models that declare a registry ``endpoint`` (their own base URL and
        API-key env var) get a dedicated client for that endpoint; everything
        else uses the default client built from config/env.
        """
        if self._registry is None:
            try:
                self._registry = CapabilityRegistry.load()
            except Exception as exc:
                logger.debug("Capability registry unavailable: %s", exc)
                self._registry = CapabilityRegistry({})
        entry = self._registry.model_for_identifier(
            self.runtime_name, resolved_model
        )
        endpoint = entry.endpoint if entry is not None else None
        if endpoint is None or endpoint.runtime != self.runtime_name:
            provider = entry.provider.lower() if entry is not None else ""

            def _non_local(url: str | None) -> bool:
                return bool(url) and (
                    "localhost" not in url and "127.0.0.1" not in url
                )

            # Note: self._base_url may hold the Ollama endpoint autodetected
            # in __init__; a loopback URL is not a generic credential.
            has_generic_creds = bool(
                self._api_key
                or os.environ.get("OPENAI_API_KEY")
                or _non_local(self._base_url)
                or _non_local(os.environ.get("OPENAI_BASE_URL"))
            )
            if provider not in ("ollama", "local") and not has_generic_creds:
                # Without generic credentials the default client autodetects
                # the local Ollama endpoint, where this cloud model name does
                # not exist — surfacing later as a cryptic 404 per request.
                raise RuntimeError(
                    f"Model '{resolved_model}' has no configured endpoint and no "
                    "OPENAI_API_KEY/OPENAI_BASE_URL is set. Declare an endpoint "
                    "for it in the capabilities registry, set generic OpenAI "
                    "credentials, or pick a different model."
                )
            return self._ensure_client()

        client = self._endpoint_clients.get(endpoint.base_url)
        if client is None:
            openai = _import_openai()
            kwargs: dict[str, Any] = {
                "api_key": os.environ.get(endpoint.api_key_env, "maestro"),
                "base_url": endpoint.base_url,
            }
            kwargs["timeout"] = _client_timeout(self._timeout_seconds)
            client = openai.AsyncOpenAI(**kwargs)
            self._endpoint_clients[endpoint.base_url] = client
        return client

    def _resolve_model(
        self, model: str | None, profile: TaskProfile | None = None
    ) -> str | None:
        return self._resolver.resolve(model, self.runtime_name, profile=profile)

    def _estimate_cost(
        self, resolved_model: str, input_tokens: int, output_tokens: int
    ) -> float | None:
        """Estimate USD cost from per-model prices in the capability registry.

        *input_tokens* should be the latest turn's prompt-token count (each
        turn re-sends the conversation, so summing across turns would
        double-count). Returns ``None`` when no price is known.
        """
        if self._registry is None:
            return None
        entry = self._registry.model_for_identifier(
            self.runtime_name, resolved_model
        )
        caps = entry.capabilities if entry is not None else None
        if caps is None or (
            caps.price_input_per_million is None
            and caps.price_output_per_million is None
        ):
            return None
        cost = 0.0
        if caps.price_input_per_million is not None:
            cost += input_tokens / 1_000_000 * caps.price_input_per_million
        if caps.price_output_per_million is not None:
            cost += output_tokens / 1_000_000 * caps.price_output_per_million
        return round(cost, 6)

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

        client = self._client_for_model(resolved)
        messages = self._build_messages(prompt, config)
        tool_schemas, tool_map = self._select_tools(config, extra_tools=extra_tools)

        start = time.monotonic()
        tool_calls_record: list[dict[str, Any]] = []
        total_output_tokens = 0
        # Per-turn prompt_tokens includes the whole conversation, so keep the
        # latest turn's count for cost/context accounting (summing would
        # double-count earlier turns).
        last_input_tokens = 0
        turns = 0

        try:
            while turns < self._max_turns:
                turns += 1
                nudge_turn = max(2, int(self._max_turns * 0.75))
                if turns == nudge_turn:
                    # The model cannot see our turn counter; without a nudge it
                    # often keeps exploring right up to the cap and gets cut
                    # off mid-task with no final artifact.
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                f"Turn budget: you have used about {turns} of "
                                f"{self._max_turns} tool turns. Stop exploring "
                                "now and produce your final response or write "
                                "the requested artifact using what you have."
                            ),
                        }
                    )
                kwargs: dict[str, Any] = dict(self._extra)
                if tool_schemas:
                    kwargs["tools"] = tool_schemas
                    kwargs["tool_choice"] = "auto"

                turn_start = time.monotonic()
                preview_state: dict[str, str] = {"text": ""}
                heartbeat = asyncio.create_task(
                    self._heartbeat(turn_start, preview_state=preview_state)
                )
                try:
                    outcome: dict[str, Any] | None = None
                    compacted = False
                    for attempt in range(1, _MAX_STREAM_ATTEMPTS + 1):
                        try:
                            outcome = await self._consume_stream(
                                client,
                                resolved,
                                messages,
                                kwargs,
                                preview_state,
                            )
                            break
                        except _stream_retry_errors() as exc:
                            if attempt >= _MAX_STREAM_ATTEMPTS:
                                raise
                            detail = str(exc).strip() or type(exc).__name__
                            logger.warning(
                                "OpenAI stream attempt %d/%d failed (%s); retrying",
                                attempt,
                                _MAX_STREAM_ATTEMPTS,
                                detail,
                            )
                            await asyncio.sleep(min(2 * attempt, 10))
                        except Exception as exc:
                            if _looks_like_context_overflow(exc) and not compacted:
                                compacted = True
                                logger.warning(
                                    "Prompt too long for %s; compacting "
                                    "conversation and retrying once",
                                    resolved,
                                )
                                messages[:] = _compact_for_overflow(messages)
                                continue
                            raise
                    if outcome is None:
                        raise RuntimeError(
                            f"stream retries exhausted for model '{resolved}'"
                        )
                    # The loop either sets outcome or raises.
                    outcome = outcome or {}
                    accumulated_content = outcome.get("content", "")
                    tool_buffers = outcome.get("tool_buffers", {})
                    emitted_tool_indices = outcome.get(
                        "emitted_tool_indices", set()
                    )
                    finish_reason = outcome.get("finish_reason")
                    total_output_tokens += outcome.get("completion_tokens", 0)
                    last_input_tokens = outcome.get("prompt_tokens", 0)
                finally:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except asyncio.CancelledError:
                        pass

                # Fallback: some local endpoints (Ollama) return tool calls as
                # plain text in the assistant's content instead of structured
                # tool_calls deltas. Parse those and synthesise tool buffers,
                # stripping the raw JSON from the content so the model does not
                # loop on its own emitted JSON in later turns.
                if not tool_buffers and accumulated_content:
                    extracted, cleaned_content = self._extract_tool_calls_from_content(
                        accumulated_content
                    )
                    if extracted:
                        accumulated_content = cleaned_content
                    for ext_idx, ext in enumerate(extracted):
                        arguments = ext.get("arguments", {})
                        if isinstance(arguments, str):
                            try:
                                arguments = json.loads(arguments)
                            except json.JSONDecodeError:
                                arguments = {"raw": arguments}
                        tool_buffers[ext_idx] = {
                            "id": f"extracted-{ext_idx}",
                            "type": "function",
                            "function": {
                                "name": ext["name"],
                                "arguments": json.dumps(arguments),
                            },
                            "name_emitted": False,
                        }

                if not tool_buffers:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    return AgentResult(
                        text=accumulated_content or "",
                        session_id=None,
                        cost_usd=self._estimate_cost(
                            resolved, last_input_tokens, total_output_tokens
                        ),
                        num_turns=turns,
                        duration_ms=duration_ms,
                        input_tokens=last_input_tokens,
                        output_tokens=total_output_tokens,
                        tokens_used=last_input_tokens + total_output_tokens,
                        tool_calls=tool_calls_record,
                        metadata={"finish_reason": finish_reason},
                    )

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": accumulated_content or None,
                    "tool_calls": [
                        {
                            "id": buf["id"],
                            "type": buf.get("type", "function"),
                            "function": {
                                "name": buf["function"]["name"],
                                "arguments": buf["function"]["arguments"],
                            },
                        }
                        for idx, buf in sorted(tool_buffers.items())
                    ],
                }
                messages.append(assistant_message)

                for idx, buf in sorted(tool_buffers.items()):
                    tool_name = buf["function"]["name"]
                    tool_input = parse_tool_input(buf["function"]["arguments"])
                    tool_calls_record.append(
                        {
                            "id": buf["id"],
                            "name": tool_name,
                            "input": tool_input,
                        }
                    )
                    if idx not in emitted_tool_indices:
                        await self._emit_tool_call(tool_name, tool_input)
                        emitted_tool_indices.add(idx)

                    result_text = await self._execute_tool(
                        tool_name, tool_input, tool_map, tool_guard
                    )
                    await self._event_bus.emit(
                        "tool.result",
                        {
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                            "allowed": not result_text.startswith(
                                "Error: use of tool"
                            ),
                            "result": result_text,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": buf["id"],
                            "content": result_text,
                        }
                    )

            # Exceeded max turns.
            duration_ms = int((time.monotonic() - start) * 1000)
            return AgentResult(
                text="Reached the maximum number of tool turns without a final response.",
                duration_ms=duration_ms,
                num_turns=turns,
                cost_usd=self._estimate_cost(
                    resolved, last_input_tokens, total_output_tokens
                ),
                input_tokens=last_input_tokens,
                output_tokens=total_output_tokens,
                tokens_used=last_input_tokens + total_output_tokens,
                tool_calls=tool_calls_record,
                is_error=True,
            )

        except Exception as exc:
            logger.exception("OpenAI API call failed")
            detail = str(exc).strip() or type(exc).__name__
            return AgentResult(
                text=f"OpenAI API error: {detail}",
                is_error=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )

    async def _consume_stream(
        self,
        client: Any,
        resolved: str,
        messages: list[dict[str, Any]],
        kwargs: dict[str, Any],
        preview_state: dict[str, str],
    ) -> dict[str, Any]:
        """Issue one streaming request and consume it to completion.

        Called by the retry loop in ``_run_tool_loop``: a stream that dies
        mid-flight cannot be resumed, so on failure the caller re-issues the
        whole request. Does not mutate *messages* or *kwargs*.
        """
        stream = await client.chat.completions.create(
            model=resolved,
            messages=messages,
            stream=True,
            **kwargs,
        )
        accumulated_content = ""
        tool_buffers: dict[int, dict[str, Any]] = {}
        emitted_tool_indices: set[int] = set()
        finish_reason: str | None = None
        prompt_tokens = 0
        completion_tokens = 0

        def _update_preview(text: str) -> None:
            snippet = text.strip().replace("\n", " ")
            if len(snippet) > 70:
                snippet = snippet[:67].rstrip() + "..."
            preview_state["text"] = snippet

        async for chunk in stream:
            # Some endpoints emit usage on the final chunk.
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0

            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                accumulated_content += delta.content
                _update_preview(accumulated_content)

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_buffers:
                        tool_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                            "name_emitted": False,
                        }
                    func = tool_buffers[idx]["function"]
                    if tc_delta.id:
                        tool_buffers[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            func["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            func["arguments"] += tc_delta.function.arguments

                    # Emit a tool.call event as soon as we know the
                    # tool name, so the user sees interim flow
                    # before the (possibly slow) arguments finish.
                    if func["name"] and not tool_buffers[idx]["name_emitted"]:
                        await self._emit_tool_call(func["name"], {})
                        tool_buffers[idx]["name_emitted"] = True

                    # Emit again once the arguments parse as
                    # complete JSON, so the detail (path, pattern,
                    # etc.) appears.
                    if (
                        idx not in emitted_tool_indices
                        and func["name"]
                        and func["arguments"]
                    ):
                        try:
                            tool_input = parse_tool_input(func["arguments"])
                            await self._emit_tool_call(func["name"], tool_input)
                            emitted_tool_indices.add(idx)
                        except Exception:
                            pass

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        return {
            "content": accumulated_content,
            "tool_buffers": tool_buffers,
            "emitted_tool_indices": emitted_tool_indices,
            "finish_reason": finish_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    async def _emit_tool_call(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> None:
        """Emit a tool.call event so the UI can show interim progress."""
        await self._event_bus.emit(
            "tool.call",
            {"tool_name": tool_name, "tool_input": tool_input},
        )

    @staticmethod
    def _extract_tool_calls_from_content(
        content: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Parse tool calls embedded in the assistant's content text.

        Some local endpoints (notably Ollama) return tool calls as plain text
        rather than as structured ``tool_calls`` deltas.  We scan the text for
        JSON objects with ``name`` and ``arguments`` keys and synthesise
        OpenAI-style tool-call records so the tool loop can execute them.

        Returns a tuple of (tool_calls, cleaned_content).  The cleaned content
        has the raw JSON tool-call blobs removed so the model does not see its
        own emitted JSON on subsequent turns and loop forever.
        """
        if not content:
            return [], content

        text = content.strip()
        original_text = text

        # If the content is wrapped in markdown fences, strip them.
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) > 2 and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Try parsing the whole block as JSON first.
        data: Any = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass

        if isinstance(data, dict):
            if "name" in data and "arguments" in data:
                return [data], ""
            for key in ("tool_calls", "calls", "tools"):
                if key in data and isinstance(data[key], list):
                    return (
                        [
                            item
                            for item in data[key]
                            if isinstance(item, dict) and "name" in item and "arguments" in item
                        ],
                        "",
                    )

        # Fall back to scanning for individual JSON objects. This handles text
        # that contains explanatory prose mixed with one or more tool-call JSON
        # blobs.
        results: list[dict[str, Any]] = []
        removed_spans: list[tuple[int, int]] = []
        for start, end, candidate in _extract_json_objects_with_spans(text):
            if isinstance(candidate, dict) and "name" in candidate and "arguments" in candidate:
                results.append(candidate)
                removed_spans.append((start, end))

        if not results:
            return [], original_text

        # Build cleaned content by removing the JSON blobs and normalising
        # whitespace. Preserve the original text order.
        cleaned_parts: list[str] = []
        last_end = 0
        for start, end in sorted(removed_spans):
            if start > last_end:
                cleaned_parts.append(text[last_end:start])
            last_end = end
        if last_end < len(text):
            cleaned_parts.append(text[last_end:])
        cleaned = "\n".join(part.strip() for part in cleaned_parts if part.strip())
        return results, cleaned

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_map: dict[str, Any],
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None,
    ) -> str:
        """Run a single tool, honouring the optional tool_guard."""
        allowed = True
        if tool_guard is not None:
            try:
                allowed = await tool_guard(tool_name, tool_input)
            except Exception as exc:
                logger.warning("tool_guard raised %s; denying tool", exc)
                allowed = False

        tool = tool_map.get(tool_name)
        if tool is None:
            return f"Error: tool '{tool_name}' is not available."
        if not allowed:
            return (
                f"Error: use of tool '{tool_name}' was denied by the orchestrator. "
                "Stop and ask the user how to proceed."
            )
        try:
            return await tool.execute(**tool_input)
        except Exception as exc:
            logger.warning("Tool %s failed: %s", tool_name, exc)
            return f"Error executing {tool_name}: {exc}"

    async def _heartbeat(
        self,
        start: float,
        interval: float = 5.0,
        preview_state: dict[str, str] | None = None,
    ) -> None:
        """Emit periodic runtime.working events while waiting for the LLM.

        If *preview_state* contains a non-empty text snippet from the streaming
        response, it is forwarded so the UI can show what the model is currently
        generating instead of a generic spinner.
        """
        while True:
            await asyncio.sleep(interval)
            duration_ms = int((time.monotonic() - start) * 1000)
            payload: dict[str, Any] = {"duration_ms": duration_ms}
            if preview_state and preview_state.get("text"):
                payload["message"] = preview_state["text"]
            await self._event_bus.emit("runtime.working", payload)

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
