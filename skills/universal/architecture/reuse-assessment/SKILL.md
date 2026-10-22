---
id: reuse-assessment
name: Service & Code Reuse Assessment
tags:
- maestro
- reuse
- architecture
- intake
- research
- milestones
---

# Service & Code Reuse Assessment

Create `docs/intake/reuse-assessment.md` (maestro `intake-002`) the same way every time: systematic search for reusable capabilities, an honest effort rating per asset, and contract mismatches surfaced instead of smoothed over.

Use when a task says "identify existing service/code reuse", "intake-002", or "what can we reuse for this feature".

## Framing rules

- **"Nothing reusable" is a valid result — padding is not.** A reuse assessment that inflates weak matches costs more at design time than admitting net-new. Rate honestly; a High-effort reuse is often a smell that net-new is cleaner — say so when it is.
- **Every claim will be re-verified.** plan-001 re-reads this document and checks each asset against the live repos. Write claims checkably: exact paths, exact shapes, no "should be easy" without a reason.
- **Cite what you actually saw.** Follow the blueprint-contract skill's citation discipline: re-read each cited `file:line` before finalizing and confirm it shows what you claim. A grep hit is not evidence; open the file.

## Search pattern (cover all six categories)

1. **Entry points** — controllers, route handlers, endpoints doing anything similar
2. **Services** — business-logic services with overlapping responsibility
3. **UI components** — existing components matching the needed interaction (grids, uploaders, wizards)
4. **Events** — published/consumed events with overlapping payloads or triggers
5. **Integration patterns** — HTTP clients, queue consumers, import/export pipelines, auth handoffs
6. **Auth/permissions models** — attribute/policy setups the feature must fit into

Search by naming variants, then by behavior keywords, then follow imports/DI registrations outward from the closest hit.

## Assessment table (one row per candidate)

| Asset | Path (file:line) | Category | Reuse LOE | Contract mismatches | Notes |
|-------|------------------|----------|-----------|---------------------|-------|

**Reuse LOE anchors** (use these meanings exactly):

- **Low** — drop-in: same data shape, same auth context, same lifecycle; call it and it works.
- **Medium** — adapt: surface changes needed (new parameter, wrapper, config) but no semantic change to the asset.
- **High** — reshape: the asset's assumptions (scale, ownership, mechanism) differ from what the feature needs; reuse means changing the asset or the feature's model.

**Contract-mismatch taxonomy** — flag any of: data shape, scale/volume, auth context, lifecycle/ownership, mechanism (the big one: the asset solves the problem a different way than the feature assumes, e.g. sync-vs-async, document-vs-relational).

## Downstream contracts

- Mismatches and High-LOE rows are risk-register candidates (intake-003) — call them out as `RR` candidates in the notes column.
- This document's claims are plan-001's Phase-A re-verification targets; keep paths and shapes precise.
- Genuinely reusable assets get cited later by design-001 as `[existing]` anchors — make sure they deserve it.

## Definition of done

- [ ] All six categories searched (state explicitly when a category has no candidates)
- [ ] Every row has a re-verified `file:line` citation and a mismatch check
- [ ] LOE ratings use the anchors above; High reuses flagged with the net-new alternative considered
- [ ] No unevidenced claims; "nothing found" stated plainly where true
- [ ] RR candidates marked for the risk register
