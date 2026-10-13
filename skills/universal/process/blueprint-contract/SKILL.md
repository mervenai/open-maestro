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

- **This step drafts; it does not freeze.** Label the document `vX.Y-draft` with status DRAFT. The word "frozen" is claimed by design-005 (sign-off) after design-006 (adversarial pass) — never by the drafting step, and never in chain summaries. Do not write "ready for sign-off" conclusions; end with the open-items register and what unblocks it.
- **Drafting ≠ verifying.** Citation self-checks (below) catch transcription errors only. Real review is design-006. Do not let a self-verification stand in for it in summaries or headers.

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
- The same IDs are reused by design-002 (diagram inline tags), design-004 (Jira stories), design-005/006 (review targets), and the decision-register CSV. IDs are the join key across all milestone artifacts.

## Mirror state back into milestones.yaml (mandatory last step)

The dashboard, decision register, and later prompts read `.open-maestro/milestones.yaml` — **not** this document. Before finishing:

- Add (or update) a `blockers:` entry under the design-blueprint milestone for each **blocking** open item, using its register ID and one-line description.
- Add a dated `notes:` entry summarizing the draft: version, headline decisions, closed/added gates.
- Set artifact detection inputs truthfully (the contract file exists → the milestone can detect `docs/*contract*.md`).
- Follow the decision-register skill's two-list invariant and validation rules when editing `milestones.yaml`.

## Definition of done

- [ ] Header block complete; labeled draft; no "frozen"/"ready for sign-off" language
- [ ] Skeleton sections 1–9 present in order
- [ ] Every load-bearing claim has a re-verified `file:line` citation; mismatch flags in place
- [ ] UI-facing values follow the authority ladder; discrepancies recorded
- [ ] Open-items register: stable IDs, owners, blocking flags
- [ ] `milestones.yaml` updated (blockers + notes), YAML validates, both blocker lists consistent
- [ ] Sources-checked table records every file read with mtime/SHA
