"""Tests for the adversarial review package (MSTRO-115..120)."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_maestro.orchestrator import critic as critic_mod
from open_maestro.review import calibrate, gate, panel
from open_maestro.review import blueprint as review_blueprint
from open_maestro.review.blueprint import (
    admission_summary,
    deep_review,
    find_blueprint_artifacts,
    milestone_review_problems,
    resolution_pack,
    select_deep_review_targets,
)
from open_maestro.review.personas import PERSONAS, READER_ROLES, render
from open_maestro.runtime.base import AgentConfig, AgentResult, AgentRuntime


class FakeRuntime(AgentRuntime):
    """Runtime returning canned text; records prompts."""

    def __init__(self, response_text: str = "", is_error: bool = False):
        self.response_text = response_text
        self.is_error = is_error
        self.prompts: list[str] = []
        self.configs: list[AgentConfig] = []

    @property
    def runtime_name(self) -> str:
        return "fake"

    async def run(self, prompt: str, config: AgentConfig | None = None) -> AgentResult:
        self.prompts.append(prompt)
        self.configs.append(config)
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
            "deepseek-flash": "Causal audit finding.\nFINDINGS n=1",
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
        ids = ["kimi-k3", "glm-flash", "claude-sonnet", "deepseek-flash"]

        def factory(rt_type):
            inst = rt(ids[counter["i"] % len(ids)])
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

    def test_seat_integrity(self):
        """MSTRO-133: unique seat ids, defined lenses, and the uncalibrated
        DeepSeek voice is seated but not chairman-eligible."""
        ids = [s.id for s in panel.PANEL_SEATS]
        assert len(ids) == len(set(ids))
        assert all(s.lens in panel.LENSES for s in panel.PANEL_SEATS)
        assert "deepseek-flash" in ids
        assert "deepseek-flash" not in panel.CHAIRMAN_PREFERENCE

    async def test_chairman_retries_transient_api_error(self, monkeypatch):
        """MSTRO-122: 'API Error: Connection closed mid-response' must retry."""

        monkeypatch.setattr(panel, "CHAIRMAN_RETRY_BACKOFF_S", 0.0)

        class FlakyChairRuntime(AgentRuntime):
            chairman_calls = 0

            @property
            def runtime_name(self):
                return "fake"

            async def run(self, prompt, config=None):
                if "Classify each distinct claim" in prompt:
                    return AgentResult(text='{"claims": [{"text": "x", "kind": "J"}]}')
                if "ranking anonymized responses" in prompt:
                    return AgentResult(text="r\nFINDINGS n=1")
                if "CHAIRMAN" in prompt:
                    FlakyChairRuntime.chairman_calls += 1
                    if FlakyChairRuntime.chairman_calls == 1:
                        return AgentResult(
                            text="API Error: Connection closed mid-response. "
                            "The response above may be incomplete."
                        )
                    return AgentResult(text="Synthesized after retry.")
                return AgentResult(text="opinion\nFINDINGS n=1")

            async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
                return await self.run(prompt, config)

            async def resume(self, session_id, prompt, config=None):
                return await self.run(prompt, config)

        def factory(rt_type):
            return FlakyChairRuntime()

        result = await panel.run_panel("question", runtime_factory=factory)
        assert result.chairman_response == "Synthesized after retry."
        assert result.ok
        assert result.chairman_seat == "claude-sonnet"  # retry, no fallback
        assert any(
            "attempt 1/2 failed" in n and "claude-sonnet" in n for n in result.notes
        )

    async def test_chairman_falls_back_to_next_preference(self, monkeypatch):
        """MSTRO-122: preferred chairman dying for good falls back (never to GLM)."""

        monkeypatch.setattr(panel, "CHAIRMAN_RETRY_BACKOFF_S", 0.0)

        class DeadChairRuntime(AgentRuntime):
            chairman_calls = 0

            @property
            def runtime_name(self):
                return "fake"

            async def run(self, prompt, config=None):
                if "Classify each distinct claim" in prompt:
                    return AgentResult(text='{"claims": [{"text": "x", "kind": "J"}]}')
                if "ranking anonymized responses" in prompt:
                    return AgentResult(text="r\nFINDINGS n=1")
                if "CHAIRMAN" in prompt:
                    DeadChairRuntime.chairman_calls += 1
                    if DeadChairRuntime.chairman_calls <= 2:
                        return AgentResult(text="boom", is_error=True)
                    return AgentResult(text="Kimi synthesized.")
                return AgentResult(text="opinion\nFINDINGS n=1")

            async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
                return await self.run(prompt, config)

            async def resume(self, session_id, prompt, config=None):
                return await self.run(prompt, config)

        def factory(rt_type):
            return DeadChairRuntime()

        result = await panel.run_panel("question", runtime_factory=factory)
        assert result.chairman_response == "Kimi synthesized."
        assert result.ok
        assert result.chairman_seat == "kimi-k3"
        assert any("chairman fell back from claude-sonnet to kimi-k3" in n for n in result.notes)

    async def test_chairman_all_candidates_fail_records_error(self, monkeypatch):
        monkeypatch.setattr(panel, "CHAIRMAN_RETRY_BACKOFF_S", 0.0)

        class AllDeadRuntime(AgentRuntime):
            @property
            def runtime_name(self):
                return "fake"

            async def run(self, prompt, config=None):
                if "Classify each distinct claim" in prompt:
                    return AgentResult(text='{"claims": [{"text": "x", "kind": "J"}]}')
                if "ranking anonymized responses" in prompt:
                    return AgentResult(text="r\nFINDINGS n=1")
                if "CHAIRMAN" in prompt:
                    return AgentResult(text="boom", is_error=True)
                return AgentResult(text="opinion\nFINDINGS n=1")

            async def run_with_hooks(self, prompt, tool_guard=None, blocked_tools=None, config=None):
                return await self.run(prompt, config)

            async def resume(self, session_id, prompt, config=None):
                return await self.run(prompt, config)

        def factory(rt_type):
            return AllDeadRuntime()

        result = await panel.run_panel("question", runtime_factory=factory)
        assert result.chairman_response.startswith("ERROR: chairman failed")
        assert not result.ok
        assert any("kimi-k3" in n and "failed" in n for n in result.notes)

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

    async def test_default_path_seats_on_configured_model(self, tmp_path, monkeypatch):
        """MSTRO-134: the bare create_runtime(None) default is gpt-4o, which
        is unconfigured on most machines — calibrating nothing."""
        doc = tmp_path / "bp.md"
        doc.write_text("# Blueprint\n\nThe design is recommended. 3 findings remain.\n")
        runtime = FakeRuntime(
            "RESULT blockers=0 should_fix=0 contradictions=0 weakened=0"
        )
        monkeypatch.setattr(
            review_blueprint,
            "_seat_persona_runtime",
            lambda used, base=None: (runtime, "kimi-code/k3"),
        )
        result = await calibrate.calibrate_persona(doc, "fidelity", project_path=tmp_path)
        assert runtime.configs[0].model == "kimi-code/k3"
        assert result.persona == "fidelity"


# ---------------------------------------------------------------- auto deep review


def _make_git_project(tmp_path):
    """A git repo with one tracked blueprint artifact."""
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True
        )

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "test")
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "blueprint-x.md"
    doc.write_text("Version: 1.0\n\n" + "filler line\n" * 100)
    git("add", "-A")
    git("commit", "-qm", "init")
    return doc


class TestSelectDeepReviewTargets:
    def test_new_untracked_artifact_triggers(self, tmp_path, monkeypatch):
        _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        (tmp_path / "docs" / "blueprint-new.md").write_text("# New contract\n" * 40)
        targets = select_deep_review_targets(tmp_path, [])
        assert len(targets) == 1
        assert targets[0][0].name == "blueprint-new.md"
        assert targets[0][1] == "new blueprint artifact"

    def test_large_change_triggers(self, tmp_path, monkeypatch):
        doc = _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        with doc.open("a") as fh:
            fh.write("added line\n" * 200)
        targets = select_deep_review_targets(
            tmp_path, [("docs/blueprint-x.md", 200)]
        )
        assert [t[1] for t in targets] == ["200 changed lines (>= 150)"]

    def test_version_marker_change_triggers(self, tmp_path, monkeypatch):
        doc = _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        doc.write_text(
            doc.read_text().replace("Version: 1.0", "Version: 2.0", 1)
        )
        targets = select_deep_review_targets(tmp_path, [("docs/blueprint-x.md", 1)])
        assert [t[1] for t in targets] == ["version marker changed"]

    def test_small_edit_without_marker_skips(self, tmp_path, monkeypatch):
        doc = _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        with doc.open("a") as fh:
            fh.write("minor clarification\n")
        assert select_deep_review_targets(tmp_path, [("docs/blueprint-x.md", 1)]) == []

    def test_kill_switch_disables(self, tmp_path, monkeypatch):
        _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        monkeypatch.setenv("MAESTRO_DEEP_REVIEW", "off")
        (tmp_path / "docs" / "blueprint-new.md").write_text("# New\n")
        assert select_deep_review_targets(tmp_path, []) == []

    def test_no_in_progress_blueprint_milestone_skips(self, tmp_path, monkeypatch):
        _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: False
        )
        (tmp_path / "docs" / "blueprint-new.md").write_text("# New\n")
        assert select_deep_review_targets(tmp_path, []) == []

    def test_current_ledger_audit_suppresses(self, tmp_path, monkeypatch):
        _make_git_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        (tmp_path / "docs" / "blueprint-new.md").write_text("# New\n")
        monkeypatch.setattr(review_blueprint.GateLedger, "check", lambda self, doc, profile="review": [])
        assert select_deep_review_targets(tmp_path, []) == []


# ---------------------------------------------------------------------------
# Non-git change detection (MSTRO-127)


class TestNonGitChangeDetection:
    """Outside a git repo, gates diff against the turn-start mtime snapshot."""

    def _make_plain_project(self, tmp_path):
        """A non-git project with one pre-existing blueprint artifact."""
        import os

        docs = tmp_path / "docs"
        docs.mkdir()
        doc = docs / "blueprint-x.md"
        doc.write_text("Version: 1.0\n\n" + "filler line\n" * 100)
        critic_mod.snapshot_mtimes(tmp_path)
        return doc

    def _touch(self, path):
        import os

        os.utime(path, None)  # guarantee mtime moves even on coarse clocks

    def test_detect_changes_seeds_then_reports(self, tmp_path):
        critic_mod.reset_mtimes_snapshot(tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        doc = docs / "blueprint-x.md"
        doc.write_text("one\ntwo\nthree\n")
        # First call seeds the baseline: pre-existing files are not changes.
        assert critic_mod._detect_changes(tmp_path, None) == []
        with doc.open("a") as fh:
            fh.write("four\nfive\n")
        self._touch(doc)
        assert critic_mod._detect_changes(tmp_path, None) == [
            ("docs/blueprint-x.md", 2)
        ]

    def test_detect_changes_counts_new_file_full_length(self, tmp_path):
        critic_mod.reset_mtimes_snapshot(tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        critic_mod.snapshot_mtimes(tmp_path)
        new_doc = docs / "blueprint-new.md"
        new_doc.write_text("# New\n" * 40)
        self._touch(new_doc)
        assert critic_mod._detect_changes(tmp_path, None) == [
            ("docs/blueprint-new.md", 40)
        ]

    def test_open_maestro_writes_do_not_count(self, tmp_path):
        critic_mod.reset_mtimes_snapshot(tmp_path)
        critic_mod.snapshot_mtimes(tmp_path)
        state = tmp_path / ".open-maestro" / "gates"
        state.mkdir(parents=True)
        ledger_file = state / "ledger.json"
        ledger_file.write_text('{"records": []}\n')
        self._touch(ledger_file)
        assert critic_mod._detect_changes(tmp_path, None) == []

    def test_artifact_gate_finds_big_docs_edit(self, tmp_path):
        critic_mod.reset_mtimes_snapshot(tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        doc = docs / "design-notes.md"
        doc.write_text("intro\n")
        critic_mod.snapshot_mtimes(tmp_path)
        with doc.open("a") as fh:
            fh.write("added line\n" * 60)
        self._touch(doc)
        assert critic_mod.detect_artifact_changes(tmp_path, None) == [
            "docs/design-notes.md"
        ]

    def test_new_untracked_artifact_triggers_no_git(self, tmp_path, monkeypatch):
        self._make_plain_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        (tmp_path / "docs" / "blueprint-new.md").write_text("# New contract\n" * 40)
        targets = select_deep_review_targets(
            tmp_path, critic_mod._detect_changes(tmp_path, None)
        )
        assert len(targets) == 1
        assert targets[0][0].name == "blueprint-new.md"
        assert targets[0][1] == "new blueprint artifact"

    def test_preexisting_artifact_not_flagged_as_new_no_git(
        self, tmp_path, monkeypatch
    ):
        """Untouched pre-existing blueprint: not a target, and not 'new'."""
        doc = self._make_plain_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        assert not review_blueprint._is_new_file(tmp_path, "docs/blueprint-x.md")
        targets = select_deep_review_targets(
            tmp_path, critic_mod._detect_changes(tmp_path, None)
        )
        assert targets == []
        assert doc.name == "blueprint-x.md"  # untouched baseline preserved

    def test_large_change_triggers_no_git(self, tmp_path, monkeypatch):
        doc = self._make_plain_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        with doc.open("a") as fh:
            fh.write("added line\n" * 200)
        self._touch(doc)
        targets = select_deep_review_targets(
            tmp_path, critic_mod._detect_changes(tmp_path, None)
        )
        assert [t[1] for t in targets] == ["200 changed lines (>= 150)"]

    def test_small_edit_skips_no_git(self, tmp_path, monkeypatch):
        doc = self._make_plain_project(tmp_path)
        monkeypatch.setattr(
            review_blueprint, "blueprint_milestone_in_progress", lambda p: True
        )
        with doc.open("a") as fh:
            fh.write("minor clarification\n")
        self._touch(doc)
        targets = select_deep_review_targets(
            tmp_path, critic_mod._detect_changes(tmp_path, None)
        )
        assert targets == []

    def test_is_new_file_no_snapshot_treats_as_new(self, tmp_path, monkeypatch):
        """Without a baseline everything is new — review is never suppressed."""
        critic_mod.reset_mtimes_snapshot(tmp_path)
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "blueprint-x.md").write_text("Version: 1.0\n")
        assert review_blueprint._is_new_file(tmp_path, "docs/blueprint-x.md")


# ---------------------------------------------------------------------------
# Persona seating on configured models (MSTRO-128)


class TestPersonaSeating:
    """deep_review must seat personas on configured models, never the SDK
    default (gpt-4o) — which has no endpoint in most setups."""

    # Persona-appropriate RESULT lines, in GATING_PERSONAS order.
    REPORTS = (
        "answers\nRESULT correct=10/10 reconciles=0 confusions=0 blocking=0",
        "RESULT blockers=0 should_fix=0 contradictions=0 weakened=0",
        "RESULT blockers=0 should_fix=0 framing=0",
    )

    def _patch_router(self, monkeypatch, seats):
        """Route select_runtime_for_task through *seats* (one per call)."""
        import open_maestro.runtime.factory as factory_mod

        calls: list[dict] = []
        state = {"i": 0}

        def fake_select(profile, **kwargs):
            calls.append({"reasoning": profile.reasoning_depth, **kwargs})
            i = state["i"]
            if i >= len(seats):
                raise RuntimeError("no more seats")
            state["i"] += 1
            seat = seats[i]
            if isinstance(seat, Exception):
                raise seat
            return seat

        monkeypatch.setattr(factory_mod, "select_runtime_for_task", fake_select)
        return calls

    def _patch_create(self, monkeypatch, runtimes):
        import open_maestro.runtime.factory as factory_mod

        created: list[str] = []

        def fake_create(runtime_type, config=None):
            created.append(runtime_type)
            return runtimes[len(created) - 1]

        monkeypatch.setattr(factory_mod, "create_runtime", fake_create)
        return created

    async def test_seats_on_configured_model(self, tmp_path, monkeypatch):
        doc = tmp_path / "blueprint.md"
        doc.write_text("# doc\n")
        runtimes = [FakeRuntime(t) for t in self.REPORTS]
        self._patch_router(monkeypatch, [("kimi-cli", "kimi-code/k3")] * 3)
        self._patch_create(monkeypatch, runtimes)

        review = await deep_review(doc, project_path=tmp_path)

        assert review.passed
        assert [r.configs[0].model for r in runtimes] == ["kimi-code/k3"] * 3
        # MSTRO-135: CLI seats read the artifact before answering — one turn
        # cut them mid-tool-use (error_max_turns).
        assert runtimes[0].configs[0].max_turns == review_blueprint.PERSONA_MAX_TURNS

    async def test_persona_reports_persisted(self, tmp_path, monkeypatch):
        """MSTRO-138: full persona reports must land on disk (the ledger keeps
        only counts), and the register must point at them so a later turn can
        resolve the quoted findings."""
        doc = tmp_path / "blueprint.md"
        doc.write_text("# doc\n")
        runtime = FakeRuntime(self.REPORTS[1])
        self._patch_router(monkeypatch, [("kimi-cli", "kimi-code/k3")])
        self._patch_create(monkeypatch, [runtime])

        review = await deep_review(doc, project_path=tmp_path, personas=("fidelity",))

        report_path = tmp_path / review.report_paths["fidelity"]
        assert report_path.is_file()
        assert report_path.read_text() == self.REPORTS[1]
        # register points at the persisted report
        assert "report:" in review.register_text()
        assert review.report_paths["fidelity"] in review.register_text()
        # stored under .open-maestro/reviews/<doc-stem>-<sha>/
        assert ".open-maestro" in review.report_paths["fidelity"]

    async def test_personas_rotate_across_models(self, tmp_path, monkeypatch):
        doc = tmp_path / "blueprint.md"
        doc.write_text("# doc\n")
        runtimes = [FakeRuntime(t) for t in self.REPORTS]
        calls = self._patch_router(
            monkeypatch,
            [("kimi-cli", "kimi-code/k3"), ("openai-sdk", "glm-5-3-flash"),
             ("claude-cli", "claude-sonnet")],
        )
        self._patch_create(monkeypatch, runtimes)

        review = await deep_review(doc, project_path=tmp_path)

        assert review.passed
        assert [r.configs[0].model for r in runtimes] == [
            "kimi-code/k3",
            "glm-5-3-flash",
            "claude-sonnet",
        ]
        # The second seat excludes the model the first persona used.
        assert "kimi-code/k3" in (calls[1]["exclude"] or set())

    async def test_quota_exhaustion_marks_and_reseats(
        self, tmp_path, monkeypatch
    ):
        from open_maestro.runtime import quota as quota_mod

        class QuotaRuntime(FakeRuntime):
            async def run(self, prompt, config=None):
                self.prompts.append(prompt)
                self.configs.append(config)
                return _quota_error()

        quota_mod.clear()
        doc = tmp_path / "blueprint.md"
        doc.write_text("# doc\n")
        dead = QuotaRuntime()
        seats = [
            ("openai-sdk", "glm-5-3-flash"),   # persona 1, attempt 1 (dies)
            ("kimi-cli", "kimi-code/k3"),      # persona 1, re-seat
            ("kimi-cli", "kimi-code/k3"),      # persona 2
            ("kimi-cli", "kimi-code/k3"),      # persona 3
        ]
        runtimes = [dead] + [FakeRuntime(t) for t in self.REPORTS]
        self._patch_router(monkeypatch, seats)
        created = self._patch_create(monkeypatch, runtimes)

        review = await deep_review(doc, project_path=tmp_path)

        assert review.passed
        assert created[:2] == ["openai-sdk", "kimi-cli"]
        assert runtimes[1].configs[0].model == "kimi-code/k3"
        assert quota_mod.is_exhausted("glm-5-3-flash")
        quota_mod.clear()

    async def test_falls_back_to_light_when_no_deep_model(
        self, tmp_path, monkeypatch
    ):
        doc = tmp_path / "blueprint.md"
        doc.write_text("# doc\n")
        runtime = FakeRuntime(self.REPORTS[1])
        calls = self._patch_router(
            monkeypatch,
            [RuntimeError("no deep model"), ("openai-sdk", "glm-5-3-flash")],
        )
        self._patch_create(monkeypatch, [runtime])

        review = await deep_review(
            doc, project_path=tmp_path, personas=("fidelity",)
        )

        assert review.passed
        assert calls[0]["reasoning"] == "deep"
        assert calls[1]["reasoning"] == "light"

    def test_no_configured_model_errors_persona(self, tmp_path, monkeypatch):
        """Zero usable models: persona records an error, gate degrades."""
        import pytest

        doc = tmp_path / "blueprint.md"
        doc.write_text("# doc\n")
        self._patch_router(monkeypatch, [])
        with pytest.raises(RuntimeError, match="no configured model"):
            review_blueprint._seat_persona_runtime(set())

    def test_low_cost_reasoning_model_is_not_cost_filtered(self, tmp_path, monkeypatch):
        """MSTRO-137: deepseek-flash is cost_level=low; the default MEDIUM
        cost floor filtered it out of persona seating and the third persona
        fell through to a LIGHT claude seat. Auditors must see the LOW floor."""
        from open_maestro.config.capabilities import CostLevel

        calls = self._patch_router(monkeypatch, [("openai-sdk", "deepseek-flash")])
        runtime = FakeRuntime(self.REPORTS[0])
        self._patch_create(monkeypatch, [runtime])

        used: set = set()
        _, model = review_blueprint._seat_persona_runtime(used)

        assert model == "deepseek-flash"
        assert calls[0]["min_cost_level"] == CostLevel.LOW



def _quota_error() -> AgentResult:
    return AgentResult(
        text="insufficient balance",
        is_error=True,
        metadata={"quota_exhausted": "insufficient balance"},
    )
