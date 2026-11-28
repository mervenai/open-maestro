# Operator Console UI Wrapper Design for Open Maestro

- **Date:** 2026-11-26 (v0.1); revised 2026-11-27 (v0.2)
- **Status:** Draft v0.2 (decisions locked; open items listed in §13.1)
- **Related:** `docs/milestone-guided-experience-design.md`, `docs/DASHBOARD.md`, `docs/code-critic-gate-plan.md`, `docs/requirements/Merven platform prototype.html` and `docs/requirements/AutonomyAI.png` (both untracked; they exist only in the primary checkout, see the note below)

> The two `docs/requirements/` inputs are directional material. At the time of writing they are not committed to the repository.

### Document status (read this first)

- **Verdict:** build the Operator Console as a local, single-user wrapper (`maestro --ui`, FastAPI + React) over Maestro's existing milestone, playbook, awaiting-input and critic machinery, after a Phase 0 backend refactor that leaves the REPL's behaviour unchanged.
- **Draft vs locked:** "Draft" describes the document; the decisions D-1 … D-9 in §13.1 are locked. What remains open is the open questions (OQ-n) and risks (R-n) in the same register.
- **Unresolved items by severity (§13.1):** 11 non-decision items (4 with Status Open: OQ-1, OQ-2, OQ-3, R-1; 7 with Mitigation planned). By severity: Critical 0, High 4 (OQ-3, R-1, R-3, R-8), Medium 4 (OQ-1, R-2, R-5, R-7), Low 3 (OQ-2, R-4, R-6).
- **Design-freeze blockers:** none. No item is Critical. Each High item must be closed before the phase named in its "Target phase" column exits; that is a phase-exit condition, not a design-freeze blocker. The first High **risk**, R-1, is fixed by verifying cancel on `kimi-acp`/`openai-sdk` in MAE-126 and recording partial artifacts in the run record.
- **Owners:** decisions and open items are owned by role, not by name: **Product owner (Operator Console)**, **Tech lead (Operator Console)** and **Security reviewer**. The owner of each item is in §13.1.
- **What changed since the previous round:** see §15 Change history (v0.1 → v0.2, review gate `068da645211d8f00`).
- **Final vs conditional:** three classes. **Final (locked):** the "Locked decisions" paragraph in §1, the §2 non-goals, §5.1, §7.2, §7.7, §7.10, and register rows D-1 … D-9. **Conditional:** §11 Phases 2-4 (re-planned at each phase start), the OQ-n and R-n rows of §13.1, and Appendix C payloads (illustrative, finalised by the OpenAPI schema). **Plan of record** (everything else, including §5.2, §6-§10, §11 Phases 0-1, §12, §14 and Appendices A-B): current and binding, changed only through an entry in §15.
- **Corrections:** open a pull request against this file, or comment on the relevant MAE story in `docs/maestro-user-stories.csv` / Jira. The Tech lead (design owner) triages corrections.

---

## 1. Context

Open Maestro (v3.8.0, `pyproject.toml`) is used only from the terminal: the `maestro` CLI and the prompt_toolkit REPL in `src/open_maestro/interactive.py`. Two needs drive this design:

1. **Less-technical operators** (PMs, POs, consultants) need to run a project's milestone lifecycle without learning slash commands.
2. **The team needs a credible, token-free demo** for prospective clients.

Two directional inputs exist:

- **The Merven platform prototype** (`docs/requirements/Merven platform prototype.html`): a Claude-Design React export with Operator, Client and Admin surfaces and five human gates.
- **The AutonomyAI screenshot and sales-call transcript** (`docs/requirements/AutonomyAI.png`): PM-facing chat with a live preview, a "Send to Devs" action (the orange button at the top right of the screenshot; image only, not in any text file), and the CPO's line *"PR is too late — I need something on the left-hand side of the page."*

**Both inputs are aspirational**; neither describes software that exists. (One adjacent piece is real: `src/open_maestro/milestones/supabase_publisher.py` already publishes milestone snapshots to a Supabase table that the separately hosted merven.ai dashboard renders. That dashboard lives outside this repository and v1 does not depend on it: the §6 row that reads the publisher is tagged A (Phase 4), and demo step 10 only mentions it aloud.) Maestro's existing milestone, playbook, awaiting-input and chain-critic machinery is the real foundation, and this design wraps it rather than inventing a new product.

**Locked decisions:** the deliverable is this document plus backlog stories (no code); the UX is an operator console first, shaped like Merven's Operator surface; deployment is a local app (`maestro --ui`) using the operator's own credentials; the stack is FastAPI in-process plus a React/Vite single-page app (SPA). In the §13.1 register these four are D-9 (deliverable), D-1 (UX), D-2 (deployment) and D-3 (stack).

## 2. Objective

**Goals**

- Without the terminal, an operator can open a project, see milestone progress, launch the next playbook prompt with remembered inputs, watch the run live, answer the agent's kickoff questions, and mark milestones complete.
- A demo mode replays a scripted run of a seeded project end to end, with no network calls and no tokens.
- A Phase 0 backend refactor adds the seams (`TurnService`, `OperatorIO`, a stream-to-EventBus bridge, run records) while the REPL keeps its current behaviour.

**Non-goals for v1:** hosting (the console binds to loopback); multiple users, roles or tenants; the client portal and chat-plus-preview workbench (Phase 4, separate design); changing chain semantics by default (blocking critic gates are opt-in in Phase 2).

## 3. Personas

| Persona | Needs | Primary screens |
|---|---|---|
| **Operator** (less-technical PM/PO/consultant) | See status, run the next prompt, answer agent questions, mark milestones, know the cost | Dashboard, Milestones, Launch, Inbox, Run detail |
| **Demo audience** (CPO / engineering lead) | Watch a governed run with approvals *before* code is written ("left-hand side") | Launch confirm sheet, live log, Inbox, Gates |
| **Engineer reviewer** | Critic verdicts, persona reviews, diffs, model and agent tuning | Runs, Gates & review, Artifacts/Diff, Admin |

## 4. Current state and seams

Every reference below was checked against the code at commit `8564f25`; references added in v0.2 were re-checked at `3895a6d` (no `src/` changes between the two).

| Finding | Evidence | Consequence |
|---|---|---|
| `run_interactive()` (~545 lines) inlines everything: startup at 2412-2512 (sources, registry, sessions, memory, `EventBus`, state, session restore, milestone discovery) and the per-turn body at 2592-2943. | `src/open_maestro/interactive.py:2401-2946` | No reusable turn API for a web surface. |
| Four blocking prompt helper families sit in the turn path (the module has 13 `questionary` call sites in total, some off the turn path, e.g. the post-run review at :2031 and milestone revisit at :2066): playbook inputs (`_ask_playbook_inputs`, :1644), repo clarification (`_maybe_clarify_repo_path`, :1159, which uses questionary at :1248-1544), escalation confirm (`_confirm_escalation`, :2077) and awaiting-input rulings (`_ask_awaiting_questions`, :1708). | `src/open_maestro/interactive.py` | Abstract behind `OperatorIO`. |
| There are 80 `Path.cwd()` call sites under `src/open_maestro/`, and `EventBus` is a process singleton (`__new__`). | `src/open_maestro/events/bus.py:20-29` | v1 runs **one project per process**. |
| `claude-cli` pipes stream-json through `pump_stream` into a terminal-only Rich `StreamPrinter`. The only bus events it emits are `runtime.working` heartbeats (every 5 s). | `src/open_maestro/runtime/claude_cli.py:306-309,478-483`; `src/open_maestro/runtime/stream_printer.py:135,259` | Tool activity never reaches the bus. |
| `claude -p` never prompts. Denials come back in the `permission_denials` result field. `--permission-mode` (`allow\|auto\|yolo\|read-only`) is a **process-wide** flag that does reach the claude-cli subprocess today: `interactive.py:2811,2831` (and `cli.py:919,939`) pass `args.permission_mode` to `PMAgent.handle` (`orchestrator/pm.py:164`), which copies it into `AgentConfig.permission_mode` (`pm.py:1087-1088`; `runtime/base.py:49`); `claude_cli.py:222-223` appends `--permission-mode <value>` to the argv. The same value sets Maestro's in-process `PermissionPolicy(mode=permission_mode or "allow")` (`pm.py:1132-1133`). The value is forwarded unmapped (see OQ-3), and there is no per-turn override. | `src/open_maestro/runtime/claude_cli.py:152,222-223,414`; `src/open_maestro/cli.py:397-401`; `src/open_maestro/orchestrator/pm.py:164,1087-1088,1132-1133` | The UI must surface denials. Passing the mode per turn is **new work** (`TurnRequest.permission_mode`, MAE-110, Phase 0) so the console can apply D-6 without a process-wide flag; the end-to-end path to the subprocess already exists. v1 offers no user-selectable mode (D-6). |
| The critic gate is advisory: it emits `chain.critic_gate` and appends the verdict, never blocks. | `src/open_maestro/orchestrator/chain.py:700-722` (emit at :711) | Phase 2 adds opt-in blocking. |
| `MilestoneStore.load()` writes a default plan when none exists; `exists()` is already present. | `src/open_maestro/milestones/store.py:99-115` | GETs need a side-effect-free load. |
| Drift-guarded mutations already exist: `open_session`, `commit` (which raises `DriftError` on an mtime change), `set_milestone_status`, `add_blocker` and `resolve_blocker`. Already used by MCP. | `src/open_maestro/milestones/mutations.py:58-277`; `src/open_maestro/mcp/server.py:24` | The milestone board is wrap-only. |
| A human-in-the-loop protocol is ready to use: `awaiting-input.yaml` with `load`/`clear`/`parse_bulk_answers`/`format_answers_message`. | `src/open_maestro/awaiting_input.py:19,51,100,121,143` | The Inbox is mostly wrap-only. |
| Playbook templates already carry `agent_hint`, `read_only`, `after` and `inputs`. Answers persist in `.open-maestro/prompt_inputs.yaml`. | `src/open_maestro/milestones/playbook.py:32-56`; `src/open_maestro/interactive.py:1618`; `src/open_maestro/milestones/commands.py:57-85` | Launch can show prerequisites and remembered inputs. |
| There is no `fake` runtime under `src/`; `FakeRuntime` is defined across test modules (`tests/test_pm.py`, `tests/test_router.py`, `tests/test_context.py`, `tests/test_review.py`, `tests/test_chain.py`), not under `src/`. The runtime registry is in `src/open_maestro/runtime/factory.py:27-31`. | as cited | Demo mode needs a new scripted runtime. |
| `prompt_toolkit` (lazy import at `interactive.py:1952`) and `mcp` (`src/open_maestro/mcp/client.py`, `server.py`) are imported but not declared in `pyproject.toml`. | `pyproject.toml` | Declare them in Phase 0. |
| `SessionStore.prune(keep=50)` exists but has no production caller (only `tests/test_session_store.py:150`). | `src/open_maestro/session/store.py:167` | Wire run-record pruning explicitly. |

## 5. UX direction and prototype reconciliation

The console takes the **Merven Operator surface's shape**: a 200 px sidebar, KPI row, data tables and status badges. Every screen is backed by real Maestro state. The CPO's "left-hand side" approval is the **Launch confirm sheet** plus the **Inbox** for kickoff questions, both before any code is written.

### 5.1 Dropped for v1

| Prototype item | Reason for dropping |
|---|---|
| Users, roles, MFA | The app is local, single-user and runs on the operator's own machine and credentials. |
| Hash-chained audit log | Run records plus the review `GateLedger` (`src/open_maestro/review/gate.py:112`) already serve as the trail. |
| Jira/Linear connectors | Out of scope for v1 (revisited in Phase 4). `scripts/sync_jira.sh` stays the backlog path. |
| Execution Gantt | Maestro executes as a step list (chain/swarm steps). A Gantt would invent timing data. |
| Numeric confidence with a 0.75 threshold | Maestro has nothing equivalent. The critic's `PASS/WARN/BLOCK` verdict is the honest substitute. |
| Submit PR | v1 does not write to git remotes. Diff is view-only. |
| Spend-cap enforcement | Cost is displayed only, and no budget is enforced. |
| Digital Twin | No foundation exists for it. |

### 5.2 Gate mapping

"Mechanism today" says what exists in the REPL/backend; "Console tag" uses the §6 tags and says what the console still needs. The two columns are deliberately separate so that §5.2 and §6 agree.

| Merven gate | Maestro equivalent | Mechanism today | Console tag |
|---|---|---|---|
| Agent config | Launch confirm sheet: agent, model, runtime, inputs, and the permission mode shown read-only (`auto`, or `read-only` for read-only prompts; D-6) | Real in REPL, as process-wide CLI flags and REPL state | **B**: console exposure needs per-turn `TurnRequest` fields (MAE-110, Phase 0). `TurnRequest.permission_mode` carries the D-6 value; v1 offers no mode picker |
| Blueprint approval | "Design Blueprint" milestone completed, plus the `review/` gate ledger | Real backend (`src/open_maestro/review/gate.py`, `src/open_maestro/milestones/mutations.py`) | **B**: Gates & review screen, Phase 2 (MAE-133) |
| Code review | Critic verdict plus diff | Advisory (`src/open_maestro/orchestrator/chain.py:711`) | **B**: Phase 2 (MAE-132); opt-in blocking |
| Low-confidence escalation | Prefer-local escalation confirm plus awaiting-input | Real in REPL (`interactive.py:2077`, `src/open_maestro/awaiting_input.py`) | **B**: console exposure needs `OperatorIO` (MAE-111, Phase 0) |
| Digital Twin | — | — | Dropped |

## 6. Screen inventory (v1 Operator console)

Tags: **R** = real today (wrap only), **B** = backend work required, **A** = aspirational. A row's Phase says when the screen ships; its tag says how much backend work it needs.

| Screen | Merven analogue | Reads | Writes | Tag |
|---|---|---|---|---|
| Projects (picker) | Projects list / New project | New `~/.open-maestro/projects.yaml`; each project's `milestones.yaml` summary | Registry upsert; Initialize is `POST /api/project/init`, which calls `MilestoneStore.load(create_if_missing=True)` explicitly (the `--discover-milestones` flag at `cli.py:232` only prints suggestions, `cli.py:533-545`) | B |
| Dashboard | KPIs, live runs, awaiting action, LLM usage | `MilestonePlan.summary`, active run, `awaiting-input.yaml`, run cost rollup | — | R (KPIs) / B (live, usage) |
| Milestones board (8 lifecycle columns per epic) | Audit & Blueprint progress | `milestones.yaml`, `gate_advisory` (`milestones/commands.py:168`), `exit_criteria`, artifact globs (`milestones/models.py:65-66`). GET path depends on `MilestoneStore.load(create_if_missing=False)` (MAE-115, Phase 0) because `load()` currently auto-creates default plans (`src/open_maestro/milestones/store.py:103-115`). Mutations are real today. | `mutations.set_milestone_status`, `add_blocker`, `resolve_blocker` | R |
| Launch (playbook prompt picker) | Orchestration tabs + launch confirm | `get_current_or_next_milestone_prompts` (`milestones/commands.py:85`), `PromptHistoryStore` (`milestones/prompt_history.py:119`), `prompt_inputs.yaml`, template `inputs/agent_hint/read_only/after` | Start a turn via TurnService; save inputs | B |
| Ask (free-form, with agent/model/chain/swarm/dry-run toggles) | Skill toggles | Agents registry, model config (`src/open_maestro/config/default_models.yaml`, user override `~/.open-maestro/models.yaml`) | Start a turn | B |
| Runs list | Agent runs | New `runs/*.yaml`, `sessions/*.yaml` | — | B |
| Run detail (live log, prompt, result, tokens, cost, critic verdict, permission denials, Cancel) | Run detail approve/edit/reject | `runs/<id>.yaml` + `.ndjson`, SSE | Cancel; follow-up turn in the same session | B |
| Inbox (awaiting-input rulings, escalation confirms, repo clarifications) | Awaiting action / HITL gates | `awaiting_input.load()`, pending interactions | Rulings/DEFER via `format_answers_message`, sent as the next turn | B (R for marker parsing) |
| Gates & review (Phase 2) | Five gates / Code review | `chain.critic_gate`, `GateLedger`, `reviews/<doc>-<sha>/<persona>.md` (`review/blueprint.py:610`) | Acknowledge/override is recorded | B |
| Artifacts / Diff (Phase 2) | Legacy vs generated | `git diff` over milestone artifact globs | — (no Submit PR) | B |
| Todos & Notes (Phase 2) | Communications (partial) | `todos.json` (`todos/store.py:14`), Basic Memory | Todo CRUD, remember | R backend / B screen (the stores exist; the screen ships in Phase 2) |
| Admin (Phase 3) | LLM routing, skill registry, monitoring | `src/open_maestro/config/default_models.yaml` and `default_capabilities.yaml` (user overrides `~/.open-maestro/models.yaml`, `capabilities.yaml`), latency (`runtime/latency.py`), quota (`runtime/quota.py`), agents registry, `sources.yaml`, `mcp.json` | Model pins, source sync | B |
| Client portal, Connectors, Messages, chat+live-preview workbench | Client portal / AutonomyAI | `milestones/supabase_publisher.py` (real; publishes snapshots to the externally hosted merven.ai dashboard) | — | A (the portal and workbench screens) |

## 7. Backend architecture

### 7.1 Package layout

New package `src/open_maestro/ui/`: `app.py` (`create_app(project_root, settings)`), `routes/`, `sse.py`, `interactions.py` (`WebIO`), `runs.py` (`RunManager`), `registry.py`, `auth.py`, `demo/`, and `static/` (built SPA, git-ignored). A new `bootstrap.py` takes over the startup sequence at `interactive.py:2412-2512`, so the REPL and the UI build the registry, session store, memory, bus and state the same way.

### 7.2 CLI flags and dependencies

New flags in `src/open_maestro/cli.py`:

- `--ui` starts the console.
- `--ui-port` sets the port; `0` picks a free one, recorded in `~/.open-maestro/ui-servers.yaml`.
- `--ui-host` accepts loopback addresses only (default `127.0.0.1`); any other host is refused, and v1 has no override.
- `--no-browser` skips opening a browser; `--demo` starts demo mode (section 9).

uvicorn's `Server.serve()` runs on Maestro's existing asyncio loop, then the CLI calls `webbrowser.open("http://127.0.0.1:<port>/#t=<token>")`. Dependencies: declaring `prompt_toolkit` in core dependencies and adding a `[mcp]` extra for `mcp` is the dependency-declaration part of MAE-131 and lands first in Phase 0 (§11); the `ui = [fastapi, uvicorn, sse-starlette]` extra lands with the rest of MAE-131 in Phase 1.

Phase 4 note: binding to a non-loopback interface needs the hosted trust model (users, real auth, TLS) and is designed there; it is not a v1 flag (D-2).

### 7.3 One project per process

The 80 `Path.cwd()` call sites and the singleton bus mean each process serves one project. The Projects picker either spawns `maestro --ui --no-browser` with `cwd=<path>` or reuses a live server from `ui-servers.yaml`. A multi-project hub mode is a Phase 3 question.

`MilestoneStore.load()` gains a `create_if_missing` parameter whose default, `True`, keeps today's REPL behaviour. API GET handlers pass `False`, so an uninitialised project returns `404 not_initialized` and the UI shows an "Initialize project" button. (`exists()` already exists.)

### 7.4 TurnService

`src/open_maestro/turns/` defines `ConversationState`, `TurnSettings`, and:

- **`TurnRequest`**: `text`, `prompt_id`, `edited`, `read_only`, `agent_id`, `model`, `runtime`, `chain`, `swarm`, `dry_run`, `show_plan`, `prefer_local`, `permission_mode` (new: today the mode is a process-wide CLI flag, §4), `input_answers`, `from_playbook`.
- **`TurnOutcome`**, returned by `TurnService.run(req, state)`: `result`, `run_id`, `session_id`, `runtime`, `model`, `artifact_warning`, `milestone_update`, `awaiting: AwaitingInputBatch | None`, `cancelled`, `error`.

`TurnService` holds the logic now at `interactive.py:2592-2943`, in the same order. It **does not ask** the awaiting-input questions; it returns the batch and the caller collects rulings. The REPL shrinks to: read input, handle commands, build a request, run it, print the outcome. `run()` sets a `contextvars.ContextVar("run_id")`, so every event in the turn, including chain and swarm steps, can be tagged with the run id without changing any emitter.

### 7.5 OperatorIO

The `OperatorIO` protocol (`turns/io.py`) has `confirm`, `select`, `checkbox`, `text`, `rulings` and `notify`. Each interaction has a stable `key` (`escalation.confirm`, `repo.clarify`, `playbook.input:<prompt>:<qid>`). Returning `None` or raising `OperatorCancelled` means Esc.

- **`TerminalIO`** wraps the existing questionary functions unchanged.
- **`WebIO`** creates a `PendingInteraction`, emits `interaction.requested` and awaits a Future, which `POST /api/interactions/{id}` resolves. After 30 minutes it times out and cancels.

The web Launch screen collects playbook inputs before the run starts, so `WebIO` only handles mid-turn surprises. `gh` repo cloning is disabled in the web flow for v1.

### 7.6 Stream bridge

`pump_stream` gains an `on_event=` callback. `StreamPrinter._try_claude_stream_json` (`stream_printer.py:135`) becomes a pure `parse_claude_stream_line(line) -> list[ParsedEvent]`, whose events go on the bus as `tool.call` and `tool.result` (existing names), a new `runtime.text {delta}`, and a new `runtime.permission_denied`. The kimi parser (`_try_kimi_stream_json`, :92) gets the same treatment. Terminal printers stay quiet in UI mode.

### 7.7 Server-Sent Events (SSE), not WebSocket

SSE fits because traffic is one-way push, browsers reconnect automatically with `Last-Event-ID`, it is easy to contract-test, and it works with cookie auth (`EventSource` cannot set headers).

`GET /api/events?since=<seq>` replays from a ring buffer of about 2,000 events; each carries `seq`, `run_id`, `ts`, `type` and `payload`. An `on("*")` bus subscriber feeds the buffer and appends to the per-run NDJSON log. It redacts `api_key` and token fields, truncates tool inputs to 100 characters (matching `_summarize_tool_input`, `stream_printer.py:58`), and sends a heartbeat every 15 seconds.

### 7.8 RunManager and the run lock

`RunManager` allows one run per project:

- `POST /api/runs` returns `409` while a run is active; a depth-1 queue holds awaiting-input follow-ups.
- `POST /api/runs/{id}/cancel` calls `task.cancel()`, the equivalent of Esc Esc in the REPL (`_run_with_interrupt`, `interactive.py:1860`). `claude_cli.py` already kills the child and drains its output on `CancelledError` (:318-349).
- Shutdown cancels the active run and finalises its record.

A **cross-process lock**, `.open-maestro/run.lock`, stores `{pid, surface, run_id, started}`. A lock held by a dead pid is reclaimed. The REPL and the UI both honour it.

### 7.9 Run records

Each run writes `.open-maestro/runs/<id>.yaml`, `<id>.ndjson` and `<id>.md` (full result); the schema is in Appendix B. `TurnService` writes them, so REPL turns also appear in the UI. Run records are pruned to the newest 200. This is a separate policy for runs: it reuses the shape of `SessionStore.prune` (whose own default, `keep=50`, applies to sessions and has no production caller today) but not its number. Sessions and `prompt_history.yaml` are written as today.

### 7.10 Local auth

At start-up the server generates a token with `secrets.token_urlsafe(32)` and puts it in the URL fragment. The SPA sends it to `POST /api/auth` and receives an `HttpOnly; SameSite=Strict` cookie. The server rejects a foreign `Host` header (blocking DNS rebinding) and mutations with a mismatched `Origin`. It sends no CORS headers, never serves `config.yaml`, and masks the publish token.

### 7.11 REST resources (`/api`)

| Resource | Endpoints |
|---|---|
| project | `GET`, `POST /init` |
| projects | `GET`, `POST /{id}/open` |
| milestones | `GET`, `PATCH /{epic}/{ms}`, blockers `POST` / `DELETE` |
| prompts | `GET ?milestone=` |
| runs | `POST`, `GET`, `GET /{id}`, `GET /{id}/events`, `POST /{id}/cancel` |
| awaiting-input | `GET`, `POST` |
| interactions | `GET`, `POST /{id}` |
| events | `GET` (SSE) |
| sessions | `GET`, `POST /session/reset` |
| agents, models, usage, todos | `GET` (+ todo CRUD) |
| memory | `search`, `notes` |
| Phase 2 | reviews, gates, artifacts/diff |

## 8. Frontend architecture

- **Stack:** Vite, React 18, strict TypeScript; `react-router` routes `/`, `/milestones`, `/launch`, `/ask`, `/runs`, `/runs/:id`, `/inbox`, `/gates`, `/admin/*`; TanStack Query for REST; types generated with `openapi-typescript`.
- **Live updates:** one `useEventStream()` hook owns the `EventSource` and feeds a small Zustand store (active run, log lines, pending interactions). It invalidates queries on `run.completed` and `interaction.*`. No `milestone.*` bus events exist today, so milestone queries are invalidated on `run.completed` and after the UI's own PATCH requests.
- **Components:** design-system components are re-implemented locally in `ui/src/ds/` under Merven's names: Button, CodeChip, MonoText, CountBadge, Icon, StatusBadge, DataTable, KpiCard/KpiRow, PageHeader, NavItem/Sidebar (200 px), MaskedCredential, TrustNote. Console-specific: LogStream, InteractionCard, MilestoneColumn, PromptCard, LaunchConfirm.
- **Tokens:** `--forest #1a3c34`, `--paper #f7f6f3`, `--teal #1fb5a5`; flat cards; Geist and Geist Mono via `@fontsource` (OFL; ship the licence) with a system-font fallback.
- **Layout:** the v1 sidebar lists Dashboard, Projects, Milestones, Launch, Ask, Runs and Inbox (with a CountBadge); Gates & Review, Todos & Notes and Admin arrive later. The header shows the project name, a lock/active-run pill, and a Demo badge in demo mode.

## 9. Demo mode and walkthrough

`maestro --ui --demo` copies `src/open_maestro/ui/demo/fixture-project/` (a seeded "Acme Claims Portal" at ~30%: 2 milestones done, prompt history, 1 blocker, sample persona reviews) to a temp directory. It forces a new **`runtime/scripted.py`** runtime, registered in `runtime/factory.py`, that replays `demo/script.yaml`: timed tool events, an artifact write, an optional awaiting-input batch and a canned critic WARN. No network, no tokens; a persistent Demo banner; the same runtime drives Playwright. The scripted runtime itself ships in Phase 0 (§11); the fixture, `--demo` flag and banner ship in Phase 1.

In the Phase 1 demo, steps 8-9 are skipped: the fixture's persona reviews and canned WARN are seeded data, but the Gates and Artifacts screens that show them ship in Phase 2. From Phase 2 the demo walks all 10 steps, still on the scripted runtime.

**Walkthrough (10 steps)**

1. Open the Projects picker and open Acme.
2. Dashboard: overall %, current and next milestone, blockers, Inbox count.
3. Milestones board: 8 columns, two milestones complete, one blocker visible.
4. Launch "Design Blueprint" with remembered inputs. The confirm sheet is the "left-hand side" approval before any code.
5. The run detail streams the live tool log.
6. The agent asks two kickoff questions and the Inbox badge lights.
7. Rule on one question and defer the other. The follow-up continues in the same session.
8. Gates: the critic verdict (WARN) and persona reviews.
9. Artifact diff for the milestone.
10. Mark the milestone complete and the Dashboard % moves. Mention, aloud only, the merven.ai client dashboard (`milestones/supabase_publisher.py`) as a possible next step; the demo does not call it.

## 10. Packaging

The frontend source lives in a top-level `ui/` directory as a Vite project. `scripts/build_ui.sh` builds it into `src/open_maestro/ui/static/`, and hatch `force-include` puts that into the wheel. This uses the same mechanism that already bundles `agents` into `open_maestro/_bundled_agents` (`pyproject.toml`, `[tool.hatch.build.targets.wheel.force-include]`).

- CI builds the SPA before `uv build`.
- Python-only contributors never need Node; missing assets produce an instructive page, not a 500.
- Target: SPA under 3 MB in the wheel.
- Without `[ui]` the CLI is unchanged; `maestro --ui` prints an install hint and exits 1.

## 11. Phased roadmap

| Phase | Scope | Exit criteria | Main risk |
|---|---|---|---|
| 0 Backend seams (no UI) | **Delivered first, in order:** (1) dependency declaration — `prompt_toolkit` in core deps, `[mcp]` extra (the deps part of MAE-131); (2) scripted runtime `runtime/scripted.py` registered in `runtime/factory.py` (the runtime part of MAE-130); (3) the REPL golden-transcript test. **Then:** TurnService (incl. `TurnRequest.permission_mode`), OperatorIO/TerminalIO, stream→bus bridge, `run_id` contextvar, run records, `run.lock`, `load(create_if_missing)` | `prompt_toolkit` and `mcp` declared; scripted runtime replays deterministically with no network; `tests/test_interactive_golden.py` added in Phase 0 before extraction, and it must remain green and unchanged thereafter; all tests green; REPL turns write run records; claude-cli tool events visible via the existing `--stream` flag (`cli.py:382`) | Regressions in the 2.9k-line REPL. Write characterization tests first. |
| 1 Console MVP | `[ui]` extra and wheel packaging (rest of MAE-131), `--ui`, auth, registry, Dashboard, Milestones, Launch, Ask, Runs, Run detail + Cancel, Inbox, demo mode UI: fixture, `--demo`, banner (rest of MAE-130) | Operator completes intake→blueprint without a terminal; demo steps 1-7 and 10 on the scripted runtime; Playwright smoke test; SPA < 3 MB in wheel | Headless permission denials; SSE drops |
| 2 Gates & review | Critic panel + ack/override; optional blocking critic (`--critic-blocking` pauses the chain via `confirm("critic.gate")`); persona reviews + GateLedger; artifact diff; blockers, todos, memory notes | Demo steps 8-9 real; BLOCK can pause a chain | A blocking critic changes chain semantics |
| 3 Admin | Models/capabilities/latency/quota + pins; agents browser; sources; skills; MCP view; usage rollups; hub-mode investigation | Operator changes the default model; per-milestone spend visible | Safe YAML editing |
| 4 Aspirational | Client portal round-trip via the merven.ai dashboard; chat + live-preview workbench; Jira/Linear connectors; audit log; multiple users; non-loopback binding / hosting | Separate design | Trust-model change (hosting) |

## 12. Verification plan

For the eventual implementation:

- **Characterization first:** `tests/test_interactive_golden.py` does not exist yet. It is added in Phase 0, before any extraction, and records a REPL transcript against a deterministic runtime, plus prompt-history and milestone side effects. Once added it must remain green and unchanged for the rest of Phase 0 and after.
- **`tests/test_turn_service.py`:** hint routing; `TurnRequest.permission_mode` reaches `AgentConfig.permission_mode`; escalation through a stub IO; the awaiting batch is returned, not asked; a stale marker is ignored; a run record is written; cancel yields `cancelled`.
- **Stream bridge:** fixture tests on `parse_claude_stream_line` (text, tool_use, tool_result, permission denials).
- **`tests/test_ui_api.py`** (`httpx.AsyncClient(ASGITransport)`, `pytest.importorskip("fastapi")`): 401/403; 404 when uninitialised; PATCH drift; 409 single-flight; cancel; awaiting-input POST clears the marker and queues a follow-up; interaction resolve; redaction.
- **SSE:** ordering and `Last-Event-ID` replay.
- **Frontend:** Vitest for hooks; Playwright (`ui/e2e/`) walks demo steps 1-7 and 10 in Phase 1 and all 10 steps from Phase 2 (§9, §11), against `maestro --ui --demo --no-browser --ui-port 0`.
- **Packaging:** CI builds the wheel and installs it with and without `[ui]`.
- **Manual:** a real project in `auto` permission mode, with the REPL in a second terminal observing the lock.

## 13. Risks and open questions

**Headless permissions — decision D-6 (v1 behaviour).** v1 runs every non-read-only prompt with `--permission-mode auto`; read-only playbook prompts additionally keep the mutating-tool block that `PMAgent.handle` already applies (`orchestrator/pm.py:274-277`). `claude -p` never prompts, so a denied tool call does not pause the run: denials are listed on Run detail and stored in the run record (`permission_denials`, Appendix B). The Launch confirm sheet shows this mode but does not let the operator change it; a mode picker would need OQ-3 resolved first. **v1 does not offer "re-run with a widened allowlist".** Turning a denial into an Interaction that re-runs the turn with extra allowed tools is open question OQ-1 (owner: Product owner with Security reviewer; decide by Phase 2 start). It is not v1 behaviour, and nothing in Phases 0-1 depends on it.

### 13.1 Decision and open-question register

Severity: **Critical** blocks the design; **High** must be closed before its target phase exits; **Medium** must be closed or consciously accepted in its target phase; **Low** is tracked only. Status: **Locked** (decided), **Open** (undecided), **Mitigation planned** (approach chosen, not yet built), **Accepted** (risk accepted for v1).

| ID | Statement | Severity | Owner (role) | Target phase | Status |
|---|---|---|---|---|---|
| D-1 | The UX is an operator console first, shaped like Merven's Operator surface (§5). Client portal and workbench are Phase 4. | High | Product owner | 1 | Locked |
| D-2 | Local-only: the server binds to loopback, single user, operator's own credentials; no remote-bind flag in v1 (§2, §7.2, §7.10). Hosting is Phase 4. | High | Tech lead + Security reviewer | 1 | Locked |
| D-3 | Stack: FastAPI in-process with uvicorn on Maestro's asyncio loop, plus a React/Vite SPA (§7, §8). | High | Tech lead | 1 | Locked |
| D-4 | Live updates use SSE, not WebSocket (§7.7). | Medium | Tech lead | 1 | Locked |
| D-5 | One project per process (80 `Path.cwd()` sites, singleton bus); hub mode revisited in Phase 3 (§7.3). | Medium | Tech lead | 1 | Locked (Phase 3 may open a new decision for hub mode) |
| D-6 | Permission default: `auto` for non-read-only prompts, shown read-only on the Launch sheet (no picker), denials displayed, no interactive re-run in v1 (§13 above). | High | Product owner + Security reviewer | 1 | Locked |
| D-7 | Phase 0 delivers dependency declaration (deps part of MAE-131) and the scripted runtime (runtime part of MAE-130) first, before the golden test and the TurnService extraction (§11). | Medium | Tech lead | 0 | Locked (v0.2) |
| D-8 | No numeric critic confidence: show `PASS/WARN/BLOCK`; do not invent a score (§5.1). | Low | Product owner | 2 | Locked |
| D-9 | Deliverable of this design round: this document plus backlog stories MAE-110 … MAE-137; no code (§1). | Low | Product owner | — | Locked |
| OQ-1 | Should a permission denial become a "re-run with widened allowlist" Interaction? | Medium | Product owner + Security reviewer | 2 | Open |
| OQ-2 | Interactions time out after 30 minutes and cancel. Should the console also raise an OS notification? | Low | Product owner | 2 | Open |
| OQ-3 | `--permission-mode` values are forwarded to `claude` unmapped (`claude_cli.py:222-223`). The `claude --help` text on the review machine lists `acceptEdits, auto, bypassPermissions, manual, dontAsk, plan`, so of Maestro's `allow\|auto\|yolo\|read-only` only `auto` appears valid there (not exercised). Map or validate per runtime. | High | Tech lead | 0 (MAE-110) | Open |
| R-1 | Cancel semantics for `kimi-acp`/`openai-sdk`, and partial artifacts. claude-cli is proven (`claude_cli.py:318-349`); the others need verification. Partial artifacts go in the run record. | High | Tech lead | 1 (MAE-126) | Open |
| R-2 | Concurrent REPL+UI writes to `prompt_inputs.yaml`/`prompt_history.yaml`: atomic write plus re-read before merge. The run lock (MAE-116) covers turns, not these files; the first story where both surfaces write them is MAE-123 (Launch saves inputs), which owns this mitigation. | Medium | Tech lead | 1 (MAE-123) | Mitigation planned |
| R-3 | Secrets leaking via `/api` or SSE: bridge redaction, `config.yaml` never served, a redaction test. | High | Security reviewer | 1 (MAE-118, MAE-125) | Mitigation planned |
| R-4 | Wheel size: SPA under 3 MB, subset fonts. | Low | Tech lead | 1 (MAE-131) | Mitigation planned |
| R-5 | Undeclared deps (`prompt_toolkit`, `mcp`): declared first in Phase 0 (D-7). | Medium | Tech lead | 0 | Mitigation planned |
| R-6 | Node toolchain: needed only for UI work; a prebuilt SPA ships in the wheel. | Low | Tech lead | 1 | Mitigation planned |
| R-7 | `bm` memory-store latency must not block the milestone-status flip or `run.completed`. | Medium | Tech lead | 1 | Mitigation planned |
| R-8 | Phase 0 refactor regresses the 2.9k-line REPL. Golden test first; one seam per PR. | High | Tech lead | 0 | Mitigation planned |

Non-decision items (all OQ-n and R-n rows): 11 — 4 Open, 7 Mitigation planned; by severity Critical 0, High 4, Medium 4, Low 3. "Locked" with a later revisit means the decision holds for v1 and any change is a new register entry.

## 14. Story index (MAE-110 … MAE-137)

All stories are in `docs/maestro-user-stories.csv` with Status `Backlog`. MAE-130 and MAE-131 are delivered in two parts across Phases 0 and 1 (D-7); the CSV rows are not split, and the build plan records the split by pull request.

| Phase | Stories |
|---|---|
| 0 Backend seams | **First:** MAE-131 (dependency-declaration part), MAE-130 (scripted-runtime part). **Then:** MAE-110 TurnService, MAE-111 OperatorIO, MAE-112 stream→EventBus bridge, MAE-113 run records, MAE-114 run-id tagging, MAE-115 side-effect-free milestone reads, MAE-116 cross-surface run lock |
| 1 Console MVP | MAE-117 `maestro --ui` launcher, MAE-118 loopback token auth, MAE-119 project registry/picker, MAE-120 Initialize from UI, MAE-121 Dashboard KPIs, MAE-122 milestone board, MAE-123 Launch with inputs, MAE-124 free-form Ask, MAE-125 live run log over SSE, MAE-126 cancel, MAE-127 Inbox rulings, MAE-128 mid-turn interactions, MAE-129 runs list/detail, MAE-130 demo mode (fixture, `--demo`, banner), MAE-131 wheel packaging (`[ui]` extra, SPA bundle) |
| 2 Gates & review | MAE-132 critic gate review + optional blocking, MAE-133 persona reviews + gate ledger, MAE-134 artifact diff, MAE-135 blockers, todos, memory notes |
| 3 Admin | MAE-136 models, agents, sources, usage admin |
| 4 Aspirational | MAE-137 client portal approvals + chat/preview workbench (design spike only) |

---

## Appendix A. Event catalogue

**Existing events.** The `EventBus` (`src/open_maestro/events/bus.py`) defines no event names; it routes by string. Emitters today:

| Emitter | Events |
|---|---|
| `orchestrator/pm.py` | `task.received`, `source.load`, `memory.recalled`, `search.completed`, `context.threshold`, `agent.selected`, `model.quota_exhausted`, `runtime.started`, `runtime.completed`, `session.saved` |
| `orchestrator/chain.py` | `agent.selected`, `runtime.started`, `runtime.completed`, `chain.step_started`, `chain.step_completed`, `chain.critic_gate` (:711) |
| `orchestrator/swarm.py` | `swarm.started`, `swarm.worker_started`, `swarm.worker_completed` |
| `runtime/claude_cli.py`, `runtime/kimi_cli.py`, `runtime/openai_sdk.py` | `runtime.working` |
| `runtime/claude_sdk.py`, `runtime/kimi_acp.py`, `runtime/openai_sdk.py` | `tool.call`, `tool.result` |

`session.resume_hint` is **not** a bus event. It is a `meta` message type in kimi's subprocess output that is parsed, not emitted: `runtime/kimi_cli.py:338-341` copies it into `AgentResult.metadata["resume_command"]`, and `runtime/stream_printer.py:125-126` prints it to the terminal. The only `bus.emit` in `kimi_cli.py` is `runtime.working`.

**New events** (Phases 0-1):

| Event | Emitted by | Payload |
|---|---|---|
| `tool.call` / `tool.result` (newly from claude-cli and kimi-cli) | stream bridge | `{name, input_summary}` / `{name, is_error, summary}` |
| `runtime.text` | stream bridge | `{delta}` |
| `runtime.permission_denied` | stream bridge | `{tool, reason}` |
| `interaction.requested` | `WebIO` | `{id, key, kind, prompt, choices?, default?}` |
| `interaction.resolved` | `WebIO` / API | `{id, key, value \| cancelled}` |
| `run.started` | `TurnService` | `{run_id, surface, prompt_id, agent, runtime, model}` |
| `run.completed` | `TurnService` | `{run_id, status, tokens, cost, critic_verdict, awaiting}` |

Every event forwarded over SSE is wrapped as `{seq, run_id, ts, type, payload}`.

## Appendix B. Run record schema (`.open-maestro/runs/<id>.yaml`)

```yaml
id: run-20261126-142233-7f3a          # sortable, unique
surface: ui                            # ui | repl
status: completed                      # queued|running|awaiting_input|completed|failed|cancelled
started: 2026-11-26T14:22:33Z
ended: 2026-11-26T14:26:51Z
prompt: "Produce the Design Blueprint data/API contract ..."
prompt_id: design-blueprint/contract   # null for free-form Ask
epic_id: default
milestone_id: design_blueprint
agent: architect
runtime: claude-cli
model: claude-opus-5-5
session_id: 3b1c...                    # backend session, reused by follow-ups
tokens: {input: 41210, output: 6893}
cost: 0.83                             # USD, display only
critic_verdict: WARN                   # PASS|WARN|BLOCK|null
permission_denials:
  - {tool: Bash, input_summary: "git push origin ..."}
artifact_warning: null
error: null
result_excerpt: "Wrote docs/blueprint-design-and-data-contract.md ..."
# siblings: <id>.ndjson (event log), <id>.md (full result text)
```

## Appendix C. Sample API payloads

`POST /api/runs`: launch a playbook prompt.

```json
{
  "prompt_id": "design-blueprint/contract",
  "text": null,
  "edited": false,
  "input_answers": {"target_repo": "acme-claims-api", "contract_style": ""},
  "agent_id": null,
  "model": null,
  "chain": true,
  "swarm": false,
  "dry_run": false,
  "prefer_local": false
}
```

The response is `202 {"run_id": "run-20261126-142233-7f3a"}`, or `409 {"error": "run_active", "run_id": "..."}` if a run is already active.

SSE frame from `GET /api/events`:

```
id: 1842
event: tool.call
data: {"seq":1842,"run_id":"run-20261126-142233-7f3a","ts":"2026-11-26T14:23:05.112Z","type":"tool.call","payload":{"name":"Write","input_summary":"{\"file_path\": \"docs/blueprint-design-and-data-contract.md\", \"content\": \"# Blueprint ..."}}
```

`POST /api/awaiting-input`: rule on one question and defer another.

```json
{
  "batch_id": "2026-11-26T14:26:40Z",
  "answers": {
    "1": "Use REST with OpenAPI 3.1; GraphQL is out of scope for v1.",
    "2": "DEFER"
  }
}
```

The server formats the answers with `format_answers_message`, clears the marker, and queues the follow-up turn in the same session. It responds with `202 {"run_id": "run-20261126-142712-91c0"}`.

## 15. Change history

| Date | Version | Change |
|---|---|---|
| 2026-11-26 | v0.1 | Initial design (PR #10). |
| 2026-11-27 | v0.2 | Review-gate remediation (gate `068da645211d8f00`: fidelity and blind-reader findings). Removed `--ui-allow-remote`; v1 binds to loopback only, hosting moves to Phase 4 (D-2). §5.2 split into "Mechanism today" and "Console tag" so it agrees with §6 (escalation needs `OperatorIO`, MAE-111; agent config needs per-turn `TurnRequest` fields, MAE-110). Chose to keep the scripted runtime and dependency declaration in Phase 0 and deliver them first, as the runtime part of MAE-130 and the deps part of MAE-131, rather than moving them to Phase 1 (D-7; §11, §14). Golden test is now "added in Phase 0 before extraction; must remain green and unchanged thereafter". `session.resume_hint` moved out of the emitter table (parsed metadata). Permission-mode wiring stated with file:line, per-turn override marked as new work, value mapping raised as OQ-3. Headless-permission behaviour made an explicit decision (D-6) with re-run-with-widened-allowlist as OQ-1. Added the document-status block, the §13.1 register with severity and owner roles, and this history. Second gate round (`cb121c3577634bd4`): Playwright scope matched to the Phase 1/2 demo split; D-6 made explicit that the Launch sheet shows the mode read-only with no picker; R-2 re-owned by MAE-123; D-9 added for the "no code" deliverable so §1's four locked decisions all have IDs; status-block counts, final/conditional classes and header date clarified. Smaller clarifications: blocking-prompt count, startup range 2412-2512, Initialize path, config file paths, Todos tag, `create_if_missing` default, run-record pruning, Phase 1 demo steps 8-9, merven.ai status, Jira/Linear "out of scope for v1". |
