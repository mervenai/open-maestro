# Memory Unification Feasibility: open-maestro ⇄ claude-mpm (kuzu-memory)

**Date:** 2026-10-10  
**Author:** Research analysis (claude-mpm)  
**Subject project:** /Users/jj/projects/M3RFP/ActivityMonitor  
**open-maestro source (editable, MIT):** /Users/jj/dev/open-maestro/src/open_maestro  
**kuzu-memory:** v1.12.11 at /Users/jj/.local/pipx/venvs/kuzu-memory/lib/python3.14/site-packages/kuzu_memory  
**Status:** Analysis only — no code changed

---

## Executive Summary

open-maestro is **already a kuzu-memory client**, with native memory integration (`memory/kuzu_client.py`). The core problem is not system incompatibility but **separate kuzu database files**: ActivityMonitor's shared store (3.6 MB) and open-maestro's own repo store (1.1 MB) diverge. The primary recommendation is **Config Alignment (§3a)**: Point both tools at one shared kuzu database with zero code changes. Optional secondary: add a one-way NL-content bridge for structured-prose fields not yet mirrored. Tertiary: upgrade the wrapper library for speed. Do **not** migrate structured state (milestones, gates, sessions) into kuzu — wrong tool, negative value, high risk.

---

## Table of Contents

1. [Storage Interfaces](#1-storage-interfaces)
2. [Bridge Feasibility](#2-bridge-feasibility--verdict-feasible-but-mostly-redundant-effort-s)
3. [Direct kuzu Integration Paths](#3-modify-open-maestro-to-use-kuzu-directly)
4. [Recommendation](#4-recommendation)
5. [Verification Flags / Open Items](#5-verification-flags--open-items-resolve-before-acting)

---

## 1. Storage Interfaces

### 1a. kuzu-memory (v1.12.11) read/write surface

**CLI Interface** (`kuzu-memory` binary → `kuzu_memory.cli.commands:cli`). Store/recall live under a `memory` subgroup:

- `kuzu-memory memory store <content>` — sync verbatim store. Options: `--source` (default `cli`), `--session-id`, `--agent-id`, `--metadata` (JSON), `--db-path`. (cli/memory_commands.py:47)
- `kuzu-memory memory learn <content>` — async, waits up to `--timeout 5.0s`; `--no-wait` for fire-and-forget. (memory_commands.py:136)
- `kuzu-memory memory recall <prompt>` — read-only; `--max-memories 10`, `--strategy auto|keyword|entity|temporal`, `--format enhanced|simple|json|raw`. (memory_commands.py:264)
- `kuzu-memory memory enhance <prompt>` — read-only; returns prompt + injected context. (memory_commands.py:465)
- Also: `memory recent | prune | merge | export`. Top-level `stats`/`health` are deprecated aliases to `status`. **No top-level `learn`/`recall`/`remember`** (stale docstrings imply otherwise).

**Python API:**

- **KuzuMemory** (sync engine, core/memory.py:153): `remember(content, source, session_id, agent_id, metadata, knowledge_type, importance) -> str`; `attach_memories(prompt, max_memories, strategy, ...) -> MemoryContext`; `generate_memories(...)`. Defaults enable `enable_git_sync=True` / `auto_sync=True` — a bridge should pass `False`.  
  `remember()` swallows errors (returns `""`).

- **KuzuMemoryClient** (async, client.py): async context manager; `learn()`, `recall()`, `enhance()`. Defaults git sync OFF.

- **MemoryService** (services/memory_service.py): `remember()` (raises `DatabaseError` on empty id), `attach_memories()`. This is what the CLI and MCP call.

**MCP tools** (mcp/server.py): `kuzu_remember` (sync store), `kuzu_learn` (async store), `kuzu_recall`, `kuzu_enhance`, `kuzu_project_context`, `kuzu_user_context`, `kuzu_stats`, `kuzu_optimize`, `kuzu_merge`, `kuzu_export_shared`, `kuzu_import_shared`.

**Memory data model** (core/models.py:122, Pydantic):

```python
id (uuid4)
content
content_hash (SHA-256 of content.lower().strip())
created_at / valid_from / valid_to / accessed_at
access_count
memory_type (EPISODIC/SEMANTIC/PROCEDURAL/WORKING/SENSORY/PREFERENCE)
knowledge_type (RULE/PATTERN/CONVENTION/GOTCHA/ARCHITECTURE/NOTE)
importance
confidence
source_type
agent_id
user_id
session_id
metadata (dict)
entities (list)
# NO tags, NO updated_at
```

**DB schema** (storage/schema.py): Kuzu graph. Node tables `Memory`, `Entity`, `Session`, `Keyword`, `ArchivedMemory`, `SchemaVersion`. Relationship tables `MENTIONS`, `RELATES_TO`, `BELONGS_TO_SESSION`, `CO_OCCURS_WITH`, `CONSOLIDATED_INTO`, `HAS_KEYWORD`. One HNSW vector index on `Memory.embedding FLOAT[384]`.

**Dedup/idempotency:** Identity key is `content_hash` (SHA-256 of normalized content), **NOT** `id`. On single insert, exact `content_hash` match is a silent skip (storage/query_builder.py:169). Re-storing byte-identical content (modulo case/outer whitespace) is idempotent. **Caveats:** not transactional (no UNIQUE constraint — concurrent identical writes can both insert); `generate_memories()` adds fuzzy 0.95/0.85 dedup that is less predictable. For a bridge: prefer `memory store`/`remember()` (verbatim + exact-hash dedup), precompute the hash, serialize writes.

### 1b. open-maestro (v2.2.0) storage layer

**NO storage abstraction exists.** No `StorageProtocol`, no repository ABC, no backend selector. (The only ABC is runtime/base.py:60 `AgentRuntime` — for LLM backends, not persistence.) Instead there are 7 independent concrete `*Store` classes, each hand-rolling YAML/JSON I/O:

| Store | File | Format | Path |
|-------|------|--------|------|
| **MilestoneStore** | milestones/store.py:89 | YAML | `.open-maestro/milestones.yaml` |
| **SessionStore** | session/store.py:60 | YAML per-session | `.open-maestro/sessions/<id>.yaml` |
| **TodoStore** | todos/store.py:17 | JSON | `.open-maestro/todos.json` |
| **PromptHistoryStore** | milestones/prompt_history.py:119 | YAML | `prompt_history.yaml` |
| **DashboardPublishHistoryStore** | milestones/publish_history.py:41 | YAML | `dashboard_publish_history.yaml` |
| **DashboardStore** | dashboard_server/store.py:13 | JSON | `dashboards/<token>.json` |
| **GateLedger** | review/gate.py:112 | JSON append-only | `gates/<sha>.json` |

Plus free functions `load_project_token`/`save_project_token` (milestones/supabase_publisher.py:42, 58). Raw file I/O appears in ~41 files; the mirrorable state is centralized in those ~7 modules + 2 functions. **No config-driven backend selection**; paths parameterizable via constructor args, serializer is not.

**Memory integration:** open-maestro already has kuzu (`memory/kuzu_client.py`). **KuzuMemoryClient.recall/enhance/store call `kuzu-memory memory recall|enhance|store`**. The wrapper passes `--project-root` (kuzu_client.py:30).

**open-maestro data model** (mirror candidates):

- **milestones.yaml** (schema 2.0, milestones/models.py): `MilestonePlan` → `Epic[]` → `Milestone[]`, with `Artifact`, `Blocker`, computed `Summary`. `MilestoneStatus` enum. Loader hard-rejects schema ≠ 2.0. Blocker descriptions and milestone notes are rich prose.
- **gates/<sha>.json** (GateLedger): append-only audit records `{gate, result, counts, at}`, keyed by artifact content hash.
- **reviews/<doc>-<sha>/<persona>.md**: free-form markdown audit reports. No structured model.
- **sessions/<id>.yaml** (SessionRecord dataclass, session/store.py:21): runtime/agent/model, cost, tokens, last_output, resumed_from/forked_from, metadata.
- **prompt_history.yaml** (PromptRunRecord): structured run telemetry.
- **resume-log.md**: NOT store-backed; free-form; contains "Key findings / decisions" (ADR-style) and verbatim kuzu-memory recall output — the richest NL decision content.

**Editability/licensing:** User's own editable git checkout (`git@github.com:mervenai/open-maestro.git`, `pip install -e`). Patches to `/Users/jj/dev/open-maestro/src` survive upgrades. **Caveat:** no LICENSE file on disk despite pyproject MIT declaration; PyPI availability not network-verified.

### Semantic Mapping (what maps to kuzu)

| Content | Type | NL? | Verdict |
|---------|------|-----|---------|
| Decisions / "Key findings" (resume-log) | EPISODIC | ✅ Excellent | maestro→kuzu trivial |
| Blocker descriptions, milestone notes | NL | ✅ Good | maestro→kuzu |
| Review persona reports (reviews/*.md) | NL blob | ✅ Good | maestro→kuzu; weak structured |
| Milestone/Epic state (status, order, weight, dates) | Structured, mutable, recomputed | ❌ Poor | kuzu wrong store (no in-place UPDATE, dedup=skip) |
| Gate audit counts | Numeric, append-only, hash-keyed | ❌ Poor | kuzu wrong store |
| Session telemetry (cost/tokens) | Relational metrics | ❌ Poor | kuzu wrong store |
| Prompt-run telemetry | Metrics | ❌ Poor | kuzu wrong store |

**Directionality:** maestro→kuzu (NL subset) is easy; kuzu→maestro is near-impossible (cannot reconstruct typed/validated/recomputed `milestones.yaml` from NL memories). **Any bridge is effectively one-way (maestro → kuzu), NL-subset only.**

---

## 2. Bridge Feasibility — VERDICT: Feasible but mostly redundant. Effort S.

A pure external bridge (no open-maestro edits) is technically easy (clean store CLI/API + content-hash idempotency), but most value is already captured by the existing `kuzu_client.py`. The only NEW value is mirroring structured-but-prose fields open-maestro does NOT currently push: blocker descriptions, milestone notes, gate results, `reviews/*.md`.

**MVP bridge design:**

1. **Trigger:** watchdog file-watcher (or 5-min cron) on `milestones.yaml`, `gates/*.json`, `reviews/*.md`, `resume-log.md`.
2. **Extract:** NL-bearing fields only — `summary.active_blockers[].description`, `Milestone.notes`, gate `{gate, result, counts}` rendered as a sentence, review markdown bodies, resume-log "Key findings".
3. **Store:** `kuzu-memory memory store --source maestro-<kind> --metadata '{"origin":"open-maestro",...}' <content>` (sync, verbatim) into the SHARED ActivityMonitor DB.
4. **Idempotency:** rely on `content_hash` exact-match skip; precompute `sha256(content.lower().strip())` to pre-filter; serialize writes.
5. **Loop/conflict safety:** one-way maestro→kuzu only; never write back to YAML; distinct `--source` so bridge memories are never re-ingested.

**Concerns:**

- `milestones.yaml` rewritten frequently (debounce)
- `reviews/*.md` no schema (ingest whole-file)
- kuzu dedup not transactional (single-writer only)
- Changed blockers accumulate (kuzu cannot update — use `memory prune` periodically)

**Effort:** S (1–2 days).

---

## 3. Modify open-maestro to use kuzu directly

Split into three sub-tasks of very different cost:

### 3a. Point both tools at ONE shared kuzu DB

**Effort:** S  
**Risk:** Low

open-maestro's `KuzuMemoryClient` uses `<project_root>/.kuzu-memory/memories.db`. When open-maestro runs **IN** the ActivityMonitor dir with `project_root = that path`, it uses the **SAME DB** claude-mpm uses. **Unifying the memory concern requires ZERO code changes** — it's a runtime/config alignment.

**ACTION:** VERIFY the `project_root` passed by `cli.py` resolves to the ActivityMonitor project (kuzu_client.py:30), not the open-maestro repo dir. See Verification Flags (§5).

### 3b. Upgrade the memory integration from CLI-subprocess to in-process Python API

**Effort:** M  
**Risk:** Medium

Use `KuzuMemoryClient`/`MemoryService`, set `enable_git_sync=False`. Faster, fixes a latent bug: `kuzu_client.recall()` JSON heuristic (lines 78-88) does `json.loads` on output that for `recall` is actually `enhanced` text. Localized change to one file.

### 3c. Move structured state (milestones/gates/sessions) into kuzu

**Effort:** L–XL  
**Risk:** High  
**RECOMMENDATION:** DO NOT ATTEMPT

Requires introducing a storage abstraction that doesn't exist (retrofit 7 Store classes + 2 functions behind a `Protocol`) AND modeling validated/mutable/recomputed records in a store built for immutable NL memories with silent-skip dedup and no in-place update. Would break schema-2.0 validators, the `_recompute_summary` invariant, atomic session writes, gate hash-keying. **Risk here is architectural, not upgrade-fragility** (editable repo patches survive).

---

## 4. Recommendation

| Approach | Effort | Risk | Value | Verdict |
|----------|--------|------|-------|---------|
| **Share one DB (3a)** | S | Low | High | **Do first** |
| **External bridge, NL subset (§2)** | S | Low | Medium | **Optional add-on** |
| **Lib/MCP upgrade of wrapper (3b)** | M | Med | Medium | **Nice-to-have** |
| **Move structured state to kuzu (3c)** | L–XL | High | Negative | **Avoid** |
| **Do nothing** | – | – | Both speak kuzu, separate DBs | Acceptable baseline |

**PRIMARY:** Do 3a and stop — share one kuzu DB, change ~nothing. Run both against the same project root. Verify the `project_root` wiring (flag below). Effort S, Risk Low.

**SECONDARY (optional):** add the MVP one-way bridge (§2) only for the prose fields open-maestro doesn't push. Effort S. Strictly maestro→kuzu.

**TERTIARY (nice-to-have):** upgrade `kuzu_client.py` to in-process API (§3b) for speed + the recall-parse bug fix. Effort M.

**DO NOT** migrate structured state into kuzu (§3c). **Wrong tool. Effort L–XL, Risk High, negative value.** YAML stores are correct for that data.

---

## One-Line Answer

open-maestro is already a kuzu-memory client; the real task is to point both tools at the **SAME** `.kuzu-memory/memories.db` (currently separate), optionally add a tiny one-way bridge for structured-prose fields, and leave structured milestone/gate/session state in YAML.

---

## 5. Verification Flags / Open Items (resolve before acting)

- **Runtime project_root resolution:** Confirmed `kuzu_client.py` passes `--project-root`, but **NOT executed live** to confirm which path an ActivityMonitor run resolves to. The two DBs differ in size (open-maestro repo 1.1 MB vs ActivityMonitor 3.6 MB), strongly implying open-maestro currently writes to its **OWN** repo dir (`/Users/jj/dev/open-maestro/.kuzu-memory/`), not the ActivityMonitor store. **VERIFY before assuming auto-unification.**

- **Latent bug:** `kuzu_client.recall()` JSON heuristic (lines 78-88) vs actual CLI `enhanced` output — noted, not root-caused.

- **No LICENSE file on disk** despite MIT in pyproject; PyPI publication not network-verified.

- **kuzu batch_store_memories dedup pass-through unconfirmed** — use single `store` for guaranteed idempotency.
