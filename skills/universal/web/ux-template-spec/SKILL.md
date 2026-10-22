---
id: ux-template-spec
name: UX & Template Specification
tags:
- maestro
- ux
- frontend
- specification
- milestones
---

# UX & Template Specification

Create `docs/template-spec.md` (maestro `design-003`) the same way every time: screens with explicit states, flows with alternate paths, exact validation messages, and file/template formats specified to the column.

Use when a task says "create the UX/template spec", "design-003", or "specify the user-facing behavior".

## Framing rules

- **Specify what the user sees and does, not how it's built.** Layout structure, component choice, and copy are the spec; implementation mechanics belong to the contract (design-001) and build plans.
- **Reuse before invent.** Every screen must cite the existing components/platform conventions it builds on (with file paths). A screen with no reused component is a finding — either the component gap is real (note it) or the search was lazy.
- **UI-facing values obey the authority ladder** from the blueprint-contract skill: ratified client/stakeholder decision > prototype bundle / actual UI code > PRD > inference. Discrepancies are recorded, not silently resolved.

## Document shape

Number screens `S-1`, `S-2`, … (stable; stories and the traceability matrix reference them). Per screen:

```
### S-1 — <Screen name>
- **Layout structure**: regions and their order (header / filter bar / grid / detail pane …)
- **Components**: existing components reused, cited by path; any net-new component flagged
- **States**: for EACH of default / loading / empty / error / validation — what the user sees.
  "Same as default" is allowed only where it is genuinely true.
- **Actions available**: what the user can do here and where each leads (flow target S-n)
```

Then, document-wide:

1. **User flow** — numbered steps across screens, including alternate paths (validation failure, permission denied, partial data) as branch annotations, not afterthoughts.
2. **Validation message table** — field/rule | exact message text | trigger | placement. Message copy is part of the spec: write the exact string, not "show an error".
3. **Empty and error states** — guaranteed per screen above; here cover cross-cutting ones (service down, session expired, partial outage) once, referenced by screens.
4. **File/template formats** — for uploads/exports/imports, specify the format to the column: column/field name, type, required?, validation rule, example row (e.g., the exact Excel columns the upload template accepts).

## Consistency checks before finishing

- Every screen named in the contract (design-001) has a spec block, and every spec block is reachable from a flow.
- Every validation rule in the contract appears in the message table with exact copy.
- Flow branches cover the error paths the risk register flagged (cite `RR-n` where a path exists because of a risk).
- No platform service or component is referenced that the codebase doesn't have (cite or cut).

## Definition of done

- [ ] Screens numbered and referenced; every state enumerated per screen (default/loading/empty/error/validation)
- [ ] User flow with alternate paths as first-class branches
- [ ] Validation table with exact message strings, triggers, placement
- [ ] File/template formats specified column-level with an example
- [ ] Components cited by path; authority ladder applied to UI-facing values; discrepancies recorded
