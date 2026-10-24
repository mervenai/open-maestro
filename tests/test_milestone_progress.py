"""Progress override, weighted epic completion, client_ready and epic step label.

Why: the dashboard overstated completion (flat 50% for in-progress work, unweighted
epic means, client_ready always true, "Planning" shown next to 100%).
"""

from __future__ import annotations

import pytest

from open_maestro.milestones import Epic, Milestone, MilestonePlan, MilestoneStatus
from open_maestro.milestones.dashboard import _derive_epic_status, export_dashboard_html

S = MilestoneStatus


def _m(order: int, weight: int, status: MilestoneStatus, progress: int | None = None) -> Milestone:
    return Milestone(
        id=f"m{order}",
        name=f"M{order}",
        order=order,
        weight=weight,
        status=status,
        progress=progress,
    )


def _epic(*milestones: Milestone, epic_id: str = "e1", order: int = 1) -> Epic:
    return Epic(id=epic_id, name=epic_id, order=order, milestones=list(milestones))


class TestProgressOverride:
    def test_in_progress_uses_progress(self):
        assert _m(1, 10, S.IN_PROGRESS, 12).completion() == 12

    def test_in_progress_without_progress_keeps_50(self):
        assert _m(1, 10, S.IN_PROGRESS).completion() == 50

    def test_terminal_statuses_ignore_stale_progress(self):
        assert _m(1, 10, S.COMPLETED, 30).completion() == 100
        assert _m(1, 10, S.NOT_STARTED, 30).completion() == 0
        assert _m(1, 10, S.SKIPPED, 30).completion() == 0

    @pytest.mark.parametrize("bad", [-1, 101])
    def test_progress_bounds(self, bad):
        with pytest.raises(ValueError):
            _m(1, 10, S.IN_PROGRESS, bad)

    def test_progress_omitted_from_dict_when_unset(self):
        plan = MilestonePlan(project_id="p", epics=[_epic(_m(1, 10, S.IN_PROGRESS))])
        assert "progress" not in plan.to_dict()["epics"][0]["milestones"][0]


class TestWeightedCompletion:
    def test_epic_weighted_by_milestone_weight(self):
        epic = _epic(_m(1, 10, S.COMPLETED), _m(2, 30, S.NOT_STARTED))
        assert epic.completion() == 25  # unweighted would be 50

    def test_epic_ignores_skipped(self):
        epic = _epic(_m(1, 10, S.SKIPPED), _m(2, 30, S.IN_PROGRESS, 12))
        assert epic.completion() == 12

    def test_epic_all_skipped_is_zero(self):
        assert _epic(_m(1, 10, S.SKIPPED)).completion() == 0

    def test_zero_weight_falls_back_to_plain_mean(self):
        assert _epic(_m(1, 0, S.IN_PROGRESS, 12)).completion() == 12

    def test_overall_floors_once_not_per_milestone(self):
        # 30 * 3 // 100 == 0 per milestone; summed first, 90 / 40 -> 2.
        plan = MilestonePlan(
            project_id="p", epics=[_epic(_m(1, 30, S.IN_PROGRESS, 3), _m(2, 10, S.NOT_STARTED))]
        )
        assert plan.summary.overall_completion == 2


class TestClientReady:
    def test_not_ready_while_work_remains(self):
        plan = MilestonePlan(
            project_id="p", epics=[_epic(_m(1, 10, S.COMPLETED), _m(2, 10, S.IN_PROGRESS, 99))]
        )
        assert plan.summary.client_ready is False

    def test_ready_when_every_active_milestone_completed(self):
        plan = MilestonePlan(
            project_id="p", epics=[_epic(_m(1, 10, S.COMPLETED), _m(2, 10, S.SKIPPED))]
        )
        assert plan.summary.client_ready is True

    def test_empty_plan_not_ready(self):
        assert MilestonePlan(project_id="p").summary.client_ready is False


class TestEpicStepLabel:
    def test_all_active_completed_is_complete_not_planning(self):
        epic = _epic(_m(1, 10, S.COMPLETED), _m(2, 10, S.COMPLETED), _m(5, 30, S.SKIPPED))
        assert epic.completion() == 100
        assert _derive_epic_status(epic) == "completed"

    def test_nothing_started_is_planning(self):
        assert _derive_epic_status(_epic(_m(5, 30, S.NOT_STARTED))) == "planning"

    def test_implementation_in_progress(self):
        assert _derive_epic_status(_epic(_m(5, 30, S.IN_PROGRESS, 12))) == "in_progress"

    def test_dashboard_renders_progress(self):
        plan = MilestonePlan(
            project_id="p",
            epics=[
                _epic(_m(5, 30, S.IN_PROGRESS, 3), epic_id="default"),
                _epic(_m(5, 30, S.IN_PROGRESS, 12), epic_id="be-1", order=2),
            ],
        )
        html = export_dashboard_html(plan)
        assert "in progress 3%" in html
        assert "12% complete" in html
