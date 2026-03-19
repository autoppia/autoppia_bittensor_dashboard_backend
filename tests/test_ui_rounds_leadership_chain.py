from __future__ import annotations

import pytest

from app.services.ui.ui_rounds_service_mixin import UIRoundsServiceMixin

pytestmark = pytest.mark.no_db


def test_normalize_leadership_chain_uses_previous_round_reward_for_threshold() -> None:
    leader_before = {
        "uid": 168,
        "reward": 0.17547999119965071,
        "score": 0.28,
        "time": 86.99,
        "cost": 0.0034,
    }
    candidate = {
        "uid": 196,
        "reward": 0.1907841665631254,
        "score": 0.13078707785595142,
        "time": 117.16,
        "cost": 0.0028,
    }
    stale_leader_after = {
        "uid": 168,
        "reward": 0.1849726186432933,
        "score": 0.08,
        "time": 80.71,
        "cost": 0.0020,
    }

    normalized = UIRoundsServiceMixin._normalize_leadership_chain(
        leader_before=leader_before,
        candidate=candidate,
        leader_after=stale_leader_after,
        required_improvement_pct=0.05,
    )

    fixed_leader_before, fixed_candidate, fixed_leader_after, dethroned, threshold = normalized
    assert fixed_leader_before["uid"] == 168
    assert fixed_leader_before["reward"] == pytest.approx(0.17547999119965071)
    assert fixed_candidate["uid"] == 196
    assert fixed_candidate["reward"] == pytest.approx(0.1907841665631254)
    assert threshold == pytest.approx(0.17547999119965071 * 1.05)
    assert dethroned is True
    assert fixed_leader_after["uid"] == 196


def test_normalize_leadership_chain_keeps_previous_leader_when_candidate_is_missing() -> None:
    leader_before = {
        "uid": 168,
        "reward": 0.17547999119965071,
        "score": 0.28,
        "time": 86.99,
        "cost": 0.0034,
    }

    fixed_leader_before, fixed_candidate, fixed_leader_after, dethroned, threshold = UIRoundsServiceMixin._normalize_leadership_chain(
        leader_before=leader_before,
        candidate=None,
        leader_after=None,
        required_improvement_pct=0.05,
    )

    assert fixed_leader_before["uid"] == 168
    assert fixed_candidate is None
    assert fixed_leader_after["uid"] == 168
    assert threshold == pytest.approx(0.17547999119965071 * 1.05)
    assert dethroned is False
