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
- Every story belongs to exactly one bundle, and the bundle list covers the whole remaining plan — no orphans. "The plan" means every build-plan doc and product epic in scope (a project can carry several product epics inside one maestro epic): enumerate them all before grouping, and batch across them — a bundle is grouped by repo/stack/touch-point, not by epic, so a bundle may serve several epics and must name each.

## The lean process per bundle

0. **Kickoff: questions before coding, asked synchronously.** List every question the bundle's scope makes foreseeable *before* writing code. Questions the project lead can answer (contract ambiguities, permission/scope calls, UX copy, audit expectations — anything internal) are **asked directly in the agent's response, numbered, each with a recommendation and what it blocks**; the agent ends its turn and waits — the lead answers in the next message and the session resumes. Work a question blocks does not start until the answer arrives, and the ruling is recorded as a register row only after it lands. Only questions that genuinely need an external party (client/M3) become Open register rows and an outbox message, awaited outside the session. Post-build questions are the most expensive kind — each one found after code has shipped cost a fix round. For UI bundles the kickoff list must also carry every template-spec-text-vs-prototype divergence for the screens in scope (see "UI bundles" below).
1. **One implementation pass** for the whole bundle, on stacked local branches; checks run at the tip of each story's branch. For UI bundles, "done" additionally requires the render gate below before review is requested.
2. **One independent review** of the bundle (two in parallel only over the sizing threshold). UI bundles additionally get the visual-fidelity review from the code-review-standards skill; a visual FAIL without a ruling blocks the bundle like any BLOCK.
3. **One fix-and-merge pass.** Re-review only if the review blocks the bundle or finds HIGH issues — and then only those diffs. A fix that changes what the user sees (component choice, labels, placement, tabs, menus) must cite the prototype region or ruling it implements; if no source covers the change, raise a CR — never improvise a visual decision inside a fix prompt.
4. **One env-check checklist per bundle**, not per story. UI bundles add one item: on the ephemeral/real environment, after login, a full-page screenshot of each new screen at the fixed reference viewport, compared against the local screenshots and the prototype. Every remaining difference is classified **data** (empty environment) or **build**; build differences block the env-check. The screenshots are appended to the smoke doc. Every bundle also appends the delivery proof from the branch-delivery skill: the relay branch moved (SHA before → after the push); an unmoved relay blocks the env-check like a build difference.

Merge discipline defers to the **branch-delivery** skill: one `--no-ff` merge per story per repo (or per bundle where stories stack) into the project's declared integration branch, then push — and the relay-moved check after every push. A bundle that pulls a repo into delivery for the first time runs the branch-delivery repo-onboarding checklist as an entry gate before any merge.

## UI bundles: carry the visual source of truth

The failure mode this prevents: a screen that passes every code-level gate — tests, build, two reviews, fix pass — and still looks nothing like the approved prototype, because no plan row or delegation prompt ever named the prototype. Every UI bundle row in the plan must open with a filled-in visual-target block:

> **VISUAL TARGET (required for any task that adds or changes a screen).**
> Source of truth: `<prototype/mockup path>`, screen/tab `<name>`, rendered reference `<PNG path, fixed viewport>`.
> Precedence for visual, spacing and interaction details: **prototype > template-spec > blueprint**. Precedence for data contracts and behaviour: **blueprint > template-spec > prototype**.
> Where the template-spec text and the prototype disagree, list the difference in the PR and follow the prototype unless a ruling below says otherwise.
> Rulings that override the prototype: `<list, with CR/decision id>`.

Entry gate for a UI bundle (confirmed at kickoff): mock data must let the screen reproduce the prototype's populated state — every column, chip, and count the prototype shows must exist in the mock and the model, or appear in the PR as a contract gap with a CR number. Include at least one row per visual variant (flagged/unflagged, each severity, empty state) and counts that make charts non-trivial.

Render gate (step 1): before review is requested, render the screen locally with mock data at a fixed viewport of **1600×1200**, in the same states as the reference (populated, empty, error), and commit the pairs under `docs/reviews/screens/<bundle>/` as `<region>-proto.png` and `<region>-build.png` (or attach them to the PR). A UI task without these screenshots is not done; capture may be delegated to a `web-qa` agent.

## The bundle-plan doc

Author it as `docs/build-plan-bundles-<yyyymmdd>.md` — one bundle doc per project, at the prompt's artifact target. A rerun or a scope correction updates that doc in place (or marks it superseded per the rescoping rule below), never a near-identically-named sibling; and a filename must never claim a scope ("all-epics") the content doesn't cover. Per bundle record: id + name, member stories, repos touched, entry gate (what must be answered or done first), estimated production lines, and status (proposed / in progress / merged / paused). UI bundle rows additionally carry the visual-target block above. Track per-bundle progress in the doc or a `docs/build-status.csv`.

Keep bundle state **out of `milestones.yaml`** — bundle composition churns fast; the yaml stays the slow decision layer (register rows, blockers, milestone notes).

## Rescoping rule

Absorb feedback by revising the bundle doc, never by rewriting story rows: add a rework bundle (e.g. R1), pause a bundle, or rescope with a prime suffix (B4′). Mark supersessions explicitly ("supersedes <file> for unbuilt work; that file stays the record of the original sizing") so history stays auditable. A rework bundle caused by visual drift inherits the visual-target block and reference renders from the original bundle row.

## Checklist

- [ ] Every remaining story is in exactly one bundle; bundles grouped by repo/stack/touch-point
- [ ] Each bundle ≤ ~1,500 production lines (or two parallel reviewers planned)
- [ ] Kickoff questions asked synchronously (internal) or opened as external-party rows before any code
- [ ] UI bundle rows carry a filled visual-target block (prototype, reference render, rulings)
- [ ] UI bundles: render-gate screenshots planned before review; env-check screenshot item planned
- [ ] Fix prompts that change what the user sees cite the prototype region or a ruling (else a CR)
- [ ] One env checklist per bundle planned
- [ ] Any repo new to delivery has its branch-delivery onboarding checklist run as an entry gate
- [ ] Bundle doc written; milestones.yaml untouched except decision-layer one-liners

## Relationship to other skills

- **milestones-authoring** — bundle state lives in docs; yaml changes only for decisions (register rows, blockers, dated notes).
- **decision-register** — kickoff questions and process decisions (merge discipline, lean gates) are register rows.
- **traceability-matrix** — story status still updates per story inside a bundle.
- **ux-template-spec** — where the prototype-authority rule and the spec-text it must match live; the bundle's visual-target block cites both.
- **code-review-standards** — the visual-fidelity and design-system review sections activate on UI bundles.
