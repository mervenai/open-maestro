# Tooling Matrix

Install + run commands for the maestro security scan gate, per ecosystem. The gate
(`impl-005` in the software-consulting playbook) expects these four tools. If a tool
is not installed, skip it and record the skip in the scan report — never fail the
gate for a missing tool.

## Secrets: Gitleaks

| | |
|---|---|
| Covers | Hardcoded API keys, tokens, passwords, private keys in git history and working tree |
| Does not cover | Secrets in ignored-but-committed files already rotated; entropy-only short strings |
| Install | `brew install gitleaks` / `choco install gitleaks` / download from github.com/gitleaks/gitleaks |
| Run | `gitleaks detect --no-banner -v` (repo) · `gitleaks dir .` (working tree only) |
| Runtime | Seconds to ~1 min on large histories |
| False positives | Low-medium; use `.gitleaks.toml` allowlists for test fixtures and example keys |

Trufflehog (`trufflehog git file://. --only-verified`) is a stronger alternative when
you need live-verification of whether a credential still works — use it for
pre-release audits, gitleaks for the routine gate.

## SAST: Semgrep

| | |
|---|---|
| Covers | Injection (SQLi/XSS/command/path traversal), authz flaws, insecure crypto, dangerous deserialization, secrets-adjacent patterns |
| Does not cover | Business-logic authz, framework-specific misconfigurations without rules, taint across service boundaries |
| Install | `brew install semgrep` / `pip install semgrep` |
| Run | `semgrep scan --config auto --json` (repo) · add `--severity ERROR` to focus |
| Runtime | 1-5 min typical repo; `--config auto` downloads community rulesets on first run |
| False positives | Medium on auto config; ratchet: baseline with `semgrep scan --baseline-commit main`, then gate on new findings only |

CodeQL is the heavier option (needs a build, GitHub Actions or CLI with a DB) —
justified for regulated clients; semgrep is the right default for the gate.

## SCA: npm audit / pip-audit

| | |
|---|---|
| Covers | Known CVEs in declared dependencies (npm audit also flags via lockfile) |
| Does not cover | Transitive license risk (see open-source-safety), vendored code, obsolescence |
| Node | `npm audit --json` · gate: `npm audit --audit-level=high` |
| Python | `pip install pip-audit` · `pip-audit -f json` (requirements.txt / pyproject.toml / Pipfile.lock) |
| Runtime | Seconds |
| False positives | Low on CVE match; medium on "affected but not exploitable" — triage by call path |

For continuous monitoring point Snyk or Dependabot at the repo — but the gate only
needs the local commands above.

## SBOM: Syft

See `supply-chain-and-sbom.md` for formats and delivery. Quick reference:

| | |
|---|---|
| Install | `brew install syft` / `curl -sSfL https://get.anchore.io/syft \| sh` |
| CycloneDX JSON | `syft . -o cyclonedx-json=docs/security/sbom-<date>.json` |
| SPDX | `syft . -o spdx-json=docs/security/sbom-<date>.spdx.json` |
| Runtime | Seconds-minutes by image/repo size |
| Notes | No false positives — it's inventory, not analysis; accuracy depends on lockfile presence |

## Host check snippet

```bash
for t in gitleaks semgrep syft; do command -v $t >/dev/null && echo "$t: ok" || echo "$t: MISSING"; done
```
