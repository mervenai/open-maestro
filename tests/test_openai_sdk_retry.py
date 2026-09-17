"""Tests for openai-sdk stream retry and timeout policy."""

from __future__ import annotations

import types
from typing import Any

import httpx
import pytest

from open_maestro.runtime.openai_sdk import (
    OpenAISDKRuntime,
    _client_timeout,
    _MAX_STREAM_ATTEMPTS,
)


def _chunk(content: str = "", finish_reason: str | None = None) -> Any:
    return types.SimpleNamespace(
        usage=None,
        choices=[
            types.SimpleNamespace(
                delta=types.SimpleNamespace(content=content, tool_calls=None),
                finish_reason=finish_reason,
            )
        ],
    )


class _FakeStream:
    """Async iterator over events; an ("raise", exc) event raises mid-stream."""

    def __init__(self, events: list[Any]):
        self._events = events

    def __aiter__(self) -> "_FakeStream":
        self._it = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            event = next(self._it)
        except StopIteration:
            raise StopAsyncIteration
        if isinstance(event, tuple) and event[0] == "raise":
            raise event[1]
        return event


class _FakeCompletions:
    def __init__(self, behaviors: list[Any]):
        # Each behavior: an exception (raised by create) or a list of events.
        self._behaviors = list(behaviors)
        self.calls = 0

    async def create(self, **kwargs: Any) -> _FakeStream:
        self.calls += 1
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeStream(behavior)


def _runtime(completions: _FakeCompletions) -> OpenAISDKRuntime:
    rt = OpenAISDKRuntime(model="fake-model")
    rt._client_for_model = lambda resolved: types.SimpleNamespace(  # type: ignore[method-assign]
        chat=types.SimpleNamespace(completions=completions)
    )
    rt._resolve_model = lambda model, profile=None: "fake-model"  # type: ignore[method-assign]
    rt._build_messages = lambda prompt, config: [{"role": "user", "content": prompt}]  # type: ignore[method-assign]
    rt._select_tools = lambda config, extra_tools=None: ([], {})  # type: ignore[method-assign]
    return rt


class TestClientTimeout:
    def test_configured_timeout_honored(self):
        assert _client_timeout(12.5) == 12.5

    def test_default_has_longer_read_window(self):
        timeout = _client_timeout(None)
        assert timeout.read == 1800.0
        assert timeout.connect == 10.0


class TestStreamRetry:
    async def test_midstream_timeout_retries_and_recovers(self):
        completions = _FakeCompletions(
            [
                [("raise", httpx.ReadTimeout("timed out"))],
                [_chunk("hello "), _chunk("world", finish_reason="stop")],
            ]
        )
        rt = _runtime(completions)
        result = await rt.run("do something")
        assert result.text == "hello world"
        assert not result.is_error
        assert completions.calls == 2

    async def test_exhausts_attempts_and_reports_error(self):
        behaviors: list[Any] = [
            [("raise", httpx.ReadTimeout("timed out"))]
            for _ in range(_MAX_STREAM_ATTEMPTS)
        ]
        completions = _FakeCompletions(behaviors)
        rt = _runtime(completions)
        result = await rt.run("do something")
        assert result.is_error
        assert "timed out" in result.text
        assert completions.calls == _MAX_STREAM_ATTEMPTS

    async def test_create_time_error_also_retried(self):
        completions = _FakeCompletions(
            [
                httpx.ConnectError("connection refused"),
                [_chunk("ok", finish_reason="stop")],
            ]
        )
        rt = _runtime(completions)
        result = await rt.run("do something")
        assert result.text == "ok"
        assert completions.calls == 2


class TestClientForModelGuard:
    def test_endpointless_cloud_model_without_creds_raises_clear_error(self):
        rt = OpenAISDKRuntime(model="gpt-4o-mini")
        with pytest.raises(RuntimeError, match="no configured endpoint"):
            rt._client_for_model("gpt-4o-mini")

    def test_ollama_model_still_uses_local_client(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        rt = OpenAISDKRuntime(model="qwen2.5-coder:32b")
        monkeypatch.setattr(
            "open_maestro.runtime.openai_sdk._ollama_api_base",
            lambda: "http://localhost:11434/v1",
        )
        client = rt._client_for_model("qwen2.5-coder:32b")
        assert client is not None
