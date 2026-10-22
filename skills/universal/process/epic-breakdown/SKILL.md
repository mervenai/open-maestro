---
id: epic-breakdown
name: Epic Breakdown
tags:
- maestro
- epics
- intake
- planning
- milestones
---

# Epic Breakdown

Create `docs/intake/epics.md` (maestro `intake-004`) the same way every time: 3–6 outcome-aligned epics with stable `CE-n` IDs, full-stack coverage tables, and gates carried as internal conditions — never as epic splits.

Use when a task says "create the epic breakdown", "intake-004", or when scope needs re-consolidating after a major PRD change.

## Framing rules

- **Epics own user-visible outcomes end-to-end.** An epic is a promise a user can observe being kept, not a component slice. If an epic's name is a layer ("API", "frontend", "database work"), the split is wrong.
- **Epics do not re-baseline scope.** Scope, success metrics, and exclusions were set by the intake synthesis and risk register; epics reference them (`RR-n`, `OQ-n`) instead of restating or adjusting them.

## Epic shape (every epic, same fields)

```
### CE-1 - <Name>
- **Goal**: the user-visible outcome, one sentence
- **Scope IN / OUT**: what this epic covers and explicitly does not
- **Service coverage**: table of repo/service → what changes there → why this epic needs it
- **Gates**: open questions / decisions / risks carried inside this epic, each cited by register ID (OQ-n / D-n / RR-n) with the condition ("can start once RR-4 mitigation lands"), not as a blocking preamble
- **Dependencies**: other epics this one needs first (CE-n), with the reason
```

- **CE-n ID discipline**: IDs are assigned once (`CE-1`, `CE-2`, …), never renumbered. A cancelled epic keeps its number marked `Withdrawn`. Build plans, dashboards, and the traceability matrix cite `CE-n` — renumbering breaks them.
- **3–6 epics** is the target band. Fewer means the breakdown adds nothing over the PRD; more means the epics are slices, not outcomes. State explicitly if the band cannot be met and why.

## Anti-patterns (reject these splits)

- **One-epic-per-layer** — "backend epic", "frontend epic", "DB epic": nothing ships until everything ships; integration risk lands all at once.
- **One-epic-per-team** — mirrors org chart, not user value.
- **Dependency-driven splits** — an epic that exists only because another epic was split ("CE-3: the parts of CE-2 that need CE-1 first"): dependencies belong in the Dependencies field.
- **Gate-as-epic** — "decisions epic" or "spike epic" that must finish before real work: gates are conditions inside the epics they gate.

## Consistency checks before finishing

- Every PRD scope item from the synthesis maps to exactly one epic's Scope IN, or is cited as OUT with a reason.
- Every `RR-n` rated design-blocking appears as a gate condition in at least one epic.
- Dependency graph is acyclic; an epic with no dependency path from CE-1 territory is either mis-numbered or orphaned.
- Coverage tables name real repos/services — no "TBD service".

## Definition of done

- [ ] 3–6 epics (or explicit justification otherwise), each with the full field set above
- [ ] `### CE-n - <Name>` headings, sequential IDs, no renumbering
- [ ] Gates cited by register ID with conditions; no gate-as-epic splits
- [ ] Dependencies explicit and acyclic; every blocking risk placed in an epic
- [ ] No scope re-baselining: metrics and exclusions referenced, not restated
