"""Adversarial review personas, ported from the skill-share publish-gate.

Each persona is a fixed prompt run by a *fresh* agent that has not seen the
author's reasoning or any other audit's output.  Every prompt ends with a
machine-parseable ``RESULT k=v ...`` line that ``open_maestro.review.gate``
parses and enforces against numeric thresholds.

Origin: ``skill-share`` (merven-publish-gate prompts).  Merven-specific rules
(Linear plumbing, named-person bans, org-specific comment formats) were
removed or generalized; the audit mechanics, questions, and RESULT contracts
are kept verbatim so results stay comparable across rounds.

Grounding rule (applies to every persona): the agent sees only the files
named in its prompt — never the author's notes, never another audit's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template


@dataclass(frozen=True)
class Persona:
    """A fixed adversarial-audit prompt with its RESULT-line contract."""

    id: str
    description: str
    template: Template
    result_keys: tuple[str, ...]


_COMMON_RESULT_RULE = (
    "End with one line exactly in the given RESULT form. "
    "Report only; do not edit any file."
)

PERSONAS: dict[str, Persona] = {}

PERSONAS["blind-reader"] = Persona(
    id="blind-reader",
    description=(
        "First-time reader quiz: can a reader get the verdict, counts, and "
        "key facts from the document alone, without reconciling two places?"
    ),
    template=Template(
        """You are a first-time reader of a design-review document. Read ONLY this file: $doc.
Do not open any other file, do not search the web, do not call any API.

Answer these 10 questions from the document alone. For each: give your answer, quote the line(s) you
relied on, and rate your confidence (high/medium/low). If the document gives conflicting answers, say so
and quote both places. Also note, per question, how long it took you to find it (quick / had to search /
had to reconcile).

$questions

Finally, list up to 8 places in the document where you, as a reader, were confused or found two
statements that disagree (quote them). Mark each one BLOCKING if it would change a reader's
understanding of a verdict, count, severity, status, owner, decision or what changed; otherwise MINOR.
End with one line exactly in this form:
RESULT correct=<n>/10 reconciles=<n> confusions=<n> blocking=<n>
(correct = questions you could answer with high or medium confidence without finding a conflict.)"""
    ),
    result_keys=("correct", "reconciles", "confusions", "blocking"),
)

PERSONAS["fidelity"] = Persona(
    id="fidelity",
    description=(
        "Does the rendered document say what its sources say — nothing "
        "dropped, weakened, invented, or contradictory. Recomputes every "
        "number and opens cited code."
    ),
    template=Template(
        """You are auditing a generated design-review document for fidelity. Be adversarial and precise.
Do not edit any file; report only.

Target: $doc
Its source of truth: $source_of_truth
Inputs it was built from:
$inputs
Code clones (read-only): $repos_dir

Intended mapping from input IDs to doc IDs:
$id_mapping

Check and report:
1. Coverage: every input finding is represented; list any whose substance (not just ID) is missing or weakened.
   Check every merge: does the merged finding still carry each input's full scope (all actions, all
   endpoints, all roles)?
2. Status fidelity: each carried finding's status and severity match the closure check.
3. Meaning drift: for each open finding, do "Still wrong" and "Fix" say the same as the source(s)? Flag
   changed meaning, invented details, INFERRED items stated as fact. Where the doc deliberately differs
   from a source, say whether its choice is internally consistent and defensible.
4. Evidence: open at least 12 code citations (repo@commit file:line) in the clones; report match/mismatch
   with what you saw. Run a positive control before reporting anything as absent.
5. Numbers: recompute every number in the prose from the sources.
6. Internal contradictions: two findings, or a finding and a decision, that recommend incompatible things.
7. Anything the doc says is "kept unchanged", "retracted" or "disputed": verify against the source.

Output: numbered defects, each with severity (blocker / should-fix / nit), the exact doc text, the source text
or code line, and a one-line correction. Then a one-paragraph verdict. End with one line exactly:
RESULT blockers=<n> should_fix=<n> contradictions=<n> weakened=<n>
(weakened counts only should-fix or blocker items where substance was lost, not nits.)"""
    ),
    result_keys=("blockers", "should_fix", "contradictions", "weakened"),
)

PERSONAS["quote-context"] = Persona(
    id="quote-context",
    description=(
        "Adversarial quote and framing check: fair paraphrase, correct "
        "section references, counts that do not mix 'fixed now' with "
        "'closed earlier'."
    ),
    template=Template(
        """Adversarial quote and framing check. Do not edit files; report only.

Read $doc (a review of a blueprint) and the blueprint it reviews: $blueprint. Also available:
$prd, and the prototype $prototype (grep it, it is large).

1. For every open finding: is each paraphrase of the blueprint fair, and is each section reference
   the section where that text actually is? Flag overstatement and understatement.
2. Check every PRD claim (FR ids, section numbers, values) against the PRD and every prototype claim against
   the prototype.
3. For every row the doc marks fixed or closed: find the blueprint text that supports it. Flag any row where
   it does not, and any count that mixes "fixed in this version" with "closed earlier".
4. Decisions: each decision's question matches how the blueprint frames it.
5. Any sentence the blueprint's author would find unfair, ambiguous or wrong.

Output: numbered list: finding id, severity (blocker / should-fix / nit), doc text, evidence with line numbers,
corrected wording. Then a one-paragraph verdict. End with one line exactly:
RESULT blockers=<n> should_fix=<n> framing=<n>
(framing counts only should-fix or blocker items about framing, not nits.)"""
    ),
    result_keys=("blockers", "should_fix", "framing"),
)

PERSONAS["noise"] = Persona(
    id="noise",
    description=(
        "Reader-value audit: every sentence must help the reader who must "
        "apply, decide, or verify. Fresh runs do not converge to zero, so "
        "the gate takes the author's triage of the last run."
    ),
    template=Template(
        """You are the busiest reader of this document: the person who must apply it or decide on it, with 15 minutes.
Read ONLY $doc. Do not open other files. Do not edit; report only.

Flag every sentence, bullet or line that does not help that reader apply, decide or verify something:
1. Meta text: explains the document itself, its conventions, its history, who wrote it, how it was reviewed or audited.
2. Glossary or definition lines that a reader of the source documents would not need, or that exist only because of the doc's own naming.
3. Repetition: the same fact stated in two places (quote both); keep the one where the reader acts.
4. Hedging, self-justification or defensive text ("this is not a finding", "nothing here reopens...").
5. Any mention of AI tools, models, reviewers, agents, or a named person.
6. Detail that belongs in an appendix or backlog, not in the path of the decision.

For each: quote it, category (1-6), and the fix: DELETE, or the shorter replacement. Do not flag technical content that a builder needs.
End with one line exactly:
RESULT noise=<n> ai_or_person=<n>"""
    ),
    result_keys=("noise", "ai_or_person"),
)

PERSONAS["delta"] = Persona(
    id="delta",
    description=(
        "Post-round delta check: after a full round's findings are fixed, a "
        "fresh agent checks every changed line and nothing else. Enables the "
        "carry mechanism that stops full re-audits, which never converge."
    ),
    template=Template(
        """Adversarial delta check of a design-review document. Do not edit files; report only.

A review doc was audited in full. Its author then changed some lines. Your job: check every changed line, and nothing else.

Files:
- $delta_diff: unified diff from the audited version to the final version. Read this first.
- $final_doc: the final doc (for context around each change).
- Sources: $sources

For each changed line (each -/+ pair or + line in the diff):
1. Is the new text true against the sources? Open the source line or code file it relies on. Run a positive control before calling anything absent.
2. Does it contradict any other part of the final doc (search the final doc for the same finding IDs, decision IDs and terms)?
3. Does it state an inference as fact, overstate, or understate what the source says?
4. Did the change drop substance that the old line had?

Output: a numbered list per changed line: ID or section, verdict (OK / defect), and for a defect: severity (blocker / should-fix / nit), evidence with line numbers, corrected wording. Then one paragraph. End with one line exactly:
RESULT changed=<n> ok=<n> should_fix=<n> blockers=<n>"""
    ),
    result_keys=("changed", "ok", "should_fix", "blockers"),
)

_READER_ROLES: dict[str, str] = {
    "owner": (
        "the engineer who owns the blueprint — you must be able to apply "
        "every change in the pack without guessing the exact text, the "
        "place, or the reason"
    ),
    "product": (
        "Product — you must be able to answer every BUSINESS item from the "
        "doc alone: the question, the recommended default, the consequence "
        "of each option"
    ),
    "leadership": (
        "client engineering leadership — you must be able to decide whether "
        "the design can be frozen: what is still open, what it blocks, what "
        "it costs to leave it open"
    ),
}

PERSONAS["reader-roles"] = Persona(
    id="reader-roles",
    description=(
        "Trial persona (not a gate until calibrated): can each role that "
        "must ACT on the doc act without asking back. One fresh agent per "
        "role, run in parallel with the blind reader."
    ),
    template=Template(
        """You are $role_description and you have to act on this document today.
Read ONLY this file: $doc. Do not open any other file, do not search the web, do not call any API.

1. List every action the document asks of you. For each: quote the line, and say whether you could do it
   now (READY), need to ask someone first (ASK: whom and what), or cannot tell what is asked (UNCLEAR).
2. List anything you would need to act that is not in the document (MISSING), in one line each.
3. List anything addressed to you that belongs to another role (MISROUTED), with the quote.
End with one line exactly in this form:
RESULT role=<owner|product|leadership> actions=<n> ready=<n> ask=<n> unclear=<n> missing=<n> misrouted=<n>"""
    ),
    result_keys=("role", "actions", "ready", "ask", "unclear", "missing", "misrouted"),
)

PERSONAS["comment-audit"] = Persona(
    id="comment-audit",
    description=(
        "Audits an announcement comment (ticket/PR/issue) that points at a "
        "document: accuracy, omissions that change what the first reader "
        "does, tone, format."
    ),
    template=Template(
        """Audit a ticket comment that announces a document. Report only.
Files: $comment. Context: $context. The announced document: $doc.

Quote the exact comment text for each problem:
1. Accuracy: every statement is true of the document; flag absolute words ("every", "resolves", "each"), decisions stated as final that the doc makes conditional, and counts.
2. Omissions that change what the first reader does (where the doc overrides their own text, what is still open, who acts first).
3. Jargon the reader cannot decode without opening the doc.
4. Tone: criticism of the reader or of earlier rounds, self-praise, defensive text.
5. Format: matches the team's announcement format; has a notice with a link; no pasted content.
6. Any mention of AI tools, models, agents, or a named person.
Blockers: false or overstated statements, conditional-as-final, wrong order of who acts.
End with one line: RESULT problems=<n> blockers=<n>"""
    ),
    result_keys=("problems", "blockers"),
)


def render(persona_id: str, **kwargs: str) -> str:
    """Render a persona prompt, substituting its ``$placeholders``."""
    persona = PERSONAS[persona_id]
    return persona.template.safe_substitute(**kwargs)


def reader_role_description(role: str) -> str:
    """Human description of a reader-roles persona role."""
    return _READER_ROLES[role]


READER_ROLES: tuple[str, ...] = tuple(_READER_ROLES)
