#!/usr/bin/env python3
"""Convert flattened claude-mpm-agents into Open Maestro bundled agents.

Usage:
    cd /path/to/claude-mpm-agents
    python build-agent.py --all

    cd /path/to/open-maestro
    python scripts/convert_claude_mpm_agents.py \
        --source /path/to/claude-mpm-agents/dist/agents \
        --output agents

The script:
1. Reads each flattened agent markdown file.
2. Maps claude-mpm frontmatter (`agent_id`, `agent_type`, `resource_tier`)
   to Maestro frontmatter (`id`, `role`, `model`, `required_capabilities`).
3. Merges in tools/blocked_tools/required_capabilities from existing Maestro
   agents when IDs match (or are mapped, e.g. `researcher` -> `research`).
4. Assigns sensible default tools for new specialist agents based on role.
5. Writes flat, Maestro-compatible `.md` files to the output directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


TIER_MAP: dict[str, dict[str, Any]] = {
    "intensive": {
        "model": "reasoning",
        "required_capabilities": {
            "tool_use": True,
            "reasoning": "deep",
            "coding_strength": "high",
        },
    },
    "standard": {
        "model": "smart",
        "required_capabilities": {
            "tool_use": True,
            "reasoning": "light",
            "coding_strength": "high",
        },
    },
    "light": {
        "model": "fast",
        "required_capabilities": {
            "tool_use": True,
            "reasoning": "light",
            "coding_strength": "medium",
        },
    },
}

# Old Maestro agent IDs that map to new claude-mpm agent IDs.
ID_MAP: dict[str, str] = {
    "researcher": "research",
    "content-agent": "content",
}

# Default tool sets based on role.
READ_ONLY_TOOLS = ["Read", "Grep", "Bash"]
MUTATING_TOOLS = ["Read", "Edit", "Write", "Bash", "Grep", "MultiEdit", "ApplyPatch"]
DOC_TOOLS = ["Read", "Bash", "Write"]


def _tools_for_role(role: str) -> tuple[list[str], list[str]]:
    """Return (tools, blocked_tools) for a given agent role."""
    r = role.lower()
    if r in {"engineer", "ops"}:
        return MUTATING_TOOLS, []
    if r in {"documentation"}:
        return DOC_TOOLS, ["Edit"]
    if r in {"qa", "security", "research", "researcher", "code-analyzer"}:
        return READ_ONLY_TOOLS, ["Write", "Edit", "MultiEdit", "ApplyPatch"]
    # universal / product-owner / memory-manager / content
    return DOC_TOOLS, ["Edit"]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            return meta, body
    return {}, text


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _load_existing_agents(directory: Path) -> dict[str, dict[str, Any]]:
    """Load existing Maestro agent frontmatter keyed by id."""
    existing: dict[str, dict[str, Any]] = {}
    if not directory.exists():
        return existing
    for path in directory.rglob("*.md"):
        try:
            meta, _ = _split_frontmatter(path.read_text(encoding="utf-8"))
            aid = meta.get("id", path.stem)
            existing[aid] = meta
        except Exception:
            continue
    return existing


def _merge_capabilities(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in override.items():
        if v is not None:
            merged[k] = v
    return merged


def convert_agent(source_path: Path, existing: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    raw = source_path.read_text(encoding="utf-8")
    meta, body = _split_frontmatter(raw)
    if not meta:
        return None

    agent_id = meta.get("agent_id") or source_path.stem
    name = meta.get("name", agent_id)
    role = meta.get("agent_type", "specialized")
    description = meta.get("description", "")
    skills = _normalize_list(meta.get("skills"))

    tier = TIER_MAP.get((meta.get("resource_tier") or "").lower(), TIER_MAP["standard"])

    tools, blocked_tools = _tools_for_role(role)
    required_caps = dict(tier["required_capabilities"])

    # Merge in existing Maestro-specific tooling/capabilities if available.
    old_id = ID_MAP.get(agent_id, agent_id)
    old = existing.get(old_id)
    if old:
        if old.get("tools"):
            tools = _normalize_list(old["tools"])
        if old.get("blocked_tools"):
            blocked_tools = _normalize_list(old["blocked_tools"])
        if old.get("required_capabilities"):
            required_caps = _merge_capabilities(required_caps, old["required_capabilities"])
        if old.get("model") and old.get("model") != "default":
            # Keep existing model choice unless it was unspecified.
            tier_model = old["model"]
        else:
            tier_model = tier["model"]
    else:
        tier_model = tier["model"]

    frontmatter = {
        "id": agent_id,
        "name": name,
        "role": role,
        "model": tier_model,
        "description": description,
        "tools": tools,
        "blocked_tools": blocked_tools,
        "skills": skills,
        "required_capabilities": required_caps,
    }

    # Strip empty lists to keep files tidy.
    if not blocked_tools:
        frontmatter.pop("blocked_tools")

    body = body.strip()
    return {"frontmatter": frontmatter, "body": body, "id": agent_id}


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert claude-mpm agents to Maestro format")
    parser.add_argument("--source", required=True, type=Path, help="Flattened claude-mpm-agents directory")
    parser.add_argument("--output", required=True, type=Path, help="Output directory for converted agents")
    parser.add_argument("--skip-claude-mpm", action="store_true", default=True, help="Skip the claude-mpm/ vendor-specific folder")
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    existing = _load_existing_agents(output)

    converted: list[Path] = []
    skipped: list[str] = []

    for path in sorted(source.rglob("*.md")):
        rel = path.relative_to(source)
        if args.skip_claude_mpm:
            first_part = rel.parts[0] if rel.parts else ""
            if first_part == "claude-mpm":
                skipped.append(str(rel))
                continue

        result = convert_agent(path, existing)
        if result is None:
            skipped.append(str(rel))
            continue

        out_path = output / f"{result['id']}.md"
        front_yaml = yaml.safe_dump(result["frontmatter"], sort_keys=False, allow_unicode=True)
        content = f"---\n{front_yaml}---\n\n{result['body']}\n"
        out_path.write_text(content, encoding="utf-8")
        converted.append(out_path)

    print(f"Converted {len(converted)} agents to {output}")
    if skipped:
        print(f"Skipped {len(skipped)} files")
        for s in skipped:
            print(f"  - {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
