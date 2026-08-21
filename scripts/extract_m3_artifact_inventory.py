#!/usr/bin/env python3
"""Inventory M3BudgetUpload artifacts and map them to milestones/epics.

Why: Slice 1 of building the milestone prompt playbook. Before we can extract
reusable prompts, we need a structured map of what artifacts exist, which
milestone they belong to, and which epic/track they support.
What: Walks ~/projects/M3BudgetUpload, applies artifact-pattern heuristics from
the milestone taxonomy, and writes a JSON inventory.
Usage:
    python scripts/extract_m3_artifact_inventory.py > data/m3_artifact_inventory.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from subprocess import run
from typing import Any

PROJECT_ROOT = Path(os.environ.get("M3_PROJECT_ROOT", "/Users/jj/projects/M3BudgetUpload"))
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "m3_artifact_inventory.json"

MILESTONE_PATTERNS: dict[str, dict[str, Any]] = {
    "intake-discovery": {
        "folders": ["requirements", "docs/intake"],
        "file_patterns": ["*synthesis*.md", "*PRD*.md", "*RFP*.md"],
        "epic_hints": [],
    },
    "execution-planning": {
        "folders": ["docs"],
        "file_patterns": ["*execution-plan*.md", "*architecture*.md", "*decisions*.md"],
        "epic_hints": [],
    },
    "design-blueprint": {
        "folders": ["docs"],
        "file_patterns": ["*blueprint*.md", "*spec*.md", "*contract*.md", "*jira*.csv"],
        "epic_hints": [],
    },
    "build-planning": {
        "folders": ["docs", "tests"],
        "file_patterns": ["*build-plan*.md", "*traceability*.md"],
        "epic_hints": {
            "budget-import": ["budget-import", "build-plan-budget"],
            "audit-log": ["audit-log", "build-plan-audit"],
        },
    },
    "implementation": {
        "folders": ["Core", "ProfitStrategyService", "AuditService", "NugetWebPackages"],
        "file_patterns": [
            "*.sln",
            "*.csproj",
            "package.json",
            "*PlanImport*.cs",
            "*PlanImport*.ts",
            "*BudgetImport*.cs",
            "*BudgetImport*.ts",
            "*AuditLog*.cs",
            "*AuditLog*.ts",
        ],
        "epic_hints": {
            "budget-import": ["PlanImport", "BudgetImport", "budget-import"],
            "audit-log": ["AuditLog", "audit-log"],
        },
    },
    "qa-integration": {
        "folders": ["docs/qa", "tests"],
        "file_patterns": ["*test-report*.md", "*uat*.md", "*manual-checklist*.md"],
        "epic_hints": {
            "budget-import": ["budget-import"],
            "audit-log": ["audit-log"],
        },
    },
    "demo-delivery": {
        "folders": ["docs/demo", "build"],
        "file_patterns": ["*demo*.md", "*update-and-demo*.md", "*.pptx", "*.txt"],
        "epic_hints": [],
    },
    "retrospective-findings": {
        "folders": ["docs/findings", "docs"],
        "file_patterns": ["*findings*.md", "*retrospective*.md", "*playbook*.md"],
        "epic_hints": [],
    },
}

EPIC_HINTS: dict[str, list[str]] = {
    "budget-import": ["budget-import", "budget", "import-flow", "plan-import"],
    "audit-log": ["audit-log", "audit"],
}


@dataclass
class ArtifactEntry:
    path: str
    relative_path: str
    milestone: str
    epic: str | None
    size_bytes: int
    modified_at: str
    excerpt: str = ""


@dataclass
class MilestoneInventory:
    project_root: str
    generated_at: str
    total_artifacts: int
    artifacts: list[ArtifactEntry] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


def _detect_epic(relative: str) -> str | None:
    lower = relative.lower()
    scores: dict[str, int] = {}
    for epic, hints in EPIC_HINTS.items():
        scores[epic] = sum(1 for hint in hints if hint in lower)
    if not scores or max(scores.values()) == 0:
        return None
    return max(scores, key=scores.get)


def _excerpt(path: Path, max_chars: int = 240) -> str:
    if path.suffix.lower() not in {".md", ".txt", ".csv"}:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _matches_patterns(path: Path, patterns: list[str]) -> bool:
    return any(path.match(p) for p in patterns)


def _collect_files(root: Path, folders: list[str]) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        target = root / folder
        if not target.exists():
            continue
        for item in target.rglob("*"):
            if item.is_file() and not item.name.startswith("."):
                files.append(item)
    return files


@dataclass
class GitSummary:
    commit_count: int
    first_commit: str | None
    last_commit: str | None
    top_contributors: list[dict[str, Any]]


def _git_summary(project_root: Path) -> GitSummary:
    summary = GitSummary(commit_count=0, first_commit=None, last_commit=None, top_contributors=[])
    git_dir = project_root / ".git"
    if not git_dir.exists():
        return summary

    def _git(args: list[str]) -> str:
        result = run(
            ["git", "-C", str(project_root)] + args,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    log_lines = _git(["log", "--pretty=format:%ad|%an", "--date=short"]).splitlines()
    if not log_lines or log_lines == [""]:
        return summary

    dates = [line.split("|")[0] for line in log_lines if "|" in line]
    authors = [line.split("|")[1] for line in log_lines if "|" in line]
    summary.commit_count = len(log_lines)
    summary.first_commit = dates[-1] if dates else None
    summary.last_commit = dates[0] if dates else None

    author_counts: dict[str, int] = {}
    for author in authors:
        author_counts[author] = author_counts.get(author, 0) + 1
    summary.top_contributors = [
        {"name": name, "commits": count}
        for name, count in sorted(author_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    ]
    return summary


def build_inventory(project_root: Path) -> MilestoneInventory:
    artifacts: list[ArtifactEntry] = []
    seen: set[Path] = set()

    for milestone, config in MILESTONE_PATTERNS.items():
        files = _collect_files(project_root, config["folders"])
        patterns = config["file_patterns"]
        epic_hints = config.get("epic_hints", {})

        for file in files:
            if not _matches_patterns(file, patterns):
                continue
            if file in seen:
                continue
            seen.add(file)

            relative = file.relative_to(project_root).as_posix()
            epic = _detect_epic(relative)

            # Refine epic using milestone-specific hints when available.
            if epic is None and epic_hints:
                lower = relative.lower()
                for hint_epic, hint_list in epic_hints.items():
                    if any(h in lower for h in hint_list):
                        epic = hint_epic
                        break

            stat = file.stat()
            artifacts.append(
                ArtifactEntry(
                    path=str(file),
                    relative_path=relative,
                    milestone=milestone,
                    epic=epic,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    excerpt=_excerpt(file),
                )
            )

    summary: dict[str, Any] = {
        "by_milestone": {},
        "by_epic": {},
    }
    for a in artifacts:
        summary["by_milestone"].setdefault(a.milestone, 0)
        summary["by_milestone"][a.milestone] += 1
        summary["by_epic"].setdefault(a.epic or "uncategorized", 0)
        summary["by_epic"][a.epic or "uncategorized"] += 1

    summary["git"] = asdict(_git_summary(project_root))

    return MilestoneInventory(
        project_root=str(project_root),
        generated_at=datetime.now().isoformat(),
        total_artifacts=len(artifacts),
        artifacts=sorted(artifacts, key=lambda x: (x.milestone, x.epic or "", x.relative_path)),
        summary=summary,
    )


def main() -> None:
    inventory = build_inventory(PROJECT_ROOT)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            asdict(inventory),
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {inventory.total_artifacts} artifacts to {OUTPUT_PATH}")
    print(json.dumps(inventory.summary, indent=2))


if __name__ == "__main__":
    main()
