---
id: terraform
name: Terraform
tags:
- terraform
- iac
- infrastructure
- provisioning
- ci
- state
---

# Terraform

## Quick Start (workflow)

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

## Safety Checklist

- State: remote backend + locking; separate state per environment
- Reviews: plan in CI; apply from a trusted runner with approvals
- Guardrails: `prevent_destroy` and policy checks for prod

## Load Next (References)

- `references/state-and-environments.md` — backends, locking, workspaces vs separate state, drift
- `references/modules-and-composition.md` — module interfaces, versioning, composition patterns
- `references/workflows-and-guardrails.md` — CI plan/apply, policy-as-code, safe migrations