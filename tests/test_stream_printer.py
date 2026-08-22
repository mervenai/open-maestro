"""Tests for the presentable subprocess StreamPrinter.

Why: The StreamPrinter is responsible for turning raw CLI output into styled
terminal output. These tests prove it buffers input and formats lines.
What: Tests ``StreamPrinter.write`` and ``create_printer``.
Test: ``uv run pytest tests/test_stream_printer.py``
"""

from __future__ import annotations

import pytest

from open_maestro.runtime.stream_printer import StreamPrinter, create_printer


def test_create_printer_sets_color_by_label() -> None:
    """create_printer chooses known colors for kimi/claude."""
    kimi = create_printer("kimi")
    assert kimi.color == "green"
    claude = create_printer("claude")
    assert claude.color == "magenta"
    other = create_printer("openai")
    assert other.color == "cyan"


def test_stream_printer_buffers_all_input() -> None:
    """Everything written is available via get_buffer."""
    printer = StreamPrinter("test")
    printer.write("line one\n")
    printer.write("line two\n")
    assert printer.get_buffer() == "line one\nline two\n"


def test_stream_printer_decodes_kimi_assistant_content(capsys) -> None:
    """Kimi stream-json assistant lines are printed as content, not raw JSON."""
    printer = StreamPrinter("kimi")
    printer.write('{ "role": "assistant", "content": "hello" }\n')
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert '{"role": "assistant"' not in captured.out


def test_stream_printer_decodes_kimi_tool_line(capsys) -> None:
    """Kimi stream-json tool lines show tool name and input."""
    printer = StreamPrinter("kimi")
    printer.write('{ "role": "tool", "name": "Bash", "input": {"command": "ls"} }\n')
    captured = capsys.readouterr()
    assert "Bash" in captured.out
    assert "ls" in captured.out


def test_stream_printer_raw_json_for_unknown_shape(capsys) -> None:
    """Unknown JSON shapes are printed compactly."""
    printer = StreamPrinter("kimi")
    printer.write('{ "role": "unknown", "foo": "bar" }\n')
    captured = capsys.readouterr()
    assert "unknown" in captured.out


def test_stream_printer_plain_line_gets_prefix(capsys) -> None:
    """Non-JSON lines receive a colored label prefix."""
    printer = StreamPrinter("claude", color="magenta")
    printer.write("plain text\n")
    captured = capsys.readouterr()
    assert "[claude]" in captured.out
    assert "plain text" in captured.out
