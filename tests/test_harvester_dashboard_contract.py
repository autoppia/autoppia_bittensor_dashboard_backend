from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.ui.tasks import ActionType, TaskAction, TaskSolutionSummary
from app.services.ui.ui_rounds_service_mixin import UIRoundsServiceMixin

pytestmark = pytest.mark.no_db


def test_king_overfit_judge_summary_normalizes_rejected_uids() -> None:
    summary = {
        "king_overfit_judgements": [
            {"uid": 2, "verdict": "accept", "confidence": 0.3},
            {"uid": "7", "verdict": "reject", "confidence": 0.91},
        ],
        "king_overfit_rejected_uids": ["7", 9, None, "bad"],
    }

    out = UIRoundsServiceMixin._king_overfit_judge_summary(summary)

    assert out["enabled"] is True
    assert out["rejected_uids"] == [7, 9]
    assert out["rejected_count"] == 2
    assert out["latest"]["uid"] == "7"


def test_task_solution_summary_exposes_trajectory_tool_count() -> None:
    summary = TaskSolutionSummary(
        solutionId="solution-1",
        agentRunId="run-1",
        minerUid=12,
        validatorUid=3,
        actionsCount=2,
        trajectoryToolsCount=2,
    )

    assert summary.actionsCount == 2
    assert summary.trajectoryToolsCount == 2


def test_task_action_can_be_used_as_trajectory_tool() -> None:
    tool = TaskAction(
        id="0",
        type=ActionType.NAVIGATE,
        value="https://example.com",
        timestamp=datetime.now(timezone.utc),
        duration=0.0,
        success=True,
    )

    assert tool.model_dump()["type"] == "navigate"
