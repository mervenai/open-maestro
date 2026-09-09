# Code-Critic Gate Plan (orchestrator-enforced review)

Context: the code-critic framework (agent + rubric + 6-stage pipeline skill) is
deployed but advisory only — nothing in `pm.py` dispatches it. This plan makes the
critic pass an orchestration-level gate, triggered automatically after
implementation turns.

Jira: MSTRO-83.

## Design

### Trigger (new step 9.5 in `ProjectManager.handle`, after step 9 result, before step 10)

Run a critic pass when ALL of the following hold:

1. Turn was a real execution (not `dry_run`, not `executed_as_chain` continuation,
   not `result.is_error`).
2. The completed agent is mutating/engineer-class (the gate reviews *implementations*,
   not analyses — mirrors the pipeline skill's post-dispatch trigger).
3. The completed agent is not `code-critic` itself (recursion guard).
4. Source changes are detected in the project working tree (see Detection below).
5. Gate not disabled via `--no-critic-gate` or `MAESTRO_CRITIC_GATE=off`
   (default: on).

### Detection (`_source_changes_detected`)

Heuristic from `code-production-process` skill, made deterministic in code:

- Snapshot `git rev-parse HEAD` before the turn; after the turn use
  `git diff <old>..HEAD --stat` if HEAD moved, else `git diff HEAD --stat`
  (covers staged + unstaged vs last commit).
- Keep files with code extensions: `.py .ts .tsx .js .jsx .go .rs .java .rb .sh .php .cs .cpp .c .h`.
- Exclude docs/config-only changes (`.md .rst .txt .yaml .yml .toml .json .env`
  when no code file also changed — mirrors skill exclusions).
- Trigger threshold: >50 code lines changed OR >1 code file changed
  (<5-line single-file fixes excluded).
- Not a git repo, or git command fails: return False, log at debug. Never crash the turn.

### Critic pass (`_run_critic_pass`)

- Resolve the `code-critic` agent from the registry; if absent, skip with a warning
  (registry is user-editable — the gate must not hard-depend on the default roster).
- Prompt: spec = original user prompt, code context = the diff summary (file list +
  stat, not full diff — the critic reads the repo itself), plus the Context Isolation
  Rule (no implementer framing) which the agent definition already enforces.
- Execute through the same `_execute_agent` path with the turn's resolved model.
- Parse verdict: regex `## Verdict:\s*(APPROVE|WARN|BLOCK)` from the critic's
  structured output; unparseable → treat as WARN ("critic output malformed") and say so.

### Surfacing results

- Append a summary block to the turn result: verdict, findings count by severity,
  top findings (≤5 lines). Consistent with the existing vendor-credit footer.
- Metadata: `critic_verdict`, `critic_findings`, `critic_agent` on the result so
  interactive mode and future dashboard work can use them.
- BLOCK does not auto-revert code (no destructive behavior); it is a loud signal the
  user routes in the next turn. Auto-fix is a separate, future decision.

## Files

1. `src/open_maestro/orchestrator/pm.py` — trigger, detection, critic pass, CLI/env
   flag plumbing.
2. `src/open_maestro/cli.py` — `--no-critic-gate` flag (and interactive `/critic`
   toggle if trivial to add alongside).
3. `tests/test_pm.py` — new `TestCriticGate` class (below).
4. `pyproject.toml` — 1.12.0 → 1.13.0.

## Tests

- Detection unit tests (pure function): code ext kept, docs-only excluded,
  threshold boundary (49 vs 51 lines, 1 vs 2 files), non-repo → False.
- Trigger conditions: mutating agent + changes → critic dispatched; read-only agent →
  no; code-critic turn → no (recursion); `--no-critic-gate` → no.
- Verdict parsing: APPROVE/WARN/BLOCK extracted; garbage → WARN fallback.
- Full-run test: mocked registry + runtime, assert critic result appended to turn text
  and metadata set.

## Non-goals

- No auto-fix/revert on BLOCK.
- No change to the critic agent definition or rubric (they're already solid).
- No pipeline-stage UI; this is one gate, not the full 6-stage pipeline.

## Risks

- **Cost**: one extra model dispatch per implementation turn. Mitigated by the
  threshold heuristic and the opt-out flag.
- **Latency**: critic pass adds its runtime to every implementation turn; acceptable
  for a quality gate, and the user can disable interactively if iterating fast.
