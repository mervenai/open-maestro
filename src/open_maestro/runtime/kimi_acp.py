"""Kimi Agent Client Protocol (ACP) runtime adapter.

This adapter spawns ``kimi acp`` and communicates with it over stdio using the
``agent-client-protocol`` Python package.  Because ACP expects the client (us)
to execute tools such as file reads and shell commands, this adapter includes a
minimal local tool client and supports async tool-guard callbacks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from open_maestro.config.capabilities import TaskProfile
from open_maestro.config.models import ModelResolver
from open_maestro.events.bus import EventBus
from open_maestro.runtime.base import AgentResult, AgentRuntime

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from open_maestro.runtime.base import AgentConfig

logger = logging.getLogger(__name__)


def _import_acp() -> Any:
    try:
        import acp
    except ImportError as exc:
        raise RuntimeError(
            "The 'agent-client-protocol' package is required for the kimi-acp runtime. "
            "Install it with: pip install 'agent-client-protocol<0.11.0'"
        ) from exc

    # ACP 0.11.0+ removed session/set_model and changed request schemas.
    # Using an incompatible version produces cryptic "Invalid params" errors.
    try:
        from importlib.metadata import version as _pkg_version

        acp_version = _pkg_version("agent-client-protocol")
        major, minor, *_ = acp_version.split(".")
        if int(major) > 0 or int(minor) >= 11:
            raise RuntimeError(
                f"agent-client-protocol {acp_version} is not supported by the "
                f"kimi-acp runtime. Install a compatible version with: "
                f"pip install 'agent-client-protocol<0.11.0'"
            )
    except RuntimeError:
        raise
    except Exception:
        pass

    return acp


class KimiACPToolClient:
    """Minimal ACP client that executes tools locally and optionally guards them."""

    def __init__(
        self,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
        blocked_tools: set[str] | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._tool_guard = tool_guard
        self._blocked_tools = blocked_tools or set()
        self._event_bus = event_bus or EventBus()
        self._transcript: list[str] = []
        self._terminals: dict[str, asyncio.subprocess.Process] = {}

    def on_connect(self, conn: Any) -> None:
        """Called when the ACP connection is established."""
        logger.debug("Kimi ACP client connected")

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        tool_name = self._extract_tool_name(tool_call)
        tool_input = getattr(tool_call, "raw_input", {}) or {}

        await self._event_bus.emit(
            "tool.call",
            {"tool_name": tool_name, "tool_input": tool_input},
        )

        if tool_name in self._blocked_tools:
            outcome = acp.schema.DeniedOutcome(
                outcome="denied",
                reason=f"{tool_name} is in the blocked-tools list",
            )
            await self._event_bus.emit(
                "tool.result",
                {"tool_name": tool_name, "allowed": False, "reason": outcome.reason},
            )
            return acp.schema.RequestPermissionResponse(outcome=outcome)

        allowed = True
        if self._tool_guard is not None:
            allowed = await self._tool_guard(tool_name, tool_input)

        if allowed:
            await self._event_bus.emit(
                "tool.result",
                {"tool_name": tool_name, "allowed": True},
            )
            return acp.schema.RequestPermissionResponse(
                outcome=acp.schema.AllowedOutcome(outcome="allowed")
            )

        outcome = acp.schema.DeniedOutcome(
            outcome="denied",
            reason="blocked by Open Maestro tool guard",
        )
        await self._event_bus.emit(
            "tool.result",
            {"tool_name": tool_name, "allowed": False, "reason": outcome.reason},
        )
        return acp.schema.RequestPermissionResponse(outcome=outcome)

    @staticmethod
    def _extract_tool_name(tool_call: Any) -> str:
        title = getattr(tool_call, "title", None) or ""
        kind = getattr(tool_call, "kind", None)
        if isinstance(kind, str):
            return kind
        if isinstance(title, str) and title.strip():
            return title.split()[0]
        return "unknown"

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        text = self._extract_text(update)
        if text:
            self._transcript.append(text)

    @staticmethod
    def _extract_text(update: Any) -> str:
        content = getattr(update, "content", None)
        if content is None:
            return ""
        text = getattr(content, "text", None)
        if text:
            return str(text)
        return ""

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            if line is not None:
                lines = text.splitlines()
                start = max(0, line - 1)
                text = "\n".join(lines[start:])
            if limit is not None:
                text = text[:limit]
            return acp.schema.ReadTextFileResponse(content=text)
        except Exception as exc:
            logger.warning("ACP read_text_file failed: %s", exc)
            return acp.schema.ReadTextFileResponse(content=f"Error: {exc}")

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        try:
            Path(path).write_text(content, encoding="utf-8")
            return acp.schema.WriteTextFileResponse()
        except Exception as exc:
            logger.warning("ACP write_text_file failed: %s", exc)
            return acp.schema.WriteTextFileResponse()

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[Any] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        cmd = [command, *(args or [])]
        env_dict = None
        if env:
            env_dict = dict(os.environ)
            for item in env:
                key = getattr(item, "name", None)
                value = getattr(item, "value", None)
                if key is not None and value is not None:
                    env_dict[key] = value
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
                env=env_dict,
            )
            terminal_id = f"term_{id(process)}"
            self._terminals[terminal_id] = process
            return acp.schema.CreateTerminalResponse(terminal_id=terminal_id)
        except Exception as exc:
            logger.warning("ACP create_terminal failed: %s", exc)
            # Return a dummy terminal so the agent can report the error.
            return acp.schema.CreateTerminalResponse(terminal_id="term_failed")

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        process = self._terminals.get(terminal_id)
        if process is None or process.stdout is None:
            return acp.schema.TerminalOutputResponse(output="")
        try:
            data = await process.stdout.read(65536)
            text = data.decode("utf-8", errors="replace")
            return acp.schema.TerminalOutputResponse(
                output=text,
                exit_status=None,
                truncated=len(data) >= 65536,
            )
        except Exception as exc:
            logger.warning("ACP terminal_output failed: %s", exc)
            return acp.schema.TerminalOutputResponse(output=f"Error: {exc}")

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        process = self._terminals.get(terminal_id)
        if process is None:
            return acp.schema.WaitForTerminalExitResponse(exit_code=1)
        try:
            await process.wait()
            return acp.schema.WaitForTerminalExitResponse(
                exit_code=process.returncode
            )
        except Exception as exc:
            logger.warning("ACP wait_for_terminal_exit failed: %s", exc)
            return acp.schema.WaitForTerminalExitResponse(exit_code=1)

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        self._terminals.pop(terminal_id, None)
        return acp.schema.ReleaseTerminalResponse()

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        acp = _import_acp()
        process = self._terminals.pop(terminal_id, None)
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=2)
            except Exception:
                process.kill()
        return acp.schema.KillTerminalCommandResponse()

    async def ext_method(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        logger.warning("ACP ext_method not implemented: %s", method)
        return {}

    async def ext_notification(
        self, method: str, params: dict[str, Any]
    ) -> None:
        logger.debug("ACP ext_notification: %s", method)


class KimiACPRuntime(AgentRuntime):
    """Execute agents via the Kimi ACP server."""

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
    def from_config(cls, config: AgentConfig) -> KimiACPRuntime:
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
        return "kimi-acp"

    def is_available(self) -> bool:
        try:
            _import_acp()
            return shutil.which("kimi") is not None
        except RuntimeError:
            return False

    def _resolve_model(
        self, model: str | None, profile: TaskProfile | None = None
    ) -> str | None:
        return self._resolver.resolve(model, "kimi-cli", profile=profile)

    async def _run_session(
        self,
        prompt: str,
        config: AgentConfig | None = None,
        *,
        resume_session: str | None = None,
        fork_session: str | None = None,
        tool_guard: Callable[[str, dict[str, Any]], Coroutine[Any, Any, bool]]
        | None = None,
    ) -> AgentResult:
        acp = _import_acp()

        system_prompt = self._system_prompt
        if config is not None and config.system_prompt is not None:
            system_prompt = config.system_prompt

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        model = self._model
        if config is not None and config.model is not None:
            model = config.model
        resolved_model = self._resolve_model(
            model, profile=config.task_profile if config else None
        )

        cwd = self._cwd if config is None or config.cwd is None else config.cwd
        cwd = cwd or str(Path.cwd())

        blocked_tools: set[str] = set()
        if config is not None and config.blocked_tools:
            blocked_tools = set(config.blocked_tools)

        event_bus = EventBus()
        client = KimiACPToolClient(
            tool_guard=tool_guard,
            blocked_tools=blocked_tools,
            event_bus=event_bus,
        )

        start = time.monotonic()
        async with acp.spawn_agent_process(
            client,
            "kimi",
            "acp",
            cwd=cwd,
        ) as (conn, process):
            try:
                await conn.initialize(
                    protocol_version=acp.PROTOCOL_VERSION,
                    client_info=acp.schema.Implementation(
                        name="open-maestro", version="0.1.0"
                    ),
                )

                if resume_session:
                    await conn.resume_session(cwd=cwd, session_id=resume_session)
                elif fork_session:
                    fork_resp = await conn.fork_session(cwd=cwd, session_id=fork_session)
                    resume_session = fork_resp.session_id
                else:
                    session_resp = await conn.new_session(cwd=cwd)
                    resume_session = session_resp.session_id

                if resolved_model:
                    await self._set_session_model(
                        conn,
                        model_id=resolved_model,
                        session_id=resume_session,
                    )

                text_block = acp.schema.TextContentBlock(type="text", text=full_prompt)
                await conn.prompt(
                    prompt=[text_block],
                    session_id=resume_session,
                )

                # Give the agent a bounded amount of time to finish.
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError:
                return AgentResult(
                    text="Kimi ACP session timed out",
                    session_id=resume_session,
                    is_error=True,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            except Exception as exc:
                logger.exception("Kimi ACP session failed")
                return AgentResult(
                    text=f"Kimi ACP error: {exc}",
                    session_id=resume_session,
                    is_error=True,
                    duration_ms=int((time.monotonic() - start) * 1000),
                )

        duration_ms = int((time.monotonic() - start) * 1000)
        return AgentResult(
            text="\n".join(client._transcript).strip(),
            session_id=resume_session,
            duration_ms=duration_ms,
            metadata={"terminal_count": len(client._terminals)},
        )

    async def _set_session_model(
        self,
        conn: Any,
        *,
        model_id: str,
        session_id: str,
    ) -> None:
        """Set the active model for an ACP session.

        ``agent-client-protocol`` 0.11.0 removed ``session/set_model`` from the
        stable surface (``ClientSideConnection.set_session_model``). On those
        versions we log a warning and continue; the underlying agent process
        will use its default model.
        """
        try:
            await conn.set_session_model(
                model_id=model_id,
                session_id=session_id,
            )
        except AttributeError as exc:
            logger.warning(
                "Cannot set ACP session model to %s: %s. "
                "The installed agent-client-protocol version likely removed "
                "session/set_model. Falling back to the agent's default model.",
                model_id,
                exc,
            )

    async def run(
        self,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._run_session(prompt, config)

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

        return await self._run_session(prompt, config, tool_guard=tool_guard)

    async def resume(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._run_session(
            prompt, config, resume_session=session_id
        )

    async def fork(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._run_session(
            prompt, config, fork_session=session_id
        )
