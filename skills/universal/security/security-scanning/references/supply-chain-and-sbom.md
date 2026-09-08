# Supply Chain & SBOM

A Software Bill of Materials is a structured inventory of everything the software
contains: direct and transitive dependencies, versions, hashes, licenses, and
supplier metadata. Maestro generates it at the implementation gate with Syft.

## Why clients ask for it

- Procurement/security review: the client's security team needs to know what they
  are running before approving deployment.
- Regulatory pressure: US EO 14028 and follow-on rules (NTIA minimum elements),
  EU CRA (Cyber Resilience Act), and enterprise vendor questionnaires increasingly
  demand SBOMs at delivery.
- Incident response: when the next log4shell-class CVE lands, an SBOM answers
  "are we affected, where?" in minutes instead of days.

## Formats: CycloneDX vs SPDX

| | CycloneDX | SPDX |
|---|---|---|
| Origin | OWASP | Linux Foundation / ISO 5962:2021 |
| JSON | Native, compact | JSON is a lossy serialization of the spec |
| Ecosystem tooling | Best-in-class (Syft, Trivy, Dependency-Track) | Strong in compliance/legal tooling |
| License data | Good | Superior (purpose fields, exceptions) |
| Vulnerability linkage | Native `vulnerabilities` field | Via external references |
| Use when | Default for delivery + Dependency-Track ingestion | Client legal/compliance explicitly asks |

Default to **CycloneDX JSON**. Emit SPDX only when the client's compliance team
asks for it — Syft produces both from the same scan, so cost is one flag.

## Generation

```bash
# From source repo (uses lockfiles: package-lock.json, requirements.txt, etc.)
syft . -o cyclonedx-json=docs/security/sbom-$(date +%F).json

# From a built container image (most complete — captures the actual runtime)
syft registry.example.com/app:1.4.2 -o cyclonedx-json=sbom.json
```

Accuracy rules:
- The SBOM is only as good as the lockfile it reads. Commit `package-lock.json` /
  `pnpm-lock.yaml` / `requirements.txt` (hashed) — never generate from loose
  manifests alone if you can avoid it.
- Scan the artifact you ship. A repo scan lists what you *declare*; an image scan
  lists what you *run*. For client delivery, prefer the image SBOM when the
  deliverable is deployed.
- Regenerate at every release. A stale SBOM is worse than none (false confidence).

## Delivery targets

| Target | When | Format |
|---|---|---|
| `docs/security/sbom-<date>.json` in the project repo | Every gate run (implementation milestone) | CycloneDX JSON |
| Attached to the release/tag | At delivery, per client contract | CycloneDX JSON (+ SPDX if requested) |
| Dependency-Track server | Ongoing: upload each SBOM, get continuous CVE monitoring of components | CycloneDX JSON |
| Client procurement portal | On request | Their format; usually SPDX or CycloneDX |

## Beyond inventory: license metadata

Syft records per-component licenses. Cross-reference with the open-source-safety
tier model: strong copyleft (GPL/AGPL/LGPL) appearing in a distributed deliverable
is a delivery blocker, not a footnote — surface it in the scan report next to the
CVE findings.

## Minimum viable gate output

The impl-005 gate must produce, at minimum:
1. `docs/security/sbom-<date>.json` — CycloneDX JSON from the repo or image.
2. A license section in the scan report: count by tier, list any HIGH-tier
   (strong copyleft) components with an owner decision.
3. A freshness note: SBOM generated from lockfile vs loose manifest, and any
   components where the version could not be resolved.
