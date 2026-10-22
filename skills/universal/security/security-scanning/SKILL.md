---
id: security-scanning
name: Security Scanning
tags:
- security
- scanning
---

# Security Scanning

## Quick Start

- Secrets: fail fast; rotate on exposure.
- Dependencies: gate critical/high; automate updates.
- SAST: start high-signal; ratchet over time.
- Open Source Safety: score components on three axes — license tier, severity-weighted CVEs, obsolescence.
- Exceptions: require reason, owner, and expiry.

## Open Source Safety

Third-party component risk is more than "vulnerable: yes/no". Evaluate each component on
three independent dimensions and gate on the worst:

- **License risk:** HIGH = strong copyleft / GPL/AGPL/LGPL (whole-app disclosure risk);
  MEDIUM = weak copyleft / MPL, EPL (modification disclosure only); LOW = permissive /
  MIT, Apache-2.0, BSD. Unknown/`NOASSERTION` → treat as HIGH until identified.
- **CVE weighting:** weight by severity (critical ≫ high ≫ medium ≫ low) rather than raw
  counts; critical/high block, medium/low track with owner + expiry.
- **Obsolescence:** score the gap to latest version; majors-behind or unmaintained
  upstream is elevated risk.

The tier tables, CVE weighting model, obsolescence scoring, and
transitive-dependency trust model are documented inline above; this section is the
authoritative copy.

## Milestone scan gate (impl-005)

Pre-QA security gate for a maestro epic: run the tool suite, group findings by severity, and issue a go/no-go for QA handoff. Report at `docs/security/scan-report-{date}.md`; SBOM at `docs/security/sbom-{date}.json`.

| Layer | Tool & command | Records |
|-------|----------------|---------|
| Secrets | `gitleaks detect --no-banner` | Findings with file:line. A secret in a git-tracked file is flagged for **immediate rotation** — removing the line does not un-leak it. |
| SAST | `semgrep scan --config auto` | Findings by severity and rule ID. |
| SCA | `npm audit --json` if `package.json` exists, else `pip-audit` if `requirements.txt`/`pyproject.toml` exists | Critical/high CVEs with package and version. |
| SBOM | `syft . -o cyclonedx-json=docs/security/sbom-{date}.json` | The SBOM artifact itself; attach to the report. |

**Gate rules:**

- **Skip-not-fail:** if a tool is not installed on this machine, record the skip in the report and continue — a missing tool never fails the gate. (Installing tools is the local-ops track, not this gate's job.)
- **Critical or high findings are blockers until triaged with an owner** — not until fixed: triaged-with-owner-and-expiry satisfies the gate, mirroring the exceptions rule above.
- **Go/No-Go for QA handoff:** `GO` (no critical/high), `GO WITH CONDITIONS` (blockers triaged with owner + expiry, listed), `NO-GO` (untriaged critical/high, or secrets in git-tracked files awaiting rotation).
- Findings are grouped by severity (critical/high/medium/low) with the producing tool noted per finding — the same finding from two tools is two records, so dedup decisions stay visible.

The full supply-chain policy (license tiers, CVE weighting, obsolescence scoring) is in `references/supply-chain-and-sbom.md`; this section is the operational gate and is the authoritative copy of the procedure.

## Load Next (References)

- `references/tooling-matrix.md`
- `references/supply-chain-and-sbom.md`