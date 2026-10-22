---
id: traceability-matrix
name: Traceability Matrix
tags:
- maestro
- traceability
- qa
- planning
- milestones
---

# Traceability Matrix

Create and maintain `docs/traceability-matrix.md` (maestro `build-002`) the same way every time: one matrix mapping every requirement to its story, implementation, and tests, with a status vocabulary small enough that nobody can game it.

Use when a task says "build the traceability matrix", "build-002", or when a phase completes and the matrix needs updating.

## Framing rules

- **The matrix is the single join table of the project.** FRs, stories (`CE-n-Sm`), implementation files, and tests meet here. If a fact exists in two places, this is the place that says which places agree.
- **It is a living document, updated at phase boundaries** — build-004 updates it after each phase, qa-001 reads it for coverage, qa-004 reports against it. A matrix that is only accurate on day one is worse than none: it lies with confidence.
- **Status is a claim, and claims get challenged.** Status values reflect evidence (a merged PR, a passing test run), not intention.

## Matrix shape (one row per requirement)

| Req ID | Source (PRD §) | Type | Story (CE-n-Sm) | Impl files | Unit test | E2E test | Status |
|--------|----------------|------|-----------------|------------|-----------|----------|--------|

- **Req ID** — the PRD's FR ID, measurable-target ID, or NFR ID. If the PRD has no IDs, assign stable `FR-n` / `TGT-n` / `NFR-n` here and say so; never renumber afterward.
- **Type** — `FR` | `Target` (Section-4 measurable) | `NFR`.
- **Status vocabulary** — exactly `Not Started` / `In Progress` / `Done`. Resist `Blocked`, `Nearly done`, `Code complete`: `Blocked` belongs in the risk register (`RR-n`), and the other two are `In Progress`.
- Multiple stories/files/tests per requirement: list them comma-separated in the cell; one row per requirement, never per link.

## Maintenance protocol

- **build-004** adds/updates rows as phases land — a phase is not done until its matrix rows reflect it.
- **qa-001** derives test-coverage targets from the matrix: every `Done` row must have both test cells filled, and every filled test cell must actually exist (spot-check paths).
- **qa-004** reports pass/fail per matrix row; the report's go/no-go references row statuses, not vibes.
- Rows are never deleted. A withdrawn requirement keeps its row with Status `Done` and a note, or is struck through with the disposition citation — the history is the point.

## Coverage checks (both directions)

- **Forward:** every FR, target, and NFR from design-004's coverage audit appears as a row — including items dispositioned `Out of scope` (their row cites the disposition).
- **Backward:** every story ID in the matrix exists in `docs/jira-stories.csv`; every impl file path exists in the repo; every test path exists under `tests/`. Dangling references make the matrix untrustworthy — verify paths before counting a row `Done`.

## Definition of done

- [ ] One row per FR/target/NFR; IDs stable; type column correct
- [ ] Status restricted to the three-value vocabulary, evidence-based
- [ ] Bidirectional check clean: no orphan requirements, no dangling story/file/test references
- [ ] Ownership of updates assigned (build-004 / qa-001 / qa-004) and noted in the doc header
