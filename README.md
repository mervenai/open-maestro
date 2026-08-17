# Open Maestro

A vendor-agnostic multi-agent orchestration runtime inspired by [claude-mpm](https://github.com/bobmatnyc/claude-mpm).

Open Maestro separates **agent definitions**, **orchestration logic**, and **model/runtime execution** so you can run the same specialist-agent workflows with Claude Code, Kimi Code, OpenAI-compatible APIs, or any future backend.

## Why this exists

`claude-mpm` is powerful but deeply Claude-specific:

- Requires the Claude Code CLI.
- Hard-codes Anthropic model names and the `claude-agent-sdk`.
- Agent definitions assume Claude-only behavior.
- MCP memory/search integrations are wired to Claude Code plugin conventions.

Open Maestro keeps the good parts (specialist agents, PM routing, memory, code search) and replaces the vendor-specific runtime layer with a small, pluggable adapter interface.

## What Kimi Code already does natively

If you are using Kimi Code CLI, you already have:

- Sub-agent spawning via the `Agent` tool (the tool used to launch this analysis).
- Built-in skills system (`--skills-dir`).
- Persistent memory via the `kuzu-memory` MCP server.
- Semantic code search via the `mcp-vector-search` MCP server.
- Multi-workspace support, session resume, and REST/WebSocket server.

Open Maestro does not replace Kimi Code. It provides a **portable, scriptable orchestration layer** that can:

1. Run outside of any single CLI session.
2. Drive Claude, Kimi, or API-based models from the same agent definitions.
3. Be embedded in services, CI pipelines, or cloud runners.

## Architecture

```
┌─────────────────────────────────────────┐
│           User / CI / Service           │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      Project Manager (Orchestrator)     │
│  - task routing  - context assembly     │
│  - memory recall - code search          │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      Vendor-Neutral Agent Registry      │
│  markdown definitions + model aliases   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      AgentRuntime Adapter Layer         │
│  Kimi CLI  │  Claude CLI  │  API/SDK   │
└─────────────────────────────────────────┘
```

## Quick start

```bash
# Install
pip install -e ".[dev]"

# Run the demo
python examples/demo.py
```

## Runtime adapters

| Adapter       | Needs                                   | Best for                            |
|---------------|-----------------------------------------|-------------------------------------|
| `kimi-cli`    | `kimi` CLI on PATH                      | Kimi Code users, local workstations |
| `kimi-acp`    | `kimi` CLI + `agent-client-protocol`    | Real Kimi tool interception         |
| `claude-cli`  | `claude` CLI on PATH                    | Claude Code users                   |
| `claude-sdk`  | `claude-agent-sdk`                      | Real Claude tool interception       |
| `openai-sdk`  | `openai` package + API key              | API-only, CI, cloud runners         |

Models are selected by capability, not just alias.  The default registry lives
in `src/open_maestro/config/default_capabilities.yaml` and can be overridden in
`~/.open-maestro/capabilities.yaml` or `.open-maestro/capabilities.yaml`:

```yaml
models:
  kimi-k3:
    name: "Kimi K3"
    provider: kimi
    aliases: [smart, reasoning]
    identifiers:
      kimi-cli: kimi-code/k3
    capabilities:
      tier: premium
      tool_use: true
      vision: false
      reasoning: deep
      coding_strength: high
      max_context_tokens: 256000
      latency_hint: medium
      cost_level: medium
```

Open Maestro scores every model against a task profile extracted from the
prompt (or set via CLI flags) and picks the best concrete identifier for the
active runtime.  For example, a request for deep architectural reasoning will
prefer `kimi-code/k3` or `claude-opus-4-7`, while a quick summary will prefer
`kimi-code/kimi-for-coding` or `gpt-4o-mini`.

## SDK runtimes and tool interception

CLI adapters are limited: they spawn a subprocess and parse the final output.
They cannot intercept individual tool calls.

SDK adapters run agents programmatically and support real async tool guards.
The `openai-sdk` runtime is the most broadly usable today: it drives any
OpenAI-compatible chat-completions endpoint (OpenAI, Azure, and several open
weight providers) and executes Read/Write/Bash/Grep locally while invoking the
`tool_guard` before each call.

```python
from open_maestro.runtime.factory import create_runtime
from open_maestro.runtime.base import AgentConfig

runtime = create_runtime("openai-sdk")

async def guard(tool_name: str, tool_input: dict) -> bool:
    return tool_name not in {"Write", "Bash"}

result = await runtime.run_with_hooks(
    "refactor the parser",
    tool_guard=guard,
    config=AgentConfig(model="smart"),
)
```

- `openai-sdk` implements a full tool loop; tool calls are executed locally and
  guarded before execution.  It also honours `blocked_tools` and `allowed_tools`.
- `claude-sdk` uses `claude-agent-sdk` and its `can_use_tool` callback.
- `kimi-acp` uses `agent-client-protocol` to talk to `kimi acp` and guards
  permissions through the ACP `request_permission` client callback.

Install the optional dependencies:

```bash
pip install open-maestro[openai,claude-sdk,kimi-acp]
```

## Agent and skill sources

Open Maestro can sync agents and skills from Git repositories, just like
claude-mpm.  The MIT-licensed ``claude-mpm-skills`` repository is
pre-registered as a default skill source, so it is available on first run
unless you remove it:

```bash
# List configured sources (claude-mpm-skills appears by default)
maestro --list-sources

# Add a custom skill source
maestro --add-skill-source https://github.com/yourorg/your-skills

# Add a custom agent source
maestro --add-agent-source https://github.com/yourorg/your-agents

# Force a fresh sync before running
maestro --sync-sources "refactor the parser"

# Skip syncing on a normal run
maestro --skip-sync "refactor the parser"
```

Sources are cloned to ``~/.open-maestro/sources/`` and refreshed once per day
(by default) using ``git ls-remote`` to check for updates.  Each source can
optionally specify a subdirectory (e.g., ``agents/`` or ``skills/``) where the
Markdown files live, plus a list of glob exclude patterns to skip non-skill
files such as documentation.

## Agent definitions

Agents are plain Markdown files with YAML frontmatter:

```markdown
---
id: engineer
name: Software Engineer
role: engineer
model: smart
tools:
  - Read
  - Edit
  - Bash
skills:
  - python
  - testing
required_capabilities:
  tool_use: true
  coding_strength: high
---

# Primary Role
Implement, refactor, and test code changes.

# When to Use
Select for implementation tasks, bug fixes, and refactoring.
```

`required_capabilities` tells the orchestrator what the agent needs from its
model (vision, deep reasoning, high coding strength, large context window).  The
orchestrator merges those requirements with the task profile so the right model
is selected even when the prompt alone is ambiguous.

### Tiered agent loading

Agents are loaded from three tiers, in order of precedence:

1. **Project agents** in `./.open-maestro/agents/`
2. **User agents** in `~/.open-maestro/agents/`
3. **Bundled agents** shipped with Open Maestro

Later tiers override earlier tiers for agents with the same id.  Use project
agents for repository-specific specialists and user agents for personal
defaults.

### Agent inheritance

Agents can extend another agent via the `extends` field.  The child inherits
scalar fields, tool lists, blocked tools, skills, and capabilities, and then
overrides them with its own values.  Agents whose id starts with `base-` are
treated as abstract templates and are not returned in the final registry.

```markdown
---
id: base-engineer
name: Base Engineer
role: engineer
model: smart
tools:
  - Read
---

Base instructions.
```

```markdown
---
id: engineer
name: Software Engineer
role: engineer
extends: base-engineer
tools:
  - Edit
---

Specialized instructions.
```

### Skills

Skills are reusable instruction modules stored in `./.open-maestro/skills/`,
`~/.open-maestro/skills/`, or the bundled `skills/` directory.  Agents declare
the skills they need in their frontmatter; the loader appends the skill content
to the agent's system prompt after inheritance is resolved.

```markdown
---
id: python
name: Python Best Practices
tags:
  - python
  - style
---

# Python Best Practices

Write idiomatic, maintainable Python code.
```

Use `--skills-dir` to point to a project skills directory from the CLI.

The same definition can be executed by Kimi, Claude, or an API runtime.  Runtime
adapters map vendor-neutral config fields to provider-specific CLI flags:

| Config field | Kimi CLI | Claude CLI | OpenAI SDK |
|---|---|---|---|
| `model` | `-m` | `--model` | `model=` |
| `allowed_tools` | system prompt* | `--allowedTools` | tool filter |
| `blocked_tools` | system prompt* | `--disallowedTools` | tool filter |
| `permission_mode` | `--auto` / `--yolo` | `--permission-mode` | not applicable |
| `mcp_servers` | unsupported | `--mcp-config` (temp JSON) | not yet |
| `max_turns` | ignored | `--max-turns` | loop limit |

*Kimi prompt mode has no native allow/block flags, so restrictions are injected
into the system prompt.

## MCP server auto-configuration

Open Maestro discovers MCP server configs automatically from:

1. ``./.open-maestro/mcp.json`` or ``mcp.yaml``
2. ``~/.open-maestro/mcp.json`` or ``mcp.yaml``
3. ``./.mcp.json`` (Claude Code convention)
4. ``OPEN_MAESTRO_MCP_CONFIG`` environment variable
5. ``--mcp-config <path>`` CLI flag

Example ``.mcp.json``:

```json
{
  "mcpServers": {
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"]
    },
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    }
  }
}
```

Runtime support:

- **claude-cli** — passes the config directly via ``--mcp-config``.
- **openai-sdk** — spawns each MCP server over stdio, lists its tools, and makes them available to the model through the built-in tool loop (requires the ``mcp`` package).
- **kimi-cli** — MCP servers must be configured in Kimi Code settings; Open Maestro warns if a config is present.

## Memory and search

Open Maestro integrates with the same MCP tools already available in your environment:

- **kuzu-memory** for project context, preferences, and decisions.
- **mcp-vector-search** for semantic code search.

These are accessed through thin async clients so the orchestrator can recall context and find code before delegating to a specialist agent.

## CLI usage

```bash
# Run with auto-detected runtime and auto-selected model
maestro refactor the budget import parser

# Force a fast/cheap model
maestro --fast summarize the codebase

# Request deep reasoning
maestro --reasoning design the async worker architecture

# Vision/multimodal task
maestro --vision explain this screenshot

# Override model alias explicitly
maestro --model kimi-code/k3 write tests for the router

# Use memory and semantic code search
maestro --memory --search "why is the import failing"

# Inspect the plan without calling an LLM
maestro --show-plan refactor the budget import parser

# Run with tool guardrails
maestro --deny-dangerous --block-tools Bash "fix the parser"
maestro --permission-mode read-only "review the codebase"

# Resume or fork a previous session
maestro --resume             # list recent sessions
maestro --resume sess_abc "continue refactoring"
maestro --fork sess_abc "explore an alternative approach"
```

## Guardrails, sessions, and context pressure

Open Maestro enforces permission policies and persists session state:

- `--deny-dangerous` blocks destructive shell commands.
- `--allowed-tools` restricts the agent to an explicit allow-list.
- `--block-tools` and agent `blocked_tools` deny specific tools.
- `--permission-mode read-only` denies mutating tools.
- `--resume` / `--fork` continue previous sessions.
- `--max-context-tokens`, `--warning-threshold`, and `--critical-threshold` configure the context budget.
- Context usage is tracked cumulatively across resumes; when the critical threshold is crossed, a resume log is returned so you can start a fresh session without losing progress.
- Sessions are saved atomically and can be pruned with the `SessionStore` API.

## Status and team deployment

This is an **alpha/reference prototype**. The vendor-agnostic abstraction, agent
registry, router, memory clients, and runtime adapters are all functional and
tested. It works well today for research, analysis, planning, and simple coding
tasks across Claude, Kimi, and OpenAI-compatible backends.

CLI runtimes delegate by spawning the vendor CLI as a subprocess, so they cannot
intercept individual tool calls. For real tool guardrails and complex multi-step
implementation work, use the `openai-sdk` or `claude-sdk` runtimes.

See [`docs/DEPLOY.md`](docs/DEPLOY.md) for instructions on distributing Open
Maestro to an engineering team on Ubuntu or WSL.
