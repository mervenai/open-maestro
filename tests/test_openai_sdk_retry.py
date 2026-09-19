"""Tests for openai-sdk stream retry and timeout policy."""

from __future__ import annotations

import types
from typing import Any

import httpx
import openai
import pytest

from open_maestro.runtime.openai_sdk import (
    OpenAISDKRuntime,
    _client_timeout,
    _compact_for_overflow,
    _looks_like_context_overflow,
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


class TestTurnBudgetNudge:
    async def test_nudge_appended_near_cap(self):
        """At ~75% of max_turns a system nudge must tell the model to wrap up."""
        rt = OpenAISDKRuntime(model="fake-model", max_turns=4)
        rt._client_for_model = lambda resolved: object()  # type: ignore[method-assign]
        rt._resolve_model = lambda model, profile=None: "fake-model"  # type: ignore[method-assign]
        rt._build_messages = lambda prompt, config: [{"role": "user", "content": prompt}]  # type: ignore[method-assign]
        rt._select_tools = lambda config, extra_tools=None: ([], {})  # type: ignore[method-assign]

        buffer = {
            "id": "t1",
            "type": "function",
            "function": {"name": "noop", "arguments": "{}"},
        }

        async def _fake_consume(client, resolved, messages, kwargs, preview_state):
            return {
                "content": "",
                "tool_buffers": {0: dict(buffer)},
                "emitted_tool_indices": {0},
                "finish_reason": "tool_calls",
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }

        seen_messages: list = []

        async def _spy_consume(client, resolved, messages, kwargs, preview_state):
            seen_messages.append(messages)
            return await _fake_consume(
                client, resolved, messages, kwargs, preview_state
            )

        rt._consume_stream = _spy_consume  # type: ignore[method-assign]
        rt._execute_tool = lambda *a, **k: _ok()  # type: ignore[method-assign]

        async def _ok():
            return "ok"

        result = await rt.run("keep going")
        assert result.is_error  # hits max turns by design
        assert "maximum number of tool turns" in result.text
        nudges = [
            m
            for m in seen_messages[-1]
            if m["role"] == "system" and "Turn budget" in str(m.get("content", ""))
        ]
        assert len(nudges) == 1, "expected exactly one turn-budget nudge"

    async def test_no_nudge_when_finishing_early(self):
        completions = _FakeCompletions([[_chunk("done", finish_reason="stop")]])
        rt = _runtime(completions)
        rt._max_turns = 8
        result = await rt.run("quick task")
        assert result.text == "done"


def _bad_request(message: str) -> openai.BadRequestError:
    return openai.BadRequestError(
        f"Error code: 400 - {{'error': {{'message': '{message}'}}}}",
        response=httpx.Response(
            400, request=httpx.Request("POST", "http://x/v1/chat/completions")
        ),
        body={"error": {"message": message}},
    )


class TestContextOverflow:
    def test_overflow_marker_detection(self):
        assert _looks_like_context_overflow(
            _bad_request("Prompt exceeds max length")
        )
        assert _looks_like_context_overflow(
            Exception("This model's maximum context length is 131072 tokens")
        )
        assert not _looks_like_context_overflow(
            _bad_request("model 'qwen-max' not found")
        )

    def test_compaction_placeholders_old_and_drops_orphans(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old question"},
            {
                "role": "assistant",
                "content": "old answer",
                "tool_calls": [{"id": "c1"}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result"},
            {"role": "user", "content": "recent"},
        ]
        out = _compact_for_overflow(msgs, keep_recent=1)
        assert out[0] == {"role": "system", "content": "sys"}
        assert [m["role"] for m in out] == ["system", "user", "assistant", "user"]
        assert "omitted" in out[1]["content"]
        assert "omitted" in out[2]["content"]
        assert "tool_calls" not in out[2]
        assert out[3] == {"role": "user", "content": "recent"}

    def test_compaction_boundary_orphan_tool_dropped(self):
        """Tool result inside the keep window whose caller assistant fell
        outside it must be dropped (OpenAI-compatible APIs reject orphans)."""
        msgs = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1"}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "r"},
            {"role": "user", "content": "u"},
        ]
        out = _compact_for_overflow(msgs, keep_recent=2)
        assert [m["role"] for m in out] == ["assistant", "user"]

    def test_compaction_truncates_oversized_kept_content(self):
        msgs = [{"role": "user", "content": "x" * 10_000}]
        out = _compact_for_overflow(msgs, keep_recent=1, max_chars=4000)
        assert len(out[0]["content"]) < 4500
        assert "omitted" in out[0]["content"]

    async def test_overflow_error_compacts_and_retries_once(self):
        completions = _FakeCompletions([[_chunk("done", finish_reason="stop")]])
        rt = _runtime(completions)
        rt._build_messages = lambda prompt, config: [  # type: ignore[method-assign]
            {"role": "system", "content": "sys"},
            *[
                {"role": "user", "content": f"old {i} " + "x" * 500}
                for i in range(20)
            ],
            {"role": "user", "content": prompt},
        ]
        real_consume = rt._consume_stream
        calls = {"n": 0}
        sizes: list[int] = []

        async def spy_consume(client, resolved, messages, kwargs, preview_state):
            calls["n"] += 1
            sizes.append(
                sum(len(str(m.get("content", ""))) for m in messages)
            )
            if calls["n"] == 1:
                raise _bad_request("Prompt exceeds max length")
            return await real_consume(
                client, resolved, messages, kwargs, preview_state
            )

        rt._consume_stream = spy_consume  # type: ignore[method-assign]
        result = await rt.run("task")
        assert not result.is_error
        assert result.text == "done"
        assert calls["n"] == 2
        assert sizes[1] < sizes[0]

    async def test_non_overflow_400_not_compacted(self):
        completions = _FakeCompletions([[_chunk("unused")]])
        rt = _runtime(completions)
        real_consume = rt._consume_stream
        calls = {"n": 0}

        async def spy_consume(client, resolved, messages, kwargs, preview_state):
            calls["n"] += 1
            raise _bad_request("model 'qwen-max' not found")

        rt._consume_stream = spy_consume  # type: ignore[method-assign]
        result = await rt.run("task")
        assert result.is_error
        assert "qwen-max" in result.text
        assert calls["n"] == 1
