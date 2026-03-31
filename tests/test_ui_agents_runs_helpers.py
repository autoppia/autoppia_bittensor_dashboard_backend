import pytest

from app.services.ui.ui_agents_runs_service_mixin import UIAgentsRunsServiceMixin

pytestmark = pytest.mark.no_db


def test_agent_ui_consensus_task_totals_exclude_all_zero_validator():
    validator_rows = [
        {
            "validator_uid": 55,
            "ipfs_uploaded": {"payload": {"summary": {"validator_all_best_runs_zero": True}}},
            "run_total_tasks": 100,
            "run_success_tasks": 0,
        },
        {
            "validator_uid": 71,
            "ipfs_uploaded": {"payload": {"summary": {"validator_all_best_runs_zero": False}}},
            "run_total_tasks": 100,
            "run_success_tasks": 6,
        },
        {
            "validator_uid": 83,
            "ipfs_uploaded": {"payload": {"summary": {"validator_all_best_runs_zero": False}}},
            "run_total_tasks": 100,
            "run_success_tasks": 2,
        },
    ]

    total_tasks, success_tasks = UIAgentsRunsServiceMixin._derive_consensus_task_totals(validator_rows)

    assert total_tasks == 200
    assert success_tasks == 8


def test_agent_ui_consensus_task_totals_keep_all_validators_when_none_are_all_zero():
    validator_rows = [
        {
            "validator_uid": 55,
            "ipfs_uploaded": {"payload": {"summary": {"validator_all_best_runs_zero": False}}},
            "run_total_tasks": 100,
            "run_success_tasks": 0,
        },
        {
            "validator_uid": 71,
            "ipfs_uploaded": {"payload": {"summary": {"validator_all_best_runs_zero": False}}},
            "run_total_tasks": 100,
            "run_success_tasks": 6,
        },
    ]

    total_tasks, success_tasks = UIAgentsRunsServiceMixin._derive_consensus_task_totals(validator_rows)

    assert total_tasks == 200
    assert success_tasks == 6


def test_agent_ui_round_local_consensus_prefers_current_run_metrics():
    validator_rows = [
        {
            "stake": 1.0,
            "run_reward": 0.04,
            "run_score": 0.04,
            "run_time": 60.0,
            "run_avg_cost": 0.0015,
            "run_total_tasks": 50,
            "run_success_tasks": 2,
            "post_consensus_avg_reward": 0.094,
            "post_consensus_avg_eval_score": 0.042,
            "post_consensus_avg_eval_time": 63.8,
            "post_consensus_avg_eval_cost": 0.0016,
            "post_consensus_tasks_received": 150,
            "post_consensus_tasks_success": 7,
        },
        {
            "stake": 2.0,
            "run_reward": 0.06,
            "run_score": 0.06,
            "run_time": 68.0,
            "run_avg_cost": 0.0013,
            "run_total_tasks": 50,
            "run_success_tasks": 3,
            "post_consensus_avg_reward": 0.094,
            "post_consensus_avg_eval_score": 0.042,
            "post_consensus_avg_eval_time": 63.8,
            "post_consensus_avg_eval_cost": 0.0016,
            "post_consensus_tasks_received": 150,
            "post_consensus_tasks_success": 7,
        },
        {
            "stake": 7.0,
            "run_reward": 0.04,
            "run_score": 0.04,
            "run_time": 61.0,
            "run_avg_cost": 0.0015,
            "run_total_tasks": 50,
            "run_success_tasks": 2,
            "post_consensus_avg_reward": 0.094,
            "post_consensus_avg_eval_score": 0.042,
            "post_consensus_avg_eval_time": 63.8,
            "post_consensus_avg_eval_cost": 0.0016,
            "post_consensus_tasks_received": 150,
            "post_consensus_tasks_success": 7,
        },
    ]

    consensus = UIAgentsRunsServiceMixin._derive_round_local_consensus(validator_rows)

    assert consensus["reward"] == pytest.approx(0.044)
    assert consensus["score"] == pytest.approx(0.044)
    assert consensus["time"] == pytest.approx(62.3)
    assert consensus["avg_cost"] == pytest.approx(0.00146)
    assert consensus["tasks_received"] == 150
    assert consensus["tasks_success"] == 7


def test_agent_ui_round_ranking_consensus_keeps_effective_post_consensus_reward():
    validator_rows = [
        {
            "stake": 1.0,
            "post_consensus_avg_reward": 0.094,
            "post_consensus_avg_eval_score": 0.042,
            "post_consensus_avg_eval_time": 63.8,
            "post_consensus_avg_eval_cost": 0.0016,
            "post_consensus_tasks_received": 150,
            "post_consensus_tasks_success": 7,
        },
        {
            "stake": 2.0,
            "post_consensus_avg_reward": 0.095,
            "post_consensus_avg_eval_score": 0.043,
            "post_consensus_avg_eval_time": 64.0,
            "post_consensus_avg_eval_cost": 0.0017,
            "post_consensus_tasks_received": 150,
            "post_consensus_tasks_success": 7,
        },
    ]

    ranking = UIAgentsRunsServiceMixin._derive_round_ranking_consensus(validator_rows)

    assert ranking["reward"] == pytest.approx((0.094 + 2 * 0.095) / 3)
    assert ranking["score"] == pytest.approx((0.042 + 2 * 0.043) / 3)
    assert ranking["time"] == pytest.approx((63.8 + 2 * 64.0) / 3)
    assert ranking["avg_cost"] == pytest.approx((0.0016 + 2 * 0.0017) / 3)
    assert ranking["tasks_received"] == 150
    assert ranking["tasks_success"] == 7
