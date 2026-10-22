---
id: bdd-story-authoring
name: BDD Story Authoring
tags:
- maestro
- bdd
- stories
- jira
- planning
- milestones
---

# BDD Story Authoring

Create `docs/jira-stories.csv` plus a Markdown summary (maestro `design-004`) the same way every time: one story schema, stable per-epic story IDs, Given/When/Then criteria that a tester could execute, and a coverage audit that leaves no requirement silently unmapped.

Use when a task says "generate Jira stories", "BDD acceptance criteria", "design-004", or "convert requirements to stories".

## Framing rules

- **A story is a negotiable unit of value, not a task list.** If a "story" is really a step in another story, merge it. If it spans epics, split it at the epic boundary and cross-link.
- **Every criterion must be executable.** Given/When/Then that a tester cannot turn into a test case is prose, not a criterion — rewrite it until it is checkable.
- **Coverage is the point of the step.** The audit below is not optional decoration; a requirements-to-story conversion that drops a measurable target has failed at its only job.

## Story schema (every story, same fields)

| Field | Rule |
|-------|------|
| ID | `<epic>-S<seq>` per epic: `CE-1-S1`, `CE-1-S2`, … Stable, never renumbered; withdrawn stories keep their ID marked withdrawn. |
| Title | User-voiced outcome: "As a <role>, I can <capability> so that <value>". |
| Description | Context + scope IN/OUT for this story (2–4 sentences). |
| Acceptance criteria | Given/When/Then scenarios; see rules below. |
| Priority | P0 (blocks other work / critical path) / P1 / P2. |
| Linked FR | PRD functional-requirement ID(s); `OQ-n` / `RR-n` when the story exists to answer or mitigate one. |

**BDD criteria rules:**
- One behavior per scenario. "Given the user is on S-3, when they submit the form with a file over the size limit, then the validation message from the template spec appears and nothing is uploaded."
- Cover at minimum: happy path, one validation failure per validated input, the permission-denied path, and the edge case the risk register flagged (cite `RR-n`).
- Additional scenarios of the same behavior stay in the same story (`Scenario 2:`); a *different* behavior gets its own story.

## Coverage audit (before finishing, always)

Build the mapping and look in both directions:

- **Requirement → story:** every PRD FR, every Section-4 measurable target, and every open question (`OQ-n`) has ≥1 story or an explicit disposition: `Deferred` (with reason + revisit trigger), `Out of scope` (with the PRD citation that excludes it), or `Resolved` (with the decision that answered it).
- **Story → requirement:** every story traces to at least one FR/target/OQ/RR. A story with no parent is scope creep in disguise — find its parent or cut it.
- **Gaps are listed, not hidden.** The Markdown summary ends with a "Coverage gaps" section: anything missing, anything dispositioned, anything the audit could not resolve. An empty section says so explicitly.

## Outputs

- `docs/jira-stories.csv` — one row per story; columns: `ID, Epic, Title, Description, Acceptance criteria (Given/When/Then, scenarios separated by blank-line-safe delimiter), Priority, Linked FR, Disposition notes`.
- Markdown summary — per-epic story tables, the coverage tally (FRs mapped / dispositions / gaps), and the Coverage gaps section.

## Definition of done

- [ ] Every story has the full schema; IDs stable per epic
- [ ] Criteria are single-behavior Given/When/Then with negative and edge paths
- [ ] Bidirectional coverage audit complete; gaps listed explicitly
- [ ] CSV + Markdown summary written; dispositions carry citations, not just labels
