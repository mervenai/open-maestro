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
import os
import re
import subprocess
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

# Auto deep-review thresholds (MSTRO-123). The per-turn artifact-critic gate
# stays at its cheap 50-line tripwire; the 3-persona deep review is an order
# of magnitude more expensive, so it only fires when the change is
# milestone-significant. All knobs are env-tunable, mirroring the
# MAESTRO_ARTIFACT_* convention.
DEFAULT_DEEP_REVIEW_MIN_LINES = 150
DEEP_REVIEW_HEADER_LINES = 50  # version-marker window from the top of the file

# A changed line in the header region that looks like a deliberate version
# event (v3.1, "Version: 2.0", "supersedes ...") — not just any number pair.
VERSION_MARKER_RE = re.compile(r"(?i)(version\s*[:=]?\s*v?\d|\bv\d+\.\d|supersedes)")

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


def auto_review_enabled() -> bool:
    """Whether the auto deep-review may run (default on, MSTRO-123).

    Mirrors the ``MAESTRO_ARTIFACT_CRITIC`` kill-switch convention: set
    ``MAESTRO_DEEP_REVIEW=off`` (also "0", "false", "no") to disable the
    auto-invocation. The explicit ``maestro --review`` command and the
    ``/complete`` gate are unaffected.
    """
    flag = os.environ.get("MAESTRO_DEEP_REVIEW", "").strip().lower()
    return flag not in {"off", "0", "false", "no"}


def _deep_review_min_lines() -> int:
    """Effective volume threshold, honoring ``MAESTRO_DEEP_REVIEW_MIN_LINES``."""
    raw = os.environ.get("MAESTRO_DEEP_REVIEW_MIN_LINES", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.debug("deep review: bad MAESTRO_DEEP_REVIEW_MIN_LINES=%r", raw)
    return DEFAULT_DEEP_REVIEW_MIN_LINES


def _matches_blueprint_pattern(rel_path: str) -> bool:
    rel = rel_path.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel, pattern) for pattern in BLUEPRINT_PATTERNS)


def blueprint_milestone_in_progress(project_path: str | Path) -> bool:
    """Soft condition: some in-progress milestone owns blueprint artifacts.

    Auto-reviewing every project's docs edits would burn personas on
    projects that have no design milestone. Projects without a milestone
    plan still get reviewed (the plan may not exist yet — e.g. right after
    the first blueprint is drafted).
    """
    from open_maestro.milestones.models import MilestoneStatus
    from open_maestro.milestones.store import MilestoneStore

    root = Path(project_path)
    try:
        plan = MilestoneStore(root).load()
    except Exception as exc:  # no plan yet, or unreadable — do not suppress
        logger.debug("deep review: no milestone plan (%s); reviewing anyway", exc)
        return True
    for epic in plan.epics:
        for milestone in epic.milestones:
            if milestone.status != MilestoneStatus.IN_PROGRESS:
                continue
            if any(
                _matches_blueprint_pattern(a.path) for a in milestone.artifacts
            ):
                return True
    return False


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=30
    )


def _is_new_file(root: Path, rel: str) -> bool:
    """True when *rel* is not tracked at HEAD (created or still untracked)."""
    out = _git(root, ["cat-file", "-e", f"HEAD:{rel}"])
    if out.returncode == 0:
        return False
    # Non-git fallback (MSTRO-127): "new" = absent from the turn-start mtime
    # snapshot. Without a snapshot we cannot tell pre-existing files from
    # fresh ones, so treat everything as new rather than suppressing review.
    from open_maestro.orchestrator import critic as critic_mod

    if not critic_mod.in_git_repo(root):
        snap = critic_mod.mtime_snapshot(root)
        return True if snap is None else rel not in snap
    return True


def _untracked_blueprint_files(root: Path) -> list[str]:
    out = _git(root, ["ls-files", "--others", "--exclude-standard"])
    if out.returncode != 0:
        return _blueprint_files_new_since_snapshot(root)
    return [
        line.strip()
        for line in out.stdout.splitlines()
        if line.strip() and _matches_blueprint_pattern(line.strip())
    ]


def _blueprint_files_new_since_snapshot(root: Path) -> list[str]:
    """Non-git fallback (MSTRO-127): blueprint-pattern files absent from the
    critic gate's mtime snapshot — i.e. created since the turn-start
    baseline. Pre-existing files are handled as edits via their changed-line
    count, not as "new artifacts"."""
    from open_maestro.orchestrator import critic as critic_mod

    if critic_mod.in_git_repo(root):
        return []
    snap = critic_mod.mtime_snapshot(root)
    if snap is None:
        return []
    found = {
        str(p.relative_to(root))
        for pattern in BLUEPRINT_PATTERNS
        for p in root.glob(pattern)
        if p.is_file()
    }
    return sorted(found - set(snap))


def _version_marker_changed(root: Path, rel: str, before_ref: str | None) -> bool:
    """True when the diff touches a version-marker line in the header region."""
    commands = []
    if before_ref:
        commands.append(["diff", f"{before_ref}..HEAD", "--", rel])
    commands.append(["diff", "HEAD", "--", rel])
    for args in commands:
        out = _git(root, args)
        if out.returncode != 0:
            continue
        new_line = 0
        for line in out.stdout.splitlines():
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                new_line = int(hunk.group(1)) - 1
                continue
            if line.startswith("+") or line.startswith("-"):
                new_line += 1 if line.startswith("+") else 0
                if new_line <= DEEP_REVIEW_HEADER_LINES and VERSION_MARKER_RE.search(
                    line[1:]
                ):
                    return True
            elif not line.startswith("\\"):
                new_line += 1
    return False


def select_deep_review_targets(
    project_path: str | Path,
    changes: list[tuple[str, int]],
    before_ref: str | None = None,
) -> list[tuple[Path, str]]:
    """Blueprint artifacts changed this turn that warrant a deep review.

    *changes* is the raw [(path, changed-lines)] list for the turn (the
    same plumbing the critic gate uses). Returns [(doc, reason)] for docs
    that pass ALL of:

    * kill switch ``MAESTRO_DEEP_REVIEW`` not set to off,
    * an in-progress milestone owns blueprint artifacts (soft condition),
    * the gate ledger has no passing audit for the doc's *current* bytes,
    * and the change is significant: new artifact, >= threshold changed
      lines (``MAESTRO_DEEP_REVIEW_MIN_LINES``, default 150), or a
      version-marker change in the header region.
    """
    root = Path(project_path)
    if not auto_review_enabled():
        return []
    if not blueprint_milestone_in_progress(root):
        return []

    ledger = GateLedger().for_project(root)
    min_lines = _deep_review_min_lines()
    candidates: dict[str, int] = {}
    for rel, n in changes:
        if _matches_blueprint_pattern(rel):
            candidates[rel] = candidates.get(rel, 0) + n
    # Newly created artifacts are often untracked, so git diff misses them.
    for rel in _untracked_blueprint_files(root):
        candidates.setdefault(rel, 0)

    targets: list[tuple[Path, str]] = []
    for rel, n in sorted(candidates.items()):
        doc = root / rel
        if not doc.is_file():
            continue
        try:
            if not ledger.check(doc):
                continue  # passing audit already recorded for these bytes
        except Exception as exc:
            logger.debug("deep review: ledger check failed for %s: %s", rel, exc)
        if _is_new_file(root, rel):
            reason = "new blueprint artifact"
        elif n >= min_lines:
            reason = f"{n} changed lines (>= {min_lines})"
        elif _version_marker_changed(root, rel, before_ref):
            reason = "version marker changed"
        else:
            continue
        targets.append((doc, reason))
    return targets


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


# Personas get a few turns, not one: the prompts point at the artifact on
# disk, and CLI-seated agents (kimi-cli, claude-cli) spend a turn reading
# the file before answering. max_turns=1 cut them mid-tool-use with
# error_max_turns (MSTRO-135) — tool-less endpoints (DeepSeek) were immune,
# which is why calibration passed but --review did not.
PERSONA_MAX_TURNS = 4


def _seat_persona_runtime(
    used_models: set[str],
    base_exclude: set[str] | None = None,
) -> tuple[AgentRuntime, str]:
    """Seat a review persona on the best *configured* model.

    Without this, ``create_runtime(None)`` lands on the auto-detected
    runtime's SDK default (gpt-4o), which has no endpoint in most setups
    — every persona errors and the gate FAILs spuriously. Personas rotate
    across the configured model families (models already used this review
    are excluded, falling back to reuse when only one qualifies), and
    session-quota-exhausted models are always skipped. Preference is a
    deep-reasoning seat; the profile degrades to LIGHT so the gate still
    runs when no deep-reasoning model is configured.
    """
    from open_maestro.config.capabilities import (
        CodingStrength,
        LatencyHint,
        ReasoningLevel,
        TaskProfile,
    )
    from open_maestro.runtime import quota as quota_mod
    from open_maestro.runtime.factory import create_runtime, select_runtime_for_task

    exclude = set(base_exclude or ()) | quota_mod.exhausted() | used_models
    attempts = [
        (ReasoningLevel.DEEP, exclude or None),
        (ReasoningLevel.LIGHT, exclude or None),
        (ReasoningLevel.LIGHT, None),
    ]
    last_exc: Exception | None = None
    for reasoning, excl in attempts:
        profile = TaskProfile(
            needs_tools=False,
            reasoning_depth=reasoning,
            coding_strength=CodingStrength.LOW,
            context_tokens_estimate=32000,
            latency_preference=LatencyHint.MEDIUM,
        )
        try:
            runtime_type, model = select_runtime_for_task(profile, exclude=excl)
        except RuntimeError as exc:
            last_exc = exc
            continue
        used_models.add(model)
        logger.info("Seated review persona on %s via %s", model, runtime_type)
        return create_runtime(runtime_type), model
    raise RuntimeError(
        f"no configured model available for review personas: {last_exc}"
    )


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
    model_exclude: set[str] | None = None,
) -> DeepReviewResult:
    """Run the gating personas against *doc* as fresh, isolated agents.

    Each persona gets its own runtime instance (fresh context — the agent
    has not seen the author's reasoning or another audit's output).  RESULT
    lines are recorded to the gate ledger.

    When *runtime_factory* is None (production), personas are seated on the
    best configured models via the capability router — never the runtime
    SDK default, which is usually unconfigured. Seats rotate across model
    families, skip *model_exclude* and session-quota-exhausted models, and
    re-seat once when a run comes back with a quota-exhausted error.
    """
    doc = Path(doc)
    project = Path(project_path) if project_path else doc.parent
    seat_models = runtime_factory is None
    if seat_models:
        from open_maestro.runtime.factory import create_runtime

        runtime_factory = create_runtime
    if ledger is None:
        ledger = GateLedger().for_project(project)

    kwargs = _default_persona_kwargs(doc, project)
    kwargs.update(extra_kwargs or {})

    result = DeepReviewResult(doc=doc)
    used_models: set[str] = set()
    for persona_id in personas:
        from open_maestro.review.personas import PERSONAS

        if persona_id not in PERSONAS:
            result.errors[persona_id] = "unknown persona"
            continue
        prompt = render(persona_id, **kwargs)
        reseats = 0
        while True:
            seated_model: str | None = None
            try:
                if seat_models:
                    # Production path: seat on a configured model, not the
                    # runtime SDK default (gpt-4o) — see _seat_persona_runtime.
                    runtime, seated_model = _seat_persona_runtime(
                        used_models, model_exclude
                    )
                    cfg = AgentConfig(model=seated_model, max_turns=PERSONA_MAX_TURNS)
                else:
                    # Test/caller-supplied factory: honor it verbatim.
                    runtime = runtime_factory(None)
                    cfg = AgentConfig(max_turns=1)
                out = await runtime.run(prompt, config=cfg)
                if out.is_error:
                    quota_reason = out.metadata.get("quota_exhausted")
                    if seated_model and quota_reason and reseats < 1:
                        from open_maestro.runtime import quota as quota_mod

                        quota_mod.mark_exhausted(seated_model)
                        logger.warning(
                            "persona %s: model %s quota exhausted (%s); "
                            "re-seating on another model",
                            persona_id,
                            seated_model,
                            quota_reason,
                        )
                        reseats += 1
                        continue
                    raise RuntimeError(out.text[:200])
                report = out.text
                break
            except Exception as exc:
                result.errors[persona_id] = str(exc)[:300]
                logger.warning("persona %s failed on %s: %s", persona_id, doc, exc)
                break
        if persona_id in result.errors:
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
