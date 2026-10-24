---
id: milestones-authoring
name: Milestone Plan Authoring (milestones.yaml)
tags:
- maestro
- milestones
- dashboard
- project-state
- authoring
---

# Milestone Plan Authoring (milestones.yaml)

Author or edit a maestro project's `.open-maestro/milestones.yaml` safely, from any tool — a maestro session, Claude Code, or by hand. This file is the project's single source of truth for the dashboard; there is no maestro command that adds custom milestones, so disciplined direct editing is the sanctioned path, not a workaround.

Use when the user says any of: "add a milestone", "add a gate", "update milestones.yaml", "sync blockers", "mark a blocker resolved", "the dashboard is stale", or before ANY edit to `.open-maestro/milestones.yaml`.

## Know the model first

- `.open-maestro/milestones.yaml` → `MilestonePlan`: `project_id`, `project_name`, `last_updated`, `epics:[]`, `summary:`.
- **Epic** → `milestones:[]`. The standard 8 lifecycle phases (`intake-discovery` … `retrospective-findings`) come from maestro's built-in template (`src/open_maestro/milestones/templates.py`) and are stamped onto every work epic by auto-populate. Extra milestones per epic are allowed — see "Adding a custom milestone" below.
- **Milestone** fields: `id`, `name`, `order` (integer, unique per epic — the dashboard sorts by it), `weight` (0–100-ish share of `overall_completion`), `client_visible`, `status` (`not_started` / `in_progress` / `completed`), `started_at`, `artifacts:[]`, `exit_criteria:[]`, `blockers:[]`, `notes`.
- **Blocker** fields: `id` (timestamp-style, e.g. `2026-10-21T00:00:00.000003`), `description`, `epic_id`, `milestone_id` (must point at its real location), `created_at`, `resolved_at` (null while active; ISO date when resolved — set it, never delete the entry).
- **`summary:`** fields: `overall_completion` (weight-derived percentage), `current_milestone_ids`, `next_milestone_ids`, `active_blockers` (flattened list), `client_ready`. Maestro recomputes these from the epic/milestone tree on its own writes; when you edit by hand you maintain them yourself.

## Before writing anything

1. **Check for a live session in the project** — a running `maestro --interactive` holds state in memory and can clobber your edits on its next save:
   ```bash
   for pid in $(pgrep -f "maestro --interactive"); do lsof -p $pid 2>/dev/null | awk -v p=$pid '$4=="cwd"{print p" cwd: "$NF}'; done
   ```
   If one is running in the target project, stop and ask whether to wait or proceed.
2. **Back up first** (these directories are often not git-tracked): `cp milestones.yaml milestones.yaml.bak-<yyyymmdd>-<label>`.

## The two-list invariant (ACTIVE blockers only)

Blockers exist in TWO places: each milestone's own `blockers:` list and the flattened `summary.active_blockers:`. The dashboard's "Active Blockers" section reads **only the summary list**. After every edit, the flattened **active** entries (`resolved_at` null) must equal `summary.active_blockers` exactly (ids and count).

- **Resolving a blocker:** set `resolved_at` to the resolution date and append the resolution (dated, with its decision source) to the description or milestone notes. The entry STAYS in the per-milestone list as history — it must NOT appear in `summary.active_blockers`. Never delete it.
- **Adding a blocker:** append to the owning milestone's `blockers:` AND mirror it into `summary.active_blockers` — one without the other is drift either way.
- **Removing from summary** requires the same dated decision source as setting `resolved_at`.

## Adding a custom milestone

**First decide: blocker or milestone?** A sign-off gate, conformance check, or external dependency with no artifacts, no completion weight, and no build work of its own is a **blocker on the milestone it gates** — not a new milestone. It would render as an extra phase after the standard 8 and dilute the phase list (the dashboard has no separate gate section). Reserve custom milestones for trackable work with real deliverables and exit criteria.

Maestro never invents these — you are defining a project-specific gate or track. Follow every rule or the dashboard misleads:

- **Stable kebab-case id**, never renumbered later (other artifacts cite `epic_id/milestone_id`).
- **`order`** = the next free integer in that epic. It will render after the standard phases, numbered accordingly — that is expected; the dashboard has no separate "gate" section.
- **`weight: 0`** unless the milestone should genuinely move `overall_completion`. A tracking gate that shows as 5% of the project bar is a lie.
- **`client_visible: true`** only if it belongs in a client-facing view.
- **`notes` must carry provenance:** creation date, the decision/doc/commit that created it ("Gate created v4.1 2026-10-20 (5b34a81)"), what it gates, and how its target date gets decided. The notes changelog convention: append dated pass lines, never rewrite history.
- **Blockers under it obey the two-list invariant** like any other milestone.

## Edit discipline

- **Atomic text edits with assertions.** Exact-match replace with per-edit occurrence counts (`text.count(old) == expected` before replacing); abort without writing on any mismatch. Never YAML round-trip the file (it rewrites every line); edit text and `yaml.safe_load` only to validate afterward.
- **Never renumber or delete** milestones or orders; retire in `notes` instead.
- **Bump `last_updated`** to a fresh ISO timestamp on every write.
- Keep `summary.current_milestone_ids` / `next_milestone_ids` consistent with the statuses you changed.

## Validate after writing

```bash
python3 - <<'EOF'
import sys, yaml
plan = yaml.safe_load(open(".open-maestro/milestones.yaml"))
flat_active, ids = [], set()
for e in plan["epics"]:
    seen_orders = set()
    for m in e["milestones"]:
        assert m["order"] not in seen_orders, f'duplicate order {m["order"]} in {e["id"]}'
        seen_orders.add(m["order"])
        for b in m.get("blockers", []):
            assert b["id"] not in ids, f'duplicate blocker id {b["id"]}'
            ids.add(b["id"])
            assert b["epic_id"] == e["id"] and b["milestone_id"] == m["id"], f'{b["id"]} mislocated'
            if b.get("resolved_at") is None:
                flat_active.append(b["id"])
summary = [b["id"] for b in plan["summary"]["active_blockers"]]
assert sorted(flat_active) == sorted(summary), f'drift: flat={sorted(flat_active)} summary={sorted(summary)}'
sys.exit(0)
EOF
```

## Dashboard regeneration (final step for visible changes)

```bash
cd <project> && maestro --export-dashboard html > dashboard.html.new
grep -c "class='blocker'" dashboard.html.new   # == your active-blocker count
mv dashboard.html.new dashboard.html
```

The published dashboard (merven.ai link) changes only when republished (`--publish-dashboard`) — a deliberate, client-visible step; confirm with the user before publishing.

## Relationship to other skills

- **`decision-register`** — the blocker reconciliation protocol and the decision-register CSV (client feedback artifact). Use it to derive *which* blockers change; use this skill for *how* to write them.
- **`traceability-matrix`** — story/impl/test status vocabulary; it reads `milestones.yaml` at phase boundaries but never writes it.

## Checklist

- [ ] No live `maestro --interactive` session in the project (or explicitly cleared to proceed)
- [ ] Backup taken
- [ ] Gate-with-no-work items (sign-off / conformance / dependency) recorded as blockers on the milestone they gate, not as custom milestones
- [ ] Custom milestones: stable id, next free `order`, `weight: 0`, provenance in `notes`
- [ ] Resolved blockers carry `resolved_at` + dated resolution; none in `summary.active_blockers`
- [ ] Active flattened blockers == `summary.active_blockers`; blocker locations correct; orders unique
- [ ] `last_updated` bumped; `current`/`next` milestone ids consistent
- [ ] YAML validates; validation snippet exits 0
- [ ] Dashboard regenerated and verified pre-swap; publish only after user confirmation
