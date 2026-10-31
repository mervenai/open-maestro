---
id: bundle-execution
name: Bundle Execution (batching implementation work)
tags:
- maestro
- milestones
- implementation
- planning
- process
---

# Bundle Execution (batching implementation work)

Plan and run implementation as **bundles** — groups of stories built, reviewed, and verified together — instead of one full agent round per story. Use when a build plan has ~15+ stories or spans multiple repos/stacks, and the per-story loop (build → review → fix → merge → env-check, repeated per story) would spend most of its rounds on process rather than code.

## Why batch

Measured on a 38-story / 10-bundle project: ~43 agent rounds instead of 150–230 per-story; ~10 env-check requests instead of ~38. The bigger win is qualitative: bundling exposes questions while they are still cheap (step 0 below).

## Sizing and grouping

- **≤ ~1,500 production lines per bundle.** Above that, or when a bundle mixes two stacks (e.g. .NET + Angular), plan two independent reviewers in parallel; otherwise one review per bundle.
- **Group by repo / stack / touch-point.** Stories that touch the same files, the same seam, or the same deployable ship together. Independent bundles (no shared files, no ordering dependency) may run two at a time.
- Every story belongs to exactly one bundle, and the bundle list covers the whole remaining plan — no orphans.

## The lean process per bundle

0. **Kickoff: questions before coding.** List every product/client question the bundle's scope makes foreseeable *before* writing code, and record each as a decision-register row. Post-build questions are the most expensive kind — each one found after code has shipped cost a fix round.
1. **One implementation pass** for the whole bundle, on stacked local branches; checks run at the tip of each story's branch.
2. **One independent review** of the bundle (two in parallel only over the sizing threshold).
3. **One fix-and-merge pass.** Re-review only if the review blocks the bundle or finds HIGH issues — and then only those diffs.
4. **One env-check checklist per bundle**, not per story.

Merge discipline is unchanged: one `--no-ff` merge per story per repo (or per bundle where stories stack).

## The bundle-plan doc

Author it as `docs/build-plan-bundles-<yyyymmdd>.md`. Per bundle record: id + name, member stories, repos touched, entry gate (what must be answered or done first), estimated production lines, and status (proposed / in progress / merged / paused). Track per-bundle progress in the doc or a `docs/build-status.csv`.

Keep bundle state **out of `milestones.yaml`** — bundle composition churns fast; the yaml stays the slow decision layer (register rows, blockers, milestone notes).

## Rescoping rule

Absorb feedback by revising the bundle doc, never by rewriting story rows: add a rework bundle (e.g. R1), pause a bundle, or rescope with a prime suffix (B4′). Mark supersessions explicitly ("supersedes <file> for unbuilt work; that file stays the record of the original sizing") so history stays auditable.

## Checklist

- [ ] Every remaining story is in exactly one bundle; bundles grouped by repo/stack/touch-point
- [ ] Each bundle ≤ ~1,500 production lines (or two parallel reviewers planned)
- [ ] Kickoff questions recorded as register rows before any code
- [ ] One env checklist per bundle planned
- [ ] Bundle doc written; milestones.yaml untouched except decision-layer one-liners

## Relationship to other skills

- **milestones-authoring** — bundle state lives in docs; yaml changes only for decisions (register rows, blockers, dated notes).
- **decision-register** — kickoff questions and process decisions (merge discipline, lean gates) are register rows.
- **traceability-matrix** — story status still updates per story inside a bundle.
