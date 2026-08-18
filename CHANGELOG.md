# Changelog

All notable changes to Open Maestro are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
