# Plan: Milestone Prompt Playbook from M3BudgetUpload Experience

## 1. Objective

Turn the completed **M3BudgetUpload** project (`~/projects/M3BudgetUpload`) into a reusable, data-driven **prompt playbook** inside Open Maestro. The playbook will suggest context-aware prompts for each of the 8 standard lifecycle milestones so future projects keep moving without the user having to remember what to ask next.

The experience should feel like this in `maestro --interactive`:

```text
> /next
Next milestone: Design Blueprint (import-flow)
Exit criteria:
  - Data/API contracts frozen and signed off
  - UX/template/spec artifacts approved
  - Jira stories with BDD acceptance criteria created

Suggested prompts:
  1. Draft the data contract for the import-flow epic based on the PRD and existing service DTOs.
  2. Generate Jira stories with BDD acceptance criteria for FR-01 through FR-22.
  3. Create a UX/template spec for the Excel upload modal and validate it against the platform patterns.
```

## 2. Sources to Mine in M3BudgetUpload

The project is a goldmine of real prompts and artifacts across all 8 milestones. We will extract from:

| Milestone | Source artifacts in M3BudgetUpload | What to extract |
|-----------|-----------------------------------|-----------------|
| **Intake & Discovery** | `docs/intake/synthesis-2026-06-09.md`, `requirements/PRD-create-financial-plan-from-import.md` | Scope IN/OUT questions, risk register prompts, open-question templates |
| **Execution Planning** | `docs/execution-plan-2026-06-18.md` | Architecture verification prompts, repo-impact mapping, decision ratification |
| **Design Blueprint** | `docs/blueprint-design-and-data-contract.md`, `docs/template-spec.md`, `docs/jira-import-stories.csv` | Data-contract drafting prompts, BDD story generation, template-spec prompts |
| **Build Planning** | `docs/build-plan-budget-import.md`, `docs/build-plan-audit-log.md`, `tests/fixtures/` | Phased implementation prompts, fixture-generation prompts, traceability matrix prompts |
| **Implementation** | `Core/`, `ProfitStrategyService/`, `AuditService/`, git history | Code-scaffolding prompts, stub-filling prompts, integration wiring prompts |
| **QA & Integration** | `docs/qa/*test-report*.md`, `docs/qa/*uat*.md`, `docs/qa/*manual-checklist*.md` | Test-report generation, UAT playbook prompts, bug-repro templating |
| **Demo & Delivery** | `docs/demo/M3-update-and-demo-2026-06-26.md`, `docs/demo/dry-run-transcript.txt`, `build/*.pptx` | Demo-script prompts, sign-off deck prompts, handoff-doc prompts |
| **Retrospective & Findings** | `docs/findings/*.md`, `docs/new-fullstack-developer-playbook.md` | Lessons-learned prompts, playbook-update prompts, known-issues capture |

We will also scan the **`.claude-mpm/PM_INSTRUCTIONS_CACHE.md`** and any conversation logs to recover the actual prompts that drove each phase.

## 3. Prompt Taxonomy

Each milestone will have a **prompt deck**: a set of reusable prompt templates that are ranked by typical sequence and relevance.

A prompt template has:

```yaml
id: design-blueprint-001
milestone: design-blueprint
order: 1
title: Draft data contract
prompt: |
  Read the PRD in requirements/ and the existing DTOs in {service_repo}.
  Draft a frozen data/API contract for the {epic_name} epic.
  Include request/response shapes, idempotency rules, error cases, and file references.
tags: [design, contract, backend]
agent_hint: researcher        # optional preferred agent role
artifact_target: docs/blueprint-design-and-data-contract.md
example_from: M3BudgetUpload/docs/blueprint-design-and-data-contract.md
```

Prompt categories per milestone:

| Category | Description |
|----------|-------------|
| `discover` | Read, summarize, identify gaps |
| `decide` | Ratify architecture or design decisions |
| `specify` | Produce contracts, specs, stories |
| `plan` | Build phased plans, traceability, fixtures |
| `build` | Generate or complete code |
| `verify` | Tests, QA, UAT, security |
| `present` | Demos, sign-off, handoff |
| `reflect` | Retrospective, playbook updates |

## 4. Extraction Methodology

We will use a mix of deterministic extraction and LLM summarization.

### Step 4.1: Artifact inventory
Run a structured scan of M3BudgetUpload and map every major artifact to a milestone and epic.

### Step 4.2: Prompt reconstruction
For each artifact, run an LLM pass (via Maestro itself) with a meta-prompt:

```text
You are a prompt engineer. Study this artifact from a completed project.
Extract the 1-3 user prompts that most likely produced it, or would produce an equivalent artifact in a new project.
For each extracted prompt, note:
- The milestone it belongs to
- The exit criterion it satisfies
- The target artifact path
- Any project-specific placeholders that should be variables (e.g., repo names, FR ranges)
```

### Step 4.3: Human curation
Review the extracted prompts, merge duplicates, and normalize placeholders. The output is a YAML playbook file.

### Step 4.4: Validation against acceptance criteria
For each milestone, confirm that executing the prompt deck in order satisfies the milestone's exit criteria.

## 5. Storage Schema

Prompts live in the Open Maestro package as YAML so they ship with the wheel and can be overridden per project.

```
src/open_maestro/milestones/playbooks/
  software-consulting.yaml      # default playbook derived from M3BudgetUpload
  __init__.py
```

Per-project overrides can be placed at:

```
.open-maestro/playbook.yaml
```

The schema:

```yaml
playbook_id: software-consulting
version: "1.0.0"
source_project: M3BudgetUpload
milestones:
  intake-discovery:
    - id: intake-001
      title: Summarize PRD and identify scope
      prompt: "Read the PRD/RFP and produce a concise scope summary with IN/OUT items..."
      agent_hint: researcher
      artifact_target: docs/intake/synthesis-{date}.md
      tags: [discover]
    - id: intake-002
      title: Create risk register
      prompt: "Based on the PRD and platform docs, create a risk register..."
      ...
  design-blueprint:
    - id: design-001
      title: Draft data contract
      ...
```

## 6. Integration Points in Maestro

### 6.1 `/next` command enhancement
`handle_next_command()` will load the playbook for the current milestone and append the top 3 suggested prompts to its output.

### 6.2 `/prompts [milestone]` command
New interactive command to list all prompts for a milestone, e.g.:

```text
> /prompts design-blueprint
```

### 6.3 Auto-suggest on milestone start
When a milestone is marked `in_progress`, Maestro will recall the playbook and surface the first prompt as a gentle hint.

### 6.4 Prompt execution
Selecting a suggested prompt number copies it into the input line (or executes it immediately), with project-specific placeholders filled from the current plan (project path, epic names, repo folders).

### 6.5 Agent routing
Each prompt may carry an `agent_hint` (e.g., `researcher`, `engineer`, `qa`, `documentation`). Maestro routes to the cheapest capable agent for that prompt category.

## 7. Implementation Slices

| Slice | Deliverable | Effort |
|-------|-------------|--------|
| **Slice 1: Inventory & mapping** | Script that maps M3BudgetUpload artifacts to milestones/epics | Small |
| **Slice 2: Prompt extraction** | Run meta-prompts over artifacts; produce draft YAML playbook | Medium |
| **Slice 3: Playbook schema & loader** | Add `PromptPlaybook` Pydantic model, YAML loader, default software-consulting playbook | Small |
| **Slice 4: `/next` enhancement** | Surface top prompts in `/next` output | Small |
| **Slice 5: `/prompts` command** | Add interactive command to browse and execute prompts | Medium |
| **Slice 6: Placeholder resolution** | Replace `{project_path}`, `{epic_name}`, `{service_repo}`, `{date}` etc. at runtime | Small |
| **Slice 7: Validation** | Run the playbook against a new project (e.g., FormulaAdmin) and confirm prompts produce sensible artifacts | Medium |

## 8. Open Questions

1. **Scope of source mining:** Should we also mine the `.claude-mpm/PM_INSTRUCTIONS_CACHE.md` and any Kimi conversation history, or restrict ourselves to committed artifacts?
2. **Playbook customization:** Do you want industry-specific playbooks later (e.g., `web-app`, `data-pipeline`, `rfp-response`), or is `software-consulting` sufficient for now?
3. **Prompt execution model:** Should selecting a prompt run it immediately, or paste it into the input line for editing before sending?
4. **Variable sources:** Which project variables should be auto-resolved? Current candidates: project path, epic name, repo folders, date, PRD path.
5. **Client visibility:** Should suggested prompts be included in the client dashboard (probably not), or remain internal to the developer experience?

## 9. Acceptance Criteria

- [ ] A default `software-consulting` playbook ships inside the Maestro wheel.
- [ ] `/next` shows 1-3 suggested prompts for the current milestone.
- [ ] `/prompts <milestone>` lists all prompts for that milestone.
- [ ] Prompts resolve project-specific placeholders at runtime.
- [ ] The playbook can be overridden per project via `.open-maestro/playbook.yaml`.
- [ ] Applying the playbook to a new project produces artifacts in the expected milestone folders.
