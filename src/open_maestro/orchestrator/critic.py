"""Code-critic gate: change detection heuristics and verdict parsing.

Why: the code-critic framework (agent + rubric) is only advisory unless the
orchestrator dispatches it. This module holds the pure, testable pieces the
ProjectManager gate uses: which files count as source, how much change is worth
a review pass, and how to read the critic's structured verdict.
What: ``snapshot_head``, ``detect_source_changes``, ``should_trigger``,
``detect_artifact_changes``, ``filter_artifact_changes``, ``artifact_gate_enabled``,
``parse_diff_stat``, ``parse_verdict``, ``extract_findings``.
Test: ``should_trigger`` respects the >50-lines / >1-file threshold and the
docs/config-only exclusions; ``filter_artifact_changes`` keeps only >50-line
docs/*.md files; ``parse_verdict`` falls back to None on garbage.
"""

from __future__ import annotations

import logging
import os
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

ARTIFACT_EXTENSIONS = {".md"}   # design docs (data/API contracts) are markdown
ARTIFACT_MIN_LINES = 50         # an artifact must exceed this to be reviewed

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


def _detect_changes(
    project_path: Path, before_ref: str | None
) -> list[tuple[str, int]]:
    """Return [(path, changed-lines)] for ALL files changed this turn.

    Shared git plumbing behind ``detect_source_changes`` and
    ``detect_artifact_changes``; callers apply their own filters.
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
        logger.debug("critic gate: detect changes failed: %s", exc)
        return []

    return list(changes.items())


def detect_source_changes(
    project_path: Path, before_ref: str | None
) -> list[tuple[str, int]]:
    """Return [(path, changed-lines)] for code files changed this turn.

    ``before_ref`` is the HEAD captured before the turn; committed changes are
    measured against it, and uncommitted (staged + unstaged) changes are
    measured against the resulting HEAD. Returns [] outside a git repo or when
    git fails — the gate must never crash a turn.
    """
    return [
        (p, n) for p, n in _detect_changes(project_path, before_ref)
        if Path(p).suffix.lower() in CODE_EXTENSIONS
    ]


def artifact_gate_enabled() -> bool:
    """Whether the artifact-critic gate may run (default on).

    Mirrors the ``MAESTRO_CRITIC_GATE`` kill-switch pattern: set
    ``MAESTRO_ARTIFACT_CRITIC=off`` (also "0", "false", "no") to disable the
    artifact review pass. The artifact gate only ever runs when the main
    ``critic_gate`` is on; this knob suppresses it independently.
    """
    flag = os.environ.get("MAESTRO_ARTIFACT_CRITIC", "").strip().lower()
    return flag not in {"off", "0", "false", "no"}


def _artifact_min_lines() -> int:
    """Effective artifact size threshold, honoring ``MAESTRO_ARTIFACT_MIN_LINES``."""
    raw = os.environ.get("MAESTRO_ARTIFACT_MIN_LINES", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.debug("critic gate: bad MAESTRO_ARTIFACT_MIN_LINES=%r", raw)
    return ARTIFACT_MIN_LINES


def filter_artifact_changes(changes: list[tuple[str, int]]) -> list[str]:
    """Keep design-artifact files from a (path, lines) change list.

    An artifact is a markdown file under ``docs/`` (any depth) whose change
    exceeds ``_artifact_min_lines()`` changed lines (default 50, overridable
    via ``MAESTRO_ARTIFACT_MIN_LINES``). Everything else — code, short doc
    tweaks, markdown outside ``docs/`` — is not a design artifact.
    """
    min_lines = _artifact_min_lines()
    artifacts: list[str] = []
    for path, lines in changes:
        p = Path(path)
        if p.suffix.lower() not in ARTIFACT_EXTENSIONS:
            continue
        if "docs" not in p.parts[:-1]:  # must live under docs/ at some depth
            continue
        if lines > min_lines:
            artifacts.append(path)
    return artifacts


def detect_artifact_changes(
    project_path: Path, before_ref: str | None
) -> list[str]:
    """Return design-artifact files (docs/*.md) changed this turn.

    Runs the same git diff as ``detect_source_changes`` (unfiltered) and keeps
    markdown files under ``docs/`` whose change exceeds the artifact threshold.
    Returns [] outside a git repo or when git fails — the gate must never
    crash a turn.
    """
    return filter_artifact_changes(_detect_changes(project_path, before_ref))


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
