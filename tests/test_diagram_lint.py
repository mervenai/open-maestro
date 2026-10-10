"""Tests for the advisory Mermaid diagram linter (fixes #4).

The linter advises (never blocks) exactly two anti-patterns: in-graph parked
(`:::parked`) nodes, and standalone open-decision nodes attached by dotted
edges. It must NOT flag node counts, label length, or header size.
"""

from __future__ import annotations

from open_maestro.review.diagram_lint import (
    OPEN_ATTACHMENT_CODE,
    PARKED_CODE,
    diagram_advisory,
    diagram_lint_enabled,
    find_diagram_artifacts,
    lint_diagram_file,
    lint_mermaid_source,
    lint_mermaid_text,
)

# A clean diagram: open gate inline-tagged on the component it gates, parked
# items only referenced by ID (no parked nodes, no dotted attachment edges).
CLEAN_BLOCK = """flowchart TB
  classDef open fill:#fff3cd,stroke:#e0a800
  classDef netnew fill:#cfe2ff,stroke:#084298
  subgraph BACKEND["Backend"]
    B1["3 · Subscription check — OPEN (R-07)<br/>FR-12<br/><i>api</i>"]:::open
    B2["4 · Importer<br/>FR-13<br/><i>api</i>"]:::netnew
  end
  U1["1 · User clicks row"]:::user
  U1 -->|row click| B1
  B1 -->|GET /api/sub| B2
"""

# A diagram with an in-graph parked node (anti-pattern a).
PARKED_BLOCK = """flowchart TB
  classDef parked fill:#f8d7da,stroke:#b02a37,stroke-dasharray:5 4
  subgraph PARKED["Parked / out of scope"]
    P1["P1 · Offline mode (DROPPED §4.2)"]:::parked
  end
  B1["3 · Importer"]:::netnew
  B1 -->|writes| B2["4 · Store"]:::netnew
"""

# A diagram with a standalone open-decision node attached by a dotted edge
# (anti-pattern b): O7 is an :::open question node, wired in with `-.->`.
OPEN_ATTACHMENT_BLOCK = """flowchart TB
  classDef open fill:#fff3cd,stroke:#e0a800
  B1["3 · Subscription check"]:::netnew
  O7["OPEN: which subscription model? (R-07)"]:::open
  B1 -.R-07.-> O7
"""

# A clean but deliberately over-budget diagram: many nodes, very long labels,
# a big header — none of which the linter should flag.
_LONG = "x" * 400
OVER_BUDGET_CLEAN_BLOCK = (
    "flowchart TB\n  classDef netnew fill:#cfe2ff\n"
    + "\n".join(f'  N{i}["{i} · Node {i} {_LONG}<br/>FR-{i}"]:::netnew' for i in range(60))
    + "\n"
    + "\n".join(f"  N{i} -->|step {i}| N{i + 1}" for i in range(59))
)


def _md(block: str) -> str:
    """Wrap a mermaid block in a Markdown fenced artifact."""
    return f"# Blueprint diagram\n\nHow to read: ...\n\n## Diagram\n\n```mermaid\n{block}```\n"


class TestParkedNodes:
    def test_parked_node_raises_advisory(self):
        advisories = lint_mermaid_source(PARKED_BLOCK)
        codes = {a.code for a in advisories}
        assert PARKED_CODE in codes
        parked = next(a for a in advisories if a.code == PARKED_CODE)
        assert "P1" in parked.message

    def test_parked_via_class_statement(self):
        block = 'flowchart TB\n  P1["P1 · Offline (DROPPED)"]\n  class P1 parked\n'
        codes = {a.code for a in lint_mermaid_source(block)}
        assert PARKED_CODE in codes


class TestOpenAttachment:
    def test_dotted_edge_to_open_node_raises_advisory(self):
        advisories = lint_mermaid_source(OPEN_ATTACHMENT_BLOCK)
        codes = {a.code for a in advisories}
        assert OPEN_ATTACHMENT_CODE in codes
        open_adv = next(a for a in advisories if a.code == OPEN_ATTACHMENT_CODE)
        assert "O7" in open_adv.message

    def test_open_node_by_label_marker(self):
        # No :::open class, but the label starts with an OPEN marker.
        block = 'flowchart TB\n  B1["Importer"]\n  Q1["OPEN (R-9): batch size?"]\n  B1 -.-> Q1\n'
        codes = {a.code for a in lint_mermaid_source(block)}
        assert OPEN_ATTACHMENT_CODE in codes

    def test_solid_edge_to_open_node_is_clean(self):
        # An inline open gate reached by a SOLID edge is fine — only dotted
        # attachment of a standalone open node is the anti-pattern.
        block = (
            "flowchart TB\n"
            "  classDef open fill:#fff3cd\n"
            '  B1["Importer"]:::netnew\n'
            '  B2["Check — OPEN (R-07)"]:::open\n'
            "  B1 -->|calls| B2\n"
        )
        assert lint_mermaid_source(block) == []


class TestCleanDiagrams:
    def test_clean_diagram_no_advisory(self):
        assert lint_mermaid_source(CLEAN_BLOCK) == []

    def test_over_budget_but_clean_passes(self):
        # Many nodes + long labels + big header must NOT trigger the linter:
        # node-count / label-length / header budgets are out of scope (#4).
        assert lint_mermaid_source(OVER_BUDGET_CLEAN_BLOCK) == []

    def test_does_not_flag_counts_or_label_length(self):
        advisories = lint_mermaid_source(OVER_BUDGET_CLEAN_BLOCK)
        assert not any(
            term in a.message.lower()
            for a in advisories
            for term in ("count", "label length", "header", "budget", "too many")
        )


class TestTextAndFile:
    def test_lint_mermaid_text_dedupes_across_blocks(self):
        text = _md(PARKED_BLOCK) + "\n\n```mermaid\n" + CLEAN_BLOCK + "```\n"
        advisories = lint_mermaid_text(text)
        assert [a.code for a in advisories] == [PARKED_CODE]

    def test_lint_diagram_file(self, tmp_path):
        doc = tmp_path / "blueprint-user-journey.md"
        doc.write_text(_md(PARKED_BLOCK))
        advisories = lint_diagram_file(doc)
        assert {a.code for a in advisories} == {PARKED_CODE}

    def test_lint_clean_file_no_advisory(self, tmp_path):
        doc = tmp_path / "blueprint-user-journey.md"
        doc.write_text(_md(CLEAN_BLOCK))
        assert lint_diagram_file(doc) == []

    def test_find_diagram_artifacts(self, tmp_path):
        (tmp_path / "docs" / "diagrams").mkdir(parents=True)
        (tmp_path / "docs" / "diagrams" / "x.md").write_text(_md(CLEAN_BLOCK))
        (tmp_path / "docs" / "notes.md").write_text("not a diagram")
        found = find_diagram_artifacts(tmp_path)
        assert [p.name for p in found] == ["x.md"]


class TestAdvisoryWiring:
    def test_diagram_advisory_reports_parked(self, tmp_path):
        d = tmp_path / "docs" / "diagrams"
        d.mkdir(parents=True)
        (d / "blueprint-user-journey.md").write_text(_md(PARKED_BLOCK))
        advisory = diagram_advisory(tmp_path)
        assert "blueprint-user-journey.md" in advisory
        assert "advisory only" in advisory.lower()
        assert "P1" in advisory

    def test_diagram_advisory_clean_is_empty(self, tmp_path):
        d = tmp_path / "docs" / "diagrams"
        d.mkdir(parents=True)
        (d / "x.md").write_text(_md(CLEAN_BLOCK))
        assert diagram_advisory(tmp_path) == ""

    def test_diagram_advisory_respects_kill_switch(self, tmp_path, monkeypatch):
        d = tmp_path / "docs" / "diagrams"
        d.mkdir(parents=True)
        (d / "x.md").write_text(_md(PARKED_BLOCK))
        monkeypatch.setenv("MAESTRO_DIAGRAM_LINT", "off")
        assert not diagram_lint_enabled()
        assert diagram_advisory(tmp_path) == ""
