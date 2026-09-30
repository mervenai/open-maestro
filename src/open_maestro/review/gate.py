"""Review gate: an artifact passes only if every required audit passed on
THIS exact version.

Ported from the skill-share ``gate.py`` discipline:

* Every audit report ends with a machine-parseable ``RESULT k=v ...`` line
  (see ``review.personas``); the gate parses it and enforces numeric
  thresholds — a gate is never passed on vibes.
* Records are keyed by the sha256 of the artifact, so any edit after an
  audit makes that audit stale and forces re-runs.  Records append only.
* Full fresh re-audits never converge (measured: 25→17→13 new noise items
  per run), so after a full round the findings are fixed and a *delta*
  persona checks only the changed lines; ``carry`` moves the old records to
  the fixed version when the delta check is clean.

What this does not stop: the agent that wrote the artifact can also write a
false report.  The gate stops skipped and stale audits; honesty rests on
fresh agents and fixed prompts.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

RESULT_RE = re.compile(r"^RESULT\s+(.+)$", flags=re.M)

# The only copy of the pass thresholds.  Measured rationale (skill-share
# round 3): first render 10/10 correct but 8 confusions (fails blind-reader);
# fidelity 3 contradictions, 2 weakened merges (fails).  confusions is
# reported but not gated — it calibrates the quiz, it does not block.
THRESHOLDS: dict[str, dict[str, tuple[str, int]]] = {
    "blind-reader": {
        "correct": ("==", 10),
        "reconciles": ("<=", 2),
        "blocking": ("==", 0),
    },
    "fidelity": {
        "blockers": ("==", 0),
        "contradictions": ("==", 0),
        "weakened": ("==", 0),
    },
    "quote-context": {"blockers": ("==", 0), "framing": ("==", 0)},
    # noise takes the author's triage of the last run: every item fixed or
    # kept with a reason (noise_open=0).
    "noise": {"noise_open": ("==", 0), "ai_or_person": ("==", 0)},
    # panel: voices = answered + recorded-unreachable; a silent voice is
    # recorded as unreachable, never counted as approval.
    "panel": {"voices": (">=", 3), "blockers_open": ("==", 0)},
    "comment-audit": {"blockers": ("==", 0)},
}

PROFILES: dict[str, list[str]] = {
    "review": ["blind-reader", "fidelity", "quote-context", "noise", "panel"],
    "brief": ["blind-reader", "fidelity", "noise"],
}

OPS = {"==": lambda a, b: a == b, "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b}

# Carry: which zero-count key each gate must show on the old version before
# its record may move to the fixed successor.
CARRY: dict[str, str] = {
    "blind-reader": "blocking",
    "fidelity": "blockers",
    "quote-context": "blockers",
}


class GateError(Exception):
    """A refused gate operation (bad report, missing record, ...)."""


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def parse_result(text: str) -> dict[str, int] | None:
    """Extract the counts from the last ``RESULT k=v ...`` line, or None."""
    matches = RESULT_RE.findall(text)
    if not matches:
        return None
    out: dict[str, int] = {}
    for k, v in re.findall(r"(\w+)=(\d+)(?:/\d+)?", matches[-1]):
        out[k] = int(v)
    return out


def verdict(
    gate: str, counts: dict[str, int]
) -> tuple[str, list[str]]:
    """Return (pass|fail, list of failed threshold descriptions)."""
    fails = [
        f"{k}={counts.get(k)} (needs {op} {v})"
        for k, (op, v) in THRESHOLDS[gate].items()
        if counts.get(k) is None or not OPS[op](counts[k], v)
    ]
    return ("pass" if not fails else "fail"), fails


def default_store(project_path: str | Path) -> Path:
    return Path(project_path) / ".open-maestro" / "gates"


class GateLedger:
    """Append-only audit records keyed by artifact sha256."""

    def __init__(self, store_dir: str | Path | None = None):
        self.store = Path(store_dir) if store_dir else None

    def for_project(self, project_path: str | Path) -> "GateLedger":
        return GateLedger(default_store(project_path))

    def _records(self, h: str) -> list[dict]:
        if self.store is None:
            raise GateError("GateLedger has no store directory")
        try:
            return json.loads((self.store / f"{h}.json").read_text())
        except (OSError, ValueError):
            return []

    def _append(self, h: str, rec: dict) -> None:
        assert self.store is not None
        self.store.mkdir(parents=True, exist_ok=True)
        records = self._records(h) + [rec]
        (self.store / f"{h}.json").write_text(json.dumps(records, indent=1))

    def record(
        self, doc: str | Path, gate: str, report_text: str
    ) -> tuple[str, list[str]]:
        """Record an audit from its RESULT line. Returns (verdict, fails)."""
        if gate not in THRESHOLDS:
            raise GateError(
                f"unknown gate {gate!r}; agent/panel gates: "
                + " ".join(sorted(THRESHOLDS))
            )
        counts = parse_result(report_text)
        if counts is None:
            raise GateError(
                f"report has no 'RESULT k=v ...' line (see persona {gate!r})"
            )
        v, fails = verdict(gate, counts)
        self._append(
            sha(doc),
            {
                "gate": gate,
                "result": v,
                "counts": counts,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        logger.info("gate %s on %s: %s", gate, sha(doc), v)
        return v, fails

    def record_counts(
        self, doc: str | Path, gate: str, counts: dict[str, int]
    ) -> tuple[str, list[str]]:
        """Record directly from pre-parsed counts (e.g. from the panel)."""
        if gate not in THRESHOLDS:
            raise GateError(f"unknown gate {gate!r}")
        v, fails = verdict(gate, counts)
        self._append(
            sha(doc),
            {
                "gate": gate,
                "result": v,
                "counts": counts,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
        )
        return v, fails

    def check(self, doc: str | Path, profile: str = "review") -> list[str]:
        """Return problems; empty means the artifact may pass the gate."""
        h = sha(doc)
        latest: dict[str, dict] = {}
        for rec in self._records(h):
            latest[rec["gate"]] = rec
        probs: list[str] = []
        for gate in PROFILES[profile]:
            rec = latest.get(gate)
            if rec is None:
                probs.append(f"{gate}: not run on this version ({h})")
            elif rec["result"] != "pass":
                probs.append(f"{gate}: failed {rec['counts']}")
        return probs

    def status(self, doc: str | Path) -> str:
        h = sha(doc)
        lines = [f"{doc} sha {h}"]
        for rec in self._records(h):
            lines.append(f"  {rec['at']} {rec['gate']:<14} {rec['result']:<5} {rec['counts']}")
        return "\n".join(lines)

    def carry(
        self,
        old: str | Path,
        new: str | Path,
        delta_counts: dict[str, int],
    ) -> list[str]:
        """Move full-round records to the fixed successor after a clean delta."""
        d = delta_counts
        if (
            not d.get("changed")
            or d.get("blockers") != 0
            or d.get("should_fix") != 0
        ):
            raise GateError(
                "delta needs changed=<n> with should_fix=0 blockers=0, "
                f"got {d}"
            )
        old_sha = sha(old)
        latest = {r["gate"]: r for r in self._records(old_sha)}
        for gate, key in CARRY.items():
            rec = latest.get(gate)
            if rec is None or rec["counts"].get(key) != 0:
                raise GateError(
                    f"{gate} on {old_sha} is missing or has {key} != 0"
                )
            if gate == "blind-reader" and rec["result"] != "pass":
                raise GateError("blind-reader did not pass on the old version")
        moved = []
        for gate in CARRY:
            self._append(
                sha(new),
                {
                    "gate": gate,
                    "result": "pass",
                    "counts": latest[gate]["counts"],
                    "carried_from": old_sha,
                    "delta": d,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
            )
            moved.append(gate)
        logger.info("carried %s from %s to %s", moved, old_sha, sha(new))
        return moved
