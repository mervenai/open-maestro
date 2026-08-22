# Deploying Open Maestro to an Engineering Team

This guide covers installing Open Maestro on internet-connected Ubuntu or WSL
workstations for a team of engineers.

## Current state (read this first)

Open Maestro **1.6.1** is a functional multi-agent orchestration layer with:

- Vendor-agnostic agent routing across Claude, Kimi, and OpenAI-compatible models
- Model arbitration that picks the cheapest capable backend for a task
- Research, planning, documentation, and code-change workflows
- Milestone-guided project lifecycle with client-facing dashboard
  - **v1.2.4+ taxonomy:** projects contain **epics** (workstreams/features); each epic contains the 8 standard lifecycle **milestones**
- Persistent project memory via kuzu-memory
- Semantic code search via mcp-vector-search
- Live activity monitor (`--monitor`) showing current agent, runtime, model, and state

CLI runtimes (`claude-cli`, `kimi-cli`) spawn a subprocess and parse the final
output, so they cannot intercept individual tool calls. For full tool-call
guardrails and mid-task handoffs, use the `openai-sdk` or `claude-sdk` runtimes.

## What each engineer needs

1. **Ubuntu 22.04+ or WSL2** with a normal internet connection.
2. **Python 3.11 or higher**.
3. **One backend CLI or SDK**:
   - `claude` CLI for Claude Code users
   - `kimi` CLI for Kimi Code users
   - or the `openai` Python package for the `openai-sdk` runtime (cloud or local/Ollama)

## Distribution options

### Option A: Distribute the pre-built wheel (recommended)

Build the wheel once and share it with the team:

```bash
cd /Users/jj/dev/open-maestro
python -m build --wheel
# Share dist/open_maestro-1.6.1-py3-none-any.whl
```

Each engineer runs the install script:

```bash
./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl
```

To also install SDK runtimes and their Python dependencies:

```bash
# openai-sdk runtime (cloud OpenAI, Azure, Ollama, vLLM, DashScope, etc.)
OPENAI=1 ./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl

# All SDK runtimes
OPENAI=1 CLAUDE_SDK=1 KIMI_ACP=1 ./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl
```

The `OPENAI=1` flag installs the `openai` package, which is required for the
`openai-sdk` runtime even when you only use it with a local Ollama server.

### Option B: Install from source

For engineers who will hack on Maestro itself:

```bash
git clone https://github.com/yourorg/open-maestro.git
cd open-maestro
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,openai,claude-sdk,kimi-acp]"
```

### Option C: Publish to a private PyPI index

If your team has a private PyPI, publish the wheel there:

```bash
pip install twine
twine upload --repository your-private-pypi dist/*.whl
```

Then each engineer installs with:

```bash
pip install --index-url https://your-pypi.example.com/simple open-maestro
```

## Install script details

`scripts/install-ubuntu.sh` does the following:

1. Finds Python 3.11+.
2. Creates `~/.open-maestro/venv`.
3. Installs the wheel (and optional extras).
4. Symlinks `~/.local/bin/maestro` to the venv binary.
5. Prints next steps.

If `~/.local/bin` is not on the user's PATH, the script prints a warning with
the line to add to `~/.bashrc` or `~/.zshrc`.

## Verify the install

```bash
maestro --version
maestro --list-runtimes
maestro --interactive
```

You should see:

- A version string
- Available runtimes (at least the ones matching installed CLIs/API keys)
- The interactive prompt with no "No agent definitions found" error

## Backend setup

### Claude Code

Install the Claude Code CLI and authenticate:

```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

> **Tool interception caveat:** `claude auth login` is enough for the `claude-cli`
> subprocess runtime, but it is **not** enough for real tool interception. When
> Maestro delegates `run_with_hooks` to the Claude Agent SDK, the SDK requires an
> Anthropic API key. Install the SDK extra and set the key:
>
> ```bash
> CLAUDE_SDK=1 ./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl
> export ANTHROPIC_API_KEY="sk-ant-..."
> ```
>
> Without `ANTHROPIC_API_KEY`, the `claude-cli` runtime will still work, but it
> will enforce guardrails via the system prompt only.

### Kimi Code

Install the Kimi Code CLI and authenticate:

```bash
# Follow Moonshot AI's current installation instructions for kimi
kimi --version
```

### OpenAI-compatible API

The `openai-sdk` runtime needs the `openai` Python package. If you did not install
with `OPENAI=1`, add it manually:

```bash
~/.open-maestro/venv/bin/pip install openai
```

Set environment variables or pass flags per command:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_API_KEY="sk-..."
maestro --runtime openai-sdk "refactor the parser"
```

For local models (e.g., Ollama):

```bash
# Ollama on the default port is auto-detected; no API key needed.
ollama pull qwen3:8b
ollama serve

maestro --prefer-local "summarize the codebase"
```

You can still set `OPENAI_BASE_URL` explicitly if Ollama runs on a non-default
host or port:

```bash
export OPENAI_BASE_URL="http://localhost:11434/v1"
maestro --prefer-local --runtime openai-sdk --model qwen3:8b "summarize the codebase"
```

## Default model and runtime preferences

Maestro picks the runtime and model in one of two ways:

1. **Pinned runtime** — if you set `--runtime` or `OPEN_MAESTRO_RUNTIME`, every
   turn uses that runtime. This is the simplest setup but it never escalates to
   a different vendor, even for hard reasoning tasks.
2. **Arbitrated selection** — if no runtime is pinned, Maestro chooses the cheapest
   capable backend for each task profile. This lets a Kimi default escalate to
   Claude for deep-reasoning prompts.

### Recommended per-user defaults

Create `~/.open-maestro/models.yaml` to declare which providers are available.
Keep all providers you want Maestro to consider; remove only the ones you never
want it to use:

```yaml
# /home/jahanzeb/.open-maestro/models.yaml
models:
  default: smart
  fast: fast
  smart: smart
  reasoning: reasoning

providers:
  kimi-cli:
    provider: kimi
    cli: kimi
    api_base: https://api.kimi.com/coding/v1
  claude-cli:
    provider: anthropic
    cli: claude
    api_base: https://api.anthropic.com/v1
  openai-sdk:
    provider: openai
    api_base: https://api.openai.com/v1
```

Do **not** set `OPEN_MAESTRO_RUNTIME` if you want the arbitrator to be able to
pick Claude when Kimi is not the best fit. Only pin the runtime when you need
to force a specific backend:

```bash
# Pin to Kimi (no cross-vendor escalation)
export OPEN_MAESTRO_RUNTIME=kimi-cli
maestro --interactive

# Let Maestro choose per turn (can escalate to Claude)
maestro --interactive
```

### Runtime flags that affect model selection

- `--fast` or `--cost-preference low` — prefer cheap/fast models.
- `--reasoning` — bump the required reasoning depth, which tends to select Kimi K3
  or Claude Opus.
- `--cost-preference high` — restrict to frontier models (Claude Opus, Kimi K3,
  GPT-4o/o3-mini).
- `--prefer-local` — only consider local/self-hosted models (Ollama, vLLM, etc.).

To preview what Maestro will pick for a task without invoking it:

```bash
maestro --show-plan "refactor the parser"
```

The output shows the selected runtime and resolved model.

## Live activity monitor

Maestro 1.3.0 added a lightweight live monitor (`--monitor`) that shows the
current agent, runtime, model, task, context usage, and recent events while a
prompt is executing. v1.4.0 added chain progress when `--chain` is used.

Use it in one-shot mode:

```bash
maestro --monitor "analyze this codebase"
```

Or in interactive mode:

```bash
maestro --monitor --interactive
```

The monitor renders a Rich panel that updates in real time and disappears once
the turn completes. It is useful for watching which agent/runtime Maestro chose,
what tool is currently running, and whether context pressure is rising.

## Multi-agent chains

v1.4.0 can decompose a single request into a chain of specialist agents. The
planner is LLM-driven with fallback to predefined patterns:

- `implement` / `build` / `create` → research → engineer → QA
- `fix` / `debug` → research → engineer → QA
- `analyze` / `evaluate` / `report` → research → documentation

Use `--chain` in one-shot mode:

```bash
maestro --chain "implement a CSV parser for the import flow"
```

Or toggle it in interactive mode:

```bash
maestro --interactive
> /chain
Multi-agent chain mode: on.
> implement a CSV parser for the import flow
```

Each step picks the cheapest capable model independently, and the final response
is grouped by agent. Chains are capped at 5 steps.

## Additional integrations

### kuzu-memory

For persistent project memory:

```bash
pip install kuzu-memory
```

Then run Maestro with `--memory`:

```bash
maestro --memory --interactive
```

### mcp-vector-search

For semantic code search:

```bash
pip install mcp-vector-search
```

Then run Maestro with `--search`:

```bash
maestro --search --interactive
```

### MCP servers

Create `~/.open-maestro/mcp.json` or `./.open-maestro/mcp.json`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"]
    }
  }
}
```

## Milestone dashboards

Maestro tracks project milestones and exposes a client-facing dashboard. In v1.3.0
the taxonomy changed: a project contains **epics** (workstreams / features such as
"Import Flow" or "Audit Log"), and each epic contains the 8 standard lifecycle
**milestones** (Intake & Discovery, Execution Planning, Design Blueprint, etc.).

### Export a dashboard

From inside a project directory:

```bash
# JSON
maestro --export-dashboard json > dashboard.json

# Markdown
maestro --export-dashboard markdown > dashboard.md

# HTML (styled like merven.ai)
maestro --export-dashboard html > dashboard.html
```

### Link a Maestro project to Merven

To pull the canonical epic/workstream structure from Merven, you need a Merven
project and its token. This is done on the Merven core server, not the engineer
workstation.

#### 1. Create the project on the Merven server

SSH into the Merven core server and run:

```bash
cd /opt/merven
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec core merven project create "Project Name" \
  --client-name "Client Name" \
  --epic "Epic One" \
  --epic "Epic Two"
```

The command prints a project ID and token. If you only have the project ID, the
token is stored in the `project` table under the tenant schema in Postgres:

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml \
  exec postgres psql -U merven -d merven -t -A \
  -c "SET search_path TO tenant_acme_corp; SELECT project_token FROM project WHERE project_id='PROJECT_ID';"
```

(Replace `tenant_acme_corp` with the correct tenant schema if different.)

#### 2. Sync milestones to the local workstation

On the engineer machine, from the project directory:

```bash
cd ~/projects/YourProject
export MERVEN_API_URL="https://api.staging.merven.ai/maestro"
export MAESTRO_DASHBOARD_PROJECT_TOKEN="project-token-from-above"
maestro --sync-milestones
```

`MERVEN_API_URL` is the Merven core API base path. `MAESTRO_DASHBOARD_PROJECT_TOKEN`
is the project token from Merven. The local plan is written to
`.open-maestro/milestones.yaml`.

**Note:** `--sync-milestones` requires the workstation to reach `MERVEN_API_URL`.
If the workstation is behind a heavy firewall or VPN that blocks outbound HTTPS,
sync will time out. In that case, create and manage milestones locally with
`maestro --discover-milestones` and the `/track`, `/complete`, `/blocker`
interactive commands.

### Serve the dashboard locally

```bash
maestro --serve-dashboard --dashboard-port 8080
```

Then open http://localhost:8080 in a browser. Endpoints:

- `/` — HTML dashboard
- `/api/dashboard` — JSON dashboard
- `/dashboard.md` — Markdown dashboard

### Publish to a remote receiver (e.g. staging.merven.ai)

Open Maestro 1.1.0 ships a dashboard *publisher*. The receiver lives in the
Merven core API on `api.staging.merven.ai` (the `merven.ai` root site is hosted on
Lovable and cannot run a Python backend). On the Merven core server, set
`MERVEN_MAESTRO_DASHBOARD_API_KEY` in `deploy/.env`. On the Maestro CLI, use
`MAESTRO_DASHBOARD_API_KEY`. Both values must be identical.

```bash
export MAESTRO_DASHBOARD_URL="https://api.staging.merven.ai/maestro/dashboard"
export MAESTRO_DASHBOARD_API_KEY="your-api-key"
export MAESTRO_DASHBOARD_PROJECT_TOKEN="project-token"

maestro --publish-dashboard "$MAESTRO_DASHBOARD_URL"
```

Or pass everything inline:

```bash
maestro --publish-dashboard https://api.staging.merven.ai/maestro/dashboard --dashboard-api-key "your-api-key" --dashboard-project-token "project-token"
```

On the Merven server, make sure `deploy/.env` contains:

```bash
MERVEN_MAESTRO_DASHBOARD_API_KEY="your-api-key"
```

And deploy with `--env-file deploy/.env` from `/opt/merven` so Compose reads the file:

```bash
cd /opt/merven
docker compose --env-file deploy/.env -f deploy/docker-compose.yml -f deploy/staging/docker-compose.staging.yml up -d
```

The receiver accepts a POST with this payload:

```json
{
  "dashboard": {
    "project_id": "...",
    "project_name": "...",
    "overall_completion": 42,
    "current_milestone": ["import-flow/implementation"],
    "active_blockers": [],
    "epics": [
      {
        "id": "import-flow",
        "name": "Import Flow",
        "completion": 65,
        "milestones": [...]
      }
    ],
    "recent_deliverables": [...]
  },
  "metadata": {
    "source": "maestro-cli"
  }
}
```

## Recommended starting workflow

For new teams, start with research and analysis tasks:

```bash
# From inside a project directory
maestro --interactive

# Then type:
# "analyze this codebase and tell me the major building blocks"
```

Add `--reasoning` for architectural questions and `--fast` for quick summaries.

Use `--show-plan` to inspect what Maestro will do before spending tokens:

```bash
maestro --show-plan "refactor the budget import parser"
```

## Updating the team

When you release a new wheel:

```bash
maestro --version          # note old version
./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl
maestro --version          # confirm new version
```

User-level config, sources, and memory in `~/.open-maestro/` are preserved.

### Upgrading to v1.6.1

v1.6.1 is backward-compatible with v1.3.0/v1.4.0/v1.4.1 milestone files. The
main additions are multi-agent chains (`--chain` / `/chain`) and per-step model
arbitration (v1.4.0), reliable multi-line paste handling in interactive mode
(v1.4.1), and chain mode defaulting to on in interactive mode (v1.6.1). After
upgrading the wheel, run `maestro --version` to confirm `1.6.1`.

### Upgrading from v1.2.x or earlier (schema migration)

v1.3.0+ uses a new milestone schema: **epics contain milestones**. Old
`.open-maestro/milestones.yaml` files created by v1.0/v1.1/v1.2 will be rejected
with a clear error. To migrate each project:

```bash
cd ~/projects/YourProject
rm .open-maestro/milestones.yaml
maestro --sync-milestones   # pull epics from Merven and create standard milestones
```

If the project is not linked to Merven, delete the old file and Maestro will
create a fresh single-epic plan on next access.

### Force reinstallation

If the existing install is broken, the wheel filename changed, or you want to
wipe dependencies and start clean, you have two options.

**In-place upgrade (recommended):**

Running the install script again will replace the venv contents while keeping
your config, sources, and memory:

```bash
./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl
```

Use the same feature flags you used the first time (e.g. `OPENAI=1`) so the
venv gets the optional SDK dependencies again.

**Nuclear option:**

Delete only the `venv` directory and rerun the install script. Everything else
in `~/.open-maestro/` — user config, agent sources, memory databases, and logs
— is preserved:

```bash
rm -rf ~/.open-maestro/venv
./install-ubuntu.sh /path/to/open_maestro-1.6.1-py3-none-any.whl
```

## Troubleshooting

### `Error: No available runtime can satisfy the task profile`

Maestro cannot find a backend that matches your request. Common causes:

- No backend CLI or SDK is installed. Run `maestro --list-runtimes` to see what
  is available.
- You used `--prefer-local` but no local model is reachable. Ensure Ollama is
  running (`curl http://localhost:11434/api/tags`) and the model is pulled.
- You asked for the `openai-sdk` runtime (directly or via `--prefer-local`) but
  the `openai` Python package is missing. Install it:

  ```bash
  ~/.open-maestro/venv/bin/pip install openai
  ```

### `--prefer-local` still picks a cloud model

`--prefer-local` only considers models whose `provider` is `ollama` or `local`
in the capability registry. If you pulled a model that is not in the default
registry, add it to `~/.open-maestro/capabilities.yaml` or
`.open-maestro/capabilities.yaml`:

```yaml
models:
  my-local-model:
    name: "My Local Model"
    provider: ollama
    aliases: [local]
    identifiers:
      openai-sdk: my-model-name:latest
    capabilities:
      tier: fast
      tool_use: true
      vision: false
      reasoning: light
      coding_strength: medium
      max_context_tokens: 128000
      max_output_tokens: 8192
      latency_hint: medium
      cost_level: low
      relative_cost: 0.05
```

## Known limitations

- CLI runtimes (`kimi-cli`, `claude-cli`) cannot intercept individual tool calls.
  Tool restrictions are enforced via system-prompt guardrails.
- `kimi-cli` ignores `--max-turns` and does not support `--allowed-tools` / `--blocked-tools` natively.
- Streaming output and progress indicators are functional but minimal.
- Vision support depends on the chosen model and runtime.
- The remote dashboard receiver endpoint is implemented in the Merven core on
  `staging.merven.ai`; Maestro ships the publishing client only.

## Getting help

- `maestro --help`
- `maestro --list-runtimes`
- Inspect logs in `~/.open-maestro/logs/` (if configured)
