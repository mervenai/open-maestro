"""Tests for session persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from open_maestro.session.store import SessionRecord, SessionStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> SessionStore:
    return SessionStore(base_dirs=[tmp_path / "sessions"])


class TestSessionStore:
    def test_save_and_get_round_trip(self, tmp_store: SessionStore):
        record = SessionRecord(
            session_id="sess_123",
            runtime_name="fake",
            agent_id="engineer",
            model="smart",
            prompt_summary="refactor parser",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        tmp_store.save(record)

        loaded = tmp_store.get("sess_123")
        assert loaded is not None
        assert loaded.session_id == "sess_123"
        assert loaded.agent_id == "engineer"
        assert loaded.model == "smart"

    def test_get_missing_session_returns_none(self, tmp_store: SessionStore):
        assert tmp_store.get("does-not-exist") is None

    def test_list_recent_orders_by_updated_at(self, tmp_store: SessionStore):
        now = datetime.now(UTC)
        older = SessionRecord(
            session_id="old",
            runtime_name="fake",
            agent_id="engineer",
            model="fast",
            prompt_summary="older",
            created_at=now,
            updated_at=now,
        )
        newer = SessionRecord(
            session_id="new",
            runtime_name="fake",
            agent_id="engineer",
            model="fast",
            prompt_summary="newer",
            created_at=now,
            updated_at=now,
        )
        tmp_store.save(older)
        tmp_store.save(newer)

        recent = tmp_store.list_recent(limit=10)
        assert [r.session_id for r in recent] == ["new", "old"]

    def test_save_updates_existing_record(self, tmp_store: SessionStore):
        now = datetime.now(UTC)
        record = SessionRecord(
            session_id="sess_123",
            runtime_name="fake",
            agent_id="engineer",
            model="fast",
            prompt_summary="first",
            created_at=now,
            updated_at=now,
            cost_usd=0.01,
        )
        tmp_store.save(record)

        updated = SessionRecord(
            session_id="sess_123",
            runtime_name="fake",
            agent_id="engineer",
            model="fast",
            prompt_summary="second",
            created_at=now,
            updated_at=datetime.now(UTC),
            cost_usd=0.02,
        )
        tmp_store.save(updated)

        loaded = tmp_store.get("sess_123")
        assert loaded is not None
        assert loaded.prompt_summary == "second"
        assert loaded.cost_usd == 0.02
        assert loaded.created_at == now

    def test_save_persists_token_counts(self, tmp_store: SessionStore):
        record = SessionRecord(
            session_id="sess_tokens",
            runtime_name="fake",
            agent_id="engineer",
            model="fast",
            prompt_summary="tokens",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            tokens_used=1234,
            input_tokens=900,
            output_tokens=334,
        )
        tmp_store.save(record)

        loaded = tmp_store.get("sess_tokens")
        assert loaded.tokens_used == 1234
        assert loaded.input_tokens == 900
        assert loaded.output_tokens == 334

    def test_save_is_atomic(self, tmp_store: SessionStore):
        record = SessionRecord(
            session_id="sess_atomic",
            runtime_name="fake",
            agent_id="engineer",
            model="fast",
            prompt_summary="atomic",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        tmp_store.save(record)

        base_dir = tmp_store._base_dirs[0]
        # Should not leave temp files behind.
        assert list(base_dir.glob(".*.yaml")) == []
        assert (base_dir / "sess_atomic.yaml").exists()

    def test_prune_keeps_recent_sessions(self, tmp_store: SessionStore):
        now = datetime.now(UTC)
        for i in range(5):
            tmp_store.save(
                SessionRecord(
                    session_id=f"sess_{i}",
                    runtime_name="fake",
                    agent_id="engineer",
                    model="fast",
                    prompt_summary=f"session {i}",
                    created_at=now,
                    updated_at=now,
                )
            )

        removed = tmp_store.prune(keep=2)
        assert len(removed) == 3
        remaining = tmp_store.list_recent(limit=10)
        assert len(remaining) == 2
