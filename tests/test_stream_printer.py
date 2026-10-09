"""Tests for the presentable subprocess StreamPrinter.

Why: The StreamPrinter is responsible for turning raw CLI output into styled
terminal output. These tests prove it buffers input and formats lines.
What: Tests ``StreamPrinter.write``, ``create_printer``, and ``pump_stream``.
Test: ``uv run pytest tests/test_stream_printer.py``
"""

from __future__ import annotations

import asyncio

import pytest

from open_maestro.runtime.stream_printer import StreamPrinter, create_printer, pump_stream


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


def test_stream_printer_decodes_claude_text_block(capsys) -> None:
    """Claude stream-json assistant text is printed as content, not raw JSON."""
    printer = StreamPrinter("claude", color="magenta")
    printer.write(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working on it"}]}}\n'
    )
    captured = capsys.readouterr()
    assert "working on it" in captured.out
    assert '"type": "assistant"' not in captured.out


def test_stream_printer_decodes_claude_tool_use(capsys) -> None:
    """Claude tool_use events show tool name plus a one-line input summary."""
    printer = StreamPrinter("claude", color="magenta")
    printer.write(
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit",'
        '"input":{"file_path":"docs/blueprint.md","old_string":"a","new_string":"b"}}]}}\n'
    )
    captured = capsys.readouterr()
    assert "Edit" in captured.out
    assert "file_path=docs/blueprint.md" in captured.out
    assert "old_string" not in captured.out  # bulky payload stays hidden


def test_stream_printer_surfaces_claude_tool_error(capsys) -> None:
    """Failed tool_results (e.g. permission denials) are shown, not swallowed."""
    printer = StreamPrinter("claude", color="magenta")
    printer.write(
        '{"type":"user","message":{"content":[{"type":"tool_result","is_error":true,'
        '"content":"Claude requested permissions to write to x, but you haven\'t granted it yet."}]}}\n'
    )
    captured = capsys.readouterr()
    assert "ERROR" in captured.out
    assert "permissions" in captured.out


def test_stream_printer_swallows_claude_noise_but_buffers_raw(capsys) -> None:
    """System/result events render minimally; raw lines stay in the buffer."""
    printer = StreamPrinter("claude", color="magenta")
    line = '{"type":"system","subtype":"thinking_tokens","session_id":"s1"}\n'
    printer.write(line)
    captured = capsys.readouterr()
    assert "thinking_tokens" not in captured.out
    assert printer.get_buffer() == line


def _reader(*chunks: bytes) -> asyncio.StreamReader:
    """Build a StreamReader with the production default 64 KiB line limit."""
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


async def test_pump_stream_single_line_longer_than_64k_limit():
    # Stream-json tool calls can put a whole file's content on one line;
    # readline() used to raise LimitOverrunError ("Separator is found, but
    # chunk is longer than limit") and kill the entire turn.
    payload = b"x" * (200 * 1024)
    printer = StreamPrinter("kimi")
    await pump_stream(_reader(payload), printer)
    assert printer.get_buffer() == payload.decode()


async def test_pump_stream_preserves_lines_split_across_chunks():
    printer = StreamPrinter("kimi")
    await pump_stream(_reader(b"hello\nwor", b"ld\n", b"tail"), printer)
    assert printer.get_buffer() == "hello\nworld\ntail"


async def test_pump_stream_none_stream_is_noop():
    printer = StreamPrinter("kimi")
    await pump_stream(None, printer)
    assert printer.get_buffer() == ""
