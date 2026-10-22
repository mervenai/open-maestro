---
id: risk-register
name: Risk Register
tags:
- maestro
- risk
- intake
- planning
- milestones
---

# Risk Register

Create and maintain `docs/intake/risk-register.md` (maestro `intake-003`) the same way every time: one register table, stable `RR-n` IDs, and blocking flags that flow into `milestones.yaml` so design cannot start blind.

Use when a task says "create a risk register", "intake-003", or when new risks surface during design, build, or review.

## Framing rules

- **The register is a living document.** Update it when risks change; never rewrite history. A superseded or retired risk keeps its row with its final status — other artifacts cite `RR-n` IDs and must stay resolvable.
- **Risks are not decisions.** A risk describes what could go wrong and what we do about it; the decision to accept, mitigate, or block on it lives in the decision trail (decision-register skill). Do not conflate the two documents.

## Register table shape (one table, every risk)

| ID | Risk | Likelihood | Impact | Owner | Mitigation | Blocks design? | Status |
|----|------|-----------|--------|-------|-----------|----------------|--------|

- **Likelihood / Impact**: `High` / `Medium` / `Low`, each with a one-line anchor so ratings are arguable ("Medium — depends on vendor API stability we have not tested"), not vibes.
- **Owner**: named team or person, never "TBD" for blocking risks. If no owner exists, the owner is whoever must find one — write that down.
- **Blocks design?**: `yes` only for risks that must close (or have a ratified decision) before design begins; everything else is `no` and stays visible.
- **Status**: `Open` / `Mitigating` / `Closed` / `Accepted` / `Superseded by RR-n`. New rows default to `Open`.
- Order rows by severity (likelihood × impact), worst first.

## RR-n ID discipline

- Every risk gets a **stable register ID** — `RR-1`, `RR-2`, … — assigned once, never renumbered. New risks take the next unused number.
- The `RR-n` namespace is shared with the blueprint-contract skill's open-items register (`DT-n` / `OQ-n` / `RR-n`). If a design-phase finding turns out to be the same risk, cite the existing `RR-n` — do not mint a parallel ID.
- IDs are the join key: design-002 tags blocked components with them, design-004 links stories to them, design-005 re-checks them at sign-off, and the decision-register CSV carries their disposition.

## Elicitation checklist (where risks come from)

- PRD scope ambiguity or unstated exclusions (cross-check the intake synthesis)
- Open questions from the synthesis (`OQ-n`) that have a failure mode, not just an answer
- Contract mismatches or reuse friction found by the reuse assessment
- External dependencies: vendor APIs, shared services, other teams' roadmaps
- Data quality, migration, or backfill hazards
- Auth/permission model gaps
- Performance, capacity, or rate-limit exposure
- Timeline or integration-window constraints

## Blocking risks must be visible in milestones.yaml

A risk marked `Blocks design? = yes` that is still `Open` is a design gate. Before finishing (or on any change to blocking status), mirror it into `.open-maestro/milestones.yaml` following the decision-register skill's two-list invariant and validation rules: the per-milestone `blockers:` list **and** `summary.active_blockers`, asserted identical. A blocking risk that exists only in the markdown register will be missed by the dashboard and `/next`.

## Definition of done

- [ ] One register table in the shape above; no risks living only in prose
- [ ] Every row has likelihood, impact (both anchored), owner, mitigation, blocks-design flag, status
- [ ] `RR-n` IDs stable and sequential; shared-ID collisions resolved by citation, not renumbering
- [ ] Every blocking risk mirrored into `milestones.yaml` (both blocker lists, consistent)
- [ ] Severity ordering; no orphaned references from later artifacts
