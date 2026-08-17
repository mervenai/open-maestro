# Milestone-Guided Experience Design for Open Maestro

## 1. Executive Summary

This document proposes a milestone-guided experience for Open Maestro, derived from an analysis of the completed M3BudgetUpload project (`~/projects/M3BudgetUpload`). The goal is to give Maestro a structured project lifecycle that:

1. Auto-detects where a project is in its lifecycle from existing artifacts.
2. Guides the user toward the next uncompleted milestone.
3. Persists milestone state in project-scoped memory.
4. Exports a client-safe progress view for an external dashboard.

The taxonomy is intentionally generic enough to fit software consulting, product engineering, and RFP response workflows, while remaining concrete enough to be auto-detected from the artifact patterns seen in M3BudgetUpload.

---

## 2. Observed Lifecycle in M3BudgetUpload

The project produced a .NET/Angular feature called **"Create Financial Plan from Excel Import"** for M3 ProfitStrategy. Its git history spans **2026-06-18 to 2026-07-17** (~4 weeks of active work, with a dense 2-week implementation window). The artifact folders reveal a clear lifecycle:

| Phase | Dates | Key Artifacts | Purpose |
|-------|-------|---------------|---------|
| **Intake / Discovery** | Jun 9 | `docs/intake/synthesis-2026-06-09.md`, `requirements/PRD-create-financial-plan-from-import.md` | Read PRD, platform docs, prior experiment transcript; identify scope, risks, and open questions. |
| **Execution Planning** | Jun 18 | `docs/execution-plan-2026-06-18.md` | Verify architecture against real repos; freeze design decisions. |
| **Design / Blueprint** | Jun 23–24 | `docs/blueprint-design-and-data-contract.md`, `docs/template-spec.md`, `docs/jira-import-stories.csv` | Finalize data/API contracts, UX flow, template spec, Jira stories. |
| **Build Planning** | Jun 25–26 | `docs/build-plan-budget-import.md`, `docs/build-plan-audit-log.md`, `tests/fixtures/` | Phased work breakdown A–H, fixtures, traceability matrix. |
| **Implementation** | Jun 25–Jul 3 | `Core/`, `ProfitStrategyService/`, `AuditService/`, `NugetWebPackages/` code changes | Backend endpoints, frontend modal, SignalR wiring. |
| **QA / UAT** | Jul 3–Jul 9 | `docs/qa/`, test reports, UAT playbooks | E2E, security, sign-off. |
| **Demo / Delivery** | Jun 26, Jul 7 | `docs/demo/`, `build/M3_Signoff_BudgetImport.pptx` | Demo script, dry-run transcript, sign-off deck. |
| **Retrospective / Findings** | Jun 27–Jul 17 | `docs/findings/`, `docs/audit-log/` | Lessons learned, known issues, updated playbooks. |

### Key insight

The project did not follow a single linear "plan → build → test" sequence. Instead, it had **parallel tracks** (import track vs. audit-log track) and **feedback loops** (design reversals on Q8/Q9, contract amendments, QA findings feeding back into implementation). Any milestone model must support:

- Parallel tracks within a milestone.
- Revisions/reversals without breaking progress tracking.
- Artifact-driven state detection, not just user self-reporting.

---

## 3. Proposed Milestone Taxonomy

Based on the observed lifecycle, Open Maestro should use **8 core milestones**. Each milestone has a clear Definition of Done (DoD), required artifacts, and client-visibility settings.

### 3.1 Core Milestones

| # | Milestone | Client Visible | Weight | Typical Duration | DoD |
|---|-----------|----------------|--------|------------------|-----|
| 1 | **Intake & Discovery** | Partial | 10% | 1–3 days | PRD/RFP read, scope in/out defined, risk register created, sprint-blocking open questions identified. |
| 2 | **Execution Planning** | Partial | 10% | 1–3 days | Architecture verified against real repos, design decisions ratified, repo/service impact map created. |
| 3 | **Design Blueprint** | Yes | 15% | 2–5 days | Data/API contracts frozen, UX flow signed off, Jira/story backlog created, template/spec artifacts approved. |
| 4 | **Build Planning** | Yes | 10% | 1–3 days | Phased implementation plan with ownership, traceability matrix, test fixtures, dev env smoke tests ready. |
| 5 | **Implementation** | Yes (high-level) | 30% | 1–4 weeks | All P0 stories implemented, unit/integration tests passing, code merged to feature branch. |
| 6 | **QA & Integration** | Yes | 15% | 3–7 days | E2E tests pass, security/scope verified, UAT playbook executed, sign-off report produced. |
| 7 | **Demo & Delivery** | Yes | 5% | 1–2 days | Demo delivered, handoff docs complete, stakeholder sign-off obtained. |
| 8 | **Retrospective & Findings** | Partial | 5% | 1–2 days | Lessons learned documented, known issues captured, playbooks updated, team debrief complete. |

### 3.2 Parallel Tracks

Within milestones 3–6, Maestro should support named **tracks**. For M3BudgetUpload, the tracks were:

- `import-flow` (FR-01 → FR-22)
- `audit-log` (FR-23 → FR-28)

Tracks have their own status and blockers but roll up into the parent milestone.

### 3.3 Artifact-to-Milestone Mapping

Maestro can auto-detect milestone state by scanning for artifact patterns:

| Milestone | Auto-detect Patterns |
|-----------|----------------------|
| Intake & Discovery | `requirements/*`, `docs/intake/*`, `docs/*synthesis*.md`, mentions of "open questions" or "risk register" |
| Execution Planning | `docs/execution-plan*.md`, `docs/*architecture*.md`, `docs/*decisions*.md` |
| Design Blueprint | `docs/blueprint*.md`, `docs/*spec*.md`, `docs/*contract*.md`, `docs/jira*.csv`, prototype files |
| Build Planning | `docs/build-plan*.md`, `tests/fixtures/`, `docs/*traceability*.md` |
| Implementation | Code changes in tracked service/repo folders, PRs, commit volume above threshold |
| QA & Integration | `docs/qa/`, `docs/*test-report*.md`, `docs/*uat*.md`, `tests/e2e*/` |
| Demo & Delivery | `docs/demo/`, `docs/*demo*.md`, `build/*.pptx`, sign-off decks |
| Retrospective & Findings | `docs/findings/`, `docs/*retrospective*.md`, `docs/audit-log/`, updated playbooks |

---

## 4. Milestone Schema

Milestone state should be stored as YAML in `.open-maestro/milestones.yaml` (project-scoped) and mirrored into Kuzu memory for semantic retrieval.

```yaml
project_id: m3-budget-upload
project_path: /Users/jj/projects/M3BudgetUpload
schema_version: 1.0
last_updated: 2026-07-17T00:00:00Z

milestones:
  - id: intake-discovery
    name: Intake & Discovery
    order: 1
    weight: 10
    client_visible: true
    status: completed        # not_started | in_progress | blocked | completed | skipped
    started_at: 2026-06-09
    completed_at: 2026-06-09
    tracks: []               # empty for single-track milestones
    artifacts:
      - path: requirements/PRD-create-financial-plan-from-import.md
        required: true
        detected: true
      - path: docs/intake/synthesis-2026-06-09.md
        required: true
        detected: true
    exit_criteria:
      - PRD/RFP read and summarized
      - Scope IN/OUT documented
      - Sprint-blocking open questions identified
    blockers: []
    notes: "Synthesis produced 8 open questions and 8 risks."

  - id: execution-planning
    name: Execution Planning
    order: 2
    weight: 10
    client_visible: true
    status: completed
    started_at: 2026-06-18
    completed_at: 2026-06-18
    artifacts:
      - path: docs/execution-plan-2026-06-18.md
        required: true
        detected: true
    exit_criteria:
      - Architecture verified against real repos
      - Design decisions ratified (D1–D4)
      - Repo/service impact map documented
    blockers: []

  - id: design-blueprint
    name: Design Blueprint
    order: 3
    weight: 15
    client_visible: true
    status: completed
    started_at: 2026-06-23
    completed_at: 2026-06-24
    tracks:
      - id: import-flow
        name: Import Flow Design
        status: completed
      - id: audit-log
        name: Audit Log Design
        status: completed
    artifacts:
      - path: docs/blueprint-design-and-data-contract.md
        required: true
        detected: true
      - path: docs/template-spec.md
        required: true
        detected: true
      - path: docs/jira-import-stories.csv
        required: true
        detected: true
    exit_criteria:
      - Data/API contracts frozen and signed off
      - UX/template spec approved
      - Jira stories with BDD acceptance criteria created
    blockers: []

  - id: build-planning
    name: Build Planning
    order: 4
    weight: 10
    client_visible: true
    status: completed
    started_at: 2026-06-25
    completed_at: 2026-06-26
    tracks:
      - id: import-flow
        name: Import Flow Build Plan
        status: completed
      - id: audit-log
        name: Audit Log Build Plan
        status: completed
    artifacts:
      - path: docs/build-plan-budget-import.md
        required: true
        detected: true
      - path: docs/build-plan-audit-log.md
        required: false
        detected: true
      - path: tests/fixtures/
        required: true
        detected: true
    exit_criteria:
      - Phased implementation plan created
      - Traceability matrix FR → file/test
      - Test fixtures prepared
      - Dev environments smoke-tested
    blockers: []

  - id: implementation
    name: Implementation
    order: 5
    weight: 30
    client_visible: true
    status: completed
    started_at: 2026-06-25
    completed_at: 2026-07-03
    tracks:
      - id: import-flow
        name: Import Flow Implementation
        status: completed
      - id: audit-log
        name: Audit Log Implementation
        status: completed
    artifacts:
      - path: ProfitStrategyService/M3.ProfitStrategyService.API/Controllers/PlanImportController.cs
        required: true
        detected: true
      - path: Core/M3.Core/ClientApp/.../budgets-and-forecast-new-budget-modal.component.ts
        required: true
        detected: true
    exit_criteria:
      - All P0 stories implemented
      - Unit/integration tests passing
      - Code merged to feature branch
    blockers: []

  - id: qa-integration
    name: QA & Integration
    order: 6
    weight: 15
    client_visible: true
    status: completed
    started_at: 2026-07-03
    completed_at: 2026-07-09
    tracks:
      - id: import-flow
        name: Import Flow QA
        status: completed
      - id: audit-log
        name: Audit Log QA
        status: completed
    artifacts:
      - path: docs/qa/budget-import-test-report.md
        required: true
        detected: true
      - path: docs/qa/budget-import-uat-playbook-M3.md
        required: true
        detected: true
    exit_criteria:
      - E2E tests pass
      - Security/scope verified
      - UAT playbook executed
      - Sign-off report produced
    blockers: []

  - id: demo-delivery
    name: Demo & Delivery
    order: 7
    weight: 5
    client_visible: true
    status: completed
    started_at: 2026-06-26
    completed_at: 2026-07-07
    artifacts:
      - path: docs/demo/M3-update-and-demo-2026-06-26.md
        required: true
        detected: true
      - path: build/M3_Signoff_BudgetImport.pptx
        required: false
        detected: true
    exit_criteria:
      - Demo delivered
      - Handoff docs complete
      - Stakeholder sign-off obtained
    blockers: []

  - id: retrospective-findings
    name: Retrospective & Findings
    order: 8
    weight: 5
    client_visible: false
    status: completed
    started_at: 2026-06-27
    completed_at: 2026-07-17
    artifacts:
      - path: docs/findings/2026-06-27-fiscal-year-dropdown-findings.md
        required: false
        detected: true
      - path: docs/new-fullstack-developer-playbook.md
        required: false
        detected: true
    exit_criteria:
      - Lessons learned documented
      - Known issues captured
      - Playbooks updated
      - Team debrief complete
    blockers: []

summary:
  overall_completion: 100
  current_milestone_id: retrospective-findings
  next_milestone_id: null
  active_blockers: []
  client_ready: true
```

---

## 5. Guided Experience Flow

### 5.1 Auto-detection on First Launch

When `maestro --interactive` is launched inside a project folder:

1. Look for `.open-maestro/milestones.yaml`.
2. If found, load it.
3. If not found, run a **milestone discovery scan**:
   - Walk the project tree for artifact patterns.
   - Infer status from artifact presence and git activity.
   - Ask the user to confirm or adjust the inferred state.
   - Write the initial `milestones.yaml`.

### 5.2 Continuous Guidance

In interactive mode, Maestro uses milestones to contextualize prompts:

- **`/milestones`** — show current milestone, overall %, and next uncompleted milestone.
- **`/next`** — suggest the next concrete action based on the current milestone's exit criteria.
- **`/complete <milestone-id>`** — mark a milestone complete (with artifact verification).
- **`/blocker <milestone-id> <reason>`** — mark a milestone blocked and record why.
- **`/track <track-id> <status>`** — update a track within a milestone.

Milestone advancement is **model-suggested, human-confirmed**. Maestro may propose that a milestone is complete based on detected artifacts, but the developer must explicitly confirm with `/complete`.

Example interaction:

```text
> /milestones
Project: M3BudgetUpload
Overall: 100% complete
Current: Retrospective & Findings (completed)
No active blockers.

> /next
No remaining milestones. Consider exporting a final client dashboard or starting a new project phase.
```

### 5.3 Prompt Contextualization

Maestro injects the current milestone into the agent prompt so agents know what kind of output is expected:

```text
You are working on "M3BudgetUpload".
Current milestone: QA & Integration (milestone 6/8).
Exit criteria: E2E tests pass, security/scope verified, UAT playbook executed, sign-off report produced.
Produce artifacts appropriate for this milestone (test reports, UAT playbooks, sign-off docs).
```

---

## 6. Client-Facing Dashboard Schema

The dashboard is a read-only, client-safe projection of milestone progress. It should hide internal details (file paths, specific risks) and surface:

- High-level milestones
- Percent complete
- Current focus
- Blockers (sanitized)
- Deliverables completed
- Upcoming review/demo dates

### 6.1 Dashboard JSON Export

```json
{
  "project_id": "m3-budget-upload",
  "project_name": "Create Financial Plan from Excel Import",
  "client_visible_milestones": [
    {
      "id": "intake-discovery",
      "name": "Intake & Discovery",
      "status": "completed",
      "completion": 100,
      "summary": "PRD reviewed, scope defined, risks and open questions captured."
    },
    {
      "id": "design-blueprint",
      "name": "Design Blueprint",
      "status": "completed",
      "completion": 100,
      "summary": "Data/API contracts frozen; template spec and Jira stories signed off."
    },
    {
      "id": "implementation",
      "name": "Implementation",
      "status": "completed",
      "completion": 100,
      "summary": "Backend endpoints, frontend modal, and SignalR wiring implemented."
    },
    {
      "id": "qa-integration",
      "name": "QA & Integration",
      "status": "completed",
      "completion": 100,
      "summary": "E2E tests, UAT playbook, and sign-off report complete."
    }
  ],
  "overall_completion": 100,
  "current_milestone": "Retrospective & Findings",
  "active_blockers": [],
  "recent_deliverables": [
    "Execution Plan (2026-06-18)",
    "Design Blueprint & Data Contract (2026-06-24)",
    "Build Plan — Budget Import (2026-06-25)",
    "QA Sign-off Report (2026-07-09)",
    "M3 Sign-off Deck (2026-07-07)"
  ],
  "next_review": null
}
```

### 6.2 Export Commands

- **`maestro --export-dashboard --format json`** — export dashboard JSON to stdout or file.
- **`maestro --export-dashboard --format markdown`** — export a client-ready Markdown summary.
- **`maestro --publish-dashboard <url>`** — POST dashboard JSON to a configured endpoint (future).

---

## 7. Storage & Memory Integration

### 7.1 Primary Store

`.open-maestro/milestones.yaml` inside the project folder is the source of truth. It is:

- Human-readable.
- Diff-friendly in git.
- Editable by PMs without touching code.

### 7.2 Memory Integration

Key milestone events are also written to the project-scoped Kuzu memory:

- `milestone_started`
- `milestone_completed`
- `milestone_blocked`
- `track_status_changed`
- `deliverable_added`

This allows Maestro agents to recall project history contextually (e.g., "What blockers did we hit during Implementation?").

### 7.3 Session State

Interactive mode keeps the current milestone in session state so every prompt is contextualized without re-scanning the filesystem.

---

## 8. Implementation Phases for Maestro

To avoid building everything at once, implement the milestone system in slices:

### Slice 1: Schema & Storage
- Define `Milestone`, `Track`, `Artifact`, `Blocker` Pydantic models.
- Implement `MilestoneStore` with YAML read/write.
- Write default milestone templates for software consulting projects.

### Slice 2: Auto-detection
- Implement artifact scanners for each milestone.
- Add `maestro --discover-milestones` command.
- Generate initial `milestones.yaml` on first interactive launch.

### Slice 3: Interactive Commands
- Add `/milestones`, `/next`, `/complete`, `/blocker`, `/track` commands.
- Inject milestone context into agent prompts.

### Slice 4: Client Dashboard Export ✅
- Implemented JSON/Markdown/HTML export in `open_maestro.milestones.dashboard`.
- Added `--export-dashboard {json,markdown,html}` CLI flag.
- Added `--serve-dashboard` with `--dashboard-host` and `--dashboard-port`.
- HTML dashboard styled with Merven.ai design tokens:
  - Dark theme (`#02040b` background, `#ebeff5` text).
  - Teal primary accent (`#00bfaf`).
  - Inter font family.
  - Gradient cards, progress bars, responsive grid.
- Endpoints served by the lightweight server:
  - `/` — HTML dashboard.
  - `/api/dashboard` — JSON dashboard.
  - `/dashboard.md` — Markdown dashboard.

### Slice 5: Dashboard Publishing ✅
- Implemented `DashboardPublisher` in `open_maestro.milestones.publisher`.
- Added `--publish-dashboard <url>` CLI flag.
- Auth via `--dashboard-api-key` / `--dashboard-project-token` or environment variables:
  - `MAESTRO_DASHBOARD_URL`
  - `MAESTRO_DASHBOARD_API_KEY`
  - `MAESTRO_DASHBOARD_PROJECT_TOKEN`
- Sends a JSON payload `{"dashboard": {...}, "metadata": {...}}` via HTTP POST.
- Designed for posting to `merven.ai` behind project-wide login; the receiver endpoint is not yet implemented on the merven.ai side.

---

## 9. Decisions (Answered)

1. **Milestone scope:** Milestones are **per project**, but projects may have **parallel tracks** (e.g., M3BudgetUpload's `import-flow` and `audit-log` tracks). Tracks have independent status and roll up into the parent milestone.
2. **Overlapping milestones:** Yes — especially **Implementation**, **QA & Integration**, and **Demo & Delivery** can overlap within a track. The schema supports `started_at`/`completed_at` per track and per milestone independently.
3. **Client dashboard hosting:** A **lightweight HTTP server** embedded in Maestro serves the dashboard; in production it will be hosted behind a project-wide login on `merven.ai`.
4. **Detection approach:** **Mostly model-driven**, with developer overrides. The model scans artifacts and suggests milestone status; the developer can confirm, reject, or manually mark milestones.
5. **Advancement policy:** **Model suggests, human confirms.** Maestro will propose completions based on artifact detection, but explicit `/complete` (or equivalent) is required to change status.

---

## 10. Appendix: Mapping to M3BudgetUpload Commit Timeline

| Date | Milestone | Notable Activity |
|------|-----------|------------------|
| 2026-06-09 | Intake & Discovery | Synthesis document created. |
| 2026-06-18 | Execution Planning | Execution plan committed; repo verification complete. |
| 2026-06-23–24 | Design Blueprint | Blueprint, template spec, Jira CSV committed. |
| 2026-06-25 | Build Planning | Build plan + fixtures committed. |
| 2026-06-25–07-03 | Implementation | Dense commit activity; backend/frontend stubs filled. |
| 2026-07-03–09 | QA & Integration | Test reports and UAT playbooks committed. |
| 2026-07-07 / 07-17 | Demo & Delivery / Retrospective | Sign-off deck and final findings/playbooks. |
