---
id: decision-register
name: Decision Register & Blocker Reconciliation
tags:
- maestro
- milestones
- decision-register
- blockers
- client-feedback
- dashboard
---

# Decision Register & Blocker Reconciliation

Keep a maestro project's milestone state truthful after decision documents change, and produce a shareable decision register (open questions + decision history) for client feedback.

Use when the user says any of: "sync blockers", "cross-check decisions", "align what's closed", "decision register", "client feedback spreadsheet", "the dashboard is stale", or after any blueprint/decision-document version bump.

## The maestro project-state model (know this first)

- `.open-maestro/milestones.yaml` is the single source of truth for the dashboard. The blueprint/decision markdown files are **never read by the dashboard** — they reach it only through manual transcription into `milestones.yaml`.
- **The two-list invariant:** blockers exist in TWO places in `milestones.yaml`:
  1. per-milestone `blockers:` lists under each epic/milestone
  2. a flattened `summary.active_blockers:` list (under `summary:`, alongside `overall_completion`, `current_milestone_ids`, `client_ready`)
  The dashboard's "Active Blockers" section reads **only the summary list**. Editing one list and not the other is the single most common failure mode — the two drift silently. Always edit both, then assert the sets are identical.
- **The invariant compares ACTIVE blockers only.** A per-milestone entry with `resolved_at:` set is resolved history: it stays in the per-milestone list (never deleted) and must NOT appear in `summary.active_blockers`. Maestro recomputes the summary from per-milestone entries where `resolved_at` is null (`models.py`), so a resolved entry mirrored into the summary is just as much a drift as a missing active one.
- The dashboard is a static export: `maestro --export-dashboard html > dashboard.html` (stdout redirect). Nothing regenerates it automatically.

## Before writing anything

1. **Check for a live session in the project** — a running `maestro --interactive` holds state in memory and can clobber your edits on its next save:
   ```bash
   for pid in $(pgrep -f "maestro --interactive"); do lsof -p $pid 2>/dev/null | awk -v p=$pid '$4=="cwd"{print p" cwd: "$NF}'; done
   ```
   If one is running in the target project, stop and ask whether to wait or proceed. A background watcher (`while kill -0 <pid> 2>/dev/null; do sleep 30; done`) lets you apply the sync the moment it exits.
2. **Back up first** (these projects are often not git repos): `cp milestones.yaml milestones.yaml.bak-<yyyymmdd>-<label>`.

## Cross-check protocol (source priority)

Conflicts resolve upward — the higher source wins:

1. **Blueprint header** — "Status" + blocking-items lists near the top are the current contract (e.g. "vX.Y blocking items that remain OPEN").
2. **Change reports** — `docs/*change-report*.md` disposition tables ("Answer (verbatim)" / "Disposition") are the record of what a stakeholder answer closed or left open.
3. **Design decisions doc** — the "Freeze Gate Summary" table (latest version section) with per-ID Status/Conditions columns.
4. **`milestones.yaml`** — lowest priority; this is what you fix.

Method: build the target blocker set from sources 1–3, diff against both lists in source 4, then apply.

## Edit discipline

- **Never remove a blocker on prose alone.** Every removal needs a dated decision source (change-report row, freeze-gate row, or quoted stakeholder answer). Record removals/additions with dates in the milestone `notes` changelog — nothing is silently lost.
- **Superseded decisions stay visible.** An option that was decided then replaced (e.g. an architecture reversal) is closed with status `Superseded` and a pointer to what replaced it — don't delete it; someone will re-propose it otherwise.
- **Notes hygiene.** Per-epic milestone notes must not contradict closed gates (classic survivors: withdrawn components like a "MassTransit consumer", or gates listed as open that are closed). Bump the version-date sentence in every epic note on each version change.
- **Atomic edits with assertions.** Apply with exact-match replace and per-edit occurrence counts (`text.count(old) == expected` before replacing); abort without writing on any mismatch. Never YAML round-trip the file (it rewrites every line); edit text and `yaml.safe_load` only to validate afterward.
- After writing, validate: YAML parses; per-milestone **active** blockers (no `resolved_at`) flattened == `summary.active_blockers` (count and content); no duplicate blocker IDs; `last_updated` bumped.

## Dashboard regeneration + verification (final step, never skip)

```bash
cd <project> && maestro --export-dashboard html > dashboard.html.new
# verify BEFORE swapping into place:
grep -c "class='blocker'" dashboard.html.new      # matches your expected count
grep -q "<new gate id>" dashboard.html.new         # additions present
grep -q "<removed id>" dashboard.html.new && echo STALE || echo gone
mv dashboard.html.new dashboard.html
```

## Decision register CSV (client feedback artifact)

One CSV, open rows on top (actionable), closed rows below as decision history (newest first). Share as-is — CSV opens in Excel/Google Sheets/Numbers and stays git-diffable.

Columns:

```
ID, Area, Question / decision point, Decision / answer, Status, Date decided,
Decided by, Context / notes, Options, Merven recommendation, Freeze-exit #,
Blueprint ref, Client answer, Client comments
```

Rules:

- **One row per decision**, stable ID matching the blueprint/milestone register ID. Never renumber; add new rows at the end.
- **Open rows:** `Question` is plain client-facing language; `Context` states what is *already decided* so they only answer what's asked; `Options` + `Merven recommendation` where a real choice exists; last two columns blank for the client.
- **Closed rows:** `Decision / answer` records the actual outcome (quote stakeholder answers verbatim when available); `Date decided` + `Decided by` are mandatory.
- **Cell discipline — registers are read in a spreadsheet, not an editor.** One slot, one job; the long-form analysis lives in a linked artifact and the cell points to it.

  | Column | Carries | Reject |
  |---|---|---|
  | `Decision / answer` | The verdict: 1–2 sentences, ≤50 words. Compact evidence in one trailing parenthetical, ≤3 citations (`repo/file:line`). | Option comparisons (that was `Options`), open questions, residual items, re-derivation of the analysis |
  | `Context / notes` | Provenance and state: who/what/when in one line — status transitions with dates, `[INTERNAL decision — not M3-ratified]`-style markers, pointer to the full analysis | Re-explaining the decision, pasted investigation narrative, "RESIDUAL" lists |
  | `Question / decision point` | The ask, client-facing, one sentence | Embedded answers or analysis |

  What to do with content that doesn't fit:
  1. **Analysis** (the how-I-know) → **`docs/design-decisions.md`, the default home for register-row detail.** One section per register ID (works for OQ-n/RR-n/DT rows too, not just D-n ADRs), keyed by the row's ID and dated, holding the evidence, file:line citations, and reasoning. The CSV cell then carries only a pointer: `full analysis: docs/design-decisions.md#OQ-10`. Alternatives when the content already lives elsewhere: blueprint section, change report, or `docs/intake/*.md` — same pointer convention.
  2. **Open follow-ups** ("RESIDUAL FOR M3: (1)… (2)…") → their **own register rows** with stable OQ-n/RR-n IDs — never embedded in a decided row's cell. Embedded residuals are invisible to the dashboard blocker logic and can't be individually closed. Their analysis goes to `design-decisions.md` under the new row's ID, same as any other row.
  3. **Multiple independent rulings** (e.g. three endpoint decisions) → split into one row each, or the row becomes unanswerable at the next gate review.

  Example — an over-stuffed cell like
  `CoreService host is m3coreservice (Chart.yaml:2). The Ocelot route … EP-S1 = GET … EP-S2 = PUT … audits via … RESIDUAL FOR M3: (1) the controller's [Module]: Option A … or Option B … (2) The audit Id key …`
  becomes:
  - `Decision / answer`: `New InvoiceCodingSettingsController served at /api/core/InvoiceCodingSettings via existing gateway host + controller-route convention; no gateway change (CoreService/Chart/Chart.yaml:2; M3.Core.ApiGateway/ocelot.json:44-52). Endpoint contracts EP-S1/EP-S2/EP-S3: full analysis docs/design-decisions.md#D-14.`
  - Three new rows: `OQ-n` (module-code choice, Option A vs B), `RR-n` (audit key/codes must match EP-S2 emit), `OQ-m` (gateway 403 behavior for IIC users).
- **Status is a strict enum — exact match, one word, no prose, ever.** The Status cell carries only the enum word. Resist `Decided (user 2026-10-20)` or `Resolved — follow-ups closed`: parenthetical free-text variants are how a register ends up with 40 distinct "statuses" across 70 rows.

  | Status | Use for |
  |---|---|
  | `Open` | awaiting an answer or ruling (including action items not yet run) |
  | `Decided` | a choice among options was made (ADR-style rulings) |
  | `Resolved` | a question/investigation closed without a new choice — code-verified, doc-backed, or answered |
  | `Ratified` | formally signed off at a gate (design sign-off, contract ratification) |
  | `Accepted` | a risk, limitation, or spec divergence formally accepted (residuals noted in Context) |
  | `Closed` | an action item completed — rows that track tasks, not decisions |
  | `Superseded` | replaced by another row — keep the pointer in Decision/answer or Context |
  | `Withdrawn` / `Absorbed` | retired without replacement / folded into another row |
  | `Info` | editorial or traceability note, not a decision |

  Where the rest goes: dates → `Date decided`; decider and the internal-vs-client ratification marker (e.g. `[INTERNAL decision — not M3-ratified]`) → `Decided by`; follow-up state, gate/freeze impact, blocking status → `Context / notes`. `Superseded`/`Withdrawn`/`Absorbed` rows keep their history with a pointer to the replacement.

  Validate after every edit — zero rows may fall outside the enum, and no
  prose cell may blow its word budget (soft flags; trim or move to an
  artifact if flagged):

  ```bash
  python3 - <<'EOF'
  import csv, sys
  ok = {"Open","Decided","Resolved","Ratified","Accepted","Closed",
        "Superseded","Withdrawn","Absorbed","Info"}
  rows = [r for r in csv.reader(open("docs/decision-register-vX.Y.csv"))
          if r and not r[0].startswith("#")][1:]
  bad = [(r[0], r[4]) for r in rows if r[4] not in ok]
  sys.exit(f"BAD STATUS rows: {bad}" if bad else 0)  # silent exit 0 = clean
  EOF
  python3 - <<'EOF'
  import csv
  # Cell budgets: Decision/answer (col 3) <= 50 words, Context/notes (col 7) <= 25.
  rows = [r for r in csv.reader(open("docs/decision-register-vX.Y.csv"))
          if r and not r[0].startswith("#")][1:]
  flags = [(r[0], "decision", len(r[3].split())) for r in rows if len(r) > 7 and len(r[3].split()) > 50] \
        + [(r[0], "context", len(r[7].split())) for r in rows if len(r) > 7 and len(r[7].split()) > 25]
  for f in flags:
      print(f"LENGTH {f[0]} {f[1]}={f[2]} words (budget exceeded — trim or link an artifact)")
  EOF
  ```
- **Priority:** mark the critical-path items (longest-lead delivery items and new gates opened by the latest answers) P0; the rest P1. If the project has a numbered freeze-exit list, map each open row to it so answers translate directly into milestone closures.
- Name it `docs/decision-register-vX.Y.csv` next to the decision docs.

## ADR format (for plan-002 ratified decisions)

plan-002's ratified design decisions are ADR-style entries, adapted to this project's registers — `D-n` IDs (not 4-digit files), home in `docs/design-decisions.md`, and one row per decision in the decision-register CSV above. The same file also holds non-ADR register-row analysis in per-ID sections (see the cell-discipline rule above): ADR entries answer "what did we decide and why", per-ID sections hold "how do we know" evidence for any register row.

**Write an entry only when all three hold** (the adoption-killer is over-forcing ADRs onto routine work):

1. **Architecturally significant** — shapes how major parts of the system are structured or affects multiple components/teams.
2. **Costly to reverse** — undoing it later means real rework, migration, or coordination.
3. **Not obvious from the code** — a new contributor in six months couldn't infer the *why* from the implementation.

Routine bug fixes, style choices, and version bumps do not get entries.

**Entry template** (one per decision, numbered D1, D2, … sequentially, never renumbered):

```markdown
### D-n — <Title, short and imperative: "Use X for Y">
- **Date**: YYYY-MM-DD
- **Status**: Proposed | Ratified | Superseded by D-m
- **Context**: the forces and constraints that motivated this — technical, product,
  organizational. Factual, no recommendation yet.
- **Decision**: what was decided, active voice: "We will use X."
- **Consequences**: what becomes easier AND harder — honest trade-offs, known risks.
- **Rejected alternatives**: what else was considered and why it lost.
```

**Lifecycle:** decisions start `Proposed` and move to `Ratified` at design-005 sign-off. A reversed decision is never deleted — its Status becomes `Superseded by D-m` with the pointer, and the CSV row keeps its history with the same status vocabulary (`Superseded`, `Withdrawn`, `Absorbed`). The execution-plan skill's Phase-B corrections arrive as proposed `D-n` entries; plan-002 ratifies, this register records.

*Format after Michael Nygard, "Documenting Architecture Decisions" (2011).*

## Relationship to the diagram standard

Decisions belong in this register (tables / CSV), **not** in the blueprint
diagram. Per the `mermaid-diagrams` skill's User-Journey Blueprint Diagram
Standard (`skills/universal/diagrams/mermaid/SKILL.md`): parked / resolved /
closed decisions never appear as graph nodes or edges, and an open decision is
an inline `:::open` tag on the component it gates (not a standalone node). The
diagram references decision IDs (e.g. `R-07`); the decision *prose* — question,
options, disposition, history — lives here. Keep the two in sync by ID.

## Guardrail

Recommend the concrete blocker changes (remove/update/add list) and show them **before writing** when the closures are based on external stakeholder input the user just pasted — one confirmation prevents a misread answer from deleting a real gate. Pure formatting/verification fixes (summary-list resync, stale note sentences, dashboard regen) need no confirmation.

## Checklist

- [ ] No live `maestro --interactive` session in the project (or explicitly cleared to proceed)
- [ ] Backup taken
- [ ] Target blocker set derived from blueprint header + change reports + freeze-gate summary
- [ ] Both blocker lists edited; ACTIVE sets asserted identical; YAML validates; `last_updated` bumped
- [ ] Epic notes free of contradictions with closed gates
- [ ] Notes changelog records every removal/addition with date
- [ ] Dashboard regenerated, verified pre-swap, swapped
- [ ] Decision register CSV written: open first, closed history below, every closed row has date + decider
- [ ] Every CSV row's Status is exactly one enum word (validation snippet exits 0)
- [ ] No prose cell over budget — Decision/answer ≤50 words, Context/notes ≤25 (length audit prints nothing); over-budget content moved to an artifact with a pointer, embedded residuals split into their own OQ-n/RR-n rows
