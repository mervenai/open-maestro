# Security Scanning Parity Plan (SBOM + SAST/SCA/Secret)

Context: client proposal feedback flagged that maestro has no SBOM generation and no
SAST/SCA/secret scanning. Verified gaps (2026-09-07):

- Playbook has zero security prompts; nothing in the methodology triggers scanning.
- `skills/universal/security/security-scanning/SKILL.md` links six reference files;
  the entire `references/` directory is missing (dangling links).
- No SBOM tooling anywhere (no syft/CycloneDX/SPDX).
- Security agent (`agents/security.md`) is LLM+regex only; no Semgrep/CodeQL/Gitleaks.
- SCA knowledge exists (`dependency-audit` skill) but is never invoked by a milestone.

Design principle: maestro is an orchestrator — it does not scan itself. Parity means
(a) the methodology demands a security gate, and (b) the skills tell the delegated
agent exactly which real tools to run. Tool absence on the host must degrade
gracefully (skip + note in report), never hard-fail the gate.

## Phase 1 — Playbook wiring (maestro core)

1. Add prompt `impl-005` "Run security scan gate" to the `implementation` milestone
   (order 5, after impl-004, before qa-integration):
   - `agent_hint: security` (read-only agent — correct for scanning)
   - Runs, in order, skipping with a note any tool not installed:
     - `gitleaks detect --no-banner -f json` (secrets)
     - `semgrep scan --config auto --json` (SAST)
     - `npm audit --json` / `pip-audit -f json` (SCA — pick by manifest present)
     - `syft . -o cyclonedx-json=docs/security/sbom-{date}.json` (SBOM)
   - Writes `docs/security/scan-report-{date}.md`: findings by severity, SBOM path,
     go/no-go for QA handoff. Critical/high = blocker until triaged.
2. Add exit criterion to `implementation` milestone in
   `src/open_maestro/milestones/templates.py`:
   "Security scan report produced with no unaddressed critical/high findings".
   (qa-integration inherits it as an entry condition via the gate.)
3. Bump playbook 1.1.0 → 1.2.0; package 1.11.0 → 1.12.0.

## Phase 2 — Skills

4. Create the two load-bearing missing references under
   `skills/universal/security/security-scanning/references/`:
   - `tooling-matrix.md` — install + run commands per ecosystem, what each tool
     covers/doesn't, expected runtime, false-positive profile.
   - `supply-chain-and-sbom.md` — syft/CycloneDX/SPDX formats, when SPDX vs CycloneDX,
     where the SBOM gets delivered (client handoff, release attach), license
     metadata from the open-source-safety framework.
   Prune the remaining four dangling links from SKILL.md rather than write stubs
   (triage-and-remediation, ci-workflows, common-findings-and-fixes,
   open-source-safety) — re-add when real content exists.
5. Add a "Tool-Backed Scanning" section to `agents/security.md` Secret Detection
   Protocol pointing at gitleaks/trufflehog as the primary path, with the existing
   grep protocol as fallback when tools are absent.

## Phase 3 — Verify

6. Render smoke test: `/next` on implementation milestone shows the gate prompt with
   auto-appended artifact line; `/prompts implementation` lists 5 prompts.
7. `pytest tests/test_playbook.py tests/test_milestones.py` — must stay green.
8. Commit as one changeset; push after user confirmation.

## Explicit non-goals

- No CI workflow generation (client repos own their CI; maestro advises only).
- No new runtime/vendor work.
- No change to the 8-milestone schema — the gate is a prompt, not a milestone.

## Jira tickets to file (MSTRO)

1. **Playbook security gate**: impl-005 prompt + implementation exit criterion.
2. **SBOM generation support**: syft/CycloneDX via security-scanning skill + playbook wiring.
3. **Tool-backed SAST/secret scanning**: gitleaks/semgrep wiring + security agent update.
4. **Fix dangling skill references**: write tooling-matrix + supply-chain-and-sbom, prune 4 dead links.
