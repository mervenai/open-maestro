"""Tests for project-specific memory integration."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from open_maestro.memory.kuzu_client import KuzuMemoryClient


class _FakeProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout.encode("utf-8"), self._stderr.encode("utf-8")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path / "project"


@pytest.fixture
def memory_client(project_root: Path, monkeypatch: Any) -> KuzuMemoryClient:
    monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin/kuzu-memory")
    return KuzuMemoryClient(project_root=str(project_root))


class TestKuzuMemoryClient:
    def test_is_initialized_detects_existing_db(
        self, memory_client: KuzuMemoryClient, project_root: Path
    ):
        db_path = project_root / ".kuzu-memory" / "memories.db"
        db_path.parent.mkdir(parents=True)
        db_path.write_text("fake db")

        assert memory_client.is_initialized() is True

    def test_is_initialized_false_when_missing(
        self, memory_client: KuzuMemoryClient
    ):
        assert memory_client.is_initialized() is False

    async def test_ensure_initialized_runs_init_when_missing(
        self,
        memory_client: KuzuMemoryClient,
        project_root: Path,
        monkeypatch: Any,
    ):
        captured_args: list[list[str]] = []

        async def fake_exec(*args: str, **kwargs: Any):
            captured_args.append(list(args))
            # Simulate init creating the database.
            db_path = project_root / ".kuzu-memory" / "memories.db"
            db_path.parent.mkdir(parents=True)
            db_path.write_text("fake db")
            return _FakeProcess(stdout="initialized")

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_exec
        )

        result = await memory_client.ensure_initialized()
        assert result is True
        assert memory_client.is_initialized() is True
        assert any(
            args[:3] == ["kuzu-memory", "--project-root", str(project_root)]
            for args in captured_args
        )
        assert any("init" in args for args in captured_args)

    async def test_ensure_initialized_skips_init_when_present(
        self,
        memory_client: KuzuMemoryClient,
        project_root: Path,
        monkeypatch: Any,
    ):
        db_path = project_root / ".kuzu-memory" / "memories.db"
        db_path.parent.mkdir(parents=True)
        db_path.write_text("fake db")

        async def fake_exec(*args: str, **kwargs: Any):
            pytest.fail("Should not call kuzu-memory when already initialized")

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_exec
        )

        result = await memory_client.ensure_initialized()
        assert result is True

    async def test_store_includes_project_root(
        self,
        memory_client: KuzuMemoryClient,
        project_root: Path,
        monkeypatch: Any,
    ):
        captured_args: list[list[str]] = []

        async def fake_exec(*args: str, **kwargs: Any):
            captured_args.append(list(args))
            return _FakeProcess(stdout="stored")

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_exec
        )

        await memory_client.store("test content", memory_type="note")
        assert captured_args
        assert captured_args[0][:3] == [
            "kuzu-memory",
            "--project-root",
            str(project_root),
        ]
        assert "memory" in captured_args[0]
        assert "store" in captured_args[0]

    async def test_recall_includes_project_root(
        self,
        memory_client: KuzuMemoryClient,
        project_root: Path,
        monkeypatch: Any,
    ):
        captured_args: list[list[str]] = []

        async def fake_exec(*args: str, **kwargs: Any):
            captured_args.append(list(args))
            return _FakeProcess(stdout="No memories found for: 'query'")

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_exec
        )

        result = await memory_client.recall("query")
        assert result == []
        assert captured_args[0][:3] == [
            "kuzu-memory",
            "--project-root",
            str(project_root),
        ]
        assert "memory" in captured_args[0]
        assert "recall" in captured_args[0]

    def test_no_project_root_treats_memory_as_initialized(
        self, monkeypatch: Any
    ):
        monkeypatch.setattr(shutil, "which", lambda _bin: "/fake/bin/kuzu-memory")
        client = KuzuMemoryClient(project_root=None)
        assert client.is_initialized() is True
