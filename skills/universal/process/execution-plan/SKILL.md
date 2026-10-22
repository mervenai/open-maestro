---
id: execution-plan
name: Execution Plan & Architecture Verification
tags:
- maestro
- planning
- architecture
- verification
- milestones
---

# Execution Plan & Architecture Verification

Produce `docs/execution-plan-{date}.md` (maestro `plan-001`) the same way every time: re-verify the intake's load-bearing claims against the real repos, confirm or correct the PRD's architectural assumptions with evidence, and end with an impact map plus an explicit go/no-go for design.

Use when a task says "verify architecture against real repos", "plan-001", or before any design phase that assumes intake findings are true.

## Framing rules

- **Verify before building on.** The intake artifacts were written against the PRD and a snapshot of the repos; both move. A plan that skips re-verification rubber-stamps stale claims. Any claim that fails re-verification is flagged **before** the plan relies on it — fail loud, fail early.
- **Confirm or correct — never assume.** Every architectural assumption ends the pass with a verdict (`confirmed` / `corrected` / `refuted`) backed by a `file:line` you actually re-read. "Looks right" is not a verdict.

## Phase A — Re-verify the intake's load-bearing claims

Re-check every claim the synthesis and reuse assessment built on, including:

- Reuse assets (controllers, services, components, events) — do they still exist, at the cited location, with the cited shape?
- Contract assumptions — do the cited types, routes, and validation rules match the current code?
- Reuse instructions — is the "just reuse X" recipe still accurate, or has X drifted?

Any claim that fails: flag it by ID/source, state the corrected fact with evidence, and carry it into the plan's assumption table. If a failed claim invalidates an epic's coverage or a reuse-based estimate, say which one.

## Phase B — Verify the PRD's architectural assumptions

For each major assumption class:

- **Data sources** — where the data actually lives, its shape, freshness, access path
- **Auth** — the real attribute/policy code (trace it, don't name-match it), per hop
- **Events** — what is actually published/consumed today, payload shapes, delivery guarantees
- **Deployment topology** — where services run, how they reach each other, env boundaries

Per assumption: confirm with evidence, or correct/refute with evidence. Record every **contract mismatch** (PRD assumes a mechanism the code doesn't have) and **missing dependency** (integration the PRD needs but nothing provides).

## Citation discipline

Follow the blueprint-contract skill's citation discipline verbatim: re-read each cited `file:line` and confirm it proves the claim it anchors; label elements `[existing]` vs `[net-new]`; flag mechanism mismatches explicitly; apply the authority ladder (ratified decision > prototype/UI code > PRD > inference) to UI-facing values. Do not weaken these rules because this document is "just a plan" — it becomes the design phase's ground truth.

## Document shape

1. **Corrected architecture summary** — what the system actually looks like, post-verification, in a few paragraphs or one diagram reference.
2. **Assumption verification table** — assumption | verdict | evidence (file:line) | correction / mismatch | decision needed (ID).
3. **Repo/service impact map** — repo/service → affected files/areas → change type (`modify` / `extend` / `net-new`) → driven by which epic (`CE-n`).
4. **Go/No-Go recommendation for design** — exactly one of:
   - `GO` — assumptions hold; design may proceed
   - `GO WITH CONDITIONS` — design may proceed once the listed conditions (cited by `RR-n` / `OQ-n` / `D-n`) close
   - `NO-GO` — a load-bearing assumption failed and design would codify a wrong architecture; name the blocking item and its owner

## Decision proposals

Every correction that changes what should be built (not just how the plan describes it) is a design decision in waiting: propose it in ADR form (context / decision / consequences / rejected alternatives) with a `D-n` ID, following the decision-register skill. The plan hands these to plan-002 (ratified design decisions) — it does not ratify them itself.

## Definition of done

- [ ] Every load-bearing intake claim re-verified; failures flagged before reliance
- [ ] All four assumption classes verified with per-assumption verdicts and `file:line` evidence
- [ ] Contract mismatches and missing dependencies listed, each with a proposed `D-n` decision where it changes scope or approach
- [ ] Impact map names real repos/files and the epic that drives each change
- [ ] Exactly one go/no-go verdict; conditions cited by register ID
- [ ] Citation discipline met (re-read, proves-the-claim, authority ladder)
