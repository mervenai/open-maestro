# Changelog

All notable changes to Open Maestro are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.9.0] - 2026-08-31

### Changed
- Work-epic status in the dashboard is now derived from the **highest active
  milestone order** instead of overall completion:
  - Milestones 1–4 (Intake through Build Planning) → **Not Started**
  - Milestones 5–7 (Implementation through Demo & Delivery) → **In Progress**
  - Milestone 8 (Retrospective & Findings) → **Complete**
- The Project Process track still displays all 8 lifecycle milestones; only the
  per-epic status tracker uses the 3-level rollup.

## [1.8.9] - 2026-08-31

### Added
- Auto-populate work epics when **Intake & Discovery** is marked complete.
  - Triggered automatically by `/complete`, `/track`, and after a playbook prompt
    advances a milestone.
  - Parses `docs/intake/epics.md` (table or heading style) to extract epic
    names and numbers.
  - Creates each work epic with the standard 8 lifecycle milestones, all
    `not_started`.
  - Writes a local `dashboard.html` immediately after the epics are created.

### Changed
- `MilestoneStore.update()` now runs the auto-population hook before recomputing
  the plan summary and saving.

## [1.8.8] - 2026-08-30

### Changed
- Dashboard layout simplified per client feedback:
  - Removed the separate "Project Process" global section.
  - The first epic (formerly "Default Track") is now rendered as **Project Process**
    and displays all 8 lifecycle milestones in two rows.
  - Work epics below Project Process display only a 3-step status tracker
    (Not Started → In Progress → Complete) without repeating the 8 milestones.
- HTML and Markdown exporters updated to consume the new `process_track` + `epics`
  data shape.

### Removed
- Deprecated dashboard helpers `_derive_global_milestones`,
  `_epic_process_milestones`, `_global_section`, `_status_sentence`, and
  `_PROCESS_MILESTONE_NAMES` are no longer used.

## [1.8.7] - 2026-08-29

### Changed
- Dashboard rendering now separates **project-wide process milestones** (orders 1,
  2, and 8: Intake & Discovery, Execution Planning, Retrospective & Findings)
  from **per-epic process milestones** (orders 3-7).
- Each epic displays a 3-step status tracker (Not Started → In Progress →
  Complete) derived from its process milestones.
- Dashboard exporters (HTML, JSON, Markdown) use milestone **order** instead of
  hardcoded IDs, making them resilient to legacy or renamed milestone schemas.

### Migrated
- `M3PMS2Daily/.open-maestro/milestones.yaml`: renamed legacy milestone IDs
  (`p1`, `p3`, `p4`, etc.) in non-default epics to the standard 8 process
  milestone IDs. A backup was saved before the change.

## [1.8.6] - 2026-08-29

### Fixed
- `kimi-cli` resume now uses the full `session_<uuid>` form Kimi expects.
- After the first Kimi resume failure in a process, Maestro stops trying to
  resume and silently starts fresh sessions. This removes the repeated warning
  spam while preserving the conversation history that Maestro already injects
  into each prompt.

## [1.8.5] - 2026-08-29

### Fixed
- `kimi-cli` runtime now falls back to a fresh session when resuming fails with
  "Session ... not found" instead of erroring out the turn. This makes
  long-running interactive sessions resilient to Kimi session expiration or
  stale session IDs.

## [1.8.4] - 2026-08-29

### Fixed
- `/next` now backfills prompt run history from existing artifact files. Prompts
  whose output files already exist (e.g. `docs/execution-plan-*.md`) show a
  `[Ran]` indicator even if they were executed before run-history recording was
  added or the record was lost.
- Recording failures for playbook prompt runs are now logged at warning level
  instead of silently swallowed at debug level.

## [1.8.3] - 2026-08-29

### Added
- Canned `/next` playbook prompts now auto-advance their milestone from
  `not_started` to `in_progress` when run, and print the milestone(s) updated.
- The interactive banner now shows the last dashboard publish timestamp and URL
  (read from `.open-maestro/dashboard_publish_history.yaml`).

## [1.8.2] - 2026-08-29

### Changed
- Removed the "Still working... (Ns elapsed)" heartbeat lines from the
  interactive progress UI. The spinner still indicates active work; the event is
  still available to `--monitor`.

## [1.8.1] - 2026-08-29

### Changed
- `maestro --export-dashboard` now prints a confirmation to stderr so users who
  redirect stdout to a file (e.g. `> dashboard.html`) see feedback in the
  terminal.

## [1.8.0] - 2026-08-29

### Added
- Standalone dashboard receiver (`maestro --serve-remote-dashboard`) that
  accepts published dashboard snapshots and serves HTML/JSON/Markdown without
  depending on the Merven project.
  - Snapshots are stored as JSON files in `--dashboard-data-dir` (default
    `.open-maestro/dashboards`), keyed by project token.
  - Supports the same publish payload and `Authorization: Bearer <key>` auth as
    the legacy Merven receiver.
  - View endpoints:
    - `GET /maestro/dashboard/<token>` — JSON
    - `GET /maestro/dashboard/<token>/html` — HTML
    - `GET /maestro/dashboard/<token>/md` — Markdown

## [1.7.0] - 2026-08-29

### Added
- Project todo list (`/todo` interactive commands).
  - `todos.json` stored under `.open-maestro/` in the project directory.
  - Commands: `/todo add`, `/todo list`, `/todo done`, `/todo block`,
    `/todo delete`, `/todo clear`.
  - Open todos are automatically injected into each agent's system prompt so
    specialists know what work is already in flight.

## [1.6.7] - 2026-08-29

### Fixed
- Cross-runtime session resume: interactive mode now tracks which runtime
  created the current session and only resumes when the selected runtime
  matches, preventing Kimi from trying to resume a Claude session.
- Claude CLI blocked-tool filtering now drops tool names the `claude` CLI does
  not recognize (e.g. `ApplyPatch`) and enforces them via system-prompt
  guardrails instead.
- Session IDs with a `session_` prefix are normalized to bare UUIDs before
  being passed to `claude --resume`.
- Repo-location clarification no longer triggers for project-management
  follow-ups about milestones, epics, backlogs, or sprints unless an explicit
  path or URL is provided.
- Playbook prompts queued by `/next` no longer trigger the repo-location
  clarification; they are already scoped to the current project.

## [1.6.5] - 2026-08-23

### Fixed
- Prompts selected via `/next` and `/select` are now queued for execution
  instead of being printed and ignored. The main loop executes queued prompts
  in subsequent turns.

## [1.6.4] - 2026-08-23

### Fixed
- Pressing Escape now cancels the `/next` and `/select` TUI flows at any step
  (prompt selection, action selection, or inline editing) and returns the user
  to the main interactive prompt.

## [1.6.3] - 2026-08-23

### Changed
- The `/next` prompt-selection TUI now displays the full expanded prompt body
  under each title, so users can see exactly what they are selecting.

## [1.6.2] - 2026-08-23

### Changed
- `/next` now opens the prompt-selection TUI automatically. Users no longer
  need to type `/select` after `/next`; they can pick prompts with the cursor,
  choose to edit each one, and queue multiple prompts for execution in one step.

## [1.6.1] - 2026-08-23

### Changed
- Suggested prompt lists now include a tip telling users they can type a number
  or run `/select` to open a cursor-driven menu.

## [1.6.0] - 2026-08-23

### Fixed
- `/select` and numeric prompt selection now use questionary's async
  ``application.run_async()`` API, fixing ``RuntimeError: asyncio.run() cannot
  be called from a running event loop`` inside ``maestro --interactive``.

## [1.5.9] - 2026-08-22

### Fixed
- Questionary TUI prompts now run on the main thread so arrow-key navigation
  and checkbox selection work reliably. Running them in an executor thread
  prevented prompt_toolkit from controlling the terminal correctly.

## [1.5.8] - 2026-08-22

### Changed
- Prompt selection now uses a TUI powered by ``questionary``. After `/next` or
  `/prompts`, type a number or run `/select` to open a cursor-driven menu.
  - Checkbox TUI lets you pick one or more prompts.
  - Each selected prompt can be executed as-is, edited inline, or skipped.
  - Multi-select via `/select` queues prompts and executes them in sequence.

## [1.5.7] - 2026-08-22

### Changed
- Prompt editing now uses the system's ``$EDITOR`` (or a fallback editor) for
  reliability. After selecting a prompt by number, the prompt opens in a temp
  file so it can be edited and saved before execution. If no editor is found,
  the original prompt executes as-is.

## [1.5.6] - 2026-08-22

### Fixed
- Selected prompts now correctly appear in the editable input line. The readline
  startup hook now calls ``redisplay()`` and collapses multi-line prompts into
  a single editable line.

## [1.5.5] - 2026-08-22

### Changed
- Selected prompts are now pre-filled into the input line using readline so the
  user can edit them before pressing Enter to execute, rather than running
  immediately.

## [1.5.4] - 2026-08-22

### Added
- Interactive prompt selection by number. After `/next` or `/prompts` displays
  suggested prompts, typing `1`, `2`, `3`, etc. selects and executes the
  corresponding prompt. The selected prompt is shown with its title before
  execution, and suggestions are cleared afterward to avoid misinterpreting
  later numeric input.

## [1.5.3] - 2026-08-22

### Fixed
- `/next` now shows the full rendered text of each suggested prompt instead of
  a truncated one-line preview, so users can see exactly what they are selecting.

## [1.5.2] - 2026-08-22

### Changed
- Subprocess output from Kimi CLI and Claude CLI runtimes is now rendered with
  Rich for better readability. Output lines are prefixed with colored labels
  (`[kimi]`, `[claude]`), Kimi stream-json lines are decoded into human-readable
  content/tool lines, and obvious Markdown lines are rendered inline.

## [1.5.1] - 2026-08-22

### Changed
- Kimi CLI and Claude CLI runtimes now stream subprocess stdout/stderr to the
  terminal in real time while a turn runs, so interactive mode shows the actual
  tool calls, file reads, and progress emitted by the underlying CLI instead of
  only a elapsed-time heartbeat.

## [1.5.0] - 2026-08-22

### Added
- Milestone prompt playbook derived from the completed M3BudgetUpload project.
  - Default `software-consulting` playbook ships with the wheel at
    `src/open_maestro/milestones/playbooks/software-consulting.yaml`.
  - Playbook contains reusable prompt templates for all 8 lifecycle milestones
    (Intake & Discovery through Retrospective & Findings).
  - `/next` now suggests the top 3 playbook prompts for the current/next
    milestone.
  - New `/prompts <milestone> [epic]` interactive command lists all prompts for
    a milestone so users can copy, edit, and execute them.
  - Project-level playbook overrides via `.open-maestro/playbook.yaml`.
  - Placeholder resolution for `{date}`, `{epic_id}`, `{epic_name}`, and
    `{artifact_target}`.

### Fixed
- `--monitor` no longer shows a blank cursor during long-running turns. CLI
  runtimes now emit `runtime.working` heartbeat events every 5 seconds, and the
  monitor renderer displays elapsed working time with interim updates.

## [1.4.2] - 2026-08-18

### Changed
- Multi-agent chain mode is now on by default in interactive mode. Use `/chain`
  to toggle it off.

## [1.4.1] - 2026-08-18

### Fixed
- Multi-line pasted text in `maestro --interactive` is now captured as a single
  prompt instead of being split into one prompt per line.

## [1.4.0] - 2026-08-18

### Added
- Multi-agent chain execution (`--chain` CLI flag, `/chain` interactive toggle).
  A single user request is decomposed into up to 5 sequential specialist-agent
  steps (e.g., research → engineer → QA) with per-step capability-aware model
  selection.
- LLM-driven chain planner with JSON output and fallback to predefined chains
  for common patterns (`implement`, `fix`, `analyze`).
- Per-step runtime/model arbitration inside a chain so each agent uses the
  cheapest capable model independently.
- Chain progress events (`chain.step_started`, `chain.step_completed`) displayed
  in the live monitor.

## [1.3.0] - 2026-08-17

### Added
- Live activity monitor (`--monitor`) showing current agent, runtime, model,
  task, context usage, and recent events during execution.
- Rich-based terminal UI for the monitor in both one-shot and interactive mode.

### Changed
- Added `rich>=13.0` as a core dependency.

## [1.2.4] - 2026-08-16

### Added
- Real tool interception for Kimi/Claude CLI runtimes via the Agent Client
  Protocol (ACP) and Claude Agent SDK.
- Capability-aware model router with cost/latency arbitration.
- `--prefer-local` flag to prefer local/self-hosted models.
- `--show-plan` and `--dry-run` flags for inspecting execution plans.
- Support for reasoning overrides from natural-language prompts and CLI flags.

### Fixed
- Local Ollama models are correctly discovered and used via the OpenAI SDK.
- Dashboard HTML renderer consumes the new epics-first milestone schema.

## [1.2.0] - 2026-08-13

### Added
- Milestone-guided project lifecycle with epics (workstreams/features) and
  standard lifecycle milestones inside each epic.
- Client-facing dashboard publisher (`--publish-dashboard`) for remote receivers
  such as Merven core.
- Local dashboard server (`--serve-dashboard`) with HTML, JSON, and Markdown
  exports.
- `--sync-milestones` to pull canonical epic/workstream structure from Merven.

### Changed
- Dashboard JSON schema changed from flat `milestones` to `epics[].milestones[]`.
  Old milestone files must be migrated.

## [1.1.0] - 2026-08-10

### Added
- Model capability registry (`capabilities.yaml`) for vendor-neutral model
  aliases and capability flags.
- Multi-runtime support: `kimi-cli`, `claude-cli`, `openai-sdk`, `kimi-acp`,
  `claude-sdk`.
- Agent sources and skill sources with Git-based sync.
- Pre-registered MIT-licensed skills repo as a default source.

## [1.0.0] - 2026-08-08

### Added
- Initial Open Maestro multi-agent orchestration layer.
- Vendor-agnostic agent routing across Claude, Kimi, and OpenAI-compatible
  models.
- Research, planning, documentation, and code-change agent workflows.
- Persistent project memory via kuzu-memory.
- Semantic code search via mcp-vector-search.
- Interactive mode (`--interactive`) with agent pinning and model overrides.
