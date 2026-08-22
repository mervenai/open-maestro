# Changelog

All notable changes to Open Maestro are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
