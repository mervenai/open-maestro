"""Awaiting-input handshake: agents flag questions; the REPL asks the lead.

When a delegated turn ends with the agent asking the project lead questions
(bundle kickoffs, contract rulings), the agent writes
``.open-maestro/awaiting-input.yaml`` alongside its other project state. The
interactive REPL picks the marker up after the turn, shows the questions
compactly, collects rulings one-by-one or in bulk, and feeds them back into
the same backend session — so a wall of agent output never buries the ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MARKER_NAME = "awaiting-input.yaml"

# A marker written just before the turn result arrives can look slightly
# older than the turn-start timestamp we compare against; tolerate the skew.
_STALE_SLACK_SECONDS = 1.0

#: Sentinel answer meaning "the lead declined to rule on this question". The
#: agent files these as Open register rows + outbox messages instead.
DEFER = "__defer__"


def marker_path(project_dir: Path) -> Path:
    return project_dir / ".open-maestro" / MARKER_NAME


@dataclass
class AwaitedQuestion:
    id: str
    audience: str = "lead"
    question: str = ""
    recommendation: str = ""
    blocks: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class AwaitingInputBatch:
    source: str = ""
    created_at: str = ""
    questions: list[AwaitedQuestion] = field(default_factory=list)


def load(project_dir: Path, since: float | None = None) -> AwaitingInputBatch | None:
    """Return the pending question batch if the marker exists and is fresh.

    ``since`` is the epoch second the current turn started: a marker older
    than that is a leftover from a killed session, not a question aimed at
    the turn that just finished. Malformed markers return None — the agent's
    human-readable response still carries the questions, so a bad marker
    degrades to the old behavior instead of crashing the REPL.
    """
    path = marker_path(project_dir)
    if not path.exists():
        return None
    if since is not None and path.stat().st_mtime < since - _STALE_SLACK_SECONDS:
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    questions: list[AwaitedQuestion] = []
    for item in raw.get("questions") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id", "")).strip()
        if not qid:
            continue
        options = item.get("options") or []
        if not isinstance(options, list):
            options = [options]
        questions.append(
            AwaitedQuestion(
                id=qid,
                audience=str(item.get("audience") or "lead"),
                question=str(item.get("question") or ""),
                recommendation=str(item.get("recommendation") or ""),
                blocks=str(item.get("blocks") or ""),
                options=[str(o) for o in options],
            )
        )
    if not questions:
        return None
    return AwaitingInputBatch(
        source=str(raw.get("source") or ""),
        created_at=str(raw.get("created_at") or ""),
        questions=questions,
    )


def clear(project_dir: Path) -> None:
    try:
        marker_path(project_dir).unlink(missing_ok=True)
    except OSError:
        pass


_BULK_ENTRY_RE = re.compile(r"^\s*(?:Q|#)?(\d+)\s*[:=.]\s*(.+?)\s*$", re.IGNORECASE)

#: Answers typed as these words mean DEFER, in both per-question and bulk mode.
_DEFER_WORDS = {"defer", "deferred", "skip", "skipped", "none", "-"}


def normalize_answer(text: str) -> str:
    """Map a typed answer to its stored form; defer-words become DEFER."""
    stripped = text.strip()
    if stripped.lower() in _DEFER_WORDS:
        return DEFER
    return stripped


def parse_bulk_answers(text: str) -> tuple[dict[int, str], list[str]]:
    """Parse a bulk ruling block into ({question_number: answer}, unparsed).

    Accepted entries: ``Q1: answer``, ``1. answer``, ``2=answer`` — one per
    line, or several separated by semicolons. Lines that match nothing are
    returned unparsed so the caller can show them back instead of silently
    dropping a ruling.
    """
    answers: dict[int, str] = {}
    unparsed: list[str] = []
    for line in text.replace(";", "\n").splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        match = _BULK_ENTRY_RE.match(line)
        if match:
            answers[int(match.group(1))] = normalize_answer(match.group(2))
        else:
            unparsed.append(line)
    return answers, unparsed


def format_answers_message(batch: AwaitingInputBatch, answers: dict[str, str]) -> str:
    """Build the user-turn message carrying the lead's rulings back to the agent.

    Self-contained by design: each ruling repeats the question text, so the
    message still anchors the right row when a resumed session has drifted.
    """
    header = "Here are the lead's rulings on the questions you asked"
    if batch.source:
        header += f" ({batch.source})"
    lines = [header + ":", ""]
    for question in batch.questions:
        answer = answers.get(question.id, DEFER)
        label = f"{question.id} [{question.audience}] {question.question}".strip()
        if answer == DEFER:
            lines.append(
                f"{label} → DECLINED to rule — file this as an Open register row "
                "plus an outbox message per the kickoff protocol."
            )
        else:
            suffix = (
                " (provisional — record with [INTERNAL — not M3-ratified])"
                if question.audience.lower().startswith("client")
                else ""
            )
            lines.append(f"{label} → {answer}{suffix}")
    lines += [
        "",
        "Record each ruled question as a Decided register row now; declined "
        "questions become Open rows plus outbox messages.",
    ]
    return "\n".join(lines)
