"""Milestone-guided project lifecycle for Open Maestro."""

from open_maestro.milestones.models import (
    Artifact,
    Blocker,
    Epic,
    Milestone,
    MilestonePlan,
    MilestoneStatus,
    Summary,
)
from open_maestro.milestones.commands import (
    format_prompt_context,
    get_current_or_next_milestone_prompts,
    handle_blocker_command,
    handle_complete_command,
    handle_next_command,
    handle_prompts_command,
    handle_track_command,
)
from open_maestro.milestones.dashboard import (
    export_dashboard_html,
    export_dashboard_json,
    export_dashboard_markdown,
)
from open_maestro.milestones.detector import (
    DetectionSuggestion,
    MilestoneDetector,
    summarize_suggestions,
)
from open_maestro.milestones.merven_sync import MervenSyncError, sync_from_merven
from open_maestro.milestones.playbook import get_prompts_for_milestone
from open_maestro.milestones.publisher import DashboardPublisher, PublishError
from open_maestro.milestones.server import serve_dashboard, stop_dashboard_server
from open_maestro.milestones.store import MilestoneStore
from open_maestro.milestones.templates import (
    default_software_template,
    software_template_with_epics,
)

__all__ = [
    "Artifact",
    "Blocker",
    "Epic",
    "Milestone",
    "MilestonePlan",
    "MilestoneStatus",
    "Summary",
    "MilestoneStore",
    "MilestoneDetector",
    "DetectionSuggestion",
    "summarize_suggestions",
    "sync_from_merven",
    "MervenSyncError",
    "handle_next_command",
    "handle_complete_command",
    "handle_blocker_command",
    "handle_track_command",
    "handle_prompts_command",
    "get_current_or_next_milestone_prompts",
    "get_prompts_for_milestone",
    "format_prompt_context",
    "export_dashboard_json",
    "export_dashboard_markdown",
    "export_dashboard_html",
    "DashboardPublisher",
    "PublishError",
    "serve_dashboard",
    "stop_dashboard_server",
    "default_software_template",
    "software_template_with_epics",
]
