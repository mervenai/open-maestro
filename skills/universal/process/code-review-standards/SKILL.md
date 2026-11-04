---
id: code-review-standards
name: Code Review Standards
tags: []
---

# Code Review Standards

## Purpose

This skill defines the structured checklist that the `code-critic` agent applies during
Stage 4 of the code production pipeline. The checklist is severity-tagged so that PM
and engineer both know exactly which findings block delivery and which are advisory.
Engineers may load this skill for self-review before requesting a critic pass.

The checklist exists because unstructured code review in multi-agent systems produces
inconsistent signal: one critic dispatch flags naming; another flags security; neither
flags the same things. Severity tagging makes the review deterministic across dispatches.

## The Severity-Tagged Checklist

### CRITICAL (must fix, blocks delivery)

- [ ] No secrets, API keys, or credentials hardcoded
- [ ] No SQL injection vectors (parameterized queries only)
- [ ] No arbitrary code execution paths (no `eval`, `exec`, unrestricted `pickle.loads`)
- [ ] Authentication/authorization not bypassable
- [ ] No infinite loops without escape conditions

### HIGH (must fix, blocks delivery)

- [ ] Type hints on all public functions and classes
- [ ] mypy --strict passes with zero errors
- [ ] pytest passes with zero failures
- [ ] Test coverage >= 90% on new code
- [ ] No bare except clauses
- [ ] No mutable default arguments
- [ ] No global mutable state
- [ ] No synchronous I/O inside async functions
- [ ] No N+1 query patterns
- [ ] Error cases handled explicitly (not silently swallowed)

### MEDIUM (flag, note in report, proceed)

- [ ] Functions <= 20 lines (prefer <= 10)
- [ ] No nested loops where hash map would reduce complexity
- [ ] list.pop(0) replaced with deque.popleft() where relevant
- [ ] asyncio.gather uses return_exceptions=True where appropriate
- [ ] Async operations have explicit timeouts
- [ ] Docstrings on public methods (Google or NumPy style)
- [ ] No Any types in production code paths

**Efficiency** (see [criteria-efficiency.md](references/criteria-efficiency.md)):

- [ ] No nested loops over two collections that should be a hash-map lookup (O(n*m) → O(n+m))
- [ ] No per-iteration I/O (queries/RPCs/fetches inside a loop) — batch outside the loop (HIGH on hot paths; see "No N+1 query patterns")
- [ ] Repeated deep property/selector resolution cached in a local (no greedy data access)
- [ ] String accumulation in loops uses list+join / StringBuilder, not `+=`
- [ ] No `SELECT *` / over-fetching in production query paths

**Transferability** (see [criteria-transferability.md](references/criteria-transferability.md)):

- [ ] No dead/unreachable code (statements after unconditional return/break/raise; uncalled private members)
- [ ] Long `if/else if` chains (3+ branches on one key) replaced by switch/match or dispatch map
- [ ] No nested `switch`/`match` — extract inner switch to a named function
- [ ] No actively misleading names (name contradicts the value it holds)

### LOW (note only)

- [ ] PEP 8 compliance (black + isort handles this automatically)
- [ ] Variable naming is clear and descriptive
- [ ] No commented-out code left in
- [ ] Import ordering is clean
- [ ] Naming consistency across the change (same concept, same name; consistent casing)
- [ ] No file managing too many responsibilities (see 800-line file limit)

## Verdict Format

Critic output MUST begin with the verdict on the first line, followed by the finding
table, followed by a summary paragraph.

**First line format:**
```
VERDICT: APPROVE
```
or
```
VERDICT: WARN
```
or
```
VERDICT: BLOCK
```

**Finding table format:**

| Severity | File | Line | Issue | Required Fix |
|----------|------|------|-------|--------------|
| CRITICAL | auth.py | 47 | Hardcoded API key `sk-...` | Move to env var; add to `.env.example` |
| HIGH | fetcher.py | 23 | `requests.get()` called inside `async def` | Replace with `await httpx.AsyncClient().get()` |
| MEDIUM | parser.py | 88 | Function is 34 lines | Extract `_parse_headers()` helper |

**Verdict definitions:**

| Verdict | Condition | PM Action |
|---------|-----------|-----------|
| APPROVE | Zero CRITICAL, zero HIGH findings | Proceed to Stage 5 (Security) |
| WARN | Zero CRITICAL, one or more HIGH findings | Proceed to Stage 5 with findings logged to docs handoff |
| BLOCK | Any CRITICAL finding (one or more) | Halt pipeline; surface findings to user; await user direction |

**APPROVE** means the implementation is ready for security review. MEDIUM and LOW findings
in an APPROVE review are passed to the Documentation agent as notes — they do not block
delivery but are preserved for future reference.

**WARN** means the implementation has structural issues that should be fixed but do not
represent exploitable defects or correctness failures. PM proceeds to security review
and appends the WARN finding table to the documentation handoff message. PM also logs
the findings (KB entry or todo) so they are not silently dropped.

**BLOCK** means the implementation has at least one defect that, if shipped, creates a
security vulnerability, data loss risk, or silent failure mode. PM halts the pipeline
immediately, presents the critic finding table verbatim to the user, and awaits explicit
direction. PM MUST NOT auto-retry the engineer without user input.

## Visual & Design-System Review (screen-touching diffs)

These checks activate automatically when a diff adds or changes a screen. They exist
because code-level gates cannot see visual drift: unit tests render no layout, production
builds compile undefined CSS silently, and a review scoped to correctness passes a screen
that looks nothing like the approved prototype. Post-mortem evidence: a dashboard shipped
with 12 visual regions wrong after 76 tests, two reviews, and a fix pass — none of which
looked at a rendered screen.

**Screenshot gate.** UI work is not verifiable without renders. If the engineer did not
attach side-by-side screenshots (build vs prototype at the fixed reference viewport, in
populated/empty/error states), record a HIGH finding: "visual claims unverifiable —
capture via `web-qa` before delivery" and treat every visual claim as unverified.

**Visual fidelity, per region.** Inputs: the prototype, the build screenshots, and the
template spec. Do not read the code first. For each region (header, tabs, tiles, charts,
lists, filter bar, grid, empty, loading and error states, spacing and page background)
record **PASS / FAIL / ACCEPTED-DEVIATION** with the ruling id. Any FAIL without a ruling
blocks. Also open the screen at 400 px and in keyboard-only navigation.

**Design system.**

1. Only CSS custom properties that are defined in the codebase are allowed. Run
   `grep -rhoE 'var\(--[a-z0-9-]+' <module> | sort -u` and check each name against its
   definition. Any undefined name fails — including names with a fallback value.
2. Where the platform has a shared component, it must be used (tabs, stat tiles, selects,
   date ranges, toggles, filter bars, grid overlays). Replacing a shared component with
   native elements is a finding unless a CR says otherwise; cite the precedent
   `file:line` for each shared component used.
3. If a shared component genuinely can't meet the requirement, fix it at the call site or
   raise a CR — do not silently substitute.

**Backend-availability flags.** Every UI call to an endpoint owned by an unbuilt or
undeployed bundle must sit behind a named availability flag defaulting to **false**,
gating both the call and the entry points (links, tabs, widgets). The reviewer lists
every endpoint the screen calls, with its owning bundle and deployed status; a flag that
gates entry points but not the call (or vice versa) is a HIGH finding, as is turning a
flag on without the backend bundle's env-check cited.

## 80% Confidence Filter

A clean review is a valid review. Do not manufacture findings.

Only report issues with >80% confidence they are real problems. Do not flag:
- Style preferences as HIGH or CRITICAL
- "This could theoretically be an issue in an edge case" without specific evidence
- Patterns that look unusual but may be intentional and correct
- Missing features that were not in scope (check the Stage 1 spec)

When confidence is below 80%, note the concern as a question in the summary paragraph
rather than as a finding in the table. Example: "The `process_batch()` function did not
appear to handle empty input — verify whether the caller guarantees non-empty batches."

This filter prevents the critic from becoming a noise generator that trains PM to
ignore findings. Each finding in the table should be actionable: engineer reads it,
knows exactly what to fix, and can do so without asking for clarification.

## Navigation

For detailed criteria with examples:
- **[CRITICAL Criteria](references/criteria-critical.md)**: Detailed explanations and examples for each CRITICAL item
- **[HIGH Criteria](references/criteria-high.md)**: Detailed explanations and examples for each HIGH item
- **[MEDIUM Criteria](references/criteria-medium.md)**: Detailed explanations and examples for each MEDIUM item
- **[LOW Criteria](references/criteria-low.md)**: Detailed explanations and examples for each LOW item
- **[Efficiency Criteria](references/criteria-efficiency.md)**: Algorithmic/data-access patterns (nested loops, fetch-in-loop, greedy access, loop concatenation, over-fetching)
- **[Transferability Criteria](references/criteria-transferability.md)**: Maintainability patterns (dead code, long if/else-if chains, nested switch, naming hygiene)
- **[Verdict Protocol](references/verdict-protocol.md)**: Full PM behavior for each verdict, failure loop templates

> The Efficiency and Transferability criteria are derived from CAST Highlight code
> quality indicators (https://doc.casthighlight.com/), paraphrased with original
> examples.