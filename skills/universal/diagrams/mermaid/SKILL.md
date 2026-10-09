---
id: mermaid-diagrams
name: Mermaid Diagrams from Blueprints
tags:
- diagrams
- mermaid
- blueprint
- architecture
- documentation
- data-contract
---

# Mermaid Diagrams from Blueprints

Turn design documents (blueprints, design reviews, data contracts, integration
event specs) into Mermaid diagrams, saved either as Markdown (`.md` with a
fenced ```mermaid block — renders on GitHub, GitLab, Linear, Notion) or
rendered to `.png` for decks, dashboards, and wiki attachments.

## When to Use

- The user asks for a diagram of a system, service, data flow, or lifecycle
  described in a design doc
- A blueprint doc is hard to scan and needs a visual summary
- An existing diagram drifted from the doc it came from — regenerate from the
  doc, do not patch stale diagrams by hand

## Workflow

1. Read the source doc fully first. Pick the narrowest diagram type that
   carries the doc's main claim (table below). One doc yields one primary
   diagram — do not try to diagram everything at once.
2. Author the Mermaid source following the syntax rules below. Derive node IDs
   from the doc's own identifiers (FR-07, EP-04, entity names) so every element
   traces back to a doc statement — never invent components the doc doesn't
   describe.
3. Validate before saving: a diagram that doesn't parse is worse than none.
   Render once with one of the recipes below.
4. Save in the requested format:
   - `.md`: a Markdown file whose body is a fenced ```mermaid block, saved
     next to the source doc (convention: `docs/diagrams/<source-stem>.md`).
     This is the single source of truth — mermaid-cli renders PNG/SVG
     directly from it (no separate `.mmd` needed).
   - `.png` / `.svg`: rendered from the `.md` with mermaid-cli (preferred)
     or the Kroki fallback. Produce both when the diagram is a living
     project artifact — PNG for decks/dashboards, SVG for docs/wiki where
     it must scale crisply.
5. When the diagram belongs inside the blueprint itself, insert the fenced
   block at the relevant section and keep the standalone file as the source of
   truth.

## Diagram Type Selection

| Blueprint content | Mermaid type |
|---|---|
| Services, calls, request/response flow | `flowchart LR` (use `TB` for layered stacks) |
| Event publication / consumption, timeouts | `sequenceDiagram` |
| Entities, tables, relationships (data contracts) | `erDiagram` |
| DTO shapes, inheritance, interfaces | `classDiagram` |
| Status / lifecycle transitions | `stateDiagram-v2` |
| System context with external actors | `flowchart` with subgraphs (C4 only on request) |

Flowchart is the default. Do not nest subgraphs deeper than two levels; split
instead.

## User-Journey Blueprint Diagram Standard

When the diagram is the blueprint's **user-journey / repo-impact / open-questions**
map (the design-002 artifact), follow this standard exactly so every project
and every run produces the same conventions. Deviating from it costs reviewers
re-learning; extend it only by editing this standard.

### Canonical color classes (fixed semantics, fixed hex values)

```mermaid
classDef open     fill:#fff3cd,stroke:#e0a800,stroke-width:3px,color:#000   %% amber  — open question / blocking decision
classDef parked   fill:#f8d7da,stroke:#b02a37,stroke-width:2px,color:#000,stroke-dasharray:5 4  %% red dashed — parked / out of scope (with disposition citation)
classDef netnew   fill:#cfe2ff,stroke:#084298,stroke-width:1px,color:#000   %% blue   — net-new, introduced by this contract (also resolved items still to be built)
classDef existing fill:#d1e7dd,stroke:#0a3622,stroke-width:1px,color:#000   %% green  — verified existing pattern / anchor, cited to the codebase
classDef user     fill:#e2e3e5,stroke:#495057,stroke-width:1px,color:#000   %% grey   — human action in the user journey
classDef external fill:#fde2e4,stroke:#6a040f,stroke-width:1px,color:#000   %% pink   — outside the M3 repos (3rd-party / DnA)
classDef lane     fill:#f8f9fb,stroke:#9e9e9e,color:#37474f                 %% swim-lane background
```

The diagram MUST render an in-diagram legend subgraph with one sample node
per class (readers must not have to scroll to prose). Apply `lane` to every
swim-lane subgraph.

### Diagram skeleton (copy-paste start)

```mermaid
%%{init: {'flowchart': {'htmlLabels': true, 'curve': 'basis'}, 'themeVariables': {'fontSize': '13px'}}}%%
flowchart TB
  %% classDef block as above
  subgraph BROWSER["Browser · <repo/area>"]
    direction TB
    U1["1 · <title><br/><evidence · FR-nn / §x.y><br/><i>repo</i>"]:::user
  end
  subgraph GATEWAY["Gateway · <repo>"] ... end
  subgraph BACKEND["Backend · <repos>"] ... end
  subgraph EXTSVC["External services"] ... end
  subgraph ADMIN["Admin / settings"] ... end
  subgraph PARKED["Parked / out of scope"]
    direction LR
    P1["P1 · <item> (<DISPOSITION> <citation>)"]:::parked
  end
  subgraph REPOS["Repos impacted — summary legend"] ... end
  subgraph LEGEND["Legend — node colors"]
    direction LR
    L_open["OPEN — blocking decision owed"]:::open
    L_parked["PARKED — out of scope"]:::parked
    L_netnew["Net-new — this contract"]:::netnew
    L_existing["Existing — verified pattern"]:::existing
    L_user["User action"]:::user
    L_external["External — not an M3 repo"]:::external
  end
  %% edges below, grouped by flow with comment banners
```

Node label format: `number · Title<br/>evidence · FR-nn / §x.y<br/><i>repo</i>`
— number, then what it is, then the traceable evidence, then the repo in
italics. Numbering is stable across revisions: never renumber, add at the end
(or use `P#` for parked items).

### Edge rules (the number-one readability lever)

- **Label every edge** — `-->|action or payload|`. A reader should follow the
  flow without opening node boxes. Name the action/data (`row click`,
  `GET /api/Activity`, `OAuth2 bearer`), never repeat the node's own title.
- **Tag blocking decisions** with their register ID on the edge or the target
  node (`-.A-5 subscription.->`, `OPEN (R-07)`), so the diagram cross-references
  the blueprint's decision list and Jira.
- **Solid arrows** carry data/control flow. **Dotted arrows** carry
  references, dependencies, loops, or guard-rails (including explicit "NO HTTP"
  edges). State the semantics once in the "How to read" section.

### Companion document structure (the `.md` file)

1. Header: source docs with versions/dates, scope sentence, stable-numbering note.
2. **How to read** — bullet the color semantics, the solid/dotted rule, repo-tag convention.
3. `## Diagram` — the fenced mermaid block; the rendered `.png` and `.svg`
   are regenerated from this file, never hand-edited.
4. **Node reference** — table per swim-lane: `# | Node | Repo | Status | Anchor`.
5. **Open-question hot-list** — amber nodes with owner → resolution path;
   resolved nodes kept visible with strikethrough + resolution version.
6. **Bottom line / Next steps**.

### Consistency checklist (run before saving)

- [ ] All 7 classDefs present with the exact hex values above; legend subgraph rendered in-diagram.
- [ ] Every edge labeled; every blocking decision carries its register ID; solid/dotted convention stated.
- [ ] Every node numbered and repo-tagged; user actions grey; externals pink.
- [ ] Swim-lanes: Browser, Gateway, Backend, External, Admin (+ Parked when any); `lane` style applied.
- [ ] Repos-impacted summary legend present; parked items carry disposition + citation.
- [ ] Node reference table + open-question hot-list present; numbering stable vs. the previous revision.
- [ ] All three artifacts saved from one source: `.md` (fenced block) + `.png` + `.svg`, renders produced by mermaid-cli (or Kroki) from the `.md`, not hand-edited.

## Syntax Rules (frequent parse failures)

- Declare the direction once: `flowchart LR`.
- Node IDs are bare (`EP04`); labels go in brackets. Quote labels containing
  parentheses, slashes, or colons: `EP04["POST /invoices (async)"]`.
- Edge labels: `A -->|label| B`. Avoid `|` inside the label text itself.
  Parentheses inside an edge label break the flowchart parser
  (`-->|authorize X (R-10)|` fails) — write `-->|authorize X R-10|`
  instead; node labels in brackets may use parentheses freely.
- Subgraphs need both a name and quoted title:
  `subgraph ENG["Engine Services"] ... end`
- `erDiagram`: relationships read left-to-right
  `INVOICE ||--o{ INVOICE_LINE : "contains"`; attributes are typed columns
  (`string invoice_number`).
- `classDiagram`: generics use `~`, e.g. `PagedResult~InvoiceDto~`.
- `sequenceDiagram`: declare actors with `participant A as Label`; notes with
  `Note over A: text`; activate/deactivate only when overlap matters.
- Keep one statement per line; trailing text after a statement breaks parsing.

## Render / Validate Recipes

### mermaid-cli via npx (preferred)

```bash
# validate + render PNG straight from the Markdown source
# (requires node/npx: check `which npx` first)
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/diagram.md -o docs/diagrams/diagram.png
# SVG alongside (second invocation; Markdown input appends -1 to the output
# name, e.g. diagram-1.svg — rename it back if you want diagram.svg)
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/diagram.md -o docs/diagrams/diagram.svg
# larger render for slides/decks
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/diagram.md -o docs/diagrams/diagram.png --width 2400
# validate only (nonzero exit = syntax error)
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/diagram.md -o /tmp/diagram.svg
```

First run downloads the package; later runs are cached.

### Kroki fallback (no npm required)

Save this as `scripts/render_mermaid.py` in the project (or `/tmp`) and run
`python3 scripts/render_mermaid.py diagram.md diagram.png` (it extracts the
fenced ```mermaid block from the Markdown source):

```python
#!/usr/bin/env python3
"""Render the Mermaid fenced block in a Markdown file to PNG/SVG via Kroki (stdlib only)."""
import base64
import re
import sys
import urllib.request
import zlib

fmt = "png"  # or "svg"
src, out = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
block = re.search(r"```mermaid\n(.*?)```", text, re.S)
if not block:
    sys.exit(f"no mermaid block found in {src}")
encoded = base64.urlsafe_b64encode(
    zlib.compress(block.group(1).encode("utf-8"), 9)
).decode("ascii")
urllib.request.urlretrieve(f"https://kroki.io/mermaid/{fmt}/{encoded}", out)
print(f"wrote {out}")
```

Kroki runs an older Mermaid than mermaid-cli — if Kroki rejects valid syntax,
switch to the npx recipe.

## Output Checklist

- Direction matches the doc's narrative order (events flow left-to-right or
  top-to-bottom as the doc describes them)
- Every node and edge traceable to a doc statement; counts in the diagram
  match the doc's counts (services, endpoints, events)
- The `.md` version parses as fenced ```mermaid inside the target doc
- The `.png` was produced from the final `.mmd`, not an earlier draft
- Paths reported back to the user match where the files were actually saved
