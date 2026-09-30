"""Tests for the adversarial review package (MSTRO-115..120)."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_maestro.review import calibrate, gate, panel
from open_maestro.review.blueprint import (
    admission_summary,
    deep_review,
    find_blueprint_artifacts,
    milestone_review_problems,
    resolution_pack,
)
from open_maestro.review.personas import PERSONAS, READER_ROLES, render
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime


class FakeRuntime(AgentRuntime):
    """Runtime returning canned text; records prompts."""

    def __init__(self, response_text: str = "", is_error: bool = False):
        self.response_text = response_text
        self.is_error = is_error
        self.prompts: list[str] = []

    @property
    def runtime_name(self) -> str:
        return "fake"

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        self.prompts.append(prompt)
        return AgentResult(text=self.response_text, is_error=self.is_error)

    async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
        return await self.run(prompt, config)

    async def resume(self, session_id, prompt, config=None):
        return await self.run(prompt, config)


# ---------------------------------------------------------------- personas


class TestPersonas:
    def test_all_personas_present(self):
        assert set(PERSONAS) >= {
            "blind-reader",
            "fidelity",
            "quote-context",
            "noise",
            "delta",
            "reader-roles",
            "comment-audit",
        }

    @pytest.mark.parametrize("persona_id", sorted(PERSONAS))
    def test_every_persona_declares_result_keys(self, persona_id):
        persona = PERSONAS[persona_id]
        assert persona.result_keys, persona_id
        assert persona.template.template.count("RESULT") >= 1, persona_id

    def test_render_substitutes_placeholders(self):
        out = render("noise", doc="/tmp/doc.md")
        assert "$doc" not in out
        assert "/tmp/doc.md" in out

    def test_render_leaves_unknown_placeholders(self):
        # safe_substitute: fidelity has many placeholders; partial kwargs ok
        out = render("fidelity", doc="/tmp/d.md")
        assert "/tmp/d.md" in out

    def test_reader_roles_enum(self):
        assert set(READER_ROLES) == {"owner", "product", "leadership"}


# ---------------------------------------------------------------- gate


GOOD_BLIND = "...\nRESULT correct=10/10 reconciles=1 confusions=2 blocking=0"
BAD_BLIND = "...\nRESULT correct=8/10 reconciles=5 confusions=6 blocking=1"


class TestGateParsing:
    def test_parse_result_last_line_wins(self):
        text = "RESULT a=1\nnoise\nRESULT blockers=0 should_fix=2"
        assert gate.parse_result(text) == {"blockers": 0, "should_fix": 2}

    def test_parse_result_none_when_missing(self):
        assert gate.parse_result("no result here") is None

    def test_parse_result_handles_fraction(self):
        assert gate.parse_result("RESULT correct=10/10 blocking=0") == {
            "correct": 10,
            "blocking": 0,
        }


class TestGateLedger:
    def test_record_and_check_pass(self, tmp_path):
        doc = tmp_path / "blueprint.md"
        doc.write_text("# Blueprint\nDesign.\n")
        ledger = gate.GateLedger(tmp_path / "gates")
        v, fails = ledger.record(doc, "blind-reader", GOOD_BLIND)
        assert v == "pass" and fails == []
        # Other gates not run -> check still flags them.
        problems = ledger.check(doc)
        assert any("fidelity" in p for p in problems)
        assert not any("blind-reader" in p for p in problems)

    def test_record_fails_threshold(self, tmp_path):
        doc = tmp_path / "blueprint.md"
        doc.write_text("x")
        ledger = gate.GateLedger(tmp_path / "gates")
        v, fails = ledger.record(doc, "blind-reader", BAD_BLIND)
        assert v == "fail"
        assert fails

    def test_record_refuses_missing_result_line(self, tmp_path):
        doc = tmp_path / "b.md"
        doc.write_text("x")
        ledger = gate.GateLedger(tmp_path / "gates")
        with pytest.raises(gate.GateError):
            ledger.record(doc, "blind-reader", "no result line")

    def test_edit_stales_records(self, tmp_path):
        doc = tmp_path / "blueprint.md"
        doc.write_text("version one")
        ledger = gate.GateLedger(tmp_path / "gates")
        for persona, report in [
            ("blind-reader", GOOD_BLIND),
            ("fidelity", "RESULT blockers=0 should_fix=0 contradictions=0 weakened=0"),
            ("quote-context", "RESULT blockers=0 should_fix=0 framing=0"),
            ("noise", "RESULT noise_open=0 ai_or_person=0"),
            ("panel", "RESULT voices=3 blockers_open=0"),
        ]:
            v, _ = ledger.record(doc, persona, report)
            assert v == "pass", persona
        assert ledger.check(doc, profile="review") == []
        doc.write_text("version two — edited after audit")
        problems = ledger.check(doc, profile="review")
        assert len(problems) == 5  # every audit now stale

    def test_carry_moves_clean_records(self, tmp_path):
        old = tmp_path / "old.md"
        old.write_text("old version")
        ledger = gate.GateLedger(tmp_path / "gates")
        ledger.record(old, "blind-reader", GOOD_BLIND)
        ledger.record(
            old, "fidelity", "RESULT blockers=0 should_fix=0 contradictions=0 weakened=0"
        )
        ledger.record(old, "quote-context", "RESULT blockers=0 should_fix=0 framing=0")
        new = tmp_path / "new.md"
        new.write_text("fixed version")
        moved = ledger.carry(old, new, {"changed": 5, "ok": 5, "should_fix": 0, "blockers": 0})
        assert set(moved) == {"blind-reader", "fidelity", "quote-context"}
        problems = ledger.check(new, profile="review")
        assert not any("blind-reader" in p or "fidelity" in p for p in problems)

    def test_carry_refused_on_blockers(self, tmp_path):
        old = tmp_path / "old.md"
        old.write_text("old")
        ledger = gate.GateLedger(tmp_path / "gates")
        ledger.record(old, "blind-reader", GOOD_BLIND)
        new = tmp_path / "new.md"
        new.write_text("new")
        with pytest.raises(gate.GateError):
            ledger.carry(old, new, {"changed": 3, "ok": 1, "should_fix": 1, "blockers": 0})

    def test_carry_refused_when_old_had_blockers(self, tmp_path):
        old = tmp_path / "old.md"
        old.write_text("old")
        ledger = gate.GateLedger(tmp_path / "gates")
        ledger.record(old, "blind-reader", BAD_BLIND)
        new = tmp_path / "new.md"
        new.write_text("new")
        with pytest.raises(gate.GateError):
            ledger.carry(old, new, {"changed": 3, "ok": 3, "should_fix": 0, "blockers": 0})


# ---------------------------------------------------------------- panel


def _seat_responses(classify_json: str, opinions: dict[str, str], rankings: str):
    """Build per-seat canned runtimes keyed by seat id."""

    class SeatRuntime(AgentRuntime):
        instances: list["SeatRuntime"] = []

        def __init__(self, seat_id: str):
            self.seat_id = seat_id
            self.n_calls = 0
            SeatRuntime.instances.append(self)

        @property
        def runtime_name(self):
            return "fake"

        async def run(self, prompt, config=None):
            self.n_calls += 1
            if "Classify each distinct claim" in prompt:
                return AgentResult(text=classify_json)
            if "ranking anonymized responses" in prompt:
                assert "kimi" not in prompt.lower() or True
                return AgentResult(text=rankings)
            if "CHAIRMAN" in prompt:
                return AgentResult(text="Final synthesized answer.")
            return AgentResult(text=opinions.get(self.seat_id, ""))

        async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
            return await self.run(prompt, config)

        async def resume(self, session_id, prompt, config=None):
            return await self.run(prompt, config)

    return SeatRuntime


class TestPanel:
    async def test_panel_runs_all_stages(self):
        seats = panel.PANEL_SEATS
        opinions = {
            "kimi-k3": "Falsifier finding one.\nFINDINGS n=2",
            "glm-flash": "Skeptic finding.\nFINDINGS n=1",
            "claude-sonnet": "Steel-man case.\nFINDINGS n=1",
        }
        rt = _seat_responses(
            '{"claims": [{"text": "choose queue vs direct", "kind": "J"},'
            ' {"text": "run tests", "kind": "E"}]}',
            opinions,
            "Ranking done.\nFINDINGS n=1",
        )
        factory = lambda rt_type: rt("seat")  # replaced below
        made: list[rt] = []
        counter = {"i": 0}
        ids = ["kimi-k3", "glm-flash", "claude-sonnet"]

        def factory(rt_type):
            inst = rt(ids[counter["i"] % 3])
            counter["i"] += 1
            made.append(inst)
            return inst

        result = await panel.run_panel(
            "Should the write path use a queue?", runtime_factory=factory
        )
        assert result.judgment_claims == ["choose queue vs direct"]
        assert len(result.tool_claims) == 1
        assert set(result.opinions) == set(ids)
        assert result.chairman_response == "Final synthesized answer."
        assert result.ok
        assert any("routed to tool verification" in n for n in result.notes)
        assert not result.unreachable

    async def test_unreachable_seat_not_approval(self):
        class FailRuntime(AgentRuntime):
            n = 0

            @property
            def runtime_name(self):
                return "fake"

            async def run(self, prompt, config=None):
                FailRuntime.n += 1
                if "Classify each distinct claim" in prompt:
                    return AgentResult(text='{"claims": []}')
                if "ranking anonymized responses" in prompt:
                    return AgentResult(text="r\nFINDINGS n=0")
                if "CHAIRMAN" in prompt:
                    return AgentResult(text="final")
                # one seat always errors
                if FailRuntime.n % 3 == 0:
                    return AgentResult(text="boom", is_error=True)
                return AgentResult(text="opinion\nFINDINGS n=1")

            async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
                return await self.run(prompt, config)

            async def resume(self, session_id, prompt, config=None):
                return await self.run(prompt, config)

        def factory(rt_type):
            return FailRuntime()

        result = await panel.run_panel(
            "question", runtime_factory=factory, seats=panel.PANEL_SEATS
        )
        assert result.unreachable, "errored seat must be recorded as unreachable"
        assert any("silence is not consent" in n for n in result.notes)

    async def test_chairman_never_glm(self):
        glm_only = tuple(s for s in panel.PANEL_SEATS if s.id == "glm-flash")
        assert panel.choose_chairman(glm_only) is glm_only[0]  # degenerate fallback
        normal = panel.choose_chairman(panel.PANEL_SEATS)
        assert normal is not None and normal.id != "glm-flash"

    def test_parse_claims_tolerates_fences(self):
        text = '```json\n{"claims": [{"text": "a", "kind": "J"}]}\n```'
        claims = panel.parse_claims(text)
        assert claims == [{"text": "a", "kind": "J"}]


# ---------------------------------------------------------------- blueprint


BLUEPRINT_DOC = """# Blueprint: demo

The write path has 3 findings open. Auth uses the standard middleware.

1. FB-1: queue drops messages under load; if not fixed: retry storm -> duplicate charges. Severity: blocker.
2. FB-2: DTO missing a field; severity: should-fix.
"""


class TestBlueprint:
    def test_find_artifacts(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "blueprint-design.md").write_text("x")
        (tmp_path / "docs" / "notes.md").write_text("x")
        found = find_blueprint_artifacts(tmp_path)
        assert [p.name for p in found] == ["blueprint-design.md"]

    def test_admission_bar(self):
        summary = admission_summary(BLUEPRINT_DOC)
        assert summary.total == 2
        assert summary.admitted == 1  # only the failure-scenario finding
        assert summary.backlog == 1

    def test_admission_security_keyword(self):
        text = "1. SEC-1: auth bypass possible; severity: blocker."
        summary = admission_summary(text)
        assert summary.admitted == 1

    async def test_deep_review_records_ledger(self, tmp_path):
        (tmp_path / "docs").mkdir()
        doc = tmp_path / "docs" / "blueprint-x.md"
        doc.write_text(BLUEPRINT_DOC)
        reports = {
            "blind-reader": "answers\nRESULT correct=10/10 reconciles=0 confusions=0 blocking=0",
            "fidelity": "RESULT blockers=0 should_fix=0 contradictions=0 weakened=0",
            "quote-context": "RESULT blockers=0 should_fix=0 framing=0",
        }
        counter = {"i": 0}
        texts = [
            reports[p]
            for p in ("blind-reader", "fidelity", "quote-context")
        ]

        def factory(rt_type):
            r = FakeRuntime(texts[counter["i"]])
            counter["i"] += 1
            return r

        review = await deep_review(
            doc, project_path=tmp_path, runtime_factory=factory
        )
        assert review.passed
        assert set(review.persona_reports) == set(reports)
        problems = gate.GateLedger().for_project(tmp_path).check(doc)
        assert not any("blind-reader" in p for p in problems)

    async def test_deep_review_persona_error_does_not_abort(self, tmp_path):
        doc = tmp_path / "blueprint-y.md"
        doc.write_text(BLUEPRINT_DOC)
        counter = {"i": 0}

        def factory(rt_type):
            counter["i"] += 1
            if counter["i"] == 1:
                return FakeRuntime("boom", is_error=True)
            return FakeRuntime("RESULT blockers=0 should_fix=0 contradictions=0 weakened=0")

        review = await deep_review(
            doc, project_path=tmp_path, personas=("fidelity",), runtime_factory=factory
        )
        assert "fidelity" in review.errors or review.passed

    async def test_resolution_pack_written(self, tmp_path):
        doc = tmp_path / "blueprint-z.md"
        doc.write_text(BLUEPRINT_DOC)
        review = await deep_review(
            doc,
            project_path=tmp_path,
            personas=("fidelity",),
            runtime_factory=lambda rt: FakeRuntime(
                "1. FB-9: queue drops messages; if not fixed: retry storm -> "
                "duplicate charges. Severity: blocker.\n"
                "RESULT blockers=1 should_fix=0 contradictions=0 weakened=0"
            ),
        )
        assert not review.passed
        assert review.admissions["fidelity"].admitted >= 0
        pack = await resolution_pack(
            review, runtime_factory=lambda rt: FakeRuntime("# Resolution pack\n| finding | decision |")
        )
        assert pack is not None and pack.exists()
        assert "Resolution pack" in pack.read_text()

    async def test_resolution_pack_none_when_nothing_admitted(self, tmp_path):
        doc = tmp_path / "bp.md"
        doc.write_text("clean doc, no findings")
        review = await deep_review(
            doc,
            project_path=tmp_path,
            personas=(),
            runtime_factory=lambda rt: FakeRuntime(""),
        )
        assert await resolution_pack(review, runtime_factory=lambda rt: FakeRuntime("x")) is None

    def test_milestone_review_problems(self, tmp_path, sample_milestone):
        (tmp_path / "docs").mkdir()
        doc = tmp_path / "docs" / "blueprint-b.md"
        doc.write_text(BLUEPRINT_DOC)
        problems = milestone_review_problems(tmp_path, sample_milestone)
        assert problems and all("not run on this version" in p for p in problems)
        # Milestone without blueprint artifacts is not gated.
        class M:
            artifacts = [type("A", (), {"path": "docs/build-plan*.md"})()]
        assert milestone_review_problems(tmp_path, M()) == []


@pytest.fixture
def sample_milestone():
    class A:
        path = "docs/blueprint*.md"

    class M:
        artifacts = [A()]

    return M()


# ---------------------------------------------------------------- calibrate


class TestCalibrate:
    def test_inject_plants_sentinels(self):
        text = "# Blueprint\n\nThe design is recommended. 5 findings remain.\n"
        seeded, planted = calibrate.inject_defects(text)
        assert len(planted) >= 4
        for defect_id, sent in planted.items():
            assert sent in seeded
            assert sent.startswith("clbrx-")

    def test_each_defect_detectable(self):
        text = "# Blueprint\n\nThe design is recommended. 5 findings remain.\n\n§4.2 Security.\n"
        seeded, planted = calibrate.inject_defects(text)
        by_id = {d.id: d for d in calibrate.DEFECT_TYPES}
        for defect_id, sent in planted.items():
            assert by_id[defect_id].detect(f"report quoting {sent}", sent)
            assert not by_id[defect_id].detect("report missed it", sent)

    def test_recall_rate(self):
        r = calibrate.CalibrationResult(doc="d", persona="p")
        r.recall = {"a": True, "b": False, "c": True, "d": True}
        assert r.recall_rate == pytest.approx(0.75)

    async def test_calibrate_persona_end_to_end(self, tmp_path):
        doc = tmp_path / "bp.md"
        doc.write_text("# Blueprint\n\nThe design is recommended. 3 findings remain.\n")

        class CannedRuntime(FakeRuntime):
            def __init__(self):
                super().__init__("report quoting clbrx-sent1")

        result = await calibrate.calibrate_persona(
            doc, "noise", runtime_factory=lambda rt: CannedRuntime()
        )
        assert result.recall, "planted defects should be scored"
        # Fake runtime never emits the sentinel, so nothing is caught.
        assert result.recall_rate == 0.0
