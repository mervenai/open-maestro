"""Basic Memory backend (3.8.0): client behavior over a scripted ``bm`` CLI.

All subprocess traffic is faked by replacing ``BasicMemoryClient._run`` (and
``subprocess.run`` for the sync ``is_initialized``), so no real bm install is
needed — the tests pin argument shapes and parsing, not the tool itself.
"""

import json
from types import SimpleNamespace

import pytest

from open_maestro.memory import (
    BasicMemoryClient,
    KuzuMemoryClient,
    create_memory_client,
)
from open_maestro.memory.basic_memory_client import _slug


def _client(tmp_path, monkeypatch, responses=None, projects=None):
    """Return a client whose _run serves scripted responses.

    ``responses`` maps a recognizable argv substring to stdout (or a
    RuntimeError to raise). ``projects`` pre-seeds the bm project registry.
    """
    client = BasicMemoryClient.__new__(BasicMemoryClient)  # skip binary check
    client.project_root = str(tmp_path)
    client._project_name = None
    registry = {p: str(tmp_path) for p in (projects or ["probe"])}

    calls: list[list[str]] = []

    async def fake_run(args, timeout=None):
        calls.append(list(args))
        joined = " ".join(args)
        if args[:3] == ["bm", "project", "list"]:
            return json.dumps(
                {
                    "projects": [
                        {"name": name, "local_path": path}
                        for name, path in registry.items()
                    ]
                }
            )
        for key, value in (responses or {}).items():
            if key in joined:
                if isinstance(value, RuntimeError):
                    raise value
                return value
        return "{}"

    monkeypatch.setattr(client, "_run", fake_run)
    return client, calls


class TestSlug:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("IntelInvoiceCoding", "intelinvoicecoding"),
            ("EP-02 ownership flip", "ep-02-ownership-flip"),
            ("  spaces  everywhere  ", "spaces-everywhere"),
            ("dots.and_underscores!", "dots-and-underscores"),
        ],
    )
    def test_slug(self, text, expected):
        assert _slug(text) == expected

    def test_empty_falls_back(self):
        assert _slug("!!!") == "memory"


class TestRecall:
    async def test_recall_parses_json_results(self, tmp_path, monkeypatch):
        payload = {
            "results": [
                {"matched_chunk": "chunk A", "content": "# A\nchunk A"},
                {"matched_chunk": "", "content": "# B\nfull body"},
            ],
            "total": 2,
        }
        client, calls = _client(
            tmp_path, monkeypatch, {"tool search": json.dumps(payload)}
        )
        out = await client.recall("outcome ownership", limit=3)
        assert out == ["chunk A", "# B\nfull body"]
        search_call = next(c for c in calls if c[:3] == ["bm", "tool", "search"])
        assert "--json" in search_call
        assert search_call[search_call.index("--page-size") + 1] == "3"
        assert search_call[search_call.index("--project") + 1] == "probe"

    async def test_recall_unregistered_project_returns_empty(
        self, tmp_path, monkeypatch
    ):
        client, _ = _client(tmp_path, monkeypatch, projects=["other"])
        assert await client.recall("anything") == []

    async def test_recall_malformed_json_returns_empty(self, tmp_path, monkeypatch):
        client, _ = _client(tmp_path, monkeypatch, {"tool search": "not json"})
        assert await client.recall("anything") == []

    async def test_enhance_without_memories_returns_prompt(self, tmp_path, monkeypatch):
        client, _ = _client(tmp_path, monkeypatch, {"tool search": '{"results": []}'})
        assert await client.enhance("do the thing") == "do the thing"

    async def test_enhance_prefixes_memory_block(self, tmp_path, monkeypatch):
        payload = {"results": [{"matched_chunk": "remembered fact", "content": "x"}]}
        client, _ = _client(tmp_path, monkeypatch, {"tool search": json.dumps(payload)})
        out = await client.enhance("do the thing")
        assert out.startswith("## Relevant project memories")
        assert "- remembered fact" in out
        assert out.endswith("do the thing")


class TestStore:
    async def test_store_writes_note_with_hash_metadata(self, tmp_path, monkeypatch):
        client, calls = _client(tmp_path, monkeypatch)
        await client.store(
            "IIC decision: outcomes owned by MappingService",
            memory_type="decision",
            metadata={"session_id": "s1"},
        )
        write = next(c for c in calls if c[:3] == ["bm", "tool", "write"])
        meta = json.loads(write[write.index("--metadata") + 1])
        assert meta["memory_hash"]
        assert meta["source"] == "maestro"  # default only when caller omitted it
        assert meta["session_id"] == "s1"
        assert write[write.index("--note-type") + 1] == "decision"
        assert write[write.index("--directory") + 1] == "memory"

    async def test_store_idempotent_on_identical_content(self, tmp_path, monkeypatch):
        client, calls = _client(
            tmp_path,
            monkeypatch,
            {
                "tool write": RuntimeError("bm failed: note already exists"),
                "bm find --meta": json.dumps({"results": [{"title": "dup"}]}),
            },
        )
        result = await client.store("same content again")
        assert result is None
        # One write attempt, one find; no suffix-retry write.
        assert sum(1 for c in calls if c[:3] == ["bm", "tool", "write"]) == 1

    async def test_store_same_title_different_content_retries_with_suffix(
        self, tmp_path, monkeypatch
    ):
        client, calls = _client(
            tmp_path,
            monkeypatch,
            {"bm find --meta": json.dumps({"results": []})},
        )
        writes = 0
        original = client._run

        async def write_once_then_succeed(args, timeout=None):
            nonlocal writes
            if args[:3] == ["bm", "tool", "write"]:
                calls.append(list(args))
                writes += 1
                if writes == 1:
                    raise RuntimeError("bm failed: note already exists")
                return "{}"
            return await original(args, timeout=timeout)

        monkeypatch.setattr(client, "_run", write_once_then_succeed)
        await client.store("different body under same first line")
        write_calls = [c for c in calls if c[:3] == ["bm", "tool", "write"]]
        assert len(write_calls) == 2
        second_title = write_calls[1][write_calls[1].index("--title") + 1]
        assert "-" in second_title

    def test_title_from_first_meaningful_line(self, tmp_path):
        client = BasicMemoryClient.__new__(BasicMemoryClient)
        assert (
            client._title_for("# Heading line\nbody")
            == "heading-line"
        )
        assert client._title_for("\n\n  spaced   \nmore") == "spaced"


class TestIsInitialized:
    def test_true_when_registered_with_memory_dir(self, tmp_path, monkeypatch):
        (tmp_path / "memory").mkdir()
        proc = SimpleNamespace(
            stdout=json.dumps(
                {"projects": [{"name": "probe", "local_path": str(tmp_path)}]}
            ).encode()
        )
        monkeypatch.setattr(
            "open_maestro.memory.basic_memory_client.subprocess.run",
            lambda *a, **k: proc,
        )
        client = BasicMemoryClient.__new__(BasicMemoryClient)
        client.project_root = str(tmp_path)
        client._project_name = None
        assert client.is_initialized() is True
        assert client._project_name == "probe"

    def test_false_without_memory_dir(self, tmp_path, monkeypatch):
        client = BasicMemoryClient.__new__(BasicMemoryClient)
        client.project_root = str(tmp_path)
        client._project_name = None
        assert client.is_initialized() is False

    def test_false_when_unregistered(self, tmp_path, monkeypatch):
        (tmp_path / "memory").mkdir()
        proc = SimpleNamespace(stdout=json.dumps({"projects": []}).encode())
        monkeypatch.setattr(
            "open_maestro.memory.basic_memory_client.subprocess.run",
            lambda *a, **k: proc,
        )
        client = BasicMemoryClient.__new__(BasicMemoryClient)
        client.project_root = str(tmp_path)
        client._project_name = None
        assert client.is_initialized() is False


class TestCreateMemoryClient:
    def test_env_none_disables_memory(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_MEMORY_BACKEND", "none")
        assert create_memory_client("/tmp/x") is None

    def test_env_basic_memory(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_MEMORY_BACKEND", "basic-memory")
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        assert isinstance(create_memory_client("/tmp/x"), BasicMemoryClient)

    def test_env_kuzu(self, monkeypatch):
        monkeypatch.setenv("MAESTRO_MEMORY_BACKEND", "kuzu")
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        assert isinstance(create_memory_client("/tmp/x"), KuzuMemoryClient)

    def test_autodetect_prefers_bm(self, monkeypatch):
        monkeypatch.delenv("MAESTRO_MEMORY_BACKEND", raising=False)
        monkeypatch.setattr(
            "shutil.which", lambda name: "/usr/bin/bm" if name == "bm" else None
        )
        assert isinstance(create_memory_client("/tmp/x"), BasicMemoryClient)

    def test_autodetect_falls_back_to_kuzu(self, monkeypatch):
        monkeypatch.delenv("MAESTRO_MEMORY_BACKEND", raising=False)
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/usr/bin/kuzu-memory" if name == "kuzu-memory" else None,
        )
        assert isinstance(create_memory_client("/tmp/x"), KuzuMemoryClient)

    def test_no_backend_returns_none(self, monkeypatch):
        monkeypatch.delenv("MAESTRO_MEMORY_BACKEND", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert create_memory_client("/tmp/x") is None
