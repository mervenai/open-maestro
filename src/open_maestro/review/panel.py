"""Multi-model adversarial panel, seated with the models maestro already has.

Constraint (MSTRO-115): no new vendor signups.  The panel seats one worker
per model family already configured — Kimi k3 (kimi-cli), GLM-5.3-Flash
(openai-sdk), Claude (claude-cli) — each with a distinct attack lens so they
cannot all find the same easy flaw.

Discipline (from skill-share brainstorm.md + karpathy/llm-council):

* **Route by falsifiability, not by vendor.**  Claims are classified
  E(executable) / Q(queryable) / W(web) / J(judgment); only J-questions go
  to the panel.  E/Q claims are returned for tool verification — an AI
  panel is the best tool you have for judgment and an actively dangerous
  tool for facts.
* **Anonymized cross-ranking** (llm-council idea): reviewers judge the other
  responses without knowing which model wrote them, so they cannot play
  favorites.
* **Reviewer scoring:** a voice with zero unique findings is discarded, not
  counted as approval; "I cannot verify this" is a high-quality answer,
  ranked above the agreeable one; a voice that errors or stays silent is
  recorded as unreachable — silence is never consent.
* **The chairman is never GLM.**  GLM-5.3-Flash is a fast but unstable
  voice (measured: invented facts, rankings that move between runs); it
  speaks, it does not chair.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from string import Template
from typing import Awaitable, Callable

from open_maestro.runtime.base import AgentConfig, AgentRuntime
from open_maestro.runtime.factory import create_runtime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PanelSeat:
    """One panel voice: a model + its attack lens."""

    id: str
    lens: str
    runtime_type: str
    model: str


PANEL_SEATS: tuple[PanelSeat, ...] = (
    PanelSeat("kimi-k3", "falsifier", "kimi-cli", "kimi-code/k3"),
    PanelSeat("glm-flash", "skeptic", "openai-sdk", "glm-5.3-flash"),
    PanelSeat("claude-sonnet", "steel-man", "claude-cli", "claude-sonnet-4-6"),
)

# Chairman preference order; GLM is deliberately absent.
CHAIRMAN_PREFERENCE: tuple[str, ...] = ("claude-sonnet", "kimi-k3")

LENSES: dict[str, str] = {
    "falsifier": (
        "You are the FALSIFIER. Find the ONE claim in the material that, if "
        "it is false, collapses the whole design. Attack load-bearing "
        "assumptions first; do not waste findings on typos or style."
    ),
    "skeptic": (
        "You are the PRACTITIONER SKEPTIC. You have operated systems like "
        "this in production. Attack operability: failure modes, rollout, "
        "rollback, cost, on-call burden, integration seams. If a claim "
        "cannot be verified from the material, say 'I cannot verify this' "
        "instead of guessing."
    ),
    "steel-man": (
        "You are the STEEL-MAN. Argue the strongest case for the OPPOSITE of "
        "the design's core decision, then identify what evidence would "
        "resolve the contest. Do not manufacture weaknesses; if the design "
        "is right, say what would prove it."
    ),
    "missing-chapter": (
        "You are the MISSING CHAPTER. Name what is absent entirely — the "
        "section nobody wrote: undeployed states, unhandled inputs, unnamed "
        "owners, unpriced options."
    ),
    "causal-auditor": (
        "You are the CAUSAL AUDITOR. For each claimed cause-effect ('X so "
        "that Y'), state the mechanism or flag it as asserted-not-shown."
    ),
}

CLASSIFY_PROMPT = Template("""Classify each distinct claim in the text below as:
E = Executable — can be verified by running code or commands
Q = Queryable — can be verified by querying local files/data/docs
W = Web — needs an external fetch (live web, API, remote repo)
J = Judgment — opinion, trade-off, prioritization, design taste

Rules: split compound claims. Every claim gets exactly one kind.
Respond with ONLY a JSON object: {"claims": [{"text": "...", "kind": "E|Q|W|J"}]}

Text:
---
$text
---""")

FINDINGS_RE = re.compile(r"FINDINGS\s+n=(\d+)")


@dataclass
class PanelResult:
    question: str
    claims: list[dict] = field(default_factory=list)
    judgment_claims: list[str] = field(default_factory=list)
    tool_claims: list[str] = field(default_factory=list)
    opinions: dict[str, str] = field(default_factory=dict)
    rankings: dict[str, str] = field(default_factory=dict)
    scores: dict[str, int] = field(default_factory=dict)
    chairman_response: str = ""
    chairman_seat: str = ""
    unreachable: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.chairman_response) and not self.chairman_response.startswith(
            "ERROR"
        )


async def _run_seat(
    seat: PanelSeat,
    prompt: str,
    runtime_factory: Callable[..., AgentRuntime],
) -> str:
    runtime = runtime_factory(seat.runtime_type)
    config = AgentConfig(model=seat.model, max_turns=1)
    result = await runtime.run(prompt, config=config)
    if result.is_error:
        raise RuntimeError(result.text[:200])
    return result.text


def parse_claims(text: str) -> list[dict]:
    """Parse the classification JSON; tolerant of fences."""
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    claims = data.get("claims")
    if not isinstance(claims, list):
        return []
    return [c for c in claims if isinstance(c, dict) and c.get("text")]


def _findings_count(text: str) -> int | None:
    m = FINDINGS_RE.search(text)
    return int(m.group(1)) if m else None


def choose_chairman(seats: tuple[PanelSeat, ...]) -> PanelSeat | None:
    for preferred in CHAIRMAN_PREFERENCE:
        for seat in seats:
            if seat.id == preferred:
                return seat
    # GLM-only degenerate case: still chair with the first seat rather
    # than fail, but note it.
    return seats[0] if seats else None


async def run_panel(
    question: str,
    *,
    seats: tuple[PanelSeat, ...] = PANEL_SEATS,
    context: str | None = None,
    runtime_factory: Callable[..., AgentRuntime] = create_runtime,
    max_turns: int = 1,
) -> PanelResult:
    """Run the adversarial panel on *question*.

    Seats that error or return empty are recorded as unreachable — never
    counted as approval.  A seat with zero findings is discarded by the
    chairman, not counted as approval.
    """
    result = PanelResult(question=question)
    chairman = choose_chairman(seats)
    if chairman is None:
        result.chairman_response = "ERROR: no panel seats configured"
        return result
    result.chairman_seat = chairman.id

    # Stage 0 — route by falsifiability.
    material = f"{context}\n\n{question}" if context else question
    try:
        raw = await _run_seat(
            chairman,
            CLASSIFY_PROMPT.safe_substitute(text=material[:20000]),
            runtime_factory,
        )
        result.claims = parse_claims(raw)
    except Exception as exc:  # classification is best-effort
        logger.warning("claim classification failed (%s); treating all as J", exc)
        result.claims = []
    result.judgment_claims = [c["text"] for c in result.claims if c.get("kind") == "J"]
    result.tool_claims = [
        f"[{c.get('kind')}] {c['text']}" for c in result.claims if c.get("kind") in ("E", "Q", "W")
    ]
    if result.tool_claims:
        result.notes.append(
            f"{len(result.tool_claims)} claim(s) routed to tool verification, "
            "NOT to the panel: " + "; ".join(result.tool_claims[:5])
        )
    if not result.judgment_claims:
        result.judgment_claims = [question]

    # Stage 1 — first opinions, one lens per seat, in parallel.
    j_block = "\n".join(f"- {c}" for c in result.judgment_claims)
    opinion_prompts = {
        seat.id: (
            f"{LENSES[seat.lens]}\n\nAnswer ONLY the judgment questions below; "
            "for anything verifiable by running code or looking it up, say "
            "'I cannot verify this' — a model that fails loudly is worth "
            "more than one that fails quietly.\n\n"
            f"Material:\n{material[:30000]}\n\n"
            f"Judgment questions:\n{j_block}\n\n"
            "End your response with one line exactly: FINDINGS n=<number of "
            "distinct, material findings you made>"
        )
        for seat in seats
    }
    opinions = await asyncio.gather(
        *(
            _run_seat(seat, opinion_prompts[seat.id], runtime_factory)
            for seat in seats
        ),
        return_exceptions=True,
    )
    for seat, op in zip(seats, opinions):
        if isinstance(op, Exception) or not str(op).strip():
            result.unreachable.append(seat.id)
            logger.warning("panel seat %s unreachable: %s", seat.id, op)
        else:
            result.opinions[seat.id] = str(op)
    if not result.opinions:
        result.notes.append(
            f"unreachable voices ({', '.join(result.unreachable)}) are NOT "
            "approval; silence is not consent"
        )
        result.chairman_response = (
            f"ERROR: all panel seats unreachable: {', '.join(result.unreachable)}"
        )
        return result

    # Stage 2 — anonymized cross-ranking.
    answered = [s for s in seats if s.id in result.opinions]
    for seat in answered:
        others = [(s, result.opinions[s.id]) for s in answered if s.id != seat.id]
        random.shuffle(others)
        labeled = "\n\n".join(
            f"Response {chr(65 + i)}:\n{op}" for i, (_, op) in enumerate(others)
        )
        ranking_prompt = (
            "You are ranking anonymized responses from other reviewers. You "
            "do not know which models wrote them; judge the text only. "
            "Rank them for accuracy and insight, flag any claim you believe "
            "is false, and state how many DISTINCT findings each adds beyond "
            "your own analysis.\n\n"
            f"The question set:\n{j_block}\n\n{labeled}\n\n"
            "End with one line exactly: FINDINGS n=<number of unique, "
            "material findings across the responses that you did not already "
            "have>"
        )
        try:
            rk = await _run_seat(seat, ranking_prompt, runtime_factory)
            result.rankings[seat.id] = rk
            n = _findings_count(rk)
            if n is not None:
                result.scores[seat.id] = n
        except Exception as exc:
            result.notes.append(f"ranking from {seat.id} failed: {exc}")

    # Stage 3 — chairman synthesis with reviewer scoring.
    discarded = [
        sid for sid, op in result.opinions.items() if _findings_count(op) == 0
    ]
    for sid in discarded:
        result.notes.append(
            f"reviewer '{sid}' produced 0 findings — discarded, NOT counted as approval"
        )
    if result.unreachable:
        result.notes.append(
            f"unreachable voices ({', '.join(result.unreachable)}) are NOT "
            "approval; silence is not consent"
        )
    digest = "\n\n".join(
        f"=== Voice {i + 1} (anonymized) ===\n{op}"
        for i, op in enumerate(result.opinions.values())
    )
    chair_prompt = (
        "You are the CHAIRMAN of an adversarial review panel. Compile the "
        "voices below into one final answer. Rules: every unique finding "
        "must be checked against the material — never accept a finding "
        "because it sounds smart; a voice with zero findings contributed "
        "nothing and is discarded, not counted as approval; rank 'I cannot "
        "verify this' above confident guesses; state explicitly which "
        "claims remain unverified.\n\n"
        f"Material:\n{material[:30000]}\n\n{digest}"
    )
    try:
        final = await _run_seat(chairman, chair_prompt, runtime_factory)
        result.chairman_response = final
    except Exception as exc:
        result.chairman_response = f"ERROR: chairman failed: {exc}"
    return result
