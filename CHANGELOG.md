# Changelog

All notable changes to Open Maestro are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.0] - 2026-10-11

### Changed
- **Swarm reliability — fail loud when a worker can't write its declared
  artifact; ensure artifact-owning workers are writable; skip swarm fan-out
  for single-deliverable requests; post-run artifact reconciliation summary**
  (`orchestrator.swarm`, fixes #2). Interactive mode defaults chain+swarm ON,
  which let a single-file request fan out into a multi-worker swarm whose
  read-only-seeded workers silently failed to write their declared artifacts
  — burning tokens/cost while producing only a fraction of the planned files
  and still reporting success-ish output. The swarm path now:
  - **Fails loud, not silent.** A worker that declares an output artifact but
    leaves no file on disk after running is marked FAILED (not success), so
    re-seating and the degraded-run signalling treat it as a real failure and
    the run summary reflects it.
  - **Grants write to artifact owners before fan-out.** When a turn is seeded
    read-only (clone-guard / pre-implementation phase), workers that own an
    output `target_file` get the `Write`/`Edit` tools back — a worker
    responsible for producing a file can always write it, while fragment-less
    analysis workers stay read-only.
  - **Skips fan-out for single-deliverable requests.** When the decomposition
    collapses to a single output file — the task names one explicit output
    and the prompt itself names no other distinct target, or at most one
    worker declares a write target — the swarm is declined and the request
    routes to a single agent instead of fanning out. Genuine multi-target
    swarms ("update docs/a.md, docs/b.md, docs/c.md", "audit repos A/B/C")
    are unaffected. (Supersedes the MSTRO-126 fan-out-then-merge behavior for
    the pure single-output case.)
  - **Reconciles produced vs planned artifacts.** The run summary and
    metadata now list which planned output files were and were not written
    (`swarm_artifacts_planned` / `_produced` / `_missing`), so a partial run
    can never masquerade as complete.
- **Docs: single-deliverable invocation** (`docs/DEPLOY.md`). Documented the
  recommended efficient pattern for one-file tasks:
  `maestro --no-chain --no-swarm --agent <id> "...one file; do not split
  it..."`.

## [2.2.1] - 2026-10-10

### Fixed
- **Dashboard status classes consistency** (`milestone.status_class()`).
  Consolidated duplicate status-class logic and ensured consistent CSS
  class names for milestone status colors across the dashboard renderer.
- **Dashboard design tokens export** (`design_tokens()`). The dashboard
  JSON now exports shared design tokens (`_STATUS_COLOR_MAP`, color
  definitions) so remote renderers (Lovable, etc.) can inherit the
  canonical color scheme without duplication. Enables cross-platform
  dashboard consistency for the merged remote dashboard work.

## [2.2.0] - 2026-10-08

### Added
- **Mermaid diagram skill** (`mermaid-diagrams`, bundled). Agents asked to
  diagram a blueprint/design doc carry a codified procedure: narrowest
  diagram type per content, syntax-pitfall rules, validate-by-rendering
  before saving, and both output formats (`.md` with a fenced mermaid block
  plus a rendered `.png` via mermaid-cli or the Kroki fallback). Wired into
  the engineer, data-engineer, and documentation agents.
- **Playbook: Design Blueprint milestones draft a diagram, then verify**
  (`software-consulting.yaml`). New `design-002` (order 2, after the contract
  draft) produces the numbered, repo-tagged, open-question-highlighted
  blueprint diagram; the original adversarial-verification prompt returns as
  `design-006` (order 6), generalized to cross-check every milestone artifact
  (contract, diagram, UX spec, Jira stories) with a consistency pass.
- **Read-only MCP vetting for auditor seats** (`mcp.policy`). Agent-declared
  tool allowlists (e.g. the code-critic's Read/Grep/Bash) previously locked
  review seats out of semantic search entirely — 72 agent protocols reference
  the `mcp__mcp-vector-search__*` tools, and restricted seats reported them
  as "not exposed in this runtime". `_execute_agent` now unions a vetted
  read-only tool set (search/analyze/kg-query, 19 tools) into active
  allowlists when an MCP config is loaded; mutating tools (`index_project`,
  `save_report`, `review_*`, `kg_build`, …) stay excluded.
- **Persona turn-cap retry** (`deep_review`). A seat cut off by
  `error_max_turns` retries once on the same seat at 2x
  `PERSONA_MAX_TURNS` (24) instead of landing in "Errored personas" with no
  report. Bounded: one retry, production seats only.
- **Model pins imply their runtime** (`runtime_for_model`). `/model opus`
  and `--model opus` now resolve the pinned model's runtime through the
  registry (id → alias → identifier → fallback-alias table) and seat the
  turn there; previously the router picked any runtime and the alias
  resolved against the wrong backend or not at all. Vendor shorthand
  (`opus`, `sonnet`, `haiku`) added to the fallback alias table.
- **`OPEN_MAESTRO_RUNTIME` honored by the model router**. The env var
  previously only pinned `create_runtime(None)`; every router call site
  passed `runtime_type=None`, so model selection ignored it. Now it narrows
  candidates exactly like the runtime pin, with a clear error for unknown
  values.

### Fixed
- **Fidelity persona burned its turn budget on serial exploration**
  (follow-up to MSTRO-135/136, seen live as session `40f5aa61` dying at
  `maxTurns: 12` with no report). The fidelity prompt now instructs a
  budget-aware audit: extract citations first, batch independent reads in
  parallel turns, verify line-specific claims with grep/sed instead of
  whole-file reads, cap evidence-gathering at half the budget (stragglers
  become "unverified citation" nits), and always end with the RESULT line —
  a partial verdict beats a report cut off before it.
- **Documentation agent hard-depended on vector-search tools** that most
  runtimes don't expose. The protocol now gates on the tools actually
  appearing in the seat's tool list (the runtime name alone does not
  determine it) and promotes grep-based discovery to a mandatory first-class
  fallback.
- **MCP config-loading tests saw the developer machine's real
  `~/.open-maestro/mcp.json`**; `TestMCPConfigLoading` isolates `HOME` now.
- **Test-suite hermeticity and stale expectations.** `test_runtime.py`
  availability/selection tests now clear provider-specific endpoint keys
  (`ZAI_API_KEY`/`DEEPSEEK_API_KEY`/`DASHSCOPE_API_KEY`) and neutralize local
  Ollama auto-detection so they no longer leak the developer environment;
  `test_openai_sdk_tools.py` doubles emit streaming delta chunks to match the
  runtime's `stream=True` loop; `_client_for_model` now honors an
  already-configured client before the endpointless-cloud-model guard;
  milestone, interactive, pm critic-gate, and events-heartbeat tests were
  updated to the intended current behavior (expanded default plan,
  3-tuple `_resolve_suggested_prompt`, artifact-critic gate, in-place spinner
  elapsed-time refresh).

## [2.1.11] - 2026-10-06

### Added
- **`maestro --review` persists full persona reports to disk**
  (MSTRO-138). Previously only RESULT counts reached the gate ledger
  (`.open-maestro/gates/<sha>.json`) and the register was printed to
  stdout — the quoted findings (blocking confusions, citation mismatches)
  lived only in terminal scrollback, so a later `--interactive` turn could
  not act on them. Each persona report is now written to
  `.open-maestro/reviews/<doc-stem>-<sha>/<persona>.md`, and the register
  prints a `report:` path per persona. Example follow-up inside
  `--interactive`: "resolve the blocking blind-reader findings in
  `.open-maestro/reviews/.../blind-reader.md`". Write is best-effort: a
  filesystem failure logs a warning and never fails the review.

### Fixed
- **`maestro --version` reported a stale 2.1.3**. `__init__.py` carried a
  hardcoded `__version__` that was never bumped alongside pyproject.toml
  (nine releases out of date). `__version__` is now derived from the
  installed package metadata — pyproject.toml is the single source of
  truth — and the editable-install metadata was refreshed, so
  `--version` reports 2.1.11.

## [2.1.10] - 2026-10-06

### Fixed
- **Review personas never seated on low-cost reasoning models**
  (MSTRO-137). `maestro --review` kept falling back to claude-cli seats
  (errored personas returned Claude Code stream-json envelopes) even
  though `deepseek-flash` was configured, keyed, and `reasoning: deep`.
  Root cause: `_seat_persona_runtime` called `select_runtime_for_task`
  with the router's default `min_cost_level=MEDIUM`, and the registry
  marks deepseek `cost_level: low` — so it was filtered out before
  reasoning matching and the third persona degraded to a LIGHT claude
  seat. Personas are auditors, not production workloads; seating now
  passes `min_cost_level=CostLevel.LOW` so the designed rotation
  (k3 → claude-opus → deepseek-flash) actually happens. Verified live:
  with the key exported, the DEEP seat now returns
  `openai-sdk deepseek-flash` instead of raising "no available runtime".

## [2.1.9] - 2026-10-06

### Fixed
- **Persona turn budget still too tight for multi-file citation auditors**
  (MSTRO-136). The 2.1.8 bump to 4 turns wasn't enough: `quote-context`
  errored again with `error_max_turns` at `num_turns: 5` on the 56KB
  IntelInvoiceCoding contract. The persona prompts are file-pointer
  prompts, and the citation personas are multi-file auditors by design
  (fidelity's prompt instructs it to open 12+ cited repo files). Budget
  raised to `PERSONA_MAX_TURNS = 12` — covers read + citation
  verification + answer with headroom while still bounding a stuck agent.
- **Registry correction: `deepseek-flash` `tool_use: false` → `true`**.
  The 2.1.6 entry assumed V3-era "no tools on this endpoint". The
  openai-sdk runtime offers tools regardless of the registry flag (the
  flag only gates routing), and the live calibration run executed read
  tools through the DeepSeek endpoint and caught all six planted
  sentinels — impossible without real file reads. V4.1 handles function
  calling.

## [2.1.8] - 2026-10-06

### Fixed
- **Seated review personas died with `error_max_turns` on CLI seats**
  (MSTRO-135). `maestro --review` on the IntelInvoiceCoding blueprint:
  blind-reader (kimi-cli) completed, but fidelity (claude-cli) and
  quote-context errored with `stop_reason: tool_use` at `num_turns: 2`.
  Personas ran with `max_turns=1`, but the persona prompts point at the
  artifact on disk and CLI-seated agents spend a turn reading the file
  before answering — one turn cut them mid-tool-use. The DeepSeek
  calibration was immune only because that endpoint exposes no tools,
  which masked the bug until a CLI seat tried to read. New
  `PERSONA_MAX_TURNS = 4` applied to the seated paths of `deep_review`
  and `calibrate_persona`; caller-supplied factories keep `max_turns=1`
  verbatim. Regression test added.

## [2.1.7] - 2026-10-06

### Fixed
- **Calibration harness seated on the unconfigured SDK default**
  (MSTRO-134). `calibrate_persona` used `create_runtime(None)` with an
  empty `AgentConfig` when no factory was passed — the same bug class
  MSTRO-132 fixed in `deep_review` — so persona calibration silently
  targeted gpt-4o (no endpoint on most machines) and measured nothing.
  The default path now seats via `_seat_persona_runtime` (capability
  router, configured models, quota-aware); explicit `runtime_factory`
  (tests, targeted model runs like the DeepSeek seat calibration) is
  honored verbatim. Regression test added.

## [2.1.6] - 2026-10-06

### Added
- **DeepSeek V4.1 Flash joins the adversarial panel** (MSTRO-133). First
  vendor added past the original MSTRO-115 no-new-signup constraint, from
  the council seat evaluation: cheapest reasoning-capable seat available
  (~$0.004–0.01 per panel voice), a non-US-lab training corpus for real
  diversity, and the natural successor to the dead GLM balance as the
  panel's cheap voice. New `deepseek-flash` registry entry (openai-sdk,
  `https://api.deepseek.com/v1`, `DEEPSEEK_API_KEY`, reasoning deep,
  `tool_use: false` — the endpoint exposes no tools, so the router only
  seats no-tool work there; OpenRouter alternative documented in the
  entry). Fourth panel seat takes the previously unassigned
  `causal-auditor` lens. `CHAIRMAN_PREFERENCE` unchanged — DeepSeek
  becomes chairman-eligible only after a calibration run proves ranking
  stability, the same bar GLM failed. GLM seat retained (swap decision
  deferred until the ZAI balance decision).

## [2.1.5] - 2026-10-06

### Fixed
- **Deep-review personas seated on the unconfigured SDK default**
  (MSTRO-132). `maestro --review <doc>` and the v2.1.0 auto deep-review
  created persona runtimes via `create_runtime(None)` with an empty
  `AgentConfig`; auto-detect lands on openai-sdk, whose SDK default is
  gpt-4o — a model with no configured endpoint on most machines. Every
  persona errored and the gate FAILed spuriously ("not run on this
  version" for all five ledger profiles). Now `_seat_persona_runtime`
  seats each persona through the capability router
  (`select_runtime_for_task`) on configured models only, preferring a
  DEEP-reasoning profile (hard bar — weak fast models are excluded) and
  degrading to LIGHT when no deep model is configured. Personas rotate
  across configured model families (models already used this pass are
  excluded, reusing when only one qualifies); session-quota-exhausted
  models are skipped, and a run that returns a `quota_exhausted` error
  marks the model on the session circuit and re-seats once on the next
  capable model. Caller-supplied `runtime_factory` (tests, calibration)
  is honored verbatim. 5 regression tests added.

## [2.1.4] - 2026-10-06

### Fixed
- **Review gates silently no-op outside git repos** (MSTRO-127). All
  change-detection behind the gates was git-based (`_detect_changes`,
  `_untracked_blueprint_files`, `_is_new_file`, `_version_marker_changed`),
  so in a project that is not a git repository the artifact-critic gate
  (9.6), the auto deep-review (2.1.0), and the `/complete` ledger checks
  never fired — e.g. `IntelInvoiceCoding` shipped its blueprint with zero
  adversarial review. Now: at turn start the pm captures an mtime/size/
  content snapshot of gate-relevant files (code + markdown; VCS, dependency,
  build dirs, and `.open-maestro` state excluded) — a cheap no-op inside a
  git repo — and outside one `_detect_changes` diffs against that baseline
  (difflib added-line counts) instead of `git diff`. Blueprint "new
  artifact" detection falls back to files absent from the snapshot, so
  pre-existing docs are treated as edits, not new artifacts. The first turn
  seeds the baseline and never reviews pre-existing files. 9 regression
  tests added.

## [2.1.3] - 2026-10-01

### Fixed
- **Swarm planner overrides explicit file-output instructions**
  (MSTRO-126). Session b3591e11: the task said "Write the output to
  `docs/blueprint-design-and-data-contract.md`" but the planner invented a
  `docs/_contract/` fragment layout, so the milestone's required artifact
  was never produced. New rule: the planner keeps full freedom to design
  its own layout (fragments, working folders) for everything else, but an
  explicit output designation is binding — `detect_explicit_output()`
  finds it in the current task text, and `_ensure_explicit_output()`
  appends a final **merge worker** (writer-role agent, target = the exact
  path) whenever no worker already targets it. Deterministic guarantee,
  not planner-LLM compliance; the planner system prompt carries the same
  rule so the merge worker is usually planned natively. Nested-git-repo
  and absolute/URL targets are excluded. 4 regression tests added.

## [2.1.2] - 2026-10-01

### Fixed
- **Swarm resilience** (MSTRO-125), from forensics on session b3591e11
  ($9.99, 2 of 5 workers alive, no usable deliverable, recorded as
  success):
  - **Failed worker slices are re-seated once.** After the fan-out, each
    worker that errored is re-run on a fresh seat; `_run_agent` marks
    quota-exhausted models as workers fail, so the retry lands on the
    next capable model instead of the dead one (the GLM-429 scenario).
    Previously a failed worker's slice silently evaporated and only the
    end-of-run consistency pass noticed.
  - **Partial failure is no longer silent.** The digest now leads with a
    "SWARM DEGRADED" / "CONSISTENCY CHECK DID NOT PASS" banner when any
    worker failed or the consistency pass reported inconsistency, and the
    result metadata records `swarm_degraded`, `swarm_failed_workers`, and
    `swarm_consistency` — so session records and dashboards show the run
    as degraded instead of complete.
  - **Quota signals propagate on partial failure.** Previously
    `quota_exhausted` metadata only surfaced when the entire swarm died;
    now any failing worker's quota signal reaches `pm.handle`'s fallback
    loop. (Worker seating already excluded quota-exhausted models via
    `chain._run_agent`; the gap was recovery, not seating.)
  3 regression tests added.

## [2.1.1] - 2026-10-01

### Fixed
- **Swarm worker anchored on a maestro-internal epic name and drafted a
  contract for the milestone-tracking tooling itself** (MSTRO-124).
  Session b3591e11 (`$9.99`, no usable deliverable): the DTO worker read
  "Project Process epic" (the `default` epic's name in
  `.open-maestro/milestones.yaml`) as the subject matter and produced
  Pydantic DTOs for the tracking machinery, while a sibling worker
  correctly scoped to the actual product — the consistency check caught
  the divergence. `format_prompt_context()` now opens the milestone block
  with a note that epic/milestone names are delivery-process labels
  (workflow stage), not the subject of the task.

## [2.1.0] - 2026-10-01

### Added
- **Auto-invoked deep review for milestone-significant blueprint changes**
  (MSTRO-123). `deep_review` (the 3-persona adversarial audit with
  `GateLedger` recording) previously only ran via explicit
  `maestro --review <doc>` or the `/complete` gate, making it easy to
  forget — the v3.0 blueprint passed the cheap per-turn artifact-critic
  pass while a manual review found 15 issues (13 accepted). The PM turn
  pipeline now runs it automatically (stage 9.7, after the artifact-critic
  tripwire) when a changed artifact matches the blueprint patterns and ANY
  trigger holds:
  - **new artifact** (`git ls-files` detects untracked files that
    `git diff` misses), or
  - **volume**: >= 150 changed lines (`MAESTRO_DEEP_REVIEW_MIN_LINES`), or
  - **version-marker change** (`vN.N` / `Version:` / `supersedes`) in the
    first 50 lines of the document.
  Suppressed when `MAESTRO_DEEP_REVIEW=off`, when no in-progress milestone
  owns blueprint artifacts, or when the ledger already holds a passing
  audit for the artifact's current bytes. The persona register summary is
  appended to the turn result and per-doc verdicts recorded in metadata;
  review failures degrade to a note and never fail the turn. The
  `/complete` ledger gate remains the enforcement point. 7 regression
  tests added.

## [2.0.2] - 2026-10-01

### Fixed
- **Panel chairman synthesis failed permanently on transient runtime errors**
  (`review.panel`, MSTRO-122). Observed live: claude-cli returned
  "API Error: Connection closed mid-response" as result text (without
  `is_error`), which `_run_seat` could not catch, so the panel ended with
  no aggregate verdict — a second degradation on top of any unreachable
  seat. The chairman stage now: (1) treats `API Error…`-prefixed result
  text as a failure, (2) retries each chairman candidate up to
  `CHAIRMAN_ATTEMPTS` (2) with linear backoff, and (3) falls back to the
  next preference-ordered seat (never GLM) before recording an error.
  Three regression tests added.

## [2.0.1] - 2026-09-29

### Fixed
- **Interactive prompt never reappearing after a long-running task**
  (`maestro --interactive`). The Esc-cancel keyboard listener was an
  `asyncio.to_thread` coroutine "cancelled" with `task.cancel()`, which
  does not stop the thread — it only noticed completion via a `task.done()`
  poll (up to 0.2 s late) and then restored termios with `TCSADRAIN`,
  blocking behind the large output drain a long task leaves pending. The
  restore-to-cooked-mode landed inside the *next* prompt_toolkit session's
  raw-mode setup, so the `> ` prompt never painted until the user pressed a
  key to force a repaint. Fixed with a deterministic `threading.Event`
  stop, a named daemon thread, a real `join` before returning to the prompt
  loop, and `TCSANOW` for the restore. Regression test added
  (`tests/test_interrupt_thread.py`).

## [2.0.0] - 2026-09-29

Major release: the adversarial review layer turns maestro from a
single-reviewer pipeline (critic gate) into a disciplined multi-reviewer
system with measured, machine-enforced quality gates.

### Added
- **Adversarial review package** (`open_maestro.review`, MSTRO-115..120).
  Evaluation of the existing critic gate vs. the skill-share review
  discipline vs. karpathy/llm-council concluded: adopt the skill-share
  core, keep the critic gate as the per-turn tripwire, skip llm-council as
  a dependency (only its anonymized cross-ranking was cherry-picked).
  - `review.personas` (MSTRO-116): the 7 fresh-agent audit prompts
    (blind-reader, fidelity, quote-context, noise, delta, reader-roles,
    comment-audit), model-agnostic with `$placeholder` substitution and a
    mandatory machine-parseable `RESULT k=v` line per persona.
  - `review.gate` (MSTRO-117): RESULT-line parser, numeric THRESHOLDS per
    persona, append-only audit ledger keyed to the artifact's sha256 (any
    edit stales every record), and the carry/delta mechanism — full
    re-audits never converge, so after a round the delta persona checks
    only changed lines and clean records carry to the fixed version.
  - `review.panel` (MSTRO-118): multi-model adversarial panel seated with
    the 3 existing model families (Kimi k3 / GLM-5.3-Flash / Claude), one
    attack lens per seat (Falsifier / Practitioner skeptic / Steel-Man).
    Claims are routed E/Q/W/J before the panel — only Judgment questions
    go to models; Executable/Queryable/Web claims are returned for tool
    verification. Cross-ranking is anonymized; reviewer scoring discards
    zero-finding voices (never counted as approval); errored/silent seats
    are recorded as unreachable — silence is not consent. The chairman is
    never GLM; GLM balance exhaustion degrades to the remaining voices.
  - `review.blueprint` (MSTRO-119): deep review of design artifacts
    (`docs/blueprint*`, `*contract*`, `*spec*`) with the admission bar
    (a finding enters the register only with a failure scenario or a
    security/tenant-isolation/audit/irreversibility marker) and the
    "resolve, don't list" resolution pack for round 2+. Wired into the
    milestone exit: `/complete` on a milestone owning blueprint artifacts
    refuses while the review gate is unpassed (`--force` overrides), and
    `/next` shows a non-blocking advisory. New CLI command:
    `maestro --review <doc.md>` runs the gating personas and exits
    non-zero unless every gate passes on the current bytes.
  - `review.calibrate` (MSTRO-120): calibration harness that plants 6
    sentinel-marked defect classes (count-framing, invented-detail,
    overstatement, contradiction, status-flip, wrong-section-ref) into a
    copy of a real artifact and scores per-persona recall deterministically.
    CLI: `python -m open_maestro.review.calibrate <doc> --persona fidelity`.
    A missed defect class requires a prompt change or a code check.

## [1.22.5] - 2026-09-29

### Fixed
- **Direct-action prompts bypass chain/swarm decomposition** (MSTRO-114).
  Tasks like "post these comments to Linear via `mcp__linear__save_comment`"
  were being scattered by the chain planner into recall/verification steps
  (memory-manager, research, product-owner) that never executed the action —
  four attempts produced zero `save_comment` calls. A new
  `is_direct_action()` classifier in `orchestrator/chain.py` detects prompts
  that name an explicit MCP tool, or combine an imperative mutation verb
  (post, create, update, file, close, push, ...) with an external system
  (Linear, Jira, Confluence, ...). Such prompts now go straight to the
  router-selected agent as a single-step run, bypassing both the swarm and
  chain planners; `ChainPlanner.plan()` keeps the same gate as
  defense-in-depth for other entry points, and the planner system prompt now
  forbids adding unrequested recall/verification steps to direct actions.

## [1.22.4] - 2026-09-29

### Fixed
- **Explicit turn-to-turn handoff for execution-style follow-ups**
  (MSTRO-113). Native session resume carries the prior conversation, but in
  a long resumed thread the model can lose anchoring: a follow-up like "ok,
  proceed with the recommended next steps" produced adjacent planning work
  instead of executing the stated recommendations. Each persisted session
  record now keeps the tail of the turn's final text (`last_output`, capped
  at 6k chars), and when a short follow-up matches execution intent
  ("proceed", "continue", "go ahead", "next steps", …) the previous turn's
  Recommendations/Next-steps section (or tail, capped at 4k chars) is
  injected into the prompt as `[Previous turn's output — act on this]`.
  The handoff excerpt is also restored on maestro restart alongside the
  session id, so it works across restarts, not just within one process.

## [1.22.3] - 2026-09-29

### Fixed
- **MCP tool names are no longer stripped from Claude CLI tool flags**
  (follow-up to the Linear posting incident). `_filter_claude_tool_names`
  dropped every name that is not a built-in Claude tool, so `mcp__*` entries
  could never reach `--allowedTools`/`--disallowedTools`. `mcp__`-prefixed
  names and per-server patterns (`mcp__linear__*`) now pass through, and
  when maestro itself passes MCP servers to the runtime, an agent-level
  allowlist is automatically widened with one `mcp__<server>__*` pattern per
  server so user-configured MCP servers are not silently locked out.  Note:
  Claude Code itself does not load MCP servers in `-p` print mode before
  v2.1.221 (anthropics/claude-code#38987), so MCP through the claude-cli
  runtime also requires a Claude Code upgrade; the kimi-cli runtime loads
  MCP in prompt mode today.

## [1.22.2] - 2026-09-29

### Fixed
- **Interactive sessions now survive maestro restarts** (MSTRO-111). The
  backend session id lived only in memory, so restarting `maestro
  --interactive` between turns silently dropped continuity — a follow-up
  like "proceed from the last session" started a fresh backend session with
  no transcript (a generated 39-comment Linear manifest was lost this way:
  the posting turn had no comments in context and no tools connected, and
  nothing reached Linear). On startup, interactive mode now restores the
  most recent *project-scoped* session record (`.open-maestro/sessions`)
  into state and prints a "Resuming session <id> (/reset to start fresh)"
  line; the first turn then natively resumes the backend session
  (`claude --resume` / `kimi -r`). The user-level session dir is
  deliberately excluded so a session from a different project is never
  resumed by mistake.

## [1.22.1] - 2026-09-29

### Fixed
- **Repo clarification no longer fires for document URLs or noun usages of
  action verbs** (MSTRO-110). A prompt comparing a local blueprint against
  an "adversarial review" and linking a Linear document previously triggered
  "Which repository should I analyze?" for two independent reasons: any URL
  (including Linear/Google Docs links) was treated as a git remote, and the
  action-verb matcher substring-matched "review" inside the noun phrase
  "adversarial review". Remote URL extraction now keeps only URLs that look
  like git remotes (`.git` suffix, known forge hosts, or `git@` scp-style),
  and the repo-analysis gate requires an action verb to co-occur with code
  context or an explicit path/URL — a bare verb no longer triggers the
  question. Verb matching also uses word boundaries so derived words like
  "reviewer" don't trip it.

## [1.22.0] - 2026-09-29

### Fixed
- **Swarm heuristic planner no longer writes into code repos from stale
  history tokens.** In interactive mode the prompt carries the full
  transcript ("Conversation so far: … Current task: …"), and the heuristic
  swarm planner's target extraction scanned all of it — a stale, hallucinated
  `DefaultRole_SystemAdministrator.js` token from an earlier turn became a
  write target during two doc-only tasks, and workers appended permission
  rows to `DefaultRole_*.json` inside a cloned repo. Target extraction now
  considers only the current task (`_current_task_text`), and heuristic write
  targets are validated: existing paths inside nested git repositories are
  rejected, and non-existent paths are only allowed under artifact dirs
  (`docs/`, `requirements/`, `prd/`, `tests/`, `scripts/`, `specs/`).
  (MSTRO-109)

### Added
- **Read-only guardrails for pre-dev phases.** Playbook prompts support
  `read_only: true` (tagged on intake-001/002/003, plan-001, design-002);
  the mutating-tool set (Write/Edit/Bash/…) is merged into `blocked_tools`,
  hard-enforced on openai-sdk/claude-sdk/ACP and system-prompt-enforced on
  kimi-cli. Independently, a clone guard snapshots nested git-repo clones
  under the project before each turn and auto-reverts tracked modifications
  (`git checkout -- .`) after the turn while the current milestone is a
  pre-implementation phase (intake through build-planning; active by default
  when no plan exists). Untracked files are never touched. Disable with
  `MAESTRO_CLONE_GUARD=off`. (MSTRO-109)

## [1.21.3] - 2026-09-27

### Fixed
- **Typed prompt text was bold but still white in interactive mode.** The
  session style set only `bold`; it now applies `bold ansiyellow` (or
  `MAESTRO_PROMPT_COLOR`) so typed input matches the amber `>` label. (MSTRO-108)

## [1.21.2] - 2026-09-27

### Fixed
- **Interactive mode crashed on startup with `AttributeError: 'str' object has
  no attribute 'invalidation_hash'`.** The 1.21.1 prompt styling passed
  `style="bold"` as a raw string to prompt_toolkit's `PromptSession`, which
  requires a `Style` instance; the first render crashed. Now wraps the style
  as `Style([("", "bold")])`. Verified in a real PTY: no traceback, and the
  `>` label renders as bold amber (`\x1b[0;33;1m`). (MSTRO-108)

## [1.21.1] - 2026-09-27

### Added
- **User prompts render bold and colored in interactive mode.** Typed input
  and queued `/next` prompt echoes now render as bold amber `> ...` (the same
  visual treatment kimi-cli gives user messages), making your prompts easy to
  spot when scrolling back through session history. The `─── Turn N ───`
  separator renders dim so prompts stand out further. Styling is applied via
  prompt_toolkit and is skipped when stdout is not a TTY or `NO_COLOR` is
  set; `MAESTRO_PROMPT_COLOR` overrides the color (e.g. `ansigreen`).
  (MSTRO-108)

## [1.21.0] - 2026-09-26

### Added
- **Dossier open-items are injected into every prompt.** Open/pending items
  from `docs/**dossier*.md` and `docs/**decision*.md` files (bullets or
  headings containing open/pending/TBD/unresolved/needs-decision, checked
  items excluded) are surfaced to the agent as an `## Open items from
  dossier` block, capped at ~2k chars. Previously a drafted contract could
  claim "none of the gates changes frozen structure" while the project's
  dossier had open items that do — the agent never saw them. (MSTRO-104)
- **Adversarial self-review prompt gated on its prerequisite.** New
  `design-002` — "Adversarially verify the drafted contract" — extracts every
  file:line citation from the produced contract, re-reads each against the
  repo, checks UI-facing values against the highest-authority source, exposes
  net-new elements presented as existing patterns, and outputs P0/P1/P2
  findings to `docs/adversarial-review.md`. Prompts support a new optional
  `after: <prompt-id>` field: the prompt only appears once its prerequisite
  has a run record in `.open-maestro/prompt_history.yaml` or its artifact
  exists on disk. Existing design-002/003/004 renumbered to 003/004/005.
  (MSTRO-105)
- **Artifact-critic gate for design documents.** After a run that creates or
  modifies a `docs/**.md` artifact larger than 50 changed lines, the
  code-critic agent adversarially verifies it (citation re-checks,
  highest-authority source check, net-new exposure) and returns
  `## Verdict: APPROVE/WARN/BLOCK`, mirroring the existing code-critic gate.
  Disable with `MAESTRO_ARTIFACT_CRITIC=off`; threshold override via
  `MAESTRO_ARTIFACT_MIN_LINES`. (MSTRO-106)

### Changed
- **design-001 now requires verified citations and honest anchors.** The
  "Draft data and API contract" prompt requires re-reading every cited
  file:line to confirm it proves its claim, recording the commit SHA of each
  repo checked, labeling each element `[existing]` or `[net-new]`, flagging
  mechanism mismatches (e.g., a MongoDB pattern cited for a SQL Server
  feature), and taking UI-facing values from the highest-authority source
  (prototype bundle beats PRD). Motivated by a human adversarial review that
  found 13 defects in a contract drafted without these checks. (MSTRO-103)
- `pyproject.toml` version aligned with the released version (was stale at
  1.15.5; built wheels misreported their version).

## [1.20.6] - 2026-09-25

### Fixed
- **Single-agent turns are guarded against read-only-worker role-play.** On a
  single-agent design turn, the model recalled prior swarm worker outputs
  ("read-only task, as assigned … handing off to the lead agent") and
  replayed that role: it analyzed, wrote nothing, and handed off to a lead
  that does not exist in a non-swarm run — the artifact was silently lost.
  Two guardrails now apply: (1) single-agent runs (interactive and CLI) get a
  sole-agent directive appended at the pm layer — "you are the only agent,
  there is no lead, write every artifact yourself" — while chain/swarm runs
  are unchanged; (2) interactive turns whose result matches a no-write
  handoff pattern but whose prompt's `artifact_target` file was never
  created print a warning naming the file and the recovery reply, instead of
  silently accepting the turn. (MSTRO-101)

## [1.20.5] - 2026-09-25

### Changed
- **Work epics scaffold with Intake & Discovery and Execution Planning
  skipped.** These are project-wide phases executed once on the default
  track, but every work epic used to get full copies that showed up as
  phantom in-progress milestones in `/previous`, diluted completion math, and
  had to be closed by hand on every project. New work epics scaffold both
  phases as `SKIPPED` with a note carrying the reactivation command
  (`/track <epic-id>/<milestone-id> in_progress`); `SKIPPED` is already
  excluded from `Epic.completion()` and from `/next`/`/previous` candidates.
  The slot remains in the schema so an epic with genuine epic-level work
  (e.g. the R-01 external-dependency pattern) can reactivate it as the
  parking spot for epic-specific gates. Default track and single-track
  projects are unaffected. Convention documented in
  `docs/milestone-guided-experience-design.md` §3.2. (MSTRO-100)

## [1.20.4] - 2026-09-25

### Added
- **`default_track_only` playbook prompt flag.** Prompts whose artifacts are
  project-wide can now be tagged `default_track_only: true` in a playbook;
  `get_prompts_for_milestone` omits them whenever a concrete non-default
  `epic_id` is requested. Since `/next`, `/previous`, `/prompts`, and
  interactive prompt selection all flow through that one function, flagged
  prompts no longer appear under work-epic tracks anywhere. `epic_id=None`
  (no track context) keeps every prompt.

### Fixed
- **Work-epic runs no longer clobber project-wide design artifacts.** The
  five prompts with fixed project-level artifact targets are now tagged in
  the software-consulting playbook (v1.4.0): plan-002 (`design-decisions.md`),
  design-001 (`blueprint-design-and-data-contract.md`), design-002
  (`template-spec.md`), design-003 (`jira-stories.csv`), and design-004
  (`design-signoff.md`). Previously, running e.g. design-blueprint under a
  work epic would overwrite the default track's ratified output with
  epic-scoped content. Per-epic design needs continue to be satisfied by
  reference (notes pointing at default-track artifacts). (MSTRO-99)

## [1.20.3] - 2026-09-23

### Added
- **intake-004: outcome-aligned epic breakdown prompt.** The software-consulting
  playbook now has an explicit Intake & Discovery prompt
  (`artifact_target: docs/intake/epics.md`) that instructs the agent to
  consolidate PRD scope bullets into 3-6 outcome-aligned epics (CE-1, CE-2,
  ...) rather than the old mechanical one-bullet-one-epic derivation, which
  produced component-sliced epics (E1-E8) with no outcome ownership and heavy
  cross-epic dependencies. The auto-generated fallback remains only for when
  the doc is missing. Playbook version bumped to 1.3.0.

### Fixed
- **Epic parser accepts consolidated `CE-n` numbering.** `_parse_epics` now
  recognizes `CE-1`/`CE1` prefixes (with or without dash, any case) in both
  headings and table rows, accepts h2-h6 headings, and builds work epic ids
  from the lowercase prefix (`ce1-data-foundation`), so revised
  consolidated-epic docs scaffold correctly. E-prefixed behavior unchanged.
  (MSTRO-98)

## [1.20.2] - 2026-09-22

### Fixed
- **Interactive turns no longer duplicate the conversation on native session
  resume.** When a turn resumes a backend session (e.g. `kimi -r <id>`), the
  model already holds the full conversation natively, but Maestro was also
  injecting the entire "Conversation so far" transcript as the new message —
  doubling a long session and degrading the model's anchoring on the most
  recent turns (symptom: a follow-up turn re-scanning files for context that
  was in its own prior output). Turns that natively resume a healthy
  same-runtime session now send a current-task-only prompt; the transcript
  remains the continuity mechanism for fresh sessions and when kimi resume is
  known-broken.
- **`state.session_runtime` records the actual post-fallback runtime.** It
  previously stored the turn's pre-fallback runtime, so after a quota-fallback
  swap (e.g. glm → kimi) the next turn's resume check compared against the
  wrong runtime and could resume a session the selected runtime never owned.
- **Debug visibility for prompt assembly.** Interactive turns log the
  assembled prompt size, injected history turns, and resume flag at debug
  level, so future continuity issues can be diagnosed from a debug log
  instead of guesswork.

## [1.20.1] - 2026-09-22

### Fixed
- **Quota fallback now works inside chain and swarm runs.** A quota failure
  in a chain step or swarm worker previously dead-ended the turn: the
  aggregated result rebuilt its metadata from scratch, dropping the
  `quota_exhausted` tag before `pm.handle` could react. Both `_synthesize`
  implementations now propagate the tag from a failing step/worker, so the
  pm-level retry loop (re-select the cheapest capable model with exhausted
  ones excluded, up to 3 fallbacks) engages for multi-agent runs too.
- **Chain/swarm workers remember quota failures mid-run.** `_run_agent`
  marks the session circuit as soon as a step fails on quota and excludes
  circuit-marked models from per-worker selection, so parallel workers that
  have not picked a model yet avoid the dead one instead of failing in
  sequence.
- **Deterministic `--allowedTools` for claude-cli.** The tool list was
  filtered through a `set`, so the CLI argument's tool order changed with
  hash randomization on every process. The filter now preserves the
  caller's order (and `test_allowed_tools_and_blocked_tools` is stable
  instead of failing ~3 runs in 4).

## [1.20.0] - 2026-09-20

### Added
- **Graceful degradation when a model's quota is exhausted.** When a
  provider error means "this credential/model is out of service" (HTTP
  401/403/402, or a balance/quota/credit message in the error body), the
  openai-sdk runtime tags the result with `quota_exhausted` metadata instead
  of dead-ending the turn. `pm.handle` then marks the model on a
  session-scoped circuit breaker, emits a `model.quota_exhausted` event
  (rendered in interactive mode as
  `⚠ '<model>' quota exhausted (<reason>) — falling back to '<fallback>'`),
  and re-runs the task with the next capable model — up to 3 fallbacks per
  turn. Selection layers (`CapabilityRegistry.match`,
  `ModelResolver.select_for_task`, `select_runtime_for_task`) accept an
  `exclude` set, and interactive per-turn selection also skips
  circuit-marked models, so subsequent turns don't pick the dead model
  again. Transient errors (stream read failures) and plain rate limits keep
  their existing retry behavior; context-overflow errors are unaffected.
- **Optional deterministic fallback chain.** Users who want a fixed
  secondary model can set `routing.fallback_order:` (a list of model ids or
  runtime identifiers) in `~/.open-maestro/capabilities.yaml`. When the
  failed model appears in the chain, the next available, capable entry is
  used; otherwise fallback stays fully automatic (cheapest capable model
  with exhausted ones excluded). If nothing can take the task, the result
  carries a warning explaining the exhausted quota and suggesting `/model`.

## [1.19.0] - 2026-09-20

### Added
- **Source-load routing: model/runtime selection now weighs the number of
  artifacts and sources a task touches, not just prompt keywords.** A task
  like "check the artifacts for consistency" carries no deep-reasoning
  keyword, so its profile stayed LIGHT and GLM-5.3-Flash won on cost — then
  died mid-stream 11 minutes in because the task actually spanned 5
  milestone artifacts, 5 repos, and 33 recalled memories. The new
  `orchestrator/load.py` measures artifact paths (prompt references ∪
  current-milestone artifacts from `milestones.yaml`), git repos under the
  project, and recalled memory count, and raises the task profile
  accordingly: MEDIUM (≥3 artifacts or ≥2 repos) bumps the context estimate
  to 64k; HIGH (≥6 artifacts, ≥3 artifacts + ≥2 repos, or ≥25 memories)
  bumps it to 128k and requires DEEP reasoning — the hard bar that excludes
  light-reasoning cheap models. Applied at both decision points: per-turn
  runtime selection in interactive mode, and inside `pm.handle` after
  memory recall (so one-shot runs and chain/swarm workers inherit it).
  A `source.load` event and a `/plan` profile line make the decision
  visible. Thresholds are user-editable via `routing.load_thresholds:` in
  `~/.open-maestro/capabilities.yaml`.

## [1.18.2] - 2026-09-20

### Fixed
- **The interactive "Thinking" spinner scrolled instead of redrawing in
  place.** `ProgressIndicator` padded every frame to a hardcoded 80 columns
  with spaces. On any terminal narrower than 80 columns each frame wrapped
  onto a second line, `\r` could no longer reach the frame's start, and
  every subsequent frame pushed a new line into the scrollback. Rendering
  now erases the line with ANSI `\x1b[2K` (no padding), caps the message at
  the actual terminal width, and truncates with an ellipsis otherwise.
  Verified under a 60-column PTY: 10 frames rendered, zero newlines.

## [1.18.1] - 2026-09-20

### Fixed
- **Interactive prompt now supports real multi-line editing, par with
  claude-cli/kimi-cli.** The TUI input was a single-line buffer with an
  unreliable Alt+Enter binding: Option+Enter did not insert newlines and
  arrow keys could not traverse multi-line drafts or recall history across
  sessions. The input is now a multi-line prompt_toolkit buffer:
  - **Enter submits; Ctrl+J or Option/Alt+Enter inserts a newline**
    (Option+Enter works in iTerm2, VS Code, and Ghostty, which send
    Esc+Enter; Terminal.app needs "Use Option as Meta key" enabled).
  - **Up/Down move the cursor within a multi-line draft** and fall back to
    history navigation at the first/last line.
  - **History persists across sessions** via prompt_toolkit FileHistory on
    `~/.open-maestro/interactive_history` — previously the TUI path used an
    in-memory history, so arrows only saw the current session.
  - **Ctrl+C cancels the current input.** The old lone-Escape cancel was
    removed: with an Escape-prefixed sequence registered it fired only
    after a multi-second timeout and swallowed the next typed character.
  - GNU readline history save is now skipped in TTY mode so it can no
    longer truncate the prompt_toolkit history file at exit.
  Verified end-to-end with PTY keystroke tests against the installed CLI.

## [1.18.0] - 2026-09-20

### Added
- **`/status` (alias `/where`) interactive command: a deterministic "where did
  we leave off" summary with no LLM call.** Previously the only way to answer
  that question was a natural-language prompt routed through agent selection,
  memory recall, and inference — observed costing ~290k tokens for a one-line
  answer. The command now renders directly from disk: milestone progress with
  current/next milestone and active blockers (from `milestones.yaml`), the
  last 5 sessions with prompt summary, agent, and model (from the session
  store), and the mission excerpt from `.open-maestro/resume-log.md` when a
  context-pressure handoff exists. Tracked as MSTRO-97.

## [1.17.6] - 2026-09-20

### Fixed
- **A critical context threshold discarded the user's answer.** When the
  cumulative session token count crossed the budget's critical threshold
  (default 90% of 200k), `pm.handle` replaced the turn's result text with the
  context-pressure resume log — a scaffold with literal "(Populate this
  section...)" instructions — so a simple question like "where did we leave
  off?" returned a meaningless template instead of the answer. The answer is
  now kept; the resume log (with the answer embedded under key findings) is
  written to `.open-maestro/resume-log.md`, and a short critical-budget
  warning is appended to the response.

## [1.17.5] - 2026-09-17

### Fixed
- **Chain mode was silently OFF in every interactive session, making the swarm
  unreachable.** The help text said "default: on", but interactive state was
  built with `getattr(args, "chain", True)` — and argparse's `store_true`
  default of `False` is a real attribute, so the `getattr` fallback never
  fired. With chain off, `pm.handle` never entered the decomposition block
  and `/plan` always showed the single-agent plan. `--chain` is now
  `--chain/--no-chain` with a tri-state default: interactive starts with
  chain on (unless `--no-chain`), one-shot behavior is unchanged (off unless
  `--chain`).

## [1.17.4] - 2026-09-17

### Fixed
- **`/plan <prompt>` on one line silently discarded the prompt.** The command
  parser treated `plan` as the command, armed the show-plan flag, and dropped
  the rest of the line — the user saw "Next response will show the execution
  plan." and then nothing, because their prompt no longer existed. The
  one-line forms `/plan <prompt>` and `/dry <prompt>` now arm the flag *and*
  plan/dry-run the remainder in the same turn. A bare `/plan` or `/dry` keeps
  its next-turn semantics.

## [1.17.3] - 2026-09-17

### Added
- **Live swarm observability in interactive mode.** A swarm was only
  distinguishable from a chain by the final `# Swarm result` header — the
  per-worker delegation lines deduped away when workers shared an agent.
  The executor now emits `swarm.started` and the interactive progress
  handler renders the full swarm lifecycle:
  `→ Swarm: 4 parallel workers (leader digest first; consistency pass after)`,
  `→ Worker 1/4 'documentation' started`, `→ Worker 1/4 'documentation' done`,
  and `→ Worker 2/4 'engineer' FAILED` for errored workers.

## [1.17.2] - 2026-09-17

### Fixed
- **Swarm folder expansion missed "/docs"-style paths.** Users naturally write
  "/docs" to mean "the docs folder in this project"; the heuristic checked it as
  an absolute filesystem path, didn't find it, and silently declined — leaving
  the prompt to the LLM planner coin flip. Leading-slash and "./" tokens that
  don't exist as absolute paths are now retried relative to the working
  directory, so prompts like "inspect the files in /docs or /docs/intake and
  update what needs updating" deterministically fan out.

## [1.17.1] - 2026-09-17

### Added
- **Swarm heuristic: folder expansion.** Prompts that name a directory (e.g.
  "inspect the files in docs/ and update whatever needs updating") now fan out
  to one worker per markdown artifact found inside it, instead of requiring an
  explicit file list. Explicit file paths and expanded folders combine and
  deduplicate (naming a folder and its subfolder does not duplicate workers);
  nonexistent directories are ignored. The existing ≥3-target and
  no-shared-write-target invariants still apply.

## [1.17.0] - 2026-09-17

### Added
- **Swarm mode: parallel multi-agent fan-out with per-worker cross-model routing**
  (Jira MSTRO-95). Inside chain mode, the new `SwarmPlanner` (`orchestrator/swarm.py`)
  detects tasks with 3+ independent targets (files to update, repos to analyze,
  angles to evaluate) and fans them out to parallel workers via `asyncio.gather`
  instead of running them sequentially. Each worker independently goes through the
  capability-aware runtime/model selection in `ChainExecutor._run_agent`, so workers
  in the same swarm can run on different vendors/models concurrently — unlike
  kimi-cli's same-model subagents or claude-mpm's Claude-only world.
  - **Three-phase shape**: optional leader step (cheap model) condenses the
    triggering evidence into a shared digest; parallel workers each get the digest
    plus their own target; optional consistency pass verifies cross-references
    among the produced artifacts (WARN-only).
  - **Write-conflict safety**: if two workers would write the same file, the swarm
    plan is rejected and the task falls back to the sequential chain.
  - **Error isolation**: one worker's failure is reported in the grouped synthesis
    without killing the other workers.
  - **No per-worker critic gate** (parallel diffs can't be attributed); one
    aggregate code-critic pass reviews the combined diff after the fan-out.
  - **Concurrency cap** via `MAESTRO_SWARM_MAX_WORKERS` (default 4).
  - **Triggers**: LLM planner first (with file-path heuristic fallback that catches
    "update these N docs" shapes offline); requires ≥3 workers.
  - New toggles: `/swarm` in interactive mode (default on) and `--swarm/--no-swarm`
    CLI flag; master switch remains `/chain` / `--chain`.

## [1.16.7] - 2026-09-17

### Fixed
- **"Prompt exceeds max length" (Z.ai code 1261) killed the turn.** GLM-5.3-flash
  publishes a 1M-token context, but plan tiers can cap the per-request prompt far
  below that; a moderately sized request died with
  `400 {'error': {'code': '1261', 'message': 'Prompt exceeds max length'}}`.
  The openai-sdk runtime now detects length-related 400s, compacts the
  conversation once (system messages kept, older messages replaced with
  placeholders, oversized kept contents head/tail-truncated, orphaned tool
  results dropped), and retries — instead of failing the turn.
- **Stream-retry exhaustion no longer returns an empty result.** If every
  stream attempt fails and the loop exits without an outcome, a clear
  `RuntimeError` is raised rather than silently producing blank output.

## [1.16.6] - 2026-09-17

### Added
- **Turn-budget nudge in the openai-sdk tool loop.** The model cannot see
  Maestro's turn counter, so on broad tasks (e.g. "evaluate this prototype and
  update the analysis documents") it kept exploring right up to the turn cap
  and died with "Reached the maximum number of tool turns without a final
  response" — 32 turns spent, no artifact produced. At ~75% of the cap a
  system message now tells the model to stop exploring and produce its final
  response/artifact with what it has.

## [1.16.5] - 2026-09-17

### Fixed
- **Empty retry warnings.** The stream-retry log line rendered the raw
  exception, and timeouts like `httpx.ReadTimeout` stringify to an empty
  string — producing `WARNING: OpenAI stream attempt 1/3 failed (); retrying`.
  It now falls back to the exception type name (e.g. `ReadTimeout`), matching
  the error-message fix in v1.16.1.

## [1.16.4] - 2026-09-17

### Fixed
- **Cloud models without a configured endpoint were wrongly selectable when
  an unrelated provider's key was set.** `_openai_sdk_cloud_available()`
  treats any model-specific endpoint key (e.g. `ZAI_API_KEY` for GLM) as
  "cloud available", so endpoint-less models like `qwen-max` were marked
  available; their requests then fell back to the autodetected Ollama client
  and died with `404 model 'qwen-max' not found` — retrying every turn.
  Model-level availability now requires generic `OPENAI_API_KEY` /
  non-local `OPENAI_BASE_URL` for endpoint-less cloud models.
- **Clear error instead of cryptic per-request 404.** The openai-sdk runtime
  now raises a configuration error up front when a non-local model has no
  endpoint and no generic credentials, rather than silently routing the
  request to Ollama.
- **Alibaba qwen entries now declare their DashScope endpoint**
  (`https://dashscope.aliyuncs.com/compatible-mode/v1`, key env
  `DASHSCOPE_API_KEY`), so they become selectable when a DashScope key is
  actually configured.

## [1.16.3] - 2026-09-17

### Changed
- **Spec-driven development hardening in the software-consulting playbook.**
  Six prompt changes closing gaps found while comparing intake syntheses:
  - `plan-001` now re-verifies the intake synthesis's load-bearing claims
    (reuse assets, contract assumptions) against the actual repos before
    building the execution plan, so wrong "verified" claims from intake get
    flagged instead of propagated.
  - `design-003` must verify coverage before finishing: every PRD FR, every
    Section 4 measurable target, and every open question needs a story or an
    explicit disposition — closes the omission hole where dropped
    requirements (e.g. a missed business target) silently propagated.
  - `build-002` traceability matrix now covers measurable targets and NFRs
    in addition to FRs.
  - `impl-001`/`impl-002`/`impl-003` must read the frozen spec artifacts
    (`docs/blueprint-design-and-data-contract.md`, `docs/template-spec.md`)
    before implementing, and must record contradictions in
    `docs/spec-deviations.md` instead of silently deviating — adds the
    spec-drift loop the implementation prompts lacked.

## [1.16.2] - 2026-09-17

### Fixed
- **Org URLs pasted into the single-repo clone prompt now redirect to the org
  flow.** Pasting `https://github.com/<org>` into "Clone remote repo..." (or
  having it extracted from prompt text) failed with gh's confusing
  "Could not resolve to a Repository" error. Single-segment github.com URLs
  are now detected as org pages and automatically switch to the org
  multi-select flow with the org prefilled, so the user picks real
  repositories from the list.

## [1.16.1] - 2026-09-17

### Fixed
- **openai-sdk turns killed mid-stream by the 600s read timeout.** The OpenAI
  client defaulted to a 600s per-operation read timeout, which fired while
  streaming providers (z.ai GLM, Ollama) were still generating after a long
  silent stretch — the whole turn died with `httpx.ReadTimeout` after ~10
  minutes of work. When no `timeout_seconds` is configured, clients now use an
  explicit `httpx.Timeout` with a 30-minute read window (connect still 10s).
- **Transient stream failures are retried.** Read timeouts, connection drops,
  and 5xx/429 responses during a streaming request are retried up to 3
  attempts (the request is re-issued, since a dead stream cannot be resumed),
  with backoff. Previously the first mid-stream hiccup ended the turn.
- **Empty API error messages.** `httpx.ReadTimeout` stringifies to an empty
  string, so turns died with a bare "OpenAI API error:" and no explanation.
  The error now falls back to the exception type name when the message is
  empty.

## [1.16.0] - 2026-09-17

### Added
- **Org-based multi-repo selection in the repo picker.** New "Select repos
  from a GitHub org..." option: enter an org name or URL, maestro lists the
  org's repos via `gh repo list`, and a checkbox TUI lets you pick the
  candidates (press `a` to toggle all). Selected repos are cloned into a
  folder of your choosing (default `<cwd>/<org-slug>/`, existing checkouts
  are reused) and the analysis prompt is clarified to that folder with the
  repo list, so impact analysis can identify which repos are actually
  relevant. Esc cancels from every step.

### Fixed
- **Private GitHub repos can now be cloned through the picker.** All clone
  paths (single-URL and org multi-select) route through a shared `_clone_repo`
  helper that uses `gh repo clone` when the gh CLI is available, supplying
  authentication that plain `git clone` cannot provide non-interactively
  (the machine had no git credential helper configured).

## [1.15.6] - 2026-09-17

### Fixed
- **Clone failures now surface git's own error.** The interactive repo picker's
  clone path swallowed `git clone` stderr and reported only the bare exit code
  ("returned non-zero exit status 128"), which hid the real cause. The
  `CalledProcessError` handler now prints git's stderr (e.g. GitHub's
  "repository not found") and adds a hint when the URL looks like an
  organization page rather than a repo.

## [1.15.5] - 2026-09-17

### Fixed
- **Playbook repo picker suppressed by the follow-up matcher.** The docs-only
  playbook gate (v1.15.4) kept the follow-up check as a safety net, but
  `_looks_like_follow_up` substring-matches question prefixes ("what is")
  that appear verbatim inside canned prompts ("Scope IN/OUT (what is
  explicitly included and excluded)"), classifying fresh playbook tasks as
  questions about past work and never showing the picker. Playbook prompts
  queued from `state.pending_prompts` are fresh tasks by construction, so
  the follow-up check is removed from that branch (it still applies to
  free-text input). Regression test uses the real canned prompt text.

## [1.15.4] - 2026-09-11

### Added
- **Repo picker for code-dependent playbook prompts in docs-only folders.**
  Playbook prompts stay scoped to the current project by default, but when a
  prompt's deliverable depends on source code (reuse assessment, codebase
  verification — detected via code-dependent keywords) and the current
  directory contains no source files, the repo picker now fires so the user
  can point the analysis at the real codebase (e.g. running intake prompts
  from a docs-only `M3RFP/*` folder instead of the codebase clone). The
  picker is evaluated before the generic repo-analysis classifier so canned
  prompts don't need action verbs to qualify; follow-up detection still
  applies. A cheap directory probe (extension match, dependency dirs skipped,
  depth-limited) decides whether the current folder "has code".
- **intake-001 playbook prompt now requires a "Superseded decisions" section**
  (anything the PRD marks resolved, rejected, or "must not be built"), after
  two independent model runs dropped all four resolved OQ decisions — the
  single most intake-relevant content in PRD §10.

## [1.15.3] - 2026-09-11

### Added
- **Per-step critic gate for chain execution.** Code-mutating chain steps now
  get the same adversarial review as single-agent turns. Before each mutating
  step the executor snapshots git HEAD plus a diff baseline; after the step it
  reviews only the delta that step produced, using the step purpose as the
  spec (preserving per-agent attribution). BLOCK verdicts are surfaced loudly
  and never auto-revert. The significance filter (`should_trigger`) still
  applies, so analysis/doc steps stay free, and the existing opt-out
  (`--no-critic-gate` / `MAESTRO_CRITIC_GATE=off`) disables the chain gate
  too. Refactored step execution into a shared `_run_agent()` used by both
  the step loop and the critic pass. Ref: MSTRO-90.

## [1.15.2] - 2026-09-11

### Added
- **Per-model pricing for cost estimates.** `Capabilities` now carries optional
  `price_input_per_million` / `price_output_per_million` fields, and the
  openai-sdk runtime computes `cost_usd` on `AgentResult` from the resolved
  model's registry prices (previously always `None` for that runtime). GLM
  5.3-Flash is priced at $0.15 / $0.50 per million input/output tokens. The
  reported `input_tokens` now uses the latest turn's prompt count instead of
  summing per-turn counts (which double-counted the conversation).

## [1.15.1] - 2026-09-11

### Fixed
- GLM-5.3-Flash endpoint URL corrected to Z.ai's documented OpenAI-compatible
  path `https://api.z.ai/api/paas/v4` (the previously shipped
  `https://api.z.ai/v1` returned 404, found during first live smoke test).

## [1.15.0] - 2026-09-11

### Added
- **GLM-5.3-Flash (Z.ai) as the cheap cloud workhorse.** New registry entry
  (`glm-5-3-flash`, `fast` alias) with a per-model endpoint override: the
  openai-sdk runtime talks to `https://api.z.ai/v1` using the `ZAI_API_KEY`
  env var, independent of the global `OPENAI_BASE_URL`/`OPENAI_API_KEY`.
  Set the key and routine implementation/review/synthesis tasks route to it;
  deep-reasoning prompts still route to premium tiers (K3/Opus). OpenRouter
  alternative documented in `default_capabilities.yaml`.
- Per-model `endpoint` support in the capability registry (user-editable via
  `~/.open-maestro/capabilities.yaml`): any model can declare its own
  OpenAI-compatible base URL and API-key env var.

### Changed
- Interactive and chain routing no longer apply a MEDIUM cost floor: capability
  scoring already enforces minimum reasoning/coding bars, so cheap capable
  models take routine tasks. Behavior change: `fast`-class models (GLM-5.3
  Flash, Haiku, gpt-4o-mini, kimi-for-coding, qwen-coder-plus) are now
  eligible for light/medium tasks they were previously excluded from.
  One-shot CLI runs keep the `--cost-preference` flags unchanged.

## [1.14.0] - 2026-09-11

### Added
- Supabase dashboard publisher for the Lovable-hosted merven.ai frontend:
  `maestro --publish-dashboard supabase` upserts the dashboard snapshot into the
  Supabase `maestro_dashboards` table via the REST API. On first publish a
  random per-project token (>=32 chars) is generated and persisted to
  `.open-maestro/config.yaml` (`dashboard.project_token`); later publishes
  reuse it. Credentials come from `MAESTRO_SUPABASE_URL` and
  `MAESTRO_SUPABASE_ANON_KEY`; the public render URL is
  `MAESTRO_DASHBOARD_PUBLIC_BASE` (default `https://merven.ai/dashboard`) plus
  the token. See `docs/dashboard-infra-setup.md`.

## [1.13.0] - 2026-09-11

### Added
- Orchestrator-enforced code-critic gate: after an implementation turn that
  changes >50 code lines or >1 source file, a `code-critic` review pass runs
  automatically and appends an APPROVE/WARN/BLOCK verdict with top findings to
  the result (metadata: `critic_verdict`). Disable with `--no-critic-gate` or
  `MAESTRO_CRITIC_GATE=off`. (MSTRO-83)

### Fixed
- Write-step handoff writer selection was task-blind: the first `engineer`-role
  agent in registry load order won regardless of the task. Writers are now
  ranked by task relevance via `registry.select()` scoring; engineer-first load
  order is only a fallback. (MSTRO-84)
- `playwright` skill added to the `qa` agent (qa-002 E2E prompt routes there).

## [1.12.0] - 2026-09-10

### Added
- Security scan gate: new `impl-005` "Run security scan gate" prompt at the end
  of the implementation milestone — gitleaks (secrets), semgrep (SAST),
  npm audit / pip-audit (SCA by manifest), syft CycloneDX SBOM. Missing tools
  are recorded as skips, never fail the gate. Implementation milestone exit
  criterion requires a scan report with no unaddressed critical/high findings.
  (MSTRO-79..82)
- Security-scanning skill references: `tooling-matrix.md` (per-ecosystem
  install/run/coverage/false-positives) and `supply-chain-and-sbom.md`
  (CycloneDX vs SPDX, delivery targets, accuracy rules); four dangling
  reference links pruned.
- `agents/security.md` now leads with gitleaks/semgrep as the primary scanning
  paths; the regex protocol is the documented fallback.

## [1.11.0] - 2026-09-08

### Added
- Multi-line prompt input in interactive mode (Ctrl+J / Alt+Enter) for pasting
  multi-line content.

### Changed
- Streamlined milestone playbook (playbook 1.1.0): removed duplicate `plan-003`
  (repo/service impact map already produced by plan-001); the
  "Write the output to {artifact_target}." line is now auto-appended by
  `PromptTemplate.render()` instead of repeated in 21 prompts; `impl-004` names
  the impl-001 501-stub handoff contract; `intake-001` keeps an uncapped
  overflow list for open questions beyond the top 5-8.

### Fixed
- No longer asks for a repo path on follow-up questions about prior analysis.
- Ollama tool calls emitted as content text are now executed.
- Architectural analysis prompts are classified as deep reasoning; tool-using
  deep-reasoning tasks route to the strongest local model.

## [1.10.3] - 2026-09-01

### Changed
- Added `local` alias to `ollama-qwen2-5-coder-32b` in the default capability
  registry. When `/local` or `--prefer-local` is active, standard-tier tasks now
  prefer the 32B model over smaller fast models like `qwen3:8b`.
- Expanded `docs/DEPLOY.md` guidance on local model selection:
  - `deepseek-coder-v2` is required for deep-reasoning / architectural tasks.
  - `qwen2.5-coder:32b` is recommended for standard coding and analysis.
  - `qwen3:8b` should be reserved for fast, cheap tasks only.

### Fixed
- Synchronized package metadata (`pyproject.toml`) and `docs/DEPLOY.md` version
  references with the runtime version (`1.10.3`).

## [1.9.5] - 2026-09-01

### Added
- Interactive permission-based escalation from local to frontier models.
  - New `/local` command in `maestro --interactive` toggles local preference.
  - When local preference is on and no capable local model exists, Maestro asks
    for approval before switching to the proposed frontier runtime/model
    (e.g. `kimi-cli/kimi-code/k3`).
  - New CLI flag `--ask-escalate` starts interactive mode with this behavior
    already enabled.

## [1.9.4] - 2026-09-01

### Changed
- Expanded `docs/DEPLOY.md` with a complete 5-step local-model setup guide:
  install/start Ollama, pull a model, install the `openai` package, point Maestro
  at the local endpoint, and run with `local-smart` / `local-reasoning` aliases.

## [1.9.3] - 2026-09-01

### Added
- Registered two higher-capability local/self-hosted models in the default
  capability registry:
  - `ollama-qwen2-5-coder-32b` (alias `local-smart`) — strong open-weights
    coding model comparable to Claude Sonnet 3.5 on many benchmarks.
  - `ollama-deepseek-coder-v2` (alias `local-reasoning`) — large MoE coding
    model for harder reasoning tasks.
  Both are consumed through the existing `openai-sdk` runtime via Ollama's
  OpenAI-compatible endpoint.

## [1.9.2] - 2026-09-01

### Fixed
- Hardened milestone loading against hand-edited or agent-written status values.
  - `MilestoneStore.load()` now normalizes common aliases before validation:
    `complete`/`done` → `completed`, `in-progress` → `in_progress`,
    `not-started` → `not_started`.
  - This fixes the crash when a milestone file contained `status: complete`
    instead of `status: completed`.

## [1.9.1] - 2026-08-31

### Changed
- Renamed the work-epic status label from **Not Started** to **Planning**.
  - Milestones 1–4 still map to this phase; only the display label changed.
  - The 3-step tracker now reads: Planning → In Progress → Complete.

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
