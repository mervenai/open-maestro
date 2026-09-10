# Deploying Open Maestro to an Engineering Team

This guide covers installing Open Maestro on internet-connected Ubuntu or WSL
workstations for a team of engineers.

## Current state (read this first)

Open Maestro **1.13.0** is a functional multi-agent orchestration layer with:

- Vendor-agnostic agent routing across Claude, Kimi, and OpenAI-compatible models
- Model arbitration that picks the cheapest capable backend for a task
- Research, planning, documentation, and code-change workflows
- Milestone-guided project lifecycle with client-facing dashboard
  - **v1.2.4+ taxonomy:** projects contain **epics** (workstreams/features); each epic contains the 8 standard lifecycle **milestones**
- Persistent project memory via kuzu-memory
- Semantic code search via mcp-vector-search
- Live activity monitor (`--monitor`) showing current agent, runtime, model, and state
- Orchestrator-enforced quality gates: automatic code-critic review pass after
  implementation turns (v1.13.0) and a security scan gate prompt — gitleaks,
  semgrep, dependency audit, SBOM — at the end of implementation (v1.12.0)

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
# Share dist/open_maestro-1.13.0-py3-none-any.whl
```

Each engineer runs the install script:

```bash
./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl
```

To also install SDK runtimes and their Python dependencies:

```bash
# openai-sdk runtime (cloud OpenAI, Azure, Ollama, vLLM, DashScope, etc.)
OPENAI=1 ./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl

# All SDK runtimes
OPENAI=1 CLAUDE_SDK=1 KIMI_ACP=1 ./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl
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
> CLAUDE_SDK=1 ./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl
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

Maestro can route to any OpenAI-compatible local server. The easiest path is
Ollama, which is auto-detected on the default port (`localhost:11434`).

### Step 1 — Install and start Ollama

```bash
brew install ollama        # macOS
# or follow https://ollama.com/download for Linux/Windows
ollama serve               # skip if already running
```

If `ollama serve` fails with `address already in use`, Ollama is already running.

### Step 2 — Pull a local model

Recommended models:

- `deepseek-coder-v2` — large MoE coding/reasoning model. This is the **only**
  default local entry with `reasoning: deep`. However, the Ollama image does not
  expose function calling through the OpenAI-compatible endpoint, so Maestro
  marks it as `tool_use: false`. It is the best local choice for non-tool deep
  reasoning (e.g., design explanations, migration plans), but for deep-reasoning
  tasks that must search the codebase or invoke tools, Maestro will fall back to
  `qwen2.5-coder:32b` or escalate to a frontier model. Requires ~150 GB of disk
  and high-end GPU(s); use only if you have the hardware.
- `qwen2.5-coder:32b` — strong open-weights coding model, comparable to Claude
  Sonnet 3.5 on many coding benchmarks. Good for implementation, code review,
  and standard-tier analysis. Alias `local-smart` / `local-coder` / `local`.
  Requires ~20 GB of disk and roughly 24 GB of VRAM (or CPU RAM with
  quantization).
- `qwen3:8b` or `llama3.1:8b` — lightweight options for fast, cheap tasks only.
  They are **not** appropriate for architectural analysis or complex design work.

```bash
ollama pull qwen2.5-coder:32b
# Required for deep-reasoning / architecture work:
ollama pull deepseek-coder-v2
```

Verify the pull:

```bash
ollama list
```

### Step 3 — Install the `openai` package

The `openai-sdk` runtime needs the `openai` Python package. If you did not install
with `OPENAI=1`, add it manually:

```bash
~/.open-maestro/venv/bin/pip install openai
```

### Step 4 — Point Maestro at the local endpoint

Either set an environment variable:

```bash
export OPENAI_BASE_URL="http://localhost:11434/v1"
```

or pass it per command:

```bash
maestro --runtime openai-sdk --openai-base-url http://localhost:11434/v1 ...
```

No API key is needed for Ollama.

### Step 5 — Run Maestro with the local model

Use the model alias registered in the default capability registry:

```bash
# DeepSeek Coder V2 (alias: local-reasoning) — deep reasoning / architecture
maestro --runtime openai-sdk --model local-reasoning "design the data migration"

# Qwen 2.5 Coder 32B (aliases: local-smart, local-coder, local)
maestro --runtime openai-sdk --model local-smart "refactor the auth module"

# Qwen 3 8B (aliases: local, fast) — quick, cheap tasks only
maestro --runtime openai-sdk --model local "summarize this file"
```

Or let Maestro prefer local models automatically:

```bash
maestro --prefer-local "summarize the codebase"
```

When `--prefer-local` is used, Maestro considers every local model in the
registry and picks the cheapest capable one for the task profile. Because
`qwen2.5-coder:32b` now carries the `local` alias, standard coding tasks will
prefer it over `qwen3:8b`. Fast/cheap tasks may still pick `qwen3:8b` because it
is cheaper and faster. Deep-reasoning tasks require `deepseek-coder-v2`.

You can still set `OPENAI_BASE_URL` explicitly if Ollama runs on a non-default
host or port:

```bash
export OPENAI_BASE_URL="http://localhost:11434/v1"
maestro --prefer-local --runtime openai-sdk --model qwen2.5-coder:32b "summarize the codebase"
```

### Interactive mode with permission-based escalation

In `maestro --interactive`, you can prefer local models while keeping frontier
(cloud) models as a permission-based fallback:

```bash
maestro --interactive
> /local
Local preference: on. Maestro will ask before escalating to a frontier model.

> design the data migration API
# If no capable local model is available, Maestro asks:
# "No capable local model found. Escalate to kimi-cli/kimi-code/k3?"
# Answer y/n.
```

Deep-reasoning tasks such as architectural analysis require a local model with
`reasoning: deep`. The only default local entry with that capability is
`deepseek-coder-v2`. If you have not pulled it, `/local` mode will ask to
escalate to a frontier model (e.g., Kimi K3 or Claude Opus) for those tasks.

Or start interactive mode with the escalation behavior already enabled:

```bash
maestro --ask-escalate --interactive
```

`/local` is a toggle; run it again to turn local preference off. When local
preference is on and the task is beyond the local model's capability, Maestro
proposes the specific vendor/runtime and model it would switch to and waits for
your approval.

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

Maestro tracks project milestones and exposes a client-facing dashboard
(local export, local serve, a self-hosted receiver, and a legacy Merven
integration). Dashboard setup and updating live in a separate guide so this
one stays focused on workstation installation:

**→ See [Dashboard Setup & Updating](DASHBOARD.md)**

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
./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl
maestro --version          # confirm new version
```

User-level config, sources, and memory in `~/.open-maestro/` are preserved.

### Upgrading to v1.13.0

v1.13.0 is backward-compatible with v1.3.0+ milestone files. Since v1.10.3 the
additions are: multi-line prompt input (Ctrl+J / Alt+Enter), a streamlined
playbook (duplicate plan-003 removed, artifact lines auto-appended), a security
scan gate at the end of implementation (v1.12.0), and an automatic code-critic
review pass after implementation turns (v1.13.0). After upgrading the wheel, run
`maestro --version` to confirm `1.13.0`.

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
./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl
```

Use the same feature flags you used the first time (e.g. `OPENAI=1`) so the
venv gets the optional SDK dependencies again.

**Nuclear option:**

Delete only the `venv` directory and rerun the install script. Everything else
in `~/.open-maestro/` — user config, agent sources, memory databases, and logs
— is preserved:

```bash
rm -rf ~/.open-maestro/venv
./install-ubuntu.sh /path/to/open_maestro-1.13.0-py3-none-any.whl
```

## Troubleshooting

### `Error: No available runtime can satisfy the task profile`

Maestro cannot find a backend that matches your request. Common causes:

- No backend CLI or SDK is installed. Run `maestro --list-runtimes` to see what
  is available.
- You used `--prefer-local` but no local model is reachable. Ensure Ollama is
  running (`curl http://localhost:11434/api/tags`) and the model is pulled.
- You used `--prefer-local` for a deep-reasoning / architectural task but did not
  pull `deepseek-coder-v2`. Either pull it (`ollama pull deepseek-coder-v2`) or
  allow Maestro to escalate to a frontier model.
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
