"""Blueprint-stage adversarial review: deep review + "resolve, don't list".

Wires the review personas and the gate ledger into the Design Blueprint
milestone.  A design artifact (``docs/blueprint*.md``, ``*contract*``,
``*spec*``) must pass the review profile before the milestone it belongs to
is marked complete — unless the user forces.

Discipline (skill-share m3-blueprint-review):

* **Admission bar.**  A finding enters the register only if it carries a
  failure scenario ("if not fixed: [input] -> [wrong or insecure result]")
  or touches security, tenant isolation, audit trust, or irreversible data.
  Everything else is a post-freeze backlog item, not a blocker.
* **"Resolve, don't list."**  From round 2 on, the review does not dump
  findings; it produces a *resolution pack* (decision table + patch) the
  blueprint owner applies.
* **Stop rule.**  Stop when every admitted item has a resolution or the
  one-round timebox ends.  "No open objection" is NOT a stop criterion.
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from open_maestro.review.gate import GateLedger
from open_maestro.review.personas import render
from open_maestro.runtime.base import AgentConfig, AgentRuntime

logger = logging.getLogger(__name__)

BLUEPRINT_PATTERNS: tuple[str, ...] = (
    "docs/blueprint*.md",
    "docs/*contract*.md",
    "docs/*spec*.md",
)

# Personas that gate a blueprint artifact (the "review" profile minus noise
# triage and panel, which run separately or by the caller).
GATING_PERSONAS: tuple[str, ...] = ("blind-reader", "fidelity", "quote-context")

SECURITY_MARKERS: tuple[str, ...] = (
    "security",
    "tenant isolation",
    "audit trust",
    "irreversible",
    "auth",
    "permission",
)

FAILURE_SCENARIO_RE = re.compile(r"if not fixed", re.IGNORECASE)


@dataclass
class FindingSummary:
    """Admission-bar accounting for one audit report."""

    total: int = 0
    admitted: int = 0
    backlog: int = 0
    admitted_evidence: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"findings: {self.total}, admitted to register: {self.admitted}, "
            f"post-freeze backlog: {self.backlog}"
        )


def find_blueprint_artifacts(project_path: str | Path) -> list[Path]:
    """Design artifacts under *project_path* that the review gate applies to."""
    root = Path(project_path)
    found: list[Path] = []
    for pattern in BLUEPRINT_PATTERNS:
        found.extend(p for p in root.glob(pattern) if p.is_file())
    return sorted(set(found))


def admission_summary(report_text: str) -> FindingSummary:
    """Apply the admission bar to a persona report.

    Findings are numbered-defect blocks ("1. ...", "2. ...") per the persona
    output contract.  A finding is admitted when it states a failure
    scenario or names a security/audit/irreversible concern.
    """
    summary = FindingSummary()
    blocks = re.split(r"\n(?=\d{1,2}[.)]\s)", report_text)
    findings = [b for b in blocks if re.match(r"\d{1,2}[.)]\s", b.strip())]
    if not findings:  # persona found nothing — nothing to admit
        return summary
    for block in findings:
        summary.total += 1
        head = block[:600]
        if FAILURE_SCENARIO_RE.search(head) or any(
            m in head.lower() for m in SECURITY_MARKERS
        ):
            summary.admitted += 1
            first_line = block.strip().splitlines()[0]
            summary.admitted_evidence.append(first_line[:120])
        else:
            summary.backlog += 1
    return summary


@dataclass
class DeepReviewResult:
    doc: Path
    persona_reports: dict[str, str] = field(default_factory=dict)
    persona_verdicts: dict[str, tuple[str, list[str]]] = field(default_factory=dict)
    admissions: dict[str, FindingSummary] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.persona_verdicts) and all(
            v[0] == "pass" for v in self.persona_verdicts.values()
        )

    def register_text(self) -> str:
        lines = [f"# Review register — {self.doc.name}", ""]
        for persona, (verdict, fails) in self.persona_verdicts.items():
            status = "PASS" if verdict == "pass" else f"FAIL ({'; '.join(fails)})"
            lines.append(f"## {persona}: {status}")
            adm = self.admissions.get(persona)
            if adm:
                lines.append(f"- {adm.summary}")
                for ev in adm.admitted_evidence:
                    lines.append(f"  - {ev}")
            lines.append("")
        if self.errors:
            lines.append("## Errored personas")
            for persona, err in self.errors.items():
                lines.append(f"- {persona}: {err}")
        return "\n".join(lines)


def _default_persona_kwargs(doc: Path, project_path: Path) -> dict[str, str]:
    """Sensible default substitutions for the gating personas."""
    docs_dir = project_path / "docs"
    blueprints = sorted(docs_dir.glob("blueprint*.md"))
    prds = sorted(
        p for p in list(docs_dir.glob("*PRD*.md")) + list((project_path / "requirements").glob("*.md"))
        if p.is_file()
    )
    questions = "\n".join(
        f"{i}. {q}"
        for i, q in enumerate(
            [
                "What is the verdict of this document, in one sentence?",
                "How many findings are open, by severity?",
                "Which findings block freezing the design?",
                "What changed since the previous round?",
                "Who owns each open decision?",
                "What is the fix for the first open blocker?",
                "Which sections are marked final vs conditional?",
                "What does the design explicitly exclude?",
                "Which external service or contract does it depend on?",
                "Where does a reader report a correction?",
            ],
            start=1,
        )
    )
    return {
        "doc": str(doc),
        "questions": questions,
        "source_of_truth": str(doc),
        "inputs": f"- {doc.name}: the design artifact under review.",
        "repos_dir": str(project_path),
        "id_mapping": "(identity mapping)",
        "blueprint": str(blueprints[0]) if blueprints else str(doc),
        "prd": str(prds[0]) if prds else str(doc),
        "prototype": "(none available)",
        "delta_diff": "(delta runs supply this)",
        "final_doc": str(doc),
        "sources": str(doc),
    }


async def deep_review(
    doc: str | Path,
    *,
    project_path: str | Path | None = None,
    personas: tuple[str, ...] = GATING_PERSONAS,
    runtime_factory: Callable[..., AgentRuntime] | None = None,
    ledger: GateLedger | None = None,
    extra_kwargs: dict[str, str] | None = None,
) -> DeepReviewResult:
    """Run the gating personas against *doc* as fresh, isolated agents.

    Each persona gets its own runtime instance (fresh context — the agent
    has not seen the author's reasoning or another audit's output).  RESULT
    lines are recorded to the gate ledger.
    """
    doc = Path(doc)
    project = Path(project_path) if project_path else doc.parent
    if runtime_factory is None:
        from open_maestro.runtime.factory import create_runtime

        runtime_factory = create_runtime
    if ledger is None:
        ledger = GateLedger().for_project(project)

    kwargs = _default_persona_kwargs(doc, project)
    kwargs.update(extra_kwargs or {})

    result = DeepReviewResult(doc=doc)
    for persona_id in personas:
        from open_maestro.review.personas import PERSONAS

        if persona_id not in PERSONAS:
            result.errors[persona_id] = "unknown persona"
            continue
        prompt = render(persona_id, **kwargs)
        try:
            runtime = runtime_factory(None)
            cfg = AgentConfig(max_turns=1)
            out = await runtime.run(prompt, config=cfg)
            if out.is_error:
                raise RuntimeError(out.text[:200])
            report = out.text
        except Exception as exc:
            result.errors[persona_id] = str(exc)[:300]
            logger.warning("persona %s failed on %s: %s", persona_id, doc, exc)
            continue
        result.persona_reports[persona_id] = report
        result.admissions[persona_id] = admission_summary(report)
        try:
            result.persona_verdicts[persona_id] = ledger.record(
                doc, persona_id, report
            )
        except Exception as exc:
            result.errors[persona_id] = f"ledger: {exc}"
    return result


def blueprint_gate(project_path: str | Path, profile: str = "review") -> list[str]:
    """Problems blocking milestone exit: blueprint artifacts whose review
    gates have not passed on their current bytes.  Empty = clear."""
    ledger = GateLedger().for_project(project_path)
    problems: list[str] = []
    for artifact in find_blueprint_artifacts(project_path):
        probs = ledger.check(artifact, profile=profile)
        for p in probs:
            problems.append(f"{artifact.relative_to(project_path)}: {p}")
    return problems


def milestone_review_problems(project_path: str | Path, milestone: Any) -> list[str]:
    """Review-gate problems for the artifacts *milestone* owns.

    A milestone owns a design artifact when one of its artifact patterns
    matches the on-disk blueprint file.  Milestones with no blueprint
    artifacts are not review-gated.
    """
    root = Path(project_path)
    owned = [
        artifact
        for artifact in find_blueprint_artifacts(root)
        if any(
            fnmatch.fnmatch(str(artifact.relative_to(root)), pattern)
            for pattern in (a.path for a in milestone.artifacts)
        )
    ]
    if not owned:
        return []
    ledger = GateLedger().for_project(root)
    problems: list[str] = []
    for artifact in owned:
        for prob in ledger.check(artifact):
            problems.append(f"{artifact.relative_to(root)}: {prob}")
    return problems


def gate_advisory(project_path: str | Path) -> str:
    """Non-blocking advisory shown for in-progress milestones."""
    problems = blueprint_gate(project_path)
    if not problems:
        return ""
    return "\nReview gate (not passed — run `maestro --review <doc>`):\n" + "\n".join(
        f"  - {p}" for p in problems
    )


async def resolution_pack(
    review: DeepReviewResult,
    *,
    runtime_factory: Callable[..., AgentRuntime] | None = None,
    output_path: str | Path | None = None,
) -> Path | None:
    """Round 2+: 'resolve, don't list'.

    A writer agent turns the admitted findings into a resolution pack —
    a decision table (finding -> accept/reject + rationale -> exact patch
    text) the blueprint owner applies.  Returns the pack path.
    """
    admitted = {
        persona: adm
        for persona, adm in review.admissions.items()
        if adm.admitted
    }
    if not admitted:
        return None
    if runtime_factory is None:
        from open_maestro.runtime.factory import create_runtime

        runtime_factory = create_runtime
    register = review.register_text()
    prompt = (
        "You are the blueprint owner applying an adversarial review. Below is "
        "the review register for your design artifact. Produce a RESOLUTION "
        "PACK: a Markdown decision table with one row per admitted finding — "
        "finding, your decision (ACCEPT / REJECT), rationale, and the exact "
        "replacement text (or edit instruction) to apply. Resolve, don't "
        "list: every admitted row must end with applicable text, not a plan "
        "to think about it. Post-freeze backlog items get one line each. "
        "Do not re-litigate findings that lack a failure scenario.\n\n"
        f"Artifact: {review.doc}\n\n{register}"
    )
    runtime = runtime_factory(None)
    out = await runtime.run(prompt, config=AgentConfig(max_turns=3))
    if out.is_error or not out.text.strip():
        raise RuntimeError(f"resolution pack generation failed: {out.text[:200]}")
    if output_path is None:
        output_path = review.doc.with_suffix(".resolution-pack.md")
    pack = Path(output_path)
    pack.write_text(out.text)
    return pack
