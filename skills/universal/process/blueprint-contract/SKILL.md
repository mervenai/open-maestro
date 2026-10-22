---
id: blueprint-contract
name: Blueprint Data & API Contract
tags:
- maestro
- blueprint
- contract
- design
- milestones
- api-design
---

# Blueprint Data & API Contract

Create `docs/blueprint-design-and-data-contract.md` (maestro `design-001`) the same way every time: one canonical document shape, disciplined citations, stable open-item IDs, and state that flows back into `milestones.yaml`.

Use when a task says "draft the data and API contract", "design-001", or "blueprint-design-and-data-contract.md".

## Framing rules (these prevent the recurring failures)

- **This step drafts; it does not freeze.** Label the document `vX.Y-draft` with status DRAFT. The word "frozen" is claimed by design-005 (sign-off), which follows the machine-enforced adversarial passes — never by the drafting step, and never in chain summaries. Do not write "ready for sign-off" conclusions; end with the open-items register and what unblocks it.
- **Drafting ≠ verifying.** Citation self-checks (below) catch transcription errors only. Real review is machine-enforced: the artifact-critic gate on every significant artifact change, and the deep-review gate (milestone-significant blueprint changes, enforced at `/complete`). Do not let a self-verification stand in for them in summaries or headers.

## Document header block (every version, from v1)

```
# <Epic> — Data & API Contract
**Milestone**: `<epic>` / `design-blueprint`
**Version**: vX.Y-draft | **Date**: YYYY-MM-DD | **Status**: DRAFT — awaiting sign-off
**Supersedes**: vX-1 archive path (omit on v1) | **Changelog**: (vX.Y: headline changes)
```

Bump the version on every material revision; archive the prior version to `docs/blueprint-design-and-data-contract-vX.Y.md` and keep the archive path in the header. Never silently overwrite a ratified version.

## Canonical skeleton

1. **Scope** — which milestone artifact this is; which contracts it covers; labeling conventions.
2. **Architecture** — ASCII or referenced diagram of the flows this contract touches.
3. **Contract sections** — one per endpoint/event: URL + method, controller registration, request/response DTOs with field types and validation rules, idempotency, auth per hop, error table with exact messages, feature toggles.
4. **Existing endpoints touched** — with the pre-release gates they carry.
5. **Integration events** — payload schema, delivery guarantees, outbox pattern where required.
6. **Auth rules** — hop-by-hop table.
7. **Open design tasks register** — table: ID | question | owner | blocking? | resolution path.
8. **Citation verification register** — table: # | claim | file:line | ✅/❌ | [existing]/[net-new] | flags.
9. **Sources checked** — repo/HEAD-date-or-mtime per source (no git history → record mtimes).

## Citation discipline (from the design-001 prompt — do not skip)

- Every load-bearing claim anchors to a real `file:line` you re-read **before finalizing**; drop or fix any citation that doesn't hold.
- Label every contract element `[existing]` (truly reuses a codebase pattern) or `[net-new]` (this contract introduces it). Never present net-new as existing.
- **Flag mechanism mismatches explicitly** — when a cited pattern's underlying mechanism differs from what the contract assumes (test double cited as production evidence, Mongo pattern cited for SQL, 0–1 scale cited for a 0–100 contract), say so in place instead of citing as-is.
- **Authority ladder for UI-facing values** (enums, states, actions, colors): ratified client/stakeholder decision > prototype bundle / actual UI code > PRD > inference. If PRD and prototype disagree and neither is ratified, take the prototype and record the discrepancy in the open-items register.

## Open-items register discipline

- Every open question gets a **stable register ID** — `DT-n` (design task), `OQ-n` (open question), `RR-n` (risk/register item) — assigned once, never renumbered; new items take the next number.
- Each row: ID | question | owner (named team/person) | blocking (yes/no) | what unblocks it. Blocking items are the freeze gates; non-blocking items still get IDs so downstream artifacts can reference them.
- The same IDs are reused by design-002 (diagram inline tags), design-004 (Jira stories), design-005 (sign-off review targets), and the decision-register CSV. IDs are the join key across all milestone artifacts.

## Propagate the decision trail (mandatory last step)

A new contract version changes the project's decision state. The dashboard, later prompts, and the client read the trail — **not** this document. Before finishing, update all three trail artifacts:

1. **`.open-maestro/milestones.yaml`** — add/update a `blockers:` entry under design-blueprint for each **blocking** open item (register ID + one-line description), add a dated `notes:` entry (version, headline decisions, gates closed/opened), and follow the decision-register skill's two-list invariant and validation rules (per-milestone lists AND `summary.active_blockers`, asserted identical).
2. **`docs/design-decisions.md`** — append a dated entry to the Revision Log (create the section if the doc doesn't have one: prior ADR docs end at a "Freeze gate summary" — add `## Revision Log` above it or at the end), and update the Freeze Gate Summary table: every ID this contract opens, closes, or changes gets its Status/Conditions row revised. Never leave the freeze-gate table describing a pre-contract state.
3. **`docs/decision-register-vX.Y.csv`** — create it if absent, else add rows, following the decision-register skill: one row per decision, stable register IDs, strict status enum (the Status cell carries one enum word only — exact match, no dates/deciders/prose; see the decision-register skill), open rows first with client-facing questions, closed rows with date + decider. This CSV is the client-feedback artifact; it only exists if a step produces it.

## Design sign-off (design-005)

The sign-off step is the only step that may declare the design frozen. Its output (`docs/design-signoff.md`) follows this shape:

- **Header**: epic, date, artifacts reviewed (each cited by path), and the verdict — `Signed off` / `Signed off with conditions` / `Rejected`. Verdicts of `Signed off` or `Signed off with conditions` are the only places the word "frozen" may appear, applied to the contract version cited in the header (e.g. "v1.3 is frozen"); a bare "the design is frozen" is not allowed.
- **Checklist**: one block per artifact (contract, diagram, UX spec, Jira stories), each with pass state and the evidence pointer — artifact-critic verdicts and the deep-review ledger record for that artifact, not a fresh re-verification.
- **Must-close items**: every open item that blocks build planning, cited by register ID (`RR-n` / `OQ-n` / `DT-n`) with its current status verified against the registers. Anything still open becomes an explicit sign-off condition.
- **Findings**: cross-artifact consistency and auth-tracing findings ordered P0/P1/P2 with quoted evidence; each `DECISION NEEDED` finding gets a `D-n` proposal per the decision-register skill's ADR format.
- **Freeze statement**: which contract version is frozen, what unfreezes it (the conditions list), and the single sentence of client-facing summary.

Sign-off triggers the same propagation step as a contract revision (above): revision-log entry, freeze-gate summary rows for every ID opened/closed, decision-register CSV rows, and `milestones.yaml` blockers under the two-list invariant. Unfreezing (a later revision) is recorded the same way — never silently.

## Definition of done

- [ ] Header block complete; labeled draft; no "frozen"/"ready for sign-off" language
- [ ] Skeleton sections 1–9 present in order
- [ ] Every load-bearing claim has a re-verified `file:line` citation; mismatch flags in place
- [ ] UI-facing values follow the authority ladder; discrepancies recorded
- [ ] Open-items register: stable IDs, owners, blocking flags
- [ ] Decision trail propagated: milestones.yaml (both blocker lists) + design-decisions.md revision log & freeze-gate rows + decision-register CSV
- [ ] `milestones.yaml` YAML validates, both blocker lists consistent
- [ ] Sources-checked table records every file read with mtime/SHA
