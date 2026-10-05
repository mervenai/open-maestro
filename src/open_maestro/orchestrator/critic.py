"""Code-critic gate: change detection heuristics and verdict parsing.

Why: the code-critic framework (agent + rubric) is only advisory unless the
orchestrator dispatches it. This module holds the pure, testable pieces the
ProjectManager gate uses: which files count as source, how much change is worth
a review pass, and how to read the critic's structured verdict.
What: ``snapshot_head``, ``in_git_repo``, ``snapshot_mtimes``, ``mtime_snapshot``,
``detect_source_changes``, ``should_trigger``, ``detect_artifact_changes``,
``filter_artifact_changes``, ``artifact_gate_enabled``, ``parse_diff_stat``,
``parse_verdict``, ``extract_findings``.
Test: ``should_trigger`` respects the >50-lines / >1-file threshold and the
docs/config-only exclusions; ``filter_artifact_changes`` keeps only >50-line
docs/*.md files; ``parse_verdict`` falls back to None on garbage; outside a
git repo, ``_detect_changes`` diffs against the mtime snapshot (MSTRO-127).
"""

from __future__ import annotations

import difflib
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

# --- Non-git change detection (MSTRO-127) -----------------------------------
# Outside a git repo there is no HEAD to diff against. The pm snapshots the
# tree at turn start (``snapshot_mtimes``) and ``_detect_changes`` falls back
# to diffing gate-relevant files against that baseline. Only files the gates
# can act on (code + markdown) are tracked, and VCS/dependency/build noise —
# plus maestro's own ``.open-maestro`` state, whose ledger writes must never
# count as project changes — is excluded from the scan.
_SCAN_EXCLUDES = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".open-maestro", "dist", "build", ".next", ".idea",
}
_SNAPSHOT_SUFFIXES = CODE_EXTENSIONS | ARTIFACT_EXTENSIONS
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024

# project_path -> {relpath: (mtime, size, head_text_or_None)}
_MTIME_SNAPSHOTS: dict[str, dict[str, tuple[float, int, str | None]]] = {}

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


def in_git_repo(project_path: Path) -> bool:
    """True when *project_path* lives inside a git work tree (MSTRO-127).

    Gates that need a "before" anchor use this to pick git diff vs. the
    mtime-snapshot fallback. Never raises — missing git binary counts as
    "not a repo" and lands on the fallback.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=project_path, capture_output=True, text=True, timeout=10,
        )
        return out.returncode == 0 and out.stdout.strip() == "true"
    except Exception as exc:
        logger.debug("critic gate: in_git_repo failed: %s", exc)
        return False


def _read_gate_file(path: Path) -> str | None:
    """Best-effort read for snapshot/diff purposes; None when unreadable."""
    try:
        if path.stat().st_size > _MAX_SNAPSHOT_BYTES:
            return None  # too big to diff — reported by size when it changes
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _scan_project(
    root: Path, prev: dict[str, tuple[float, int, str | None]] | None = None
) -> dict[str, tuple[float, int, str | None]]:
    """Snapshot every gate-relevant file under *root* as {rel: (mtime, size, text)}.

    *prev* is the previous snapshot: entries whose mtime and size are
    unchanged keep their cached text, so steady-state rescans stat but
    never re-read files.
    """
    files: dict[str, tuple[float, int, str | None]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SCAN_EXCLUDES)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower() not in _SNAPSHOT_SUFFIXES:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(root))
            old = prev.get(rel) if prev else None
            if old is not None and old[0] == st.st_mtime and old[1] == st.st_size:
                files[rel] = old
            else:
                files[rel] = (st.st_mtime, st.st_size, _read_gate_file(p))
    return files


def snapshot_mtimes(project_path: Path) -> None:
    """Capture the non-git change-detection baseline (MSTRO-127).

    Cheap no-op inside a git repo, where git remains the source of truth.
    Outside one, records (mtime, size, head text) for every gate-relevant
    file so ``_detect_changes`` can diff the turn against this baseline
    instead of git. The pm calls this at turn start, in the slot where a
    git repo would snapshot HEAD.
    """
    root = Path(project_path)
    if in_git_repo(root):
        return
    key = str(root)
    _MTIME_SNAPSHOTS[key] = _scan_project(root, prev=_MTIME_SNAPSHOTS.get(key))


def mtime_snapshot(
    project_path: Path,
) -> dict[str, tuple[float, int, str | None]] | None:
    """The baseline recorded by ``snapshot_mtimes``, or None (not taken yet)."""
    return _MTIME_SNAPSHOTS.get(str(Path(project_path)))


def reset_mtimes_snapshot(project_path: Path) -> None:
    """Drop the baseline for *project_path* (tests)."""
    _MTIME_SNAPSHOTS.pop(str(Path(project_path)), None)


def _count_added_lines(old: str, new: str) -> int:
    """Added-line count between two texts (the non-git ``--stat`` proxy)."""
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="")
    return sum(
        1 for line in diff if line.startswith("+") and not line.startswith("+++")
    )


def _detect_changes_nogit(project_path: Path) -> list[tuple[str, int]]:
    """MSTRO-127: mtime-based change detection outside git repos.

    Diffs gate-relevant files against the snapshot taken by
    ``snapshot_mtimes`` at turn start. A file absent from the snapshot is
    new (full line count); a file with changed mtime/size is diffed for
    added lines. The first call seeds the snapshot and reports no changes,
    so a project's first turn never reviews pre-existing files. Never
    raises — the gate must never crash a turn.
    """
    root = Path(project_path)
    snap = _MTIME_SNAPSHOTS.get(str(root))
    if snap is None:
        snapshot_mtimes(root)
        return []
    changes: dict[str, int] = {}
    for rel, (mtime, size, new_text) in _scan_project(root, prev=snap).items():
        old = snap.get(rel)
        if old is None:
            if new_text is not None:
                changes[rel] = len(new_text.splitlines())
            continue
        if old[0] == mtime and old[1] == size:
            continue
        if new_text is None:
            continue
        if old[2] is not None:
            n = _count_added_lines(old[2], new_text)
        else:  # file too big to snapshot: report its current size
            n = len(new_text.splitlines())
        if n > 0:
            changes[rel] = n
    return sorted(changes.items())


def _detect_changes(
    project_path: Path, before_ref: str | None
) -> list[tuple[str, int]]:
    """Return [(path, changed-lines)] for ALL files changed this turn.

    Shared plumbing behind ``detect_source_changes`` and
    ``detect_artifact_changes``; callers apply their own filters.
    ``before_ref`` is the HEAD captured before the turn; committed changes are
    measured against it, and uncommitted (staged + unstaged) changes are
    measured against the resulting HEAD. Outside a git repo (MSTRO-127) the
    mtime snapshot taken at turn start is the "before" anchor instead.
    Returns [] when detection fails — the gate must never crash a turn.
    """
    if not in_git_repo(project_path):
        return _detect_changes_nogit(project_path)

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
    measured against the resulting HEAD. Outside a git repo, changes are
    measured against the mtime snapshot (MSTRO-127). Returns [] when
    detection fails — the gate must never crash a turn.
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

    Runs the same change detection as ``detect_source_changes`` (unfiltered)
    and keeps markdown files under ``docs/`` whose change exceeds the artifact
    threshold. Outside a git repo the mtime snapshot is the baseline
    (MSTRO-127). Returns [] when detection fails — the gate must never crash
    a turn.
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
