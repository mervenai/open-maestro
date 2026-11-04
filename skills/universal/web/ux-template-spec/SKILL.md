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
- **When an approved prototype exists, it is the visual authority.** The spec text is a summary; the prototype takes precedence on all visual, spacing, and interaction details wherever the two differ. Record the prototype path and its rendered reference screenshots in the document header on every version, so build plans and delegation prompts can cite them.

## Prototype reconciliation (required when a prototype exists)

The text summary drifts from the prototype silently — a tile dropped here, a control model changed there — and builders follow the text. Before the spec is done:

- Walk the prototype region by region (header, tabs, tiles, charts, lists, filter bar, grid, empty/loading/error states, spacing) and enumerate **every** divergence between the prototype and the spec text (counts, components, layout, copy, states).
- Each divergence gets a ruling recorded inline — a register or CR id and which source wins — or becomes a CR raised before coding. "The prototype takes precedence" resolves a divergence only if the divergence is listed; an unlisted divergence is a spec defect, not a precedence question.
- Divergences the client ruled on (call minutes, register ratifications) cite that ruling; unresolved ones are `OQ-n`/`DT-n` rows per the blueprint-contract skill.

## Verify prescriptions against the codebase

A spec that prescribes what the codebase doesn't have produces screens that compile and render wrong — undefined tokens fail silently. Every prescribed **design token, palette name, and component** must be shown to exist in the codebase, with the same citation discipline as claims:

- Grep for the definition of each prescribed token/component (`file:line` evidence) before finalizing. Naming a token that has no definition is a defect, even with a CSS fallback value.
- When the prototype uses a token or component the codebase lacks, map it to the real equivalent (e.g. the actual token family) and record the mapping in the spec — never transcribe the prototype's names unverified.
- The same check applies to component prescriptions: name the real shared component (cited by path) for tabs, stat tiles, selects, date ranges, toggles, filter bars, and grid overlays — not a generic description that builders satisfy with native elements.

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
- Prototype reconciliation: every prototype-vs-text divergence is listed with a ruling or a CR (when a prototype exists); the header names the prototype and its rendered reference screenshots.
- Prescription verification: every prescribed token/palette/component greps to a definition or a recorded mapping — no undefined names ship in the spec.

## Definition of done

- [ ] Screens numbered and referenced; every state enumerated per screen (default/loading/empty/error/validation)
- [ ] User flow with alternate paths as first-class branches
- [ ] Validation table with exact message strings, triggers, placement
- [ ] File/template formats specified column-level with an example
- [ ] Components cited by path; authority ladder applied to UI-facing values; discrepancies recorded
- [ ] Prototype divergences enumerated, each ruled or raised as a CR; prototype + reference renders named in the header
- [ ] Prescribed tokens/components verified against the codebase (or mapped), with evidence
