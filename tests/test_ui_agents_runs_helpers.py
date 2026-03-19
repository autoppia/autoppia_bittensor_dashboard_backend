from app.services.ui.ui_agents_runs_service_mixin import UIAgentsRunsServiceMixin


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
