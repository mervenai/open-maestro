"""Kimi Code CLI runtime adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from typing import TYPE_CHECKING, Any

from open_maestro.config.capabilities import TaskProfile
from open_maestro.config.models import ModelResolver
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from open_maestro.runtime.base import AgentConfig

_termios: Any | None = None
try:
    import termios

    _termios = termios
except ImportError:
    pass

logger = logging.getLogger(__name__)


class _TerminalAttrs:
    """Save and restore Unix terminal attributes around a subprocess.

    CLI tools such as ``kimi`` may put the terminal into raw mode.  If they
    exit without restoring cooked mode, subsequent ``input()`` calls in the
    parent process receive corrupted keystrokes (e.g. backspace becomes ``^C``).
    """

    def __init__(self) -> None:
        self._fd: int | None = None
        self._attrs: list[Any] | None = None

    def save(self) -> None:
        if _termios is None:
            return
        try:
            fd = sys.stdin.fileno()
            if not sys.stdin.isatty():
                return
            self._fd = fd
            self._attrs = _termios.tcgetattr(fd)
        except Exception as exc:
            logger.debug("Could not save terminal attributes: %s", exc)

    def restore(self) -> None:
        if _termios is None or self._fd is None or self._attrs is None:
            return
        try:
            _termios.tcsetattr(self._fd, _termios.TCSADRAIN, self._attrs)
        except Exception as exc:
            logger.debug("Could not restore terminal attributes: %s", exc)


class KimiCLIRuntime(AgentRuntime):
    """Execute agents via the ``kimi`` command-line tool."""

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
    def from_config(cls, config: AgentConfig) -> KimiCLIRuntime:
        """Create a Kimi CLI runtime from a vendor-neutral ``AgentConfig``."""
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
        return "kimi-cli"

    def is_available(self) -> bool:
        return shutil.which("kimi") is not None

    def _resolve_model(
        self, model: str | None, profile: TaskProfile | None = None
    ) -> str | None:
        return self._resolver.resolve(model, self.runtime_name, profile=profile)

    def _build_prompt(
        self, user_prompt: str, system_prompt: str | None = None
    ) -> str:
        """Kimi prompt mode has no --system-prompt, so we prepend it inline."""
        sp = system_prompt if system_prompt is not None else self._system_prompt
        if sp:
            return f"{sp}\n\n{user_prompt}"
        return user_prompt

    def _build_args(
        self,
        prompt: str,
        *,
        resume_session: str | None = None,
        output_json: bool = True,
        config: AgentConfig | None = None,
    ) -> list[str]:
        args: list[str] = ["kimi"]

        if resume_session:
            args.extend(["-r", resume_session])

        args.extend(["-p", prompt])

        if output_json:
            args.extend(["--output-format", "stream-json"])

        model = self._model
        if config is not None and config.model is not None:
            model = config.model
        profile = config.task_profile if config is not None else None
        resolved = self._resolve_model(model, profile=profile)
        if resolved:
            args.extend(["-m", resolved])

        if config is not None:
            if config.permission_mode == "auto":
                args.append("--auto")
            elif config.permission_mode == "yolo":
                args.append("--yolo")

            skills_dirs = (config.extra or {}).get("skills_dirs", [])
            for skills_dir in skills_dirs:
                args.extend(["--skills-dir", str(skills_dir)])

            if config.allowed_tools:
                logger.debug(
                    "kimi-cli runtime does not support --allowed-tools; "
                    "allowed tools will be enforced via system prompt only."
                )
            if config.blocked_tools:
                logger.debug(
                    "kimi-cli runtime does not support --blocked-tools; "
                    "blocked tools will be enforced via system prompt only."
                )
            if config.max_turns is not None:
                logger.debug(
                    "kimi-cli runtime does not support --max-turns; ignoring."
                )
            if config.mcp_servers:
                logger.debug(
                    "kimi-cli runtime does not support passing MCP servers via CLI flags; "
                    "configure MCP in the Kimi Code settings instead."
                )

        return args

    async def _invoke(
        self,
        prompt: str,
        *,
        resume_session: str | None = None,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        system_prompt = self._system_prompt
        if config is not None and config.system_prompt is not None:
            system_prompt = config.system_prompt
        full_prompt = self._build_prompt(prompt, system_prompt)

        args = self._build_args(
            full_prompt,
            resume_session=resume_session,
            config=config,
        )

        start = time.monotonic()
        term_attrs = _TerminalAttrs()
        term_attrs.save()
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd if config is None or config.cwd is None else config.cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            return AgentResult(
                text="Kimi CLI subprocess timed out",
                is_error=True,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            term_attrs.restore()

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = stdout_bytes.decode() if stdout_bytes else ""
        stderr = stderr_bytes.decode() if stderr_bytes else ""

        if process.returncode != 0:
            logger.error("Kimi CLI failed (rc=%s): %s", process.returncode, stderr)
            return AgentResult(
                text=stderr or f"kimi exited with code {process.returncode}",
                is_error=True,
                duration_ms=duration_ms,
            )

        return self._parse_output(stdout, duration_ms=duration_ms)

    @staticmethod
    def _parse_output(raw: str, *, duration_ms: int | None = None) -> AgentResult:
        """Parse Kimi stream-json output."""
        text_parts: list[str] = []
        session_id: str | None = None
        metadata: dict[str, Any] = {}

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON line; treat as plain text fallback
                text_parts.append(line)
                continue

            role = msg.get("role")
            if role == "assistant":
                content = msg.get("content")
                if content:
                    text_parts.append(str(content))
            elif role == "meta":
                if msg.get("type") == "session.resume_hint":
                    session_id = msg.get("session_id")
                    metadata["resume_command"] = msg.get("command")

        return AgentResult(
            text="\n".join(text_parts).strip(),
            session_id=session_id,
            duration_ms=duration_ms,
            metadata=metadata,
        )

    async def run(
        self,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._invoke(prompt, config=config)

    def _try_acp_runtime(
        self, config: AgentConfig | None = None
    ) -> "KimiACPRuntime" | None:
        """Return a Kimi ACP runtime only when explicitly enabled.

        Auto-delegating ``kimi-cli`` to ACP has proven brittle across ACP
        versions (set_session_model removal, schema changes, "Invalid params" on
        tool calls). To keep ``kimi-cli`` stable, ACP delegation is now opt-in
        via ``MAESTRO_KIMI_ACP_DELEGATION=1``. Use the explicit ``kimi-acp``
        runtime if you need real tool interception.
        """
        if os.environ.get("MAESTRO_KIMI_ACP_DELEGATION") != "1":
            return None

        try:
            import acp as _acp  # noqa: F401
        except ImportError:
            return None
        if shutil.which("kimi") is None:
            return None

        # ACP 0.11.0+ removed session/set_model and changed several schemas.
        # Delegating to an incompatible ACP version produces "Invalid params"
        # errors during tool calls, so fall back to system-prompt guardrails.
        try:
            from importlib.metadata import version as _pkg_version

            acp_version = _pkg_version("agent-client-protocol")
            major, minor, *_ = acp_version.split(".")
            if int(major) > 0 or int(minor) >= 11:
                logger.warning(
                    "agent-client-protocol %s is not supported by the kimi-acp "
                    "runtime; set MAESTRO_KIMI_ACP_DELEGATION=0 or downgrade to "
                    "'agent-client-protocol<0.11.0'",
                    acp_version,
                )
                return None
        except Exception:
            pass

        from open_maestro.runtime.kimi_acp import KimiACPRuntime

        return KimiACPRuntime(
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

        If the Kimi Agent Client Protocol (ACP) package and ``kimi`` binary are
        available, delegate to the ACP runtime so tool calls are intercepted and
        guarded.  Otherwise fall back to prompt-based guardrails.
        """
        acp_runtime = self._try_acp_runtime(config)
        if acp_runtime is not None and (tool_guard is not None or blocked_tools):
            logger.info(
                "Kimi CLI runtime delegating to kimi-acp for real tool interception"
            )
            return await acp_runtime.run_with_hooks(
                prompt,
                tool_guard=tool_guard,
                blocked_tools=blocked_tools,
                config=config,
            )

        # Fall back to system-prompt guardrails.
        if blocked_tools:
            guard = (
                "You are forbidden from using these tools under any circumstances: "
                + ", ".join(sorted(blocked_tools))
                + ". If you need one of them, stop and ask the user instead."
            )
            merged_config = self._merge_config(config, system_prompt_append=guard)
        else:
            merged_config = config

        if tool_guard is not None:
            logger.debug(
                "Kimi CLI runtime does not support async tool_guard callbacks; "
                "blocked_tools will be enforced via system prompt only."
            )

        return await self.run(prompt, config=merged_config)

    async def resume(
        self,
        session_id: str,
        prompt: str,
        config: AgentConfig | None = None,
    ) -> AgentResult:
        return await self._invoke(prompt, resume_session=session_id, config=config)

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
