---
id: intake-synthesis
name: PRD/RFP Intake Synthesis
tags:
- maestro
- intake
- research
- planning
- milestones
---

# PRD/RFP Intake Synthesis

Create `docs/intake/synthesis-{date}.md` (maestro `intake-001`) the same way every time: faithful compression of the PRD/RFP into one canonical section set, with superseded decisions captured so the team never re-litigates them.

Use when a task says "read and summarize the PRD/RFP", "intake-001", or "draft the intake synthesis".

## Framing rules

- **Synthesis is compression, not reinterpretation.** Every claim traces to the PRD or a linked document — cite the section. If two sources disagree, quote both and flag the conflict; do not silently pick one.
- **Linked documents are in scope.** Read what the PRD links (earlier specs, annexes, referenced spreadsheets) before ranking open questions — blockers often hide in the annexes.
- **Do not mint register IDs at intake.** Open questions and risks listed here are candidates; design-001 assigns the stable `OQ-n` / `RR-n` / `DT-n` IDs. Use stable descriptive labels ("upload file-size limit") so design-001 can adopt them without re-derivation.

## Canonical sections (in this order)

1. **Business goal and user value proposition** — why this exists, who it serves, one paragraph max.
2. **Scope IN / OUT** — what is explicitly included and explicitly excluded. "Not mentioned" is not "excluded": only list OUT what the PRD actually excludes.
3. **Superseded decisions** — anything the PRD marks resolved, rejected, or "must not be built". For each: the decision, what it supersedes, and the source citation. This section exists so nobody re-opens a closed door in design.
4. **Top 5–8 blocking open questions** — ranked by blocking severity (what they prevent, not how curious they are). Each: question + why it blocks + who can answer it.
5. **Additional open questions** — brief, **uncapped**. Never drop a question to hit a count; the tail is where integration surprises live.
6. **Top 5–8 risks with proposed mitigations** — each: risk + likelihood/impact in words + mitigation sketch. These are candidates for the risk register (intake-003), not the register itself.
7. **Reuse opportunities** — existing capabilities the PRD implies or explicitly references. Candidates for the reuse assessment (intake-002); one line each.

## Ranking and separation discipline

- **Questions vs risks are different sections.** A question has an answer somewhere ("what is the max file size?"); a risk is a thing that might go wrong ("vendor API may not handle the volume"). If an item is both, put the question in 4/5 and the risk in 6, cross-referenced by label.
- **Severity ordering is by blocking effect** on design and implementation, never by political weight or ease of answering.
- **Superseded decisions are facts, not opinions.** Quote the PRD's wording; if the PRD contradicts an earlier document, the PRD wins and the contradiction is itself a finding.

## Definition of done

- [ ] All seven sections present, in order, each cited to PRD sections or linked docs
- [ ] Every linked document was actually read (listed in a Sources footer)
- [ ] Superseded decisions quoted verbatim with their source
- [ ] Blocking questions ranked with blocker rationale and answer-owner; uncapped tail present
- [ ] No register IDs minted; labels stable enough for design-001 to adopt
- [ ] No silent conflict resolution — disagreements between sources are quoted and flagged
