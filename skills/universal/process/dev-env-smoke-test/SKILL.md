---
id: dev-env-smoke-test
name: Dev Environment Smoke Test
tags:
- maestro
- milestones
- planning
- verify
- process
---

# Dev Environment Smoke Test

Verify — before implementation starts — that the environment where the work will be built and verified actually works, and record the result as the project's standing env playbook. Use for maestro `build-004`, or any task saying "smoke-test the dev environment" / "dev smoke tests".

## Why this step exists

Every task in the build plan silently assumes an environment exists where it can be exercised. This is the only step that checks the assumption, and the cheapest moment to find it false: a missing database, an ungranted RBAC role, or an unreachable auth broker discovered mid-sprint stalls every dependent story; discovered at planning close it is a named blocker with an owner. The output doc is then reused for the whole project: bundle env-checks, QA evidence citations, and as-built verification all point at its section numbers.

## Target selection

- The target comes from the prompt's `{dev_env}` answer: a preview/deployment URL or a local path. **Remote shared dev / ephemeral per-branch environments are preferred over local** whenever both are reachable — remote carries the real database, auth broker, and service networking. Local environments frequently cannot boot for environmental reasons (no DB, no cross-service auth, VPN-only dependencies); that is an environmental block, not a product failure.
- Blank answer = discover before acting: check chart values (`values*.yaml`), CI/deploy configs, deploy scripts, recent branches/PRs, and `.open-maestro` history for environment URLs. If candidates are found, state which one you chose and why. If none are found, **stop and report** — never invent a URL.

## Liveness rules (the anti-hang contract)

- **Never leave a foreground server running.** Do not invoke `docker compose up`, `dotnet run`, `npm start`, or equivalents as a blocking step; use detached/background mode with a health-endpoint probe, or prefer an already-running environment.
- **One clean boot attempt per service, plus at most one retry** after a config fix you can articulate. If it still fails, record BLOCKED and move on — no retry loops, no log tailing while waiting.
- **A missing dependency is BLOCKED with evidence, not a failed smoke test.** No reachable DB/broker, missing env vars, VPN-only endpoint unreachable → record the exact gap and the owner-level fix (access request, secret name), then move to the next service.
- **Every network check carries a timeout (default 30 s)** and records the exact command plus the status/output excerpt that justifies the verdict.

## Procedure: remote URL

1. **Reachability** — GET the base URL; record status, redirect chain, TLS outcome.
2. **Health per affected service** — deploy/health endpoints (`/health`, `/healthz`, `/ready`, actuator or platform equivalents). A health check must prove dependency connectivity (DB, broker), not merely that a process is up.
3. **Auth path** — exercise or trace the login flow the app uses (broker/SSO); record what was tried and the outcome. Do not block on interactive SSO — record it as a workaround if it needs a human.
4. **Config surface** — record the env var / config names required to point services at this environment (names only).

## Procedure: local path

1. List the affected services from the build plan: boot command, expected port, dependency list (DB, auth, other services).
2. **Check dependencies before booting anything** — a service whose DB is unreachable boots into a wall.
3. Boot detached, with a timeout; verify via a health endpoint or first-request probe, not process liveness or log tailing.
4. Record env var names only; leave nothing running when the pass ends.

## The smoke doc

Author or update `docs/dev-smoke-test.md` with:

- **Header** — environment target, date, branch/sha, verdict.
- **Verdict vocabulary** — exactly `PASS` / `PASS WITH WORKAROUNDS` / `BLOCKED`. Three values, the same discipline as the traceability matrix: resist `mostly works`, `green except auth`.
- **Per-service table** — service | how reached (URL:port or boot command) | startup/deploy steps | env var names | health check + result | failures/workarounds.
- **Blocked table** — item | what is missing | evidence | proposed fix + owner.
- **Deploy path** — if the environment builds from git (CI relay + GitOps values), author `docs/delivery-routing.md` per the branch-delivery skill's template from what this smoke run proves, cite it here, and record the one-time path verification (a pointer swap or trivial relayed change shifts the served artifact). If the environment is **not** git-backed (manual deploys, managed platform pipelines outside the project's control), record that in one line. The omission must be explicit — "no routing config" is a verdict, not a default.

Once sections exist, keep their numbers stable: downstream artifacts cite `§x.y` of this doc as evidence, and renumbering breaks those citations.

## Checklist

- [ ] Target chosen (remote preferred) and stated; discovery findings recorded if the answer was blank
- [ ] Every affected service has a health check proving dependency connectivity
- [ ] Deploy path settled: `docs/delivery-routing.md` authored when the environment builds from git, or "not git-backed" recorded as a one-line verdict in the smoke doc
- [ ] At most one boot attempt plus one articulated retry per service; no servers left running
- [ ] Every check has a timeout; commands and outputs recorded as evidence
- [ ] No secret values in the doc — names only
- [ ] Verdict ∈ {PASS, PASS WITH WORKAROUNDS, BLOCKED}; every BLOCKED item carries an owner-level fix
- [ ] `docs/dev-smoke-test.md` updated; section numbers stable

## Relationship to other skills

- **bundle-execution** — the per-bundle env-check appends its evidence here; a BLOCKED verdict gates dependent bundles.
- **branch-delivery** — when the environment builds from git (CI relay + GitOps), document the code→env deploy path from the smoke run as structured config in `docs/delivery-routing.md` per that skill's template, and verify the path once end to end (a pointer swap or trivial relayed change shifts the served artifact). The routing config is then the source the per-push relay checks run against.
- **traceability-matrix** — as-built and QA citations point at this doc's section numbers; stable numbering is part of the contract.
- **milestones-authoring** — a BLOCKED verdict becomes a milestone blocker with an owner; do not leave it in the doc only.
