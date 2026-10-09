"""Read-only MCP tool vetting for restricted (auditor) seats.

Agent definitions declare a ``tools:`` allowlist (e.g. the code-critic's
Read/Grep/Bash). Maestro enforces that list for real when tool interception is
active, and renders it into the seat's system prompt via
``PermissionPolicy.guard_text`` — so any tool not in the list is both unusable
and invisible to the agent. That correctly sandboxes reviewers, but it also
locks out semantic search: 72 agent protocols reference the
``mcp__mcp-vector-search__*`` tools, which are read-only and exactly what an
auditor wants.

This module names the MCP tools that are safe to admit into a read-only seat.
Everything else a server might expose (``index_project``, ``save_report``,
``review_pull_request``, ``kg_build``, Linear's ``create_issue``, ...) stays
excluded: a read-only critic must not reach mutating or agentic tools.
"""

from __future__ import annotations

from typing import Any

# Suffixes vetted read-only against mcp-vector-search 4.1.14's tool list.
# Search/query/analysis only — no indexing, no report writing, no review
# agents (those may call LLMs or persist state).
READONLY_MCP_TOOL_SUFFIXES: tuple[str, ...] = (
    "search_code",
    "search_similar",
    "search_context",
    "search_hybrid",
    "get_project_status",
    "analyze_project",
    "analyze_file",
    "analyze_tests",
    "find_smells",
    "get_complexity_hotspots",
    "check_circular_dependencies",
    "interpret_analysis",
    "trace_execution_flow",
    "kg_query",
    "kg_stats",
    "kg_history",
    "kg_ontology",
    "kg_ia",
    "kg_callers_at_commit",
)


def readonly_mcp_tool_names(mcp_servers: dict[str, Any] | None) -> list[str]:
    """Fully-qualified ``mcp__<server>__<tool>`` names approved for read-only seats.

    *mcp_servers* is the config shape returned by ``load_mcp_config``
    (``{"mcpServers": {...}}``) or a flat server mapping. Returns an empty
    list when no config is present, so unrestricted seats are unaffected.
    """
    if not mcp_servers:
        return []
    servers = mcp_servers.get("mcpServers", mcp_servers)
    if not isinstance(servers, dict):
        return []
    return [
        f"mcp__{server}__{suffix}"
        for server in sorted(servers)
        for suffix in READONLY_MCP_TOOL_SUFFIXES
    ]
