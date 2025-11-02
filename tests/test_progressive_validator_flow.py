from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import RoundORM, TaskORM, TaskSolutionORM, EvaluationResultORM


@pytest.mark.asyncio
async def test_progressive_validator_flow(client, db_session, monkeypatch):
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    base_payload = {
        "round": {
            "validator_round_id": "round_progressive",
            "round": 301,
            "validators": [
                {
                    "uid": 1001,
                    "hotkey": "validator_hotkey_1001",
                    "coldkey": None,
                    "stake": 1500.0,
                    "vtrust": 0.9,
                    "name": "Progressor",
                    "version": "7.0.1",
                }
            ],
            "start_block": 1200,
            "start_epoch": 1,
            "n_tasks": 5,
            "n_miners": 3,
            "n_winners": 1,
            "started_at": 1_700_000_000.0,
            "status": "in_progress",
        },
        "tasks": [],
        "agent_run": {
            "agent_run_id": "run_progressive",
            "validator_round_id": "round_progressive",
            "validator_uid": 1001,
            "miner_uid": 501,
            "miner_info": {
                "uid": 501,
                "hotkey": "miner_hotkey_501",
                "coldkey": None,
                "agent_name": "Miner 501",
                "agent_image": "",
                "github": "https://github.com/autoppia/miner-501",
                "is_sota": False,
            },
            "is_sota": False,
            "version": "1.0",
            "task_ids": [],
            "started_at": 1_700_000_050.0,
            "metadata": {},
        },
    }

    start_payload = {
        "validator_round_id": base_payload["round"]["validator_round_id"],
        "round": base_payload["round"],
    }
    # Patch chain to be inside this test round window (301)
    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block", lambda: inside_round(301)
    )
    start_response = await client.post(
        "/api/v1/validator-rounds/start", json=start_payload
    )
    assert start_response.status_code == 200

    # Create tasks and results incrementally
    for idx in range(1, 6):
        task_id = f"task_progressive_{idx:02d}"
        task_payload = {
            "task_id": task_id,
            "validator_round_id": base_payload["round"]["validator_round_id"],
            "scope": "local",
            "is_web_real": False,
            "web_project_id": None,
            "url": f"https://example.com/{idx}",
            "prompt": f"Progressive task {idx}",
            "html": "<html></html>",
            "clean_html": "<html></html>",
            "interactive_elements": None,
            "screenshot": None,
            "screenshot_description": None,
            "specifications": {},
            "tests": [],
            "milestones": None,
            "relevant_data": {},
            "success_criteria": "seeded",
            "use_case": {"name": "Progressive"},
            "should_record": False,
        }

        tasks_response = await client.post(
            f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/tasks",
            json={"tasks": [task_payload]},
        )
        assert tasks_response.status_code == 200

        agent_run_payload = {
            "agent_run": base_payload["agent_run"],
        }
        agent_run_payload["agent_run"]["task_ids"].append(task_id)

        start_agent_response = await client.post(
            f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/agent-runs/start",
            json=agent_run_payload,
        )
        assert start_agent_response.status_code == 200

        add_evaluation_payload = {
            "task": task_payload,
            "task_solution": {
                "solution_id": f"{task_id}_solution",
                "task_id": task_id,
                "validator_round_id": base_payload["round"]["validator_round_id"],
                "agent_run_id": base_payload["agent_run"]["agent_run_id"],
                "miner_uid": 501,
                "validator_uid": 1001,
                "actions": [],
                "web_agent_id": "miner-501",
            },
            "evaluation_result": {
                "evaluation_id": f"{task_id}_eval",
                "task_id": task_id,
                "task_solution_id": f"{task_id}_solution",
                "validator_round_id": base_payload["round"]["validator_round_id"],
                "agent_run_id": base_payload["agent_run"]["agent_run_id"],
                "miner_uid": 501,
                "validator_uid": 1001,
                "final_score": 0.9,
                "test_results_matrix": [[{"success": True}]],
                "execution_history": [],
                "feedback": None,
                "web_agent_id": "miner-501",
                "raw_score": 0.9,
                "evaluation_time": 5.0,
                "stats": None,
                "gif_recording": None,
            },
        }

        evaluation_response = await client.post(
            f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/agent-runs/{base_payload['agent_run']['agent_run_id']}/evaluations",
            json=add_evaluation_payload,
        )
        assert evaluation_response.status_code == 200

    finish_payload = {
        "status": "completed",
        "winners": [{"miner_uid": 501, "score": 0.9}],
        "winner_scores": [0.9],
        "weights": {"501": 1.0},
        "ended_at": 1_700_001_000.0,
        "summary": {"tasks": 5},
    }
    finish_response = await client.post(
        f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/finish",
        json=finish_payload,
    )
    assert finish_response.status_code == 200

    round_row = await db_session.scalar(
        select(RoundORM).where(
            RoundORM.validator_round_id == base_payload["round"]["validator_round_id"]
        )
    )
    assert round_row is not None
    assert round_row.data["status"] == "finished"
