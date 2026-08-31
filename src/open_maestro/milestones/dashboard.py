"""Client-facing dashboard rendering for milestone progress."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from open_maestro.milestones.models import Epic, Milestone, MilestonePlan, MilestoneStatus


# Merven.ai design tokens extracted from https://merven.ai
_MERVEN_COLORS = {
    "background": "#02040b",
    "foreground": "#ebeff5",
    "card": "#070b14",
    "card_gradient_start": "#070d18",
    "card_gradient_end": "#04070f",
    "primary": "#00bfaf",
    "primary_foreground": "#02040b",
    "muted": "#12161d",
    "muted_foreground": "#79818d",
    "accent": "#141b26",
    "border": "#1d2229",
    "shadow": "0 4px 24px -4px rgba(0,0,0,0.4)",
}


def _status_color(status: str) -> str:
    """Return a status color matching the Merven palette."""
    return {
        MilestoneStatus.COMPLETED.value: "#00bfaf",
        MilestoneStatus.IN_PROGRESS.value: "#3b82f6",
        MilestoneStatus.BLOCKED.value: "#ef4444",
        MilestoneStatus.SKIPPED.value: "#79818d",
    }.get(status, "#79818d")


def _status_bg(status: str) -> str:
    """Return a translucent background color for status badges/bars."""
    return {
        MilestoneStatus.COMPLETED.value: "rgba(0, 191, 175, 0.15)",
        MilestoneStatus.IN_PROGRESS.value: "rgba(59, 130, 246, 0.15)",
        MilestoneStatus.BLOCKED.value: "rgba(239, 68, 68, 0.15)",
        MilestoneStatus.SKIPPED.value: "rgba(121, 129, 141, 0.15)",
    }.get(status, "rgba(121, 129, 141, 0.15)")


def export_dashboard_json_from_data(data: dict[str, Any]) -> str:
    """Return a client-safe JSON dashboard string from pre-built data."""
    return json.dumps(data, indent=2, default=_json_serializer)


def export_dashboard_json(plan: MilestonePlan) -> str:
    """Return a client-safe JSON dashboard string from a milestone plan."""
    return export_dashboard_json_from_data(_dashboard_data(plan))


def export_dashboard_markdown_from_data(data: dict[str, Any]) -> str:
    """Return a client-ready Markdown dashboard string from pre-built data."""
    lines = [
        f"# {data['project_name']} — Project Dashboard",
        "",
        f"**Overall completion:** {data['overall_completion']}%",
        "",
        "## Project Process",
        "",
    ]
    process_track = data.get("process_track") or {}
    for m in process_track.get("milestones", []):
        icon = "✓" if m["status"] == "completed" else "○"
        lines.append(f"- {icon} **{m['name']}:** {m['status']} ({m['completion']}%)")
    lines.append("")

    lines.append("## Epics")
    lines.append("")
    for epic in data["epics"]:
        status_label = epic["status"].replace("_", " ").title()
        lines.append(f"### {epic['name']} — {status_label} ({epic['completion']}%)")
        lines.append("")

    if data.get("active_blockers"):
        lines.append("## Active Blockers")
        lines.append("")
        for b in data["active_blockers"]:
            lines.append(f"- **{b['epic']} / {b['milestone']}:** {b['description']}")
        lines.append("")

    if data.get("recent_deliverables"):
        lines.append("## Recent Deliverables")
        lines.append("")
        for d in data["recent_deliverables"]:
            lines.append(f"- {d}")
        lines.append("")

    return "\n".join(lines)


def export_dashboard_markdown(plan: MilestonePlan) -> str:
    """Return a client-ready Markdown dashboard string from a milestone plan."""
    return export_dashboard_markdown_from_data(_dashboard_data(plan))


def export_dashboard_html_from_data(data: dict[str, Any]) -> str:
    """Return an HTML dashboard styled to match merven.ai from pre-built data."""
    process_html = _process_track_section(data.get("process_track"))
    epics_html = "\n".join(_epic_swimlane(e) for e in data["epics"])
    blockers_html = _blockers_section(data.get("active_blockers", []))
    deliverables_html = _deliverables_section(data.get("recent_deliverables", []))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(data['project_name'])} — Project Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: {_MERVEN_COLORS['background']};
      --fg: {_MERVEN_COLORS['foreground']};
      --card: {_MERVEN_COLORS['card']};
      --card-start: {_MERVEN_COLORS['card_gradient_start']};
      --card-end: {_MERVEN_COLORS['card_gradient_end']};
      --primary: {_MERVEN_COLORS['primary']};
      --primary-fg: {_MERVEN_COLORS['primary_foreground']};
      --muted: {_MERVEN_COLORS['muted']};
      --muted-fg: {_MERVEN_COLORS['muted_foreground']};
      --accent: {_MERVEN_COLORS['accent']};
      --border: {_MERVEN_COLORS['border']};
      --shadow: {_MERVEN_COLORS['shadow']};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: 'Inter', system-ui, sans-serif;
      background: var(--bg);
      color: var(--fg);
      line-height: 1.6;
    }}
    .container {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 4rem 1.5rem;
    }}
    header {{
      text-align: center;
      margin-bottom: 3rem;
    }}
    header h1 {{
      font-size: 2.25rem;
      font-weight: 700;
      margin: 0 0 0.5rem;
      letter-spacing: -0.02em;
    }}
    header p {{
      color: var(--muted-fg);
      margin: 0;
      font-size: 1rem;
    }}
    .overall {{
      background: linear-gradient(135deg, var(--card-start) 0%, var(--card-end) 100%);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 2rem;
      margin-bottom: 2rem;
      box-shadow: var(--shadow);
      text-align: center;
    }}
    .overall .percent {{
      font-size: 3rem;
      font-weight: 700;
      color: var(--primary);
      line-height: 1;
    }}
    .overall .label {{
      color: var(--muted-fg);
      font-size: 0.875rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-top: 0.5rem;
    }}
    .epic {{
      background: linear-gradient(135deg, var(--card-start) 0%, var(--card-end) 100%);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: var(--shadow);
    }}
    .epic-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }}
    .epic-title {{
      font-size: 1.25rem;
      font-weight: 600;
      margin: 0;
    }}
    .epic-completion {{
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--primary);
    }}
    .gantt-row {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0.75rem;
      margin-bottom: 0.75rem;
    }}
    .gantt-row:last-child {{ margin-bottom: 0; }}
    .milestone-bar {{
      background: var(--accent);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 0.75rem;
      position: relative;
      overflow: hidden;
    }}
    .milestone-bar::before {{
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      height: 100%;
      width: var(--fill, 0%);
      background: var(--fill-color, var(--muted));
      opacity: 0.25;
      transition: width 0.5s ease;
    }}
    .milestone-bar.completed::before {{ --fill-color: var(--primary); }}
    .milestone-bar.in_progress::before {{ --fill-color: #3b82f6; }}
    .milestone-bar.blocked::before {{ --fill-color: #ef4444; }}
    .milestone-content {{
      position: relative;
      z-index: 1;
    }}
    .milestone-name {{
      font-size: 0.8125rem;
      font-weight: 500;
      margin: 0 0 0.25rem;
      line-height: 1.3;
    }}
    .milestone-status {{
      display: inline-block;
      font-size: 0.6875rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      padding: 0.125rem 0.5rem;
      border-radius: 9999px;
      background: var(--status-bg);
      color: var(--status-fg);
    }}
    .section {{
      margin-top: 3rem;
    }}
    .section h2 {{
      font-size: 1.5rem;
      font-weight: 600;
      margin: 0 0 1.25rem;
    }}
    .global-section {{
      background: linear-gradient(135deg, var(--card-start) 0%, var(--card-end) 100%);
      border: 1px solid var(--border);
      border-radius: 1rem;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: var(--shadow);
    }}
    .global-section h2 {{
      font-size: 1.25rem;
      font-weight: 600;
      margin: 0 0 1rem;
    }}
    .status-tracker {{
      display: flex;
      gap: 0.75rem;
      margin-bottom: 1.25rem;
    }}
    .status-step {{
      flex: 1;
      text-align: center;
      padding: 0.75rem 0.5rem;
      border-radius: 0.5rem;
      border: 1px solid var(--border);
      background: var(--accent);
      color: var(--muted-fg);
      font-size: 0.8125rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .status-step.active {{
      background: var(--status-bg);
      color: var(--status-fg);
      border-color: var(--status-fg);
    }}
    .blocker {{
      background: rgba(239, 68, 68, 0.08);
      border: 1px solid rgba(239, 68, 68, 0.25);
      border-radius: 0.75rem;
      padding: 1rem 1.25rem;
      margin-bottom: 0.75rem;
    }}
    .blocker strong {{
      color: #f87171;
    }}
    .deliverable {{
      background: var(--accent);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 0.875rem 1.25rem;
      margin-bottom: 0.75rem;
    }}
    footer {{
      text-align: center;
      margin-top: 4rem;
      color: var(--muted-fg);
      font-size: 0.8125rem;
    }}
    footer a {{
      color: var(--primary);
      text-decoration: none;
    }}
    @media (max-width: 640px) {{
      .container {{ padding: 2rem 1rem; }}
      header h1 {{ font-size: 1.75rem; }}
      .overall .percent {{ font-size: 2.25rem; }}
      .gantt-row {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{_escape(data['project_name'])}</h1>
      <p>Project progress dashboard</p>
    </header>

    <section class="overall">
      <div class="percent">{data['overall_completion']}%</div>
      <div class="label">Overall Completion</div>
    </section>

    {process_html}

    {epics_html}

    {blockers_html}

    {deliverables_html}

    <footer>
      Powered by <a href="https://merven.ai" target="_blank" rel="noopener">Merven.ai</a>
    </footer>
  </div>
</body>
</html>"""


def export_dashboard_html(plan: MilestonePlan) -> str:
    """Return an HTML dashboard styled to match merven.ai from a milestone plan."""
    return export_dashboard_html_from_data(_dashboard_data(plan))


def _derive_epic_status(epic: Epic) -> str:
    """Map an epic's process milestones to Planning/In-Progress/Complete.

    The 8 lifecycle milestones are rolled up to 3 high-level statuses:
      - Milestones 1-4 (Intake through Build Planning) = Planning
      - Milestones 5-7 (Implementation through Demo & Delivery) = In Progress
      - Milestone 8 (Retrospective & Findings) = Complete
    """
    active = [
        m for m in epic.milestones
        if m.status not in (MilestoneStatus.NOT_STARTED, MilestoneStatus.SKIPPED)
    ]
    if not active:
        return "planning"

    max_order = max(m.order for m in active)
    if max_order >= 8:
        return "completed"
    if max_order >= 5:
        return "in_progress"
    return "planning"


def _process_track_from_epic(epic: Epic) -> dict[str, Any]:
    """Return the default epic formatted as the project process track."""
    return {
        "id": epic.id,
        "name": "Project Process",
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


def _work_epic(epic: Epic) -> dict[str, Any]:
    """Return a non-process epic formatted for the dashboard."""
    return {
        "id": epic.id,
        "name": epic.name,
        "status": _derive_epic_status(epic),
        "completion": epic.completion(),
    }


def _dashboard_data(plan: MilestonePlan) -> dict[str, Any]:
    """Build a client-safe dashboard data structure."""
    sorted_epics = sorted(plan.epics, key=lambda x: x.order)
    process_epic = sorted_epics[0] if sorted_epics else None
    work_epics = sorted_epics[1:] if len(sorted_epics) > 1 else []

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
        "process_track": _process_track_from_epic(process_epic) if process_epic else None,
        "epics": [_work_epic(epic) for epic in work_epics],
        "recent_deliverables": _recent_deliverables(plan),
    }


def _process_track_section(process_track: dict[str, Any] | None) -> str:
    if not process_track:
        return ""
    milestones = process_track.get("milestones", [])
    rows = [milestones[i : i + 4] for i in range(0, len(milestones), 4)]
    row_html = "\n".join(
        f"""<div class="gantt-row">\n{"\n".join(_milestone_bar(m) for m in row)}\n</div>"""
        for row in rows
    )
    return f"""<section class="global-section">
  <h2>Project Process</h2>
  {row_html}
</section>"""


def _epic_swimlane(epic: dict[str, Any]) -> str:
    status = epic["status"]
    status_fg = _status_color(status)
    status_bg = _status_bg(status)
    steps = ["planning", "in_progress", "completed"]
    step_labels = ["Planning", "In Progress", "Complete"]
    step_html = "\n".join(
        f"""<div class="status-step{' active' if s == status else ''}" style="--status-bg: {status_bg if s == status else 'var(--accent)'}; --status-fg: {status_fg if s == status else 'var(--muted-fg)'}">{label}</div>"""
        for s, label in zip(steps, step_labels)
    )

    return f"""<section class="epic">
  <div class="epic-header">
    <h2 class="epic-title">{_escape(epic['name'])}</h2>
    <span class="epic-completion">{epic['completion']}% complete</span>
  </div>
  <div class="status-tracker">
    {step_html}
  </div>
</section>"""


def _milestone_bar(m: dict[str, Any]) -> str:
    status = m["status"].replace("_", "-")
    status_label = m["status"].replace("_", " ")
    status_fg = _status_color(m["status"])
    status_bg = _status_bg(m["status"])
    return f"""<div class="milestone-bar {status}" style="--fill: {m['completion']}%; --status-bg: {status_bg}; --status-fg: {status_fg};">
      <div class="milestone-content">
        <div class="milestone-name">{_escape(m['name'])}</div>
        <span class="milestone-status">{status_label} {m['completion']}%</span>
      </div>
    </div>"""


def _blockers_section(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return ""
    items = "\n".join(
        f"<div class='blocker'><strong>{_escape(b['epic'])} / {_escape(b['milestone'] or '')}</strong> — {_escape(b['description'])}</div>"
        for b in blockers
    )
    return f"""<section class="section">
  <h2>Active Blockers</h2>
  {items}
</section>"""


def _deliverables_section(deliverables: list[str]) -> str:
    if not deliverables:
        return ""
    items = "\n".join(f"<div class='deliverable'>{_escape(d)}</div>" for d in deliverables)
    return f"""<section class="section">
  <h2>Recent Deliverables</h2>
  {items}
</section>"""


def _recent_deliverables(plan: MilestonePlan) -> list[str]:
    """Extract recent deliverable names from detected artifacts."""
    deliverables: list[str] = []
    for epic in sorted(plan.epics, key=lambda e: e.order):
        for milestone in sorted(epic.milestones, key=lambda m: m.order):
            for artifact in milestone.artifacts:
                if artifact.detected and artifact.path.startswith("docs/"):
                    name = artifact.path.replace("docs/", "").replace("/", " — ").replace("-", " ").replace("_", " ")
                    deliverables.append(f"{epic.name} / {milestone.name}: {name.title()}")
    return deliverables[-5:]


def _milestone_summary(milestone: Any) -> str:
    """Generate a one-sentence client-safe summary."""
    if milestone.status.value == "completed":
        return f"{milestone.name} is complete."
    if milestone.status.value == "in_progress":
        return f"{milestone.name} is in progress ({milestone.completion()}% complete)."
    if milestone.status.value == "blocked":
        return f"{milestone.name} is blocked."
    return f"{milestone.name} has not started."


def _escape(text: str) -> str:
    """Escape HTML special characters."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _json_serializer(obj: Any) -> Any:
    """JSON serializer for dates and datetimes."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
