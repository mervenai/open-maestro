#!/usr/bin/env python3
"""Convert Claude-MPM / mcp-skillset agent definitions to Open Maestro format.

Usage:
    python scripts/convert_mcp_skillset_agents.py \
        --source /tmp/mcp-skillset-convert/.claude/agents \
        --output agents

The script reads Markdown files with YAML frontmatter from the source directory,
maps mcp-skillset fields to Maestro's schema, sanitizes Claude-specific tool
references, and writes the converted files to the output directory.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml

# Map mcp-skillset model hints to Maestro model selectors.
MODEL_MAP = {
    "opus": "smart",
    "sonnet": "smart",
    "haiku": "fast",
    "claude-opus": "smart",
    "claude-sonnet": "smart",
    "claude-haiku": "fast",
}

# Map agent role/name to Maestro tool lists, capabilities, and default model.
ROLE_CONFIG: dict[str, dict[str, Any]] = {
    "engineer": {
        "name": "Software Engineer",
        "model": "smart",
        "tools": ["Read", "Edit", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "php-engineer": {
        "name": "PHP Engineer",
        "model": "smart",
        "tools": ["Read", "Edit", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "ruby-engineer": {
        "name": "Ruby Engineer",
        "model": "smart",
        "tools": ["Read", "Edit", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "svelte-engineer": {
        "name": "Svelte Engineer",
        "model": "smart",
        "tools": ["Read", "Edit", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "agentic-coder-optimizer": {
        "name": "Agentic Coder Optimizer",
        "model": "smart",
        "tools": ["Read", "Edit", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "research": {
        "name": "Codebase Researcher",
        "model": "fast",
        "tools": ["Read", "Grep", "Bash"],
        "blocked_tools": ["Write", "Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "low"},
    },
    "qa": {
        "name": "QA Engineer",
        "model": "smart",
        "tools": ["Read", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "documentation": {
        "name": "Documentation Writer",
        "model": "smart",
        "tools": ["Read", "Grep", "Bash", "Write"],
        "blocked_tools": ["Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "medium"},
    },
    "security": {
        "name": "Security Engineer",
        "model": "smart",
        "tools": ["Read", "Grep", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "high"},
    },
    "ops": {
        "name": "DevOps Engineer",
        "model": "default",
        "tools": ["Read", "Bash"],
        "blocked_tools": ["Write", "Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "medium"},
    },
    "clerk-ops": {
        "name": "Clerk Operations",
        "model": "default",
        "tools": ["Read", "Bash"],
        "blocked_tools": ["Write", "Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "medium"},
    },
    "content-agent": {
        "name": "Content Agent",
        "model": "default",
        "tools": ["Read", "Bash", "Write"],
        "blocked_tools": ["Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "medium"},
    },
    "prompt-engineer": {
        "name": "Prompt Engineer",
        "model": "smart",
        "tools": ["Read", "Grep", "Bash", "Write"],
        "blocked_tools": [],
        "required_capabilities": {"tool_use": True, "coding_strength": "medium"},
    },
    "imagemagick": {
        "name": "ImageMagick Specialist",
        "model": "default",
        "tools": ["Read", "Bash"],
        "blocked_tools": ["Write", "Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "low"},
    },
    "ticketing": {
        "name": "Ticketing Agent",
        "model": "default",
        "tools": ["Read", "Bash"],
        "blocked_tools": ["Write", "Edit"],
        "required_capabilities": {"tool_use": True, "coding_strength": "low"},
    },
}

# Rename a few mcp-skillset agent IDs to match Maestro conventions.
ID_RENAMES = {
    "research": "researcher",
}

# Map upstream skill references to the actual skill IDs in claude-mpm-skills.
SKILL_ID_RENAMES = {
    "anthropic-sdk": "anthropic",
    "bug-fix-verification": "bug-fix",
    "pre-merge-verification": "pre-merge",
    "screenshot-verification": "screenshot",
    "api-security-review": "api-review",
}


def _slugify(name: str) -> str:
    """Create a lowercase, hyphenated ID from a name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if raw.startswith("---"):
        _, frontmatter, body = raw.split("---", 2)
        return frontmatter.strip(), body.strip()
    return "", raw.strip()


def _map_model(model_value: Any) -> str:
    if not model_value or not isinstance(model_value, str):
        return "default"
    return MODEL_MAP.get(model_value.lower(), "default")


def _sanitize_body(body: str) -> str:
    """Replace Claude-only tool references with Maestro-compatible guidance."""
    # TodoWrite is a Claude-only tool; replace with a generic instruction.
    body = re.sub(
        r"\bTodoWrite\b",
        "track progress explicitly in your response",
        body,
    )
    return body


def _maybe_add_mcp_note(body: str) -> str:
    """Add a note when the body references optional MCP tools."""
    if "mcp__mcp-skillset__" in body or "mcp__mcp-ticketer__" in body:
        note = (
            "> **Note:** This agent references optional MCP tools "
            "(`mcp__mcp-skillset__*`, `mcp__mcp-ticketer__*`). "
            "They are only available when the corresponding MCP server is configured "
            "in your Maestro environment."
        )
        body = f"{note}\n\n{body}"
    return body


def convert_agent(source_path: Path) -> tuple[str, dict[str, Any], str]:
    """Return (target_filename, frontmatter_dict, sanitized_body)."""
    raw = source_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    meta = yaml.safe_load(frontmatter) or {}

    source_name = str(meta.get("name", source_path.stem))
    source_type = str(meta.get("type", source_path.stem))
    agent_id = ID_RENAMES.get(source_name, source_name)
    agent_id = _slugify(agent_id)

    # Prefer the agent ID/filename mapping; fall back to the declared type.
    config = ROLE_CONFIG.get(agent_id, ROLE_CONFIG.get(source_type, {}))
    # When the agent has its own explicit config, trust its ID over a mismatched
    # source type (e.g. ticketing.md whose type says documentation).
    role = agent_id if agent_id in ROLE_CONFIG else source_type

    description = meta.get("description", "")
    # Strip long example/commentary blocks from the description for Maestro's
    # short description field; keep the first sentence or line.
    if isinstance(description, str):
        short_description = description.split("\n")[0].strip()
        short_description = (
            re.sub(r"<example>.*?</example>", "", short_description, flags=re.S)
            .strip()
        )
        short_description = (
            re.sub(r"<commentary>.*?</commentary>", "", short_description, flags=re.S)
            .strip()
        )
        short_description = short_description.strip('"').strip()
    else:
        short_description = config.get("name", agent_id)

    mapped_model = _map_model(meta.get("model"))
    if mapped_model == "default":
        mapped_model = config.get("model", "default")

    tools = config.get("tools", ["Read", "Bash"])
    blocked_tools = config.get("blocked_tools", [])
    required_capabilities = config.get(
        "required_capabilities", {"tool_use": True, "coding_strength": "medium"}
    )

    # Preserve skills references; Maestro ignores missing skill IDs gracefully.
    skills = meta.get("skills", [])
    if isinstance(skills, str):
        skills = [skills]
    skills = [SKILL_ID_RENAMES.get(str(s), str(s)) for s in skills]

    new_frontmatter: dict[str, Any] = {
        "id": agent_id,
        "name": config.get("name", agent_id.replace("-", " ").title()),
        "role": role,
        "model": mapped_model,
        "tools": tools,
        "blocked_tools": blocked_tools,
    }
    if skills:
        new_frontmatter["skills"] = skills
    new_frontmatter["required_capabilities"] = required_capabilities
    new_frontmatter["description"] = short_description or config.get("name", agent_id)

    sanitized_body = _maybe_add_mcp_note(_sanitize_body(body))
    target_filename = f"{agent_id}.md"
    return target_filename, new_frontmatter, sanitized_body


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert mcp-skillset agents to Open Maestro agent definitions."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("/tmp/mcp-skillset-convert/.claude/agents"),
        help="Directory containing source mcp-skillset agent Markdown files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("agents"),
        help="Directory to write converted Maestro agent Markdown files.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source directory not found: {args.source}")

    args.output.mkdir(parents=True, exist_ok=True)

    for source_path in sorted(args.source.glob("*.md")):
        target_filename, frontmatter, body = convert_agent(source_path)
        target_path = args.output / target_filename

        lines = ["---", yaml.safe_dump(frontmatter, sort_keys=False).rstrip(), "---", "", body]
        target_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {target_path}")


if __name__ == "__main__":
    main()
