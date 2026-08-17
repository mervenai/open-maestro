"""YAML-backed persistence for milestone plans."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from open_maestro.milestones.models import MilestonePlan
from open_maestro.milestones.templates import default_software_template

logger = logging.getLogger(__name__)


def _normalize_project_id(name: str) -> str:
    """Convert a folder name to a valid milestone project_id."""
    normalized = name.lower()
    # Replace spaces and dots with hyphens; keep alphanumerics, hyphens, underscores.
    import re

    normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    return normalized or "project"


class MilestoneStore:
    """Load and save milestone plans to ``.open-maestro/milestones.yaml``."""

    FILENAME = "milestones.yaml"

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path)
        self.open_maestro_dir = self.project_path / ".open-maestro"
        self.file_path = self.open_maestro_dir / self.FILENAME

    def exists(self) -> bool:
        """Return True if a milestone plan already exists for this project."""
        return self.file_path.exists()

    def load(self) -> MilestonePlan:
        """Load the milestone plan from disk.

        If no plan exists, initialize one from the default software template.
        """
        if not self.exists():
            logger.info("No milestone plan found; creating default template at %s", self.file_path)
            plan = default_software_template(
                project_id=_normalize_project_id(self.project_path.name),
                project_path=str(self.project_path),
            )
            self.save(plan)
            return plan

        try:
            raw = yaml.safe_load(self.file_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            raise RuntimeError(f"Failed to load milestone plan from {self.file_path}: {exc}") from exc

        schema_version = str(raw.get("schema_version", "1.0"))
        if schema_version != "2.0" or "milestones" in raw:
            raise RuntimeError(
                f"Old milestone schema ({schema_version}) detected in {self.file_path}. "
                "Run `maestro --sync-milestones` to regenerate from Merven, "
                "or delete the file to create a fresh schema v2.0 plan."
            )

        return MilestonePlan(**raw)

    def save(self, plan: MilestonePlan) -> None:
        """Save the milestone plan to disk."""
        self.open_maestro_dir.mkdir(parents=True, exist_ok=True)
        plan.last_updated = datetime.now()
        plan.schema_version = "2.0"
        data = plan.to_dict()
        try:
            self.file_path.write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to save milestone plan to {self.file_path}: {exc}") from exc

    def update(self, plan: MilestonePlan) -> None:
        """Recompute summary and save."""
        plan._recompute_summary()  # noqa: SLF001
        self.save(plan)

    def export_dashboard(self, plan: MilestonePlan) -> dict[str, Any]:
        """Return a client-safe dashboard projection."""
        from open_maestro.milestones.dashboard import _milestone_summary

        return {
            "project_id": plan.project_id,
            "project_name": plan.project_name or plan.project_id.replace("-", " ").title(),
            "overall_completion": plan.summary.overall_completion,
            "current_milestone": plan.summary.current_milestone_ids,
            "active_blockers": [
                {
                    "epic": b.epic_id,
                    "milestone": b.milestone_id,
                    "description": b.description,
                }
                for b in plan.summary.active_blockers
            ],
            "epics": [
                {
                    "id": epic.id,
                    "name": epic.name,
                    "completion": epic.completion(),
                    "milestones": [
                        {
                            "id": m.id,
                            "name": m.name,
                            "status": m.status.value,
                            "completion": m.completion(),
                            "summary": _milestone_summary(m),
                        }
                        for m in sorted(epic.milestones, key=lambda x: x.order)
                    ],
                }
                for epic in sorted(plan.epics, key=lambda x: x.order)
            ],
        }
