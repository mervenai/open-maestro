"""Advisory Mermaid linter for blueprint diagram artifacts (fixes #4).

Why: the user-journey blueprint diagram standard (mermaid-diagrams skill) says
decisions never belong in the graph — parked/resolved decisions live in the
companion tables, and open gates are inline ``:::open`` tags on the component
they gate, not standalone nodes wired in with dotted attachment edges. Nothing
enforced that, so output quality depended on each agent run's discipline.

What: ``lint_diagram_file`` / ``lint_mermaid_source`` parse fenced ```mermaid
blocks in ``docs/diagrams/*.md`` artifacts and emit ADVISORIES (never blocks)
for exactly two anti-patterns:

  (a) in-graph parked nodes — any node carrying the ``:::parked`` class;
  (b) standalone open-decision nodes attached by dotted edges — a dotted edge
      (``-.->`` / ``-. label .->``) whose endpoint is an open-decision/question
      node (``:::open``, or a label that starts with an open/question marker
      like ``OPEN`` / ``O#`` / ``R-07?``).

It deliberately does NOT check node counts, label length, or header size
(out of scope per #4). ``diagram_advisory`` mirrors ``blueprint.gate_advisory``:
advise, don't block, with an env kill-switch (``MAESTRO_DIAGRAM_LINT=off``).

Test: feed ``lint_mermaid_source`` a block with a ``:::parked`` node and assert
one PARKED advisory; a dotted edge to an ``:::open`` node and assert one
OPEN_ATTACHMENT advisory; a clean inline-tagged diagram and assert none; and an
over-budget-but-clean diagram (many nodes, long labels) and assert none.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Diagram artifacts the advisory applies to (parallels BLUEPRINT_PATTERNS).
DIAGRAM_PATTERNS: tuple[str, ...] = ("docs/diagrams/*.md",)

# Advisory codes (string resources — avoid magic strings at call sites).
PARKED_CODE = "parked-node-in-graph"
OPEN_ATTACHMENT_CODE = "open-decision-attachment-edge"

# A fenced ```mermaid block. Non-greedy body; tolerant of CRLF.
_MERMAID_BLOCK_RE = re.compile(r"```mermaid[^\n]*\n(.*?)```", re.S)

# Node id that carries an explicit class, e.g. ``P1[...]:::parked`` or
# ``B1("...")::: open``. Captures the id and the class name.
_CLASSED_NODE_RE = re.compile(
    r"([A-Za-z_][\w-]*)\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*:::\s*([A-Za-z_][\w-]*)"
)

# A ``class A,B parked`` statement (the non-inline way to assign a class).
_CLASS_STMT_RE = re.compile(r"^\s*class\s+([\w,\s-]+?)\s+([A-Za-z_][\w-]*)\s*$", re.M)

# A dotted edge: ``A -.-> B`` or ``A -. label .-> B`` (optional arrowheads).
# Captures the left id, the optional label, and the right id.
_DOTTED_EDGE_RE = re.compile(
    r"([A-Za-z_][\w-]*)\s*(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*"
    r"[xo<]?-\.(?P<label>[^.]*?)\.?-+[>xo]?\s*"
    r"([A-Za-z_][\w-]*)"
)

# A node *definition* with a bracketed label, e.g. ``O7["OPEN: pick X"]``.
# Captures id + the inner label text (first bracket style that matches).
_NODE_LABEL_RE = re.compile(
    r"([A-Za-z_][\w-]*)\s*(?:\[\"?(?P<sq>[^\]\"]*)\"?\]|\(\"?(?P<rd>[^)\"]*)\"?\)|\{\"?(?P<cl>[^}\"]*)\"?\})"
)

# Label text that marks a node as an open-decision / question node.
_OPEN_LABEL_RE = re.compile(r"^\s*(?:OPEN\b|O#|O\d|Q#|Q\d|R-?\d+\s*\?)", re.I)


@dataclass(frozen=True)
class DiagramAdvisory:
    """One advisory finding for a diagram artifact.

    Why: callers (the gate advisory, tests) need a structured, non-blocking
    result — a code to branch on plus a human message to surface.
    """

    code: str
    message: str


def _strip_comments(line: str) -> str:
    """Drop a trailing ``%% ...`` Mermaid comment so it is not mis-parsed.

    Why: skeleton/legend lines use ``%%`` comments that can contain words like
    ``parked``; matching inside them would raise false advisories.
    What: returns the line with any ``%%`` comment removed.
    Test: ``_strip_comments('A:::open %% note')`` returns ``'A:::open '``.
    """
    idx = line.find("%%")
    return line if idx < 0 else line[:idx]


def _class_assignments(body: str) -> dict[str, set[str]]:
    """Map node id -> set of classes assigned via ``class A,B name`` statements.

    Why: a parked/open node may get its class from a standalone ``class``
    statement rather than an inline ``:::`` tag; both must be detected.
    What: parses every ``class <ids> <name>`` line into an id->classes map.
    Test: ``class P1,P2 parked`` yields ``{'P1': {'parked'}, 'P2': {'parked'}}``.
    """
    assigned: dict[str, set[str]] = {}
    for ids, cls in _CLASS_STMT_RE.findall(body):
        for node_id in (i.strip() for i in ids.split(",")):
            if node_id:
                assigned.setdefault(node_id, set()).add(cls)
    return assigned


def _inline_classes(body: str) -> dict[str, set[str]]:
    """Map node id -> set of classes assigned inline via ``:::class``.

    Why: the primary way the skeleton tags nodes (``P1[...]:::parked``).
    What: scans comment-stripped lines for ``id...:::class`` and collects them.
    Test: ``B1[\"x\"]:::open`` yields ``{'B1': {'open'}}``.
    """
    inline: dict[str, set[str]] = {}
    for raw in body.splitlines():
        line = _strip_comments(raw)
        for node_id, cls in _CLASSED_NODE_RE.findall(line):
            inline.setdefault(node_id, set()).add(cls)
    return inline


def _node_labels(body: str) -> dict[str, str]:
    """Map node id -> its declared bracket label text (last wins).

    Why: open-decision nodes are detectable by a label that starts with an
    open/question marker even without a ``:::open`` tag.
    What: collects every ``id[...]`` / ``id(...)`` / ``id{...}`` label.
    Test: ``O7[\"OPEN: pick store\"]`` maps ``'O7' -> 'OPEN: pick store'``.
    """
    labels: dict[str, str] = {}
    for raw in body.splitlines():
        line = _strip_comments(raw)
        for m in _NODE_LABEL_RE.finditer(line):
            text = m.group("sq") or m.group("rd") or m.group("cl") or ""
            labels[m.group(1)] = text
    return labels


def _is_open_decision_node(
    node_id: str,
    classes: dict[str, set[str]],
    labels: dict[str, str],
) -> bool:
    """Whether *node_id* is an open-decision / question node.

    Why: a dotted edge is only an anti-pattern when it attaches such a node.
    What: true if the node has the ``open`` class or an open/question label.
    Test: ``:::open`` node -> True; label ``OPEN (R-7)`` -> True; plain -> False.
    """
    if "open" in classes.get(node_id, set()):
        return True
    label = labels.get(node_id, "")
    return bool(_OPEN_LABEL_RE.match(label))


def lint_mermaid_source(body: str) -> list[DiagramAdvisory]:
    """Advisories for a single Mermaid block *body* (fences already stripped).

    Why: the core, side-effect-free check that both file linting and tests
    drive; it must advise exactly the two forbidden decision-in-graph shapes
    and nothing budget-related.
    What: returns a list of :class:`DiagramAdvisory` — one per distinct
    parked node group and one per dotted-attachment-to-open-node group.
    Test: parked block -> PARKED_CODE; dotted-to-open -> OPEN_ATTACHMENT_CODE;
    clean/over-budget-clean -> ``[]``.
    """
    classes: dict[str, set[str]] = {}
    for node_id, cls in {**_class_assignments(body)}.items():
        classes.setdefault(node_id, set()).update(cls)
    for node_id, cls in _inline_classes(body).items():
        classes.setdefault(node_id, set()).update(cls)
    labels = _node_labels(body)

    advisories: list[DiagramAdvisory] = []

    # (a) in-graph parked nodes.
    parked = sorted(nid for nid, cls in classes.items() if "parked" in cls)
    if parked:
        advisories.append(
            DiagramAdvisory(
                PARKED_CODE,
                "parked node(s) in the graph: "
                + ", ".join(parked)
                + " — move parked/out-of-scope and resolved/closed decisions to "
                "the companion parked table and the decision-register CSV, not "
                "the graph.",
            )
        )

    # (b) standalone open-decision nodes attached by dotted edges.
    attached: list[str] = []
    for left, _label, right in _DOTTED_EDGE_RE.findall(body):
        for endpoint in (left, right):
            if _is_open_decision_node(endpoint, classes, labels):
                attached.append(endpoint)
    attached = sorted(set(attached))
    if attached:
        advisories.append(
            DiagramAdvisory(
                OPEN_ATTACHMENT_CODE,
                "open-decision node(s) attached by dotted edges: "
                + ", ".join(attached)
                + " — tag the open gate inline on the component it gates "
                "(`OPEN (R-nn)` with `:::open`) instead of a standalone node "
                "plus a dotted attachment edge.",
            )
        )

    return advisories


def lint_mermaid_text(text: str) -> list[DiagramAdvisory]:
    """Advisories across every fenced ```mermaid block in *text*.

    Why: a Markdown diagram artifact may hold more than one fenced block; all
    are subject to the same decision-in-graph rules.
    What: extracts each ```mermaid block and unions their advisories.
    Test: a two-block doc (one parked, one clean) yields one PARKED advisory.
    """
    advisories: list[DiagramAdvisory] = []
    seen: set[tuple[str, str]] = set()
    for block in _MERMAID_BLOCK_RE.findall(text):
        for adv in lint_mermaid_source(block):
            key = (adv.code, adv.message)
            if key not in seen:
                seen.add(key)
                advisories.append(adv)
    return advisories


def lint_diagram_file(path: str | Path) -> list[DiagramAdvisory]:
    """Advisories for a diagram Markdown artifact on disk.

    Why: the on-disk entry point the gate advisory iterates over.
    What: reads *path* and runs :func:`lint_mermaid_text`; unreadable/no-block
    files yield no advisories (advise, never fail).
    Test: write a .md with a ``:::parked`` block -> one PARKED advisory.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return lint_mermaid_text(text)


def find_diagram_artifacts(project_path: str | Path) -> list[Path]:
    """Diagram Markdown artifacts under *project_path* the advisory applies to.

    Why: the gate advisory needs the set of files to lint.
    What: globs ``docs/diagrams/*.md`` and returns sorted unique files.
    Test: a project with ``docs/diagrams/x.md`` returns ``[.../x.md]``.
    """
    root = Path(project_path)
    found: list[Path] = []
    for pattern in DIAGRAM_PATTERNS:
        found.extend(p for p in root.glob(pattern) if p.is_file())
    return sorted(set(found))


def diagram_lint_enabled() -> bool:
    """Whether the diagram advisory may run (default on).

    Why: mirror the ``MAESTRO_DEEP_REVIEW`` kill-switch convention so operators
    can silence the advisory without code changes.
    What: false when ``MAESTRO_DIAGRAM_LINT`` is off/0/false/no.
    Test: unset -> True; ``MAESTRO_DIAGRAM_LINT=off`` -> False.
    """
    flag = os.environ.get("MAESTRO_DIAGRAM_LINT", "").strip().lower()
    return flag not in {"off", "0", "false", "no"}


def diagram_advisory(project_path: str | Path) -> str:
    """Non-blocking advisory string for diagram artifacts (empty = clean).

    Why: parallels :func:`open_maestro.review.blueprint.gate_advisory` so the
    milestone advisory can surface decision-in-graph smells without blocking.
    What: lints every ``docs/diagrams/*.md`` artifact and formats a short
    bullet list keyed by file; returns ``""`` when disabled or clean.
    Test: a project with a parked-node diagram yields a non-empty string
    naming the file and the parked node; a clean project yields ``""``.
    """
    if not diagram_lint_enabled():
        return ""
    root = Path(project_path)
    lines: list[str] = []
    for artifact in find_diagram_artifacts(root):
        advisories = lint_diagram_file(artifact)
        if not advisories:
            continue
        try:
            rel = artifact.relative_to(root)
        except ValueError:
            rel = artifact
        for adv in advisories:
            lines.append(f"  - {rel}: {adv.message}")
    if not lines:
        return ""
    return (
        "\nDiagram advisory (decisions belong in tables, not the graph — "
        "advisory only, not blocking):\n" + "\n".join(lines)
    )
