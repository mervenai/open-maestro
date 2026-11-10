---
id: branch-delivery
name: Branch Delivery (standing-branch delivery to ephemeral environments)
tags:
- maestro
- milestones
- implementation
- process
---

# Branch Delivery

Deliver implementation work to a shared or ephemeral dev environment through **standing integration branches** — one long-lived branch per impacted repo, with standing draft PRs that CI builds from — instead of per-story pushes. Use when a project's environment builds from git (CI relays artifacts, GitOps syncs), when several repos feed one environment, or when a task says "merge, push, and deploy".

## Why standing branches

Per-story pushes toward a shared environment surrender control of CI: image pushes, shared-namespace deploys, and release pipelines can all trigger on branch patterns you didn't intend. The proven failure this prevents: a feature branch matching a repo's `team-*/feature-*` push trigger started a CI build and a team-release deploy into the shared dev namespace — the environment the project was *not* targeting. Naming and PR conventions are how you steer CI; treat them as controls, not cosmetics.

## The model

1. **One integration branch, the same name in every impacted repo** (shape: `<org-prefix>/feature-<project>`). Choose the prefix deliberately: enumerate each repo's push/PR branch triggers (`.github/workflows/`, `azure-pipelines.yml`, branch filters) *before* naming, and pick a name that matches none of the unintended ones. Remember the relay usually keys on the **last branch segment only**, so that segment must be unique across teams.
2. **Story work stays local.** Story branches/worktrees are never pushed. Only after the full unit suite, a production build, and an independent review: merge `--no-ff` into the integration branch (one merge commit per story), then push the integration branch.
3. **One standing DRAFT PR per deploy repo**, into `main`, opened once: required labels set at creation time (some CI reads labels only when the build starts — there is no `labeled` trigger to rely on), body carrying the org's required tokens (work-item reference, a DO-NOT-CLOSE line naming the environment the PR feeds). No tooling footers.

## Push → deploy → verify loop

1. Push → PR synchronize → CI builds the **merge ref** → images/artifacts relayed (typically a relay branch per repo, e.g. `ci/<Repo>/<last-segment>`).
2. **Verify the relay moved after EVERY push.** Record `git ls-remote <relay-repo> "refs/heads/ci/<Repo>/<segment>"` before and after. An unmoved relay means: build skipped (many workflows no-op without a code diff), build failed, or **the PR has merge conflicts** — and the conflict case is silent, because a conflicted PR has no merge ref to build: nothing relays, and the environment keeps serving the last good build with no failure anywhere. If the first push after onboarding doesn't relay, check the no-code-diff skip before anything else.
3. **Conflict rhythm.** Merge `main` INTO the integration branch at least weekly and immediately when the PR shows conflicts (plain merge commit); re-run the relay check afterward. The deployed artifact already contains main's latest changes via the merge ref — the merge keeps the PR buildable.
4. **Know each repo's fallback.** Some repos offer a manual dispatch build that relays branch-head artifacts and works while the PR is conflicted; others have none, and a conflicted PR there means deploys stop until resolved. Record fallback availability per repo in the routing config (below).

## Environment pointer

GitOps environments select code through a values file, not through deploys you run: a per-environment branch/file maps service keys to a branch segment (shape: `services.<key>.branch: <segment>`; a `sha` key, where the chart supports it, takes precedence over `branch`). Rules:

- One pointer edit per service, at onboarding. After that, you ship code by pushing the integration branch — never per-story pointer edits.
- Key names are chart-version dependent. Verify each key against the chart version the environment actually runs; never copy entries from older environment branches.
- After changing a pointer, verify the environment actually shifted (served artifact hash changed) once — after that, the relay check per push is sufficient.

## Repo onboarding checklist

Run when a bundle or story pulls a repo into delivery for the first time — it is an entry gate, not paperwork:

- [ ] Push rights verified for the local git identity on the repo and on the values/relay repos (`git ls-remote` is enough)
- [ ] Repo CI triggers enumerated; integration-branch name confirmed to avoid unintended triggers
- [ ] Branch created from up-to-date `main` and pushed (remember: no code diff vs main may mean no build)
- [ ] Standing draft PR opened with labels set at creation and the required body tokens
- [ ] Relay branch confirmed moved after the first real push
- [ ] Environment values key set for this service; env shift verified once
- [ ] Gate baseline recorded: build/test baseline, format/lint gates, anything the repo's CI enforces that the project must pre-run
- [ ] Fallback build availability recorded (dispatch build / none)

## Final merge to main (project end)

- One merge per repo in a coordinated order, feature flagged off first; **tag the integration head before merging** — per-story history lives in the integration branch and squash-only rulesets will not preserve it in `main`.
- Respect the org ruleset (approvals, required checks, conventional titles). **Never merge to `main` without explicit user approval.**
- Cleanup CI leaves behind: orphaned relay `ci/<Repo>/<segment>` branches (and stale images where no retention policy exists).

## Evidence conventions (record in the bundle's build note, per merge/push)

- Merge SHA, plus proof its tree equals the reviewed story-head tree (`git diff <merge> <story-head> --stat` empty)
- Push range (`<from>..<to>`)
- PR/build numbers and result
- Relay SHA before → after
- Served-artifact verification when reachable (e.g. environment chunk hash byte-compared against the local production build)

## The delivery-routing config

Keep the per-project parameters in `docs/delivery-routing.md` — structured config, not prose. Shape:

```markdown
# Delivery Routing — <project>

| Item | Value |
|---|---|
| Integration branch | `<name>` (same in all repos; prefix avoids `<trigger patterns>`) |
| Relay repo / pattern | `<org>/<repo>` / `ci/<RepoName>/<last-segment>` |
| Values repo / env branch / file | `<repo>@<branch>` / `<path>` |
| PR labels | `<label>`, `<label>` (set at creation) |
| PR body tokens | `<work-item id>`, `<DO-NOT-CLOSE line>` |

| Repo | Deploy repo? | Values key | CI (GHA/ADO/other) | Relay fallback | Notes |
|---|---|---|---|---|---|
| `<RepoA>` | yes | `services.<keyA>.branch` | GHA | dispatch build | |
| `<RepoB>` | yes | `services.<keyB>.branch` | ADO | **none** | conflict = deploy stall |
| `<RepoC>` | no | — | — | — | storage only; run suites locally with URL overrides |
```

Discover or re-verify every row at smoke-test time: `dev-env-smoke-test` documents the deploy path — write it here as this config. Update the config when a repo onboards, a key changes, or fallback availability changes. A routing doc that disagrees with the environment is worse than none; re-verify a row before trusting it.

## Relationship to other skills

- **bundle-execution** — the merge/push step defers to this skill; a bundle pulling a new repo into delivery runs the onboarding checklist as its entry gate; the per-bundle env-check appends the relay-moved proof.
- **dev-env-smoke-test** — the smoke doc's deploy-path section is the discovery pass that produces and updates this config.
- **decision-register** — branch naming, PR conventions, and merge discipline are decision rows, not folklore; record them so the next project inherits the reasoning.
- **milestones-authoring** — final merge and environment decommissioning are milestone notes with owners, not afterthoughts.
