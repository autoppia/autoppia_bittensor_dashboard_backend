import pytest

from app.services.ui.ui_agents_runs_service_mixin import UIAgentsRunsServiceMixin
from app.services.ui.ui_data_service import UIDataService

pytestmark = pytest.mark.no_db


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar


class _FakeSession:
    async def execute(self, statement, params=None):  # noqa: ARG002
        sql = str(statement)

        if "SELECT season_rank" in sql and "FROM ranked" in sql:
            return _FakeResult([{"season_rank": 2}])

        if "SELECT rvm.*" in sql:
            return _FakeResult(
                [
                    {
                        "round_validator_id": 10083,
                        "name": "onyxdrift",
                        "miner_hotkey": "5C4iqmFUgsdkausBNMyfqD7AqBmxfi340mnSUsW4nTdSk5aR",
                        "image_url": None,
                        "github_url": "https://github.com/example/repo/commit/abc",
                        "is_sota": False,
                        "version": "1.0.0",
                        "local_avg_reward": 0.09431335013637909,
                        "local_tasks_received": 50,
                        "local_tasks_success": 5,
                        "best_local_reward": 0.09431335013637909,
                        "post_consensus_avg_reward": 0.09433364717204927,
                        "post_consensus_rank": 2,
                    }
                ]
            )

        if "SELECT validator_uid, validator_hotkey, name" in sql:
            return _FakeResult(
                [
                    {"validator_uid": 20, "validator_hotkey": "hk20", "name": "Rizzo"},
                    {"validator_uid": 55, "validator_hotkey": "hk55", "name": "Yuma"},
                    {"validator_uid": 83, "validator_hotkey": "hk83", "name": "Autoppia"},
                ]
            )

        if "rv.validator_uid," in sql and "rvm.local_tasks_received" in sql:
            return _FakeResult(
                [
                    {
                        "validator_uid": 20,
                        "ipfs_uploaded": None,
                        "local_tasks_received": 50,
                        "local_tasks_success": 3,
                        "run_total_tasks": 50,
                        "run_success_tasks": 3,
                    },
                    {
                        "validator_uid": 55,
                        "ipfs_uploaded": None,
                        "local_tasks_received": 50,
                        "local_tasks_success": 4,
                        "run_total_tasks": 50,
                        "run_success_tasks": 4,
                    },
                    {
                        "validator_uid": 83,
                        "ipfs_uploaded": None,
                        "local_tasks_received": 50,
                        "local_tasks_success": 5,
                        "run_total_tasks": 50,
                        "run_success_tasks": 5,
                    },
                ]
            )

        if "WHERE mer.miner_uid = :uid" in sql and "mer.round_validator_id = :round_validator_id" in sql:
            return _FakeResult(
                [
                    {
                        "agent_run_id": "run-round-1-main",
                        "started_at": 1_700_000_000,
                        "ended_at": 1_700_000_100,
                        "total_tasks": 50,
                        "success_tasks": 5,
                        "zero_reason": None,
                        "early_stop_reason": None,
                        "early_stop_message": None,
                        "tasks_attempted": 50,
                    }
                ]
            )

        if "SELECT mer.agent_run_id" in sql and "JOIN round_validators rv" in sql:
            return _FakeResult(
                [
                    {"agent_run_id": "run-round-1-v20"},
                    {"agent_run_id": "run-round-1-v55"},
                    {"agent_run_id": "run-round-1-v83"},
                ]
            )

        if "FROM task_solutions ts" in sql:
            agent_run_id = (params or {}).get("agent_run_id")
            success_count = {
                "run-round-1-v20": 3,
                "run-round-1-v55": 4,
                "run-round-1-v83": 5,
            }.get(agent_run_id, 0)
            rows = []
            for idx in range(50):
                rows.append(
                    {
                        "task_id": f"{agent_run_id}-task-{idx}",
                        "actions": [{"url": "http://localhost:8000"}],
                        "web_project_id": "autocinema",
                        "task_url": "http://localhost:8000",
                        "evaluation_score": 1.0 if idx < success_count else 0.0,
                        "llm_cost": 0.002,
                        "llm_usage_count": 1,
                    }
                )
            return _FakeResult(rows)

        if "FROM round_summary" in sql:
            return _FakeResult([])

        if "SELECT COUNT(*)" in sql and "FROM miner_evaluation_runs mer" in sql and "mer.success_tasks > 0" in sql:
            return _FakeResult(scalar=2)

        if "SELECT COUNT(*)" in sql and "FROM miner_evaluation_runs mer" in sql:
            return _FakeResult(scalar=2)

        if "SELECT COUNT(DISTINCT rvm.round_id)" in sql:
            return _FakeResult(scalar=2)

        if "SELECT COUNT(*) FROM round_summary" in sql:
            return _FakeResult(scalar=0)

        raise AssertionError(f"Unexpected SQL in test: {sql}")


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


def test_agent_ui_best_round_prefers_actual_round_reward_over_effective_reward():
    round_rows = [
        {
            "round_number_in_season": 1,
            "reward": 0.09431335013637909,
            "rank": 2,
            "competition_reward": 0.09431335013637909,
        },
        {
            "round_number_in_season": 2,
            "reward": 0.0419,
            "rank": 2,
            "competition_reward": 0.09433364717204927,
        },
    ]

    best_row = max(round_rows, key=UIAgentsRunsServiceMixin._best_round_sort_key)

    assert best_row["round_number_in_season"] == 1


@pytest.mark.asyncio
async def test_get_agent_detail_best_round_uses_best_round_metrics(monkeypatch):
    service = UIDataService(_FakeSession())

    async def _fake_main_validator_uid():
        return 83

    async def _fake_round_ref(season, round_in_season):  # noqa: ARG001
        return {
            "round_id": 101 if int(round_in_season) == 1 else 102,
            "round_number_in_season": int(round_in_season),
        }

    async def _fake_season_round_metric_rows(miner_uid, season):  # noqa: ARG001
        return [
            {
                "round_id": 102,
                "season_number": 1,
                "round_number_in_season": 2,
                "reward": 0.0419105977267336,
                "rank": 2,
                "eval_score": 0.042052927398460324,
                "eval_time": 61.871524566677,
                "eval_cost": 0.00149,
                "tasks_received": 150,
                "tasks_success": 7,
                "top_reward": 0.0995551883,
                "competition_reward": 0.09433364717204927,
            },
            {
                "round_id": 101,
                "season_number": 1,
                "round_number_in_season": 1,
                "reward": 0.09431335013637909,
                "rank": 2,
                "eval_score": 0.09639023354383226,
                "eval_time": 81.39009634199863,
                "eval_cost": 0.0021891451453477763,
                "tasks_received": 150,
                "tasks_success": 12,
                "top_reward": 0.0995601692,
                "competition_reward": 0.09431335013637909,
            },
        ]

    monkeypatch.setattr(service, "_get_main_validator_uid", _fake_main_validator_uid)
    monkeypatch.setattr(service, "_round_ref", _fake_round_ref)
    monkeypatch.setattr(service, "_get_season_round_metric_rows", _fake_season_round_metric_rows)

    payload = await service.get_agent_detail(191, season=1, round_in_season=None)

    assert payload["agent"]["bestRoundId"] == 1
    assert payload["agent"]["bestRoundReward"] == pytest.approx(0.09431335013637909)
    assert payload["agent"]["currentReward"] == pytest.approx(0.09431335013637909)
    assert payload["bestRound"]["round"] == 1
    assert payload["bestRound"]["post_consensus_avg_reward"] == pytest.approx(0.09431335013637909)
    assert payload["bestRound"]["post_consensus_avg_eval_time"] == pytest.approx(81.39009634199863)
    assert payload["bestRound"]["post_consensus_avg_cost"] == pytest.approx(0.0021891451453477763)
    assert payload["bestRound"]["tasks_received"] == 150
    assert payload["bestRound"]["tasks_success"] == 12
