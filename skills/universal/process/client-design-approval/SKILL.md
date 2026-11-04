---
id: client-design-approval
name: Client Design Approval
tags:
- maestro
- milestones
- design
- client
- approval
---

# Client Design Approval

Create `docs/client-design-approval.md` (maestro `design-006`): the readable, standalone brief a client approver reviews to approve (or conditionally approve) a frozen design — instead of the artifact stack behind it.

Use when a task says "design-006", "assemble the client approval", "client design approval", or "approval brief".

## Framing rules

- **Synthesized, never authoritative.** Every row and claim traces to a source artifact — blueprint `§` references and register IDs — and introduces no new decision. If synthesis surfaces a discrepancy, fix the source artifact, not this doc.
- **The doc cites the contract version it asks the client to approve**, which may postdate the sign-off freeze (a `CR-nn` revision). Regenerate on every contract version bump; never silently overwrite a returned (signed) copy — archive it and bump.
- **Client language.** No `file:line` citations, no mechanism internals a non-engineer wouldn't parse. Engineering evidence stays in the source artifacts; this doc carries the conclusions and the pointers.
- **`DRAFT, NOT APPROVED` banner** until the returned record is registered; the header names the approver by role and, when known, by person.
- **Scope boundary is stated in the doc**: what this approval covers (architecture, decisions, repo impact, security model) and what it does not (deployment sequencing, go-live plans, merging PRs). Companion review documents are named and linked, and approving this design does not approve them.

## Decision status vocabulary

Every major-decision row carries exactly one status, with provenance inside the cell (date, and how agreed — call, written confirmation, register ratification):

- **Agreed with M3** — the client agreed, on a dated call or in writing.
- **Merven proposal, needs M3 sign-off** — decided by Merven (or proposed by a third party without M3 input); the row is an ask.
- **Open, depends on M3 or DnA** — blocked on a named external party; the row names what unblocks it.

Rows in the second or third state are the asks; a reader must be able to find every open ask without reading anything else.

## The explicit-call list (required)

Before the decision table, list every decision that **changed since the client last agreed** or was **made without client input that the client would reasonably expect to have had**. One short paragraph each: what was originally agreed (and when), what it became, and why. A client discovering a silent change in a table row — instead of in this list — is a trust defect worse than the change itself.

## Document skeleton

1. **Header** — title (`<Epic>: Blueprint vX.Y Approval Brief`), authors, dates (authored + revised), requested approver (role + person), `DRAFT` banner, `§ = blueprint vX.Y section` notation.
2. **The ask** — what to review, where to record the decision (the §8 signature block), what approval does and does not cover, companion docs named. Then the explicit-call list.
3. **Architecture in brief** — an inline diagram (text-rendered boxes and arrows are fine) with a legend (`new` / `existing` / `external`; dashed = paths still owed) and one behavioral sentence per flow ("DnA never pushes: the poll job calls it").
4. **Major decisions** — table: Decision (with `§` refs) | Rationale | Status (the three-value vocabulary above). Cover every decision the client's world depends on; a one-line row beats an absent row.
5. **Repos impacted** — table: Repo | What changes | New tables/APIs/events | Schema change Y/N | **Current state** (live draft-PR/env-check status). Close with the timing line: "Nothing is merged; this review can change the draft PRs" (or its current truth).
6. **Security and data** — short prose paragraphs (tenant isolation, secrets, PII/retention, auth, fail-open/fail-closed behavior). Each open concern ends in an explicit ask: "Please confirm …". Security findings the client must ratify are never buried as table text.
7. **Conditions of approval** — the sign-off conditions (this is the design-signoff doc's conditions table, client-worded) plus any condition the approver should know gates the build.
8. **Approval record** — the instrument: checkboxes `Approved` / `Approved with conditions` / `Changes required`, Name / Role / Date, the scope line ("Approves the <epic> design as described in blueprint vX.Y"), and free-text "Other conditions".
9. **Glossary + Sources** — terms the approver may not know (team names, environment names, domain abbreviations); and the provenance footer (contract version, register version, sign-off date, env-check dates, PR state date).

## When the record comes back

- The returned verdict becomes register row `APPROVAL-1` per the decision-register skill (`Approved` → Ratified; `Approved with conditions` → Accepted with conditions in Context; `Changes required` → the required changes become/refresh open rows).
- Per-decision client answers flow into the existing register rows' `Client answer` columns — never into new rows, never into this doc (the doc regenerates from the register).
- The signed copy is archived next to the doc (e.g. `docs/client-design-approval-vX.Y-signed.pdf`) and cited in the register row.

## Definition of done

- [ ] Header names the approver; DRAFT banner; version mirrors the contract under approval; `§` notation stated
- [ ] The ask states scope boundaries and names companion docs; explicit-call list complete (changed-since-agreed + decided-without-client)
- [ ] Every major decision row carries a status from the three-value vocabulary with provenance; every ask is findable
- [ ] Architecture diagram has a legend and per-flow behavioral sentences
- [ ] Repos table carries Current state and the nothing-merged/timing line
- [ ] Security paragraphs each end in an explicit ask where client confirmation is needed
- [ ] Conditions of approval present; signature block has all three verdict checkboxes
- [ ] Glossary and Sources footers present; zero new decisions introduced (everything cites `§`/register ID)

## Relationship to other skills

- **blueprint-contract** — the design-signoff section's conditions and must-close tables are this doc's §7 source; only design-005 may say "frozen", this doc says "approved".
- **decision-register** — the register CSV stays the durable instrument; this doc is its readable face, and the returned record lands as `APPROVAL-1`.
- **bundle-execution** — §5's Current-state column reads the same PR/env-check status the bundle process maintains.
- **milestones-authoring** — ratified approval is worth one dated decision-layer note on the design milestone; no yaml state churn otherwise.
