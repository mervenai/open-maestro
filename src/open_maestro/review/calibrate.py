"""Calibration harness for the review personas.

Core lesson from the skill-share (Calibration section): *the gate assumes
the audit prompts catch what matters; nobody has measured their recall.*
Until you inject known defects and count what the personas catch, "the
audits passed" is an unmeasured claim.

Mechanics: each defect type plants a sentence carrying a unique sentinel
token into a copy of a real document; a persona is credited with catching
the defect when its report quotes or mentions that sentinel.  Recall is
therefore deterministic to score.  Any defect class that is missed needs a
prompt change or a deterministic code check — never a shrug.

CLI:

    python -m open_maestro.review.calibrate <doc.md> --persona fidelity \\
        [--persona blind-reader] [--out calibration-results.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from open_maestro.review.personas import render
from open_maestro.runtime.base import AgentConfig

logger = logging.getLogger(__name__)


def _sentinel() -> str:
    return "clbrx-" + "".join(random.choices(string.ascii_lowercase, k=6))


@dataclass(frozen=True)
class DefectType:
    """One planted-defect class.  ``plant`` returns the seeded text plus the
    sentinel token embedded in it, or None if this doc cannot host it."""

    id: str
    description: str
    plant: Callable[[str], tuple[str, str] | None]

    def detect(self, report_text: str, sentinel: str) -> bool:
        return sentinel in report_text


def _after_first_heading(text: str, insertion: str) -> str:
    m = re.search(r"^#.*$", text, flags=re.M)
    at = m.end() if m else 0
    return text[:at] + "\n\n" + insertion + text[at:]


def _plant_count(text: str) -> tuple[str, str] | None:
    m = re.search(r"\b(\d+)\s+(findings|items|tasks|stories|risks|questions)\b", text)
    sent = _sentinel()
    if m:
        new = text[: m.end()] + f" ({sent}, all confirmed this round)" + text[m.end() :]
        return new, sent
    return _after_first_heading(
        text, f"Summary: 3 findings ({sent}), all confirmed this round."
    ), sent


def _plant_invented(text: str) -> tuple[str, str]:
    sent = _sentinel()
    return (
        _after_first_heading(text, f"- `{sent}_mode` (required enum: on | off)"),
        sent,
    )


def _plant_overstatement(text: str) -> tuple[str, str]:
    sent = _sentinel()
    for word in ("recommended", "suggested", "proposed", "preferred"):
        if re.search(rf"\b{word}\b", text, flags=re.I):
            return (
                re.sub(
                    rf"\b{word}\b",
                    f"required ({sent})",
                    text,
                    count=1,
                    flags=re.I,
                ),
                sent,
            )
    return (
        _after_first_heading(text, f"Note: this design is required ({sent}), not advisory."),
        sent,
    )


def _plant_contradiction(text: str) -> tuple[str, str]:
    sent = _sentinel()
    pair = (
        f"Decision {sent}: the write path must go through the queue.\n\n"
        f"Decision {sent} (amendment): the write path must bypass the queue for latency."
    )
    return _after_first_heading(text, pair), sent


def _plant_status_flip(text: str) -> tuple[str, str]:
    sent = _sentinel()
    return (
        _after_first_heading(
            text,
            f"Register status ({sent}): every finding listed below is CLOSED and verified.",
        ),
        sent,
    )


def _plant_wrong_ref(text: str) -> tuple[str, str] | None:
    sent = _sentinel()
    m = re.search(r"§\d+(?:\.\d+)?", text)
    if m:
        return (
            text[: m.start()] + f"§9.9 ({sent})" + text[m.end() :],
            sent,
        )
    return _after_first_heading(
        text, f"See §9.9 ({sent}) for the security model."
    ), sent


DEFECT_TYPES: tuple[DefectType, ...] = (
    DefectType(
        "count-framing",
        "A count is quietly scoped so it overstates progress ('N findings (token), all confirmed').",
        _plant_count,
    ),
    DefectType(
        "invented-detail",
        "An unsupported named detail (required field) stated as fact.",
        _plant_invented,
    ),
    DefectType(
        "overstatement",
        "A recommendation stated as a hard requirement.",
        _plant_overstatement,
    ),
    DefectType(
        "contradiction",
        "Two statements recommend incompatible things.",
        _plant_contradiction,
    ),
    DefectType(
        "status-flip",
        "Open findings described as closed/verified.",
        _plant_status_flip,
    ),
    DefectType(
        "wrong-section-ref",
        "A section reference pointing at the wrong place.",
        _plant_wrong_ref,
    ),
)


@dataclass
class CalibrationResult:
    doc: str
    persona: str
    recall: dict[str, bool] = field(default_factory=dict)
    planted: dict[str, str] = field(default_factory=dict)

    @property
    def recall_rate(self) -> float:
        return (
            sum(1 for v in self.recall.values() if v) / len(self.recall)
            if self.recall
            else 0.0
        )

    def to_dict(self) -> dict:
        return {
            "doc": self.doc,
            "persona": self.persona,
            "recall": self.recall,
            "planted": self.planted,
            "recall_rate": round(self.recall_rate, 3),
        }


def inject_defects(
    text: str, defect_types: tuple[DefectType, ...] = DEFECT_TYPES
) -> tuple[str, dict[str, str]]:
    """Plant one defect of each plantable type.

    Returns (seeded_text, {defect_id: sentinel}).  Defects plant after the
    first heading in order, so sentinels don't disturb each other.
    """
    planted: dict[str, str] = {}
    for defect in defect_types:
        out = defect.plant(text)
        if out is None:
            continue
        new_text, sent = out
        if new_text != text and sent not in text:
            text = new_text
            planted[defect.id] = sent
    return text, planted


async def calibrate_persona(
    doc: str | Path,
    persona: str,
    *,
    runtime_factory: Callable | None = None,
    defect_types: tuple[DefectType, ...] = DEFECT_TYPES,
    project_path: str | Path | None = None,
) -> CalibrationResult:
    """Run one persona against a defect-seeded copy of *doc*."""
    from open_maestro.review.blueprint import _default_persona_kwargs
    from open_maestro.runtime.factory import create_runtime

    doc = Path(doc)
    seeded, planted = inject_defects(doc.read_text(), defect_types)
    seeded_path = doc.with_suffix(f".calibration-{persona}.md")
    seeded_path.write_text(seeded)
    try:
        kwargs = _default_persona_kwargs(seeded_path, Path(project_path or doc.parent))
        prompt = render(persona, **kwargs)
        factory = runtime_factory or create_runtime
        runtime = factory(None)
        out = await runtime.run(prompt, config=AgentConfig(max_turns=1))
        report = "" if out.is_error else out.text
        if out.is_error:
            logger.warning("persona %s errored during calibration", persona)
    finally:
        seeded_path.unlink(missing_ok=True)
    result = CalibrationResult(doc=str(doc), persona=persona)
    by_id = {d.id: d for d in defect_types}
    for defect_id, sentinel in planted.items():
        result.planted[defect_id] = sentinel
        result.recall[defect_id] = by_id[defect_id].detect(report, sentinel)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate review personas against known planted defects."
    )
    parser.add_argument("doc", help="artifact to seed with defects")
    parser.add_argument(
        "--persona",
        action="append",
        dest="personas",
        help="persona to calibrate (repeatable); default: fidelity",
    )
    parser.add_argument("--out", help="write JSON results here")
    parser.add_argument("--project", help="project root for persona context")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    personas = args.personas or ["fidelity"]

    async def _run() -> list[dict]:
        results = []
        for persona in personas:
            r = await calibrate_persona(
                args.doc, persona, project_path=args.project
            )
            results.append(r.to_dict())
            print(
                f"{persona}: recall {r.recall_rate:.0%} "
                f"({sum(r.recall.values())}/{len(r.recall)} planted defects caught)"
            )
            missed = [k for k, v in r.recall.items() if not v]
            if missed:
                print(
                    "  MISSED -> prompt change or code check needed: "
                    + ", ".join(missed)
                )
        return results

    results = asyncio.run(_run())
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1))
        print(f"results: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
