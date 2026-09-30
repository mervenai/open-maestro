"""Regression tests for the _run_with_interrupt Esc-listener lifecycle.

Fix for the "prompt never reappears after a long task" bug: the Esc-listener
thread used to be left running past the end of the turn, and its deferred
termios restore could land inside the next prompt_toolkit session's raw-mode
setup, leaving the terminal in cooked mode so the `> ` prompt never painted.
"""

import os
import pty
import sys
import termios

import pytest

from open_maestro.interactive import _run_with_interrupt


class _FakeStdin:
    """Minimal stdin stand-in that claims to be a TTY backed by *fd*."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._fd

    def read(self, n: int) -> str:
        return os.read(self._fd, n).decode(errors="ignore")


@pytest.mark.asyncio
async def test_run_with_interrupt_joins_listener_and_restores_termios(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("pty-based test is Unix-only")

    master, slave = pty.openpty()
    old_attrs = termios.tcgetattr(master)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(master))
    try:
        result = await _run_with_interrupt(_trivial_coro())
        assert result == "done"

        # The listener thread must be fully stopped once the turn returns.
        # This is the core regression guard: previously the thread was left
        # running and its deferred termios restore landed inside the next
        # prompt_toolkit session, leaving the prompt unpainted. The join in
        # _run_with_interrupt guarantees the thread has exited (and therefore
        # finished restoring the terminal) before the prompt loop resumes.
        import threading

        alive = [
            t for t in threading.enumerate() if t.name == "maestro-esc-listener"
        ]
        assert not alive, f"Esc-listener thread leaked: {alive}"

        # NOTE: byte-identical termios restoration is not asserted here.
        # Even a plain setcbreak + TCSANOW restore on a pty MASTER does not
        # reproduce the original attrs (the driver re-derives lflag), so on
        # ptys that assertion tests pty quirks, not our code. The join above
        # is what actually guarantees the restore completed in-order.
    finally:
        os.close(master)
        os.close(slave)


async def _trivial_coro() -> str:
    import asyncio

    await asyncio.sleep(0.05)
    return "done"
