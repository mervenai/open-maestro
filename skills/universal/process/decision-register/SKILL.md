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
- After writing, validate: YAML parses; per-milestone flattened blockers == `summary.active_blockers` (count and content); no duplicate blocker IDs; `last_updated` bumped.

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
- **Status vocabulary:** `Open`, `Decided`, `Resolved`, `Ratified`, `Accepted`, `Superseded`, `Withdrawn`, `Absorbed`. `Superseded`/`Withdrawn`/`Absorbed` rows keep their history with a pointer to the replacement.
- **Priority:** mark the critical-path items (longest-lead delivery items and new gates opened by the latest answers) P0; the rest P1. If the project has a numbered freeze-exit list, map each open row to it so answers translate directly into milestone closures.
- Name it `docs/decision-register-vX.Y.csv` next to the decision docs.

## Guardrail

Recommend the concrete blocker changes (remove/update/add list) and show them **before writing** when the closures are based on external stakeholder input the user just pasted — one confirmation prevents a misread answer from deleting a real gate. Pure formatting/verification fixes (summary-list resync, stale note sentences, dashboard regen) need no confirmation.

## Checklist

- [ ] No live `maestro --interactive` session in the project (or explicitly cleared to proceed)
- [ ] Backup taken
- [ ] Target blocker set derived from blueprint header + change reports + freeze-gate summary
- [ ] Both blocker lists edited; sets asserted identical; YAML validates; `last_updated` bumped
- [ ] Epic notes free of contradictions with closed gates
- [ ] Notes changelog records every removal/addition with date
- [ ] Dashboard regenerated, verified pre-swap, swapped
- [ ] Decision register CSV written: open first, closed history below, every closed row has date + decider
