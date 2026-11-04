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

## Preconditions (hard gate)

This brief is assembled **only after a passing design sign-off** (`docs/design-signoff.md`, maestro design-005, verdict `Signed off` or `Signed off with conditions`). If the sign-off is absent, `Rejected`, or still open: **stop and report** — do not assemble a brief, do not ask the client to ratify. A terminal-clean decision register is necessary but never sufficient evidence of readiness: freeze gating lives in artifact-level state (milestone exit criteria, cross-artifact consistency, auth claims traced to code) that only the sign-off verifies. Deriving "all resolved" from a CSV sweep, or asking the client to approve while internal findings are open, is the exact failure this gate exists to prevent.

## Framing rules

- **Synthesized, never authoritative.** Every row and claim traces to a source artifact — blueprint `§` references and register IDs — and introduces no new decision. If synthesis surfaces a discrepancy, fix the source artifact, not this doc.
- **The doc cites the contract version it asks the client to approve**, which may postdate the sign-off freeze (a `CR-nn` revision). Regenerate on every contract version bump; never silently overwrite a returned (signed) copy — archive it and bump.
- **Client language.** No `file:line` citations, no mechanism internals a non-engineer wouldn't parse. Engineering evidence stays in the source artifacts; this doc carries the conclusions and the pointers.
- **Header block** (one compact paragraph): authors, base date and revision date, requested approver by role and (when known) person, the `§ = blueprint vX.Y section` notation — then a standalone `DRAFT, not approved by <client>` banner line until the returned record is registered.

## Section requirements are normative

The document is **six numbered sections plus a reference appendix, in this order**. Never omit a section: where a section genuinely has no content, write `N/A — <reason>` in place (a UI-only change still shows §4 with its repos and current state). Do not add memo sections (next steps, FAQs, contact lists) — the brief is for deciding, not for project onboarding.

## The six sections

**§1 The ask.** What to review (the contract version and its draft/frozen state), where the decision is recorded (§6), and the scope boundary — what this approval does **not** cover (deployment sequencing, go-live dates, merging PRs) with companion review documents named and linked. Include any specific review tasks the approver is uniquely positioned to do (e.g. "the blueprint has no table-level DDL; the alert tables in the draft PR were derived from it — this ask includes a schema review").

Then the **explicit-call list**: every decision that changed since the client last agreed or was made without client input that the client would reasonably expect to have had. One short paragraph each — what was originally agreed (and when), what it became, why, and the ask ("accept 5 minutes or ask for 1 minute back"). A client discovering a silent change in a table row instead of this list is a trust defect worse than the change itself. Items carry their status/register ID inline, and fresh input carries a dated attribution.

**§2 Architecture in brief.** The legend line — `(new)` net-new per the blueprint, `(existing)` existing service, `(external)` outside the client; dashed arrows are lookups or paths still owed — plus the pointer to the rendered diagram file (the journey-map packet or a mermaid source), and one behavioral sentence per flow ("DnA never pushes: the poll job calls it").

**§3 Major decisions.** One table, three columns: `Decision | Rationale | Status`. The Decision cell carries the blueprint `§` refs (and code formatting for identifiers). Every row's Status is one of the three-value vocabulary below; open items name their register ID in the status cell.

**§4 Repos impacted.** Table: `Repo | What changes | New tables, APIs, events | Schema Y/N | Current state` — Current state is live draft-PR/deploy status with dated notes for anything checked recently. Close with the timing line: "Nothing is merged; this review can change the draft PRs" (or its current truth).

**§5 Security and data.** Bulleted prose: tenant isolation, secrets handling, auth, PII/retention/evidence caps, immutability, fail-open vs fail-closed behavior. **Never omit** — "no client confirmation needed" is the floor, not absence. Every open concern ends in an explicit ask: **"Please confirm …"** (and name who should confirm, when it isn't obvious). Security findings the client must ratify are never buried as table text.

**§6 Approval.** The boundary statement (what this records — architecture, decisions, repo impact, security model — and what it does not), then **Conditions of approval** (sourced from the design-signoff conditions table, client-worded, including carried-forward tracked risks), then the signature table:

```
| Field | Entry | Field | Entry |
| --- | --- | --- | --- |
| Name |  | Role |  |
| Date |  | Decision | ☐ Approved ☐ Approved with conditions ☐ Changes required |
| Scope | Approves the <epic> design as described in blueprint vX.Y. |  |  |
| Other conditions |  |  |  |
```

**Reference (appendix).** Glossary — team names, environment names, domain abbreviations the approver may not know — and Sources: the contract version, register version, sign-off date, PR-state date, env-check dates.

## Decision status vocabulary

Every §3 row carries exactly one status, with provenance inside the cell (date, and how agreed — call, written confirmation, register ratification):

- **Agreed with M3** — the client agreed, on a dated call or in writing.
- **Merven proposal, needs M3 sign-off** — decided by Merven (or proposed by a third party without client input); the row is an ask.
- **Open, depends on M3 or DnA** — blocked on a named external party; name the register ID in the status cell (`Open, depends on M3 (DNA-D12)`) and what unblocks it in the Rationale cell.

Rows in the second or third state are the asks; a reader must be able to find every open ask without reading anything else. A RAG/traffic-light summary may be added on top as an optional rollup for read-aloud reviews — never as a replacement for the per-row status.

## Freshness attributions

Any external input incorporated after the doc's base date carries an inline attribution at the point it appears: `(Name (party), YYYY-MM-DD)` — e.g. a changed retry policy, a newly confirmed host, an updated contract caveat. The doc must make it visible *what changed because of the last conversation*, without re-reading the register.

## Common drifts to reject

- **Memo genre** — "ratification memo", next-steps lists, contact/FAQ sections, "prepared by" agent bylines. The artifact is a decision brief; internal narrative belongs in the sign-off doc.
- **Traffic-light substitution** — Green/Yellow/Red replacing the per-row three-value status.
- **Tables for the architecture** — a component/owner/status table instead of the diagram + legend + behavioral sentences.
- **Register dump as narrative** — long decision lists without rationale or status; the table is the summary, the register is the detail.
- **Omitting §5** because "security is fine" — fine is a sentence in §5, not an absent section.
- **Register-clean claims** — "all decisions resolved / freeze gate green" derived from a terminal-clean CSV sweep. Readiness is the sign-off's verdict, not the register's Status column.
- **Approval language beyond the scope line** — the only approval sentence is the Scope cell; everything else is conditions.

## When the record comes back

- The returned verdict becomes register row `APPROVAL-1` per the decision-register skill (`Approved` → Ratified; `Approved with conditions` → Accepted with the conditions quoted in Context; `Changes required` → the required changes become or refresh open rows).
- Per-decision client answers flow into the existing register rows' `Client answer` columns — never into new rows, never into this doc (the doc regenerates from the register).
- The signed copy is archived next to the doc (e.g. `docs/client-design-approval-vX.Y-signed.pdf`) and cited in the register row.

## Definition of done

- [ ] Header paragraph: authors, base + revision dates, named approver, `§` notation; standalone DRAFT banner
- [ ] §1 states the ask, the decision location, scope boundaries with companion docs named, and any specific review tasks; explicit-call list complete with inline status/IDs and dated attributions
- [ ] §2 legend + rendered-diagram pointer + one behavioral sentence per flow
- [ ] §3 three-column table; every row a three-value status with provenance; open items carry their register ID
- [ ] §4 five-column repos table with Current state + dated notes; nothing-merged/timing line present
- [ ] §5 present with every open concern ending in an explicit confirm-ask
- [ ] §6 boundary statement, conditions of approval, four-field signature table with scope line
- [ ] Appendix glossary + dated sources footer
- [ ] Exactly six sections + appendix, in order; zero memo-genre additions; zero new decisions (everything cites `§`/register ID)

## Relationship to other skills

- **blueprint-contract** — the design-signoff section's conditions and must-close tables are this doc's §6 conditions source; only design-005 may say "frozen", this doc says "approved".
- **decision-register** — the register CSV stays the durable instrument; this doc is its readable face, and the returned record lands as `APPROVAL-1`.
- **bundle-execution** — §4's Current-state column reads the same PR/env-check status the bundle process maintains.
- **milestones-authoring** — ratified approval is worth one dated decision-layer note on the design milestone; no yaml state churn otherwise.
