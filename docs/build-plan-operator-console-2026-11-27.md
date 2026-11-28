# Operator Console Build Plan in Maestro's Lifecycle

- **Date:** 2026-11-27
- **Status:** Draft
- **Related:** `docs/ui-operator-console-design-2026-11-26.md` (design v0.2; v0.1 merged in PR #10), `docs/milestone-guided-experience-design.md`, `docs/playbooks/internal-engineering.yaml`, `docs/code-critic-gate-plan.md`, `docs/jira-sync.md`, `docs/maestro-user-stories.csv`

---

## 1. Context

The Operator Console design merged in PR #10 (`5652453`) with backlog stories MAE-110..137. This plan schedules that work through Maestro's own 8-milestone lifecycle (`src/open_maestro/milestones/templates.py`): intake-discovery, execution-planning, design-blueprint, build-planning, implementation, qa-integration, demo-delivery, retrospective-findings. Statuses are `not_started | in_progress | blocked | completed | skipped` (`milestones/models.py`). Using Maestro to build Maestro's UI checks the lifecycle on a real project and leaves an audit trail.

Two hygiene defects found during research would interfere with the plan, so they become stories:

- **MAE-138.** The tests leak `runtime_name: fake` sessions into the real repo's `.open-maestro/sessions/` (208 files). Also, `MilestoneStore.update()` writes `dashboard.html` to the repo root, and `.gitignore` does not cover it.
- **MAE-139.** No `add_epic` mutation or MCP tool exists (`milestones/mutations.py`, `mcp/server.py`), so phase epics can only be added by hand-editing `milestones.yaml`.

## 2. Objective

- Map design-doc phases 0-3 to four work epics. Give each epic all 8 milestones, plus playbook prompts, required artifacts, checkable exit criteria and the gate that applies.
- Sequence MAE-110..139 into branches, with dependencies, primary files and verification.
- Fix one release and story-sync procedure per epic.
- Ship a forked playbook, `docs/playbooks/internal-engineering.yaml`, written for Maestro developing itself.

**Non-goals.** Hosted mode and the client portal: design-doc Phase 4 and MAE-137 are out of this plan. Auto-tracking: this plan is **not** committed to `.open-maestro/milestones.yaml`. This document is the tracking artifact, and the Appendix gives an opt-in local plan.

## 3. Programme track (`default` epic)

The `default` epic covers the project-wide phases, which run once.

| Milestone | Status | Prompts effectively executed | Satisfying artifacts |
|---|---|---|---|
| intake-discovery | completed | intake-001 (requirements folder, sales-call transcript and Merven prototype analysed); intake-002 (reuse: design §4 seams table); intake-003 (risks: design §13); intake-004 (epics: design §11 phases) | `docs/ui-operator-console-design-2026-11-26.md` §1, §4, §5, §13. The `docs/requirements/` inputs stay untracked. |
| execution-planning | completed (plan approved 2026-11-26) | plan-001 (every seam re-verified at `8564f25`, design §4); plan-002 (locked decisions, design §1) | Same doc, §1 "Locked decisions" and §4 |
| design-blueprint | in_progress | design-001 (API contract, design §7.11 and Appendix C); design-002 is not done (no diagram); design-003 (screens, design §6 and §8); design-005 is pending | Design doc §6-§10. Gate: `maestro --review docs/ui-operator-console-design-2026-11-26.md` |
| build-planning | in_progress (completes when this plan merges) | build-001 at programme level (this document) | `docs/build-plan-operator-console-2026-11-27.md` |
| implementation, qa-integration, demo-delivery | skipped (carried by the phase epics) | — | §5-§8 below |
| retrospective-findings | not_started (programme retro after e3) | retro-001..003 | `docs/findings/default-lessons-learned.md` |

**Gate state.** `/complete default/design-blueprint` waits for a passing `maestro --review` on the design doc. That review is a separate step, not part of this change. The primary checkout's ledger (`.open-maestro/gates/068da645211d8f00.json`) holds an entry for the design doc's current sha256: `blind-reader` **fail**, with 2 blocking confusions (2026-11-27T07:03). Fix those two confusions, re-run the review, and then complete the milestone (see §11 on the filename mismatch). This document matches `docs/build-plan*.md`, so it also satisfies the default track's build-planning artifact.

## 4. Phase epics: shared conventions

- **Epic ids:** `e0-backend-seams`, `e1-console-mvp`, `e2-gates-review`, `e3-admin`. They follow design §11 phases 0-3.
- **Kickoff statuses:** intake-discovery and execution-planning start as `skipped` ("satisfied by reference to the default track"), the same as `auto_populate._SKIP_BY_DEFAULT`. The rest start as `not_started` unless noted.
- **Design-blueprint in work epics:** every design-* prompt is `default_track_only`, so `/next` offers nothing here. e0 and e1 mark it `skipped` and point to design §7-§10. e2 and e3 each write a short spec addendum. Its name matches `docs/*spec*.md`, so the review gate applies naturally.
- **Delivery rule:** one story per branch `feat/MAE-nnn-<slug>` off main, one PR, squash-merge. Only the release commit goes straight to main.
- **Gates.** *Artifact check:* `/complete` refuses until the `required: true` globs exist (`handle_complete_command`). *Review gate:* fires only for `docs/blueprint*.md`, `docs/*contract*.md` and `docs/*spec*.md` (`review/blueprint.py`). *Code-critic:* advisory, after engineer turns of more than 50 lines (`orchestrator/pm.py`). *Artifact-critic:* fires on docs changes of more than 50 lines. *CI:* `uv run ruff check` and `uv run pytest` on every PR. *Playwright:* e1 onward.
- **Playbook-prompt column:** shows which prompt produces each story. impl-001 covers backend, impl-002 covers frontend (`web-ui-engineer`), impl-003 covers integration, impl-005 is the security scan, and qa-001/qa-002 are tests.

## 5. Epic e0: Backend seams (`e0-backend-seams`)

### 5.1 Milestones

| # | Milestone | Kickoff | Prompts (internal adaptation) | agent_hint | Required artifacts | Exit criteria (checkable) | Gate |
|---|---|---|---|---|---|---|---|
| 1 | intake-discovery | skipped | — | — | — | Uses the default track by reference | — |
| 2 | execution-planning | skipped | — | — | — | Uses the default track by reference | — |
| 3 | design-blueprint | skipped | — (design §7.1-§7.9 is the spec) | — | — | Uses the default design-blueprint by reference | Review gate (default track) |
| 4 | build-planning | not_started | build-001; build-002; **build-003 produces the REPL golden-transcript fixture**; build-004 (local `uv sync` plus a `pytest` baseline); build-005 | research, documentation, engineer, local-ops | `docs/build-plan-e0-backend-seams.md`, `tests/fixtures/golden/**`, `tests/test_interactive_golden.py` | The golden test passes on unmodified `main`; the baseline test count is recorded | Artifact check, artifact-critic |
| 5 | implementation | not_started | impl-000 (kickoff questions, in `awaiting-input.yaml`); impl-001; impl-003; impl-004; impl-005. impl-002 does not apply (no frontend). | engineer, security | `src/open_maestro/turns/**`, `src/open_maestro/bootstrap.py`, `tests/test_turn_service.py`, `docs/security/scan-report-*.md` | The golden transcript is **unchanged**; REPL turns write `.open-maestro/runs/<id>.yaml`; `--stream` shows claude-cli `tool.call` events; `prompt_toolkit` is declared and an `[mcp]` extra exists; `runtime/scripted.py` replays deterministically with no network | Code-critic, CI |
| 6 | qa-integration | not_started | qa-001; qa-002 is replaced by the golden plus stream-fixture tests; qa-003 (manual REPL checklist, a second terminal watching `run.lock`); qa-004 | qa | `docs/qa/e0-backend-seams-test-report.md`, `docs/qa/e0-backend-seams-uat-playbook.md` | All tests green; after the suite, `git status` is clean and `.open-maestro/sessions/` gains no new files | Artifact check |
| 7 | demo-delivery | not_started | demo-001 (REPL run plus `--stream` tool log plus run record); demo-002; release-001 (3.9.0); story-sync-001 | documentation, qa, local-ops | `docs/demo/e0-backend-seams-demo-script.md`, `CHANGELOG.md` | Tag `v3.9.0` exists; MAE-110..116, 138 and 139 are Done in the CSV, and the Phase 0 parts of MAE-130 (scripted runtime) and MAE-131 (dependency declaration) are merged (both stories stay open until e1); the Jira dry-run is clean | Artifact check |
| 8 | retrospective-findings | not_started | retro-001..003 | documentation | `docs/findings/e0-backend-seams-lessons-learned.md` | Lessons recorded; refactor surprises fed back to e1 impl-000 | — |

**e0 exit criterion: REPL behaviour unchanged.** build-003 produces the proof, a characterization transcript (new in e0; `tests/test_interactive_golden.py` does not exist today) run against a deterministic runtime that includes prompt-history and milestone side effects (design §12). Commit that transcript **before** any extraction. Every e0 PR must leave it byte-identical.

### 5.2 Story breakdown

| Story | Summary | Sequencing | Branch | Primary files | Verification (design §12) | Prompt |
|---|---|---|---|---|---|---|
| MAE-138 | Test-session isolation; dashboard.html ignored | **First**; it blocks the golden fixture | `feat/MAE-138-test-session-isolation` | `tests/conftest.py`, `.gitignore` | Suite run → no new `.open-maestro/sessions/*`; `git check-ignore dashboard.html` | impl-001 |
| MAE-139 | `add_epic` mutation plus MCP tool | Independent; early, so the epics need no hand edits | `feat/MAE-139-add-epic` | `milestones/mutations.py`, `mcp/server.py`, `tests/test_milestones.py`, `tests/test_mcp_server.py` | N+1 epics with 8 milestones; DriftError honoured; MCP round-trip | impl-001 |
| MAE-131 (deps part) | Declare `prompt_toolkit` in core deps; add the `[mcp]` extra | **Early** (design D-7); after 138 | `feat/MAE-131-declare-deps` | `pyproject.toml` | `uv sync` clean; `import prompt_toolkit, mcp` works from a fresh venv with `[mcp]` | impl-001 |
| MAE-130 (runtime part) | Scripted runtime `runtime/scripted.py`, registered in `runtime/factory.py` | **Early** (design D-7); after 138, before the golden fixture | `feat/MAE-130-scripted-runtime` | `runtime/scripted.py`, `runtime/factory.py`, `tests/test_scripted_runtime.py` | No network; deterministic replay of a script fixture | impl-001 |
| (fixture) | Golden transcript | After 138 and the two early parts above, before 110 | `feat/MAE-110-golden-transcript` | `tests/test_interactive_golden.py`, `tests/fixtures/golden/` | Green on main | build-003 |
| MAE-115 | Side-effect-free milestone reads | Independent; it must land before MAE-120/122 | `feat/MAE-115-load-create-if-missing` | `milestones/store.py` | `load(create_if_missing=False)` writes nothing; 404 path tested | impl-001 |
| MAE-112 | Stream → EventBus bridge | Independent (parallel) | `feat/MAE-112-stream-bridge` | `runtime/claude_cli.py`, `runtime/stream_printer.py` (`parse_claude_stream_line`, kimi parser) | Fixture tests: text, tool_use, tool_result, permission denials | impl-003 |
| MAE-110 | TurnService | After the golden fixture; it blocks 111/113/114/116 | `feat/MAE-110-turn-service` | `turns/`, `bootstrap.py`, `interactive.py:2412-2943`; `TurnRequest.permission_mode` (new per-turn field; the CLI flag already reaches claude-cli via `pm.py:1087-1088` → `claude_cli.py:222-223`) | `tests/test_turn_service.py`: hint routing; awaiting batch returned, not asked; stale marker ignored; `permission_mode` reaches `AgentConfig`; golden unchanged | impl-001 |
| MAE-111 | OperatorIO / TerminalIO | After 110 | `feat/MAE-111-operator-io` | `turns/io.py`, `interactive.py` prompt helpers | Escalation through a stub IO; `OperatorCancelled` handled as Esc | impl-001 |
| MAE-113 | Run records | After 110 | `feat/MAE-113-run-records` | `turns/` records, `.open-maestro/runs/`, prune(200) | A run record is written; cancel → `cancelled`; pruning tested | impl-001 |
| MAE-114 | run_id contextvar tagging | After 110 | `feat/MAE-114-run-id-tagging` | `turns/`, `events/bus.py` | Chain and swarm events carry `run_id` | impl-003 |
| MAE-116 | Cross-surface run lock | After 110 and 113 | `feat/MAE-116-run-lock` | `.open-maestro/run.lock` handling in `turns/` | A dead-pid lock is reclaimed; a second surface is refused | impl-003 |

After the merges, run impl-005 (scan) and qa-001 (branch coverage from the traceability matrix).

## 6. Epic e1: Console MVP (`e1-console-mvp`)

### 6.1 Milestones

| # | Milestone | Kickoff | Prompts (internal adaptation) | agent_hint | Required artifacts | Exit criteria (checkable) | Gate |
|---|---|---|---|---|---|---|---|
| 1 | intake-discovery | skipped | — | — | — | By reference | — |
| 2 | execution-planning | skipped | — | — | — | By reference | — |
| 3 | design-blueprint | skipped | — (design §6-§10, Appendices A-C) | — | — | By reference | Review gate (default track) |
| 4 | build-planning | not_started | build-001; build-002; build-003 (`ui/demo/fixture-project/`, `demo/script.yaml`); build-004 (Node and Vite toolchain smoke, `scripts/build_ui.sh`); build-005 | research, documentation, engineer, local-ops | `docs/build-plan-e1-console-mvp.md`, `tests/fixtures/**` | The bundles cover MAE-117..131; a Node-free install path is confirmed | Artifact check, artifact-critic |
| 5 | implementation | not_started | **impl-000 visual-fidelity reconciliation** (below); impl-001 (FastAPI routes); impl-002 (SPA, `web-ui-engineer`); impl-003 (SSE, WebIO, RunManager); impl-004; impl-005 (auth surface) | engineer, web-ui-engineer, security | `src/open_maestro/ui/**`, `ui/src/**`, `tests/test_ui_api.py`, `docs/security/scan-report-*.md` | An operator completes intake → blueprint with no terminal; without `[ui]`, `maestro --ui` prints an install hint and exits 1 | Code-critic, CI, security |
| 6 | qa-integration | not_started | qa-001 (`tests/test_ui_api.py`, SSE); qa-002 (Playwright `ui/e2e/` against `maestro --ui --demo --no-browser --ui-port 0`); qa-003; qa-004 | qa | `docs/qa/e1-console-mvp-test-report.md`, `docs/qa/e1-console-mvp-uat-playbook.md`, `ui/e2e/**` | The Playwright smoke passes demo steps 1-7 and 10; SPA under 3 MB in the wheel; the wheel installs with and without `[ui]` | **Playwright**, artifact check |
| 7 | demo-delivery | not_started | demo-001 and demo-002 = the **10-step walkthrough (design §9)**; release-001 (4.0.0); story-sync-001 | documentation, qa, local-ops | `docs/demo/e1-console-mvp-demo-script.md`, `CHANGELOG.md` | Dry-run notes record steps 1-7 and 10 live, with 8-9 shown as "Phase 2"; tag `v4.0.0` | Artifact check |
| 8 | retrospective-findings | not_started | retro-001..003 | documentation | `docs/findings/e1-console-mvp-lessons-learned.md` | SSE and permission-denial findings recorded | — |

**impl-000 visual-fidelity reconciliation.** For each screen in design §6, the kickoff lists every difference between the design text and the Merven prototype's Operator surface. Each difference gets a ruling or becomes a CR. The components to reconcile are the design-system set in `ui/src/ds/`: Button, CodeChip, MonoText, CountBadge, Icon, StatusBadge, DataTable, KpiCard/KpiRow, PageHeader, NavItem/Sidebar (200 px), MaskedCredential and TrustNote. They are followed by the console set: LogStream, InteractionCard, MilestoneColumn, PromptCard and LaunchConfirm. The kickoff also checks the tokens `--forest #1a3c34`, `--paper #f7f6f3` and `--teal #1fb5a5`, plus Geist and Geist Mono with their licence. It confirms the §5.1 drop list (no Gantt, no confidence score, no Submit PR). The prototype lives in the untracked `docs/requirements/` of the primary checkout, so pass its absolute path to the agent; a worktree does not contain it.

**Demo content (demo-001 and demo-002).** Script the design §9 walkthrough:

1. Projects picker
2. Dashboard KPIs
3. Milestone board
4. Launch confirm sheet (the "left-hand side" approval)
5. Live log
6. Inbox badge
7. Rule and defer → follow-up in the same session
8. Critic WARN (e2)
9. Artifact diff (e2)
10. Complete a milestone → Dashboard % moves; mention `supabase_publisher.py`

### 6.2 Story breakdown

| Story | Summary | Sequencing | Branch | Primary files | Verification | Prompt |
|---|---|---|---|---|---|---|
| MAE-117 | `maestro --ui` launcher | **Before any screen story** | `feat/MAE-117-ui-launcher` | `cli.py` (`--ui*`, `--no-browser`, `--demo`), `ui/app.py` | Free-port allocation; non-loopback host refused | impl-001 |
| MAE-118 | Loopback token auth | **Before any screen story**; security review | `feat/MAE-118-loopback-auth` | `ui/auth.py` | 401/403; foreign Host and bad Origin rejected; `config.yaml` never served | impl-001, impl-005 |
| MAE-130 (demo part) | Demo mode: fixture project, `--demo`, banner (the scripted runtime landed in e0) | Before Playwright work; **MAE-125 tests and all Playwright use the e0 runtime** | `feat/MAE-130-demo-mode` | `ui/demo/`, `cli.py` (`--demo`) | No network; seeded fixture copied to a temp dir | impl-001 |
| MAE-131 (packaging part) | Wheel packaging (`[ui]` extra; deps were declared in e0) | Skeleton early; size check last | `feat/MAE-131-ui-packaging` | `pyproject.toml`, `scripts/build_ui.sh`, hatch `force-include` | Install with and without `[ui]`; SPA < 3 MB; instructive page when assets are missing | impl-003 |
| MAE-119 | Project registry and picker | After 117/118 | `feat/MAE-119-project-registry` | `ui/registry.py`, `~/.open-maestro/projects.yaml` | Upsert; `ui-servers.yaml` reuse | impl-001, impl-002 |
| MAE-120 | Initialize from the UI | After MAE-115 | `feat/MAE-120-ui-init` | `routes/project`, explicit `MilestoneStore.load(create_if_missing=True)` (not `--discover-milestones`, which only prints suggestions) | 404 `not_initialized` → init | impl-001, impl-002 |
| MAE-121 | Dashboard KPIs | After 117/118 | `feat/MAE-121-dashboard` | `routes/`, `ui/src/` Dashboard | Vitest; KPIs match `MilestonePlan.summary` | impl-002 |
| MAE-122 | Milestone board | After MAE-115 | `feat/MAE-122-milestone-board` | `routes/milestones`, MilestoneColumn | PATCH drift → 409/DriftError | impl-001, impl-002 |
| MAE-123 | Launch with inputs | After MAE-110 | `feat/MAE-123-launch` | `routes/prompts`, `routes/runs`, LaunchConfirm | Remembered inputs; 409 single-flight; atomic write + re-read of `prompt_inputs.yaml`/`prompt_history.yaml` (design R-2) | impl-001, impl-002 |
| MAE-124 | Free-form Ask | After 123 | `feat/MAE-124-ask` | Ask screen | Toggles reach `TurnRequest` | impl-002 |
| MAE-125 | Live run log over SSE | After 112/114 (the 130 runtime is already in e0) | `feat/MAE-125-sse-log` | `ui/sse.py`, `useEventStream`, LogStream | Ordering; `Last-Event-ID` replay; redaction | impl-003 |
| MAE-126 | Cancel | After 116 and 125 | `feat/MAE-126-cancel` | `ui/runs.py` RunManager | Cancel → `cancelled` record | impl-003 |
| MAE-127 | Inbox rulings | After 123 | `feat/MAE-127-inbox` | `routes/awaiting-input`, InteractionCard | POST clears the marker and queues a follow-up | impl-001, impl-002 |
| MAE-128 | Mid-turn interactions | After MAE-111 | `feat/MAE-128-web-io` | `ui/interactions.py` (WebIO) | Interaction resolve; 30-minute timeout | impl-003 |
| MAE-129 | Runs list and detail | After MAE-113 | `feat/MAE-129-runs` | `routes/runs`, Run detail | Permission denials shown | impl-002 |

## 7. Epic e2: Gates and review (`e2-gates-review`)

### 7.1 Milestones

| # | Milestone | Kickoff | Prompts | agent_hint | Required artifacts | Exit criteria (checkable) | Gate |
|---|---|---|---|---|---|---|---|
| 1-2 | intake / execution-planning | skipped | — | — | — | By reference | — |
| 3 | design-blueprint | not_started | design-001-style addendum, run as a free-form Ask (design-* prompts are default-track only) | research | `docs/spec-critic-blocking.md` | Blocking-critic semantics ratified; flag off = chain unchanged | **Review gate** (`*spec*`) |
| 4 | build-planning | not_started | build-001, build-002, build-003 (critic BLOCK fixtures), build-005 | research, engineer | `docs/build-plan-e2-gates-review.md` | Bundles cover MAE-132..135 | Artifact check |
| 5 | implementation | not_started | impl-000, impl-001, impl-002, impl-003, impl-004, impl-005 | engineer, web-ui-engineer, security | `src/open_maestro/ui/routes/**`, `tests/test_chain.py` | A BLOCK pauses the chain through `confirm("critic.gate")` under `--critic-blocking` | Code-critic, CI |
| 6 | qa-integration | not_started | qa-001, qa-002 (Playwright steps 8-9), qa-003, qa-004 | qa | `docs/qa/e2-gates-review-test-report.md`, `docs/qa/e2-gates-review-uat-playbook.md` | Demo steps 8-9 are real in Playwright; the golden transcript is still unchanged | Playwright |
| 7 | demo-delivery | not_started | demo-001, demo-002 (full 10 steps), release-001 (4.1.0), story-sync-001 | documentation, qa, local-ops | `docs/demo/e2-gates-review-demo-script.md` | Tag `v4.1.0`; MAE-132..135 Done | Artifact check |
| 8 | retrospective-findings | not_started | retro-001..003 | documentation | `docs/findings/e2-gates-review-lessons-learned.md` | — | — |

### 7.2 Story breakdown

| Story | Summary | Sequencing | Branch | Primary files | Verification | Prompt |
|---|---|---|---|---|---|---|
| MAE-132 | Critic review panel plus optional blocking | After the spec review passes; needs MAE-111 | `feat/MAE-132-critic-blocking` | `orchestrator/chain.py:700-722`, `turns/io.py`, Gates screen | BLOCK pauses the chain; default mode advisory | impl-001, impl-002 |
| MAE-133 | Persona reviews plus GateLedger | Independent | `feat/MAE-133-gate-ledger-ui` | `review/gate.py`, `reviews/<doc>-<sha>/` | Ledger state matches `maestro --review` | impl-001, impl-002 |
| MAE-134 | Artifact diff | After MAE-122 | `feat/MAE-134-artifact-diff` | `git diff` over the milestone artifact globs | View-only; no remote writes | impl-001, impl-002 |
| MAE-135 | Blockers, todos and memory notes | After MAE-122 | `feat/MAE-135-housekeeping` | `todos/store.py`, Basic Memory | `bm` latency never blocks `run.completed` | impl-001, impl-002 |

## 8. Epic e3: Admin (`e3-admin`)

### 8.1 Milestones

| # | Milestone | Kickoff | Prompts | agent_hint | Required artifacts | Exit criteria (checkable) | Gate |
|---|---|---|---|---|---|---|---|
| 1-2 | intake / execution-planning | skipped | — | — | — | By reference | — |
| 3 | design-blueprint | not_started | Addendum (free-form Ask) | research | `docs/spec-admin-yaml-writes.md` | Safe YAML write rules (atomic, drift-checked, no secrets) ratified | **Review gate** |
| 4 | build-planning | not_started | build-001, build-002, build-005 | research | `docs/build-plan-e3-admin.md` | MAE-136 split into PRs | Artifact check |
| 5 | implementation | not_started | impl-000..005 | engineer, web-ui-engineer, security | `src/open_maestro/ui/routes/**` | The operator changes the default model; per-milestone spend is visible | Code-critic, CI |
| 6 | qa-integration | not_started | qa-001..004 | qa | `docs/qa/e3-admin-test-report.md`, `docs/qa/e3-admin-uat-playbook.md` | The pin is honoured by the next run (test); no secret is served | Playwright |
| 7 | demo-delivery | not_started | demo-001, demo-002, release-001 (4.2.0), story-sync-001 | documentation, qa, local-ops | `docs/demo/e3-admin-demo-script.md` | Tag `v4.2.0`; MAE-136 Done | Artifact check |
| 8 | retrospective-findings | not_started | retro-001..003 (includes the hub-mode investigation) | documentation | `docs/findings/e3-admin-lessons-learned.md` | Hub-mode recommendation recorded | — |

### 8.2 Story breakdown

| Story | Summary | Sequencing | Branch | Primary files | Verification | Prompt |
|---|---|---|---|---|---|---|
| MAE-136 (a) | Models, capabilities, latency, quota, pins | After the spec review | `feat/MAE-136-models-admin` | `config/models.yaml`, `capabilities.yaml`, `runtime/latency.py`, `runtime/quota.py` | The pinned model is used by the next run | impl-001, impl-002 |
| MAE-136 (b) | Agents, sources, skills, MCP view | After (a) | `feat/MAE-136-agents-sources` | agents registry, `sources.yaml`, `mcp.json` | Read-only views; source sync | impl-002 |
| MAE-136 (c) | Usage rollups | After MAE-113 | `feat/MAE-136-usage` | run records → per-milestone cost | Sums match the run records | impl-001, impl-002 |

## 9. Release and story-sync procedure

Each epic closes demo-delivery with `release-001` and then `story-sync-001`. The proposed versions are **e0 → 3.9.0**, **e1 → 4.0.0** (new `[ui]` extra and `--ui` flag), **e2 → 4.1.0** and **e3 → 4.2.0**. These are proposals; the maintainer confirms each version at release-001.

1. Bring main up to date with every story PR of the epic squash-merged. Run `uv run ruff check` (line-length 100, E/F/I/W/N/UP) and `uv run pytest` (asyncio auto).
2. Bump `version` in `pyproject.toml`. That is the only place it lives.
3. Add a CHANGELOG entry in Keep a Changelog format, with Jira ids:

   ```markdown
   ## [3.9.0] - 2026-12-xx

   ### Added
   - **REPL turns now run through a reusable TurnService.** ... (MAE-110, MAE-111)
   - **Every turn writes a run record** under `.open-maestro/runs/` ... (MAE-113)

   ### Fixed
   - **Tests no longer leak sessions into the repo.** ... (MAE-138)
   ```

4. Make one release commit (`release: v3.9.0 — Operator Console backend seams`), then tag it with an annotated tag: `git tag -a v3.9.0 -m "v3.9.0 — Operator Console backend seams"`. The tag message follows the existing `vX.Y.Z — headline` style.
5. After the push, run story-sync: set the epic's MAE rows to `Done` in the CSV (CRLF; never rename a Summary), run `./scripts/sync_jira.sh --dry-run`, review every transition, and only then run `--create-missing`. Commit the CSV.

## 10. Operating the plan with Maestro

**Install the playbook.** Run `cp docs/playbooks/internal-engineering.yaml .open-maestro/playbook.yaml`. An override replaces the bundled playbook entirely (`load_playbook()`), which is why the fork is a complete file. The fork removes design-004, design-006 and demo-003, rewords the client/M3 wording, and adds release-001 and story-sync-001. In impl-000, a `[maintainers]` audience does not get the hard-coded M3 marker (`awaiting_input.py:163`).

**Per-milestone loop.**

| Command | Use |
|---|---|
| `/next` | Offers the current milestone's prompts, with `after:` prerequisites honoured (for example, story-sync-001 after release-001) |
| `/select` | Edit a prompt before it runs; inputs are remembered in `prompt_inputs.yaml` |
| `/track <epic>/<ms> <status>` | Move a milestone to `in_progress`, or reactivate a skipped one |
| `/blocker <epic>/<ms> <reason>` | Record a dependency (for example, `e1-console-mvp/implementation "waiting on MAE-110"`) |
| `/complete <epic>/<ms>` | Artifact check, then review gate; `--force` only with a written reason in `notes` |
| `maestro --review <doc>` | Records persona verdicts in `.open-maestro/gates/` |

**Delegation pattern.** The PM session runs the prompt, and the engineer agent works in its own git worktree on the story branch. Code-critic advice from turns over 50 lines is triaged before the PR. QA attaches evidence (test counts, Playwright traces, `git status` after the suite) to `docs/qa/<epic>-test-report.md`. Kickoff questions are answered through the awaiting-input Inbox in the REPL, and in the console once e1 ships.

## 11. Risks and sequencing notes

- **Phase 0 refactor regression.** `run_interactive()` is about 545 lines inside a 2.9k-line REPL. Mitigation: commit the golden transcript first, extract one seam per PR, and never update the golden file in the same PR as an extraction.
- **Maestro running on its own repo.** `maestro` on PATH resolves to `~/.local/bin/maestro` → `~/.open-maestro/venv` (the installed wheel, `installpackage/install-maestro.sh`). Editing `src/` does not change the running operator until you reinstall. Keep the operator on the last tagged release, and exercise source changes with `uv run maestro` inside the worktree. Reinstall only after a release tag.
- **Review-gate filename mismatch.** The design doc does not match `docs/blueprint*.md`, `*contract*` or `*spec*`. As a result, `/complete` neither finds the default design-blueprint required artifacts nor enforces its ledger entry. Mitigation: the Appendix plan points design-blueprint's artifact at the design doc, and a passing `maestro --review` is a manual exit criterion. The e2 and e3 addenda use `spec-` names so the gate enforces itself.
- **The session leak must be fixed before the e0 golden tests.** Otherwise fixture runs pollute `.open-maestro/sessions/` and the golden side-effect assertions go non-deterministic. MAE-138 goes first.
- **Untracked requirements.** `docs/requirements/` is not in git, so agents in worktrees need an absolute path to the prototype.
- **Scripted runtime and dependency placement (resolved, design v0.2 D-7).** The scripted runtime (runtime part of MAE-130) and the dependency declaration (deps part of MAE-131) land first in e0, before the golden fixture. The demo UI and `[ui]` packaging parts stay in e1. The CSV rows are not split; the split is by PR as listed in §5.2 and §6.2.
- **Permission mode.** `--permission-mode` already reaches the claude-cli subprocess unmapped (`claude_cli.py:222-223`); only `auto` appears in the installed `claude --help` choices. MAE-110 must map or validate the value per runtime (design OQ-3), and v1 defaults non-read-only prompts to `auto` and the Launch sheet shows the mode read-only, with no picker (design D-6). Remote binding is not a v1 option (design D-2): MAE-117 refuses non-loopback hosts with no override.
- **Stale local plan.** The primary checkout holds a 2026-10-01 default-only `milestones.yaml` with every milestone `not_started`. Back it up before adopting the Appendix.

## 12. Story index (MAE-110 … MAE-139)

| Epic | Stories |
|---|---|
| e0-backend-seams | MAE-138 test-session isolation, MAE-139 add_epic, MAE-131 deps declaration (part), MAE-130 scripted runtime (part), MAE-110 TurnService, MAE-111 OperatorIO, MAE-112 stream bridge, MAE-113 run records, MAE-114 run-id tagging, MAE-115 side-effect-free reads, MAE-116 run lock |
| e1-console-mvp | MAE-117 launcher, MAE-118 auth, MAE-119 registry, MAE-120 init, MAE-121 dashboard, MAE-122 milestone board, MAE-123 launch, MAE-124 ask, MAE-125 SSE log, MAE-126 cancel, MAE-127 inbox, MAE-128 mid-turn interactions, MAE-129 runs, MAE-130 demo mode (rest), MAE-131 packaging (rest) |
| e2-gates-review | MAE-132 critic blocking, MAE-133 persona reviews and ledger, MAE-134 artifact diff, MAE-135 blockers, todos and notes |
| e3-admin | MAE-136 models, agents, sources and usage admin |
| Out of plan | MAE-137 client portal and workbench (Phase 4) |

---

## Appendix. Opt-in local `milestones.yaml`

`.open-maestro/` is gitignored, so this file is never committed. Paste it into `.open-maestro/milestones.yaml` to let Maestro track the plan locally; MAE-139's `add_epic` replaces this hand edit later. The plan is valid per `models.py`: `schema_version: '2.0'`, unique orders and ids matching `^[a-z0-9_-]+$`. Artifacts default to `required: true`; milestone status defaults to `not_started`.

```yaml
project_id: open-maestro
project_name: Open Maestro
schema_version: '2.0'
epics:
- id: default
  name: Programme
  order: 1
  status: in_progress
  milestones:
  - {id: intake-discovery, name: Intake & Discovery, order: 1, weight: 10, status: completed,
     artifacts: [{path: docs/ui-operator-console-design-2026-11-26.md}]}
  - {id: execution-planning, name: Execution Planning, order: 2, weight: 10, status: completed,
     artifacts: [{path: docs/ui-operator-console-design-2026-11-26.md}]}
  - {id: design-blueprint, name: Design Blueprint, order: 3, weight: 15, status: in_progress,
     artifacts: [{path: docs/ui-operator-console-design-2026-11-26.md}],
     exit_criteria: ["maestro --review on the design doc passes for its current sha"]}
  - {id: build-planning, name: Build Planning, order: 4, weight: 10, status: in_progress,
     artifacts: [{path: docs/build-plan-operator-console-2026-11-27.md}]}
  - {id: implementation, name: Implementation, order: 5, weight: 30, status: skipped}
  - {id: qa-integration, name: QA & Integration, order: 6, weight: 15, status: skipped}
  - {id: demo-delivery, name: Demo & Delivery, order: 7, weight: 5, status: skipped}
  - {id: retrospective-findings, name: Retrospective & Findings, order: 8, weight: 5,
     artifacts: [{path: docs/findings/default-lessons-learned.md, required: false}]}
- id: e0-backend-seams
  name: Backend seams
  order: 2
  milestones:
  - {id: intake-discovery, name: Intake & Discovery, order: 1, weight: 10, status: skipped}
  - {id: execution-planning, name: Execution Planning, order: 2, weight: 10, status: skipped}
  - {id: design-blueprint, name: Design Blueprint, order: 3, weight: 15, status: skipped}
  - {id: build-planning, name: Build Planning, order: 4, weight: 10,
     artifacts: [{path: docs/build-plan-e0-backend-seams.md},
                 {path: tests/test_interactive_golden.py}],
     exit_criteria: [Golden transcript green on unmodified main]}
  - {id: implementation, name: Implementation, order: 5, weight: 30,
     artifacts: [{path: src/open_maestro/turns/**}, {path: tests/test_turn_service.py},
                 {path: docs/security/scan-report-*.md}],
     exit_criteria: [Golden transcript unchanged, REPL turns write run records, scripted runtime replays offline,
                     claude-cli tool events visible via --stream]}
  - {id: qa-integration, name: QA & Integration, order: 6, weight: 15,
     artifacts: [{path: docs/qa/e0-backend-seams-test-report.md}],
     exit_criteria: [All tests green, working tree clean after the suite]}
  - {id: demo-delivery, name: Demo & Delivery, order: 7, weight: 5,
     artifacts: [{path: docs/demo/e0-backend-seams-demo-script.md}],
     exit_criteria: [v3.9.0 tagged, e0 stories Done and synced]}
  - {id: retrospective-findings, name: Retrospective & Findings, order: 8, weight: 5,
     artifacts: [{path: docs/findings/e0-backend-seams-lessons-learned.md, required: false}]}
- id: e1-console-mvp
  name: Console MVP
  order: 3
  milestones:
  - {id: intake-discovery, name: Intake & Discovery, order: 1, weight: 10, status: skipped}
  - {id: execution-planning, name: Execution Planning, order: 2, weight: 10, status: skipped}
  - {id: design-blueprint, name: Design Blueprint, order: 3, weight: 15, status: skipped}
  - {id: build-planning, name: Build Planning, order: 4, weight: 10,
     artifacts: [{path: docs/build-plan-e1-console-mvp.md}]}
  - {id: implementation, name: Implementation, order: 5, weight: 30,
     artifacts: [{path: src/open_maestro/ui/**}, {path: tests/test_ui_api.py}],
     exit_criteria: [Operator completes intake to blueprint without a terminal]}
  - {id: qa-integration, name: QA & Integration, order: 6, weight: 15,
     artifacts: [{path: docs/qa/e1-console-mvp-test-report.md}, {path: ui/e2e/**}],
     exit_criteria: [Playwright demo steps 1-7 and 10 pass, SPA under 3 MB in wheel]}
  - {id: demo-delivery, name: Demo & Delivery, order: 7, weight: 5,
     artifacts: [{path: docs/demo/e1-console-mvp-demo-script.md}],
     exit_criteria: [v4.0.0 tagged, e1 stories Done and synced]}
  - {id: retrospective-findings, name: Retrospective & Findings, order: 8, weight: 5}
- id: e2-gates-review
  name: Gates and review
  order: 4
  milestones:
  - {id: intake-discovery, name: Intake & Discovery, order: 1, weight: 10, status: skipped}
  - {id: execution-planning, name: Execution Planning, order: 2, weight: 10, status: skipped}
  - {id: design-blueprint, name: Design Blueprint, order: 3, weight: 15,
     artifacts: [{path: docs/spec-critic-blocking.md}]}
  - {id: build-planning, name: Build Planning, order: 4, weight: 10,
     artifacts: [{path: docs/build-plan-e2-gates-review.md}]}
  - {id: implementation, name: Implementation, order: 5, weight: 30,
     exit_criteria: [BLOCK pauses a chain under --critic-blocking]}
  - {id: qa-integration, name: QA & Integration, order: 6, weight: 15,
     artifacts: [{path: docs/qa/e2-gates-review-test-report.md}],
     exit_criteria: [Demo steps 8-9 real in Playwright, golden transcript unchanged]}
  - {id: demo-delivery, name: Demo & Delivery, order: 7, weight: 5,
     artifacts: [{path: docs/demo/e2-gates-review-demo-script.md}],
     exit_criteria: [v4.1.0 tagged]}
  - {id: retrospective-findings, name: Retrospective & Findings, order: 8, weight: 5}
- id: e3-admin
  name: Admin
  order: 5
  milestones:
  - {id: intake-discovery, name: Intake & Discovery, order: 1, weight: 10, status: skipped}
  - {id: execution-planning, name: Execution Planning, order: 2, weight: 10, status: skipped}
  - {id: design-blueprint, name: Design Blueprint, order: 3, weight: 15,
     artifacts: [{path: docs/spec-admin-yaml-writes.md}]}
  - {id: build-planning, name: Build Planning, order: 4, weight: 10,
     artifacts: [{path: docs/build-plan-e3-admin.md}]}
  - {id: implementation, name: Implementation, order: 5, weight: 30,
     exit_criteria: [Operator changes the default model, per-milestone spend visible]}
  - {id: qa-integration, name: QA & Integration, order: 6, weight: 15,
     artifacts: [{path: docs/qa/e3-admin-test-report.md}]}
  - {id: demo-delivery, name: Demo & Delivery, order: 7, weight: 5,
     artifacts: [{path: docs/demo/e3-admin-demo-script.md}],
     exit_criteria: [v4.2.0 tagged]}
  - {id: retrospective-findings, name: Retrospective & Findings, order: 8, weight: 5}
```
