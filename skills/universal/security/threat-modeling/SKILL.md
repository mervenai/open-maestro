---
id: threat-modeling
name: Threat Modeling
tags:
- security
- threat-modeling
- stride
- architecture
- risk
---

# Threat Modeling (STRIDE)

## Workflow

1. **Scope** — Identify the system boundary, assets (PII, credentials, payments), and availability requirements (SLO/SLA).
2. **Data Flow Diagram** — Map actors, entry points, data stores, and external dependencies. Mark trust boundaries (public internet → edge → internal → database → third-party).
3. **STRIDE per element** — For each element in the diagram, walk through all six STRIDE categories (Spoofing, Tampering, Repudiation, Information disclosure, Denial of service, Elevation of privilege) and record threats.
4. **Risk score** — Rate each threat by Impact (Low/Med/High) and Likelihood (Low/Med/High). Prioritize High-impact + Med/High-likelihood items first.
5. **Mitigate** — Convert each prioritized threat into engineering tasks, verification tasks (tests, alerts), and operational controls (runbooks, access reviews).
6. **Tickets and tests** — Create backlog items for mitigations and add abuse-case tests for critical flows. Add PR checklist items for ongoing verification.

## Example: Threat Register Row

| Element | STRIDE | Threat | Impact | Likelihood | Mitigation | Owner | Status |
|---------|--------|--------|--------|------------|------------|-------|--------|
| API Gateway | Spoofing | Stolen JWT reuse after session revocation | High | Med | Short-lived tokens (15 min TTL), refresh rotation, revocation list check on each request | Security | Open |

This single row drives three artifacts: an engineering ticket (implement revocation-list middleware), a test (verify revoked token returns 401 within TTL window), and a PR checklist item (authz checks for new endpoints).

## Validation Checkpoint

Before finalizing, verify completeness:

- [ ] Every element in the data flow diagram has at least one STRIDE entry
- [ ] All High-impact threats have an assigned owner and mitigation
- [ ] Each mitigation maps to a backlog ticket or test case
- [ ] Threat model doc includes assumptions and scope boundaries
- [ ] PR checklist updated with new security requirements

## Outputs (Definition of Done)

Produce a data flow diagram, a threat register, and a mitigation plan that becomes tickets and tests.

## Load Next (References)

- `references/stride-workshop.md` — step-by-step workshop agenda + DFD guidance
- `references/common-threats-and-mitigations.md` — threat catalog with mitigations
- `references/templates.md` — copy/paste templates for docs and tickets