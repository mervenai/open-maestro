"""Claude Code CLI runtime adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from open_maestro.config.capabilities import TaskProfile
from open_maestro.config.models import ModelResolver
from open_maestro.events.bus import EventBus
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime

logger = logging.getLogger(__name__)


class ClaudeCLIRuntime(AgentRuntime):
    """Execute agents via the ``claude`` command-line tool."""

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
    def from_config(cls, config: AgentConfig) -> ClaudeCLIRuntime:
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
        return "claude-cli"

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def _resolve_model(
        self, model: str | None, profile: TaskProfile | None = None
    ) -> str | None:
        return self._resolver.resolve(model, self.runtime_name, profile=profile)

    def _build_args(
        self,
        prompt: str,
        *,
        resume_session: str | None = None,
        fork: bool = False,
        output_json: bool = True,
        config: AgentConfig | None = None,
        temp_files: list[str] | None = None,
    ) -> list[str]:
        # The prompt must come immediately after ``claude -p``.  Variadic
        # options such as ``--allowedTools`` will otherwise consume the prompt
        # as a tool name and Claude will complain that no input was provided.
        args: list[str] = ["claude", "-p", prompt]

        if output_json:
            args.extend(["--output-format", "json"])

        model = self._model
        if config is not None and config.model is not None:
            model = config.model
        profile = config.task_profile if config is not None else None
        resolved = self._resolve_model(model, profile=profile)
        if resolved:
            args.extend(["--model", resolved])

        max_turns = self._max_turns
        if config is not None and config.max_turns is not None:
            max_turns = config.max_turns
        if max_turns is not None:
            args.extend(["--max-turns", str(max_turns)])

        if resume_session:
            args.extend(["--resume", resume_session])
            if fork:
                args.append("--fork-session")

        system_prompt = self._system_prompt
        if config is not None and config.system_prompt is not None:
            system_prompt = config.system_prompt
        if system_prompt:
            # Write the system prompt to a temp file to avoid hitting the OS
            # argument length limit when agents embed many skills.
            sp_path = self._write_system_prompt(system_prompt)
            if sp_path:
                args.extend(["--system-prompt-file", sp_path])
                if temp_files is not None:
                    temp_files.append(sp_path)

        if config is not None:
            if config.allowed_tools:
                args.extend(["--allowedTools", ",".join(config.allowed_tools)])

            if config.blocked_tools:
                args.extend(
                    ["--disallowedTools", ",".join(sorted(config.blocked_tools))]
                )

            if config.permission_mode:
                args.extend(["--permission-mode", config.permission_mode])

            if config.mcp_servers:
                mcp_path = self._write_mcp_config(config.mcp_servers)
                if mcp_path:
                    args.extend(["--mcp-config", mcp_path])
                    if temp_files is not None:
                        temp_files.append(mcp_path)

        return args

    @staticmethod
    def _write_system_prompt(system_prompt: str) -> str | None:
        """Write a system prompt to a temporary file for Claude CLI."""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(system_prompt)
                return f.name
        except Exception as exc:
            logger.warning("Failed to write system prompt file: %s", exc)
            return None

    @staticmethod
    def _write_mcp_config(mcp_servers: dict[str, Any]) -> str | None:
        """Write MCP server config to a temporary JSON file for Claude CLI.

        The input is expected to be either a dict already containing
        ``mcpServers`` or a flat mapping of server names to their config.
        """
        try:
            if "mcpServers" in mcp_servers:
                payload = dict(mcp_servers)
            else:
                payload = {"mcpServers": dict(mcp_servers)}

            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                delete=False,
            ) as f:
                json.dump(payload, f)
                return f.name
        except Exception as exc:
            logger.warning("Failed to write MCP config: %s", exc)
            return None

    @staticmethod
    async def _stream_pipe(
        stream: asyncio.StreamReader | None,
        output_stream: Any,
        buffer: list[str],
        prefix: str = "",
    ) -> None:
        """Read a subprocess stream line-by-line, print it, and buffer it.

        Why: CLI tools produce progress output (tool calls, file reads, etc.)
        that users want to see in real time. This helper tees the stream to both
        the terminal and a capture buffer.
        """
        if stream is None:
            return
        while True:
            line_bytes = await stream.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="replace")
            buffer.append(line)
            if prefix:
                output_stream.write(prefix)
            output_stream.write(line)
            try:
                output_stream.flush()
            except Exception:
                pass

    async def _invoke(
        self,
        prompt: str,
        *,
        resume_session: str | None = None,
        fork: bool = False,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        temp_files: list[str] = []
        args = self._build_args(
            prompt,
            resume_session=resume_session,
            fork=fork,
            config=config,
            temp_files=temp_files,
        )

        start = time.monotonic()
        heartbeat = asyncio.create_task(self._working_heartbeat(start))

        stdout_buffer: list[str] = []
        stderr_buffer: list[str] = []

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd if config is None or config.cwd is None else config.cwd,
            )

            stdout_task = asyncio.create_task(
                self._stream_pipe(process.stdout, sys.stdout, stdout_buffer)
            )
            stderr_task = asyncio.create_task(
                self._stream_pipe(process.stderr, sys.stderr, stderr_buffer, prefix="[claude] ")
            )

            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task, process.wait()),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError:
                process.kill()
                return AgentResult(
                    text="Claude CLI subprocess timed out",
                    is_error=True,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            for path in temp_files:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed to clean up temp MCP config %s: %s", path, exc)

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = "".join(stdout_buffer)
        stderr = "".join(stderr_buffer)

        if process.returncode != 0:
            # Claude writes structured errors to stdout as JSON; prefer that
            # over stderr when available.
            if stdout.strip():
                parsed = self._parse_output(stdout, duration_ms=duration_ms)
                if parsed.is_error or parsed.text:
                    return parsed
            logger.error("Claude CLI failed (rc=%s): %s", process.returncode, stderr)
            return AgentResult(
                text=stderr or f"claude exited with code {process.returncode}",
                is_error=True,
                duration_ms=duration_ms,
            )

        return self._parse_output(stdout, duration_ms=duration_ms)

    @staticmethod
    def _parse_output(raw: str, *, duration_ms: int | None = None) -> AgentResult:
        """Parse Claude --output-format json output."""
        try:
            data: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return AgentResult(text=raw.strip(), duration_ms=duration_ms)

        text = data.get("result", data.get("text", raw.strip()))
        usage = data.get("usage") or {}
        return AgentResult(
            text=str(text),
            session_id=data.get("session_id"),
            cost_usd=data.get("cost_usd") or data.get("total_cost_usd"),
            num_turns=data.get("num_turns"),
            duration_ms=duration_ms,
            input_tokens=usage.get("input_tokens") if usage else None,
            output_tokens=usage.get("output_tokens") if usage else None,
            tokens_used=usage.get("total_tokens") if usage else None,
            is_error=bool(data.get("is_error", False)),
            metadata={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "result",
                    "text",
                    "session_id",
                    "cost_usd",
                    "total_cost_usd",
                    "num_turns",
                    "is_error",
                    "usage",
                }
            },
        )

    @staticmethod
    async def _working_heartbeat(
        start: float, interval: float = 5.0
    ) -> None:
        """Emit periodic runtime.working events while the CLI subprocess runs."""
        bus = EventBus()
        while True:
            await asyncio.sleep(interval)
            duration_ms = int((time.monotonic() - start) * 1000)
            await bus.emit("runtime.working", {"duration_ms": duration_ms})

    async def run(
        self,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._invoke(prompt, config=config)

    def _try_sdk_runtime(
        self, config: AgentConfig | None = None
    ) -> "ClaudeSDKRuntime" | None:
        """Return a Claude SDK runtime if the Agent SDK is installed and configured.

        The CLI runtime delegates ``run_with_hooks`` to the SDK runtime so that
        tool calls can be intercepted without forcing the user to switch runtimes.
        """
        try:
            import claude_agent_sdk as _sdk  # noqa: F401
        except ImportError:
            return None
        # The SDK ultimately needs an Anthropic API key to actually run.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        from open_maestro.runtime.claude_sdk import ClaudeSDKRuntime

        return ClaudeSDKRuntime(
            system_prompt=self._system_prompt,
            model=self._model,
            cwd=self._cwd if config is None or config.cwd is None else config.cwd,
            max_turns=self._max_turns,
            timeout_seconds=self._timeout_seconds,
            extra=self._extra,
        )

    async def run_with_hooks(
        self,
        prompt: str,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
        blocked_tools: set[str] | None = None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        """Execute with optional tool interception.

        If the Claude Agent SDK is installed, delegate to it so individual tool
        calls are guarded.  Otherwise fall back to system-prompt guardrails.
        """
        sdk_runtime = self._try_sdk_runtime(config)
        if sdk_runtime is not None and (tool_guard is not None or blocked_tools):
            logger.info(
                "Claude CLI runtime delegating to claude-sdk for real tool interception"
            )
            return await sdk_runtime.run_with_hooks(
                prompt,
                tool_guard=tool_guard,
                blocked_tools=blocked_tools,
                config=config,
            )

        # Fall back to system-prompt guardrails.
        if tool_guard is not None:
            logger.debug(
                "Claude CLI runtime cannot apply async tool_guard; "
                "relying on system-prompt guardrails"
            )
        if blocked_tools:
            guard = (
                "You are forbidden from using these tools under any circumstances: "
                + ", ".join(sorted(blocked_tools))
                + "."
            )
            merged = self._merge_config(config, system_prompt_append=guard)
            return await self.run(prompt, config=merged)
        return await self.run(prompt, config=config)

    async def resume(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._invoke(prompt, resume_session=session_id, config=config)

    async def fork(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._invoke(
            prompt, resume_session=session_id, fork=True, config=config
        )

    def _merge_config(
        self,
        config: AgentConfig | None,
        *,
        system_prompt_append: str,
    ) -> AgentConfig:
        base_sp = self._system_prompt or ""
        if config is not None and config.system_prompt is not None:
            base_sp = config.system_prompt

        new_sp = f"{base_sp}\n\n{system_prompt_append}" if base_sp else system_prompt_append

        if config is None:
            return AgentConfig(system_prompt=new_sp)

        from dataclasses import replace

        return replace(config, system_prompt=new_sp)
