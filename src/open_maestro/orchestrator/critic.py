"""Code-critic gate: change detection heuristics and verdict parsing.

Why: the code-critic framework (agent + rubric) is only advisory unless the
orchestrator dispatches it. This module holds the pure, testable pieces the
ProjectManager gate uses: which files count as source, how much change is worth
a review pass, and how to read the critic's structured verdict.
What: ``snapshot_head``, ``detect_source_changes``, ``should_trigger``,
``parse_diff_stat``, ``parse_verdict``, ``extract_findings``.
Test: ``should_trigger`` respects the >50-lines / >1-file threshold and the
docs/config-only exclusions; ``parse_verdict`` falls back to None on garbage.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
    ".rb", ".sh", ".php", ".cs", ".cpp", ".c", ".h",
}

MIN_CODE_LINES = 50          # >50 changed code lines triggers a review
MIN_CODE_FILES = 2           # ... or changes across >1 code file
MAX_TRIVIAL_LINES = 5        # single-file fixes of <=5 lines never trigger

_DIFF_STAT_RE = re.compile(r"^\s*(?P<path>.+?)\s*\|\s*(?P<lines>\d+)")
_VERDICT_RE = re.compile(r"##\s*Verdict:\s*(?P<verdict>APPROVE|WARN|BLOCK)", re.IGNORECASE)


def parse_diff_stat(stat: str) -> list[tuple[str, int]]:
    """Parse ``git diff --stat`` output into [(path, changed-lines)]."""
    files: list[tuple[str, int]] = []
    for line in stat.splitlines():
        match = _DIFF_STAT_RE.match(line)
        if not match:
            continue
        path = match.group("path").strip()
        if path.startswith("...") or path == "file":
            continue  # summary lines like " 3 files changed, ..."
        if " => " in path:  # renames: "old => new" — keep the new path
            path = path.split(" => ")[-1].strip()
        files.append((path, int(match.group("lines"))))
    return files


def should_trigger(changes: list[tuple[str, int]]) -> bool:
    """Decide whether a set of (path, lines) changes merits a critic pass.

    Mirrors the code-production-process skill: docs/config-only changes are
    excluded, tiny single-file fixes are excluded, and anything over 50 code
    lines or touching more than one code file is reviewed.
    """
    code = [(p, n) for p, n in changes if Path(p).suffix.lower() in CODE_EXTENSIONS]
    if not code:
        return False
    if len(code) == 1 and code[0][1] <= MAX_TRIVIAL_LINES:
        return False
    total_lines = sum(n for _, n in code)
    return total_lines > MIN_CODE_LINES or len(code) >= MIN_CODE_FILES


def snapshot_head(project_path: Path) -> str | None:
    """Return the current HEAD sha, or None outside a git repo / on failure."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path, capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception as exc:
        logger.debug("critic gate: snapshot_head failed: %s", exc)
        return None


def detect_source_changes(
    project_path: Path, before_ref: str | None
) -> list[tuple[str, int]]:
    """Return [(path, changed-lines)] for code files changed this turn.

    ``before_ref`` is the HEAD captured before the turn; committed changes are
    measured against it, and uncommitted (staged + unstaged) changes are
    measured against the resulting HEAD. Returns [] outside a git repo or when
    git fails — the gate must never crash a turn.
    """
    commands: list[list[str]] = []
    if before_ref:
        commands.append(["git", "diff", "--stat", f"{before_ref}..HEAD"])
    commands.append(["git", "diff", "--stat", "HEAD"])

    changes: dict[str, int] = {}
    try:
        for command in commands:
            out = subprocess.run(
                command, cwd=project_path, capture_output=True, text=True, timeout=30,
            )
            if out.returncode != 0:
                continue
            for path, lines in parse_diff_stat(out.stdout):
                changes[path] = changes.get(path, 0) + lines
    except Exception as exc:
        logger.debug("critic gate: detect_source_changes failed: %s", exc)
        return []

    return [
        (p, n) for p, n in changes.items()
        if Path(p).suffix.lower() in CODE_EXTENSIONS
    ]


def parse_verdict(text: str) -> str | None:
    """Extract APPROVE/WARN/BLOCK from the critic's structured output."""
    match = _VERDICT_RE.search(text)
    return match.group("verdict").upper() if match else None


def extract_findings(text: str, max_lines: int = 6) -> list[str]:
    """Pull the first rows of the critic's Findings table for the summary."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().startswith("## Findings"):
            findings: list[str] = []
            for row in lines[idx + 1:]:
                stripped = row.strip()
                if not stripped:
                    if findings:
                        break
                    continue
                if stripped.startswith("##"):
                    break
                findings.append(stripped)
                if len(findings) >= max_lines:
                    break
            return findings
    return []
