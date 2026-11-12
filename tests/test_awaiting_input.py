"""Awaiting-input handshake (3.7.6): marker file + interactive ruling loop.

An agent that ends its turn with questions for the project lead writes
``.open-maestro/awaiting-input.yaml``; the REPL surfaces the ask compactly,
collects rulings one-by-one or in bulk, and sends them back into the same
backend session.
"""

import asyncio
import os
import time

import pytest

from open_maestro import awaiting_input
from open_maestro.awaiting_input import (
    DEFER,
    AwaitedQuestion,
    AwaitingInputBatch,
    clear,
    format_answers_message,
    load,
    normalize_answer,
    parse_bulk_answers,
)

MARKER_DIR = ".open-maestro"


def _write_marker(tmp_path, text: str) -> None:
    d = tmp_path / MARKER_DIR
    d.mkdir(exist_ok=True)
    (d / "awaiting-input.yaml").write_text(text)


VALID = """\
created_at: '2026-11-13T09:00:00'
source: impl-000
questions:
  - id: Q1
    audience: lead
    question: 'Store user GUID?'
    recommendation: 'store GUID, email fallback'
    blocks: 'P1-4 DDL'
  - id: Q2
    audience: client/M3
    question: 'Telemetry OK?'
    recommendation: 'App Insights events'
"""


class TestLoadMarker:
    def test_missing_marker_returns_none(self, tmp_path):
        assert load(tmp_path) is None

    def test_valid_marker_round_trips(self, tmp_path):
        _write_marker(tmp_path, VALID)
        batch = load(tmp_path)
        assert batch is not None
        assert batch.source == "impl-000"
        assert len(batch.questions) == 2
        q1, q2 = batch.questions
        assert q1.id == "Q1"
        assert q1.audience == "lead"
        assert q1.recommendation == "store GUID, email fallback"
        assert q1.blocks == "P1-4 DDL"
        assert q1.options == []
        assert q2.audience == "client/M3"

    def test_missing_fields_get_defaults(self, tmp_path):
        _write_marker(tmp_path, "questions:\n  - id: Q1\n")
        batch = load(tmp_path)
        assert batch.questions[0].audience == "lead"
        assert batch.questions[0].question == ""

    def test_non_list_options_coerced(self, tmp_path):
        _write_marker(
            tmp_path,
            "questions:\n  - id: Q1\n    options: 'a) one'\n",
        )
        assert load(tmp_path).questions[0].options == ["a) one"]

    def test_stale_marker_ignored(self, tmp_path):
        _write_marker(tmp_path, VALID)
        old = time.time() - 3600
        os.utime(tmp_path / MARKER_DIR / "awaiting-input.yaml", (old, old))
        assert load(tmp_path, since=time.time()) is None

    def test_fresh_marker_accepted(self, tmp_path):
        _write_marker(tmp_path, VALID)
        assert load(tmp_path, since=time.time()) is not None

    def test_no_since_accepts_any_age(self, tmp_path):
        _write_marker(tmp_path, VALID)
        old = time.time() - 3600
        os.utime(tmp_path / MARKER_DIR / "awaiting-input.yaml", (old, old))
        assert load(tmp_path) is not None

    @pytest.mark.parametrize(
        "text",
        ["just a string", "questions: nope", "questions: []", "- a\n- b\n", "[1, 2]"],
    )
    def test_malformed_markers_return_none(self, tmp_path, text):
        _write_marker(tmp_path, text)
        assert load(tmp_path) is None

    def test_questions_without_id_are_skipped(self, tmp_path):
        _write_marker(
            tmp_path,
            "questions:\n  - question: 'no id'\n  - id: Q2\n    question: ok\n",
        )
        batch = load(tmp_path)
        assert [q.id for q in batch.questions] == ["Q2"]

    def test_clear_removes_marker(self, tmp_path):
        _write_marker(tmp_path, VALID)
        clear(tmp_path)
        assert load(tmp_path) is None

    def test_clear_tolerates_missing_marker(self, tmp_path):
        clear(tmp_path)  # no crash


class TestParseBulkAnswers:
    def test_q_prefix_colon(self):
        answers, unparsed = parse_bulk_answers("Q1: store GUID\n")
        assert answers == {1: "store GUID"}
        assert unparsed == []

    def test_bare_number_equals_and_semicolons(self):
        answers, unparsed = parse_bulk_answers("2=b; 3: defer")
        assert answers == {2: "b", 3: DEFER}
        assert unparsed == []

    def test_dot_separator(self):
        answers, _ = parse_bulk_answers("4. use option a")
        assert answers == {4: "use option a"}

    def test_defer_words_normalize(self):
        for word in ("defer", "Defer", "skip", "none", "-"):
            answers, _ = parse_bulk_answers(f"1: {word}")
            assert answers[1] == DEFER, word

    def test_unparsed_lines_returned(self):
        answers, unparsed = parse_bulk_answers("garbage line\nQ1: ok")
        assert answers == {1: "ok"}
        assert unparsed == ["garbage line"]

    def test_answer_may_contain_commas_and_colons(self):
        answers, unparsed = parse_bulk_answers("1: use a, because b: cheaper")
        assert answers == {1: "use a, because b: cheaper"}
        assert unparsed == []


class TestNormalizeAnswer:
    def test_plain_text_kept(self):
        assert normalize_answer("option a") == "option a"

    def test_whitespace_stripped(self):
        assert normalize_answer("  yes  ") == "yes"


class TestFormatAnswersMessage:
    def _batch(self):
        return AwaitingInputBatch(
            source="impl-000",
            questions=[
                AwaitedQuestion(
                    id="Q1",
                    audience="lead",
                    question="Store user GUID?",
                ),
                AwaitedQuestion(
                    id="Q2",
                    audience="client/M3",
                    question="Telemetry OK?",
                ),
            ],
        )

    def test_ruled_and_declined_rows(self):
        msg = format_answers_message(self._batch(), {"Q1": "store GUID"})
        assert "Q1 [lead] Store user GUID? → store GUID" in msg
        assert "Q2 [client/M3] Telemetry OK? → DECLINED" in msg
        assert "Open register row" in msg
        # Q1 got no provisional marker; Q2 is not ruled so no marker either.
        assert msg.count("[INTERNAL — not M3-ratified]") == 0

    def test_provisional_marker_on_client_ruling(self):
        msg = format_answers_message(self._batch(), {"Q2": "go ahead"})
        assert "Q2 [client/M3] Telemetry OK? → go ahead" in msg
        assert "[INTERNAL — not M3-ratified]" in msg

    def test_self_contained_question_text(self):
        msg = format_answers_message(self._batch(), {"Q1": "yes"})
        assert "Store user GUID?" in msg
        assert "impl-000" in msg


class TestAskAwaitingQuestions:
    """The interactive ruling loop: one-by-one, bulk paste, decline all, skip."""

    def _batch(self):
        return AwaitingInputBatch(
            source="impl-000",
            questions=[
                AwaitedQuestion(
                    id="Q1", audience="lead", question="GUID?", recommendation="store it"
                ),
                AwaitedQuestion(
                    id="Q2",
                    audience="client/M3",
                    question="Telemetry?",
                    recommendation="events",
                ),
            ],
        )

    def _run(self, replies: list[str]):
        from open_maestro.interactive import _ask_awaiting_questions

        iterator = iter(replies)

        async def fake_read_line(prompt: str = "> ") -> str:
            return next(iterator, "")

        return asyncio.run(
            _ask_awaiting_questions(self._batch(), read_line=fake_read_line)
        )

    def test_skip_returns_none(self):
        assert self._run(["s"]) is None

    def test_blank_mode_is_skip(self):
        assert self._run([""]) is None

    def test_decline_all_marks_every_question_deferred(self):
        assert self._run(["d", "y"]) == {"Q1": DEFER, "Q2": DEFER}

    def test_decline_all_confirm_no_returns_none(self):
        assert self._run(["d", "n"]) is None

    def test_one_by_one_collects_answers_and_defers(self):
        assert self._run(["o", "store GUID", "", "y"]) == {
            "Q1": "store GUID",
            "Q2": DEFER,
        }

    def test_one_by_one_confirm_no_reoffers_menu(self):
        # First pass answered Q1, confirm declined; second pass declines all.
        assert self._run(["o", "x", "", "n", "d", "y"]) == {"Q1": DEFER, "Q2": DEFER}

    def test_bulk_paste_collects_multiple_at_once(self):
        assert self._run(["b", "Q1: store GUID; 2: defer", "", "y"]) == {
            "Q1": "store GUID",
            "Q2": DEFER,
        }

    def test_bulk_unparsed_entry_reprompts(self):
        # First bulk line unrecognized, second pass fills it, empty ends.
        assert self._run(["b", "garbage", "1: ok", "", "y"]) == {
            "Q1": "ok",
            "Q2": DEFER,
        }

    def test_bulk_out_of_range_number_reported_not_applied(self):
        assert self._run(["b", "9: nope", "", "y"]) == {"Q1": DEFER, "Q2": DEFER}

    def test_invalid_mode_reprompts(self):
        assert self._run(["x", "s"]) is None
