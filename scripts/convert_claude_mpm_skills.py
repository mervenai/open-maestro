#!/usr/bin/env python3
"""Convert the bobmatnyc/claude-mpm-skills repo into Open Maestro bundled skills.

Usage:
    python scripts/convert_claude_mpm_skills.py \
        --source /tmp/claude-mpm-skills-convert \
        --output skills

Only main SKILL.md files are converted; docs/, .bundles/, .github/, scripts/,
README, CLAUDE, and BUNDLE files are excluded to mirror the default Git source
configuration in open_maestro.sources.config.
"""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
from typing import Any

import yaml

DEFAULT_EXCLUDES = [
    "docs/**/*.md",
    ".bundles/**/*.md",
    ".github/**/*.md",
    "scripts/**/*.md",
    "examples/**/*.md",
    "**/README.md",
    "**/CLAUDE.md",
    "**/BUNDLE.md",
]


def _matches_glob(rel: Path, pattern: str) -> bool:
    """Shell-style glob matching with ** support."""
    rel_parts = rel.parts
    pat_parts = pattern.split("/")

    def _match_component(name: str, pat: str) -> bool:
        return fnmatch.fnmatchcase(name, pat)

    def match(i: int, j: int) -> bool:
        while j < len(pat_parts):
            pat = pat_parts[j]
            if pat == "**":
                for k in range(i, len(rel_parts) + 1):
                    if match(k, j + 1):
                        return True
                return False
            if i >= len(rel_parts):
                return False
            if not _match_component(rel_parts[i], pat):
                return False
            i += 1
            j += 1
        return i == len(rel_parts)

    return match(0, 0)


def _should_exclude(rel: Path) -> bool:
    return any(_matches_glob(rel, pattern) for pattern in DEFAULT_EXCLUDES)


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if raw.startswith("---"):
        _, frontmatter, body = raw.split("---", 2)
        return frontmatter.strip(), body.strip()
    return "", raw.strip()


def _normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def convert_skill(source_path: Path) -> tuple[Path, str]:
    """Return (relative output path, converted file content)."""
    raw = source_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw)
    meta = yaml.safe_load(frontmatter) or {}

    skill_id = str(meta.get("name", source_path.parent.name))
    skill_name = str(meta.get("name", source_path.parent.name)).replace("-", " ").title()
    tags = _normalize_tags(meta.get("tags"))

    new_frontmatter: dict[str, Any] = {
        "id": skill_id,
        "name": skill_name,
        "tags": tags,
    }

    lines = ["---", yaml.safe_dump(new_frontmatter, sort_keys=False).rstrip(), "---", "", body]
    return source_path.relative_to(source_path), "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert claude-mpm-skills to Open Maestro bundled skills."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("/tmp/claude-mpm-skills-convert"),
        help="Root directory of a checked-out claude-mpm-skills repository.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("skills"),
        help="Directory to write converted Maestro skill files.",
    )
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source directory not found: {args.source}")

    args.output.mkdir(parents=True, exist_ok=True)

    skill_files = sorted(args.source.rglob("SKILL.md"))
    if not skill_files:
        raise SystemExit(f"No SKILL.md files found under {args.source}")

    written = 0
    for source_path in skill_files:
        rel = source_path.relative_to(args.source)
        if _should_exclude(rel):
            continue

        output_path = args.output / rel
        output_path.parent.mkdir(parents=True, exist_ok=True)

        raw = source_path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
        meta = yaml.safe_load(frontmatter) or {}

        skill_id = str(meta.get("name", source_path.parent.name))
        skill_name = str(meta.get("name", source_path.parent.name)).replace("-", " ").title()
        tags = _normalize_tags(meta.get("tags"))

        new_frontmatter: dict[str, Any] = {
            "id": skill_id,
            "name": skill_name,
            "tags": tags,
        }

        lines = [
            "---",
            yaml.safe_dump(new_frontmatter, sort_keys=False).rstrip(),
            "---",
            "",
            body,
        ]
        output_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {output_path}")
        written += 1

    print(f"\nConverted {written} skill files to {args.output}")


if __name__ == "__main__":
    main()
