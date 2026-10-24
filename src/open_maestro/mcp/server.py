"""maestro MCP server — out-of-band milestone state, enforced in code.

Any MCP-capable agent (Claude Code, Kimi, ...) can record project progress —
client answers, resolved risks, status flips — through these tools instead of
hand-editing ``.open-maestro/milestones.yaml``. Writes go through
:mod:`open_maestro.milestones.mutations`, so the drift guard, summary
recomputation, and one-line-delta discipline hold regardless of which agent
performed them, and every mutation also stores a dated memory so the narrative
advances with the state.

Run ``maestro --mcp-serve`` (stdio, blocks) or wire it into a project's
``.mcp.json`` via ``maestro --mcp-init``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from open_maestro.memory.kuzu_client import KuzuMemoryClient
from open_maestro.milestones import mutations

logger = logging.getLogger(__name__)

mcp = FastMCP("maestro")


def _project_root() -> Path:
    """The project the server operates on.

    MCP hosts start stdio servers in the project root; MAESTRO_PROJECT_ROOT
    overrides for hosts that do not.
    """
    return Path(os.environ.get("MAESTRO_PROJECT_ROOT", os.getcwd()))


async def _record_memory(root: Path, content: str) -> None:
    """Store the mutation's narrative line; never fail the mutation on this."""
    try:
        client = KuzuMemoryClient(str(root))
    except Exception as exc:  # kuzu-memory CLI not installed
        logger.info("maestro-mcp: memory not recorded (%s)", exc)
        return
    try:
        if not client.is_initialized():
            return  # do not bootstrap a memory DB from a state write
        await client.store(content, memory_type="maestro-mcp")
    except Exception as exc:
        logger.warning("maestro-mcp: memory store failed: %s", exc)


@mcp.tool()
async def get_state() -> str:
    """Compact project summary: per-epic milestone statuses, current/next
    milestones, and active blockers. Cheaper than reading milestones.yaml."""
    session = mutations.open_session(_project_root())
    return mutations.compact_state(session)


@mcp.tool()
async def list_blockers(active_only: bool = True) -> str:
    """List blockers. Defaults to active only; pass active_only=False for the
    full history including resolved ones."""
    session = mutations.open_session(_project_root())
    rows: list[str] = []
    for epic in session.plan.epics:
        for milestone in epic.milestones:
            for blocker in milestone.blockers:
                if active_only and blocker.resolved_at is not None:
                    continue
                state = (
                    "active"
                    if blocker.resolved_at is None
                    else f"resolved {blocker.resolved_at.date().isoformat()}"
                )
                rows.append(
                    f"- [{state}] {blocker.id} [{epic.id}/{milestone.id}] "
                    f"{blocker.description}"
                )
    if not rows:
        return "No blockers recorded." if not active_only else "No active blockers."
    return "\n".join(rows)


@mcp.tool()
async def add_blocker(epic_id: str, milestone_id: str, description: str) -> str:
    """Record a new active blocker on a milestone. Keep description to one
    line (what blocks, who owns the next step, doc pointer); details belong in
    the decision register or design-decisions doc, not here."""
    root = _project_root()
    session = mutations.open_session(root)
    result = mutations.add_blocker(session, epic_id, milestone_id, description)
    mutations.commit(session)
    await _record_memory(root, result["memory"])
    return (
        f"Blocker {result['blocker_id']} recorded on {result['location']}. "
        "Dashboard Active Blockers now includes it."
    )


@mcp.tool()
async def resolve_blocker(
    blocker_id: str,
    resolution: str,
    date: str | None = None,
) -> str:
    """Mark a blocker resolved. `resolution` is a dated one-liner appended to
    the blocker's description (verdict + decision source); the full analysis
    stays in the register/design-decisions doc. `date` is ISO (default today).
    The blocker drops off the dashboard once resolved."""
    root = _project_root()
    session = mutations.open_session(root)
    result = mutations.resolve_blocker(session, blocker_id, resolution, date)
    mutations.commit(session)
    await _record_memory(root, result["memory"])
    return (
        f"Blocker {result['blocker_id']} on {result['location']} "
        f"resolved {result['resolved_at']}."
    )


@mcp.tool()
async def set_milestone_status(
    epic_id: str,
    milestone_id: str,
    status: str,
    note: str | None = None,
) -> str:
    """Flip a milestone status (not_started|in_progress|blocked|completed|skipped).
    Optional `note` is appended to the milestone notes as a dated one-liner."""
    root = _project_root()
    session = mutations.open_session(root)
    result = mutations.set_milestone_status(
        session, epic_id, milestone_id, status, note
    )
    mutations.commit(session)
    await _record_memory(root, result["memory"])
    return (
        f"Milestone {result['epic']}/{result['milestone']}: "
        f"{result['old_status']} -> {result['status']}."
    )


def run_stdio() -> None:
    """Serve the tools over stdio (invoked by `maestro --mcp-serve`)."""
    mcp.run(transport="stdio")
