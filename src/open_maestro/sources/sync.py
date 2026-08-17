"""Git-based syncing for agent and skill sources.

A *source* is a Git URL that is cloned into a local cache directory.  Open
Maestro checks the remote HEAD on each run (unless ``--skip-sync`` is used) and
fast-forwards the local clone when it is behind.  Sync metadata is persisted so
we can avoid hammering the remote on every invocation.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SYNC_TTL_SECONDS = 24 * 60 * 60


@dataclass
class GitSource:
    """A remote source of agents or skills."""

    name: str
    url: str
    kind: str  # "agents" or "skills"
    ref: str = "main"
    subdir: str = ""
    exclude: list[str] = field(default_factory=list)
    last_sync: datetime | None = None
    last_remote_head: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def checkout_path(self) -> Path:
        """Return the local cache path for this source."""
        return (
            Path.home()
            / ".open-maestro"
            / "sources"
            / f"{self.kind}-{self.name}"
        ).expanduser()

    @property
    def content_path(self) -> Path:
        """Return the directory containing the agent/skill markdown files."""
        base = self.checkout_path
        if self.subdir:
            return base / self.subdir
        return base

    def is_stale(self, ttl_seconds: int = DEFAULT_SYNC_TTL_SECONDS) -> bool:
        """Return True if we should check the remote again."""
        if self.last_sync is None:
            return True
        age = (datetime.now(UTC) - self.last_sync).total_seconds()
        return age > ttl_seconds


def _run_git(
    *args: str,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git subprocess command."""
    cmd = ["git", *args]
    logger.debug("Running git command: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result


def _get_remote_head(
    url: str, ref: str = "HEAD", local_path: Path | None = None
) -> str | None:
    """Return the remote commit hash for *ref* via ``git ls-remote``."""
    try:
        result = _run_git("ls-remote", url, ref)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return None
        return lines[0].split()[0]
    except Exception as exc:
        err = str(exc).lower()
        offline_keywords = (
            "could not resolve host",
            "could not connect",
            "network is unreachable",
            "temporary failure",
            "no route to host",
            "timed out",
            "timeout",
        )
        appears_offline = any(kw in err for kw in offline_keywords)
        if local_path is not None and local_path.exists():
            # A local clone is available, so an unreachable remote is not fatal.
            if appears_offline:
                logger.info("Remote unreachable for %s; using local clone", url)
            else:
                logger.info(
                    "Could not check remote HEAD for %s; leaving local clone", url
                )
        else:
            logger.warning("Failed to get remote HEAD for %s: %s", url, exc)
        return None


def _get_local_head(path: Path) -> str | None:
    """Return the current commit hash of the local clone."""
    try:
        result = _run_git("rev-parse", "HEAD", cwd=path)
        return result.stdout.strip()
    except Exception as exc:
        logger.warning("Failed to get local HEAD for %s: %s", path, exc)
        return None


def _clone(url: str, path: Path, ref: str) -> None:
    """Clone *url* into *path* and check out *ref*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _run_git("clone", "--depth", "1", "--branch", ref, url, str(path))


def _fast_forward(path: Path, ref: str) -> None:
    """Fetch and reset the shallow clone at *path* to origin/*ref*."""
    # Fetch only the latest commit for the requested ref.  Using an explicit
    # --depth 1 fetch keeps shallow clones small and reliably updates the
    # remote-tracking ref even when the local clone is behind.
    _run_git("fetch", "--depth", "1", "origin", ref, cwd=path)
    _run_git("reset", "--hard", f"origin/{ref}", cwd=path)


def sync_source(
    source: GitSource,
    *,
    force: bool = False,
    ttl_seconds: int = DEFAULT_SYNC_TTL_SECONDS,
) -> GitSource:
    """Clone or update *source* and return an updated source record.

    If the local clone is missing, clone it.  If ``force`` is True or the
    source is stale, check the remote HEAD and fast-forward if needed.
    """
    path = source.checkout_path

    if not path.exists():
        logger.info("Cloning %s source '%s' from %s", source.kind, source.name, source.url)
        _clone(source.url, path, source.ref)
        source.last_remote_head = _get_local_head(path)
        source.last_sync = datetime.now(UTC)
        return source

    if not force and not source.is_stale(ttl_seconds):
        logger.debug("Source '%s' is fresh; skipping sync", source.name)
        return source

    # Ensure the local clone points at the URL currently recorded in the
    # registry, in case the user changed it or the same name is reused across
    # different upstreams (e.g. during tests).
    try:
        _run_git("remote", "set-url", "origin", source.url, cwd=path)
    except Exception as exc:
        logger.warning("Could not update remote URL for '%s': %s", source.name, exc)

    remote_head = _get_remote_head(source.url, source.ref, path)
    local_head = _get_local_head(path)

    if remote_head is None:
        # _get_remote_head already logged the appropriate message.
        source.last_sync = datetime.now(UTC)
        return source

    if remote_head == local_head:
        logger.debug("Source '%s' is up to date", source.name)
        source.last_remote_head = remote_head
        source.last_sync = datetime.now(UTC)
        return source

    logger.info(
        "Updating %s source '%s' (%s -> %s)",
        source.kind,
        source.name,
        local_head[:8] if local_head else "unknown",
        remote_head[:8],
    )
    _fast_forward(path, source.ref)
    source.last_remote_head = remote_head
    source.last_sync = datetime.now(UTC)
    return source


def remove_source(source: GitSource) -> None:
    """Delete the local checkout for *source*."""
    path = source.checkout_path
    if path.exists():
        shutil.rmtree(path)
        logger.info("Removed source checkout: %s", path)
