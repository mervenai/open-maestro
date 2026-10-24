"""Tests for the maestro MCP server and the mutation service beneath it."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from open_maestro.mcp import server as mcp_server
from open_maestro.mcp.init_config import write_mcp_config
from open_maestro.milestones import mutations
from open_maestro.milestones.models import (
    Blocker,
    Epic,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
)
from open_maestro.milestones.store import MilestoneStore


def _sample_plan(project_path: Path) -> MilestonePlan:
    plan = MilestonePlan(
        project_id="demo",
        project_name="Demo",
        project_path=str(project_path),
        epics=[
            Epic(
                id="default",
                name="Default Track",
                order=1,
                milestones=[
                    Milestone(
                        id="intake-discovery",
                        name="Intake & Discovery",
                        order=1,
                        weight=10,
                        status=MilestoneStatus.COMPLETED,
                    ),
                    Milestone(
                        id="design-blueprint",
                        name="Design Blueprint",
                        order=2,
                        weight=15,
                        status=MilestoneStatus.IN_PROGRESS,
                        blockers=[
                            Blocker(
                                id="2026-10-20T00:00:00.000001",
                                description="OPEN-1: waiting on Product sign-off",
                                epic_id="default",
                                milestone_id="design-blueprint",
                                created_at="2026-10-20T00:00:00",
                            )
                        ],
                    ),
                    Milestone(
                        id="build-planning",
                        name="Build Planning",
                        order=3,
                        weight=10,
                        status=MilestoneStatus.NOT_STARTED,
                    ),
                ],
            )
        ],
    )
    store = MilestoneStore(project_path)
    store.save(plan)
    return store.load()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".open-maestro").mkdir()
    _sample_plan(tmp_path)
    return tmp_path


# --- mutation service -------------------------------------------------------


def test_open_session_refuses_missing_plan(tmp_path: Path):
    with pytest.raises(mutations.NoPlanError):
        mutations.open_session(tmp_path)


def test_add_blocker_updates_summary(project: Path):
    session = mutations.open_session(project)
    result = mutations.add_blocker(
        session, "default", "build-planning", "NEW-1: DnA date outstanding"
    )
    mutations.commit(session)

    fresh = MilestoneStore(project).load()
    assert len(fresh.summary.active_blockers) == 2
    assert result["location"] == "default/build-planning"
    assert "NEW-1" in result["memory"]


def test_resolve_blocker_appends_dated_resolution(project: Path):
    session = mutations.open_session(project)
    result = mutations.resolve_blocker(
        session,
        "2026-10-20T00:00:00.000001",
        "Product confirmed sign-off by email",
        when="2026-10-24",
    )
    mutations.commit(session)

    assert result["resolved_at"] == "2026-10-24"
    fresh = MilestoneStore(project).load()
    milestone = fresh.get_milestone("default", "design-blueprint")
    blocker = milestone.blockers[0]
    assert blocker.resolved_at is not None
    assert "Resolved 2026-10-24: Product confirmed" in blocker.description
    # two-list invariant after recompute
    assert fresh.summary.active_blockers == []


def test_resolve_unknown_blocker_lists_active_ids(project: Path):
    session = mutations.open_session(project)
    with pytest.raises(mutations.UnknownRefError) as exc:
        mutations.resolve_blocker(session, "nope", "done")
    assert "2026-10-20T00:00:00.000001" in str(exc.value)


def test_cannot_resolve_twice(project: Path):
    session = mutations.open_session(project)
    mutations.resolve_blocker(session, "2026-10-20T00:00:00.000001", "first", "2026-10-24")
    with pytest.raises(mutations.UnknownRefError):
        mutations.resolve_blocker(session, "2026-10-20T00:00:00.000001", "again")


def test_set_milestone_status_with_note(project: Path):
    session = mutations.open_session(project)
    result = mutations.set_milestone_status(
        session,
        "default",
        "build-planning",
        "in_progress",
        note="kicked off after client call",
        when="2026-10-24",
    )
    mutations.commit(session)

    assert result["old_status"] == "not_started"
    fresh = MilestoneStore(project).load()
    milestone = fresh.get_milestone("default", "build-planning")
    assert milestone.status == MilestoneStatus.IN_PROGRESS
    assert milestone.started_at is not None
    assert "[2026-10-24] kicked off after client call" in milestone.notes
    assert "default/build-planning" in fresh.summary.current_milestone_ids


def test_set_milestone_status_rejects_bad_status(project: Path):
    session = mutations.open_session(project)
    with pytest.raises(mutations.UnknownRefError):
        mutations.set_milestone_status(session, "default", "build-planning", "done-ish")


def test_drift_guard_refuses_stale_write(project: Path):
    session = mutations.open_session(project)
    mutations.add_blocker(session, "default", "build-planning", "DRIFT-TEST")

    # Someone else writes in between.
    other = mutations.open_session(project)
    mutations.add_blocker(other, "default", "build-planning", "concurrent edit")
    mutations.commit(other)

    with pytest.raises(mutations.DriftError):
        mutations.commit(session)


def test_compact_state_lists_everything(project: Path):
    session = mutations.open_session(project)
    state = mutations.compact_state(session)
    assert "Demo" in state
    assert "design-blueprint(active)" in state
    assert "active blockers (1)" in state
    assert "OPEN-1" in state


# --- MCP tools (in-memory transport) ----------------------------------------


def _call(client_cm, name: str, arguments: dict) -> str:
    async def _run():
        async with client_cm as session:
            result = await session.call_tool(name, arguments)
            text = result.content[0].text
            if result.isError:
                raise RuntimeError(text)
            return text

    return asyncio.run(_run())


@pytest.fixture
def make_client(project: Path, monkeypatch):
    # Tools resolve the project root from the environment.
    monkeypatch.setenv("MAESTRO_PROJECT_ROOT", str(project))
    # No kuzu-memory CLI in tests: memory writes must silently no-op.
    monkeypatch.setattr(
        mcp_server, "_record_memory", lambda root, content: asyncio.sleep(0)
    )
    from mcp.shared.memory import create_connected_server_and_client_session

    # The in-memory session context manager is single-use; hand out fresh ones.
    return lambda: create_connected_server_and_client_session(mcp_server.mcp)


def test_mcp_get_state(make_client):
    text = _call(make_client(), "get_state", {})
    assert "design-blueprint(active)" in text


def test_mcp_add_and_resolve_blocker_roundtrip(make_client, project: Path):
    text = _call(
        make_client(),
        "add_blocker",
        {"epic_id": "default", "milestone_id": "build-planning", "description": "RT-1: roundtrip"},
    )
    assert "recorded on default/build-planning" in text

    store = MilestoneStore(project)
    plan = store.load()
    new_id = plan.summary.active_blockers[-1].id
    new_blocker = next(b for b in plan.summary.active_blockers if b.id == new_id)
    assert "RT-1" in new_blocker.description

    text = _call(
        make_client(),
        "resolve_blocker",
        {"blocker_id": new_id, "resolution": "answered in client call", "date": "2026-10-24"},
    )
    assert "resolved 2026-10-24" in text

    plan = store.load()
    assert len(plan.summary.active_blockers) == 1  # only the fixture blocker remains


def test_mcp_set_milestone_status(make_client, project: Path):
    text = _call(
        make_client(),
        "set_milestone_status",
        {"epic_id": "default", "milestone_id": "build-planning", "status": "in_progress"},
    )
    assert "not_started -> in_progress" in text
    plan = MilestoneStore(project).load()
    assert plan.get_milestone("default", "build-planning").status == MilestoneStatus.IN_PROGRESS


def test_mcp_unknown_milestone_returns_error(make_client):
    with pytest.raises(Exception) as exc:
        _call(
            make_client(),
            "add_blocker",
            {"epic_id": "default", "milestone_id": "nope", "description": "x"},
        )
    assert "Unknown milestone" in repr(exc.value)


# --- config init ------------------------------------------------------------


def test_write_mcp_config_creates_and_merges(tmp_path: Path):
    report = write_mcp_config(tmp_path)
    assert report["created"] is True
    assert report["servers"] == ["maestro"]

    data = json.loads((tmp_path / ".mcp.json").read_text())
    entry = data["mcpServers"]["maestro"]
    assert entry["type"] == "stdio"
    assert entry["args"] == ["--mcp-serve"]
    assert Path(entry["command"]).is_absolute()


def test_write_mcp_config_preserves_other_servers(tmp_path: Path):
    existing = {
        "mcpServers": {
            "mcp-vector-search": {
                "type": "stdio",
                "command": "mcp-vector-search",
                "args": ["mcp"],
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(existing))

    report = write_mcp_config(tmp_path)
    assert report["created"] is False
    assert report["servers"] == ["maestro", "mcp-vector-search"]

    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["mcp-vector-search"]["args"] == ["mcp"]
    # unrelated top-level keys survive too
    data["extra"] = {"keep": "me"}
    (tmp_path / ".mcp.json").write_text(json.dumps(data))
    write_mcp_config(tmp_path)
    assert json.loads((tmp_path / ".mcp.json").read_text())["extra"] == {"keep": "me"}


def test_write_mcp_config_rejects_broken_json(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text("{not json")
    with pytest.raises(ValueError):
        write_mcp_config(tmp_path)
