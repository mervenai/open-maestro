"""Tests for the milestone-guided lifecycle module."""

from __future__ import annotations

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
import yaml

from open_maestro.milestones import (
    Artifact,
    Blocker,
    Epic,
    Milestone,
    MilestoneDetector,
    MilestonePlan,
    MilestoneStatus,
)
from open_maestro.milestones.commands import (
    format_prompt_context,
    handle_blocker_command,
    handle_complete_command,
    handle_next_command,
    handle_track_command,
)
from open_maestro.milestones.dashboard import (
    export_dashboard_html,
    export_dashboard_json,
    export_dashboard_markdown,
)
from open_maestro.milestones.publisher import DashboardPublisher, PublishError
from open_maestro.milestones.server import serve_dashboard, stop_dashboard_server
from open_maestro.milestones.store import MilestoneStore, _normalize_project_id
from open_maestro.milestones.templates import default_software_template


class TestModels:
    def test_milestone_completion_status(self):
        m = Milestone(id="m1", name="Test", order=1, weight=10, status=MilestoneStatus.COMPLETED)
        assert m.completion() == 100

        m.status = MilestoneStatus.NOT_STARTED
        assert m.completion() == 0

        m.status = MilestoneStatus.IN_PROGRESS
        assert m.completion() == 50

    def test_invalid_dates(self):
        with pytest.raises(ValueError):
            Milestone(
                id="m1",
                name="Test",
                order=1,
                weight=10,
                started_at=date(2026, 7, 10),
                completed_at=date(2026, 7, 1),
            )

    def test_epic_completion_averaged_across_milestones(self):
        epic = Epic(
            id="e1",
            name="Epic",
            order=1,
            milestones=[
                Milestone(id="m1", name="M1", order=1, weight=10, status=MilestoneStatus.COMPLETED),
                Milestone(id="m2", name="M2", order=2, weight=10, status=MilestoneStatus.NOT_STARTED),
            ],
        )
        assert epic.completion() == 50

    def test_duplicate_milestone_orders_in_epic(self):
        with pytest.raises(ValueError):
            Epic(
                id="e1",
                name="Epic",
                order=1,
                milestones=[
                    Milestone(id="m1", name="M1", order=1, weight=10),
                    Milestone(id="m2", name="M2", order=1, weight=10),
                ],
            )


class TestMilestonePlan:
    def test_default_template(self):
        plan = default_software_template("test-project")
        assert plan.project_id == "test-project"
        assert len(plan.epics) == 1
        assert plan.epics[0].id == "default"
        assert len(plan.epics[0].milestones) == 8
        assert plan.epics[0].milestones[0].id == "intake-discovery"
        assert plan.summary.overall_completion == 0

    def test_summary_recomputed(self):
        plan = default_software_template("test-project")
        plan.epics[0].milestones[0].status = MilestoneStatus.COMPLETED
        plan._recompute_summary()
        assert plan.summary.overall_completion == 10
        assert plan.summary.current_milestone_ids == []
        assert plan.summary.next_milestone_ids == ["default/execution-planning"]

    def test_overlapping_milestones(self):
        plan = default_software_template("test-project")
        plan.epics[0].milestones[4].status = MilestoneStatus.IN_PROGRESS  # implementation
        plan.epics[0].milestones[5].status = MilestoneStatus.IN_PROGRESS  # qa
        plan._recompute_summary()
        assert set(plan.summary.current_milestone_ids) == {"default/implementation", "default/qa-integration"}

    def test_blocker_rollup(self):
        plan = default_software_template("test-project")
        plan.epics[0].milestones[4].status = MilestoneStatus.BLOCKED
        plan.epics[0].milestones[4].blockers.append(
            Blocker(
                description="Missing test env",
                epic_id="default",
                milestone_id="implementation",
            )
        )
        plan._recompute_summary()
        assert len(plan.summary.active_blockers) == 1
        assert plan.summary.active_blockers[0].epic_id == "default"
        assert plan.summary.active_blockers[0].milestone_id == "implementation"

    def test_get_milestone(self):
        plan = default_software_template("test-project")
        assert plan.get_milestone("default", "implementation") is not None
        assert plan.get_milestone("default", "missing") is None

    def test_find_milestone(self):
        plan = default_software_template("test-project")
        epic, milestone = plan.find_milestone("implementation")
        assert epic.id == "default"
        assert milestone.id == "implementation"
        assert plan.find_milestone("missing") is None


class TestMilestoneStore:
    def test_creates_default_template(self, tmp_path):
        store = MilestoneStore(tmp_path)
        assert not store.exists()
        plan = store.load()
        assert store.exists()
        assert plan.project_id == tmp_path.name
        assert len(plan.epics) == 1
        assert len(plan.epics[0].milestones) == 8

    def test_roundtrip(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[0].status = MilestoneStatus.COMPLETED
        store.update(plan)

        loaded = store.load()
        assert loaded.epics[0].milestones[0].status == MilestoneStatus.COMPLETED
        assert loaded.summary.overall_completion == 10

    def test_export_dashboard(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[0].status = MilestoneStatus.COMPLETED
        plan.epics[0].milestones[1].status = MilestoneStatus.IN_PROGRESS
        store.update(plan)

        dashboard = store.export_dashboard(plan)
        assert dashboard["project_id"] == tmp_path.name
        # intake completed (10) + execution in-progress at 50% (5)
        assert dashboard["overall_completion"] == 15
        assert len(dashboard["epics"]) == 1
        assert dashboard["current_milestone"] == ["default/execution-planning"]

    def test_yaml_format(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        raw = yaml.safe_load(store.file_path.read_text())
        assert raw["project_id"] == tmp_path.name
        assert raw["schema_version"] == "2.0"
        assert len(raw["epics"]) == 1
        assert len(raw["epics"][0]["milestones"]) == 8

    def test_rejects_old_schema(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.open_maestro_dir.mkdir(parents=True, exist_ok=True)
        store.file_path.write_text(
            yaml.safe_dump({
                "project_id": tmp_path.name,
                "schema_version": "1.0",
                "milestones": [{"id": "m1", "name": "M1", "order": 1, "weight": 10}],
            }),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError):
            store.load()

    def test_project_id_normalization(self, tmp_path):
        project = tmp_path / "M3 Budget Upload"
        project.mkdir()
        store = MilestoneStore(project)
        plan = store.load()
        assert plan.project_id == "m3-budget-upload"


def test_normalize_project_id():
    assert _normalize_project_id("M3BudgetUpload") == "m3budgetupload"
    assert _normalize_project_id("My Project.Name") == "my-project-name"
    assert _normalize_project_id("---") == "project"


class TestMilestoneCommands:
    def test_next_shows_current_milestones(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[2].status = MilestoneStatus.IN_PROGRESS
        store.update(plan)

        result = handle_next_command(tmp_path)
        assert "Design Blueprint" in result
        assert "Exit criteria:" in result

    def test_next_shows_next_when_nothing_in_progress(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        result = handle_next_command(tmp_path)
        assert "Intake & Discovery" in result

    def test_complete_mark_milestone_completed(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        (tmp_path / "requirements").mkdir()
        (tmp_path / "requirements" / "PRD.md").write_text("PRD")

        result = handle_complete_command(tmp_path, ["intake-discovery"])
        assert "completed" in result
        plan = store.load()
        assert plan.get_milestone("default", "intake-discovery").status == MilestoneStatus.COMPLETED

    def test_complete_warns_when_artifacts_missing(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        result = handle_complete_command(tmp_path, ["intake-discovery"])
        assert "Not all required artifacts" in result

    def test_complete_force_overrides_warning(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        result = handle_complete_command(tmp_path, ["intake-discovery", "--force"])
        assert "completed" in result

    def test_complete_with_epic_prefix(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        result = handle_complete_command(tmp_path, ["default/intake-discovery", "--force"])
        assert "completed" in result
        plan = store.load()
        assert plan.get_milestone("default", "intake-discovery").status == MilestoneStatus.COMPLETED

    def test_blocker_records_blocker(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        result = handle_blocker_command(tmp_path, ["implementation", "Missing test env"])
        assert "Recorded blocker" in result
        plan = store.load()
        assert len(plan.summary.active_blockers) == 1

    def test_track_updates_milestone_status(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        result = handle_track_command(tmp_path, ["design-blueprint", "in_progress"])
        assert "Updated milestone" in result
        plan = store.load()
        assert plan.get_milestone("default", "design-blueprint").status == MilestoneStatus.IN_PROGRESS

    def test_prompt_context_includes_current_milestones(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[2].status = MilestoneStatus.IN_PROGRESS
        store.update(plan)

        context = format_prompt_context(tmp_path)
        assert "Project milestone context" in context
        assert "Design Blueprint" in context

    def test_prompt_context_empty_when_no_plan(self, tmp_path):
        context = format_prompt_context(tmp_path / "nonexistent")
        assert context == ""


class TestMilestoneDetector:
    def test_detects_required_artifact(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        # Create a requirements file to satisfy intake-discovery.
        (tmp_path / "requirements").mkdir()
        (tmp_path / "requirements" / "PRD.md").write_text("PRD")

        detector = MilestoneDetector(tmp_path)
        suggestions = detector.detect(plan)
        by_key = {(s.epic_id, s.milestone_id): s for s in suggestions}
        assert by_key[("default", "intake-discovery")].suggested_status == MilestoneStatus.COMPLETED

    def test_detects_in_progress(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        # Create only one required artifact for design-blueprint.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "blueprint.md").write_text("blueprint")

        detector = MilestoneDetector(tmp_path)
        suggestions = detector.detect(plan)
        by_key = {(s.epic_id, s.milestone_id): s for s in suggestions}
        assert by_key[("default", "design-blueprint")].suggested_status == MilestoneStatus.IN_PROGRESS

    def test_ordering_constraint_prevents_later_complete(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        # Only QA artifacts present, no earlier milestones.
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "qa").mkdir()
        (tmp_path / "docs" / "qa" / "report.md").write_text("report")

        detector = MilestoneDetector(tmp_path)
        suggestions = detector.detect(plan)
        by_key = {(s.epic_id, s.milestone_id): s for s in suggestions}
        assert by_key[("default", "qa-integration")].suggested_status == MilestoneStatus.IN_PROGRESS

    def test_apply_suggestions_confirmed_only(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        (tmp_path / "requirements").mkdir()
        (tmp_path / "requirements" / "PRD.md").write_text("PRD")

        detector = MilestoneDetector(tmp_path)
        suggestions = detector.detect(plan)
        plan = detector.apply_suggestions(
            plan, suggestions, confirmed_ids={"default/intake-discovery"}
        )
        assert plan.get_milestone("default", "intake-discovery").status == MilestoneStatus.COMPLETED
        assert plan.get_milestone("default", "execution-planning").status == MilestoneStatus.NOT_STARTED

    def test_detected_flag_set_on_artifacts(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        (tmp_path / "requirements").mkdir()
        (tmp_path / "requirements" / "PRD.md").write_text("PRD")

        detector = MilestoneDetector(tmp_path)
        detector.detect(plan)
        milestone = plan.get_milestone("default", "intake-discovery")
        assert any(a.path == "requirements/*" and a.detected for a in milestone.artifacts)

    def test_no_git_history_does_not_crash(self, tmp_path):
        detector = MilestoneDetector(tmp_path)
        assert detector.git_commit_dates() == []


class TestDashboard:
    def test_json_export(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[0].status = MilestoneStatus.COMPLETED
        store.update(plan)
        data = json.loads(export_dashboard_json(plan))
        assert data["project_id"] == tmp_path.name
        assert data["overall_completion"] == 10
        assert len(data["epics"]) == 1
        assert len(data["epics"][0]["milestones"]) == 8

    def test_markdown_export(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[0].status = MilestoneStatus.COMPLETED
        store.update(plan)
        md = export_dashboard_markdown(plan)
        assert "# Test" in md or "Project Dashboard" in md
        assert "Overall completion:** 10%" in md
        assert "Intake & Discovery" in md
        assert "Epics" in md

    def test_html_export_uses_merven_colors(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        html = export_dashboard_html(plan)
        assert "#02040b" in html  # merven background
        assert "#00bfaf" in html  # merven primary teal
        assert "Inter" in html    # merven font
        assert "Project progress dashboard" in html

    def test_html_escapes_content(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.project_id = "test"
        plan.epics[0].milestones[0].name = "<script>alert('xss')</script>"
        html = export_dashboard_html(plan)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestDashboardServer:
    def test_server_serves_html_and_json(self, tmp_path):
        store = MilestoneStore(tmp_path)
        store.load()
        server = serve_dashboard(tmp_path, host="127.0.0.1", port=18080, blocking=False)
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:18080/", timeout=5) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
                assert "Project progress dashboard" in body

            with urllib.request.urlopen(
                "http://127.0.0.1:18080/api/dashboard", timeout=5
            ) as resp:
                assert resp.status == 200
                assert resp.headers.get_content_type() == "application/json"
        finally:
            stop_dashboard_server(server)


class TestMervenSync:
    def test_sync_preserves_local_progress_when_merven_is_pending(self, tmp_path):
        """Re-syncing must not reset locally-recorded progress when Merven still says pending."""
        from open_maestro.milestones.merven_sync import _plan_from_merven_payload

        existing = _plan_from_merven_payload(
            "proj-123",
            {
                "project_id": "proj-123",
                "name": "Test Project",
                "epics": [
                    {
                        "name": "Import Flow",
                        "milestones": [],
                    }
                ],
            },
        )
        existing.epics[0].milestones[0].status = MilestoneStatus.IN_PROGRESS
        store = MilestoneStore(tmp_path)
        store.save(existing)

        refreshed = _plan_from_merven_payload(
            "proj-123",
            {
                "project_id": "proj-123",
                "name": "Test Project",
                "epics": [
                    {
                        "name": "Import Flow",
                        "milestones": [],
                    }
                ],
            },
        )

        # Simulate the merge logic from sync_from_merven.
        from open_maestro.milestones.merven_sync import _merge_status

        existing_by_key = {
            (e.id, m.id): m for e in existing.epics for m in e.milestones
        }
        for epic in refreshed.epics:
            for milestone in epic.milestones:
                local = existing_by_key.get((epic.id, milestone.id))
                if local is not None:
                    milestone.status = _merge_status(local.status, milestone.status)

        # Local IN_PROGRESS beats Merven pending.
        assert refreshed.epics[0].milestones[0].status == MilestoneStatus.IN_PROGRESS

    def test_sync_uses_merven_completion(self):
        """Merven's done/acked status must advance local in-progress milestones."""
        from open_maestro.milestones.merven_sync import _merge_status

        assert (
            _merge_status(MilestoneStatus.IN_PROGRESS, MilestoneStatus.COMPLETED)
            == MilestoneStatus.COMPLETED
        )
        assert (
            _merge_status(MilestoneStatus.NOT_STARTED, MilestoneStatus.COMPLETED)
            == MilestoneStatus.COMPLETED
        )

    def test_sync_creates_standard_milestones_per_epic(self):
        from open_maestro.milestones.merven_sync import _plan_from_merven_payload

        plan = _plan_from_merven_payload(
            "proj-123",
            {
                "project_id": "proj-123",
                "name": "Test Project",
                "epics": [
                    {"name": "Import Flow"},
                    {"name": "Audit Log"},
                ],
            },
        )
        assert len(plan.epics) == 2
        assert plan.epics[0].id == "import-flow"
        assert plan.epics[1].id == "audit-log"
        assert len(plan.epics[0].milestones) == 8
        assert plan.epics[1].milestones[0].id == "intake-discovery"


class TestDashboardPublisher:
    def test_publishes_dashboard_to_mock_receiver(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        plan.epics[0].milestones[0].status = MilestoneStatus.COMPLETED
        store.update(plan)

        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                from urllib.parse import urlparse

                parsed = urlparse(self.path)
                if parsed.path == "/dashboard":
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    received["body"] = body
                    received["auth"] = self.headers.get("Authorization")
                    received["project"] = self.headers.get("X-Maestro-Project-Token")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"received": true}')
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", 18082), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            publisher = DashboardPublisher(
                url="http://127.0.0.1:18082/dashboard",
                api_key="secret",
                project_token="proj-123",
            )
            response = publisher.publish(plan)
            assert response == {"received": True}
            assert received["auth"] == "Bearer secret"
            assert received["project"] == "proj-123"
            data = json.loads(received["body"])
            assert data["dashboard"]["overall_completion"] == 10
        finally:
            server.shutdown()
            server.server_close()

    def test_missing_url_raises(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        publisher = DashboardPublisher()
        with pytest.raises(PublishError):
            publisher.publish(plan)

    def test_failed_publish_raises(self, tmp_path):
        store = MilestoneStore(tmp_path)
        plan = store.load()
        publisher = DashboardPublisher(url="http://127.0.0.1:18083/noop")
        with pytest.raises(PublishError):
            publisher.publish(plan, timeout=1.0)
