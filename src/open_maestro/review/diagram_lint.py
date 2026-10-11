"""Advisory Mermaid linter for blueprint diagram artifacts (fixes #4).

Why: the user-journey blueprint diagram standard (mermaid-diagrams skill) says
decisions never belong in the graph — parked/resolved decisions live in the
companion tables, and open gates are inline ``:::open`` tags on the component
they gate, not standalone nodes wired in with dotted attachment edges. Nothing
enforced that, so output quality depended on each agent run's discipline.

What: ``lint_diagram_file`` / ``lint_mermaid_source`` parse fenced ```mermaid
blocks in ``docs/diagrams/*.md`` artifacts and emit ADVISORIES (never blocks)
for exactly three anti-patterns:

  (a) in-graph parked nodes — any node carrying the ``:::parked`` class;
  (b) standalone open-decision nodes attached by dotted edges — a dotted edge
      (``-.->`` / ``-. label .->``) whose endpoint is a *standalone* open
      decision/question node (a label with no real component identity left once
      the open/question marker is stripped). A real component that merely
      carries a correct inline ``OPEN (R-nn)`` tag alongside substantive
      component text is EXEMPT even when reached by a dotted dependency edge —
      that is the prescribed pattern, not an anti-pattern (fixes #6, bug 1);
  (c) inline resolved/closed decision prose — a node OR edge label that embeds
      a resolved-disposition verb (RATIFIED/DECIDED/RESOLVED/… ) next to a
      decision ID or a ``vX.Y`` decision stamp. Such closures belong in the
      Closed / decision-register tables, not the graph (fixes #6, bug 2). A
      bare inline ``OPEN (id)`` gate tag is explicitly allowed here.

It deliberately does NOT check node counts, label length, or header size
(out of scope per #4/#6). ``diagram_advisory`` mirrors ``blueprint.gate_advisory``:
advise, don't block, with an env kill-switch (``MAESTRO_DIAGRAM_LINT=off``).

Test: feed ``lint_mermaid_source`` a block with a ``:::parked`` node and assert
one PARKED advisory; a dotted edge to a standalone ``:::open`` question node and
assert one OPEN_ATTACHMENT advisory; a dotted edge to a real component carrying
an inline ``OPEN (id)`` tag and assert none; a node/edge label with a
disposition verb + ``vX.Y`` stamp and assert one RESOLVED_PROSE advisory; a
clean inline-tagged diagram and assert none; and an over-budget-but-clean
diagram (many nodes, long labels) and assert none.
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
RESOLVED_PROSE_CODE = "resolved-decision-prose-in-label"

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

# Any edge label text. Two shapes carry labels:
#   - pipe form:   ``A -->|row click| B`` / ``A -.->|R-07| B``
#   - dotted inline: ``A -. ingests into stack .-> B``
# Bug 2 scans these for resolved-decision prose, so we need every label's text.
_PIPE_EDGE_LABEL_RE = re.compile(r"\|([^|]*)\|")
_DOTTED_INLINE_LABEL_RE = re.compile(r"-\.(?P<label>[^.]*?)\.-+[>xo]?")

# A node *definition* with a bracketed label, e.g. ``O7["OPEN: pick X"]``.
# Captures id + the inner label text (first bracket style that matches).
_NODE_LABEL_RE = re.compile(
    r"([A-Za-z_][\w-]*)\s*(?:\[\"?(?P<sq>[^\]\"]*)\"?\]|\(\"?(?P<rd>[^)\"]*)\"?\)|\{\"?(?P<cl>[^}\"]*)\"?\})"
)

# Label text that marks a node as an open-decision / question node.
_OPEN_LABEL_RE = re.compile(r"^\s*(?:OPEN\b|O#|O\d|Q#|Q\d|R-?\d+\s*\?)", re.I)

# --- Bug 1: distinguishing a STANDALONE open-decision node from a REAL
# component that merely carries an inline ``OPEN (id)`` tag --------------------
#
# Heuristic: strip from the label everything that is pure decision/journey
# scaffolding — the leading ``number ·`` ordinal, any inline open/question
# marker (``OPEN (R-07)`` / ``O#`` / ``Q#`` / ``R-07?``), any ``:::open`` class
# residue, bracket/quote punctuation, ``<br/>`` separators, evidence crumbs
# (``FR-12`` / ``§4.2``), and italic repo tags (``<i>api</i>``). Whatever text
# survives is the node's own *component identity*. If meaningful words remain
# (e.g. "Export API", "Databricks ingestion job"), it is a real component that
# happens to be gated → EXEMPT from the dotted-attachment rule. If nothing
# substantive remains (the label was essentially just the decision marker /
# question text), it is a standalone decision bubble → STILL flagged.

# A leading ``"10 · "`` / ``"3. "`` ordinal prefix used by the node-label format.
_ORDINAL_PREFIX_RE = re.compile(r"^\s*\d+\s*(?:·|\.|\)|-)\s*")

# Inline open/question markers to strip before judging remaining identity:
# ``OPEN (R-07)`` / ``OPEN: pick X`` / ``OPEN`` / ``O#`` / ``O3`` / ``Q#`` /
# ``Q2`` / ``R-07?``. These express the *gate*, not the component.
_OPEN_MARKER_RE = re.compile(
    r"OPEN\b(?:\s*[:\-]?\s*)?(?:\([^)]*\))?|O#\d*|O\d+|Q#\d*|Q\d+|R-?\d+\s*\?",
    re.I,
)

# HTML-ish label noise (``<br/>``, ``<i>repo</i>``) and evidence crumbs.
_LABEL_NOISE_RE = re.compile(r"<[^>]+>|FR-\d[\w.-]*|EP-\d[\w.-]*|§\s*[\w.]+", re.I)

# --- Bug 2: inline resolved/closed decision prose ----------------------------
#
# A resolved-disposition verb set (whole-word, case-insensitive). These name a
# *closed* decision outcome; per the skill they belong in the Closed /
# decision-register tables, never embedded in a live node/edge label. The
# open-gate word "OPEN" is intentionally NOT in this set — a bare inline
# ``OPEN (R-07)`` tag is the prescribed pattern and must stay allowed.
_DISPOSITION_VERBS: tuple[str, ...] = (
    "RATIFIED",
    "DECIDED",
    "RESOLVED",
    "CLOSED",
    "DESCOPED",
    "SUPERSEDED",
    "DEMOTED",
    "DROPPED",
    "EXCLUDED",
    "REJECTED",
    "ACCEPTED",
    "VERIFIED",
    "RESEARCHED",
    "DEPRECATED",
    "REVISED",
)

# A decision ID like ``R-16`` / ``D-5`` / ``EP-04`` — the register key a
# disposition hangs off. Used to disambiguate a genuine decision disposition
# (``R-16 RESOLVED``) from a UI-state noun (a "Resolved" state chip).
_DECISION_ID_RE = re.compile(r"\b[A-Z]{1,3}-?\d+\b")

# A version-stamped decision disposition: ``v3.7`` (node labels, dots intact)
# or ``v3 7`` (edge labels, where mermaid forbids the dot so authors write a
# space). The ``vX.Y`` / ``vX Y`` stamp is itself a closure marker.
_VERSION_STAMP_RE = re.compile(r"\bv\s*\d+\s*[. ]\s*\d+\b", re.I)

# Matching approach (documented per #6): a label is "resolved-decision prose"
# when EITHER
#   (1) it carries a vX.Y / vX Y decision stamp (always a closure marker), OR
#   (2) a disposition verb appears ADJACENT to a decision ID (within a short
#       window, either order: ``R-16 RESOLVED`` or ``RATIFIED D-5``).
# Requiring an adjacent decision ID for the verb path is what keeps the tricky
# UI-state-chip case ("New / Under review / Escalated / Resolved" — a bare
# state noun with no decision ID and no vX.Y) from false-positiving, while a
# bare ``OPEN (R-07)`` gate (no disposition verb, no stamp) is never matched.
_VERB_ALTERNATION = "|".join(_DISPOSITION_VERBS)
# verb immediately preceded OR followed by a decision ID within ~24 chars.
_VERB_NEAR_ID_RE = re.compile(
    rf"(?:{_DECISION_ID_RE.pattern}[^A-Za-z]{{0,24}}(?:{_VERB_ALTERNATION})"
    rf"|(?:{_VERB_ALTERNATION})[^A-Za-z]{{0,24}}{_DECISION_ID_RE.pattern})",
    re.I,
)


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


def _component_text_before_open(label: str) -> str:
    """Return the component identity that appears BEFORE the open marker.

    Why: Bug 1 — the robust signal separating a *real gated component* from a
    *standalone decision bubble* is label *position*. The prescribed pattern is
    ``number · <Component title> — OPEN (R-nn)``: the OPEN tag trails real
    component text. A standalone bubble *leads* with the marker
    (``OPEN: which model?`` / ``OPEN (R-9): batch size?``), so nothing
    substantive precedes it — the whole label IS the question.
    What: strips the leading ``number ·`` ordinal and label noise, truncates at
    the first OPEN/O#/Q#/R-nn? marker, and returns whatever component text came
    before it (collapsed).
    Test: ``"12 · Export API — OPEN (R-08)<br/>FR-20<br/><i>api</i>"`` -> text
    containing "Export API"; ``"OPEN (R-9): batch size?"`` -> ""; ``"OPEN:
    which subscription model? (R-07)"`` -> "".
    """
    text = _ORDINAL_PREFIX_RE.sub(" ", label)
    # Truncate at the first open/question marker — only text before it counts
    # as the node's own component identity.
    marker = _OPEN_MARKER_RE.search(text)
    if marker is not None:
        text = text[: marker.start()]
    text = _LABEL_NOISE_RE.sub(" ", text)
    # Drop separator punctuation the format uses between crumbs.
    text = re.sub(r"[—–\-:·|/()\"']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _edge_labels(body: str) -> list[str]:
    """Return every edge label's text (pipe form and dotted-inline form).

    Why: Bug 2 scans edge labels (not just node labels) for resolved-decision
    prose — the issue cites ``N13 -.… D-2 ratified v3 7.-> N12``.
    What: collects ``|...|`` pipe labels and ``-. ... .->`` dotted-inline labels
    from each comment-stripped line.
    Test: ``"A -.D-2 ratified v3 7.-> B"`` yields ``["D-2 ratified v3 7"]``;
    ``"A -->|row click| B"`` yields ``["row click"]``.
    """
    found: list[str] = []
    for raw in body.splitlines():
        line = _strip_comments(raw)
        found.extend(m.strip() for m in _PIPE_EDGE_LABEL_RE.findall(line))
        found.extend(m.group("label").strip() for m in _DOTTED_INLINE_LABEL_RE.finditer(line))
    return [lbl for lbl in found if lbl]


def _is_open_decision_node(
    node_id: str,
    classes: dict[str, set[str]],
    labels: dict[str, str],
) -> bool:
    """Whether *node_id* carries an open gate (``:::open`` or an OPEN-ish label).

    Why: the first gate of the dotted-attachment check — a dotted edge can only
    be an anti-pattern when its endpoint is open at all.
    What: true if the node has the ``open`` class or an open/question label.
    Test: ``:::open`` node -> True; label ``OPEN (R-7)`` -> True; plain -> False.
    """
    if "open" in classes.get(node_id, set()):
        return True
    label = labels.get(node_id, "")
    return bool(_OPEN_LABEL_RE.match(label))


def _is_standalone_open_node(
    node_id: str,
    classes: dict[str, set[str]],
    labels: dict[str, str],
) -> bool:
    """Whether *node_id* is a STANDALONE open-decision/question bubble (Bug 1).

    Why: only standalone decision bubbles — a question wired in with a dotted
    attachment edge — are the anti-pattern. A real component that carries a
    correct inline ``OPEN (id)`` tag alongside substantive component text is the
    prescribed pattern and must be exempt even on a dotted dependency edge.
    What: the node must be open (class/label), AND carry no real component
    identity BEFORE its open marker. Component identity reads as a noun phrase:
    >=2 words, or one substantial word (>=4 chars). Real gated components
    (``… Export API — OPEN (R-08)``) have identity before the tag → exempt;
    bubbles that LEAD with the marker (``OPEN: which model?``) have none → flag.
    Test: ``"Export API — OPEN (R-08)"`` -> False (real component, exempt);
    ``"Databricks ingestion job — OPEN (R-12)"`` -> False; ``"OPEN (R-9):
    batch size?"`` -> True (standalone question); ``"OPEN (R-07)"`` -> True.
    """
    if not _is_open_decision_node(node_id, classes, labels):
        return False
    identity = _component_text_before_open(labels.get(node_id, ""))
    words = [w for w in re.findall(r"[A-Za-z][\w]*", identity) if len(w) > 1]
    # A real component reads as a noun phrase before its gate tag: >=2 words,
    # or one substantial word (>=4 chars). Anything less means the label leads
    # with the decision marker → standalone bubble → flag it.
    if len(words) >= 2:
        return False
    if len(words) == 1 and len(words[0]) >= 4:
        return False
    return True


def _resolved_prose_hits(text: str) -> bool:
    """Whether *text* embeds resolved/closed decision prose (Bug 2).

    Why: closures (``R-16 RESOLVED``, ``D-5 RATIFIED``, ``v3.7``) belong in the
    Closed / decision-register tables, never a live node/edge label; but a bare
    ``OPEN (R-07)`` gate tag and a lone UI-state noun ("Resolved" state chip)
    must NOT trip this.
    What: true if the label carries a ``vX.Y`` / ``vX Y`` decision stamp, or a
    disposition verb adjacent to a decision ID. See ``_VERB_NEAR_ID_RE`` /
    ``_VERSION_STAMP_RE`` for the documented matching rationale.
    Test: ``"D-5 RATIFIED; D-6 DECIDED property-local v3.7"`` -> True;
    ``"D-2 ratified v3 7"`` -> True; ``"OPEN (R-07)"`` -> False;
    ``"New / Under review / Escalated / Resolved"`` -> False.
    """
    if _VERSION_STAMP_RE.search(text):
        return True
    return bool(_VERB_NEAR_ID_RE.search(text))


def lint_mermaid_source(body: str) -> list[DiagramAdvisory]:
    """Advisories for a single Mermaid block *body* (fences already stripped).

    Why: the core, side-effect-free check that both file linting and tests
    drive; it must advise exactly the three forbidden decision-in-graph shapes
    and nothing budget-related.
    What: returns a list of :class:`DiagramAdvisory` — one per distinct parked
    node group, one per standalone-open-node dotted-attachment group, and one
    for inline resolved/closed decision prose in node/edge labels.
    Test: parked block -> PARKED_CODE; dotted-to-standalone-open ->
    OPEN_ATTACHMENT_CODE; disposition verb+ID / vX.Y -> RESOLVED_PROSE_CODE;
    dotted-to-real-component / clean / over-budget-clean -> no such code.
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

    # (b) STANDALONE open-decision nodes attached by dotted edges. A real
    # component that merely carries an inline `OPEN (id)` tag is exempt even on
    # a dotted dependency edge (Bug 1 fix) — only identity-less decision bubbles
    # are flagged.
    attached: list[str] = []
    for left, _label, right in _DOTTED_EDGE_RE.findall(body):
        for endpoint in (left, right):
            if _is_standalone_open_node(endpoint, classes, labels):
                attached.append(endpoint)
    attached = sorted(set(attached))
    if attached:
        advisories.append(
            DiagramAdvisory(
                OPEN_ATTACHMENT_CODE,
                "standalone open-decision node(s) attached by dotted edges: "
                + ", ".join(attached)
                + " — tag the open gate inline on the live component it gates "
                "(`OPEN (R-nn)` with `:::open`) instead of a standalone question "
                "node plus a dotted attachment edge. (A real component that "
                "merely carries an inline `OPEN (id)` tag is fine on a dotted "
                "dependency edge.)",
            )
        )

    # (c) inline resolved/closed decision prose in node OR edge labels (Bug 2).
    prose: list[str] = []
    for node_id in sorted(labels):
        if _resolved_prose_hits(labels[node_id]):
            prose.append(node_id)
    edge_prose = sorted({lbl for lbl in _edge_labels(body) if _resolved_prose_hits(lbl)})
    if prose or edge_prose:
        parts: list[str] = []
        if prose:
            parts.append("node(s) " + ", ".join(prose))
        if edge_prose:
            parts.append("edge label(s) " + ", ".join(f'"{lbl}"' for lbl in edge_prose))
        advisories.append(
            DiagramAdvisory(
                RESOLVED_PROSE_CODE,
                "resolved/closed decision prose in "
                + "; ".join(parts)
                + " — a resolved-disposition verb (RATIFIED/DECIDED/RESOLVED/… ) "
                "or a vX.Y decision stamp belongs with its decision ID in the "
                "Closed / decision-register tables, not the graph. Keep only "
                "live-architecture text in the label (plus, if gated, an inline "
                "`OPEN (id)` tag).",
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
