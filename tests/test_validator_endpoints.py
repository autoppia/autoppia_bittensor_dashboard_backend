from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationResultORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
)


def _make_submission_payload(prefix: str = "001") -> dict:
    validator_uid = 900 + int(prefix)
    validator_round_id = f"round_{prefix}"
    agent_run_id = f"agent_run_{prefix}"
    task_id = f"task_{prefix}"
    solution_id = f"solution_{prefix}"
    evaluation_id = f"evaluation_{prefix}"
    miner_uid = 700 + int(prefix)

    validator_info = {
        "uid": validator_uid,
        "hotkey": f"validator_hotkey_{prefix}",
        "coldkey": f"validator_coldkey_{prefix}",
        "stake": 123.45,
        "vtrust": 0.98,
        "name": f"Validator {prefix}",
    }

    miner_info = {
        "uid": miner_uid,
        "hotkey": f"miner_hotkey_{prefix}",
        "coldkey": f"miner_coldkey_{prefix}",
        "agent_name": f"Agent {prefix}",
        "agent_image": "",
        "github": f"https://github.com/agent{prefix}",
        "is_sota": False,
    }

    round_payload = {
        "validator_round_id": validator_round_id,
        "round": int(prefix),
        "validator_info": validator_info,
        "validators": [validator_info],
        "start_block": 100,
        "start_epoch": 1,
        "end_block": 120,
        "end_epoch": 2,
        "started_at": 1000.0,
        "ended_at": 1200.0,
        "elapsed_sec": 200.0,
        "max_epochs": 20,
        "max_blocks": 360,
        "n_tasks": 1,
        "miners": [miner_info],
        "sota_agents": [],
        "winners": [],
        "winner_scores": [],
        "weights": {},
        "average_score": 0.75,
        "top_score": 0.9,
        "status": "in_progress",
    }

    agent_run_payload = {
        "agent_run_id": agent_run_id,
        "validator_round_id": validator_round_id,
        "validator_uid": validator_uid,
        "miner_uid": miner_uid,
        "miner_info": miner_info,
        "is_sota": False,
        "version": "1.0",
        "task_ids": [task_id],
        "started_at": 1010.0,
        "ended_at": 1020.0,
        "elapsed_sec": 10.0,
        "avg_eval_score": 0.8,
        "avg_execution_time": 12.0,
        "avg_reward": 0.4,
        "total_reward": 1.2,
        "n_tasks_total": 1,
        "n_tasks_completed": 1,
        "n_tasks_failed": 0,
        "rank": 1,
        "weight": 0.5,
        "metadata": {"notes": "Test run"},
    }

    task_payload = {
        "task_id": task_id,
        "validator_round_id": validator_round_id,
        "is_web_real": False,
        "web_project_id": None,
        "url": "https://example.com",
        "prompt": "Execute integration test task.",
        "specifications": {"browser": "chrome"},
        "tests": [],
        "use_case": {"name": "Example"},
    }

    task_solution_payload = {
        "solution_id": solution_id,
        "task_id": task_id,
        "validator_round_id": validator_round_id,
        "agent_run_id": agent_run_id,
        "miner_uid": miner_uid,
        "validator_uid": validator_uid,
        "actions": [{"type": "click", "attributes": {"selector": "#submit"}}],
        "web_agent_id": "agent",
    }

    evaluation_payload = {
        "evaluation_id": evaluation_id,
        "task_id": task_id,
        "task_solution_id": solution_id,
        "validator_round_id": validator_round_id,
        "agent_run_id": agent_run_id,
        "miner_uid": miner_uid,
        "validator_uid": validator_uid,
        "evaluation_score": 0.92,
        "test_results_matrix": [[{"success": True, "extra_data": {"confidence": 0.99}}]],
        "execution_history": [{"action": "click", "selector": "#submit"}],
        "feedback": None,
        "web_agent_id": "agent",
        "raw_score": 0.92,
        "evaluation_time": 5.0,
        "stats": None,
        "gif_recording": None,
    }

    return {
        "round": round_payload,
        "agent_evaluation_runs": [agent_run_payload],
        "tasks": [task_payload],
        "task_solutions": [task_solution_payload],
        "evaluation_results": [evaluation_payload],
    }


class _MockResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _normalize_round_status(status_value) -> str:
    normalized = str(status_value or "").lower()
    if normalized in {"in_progress", "in-progress", "active"}:
        return "active"
    if normalized in {"finished", "complete", "completed"}:
        return "completed"
    if normalized == "pending":
        return "pending"
    return normalized or "active"


async def submit_round_via_validator_endpoints(client, payload):
    round_data = deepcopy(payload["round"])
    validator_round_id = round_data["validator_round_id"]
    # Ensure chain-derived backend accepts the requested round number
    try:
        import app.api.validator.validator_round as vmod
        from app.config import settings as _settings

        blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
        dz = int(_settings.DZ_STARTING_BLOCK)

        def _inside_round(n: int) -> int:
            return dz + (n - 1) * blocks_per_round + 1

        _prev = getattr(vmod, "get_current_block", None)
        vmod.get_current_block = lambda: _inside_round(int(round_data.get("round") or round_data.get("round_number") or 1))  # type: ignore[assignment]
    except Exception:
        _prev = None

    start_response = await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": validator_round_id, "round": round_data},
    )
    if start_response.status_code >= 400:
        return start_response

    tasks = [deepcopy(task) for task in payload.get("tasks", [])]
    if tasks:
        tasks_response = await client.post(
            f"/api/v1/validator-rounds/{validator_round_id}/tasks",
            json={"tasks": tasks},
        )
        if tasks_response.status_code >= 400:
            return tasks_response

    tasks_by_id = {task["task_id"]: task for task in tasks}

    solutions_by_run = {}
    for solution in payload.get("task_solutions", []):
        run_map = solutions_by_run.setdefault(solution["agent_run_id"], {})
        run_map[solution["solution_id"]] = solution

    evaluations_by_run = {}
    for result in payload.get("evaluation_results", []):
        evaluations_by_run.setdefault(result["agent_run_id"], []).append(result)

    agent_runs = payload.get("agent_evaluation_runs", [])
    for agent_run in agent_runs:
        agent_response = await client.post(
            f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start",
            json={"agent_run": agent_run},
        )
        if agent_response.status_code >= 400:
            return agent_response

        agent_run_id = agent_run["agent_run_id"]
        solution_map = solutions_by_run.get(agent_run_id, {})

        for result in evaluations_by_run.get(agent_run_id, []):
            task = tasks_by_id.get(result["task_id"])
            solution = solution_map.get(result["task_solution_id"])
            if task is None or solution is None:
                raise AssertionError("Evaluation payload missing task or solution data")
            evaluation_response = await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations",
                json={
                    "task": task,
                    "task_solution": solution,
                    "evaluation_result": result,
                },
            )
            if evaluation_response.status_code >= 400:
                return evaluation_response

    validator_info = round_data.get("validator_info") or {}
    primary_validator = (round_data.get("validators") or [validator_info])[0]
    validator_uid = primary_validator.get("uid")

    finish_response = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/finish",
        json={
            "status": _normalize_round_status(round_data.get("status")),
            "winners": round_data.get("winners", []),
            "winner_scores": round_data.get("winner_scores", []),
            "weights": round_data.get("weights", {}),
            "ended_at": round_data.get("ended_at"),
            "summary": round_data.get("summary"),
            "agent_runs": [
                {
                    "agent_run_id": agent_run["agent_run_id"],
                    "rank": agent_run.get("rank"),
                    "weight": agent_run.get("weight"),
                }
                for agent_run in agent_runs
            ],
        },
    )
    # Restore chain getter
    try:
        if _prev is not None:
            vmod.get_current_block = _prev  # type: ignore[assignment]
    except Exception:
        pass
    if finish_response.status_code >= 400:
        return finish_response

    return _MockResponse(
        200,
        {
            "success": True,
            "validator_round_id": validator_round_id,
            "validator_uid": validator_uid,
        },
    )


@pytest.mark.asyncio
async def test_round_submission_flow(client, db_session):
    payload = _make_submission_payload("101")
    response = await submit_round_via_validator_endpoints(client, payload)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["validator_round_id"] == payload["round"]["validator_round_id"]

    round_row = await db_session.scalar(select(RoundORM).where(RoundORM.validator_round_id == payload["round"]["validator_round_id"]))
    assert round_row is not None
    assert round_row.validator_uid == payload["round"]["validator_info"]["uid"]
    assert round_row.data["status"] == "in_progress"

    round_count = await db_session.scalar(select(func.count()).select_from(RoundORM))
    run_count = await db_session.scalar(select(func.count()).select_from(AgentEvaluationRunORM))
    task_count = await db_session.scalar(select(func.count()).select_from(TaskORM))
    solution_count = await db_session.scalar(select(func.count()).select_from(TaskSolutionORM))
    evaluation_count = await db_session.scalar(select(func.count()).select_from(EvaluationResultORM))
    assert round_count == 1
    assert run_count == 1
    assert task_count == 1
    assert solution_count == 1
    assert evaluation_count == 1


@pytest.mark.asyncio
async def test_round_submission_rejects_duplicate_round_numbers(client):
    base_payload = _make_submission_payload("150")
    first_response = await submit_round_via_validator_endpoints(client, base_payload)
    assert first_response.status_code == 200

    duplicate_payload = deepcopy(base_payload)
    duplicate_payload["round"]["validator_round_id"] = "round_150_dup"

    duplicate_payload["agent_evaluation_runs"][0]["validator_round_id"] = "round_150_dup"
    duplicate_payload["agent_evaluation_runs"][0]["agent_run_id"] = "agent_run_150_dup"
    duplicate_payload["agent_evaluation_runs"][0]["task_ids"] = [f"{task_id}_dup" for task_id in duplicate_payload["agent_evaluation_runs"][0]["task_ids"]]

    duplicate_payload["tasks"][0]["validator_round_id"] = "round_150_dup"
    duplicate_payload["tasks"][0]["task_id"] = "task_150_dup"

    duplicate_payload["task_solutions"][0]["validator_round_id"] = "round_150_dup"
    duplicate_payload["task_solutions"][0]["agent_run_id"] = "agent_run_150_dup"
    duplicate_payload["task_solutions"][0]["task_id"] = "task_150_dup"
    duplicate_payload["task_solutions"][0]["solution_id"] = "solution_150_dup"

    duplicate_payload["evaluation_results"][0]["validator_round_id"] = "round_150_dup"
    duplicate_payload["evaluation_results"][0]["agent_run_id"] = "agent_run_150_dup"
    duplicate_payload["evaluation_results"][0]["task_id"] = "task_150_dup"
    duplicate_payload["evaluation_results"][0]["task_solution_id"] = "solution_150_dup"
    duplicate_payload["evaluation_results"][0]["evaluation_id"] = "evaluation_150_dup"

    dup_response = await submit_round_via_validator_endpoints(client, duplicate_payload)
    assert dup_response.status_code == 409
    expected_detail = f"Validator {base_payload['round']['validator_info']['uid']} already has a round with number {base_payload['round']['round']}"
    assert dup_response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_start_round_is_idempotent_on_duplicate_round_numbers(client, monkeypatch):
    payload = _make_submission_payload("303")
    round_data = {**payload["round"]}
    start_payload = {
        "validator_round_id": round_data["validator_round_id"],
        "round": round_data,
    }
    # Patch chain for this round
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: inside_round(int(round_data["round"])),
    )

    first_start = await client.post("/api/v1/validator-rounds/start", json=start_payload)
    assert first_start.status_code == 200
    existing_id = first_start.json().get("validator_round_id")

    duplicate_round_data = {**round_data, "validator_round_id": "round_303_duplicate"}
    duplicate_payload = {
        "validator_round_id": duplicate_round_data["validator_round_id"],
        "round": duplicate_round_data,
    }

    duplicate_response = await client.post("/api/v1/validator-rounds/start", json=duplicate_payload)
    assert duplicate_response.status_code == 200
    dup_body = duplicate_response.json()
    assert dup_body.get("validator_round_id") == existing_id


@pytest.mark.asyncio
async def test_progressive_validator_flow(client, db_session, monkeypatch):
    payload = _make_submission_payload("202")
    round_data = payload["round"]
    validator_round_id = round_data["validator_round_id"]
    agent_run = payload["agent_evaluation_runs"][0]
    task = payload["tasks"][0]
    task_solution = payload["task_solutions"][0]
    evaluation = payload["evaluation_results"][0]

    # Patch chain to be inside this requested round
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: inside_round(int(round_data["round"])),
    )

    start_response = await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": validator_round_id, "round": round_data},
    )
    assert start_response.status_code == 200

    tasks_response = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/tasks",
        json={"tasks": [task]},
    )
    assert tasks_response.status_code == 200

    agent_run_response = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start",
        json={"agent_run": agent_run},
    )
    assert agent_run_response.status_code == 200

    evaluation_response = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run['agent_run_id']}/evaluations",
        json={
            "task": task,
            "task_solution": task_solution,
            "evaluation_result": evaluation,
        },
    )
    assert evaluation_response.status_code == 200

    finish_response = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/finish",
        json={
            "status": "completed",
            "winners": [{"miner_uid": agent_run["miner_uid"], "score": 0.92}],
            "winner_scores": [0.92],
            "weights": {"winner": 1.0},
            "ended_at": 1300.0,
            "summary": {"tasks": 1},
        },
    )
    assert finish_response.status_code == 200

    # Verify persistence state
    round_row = await db_session.scalar(select(RoundORM).where(RoundORM.validator_round_id == validator_round_id))
    assert round_row is not None
    assert round_row.data["status"] == "finished"
    assert round_row.data["summary"]["tasks"] == 1

    agent_run_row = await db_session.scalar(select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == agent_run["agent_run_id"]))
    assert agent_run_row is not None
    assert agent_run_row.validator_uid == agent_run["validator_uid"]

    task_row = await db_session.scalar(select(TaskORM).where(TaskORM.task_id == task["task_id"]))
    assert task_row is not None
    assert task_row.validator_round_id == validator_round_id

    solution_row = await db_session.scalar(select(TaskSolutionORM).where(TaskSolutionORM.solution_id == task_solution["solution_id"]))
    assert solution_row is not None
    assert solution_row.validator_uid == task_solution["validator_uid"]

    evaluation_row = await db_session.scalar(select(EvaluationResultORM).where(EvaluationResultORM.evaluation_id == evaluation["evaluation_id"]))
    assert evaluation_row is not None
    assert evaluation_row.data["evaluation_score"] == pytest.approx(evaluation["evaluation_score"])

    # Idempotency: repeat progressive calls with the same payloads
    again_start = await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": validator_round_id, "round": round_data},
    )
    assert again_start.status_code == 200
    assert again_start.json().get("validator_round_id") == validator_round_id

    again_tasks = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/tasks",
        json={"tasks": [task]},
    )
    assert again_tasks.status_code == 200

    again_agent_run = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start",
        json={"agent_run": agent_run},
    )
    assert again_agent_run.status_code == 200

    again_eval = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run['agent_run_id']}/evaluations",
        json={
            "task": task,
            "task_solution": task_solution,
            "evaluation_result": evaluation,
        },
    )
    assert again_eval.status_code == 200


@pytest.mark.asyncio
async def test_start_agent_run_idempotent_outside_window(client, monkeypatch):
    payload = _make_submission_payload("505")
    round_data = payload["round"]
    vrid = round_data["validator_round_id"]
    agent_run = payload["agent_evaluation_runs"][0]
    task = payload["tasks"][0]

    # Inside window for initial setup
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: inside_round(int(round_data["round"])),
    )

    # Start round and tasks
    start_response = await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": vrid, "round": round_data},
    )
    assert start_response.status_code == 200
    tasks_response = await client.post(f"/api/v1/validator-rounds/{vrid}/tasks", json={"tasks": [task]})
    assert tasks_response.status_code == 200

    # Start agent run inside window
    resp = await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/start",
        json={"agent_run": agent_run},
    )
    assert resp.status_code == 200

    # Move chain outside window and re-send the same agent_run → expect idempotent 200
    def outside_round(n: int) -> int:
        # Far past end of window
        return dz + n * blocks_per_round + blocks_per_round * 10

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: outside_round(int(round_data["round"])),
    )

    again = await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/start",
        json={"agent_run": agent_run},
    )
    assert again.status_code == 200


@pytest.mark.asyncio
async def test_add_evaluation_idempotent_outside_window(client, monkeypatch):
    payload = _make_submission_payload("606")
    round_data = payload["round"]
    vrid = round_data["validator_round_id"]
    agent_run = payload["agent_evaluation_runs"][0]
    task = payload["tasks"][0]
    task_solution = payload["task_solutions"][0]
    evaluation = payload["evaluation_results"][0]

    # Inside window for initial submission
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: inside_round(int(round_data["round"])),
    )

    await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": vrid, "round": round_data},
    )
    await client.post(f"/api/v1/validator-rounds/{vrid}/tasks", json={"tasks": [task]})
    await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/start",
        json={"agent_run": agent_run},
    )
    first = await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/{agent_run['agent_run_id']}/evaluations",
        json={
            "task": task,
            "task_solution": task_solution,
            "evaluation_result": evaluation,
        },
    )
    assert first.status_code == 200

    # Move chain outside window and re-send the same evaluation → expect idempotent 200
    def outside_round(n: int) -> int:
        return dz + n * blocks_per_round + blocks_per_round * 10

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: outside_round(int(round_data["round"])),
    )

    again = await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/{agent_run['agent_run_id']}/evaluations",
        json={
            "task": task,
            "task_solution": task_solution,
            "evaluation_result": evaluation,
        },
    )
    assert again.status_code == 200


@pytest.mark.asyncio
async def test_add_evaluation_partial_upsert_completes(client, db_session, monkeypatch):
    payload = _make_submission_payload("707")
    round_data = payload["round"]
    vrid = round_data["validator_round_id"]
    agent_run = payload["agent_evaluation_runs"][0]
    task = payload["tasks"][0]
    task_solution = payload["task_solutions"][0]
    evaluation = payload["evaluation_results"][0]

    # Inside window for the whole test
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: inside_round(int(round_data["round"])),
    )

    await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": vrid, "round": round_data},
    )
    await client.post(f"/api/v1/validator-rounds/{vrid}/tasks", json={"tasks": [task]})
    await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/start",
        json={"agent_run": agent_run},
    )
    first = await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/{agent_run['agent_run_id']}/evaluations",
        json={
            "task": task,
            "task_solution": task_solution,
            "evaluation_result": evaluation,
        },
    )
    assert first.status_code == 200

    # Delete only the task_solution row to simulate a partial crash
    await db_session.execute(
        TaskSolutionORM.__table__.delete().where(TaskSolutionORM.solution_id == task_solution["solution_id"])  # type: ignore[attr-defined]
    )
    # Remove the solution (harder case): now re-post should recreate solution, evaluation, and result

    second = await client.post(
        f"/api/v1/validator-rounds/{vrid}/agent-runs/{agent_run['agent_run_id']}/evaluations",
        json={
            "task": task,
            "task_solution": task_solution,
            "evaluation_result": evaluation,
        },
    )
    assert second.status_code == 200

    # Verify rows exist again
    sol_row = await db_session.scalar(select(TaskSolutionORM).where(TaskSolutionORM.solution_id == task_solution["solution_id"]))
    assert sol_row is not None
    eval_row = await db_session.scalar(select(EvaluationResultORM).where(EvaluationResultORM.evaluation_id == evaluation["evaluation_id"]))
    assert eval_row is not None


@pytest.mark.asyncio
async def test_finish_round_idempotent_repeat(client, monkeypatch, db_session):
    payload = _make_submission_payload("808")
    round_data = payload["round"]
    vrid = round_data["validator_round_id"]

    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.validator.validator_round.get_current_block",
        lambda: inside_round(int(round_data["round"])),
    )

    start_response = await client.post(
        "/api/v1/validator-rounds/start",
        json={"validator_round_id": vrid, "round": round_data},
    )
    assert start_response.status_code == 200

    first_finish = await client.post(
        f"/api/v1/validator-rounds/{vrid}/finish",
        json={
            "status": "completed",
            "winners": [],
            "winner_scores": [],
            "weights": {},
            "ended_at": 9999.0,
            "summary": {"tasks": 0},
        },
    )
    assert first_finish.status_code == 200

    # Call finish again: expect idempotent 200 and same ended_at in DB
    second_finish = await client.post(
        f"/api/v1/validator-rounds/{vrid}/finish",
        json={
            "status": "completed",
            "winners": [],
            "winner_scores": [],
            "weights": {},
            "ended_at": 10000.0,
            "summary": {"tasks": 0},
        },
    )
    assert second_finish.status_code == 200
    round_row = await db_session.scalar(select(RoundORM).where(RoundORM.validator_round_id == vrid))
    assert round_row is not None
    assert float(round_row.ended_at) == pytest.approx(9999.0)


@pytest.mark.asyncio
async def test_rounds_endpoint_returns_data(client, monkeypatch):
    payload = _make_submission_payload("303")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    # Ensure chain thinks the round has started when listing
    from app.config import settings as _settings

    blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
    dz = int(_settings.DZ_STARTING_BLOCK)

    def inside_round(n: int) -> int:
        return dz + (n - 1) * blocks_per_round + 1

    monkeypatch.setattr(
        "app.api.ui.rounds.get_current_block",
        lambda: inside_round(int(payload["round"]["round"])),
    )

    response = await client.get("/api/v1/rounds/?limit=10&skip=0")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert any(any(validator_round["validatorRoundId"] == payload["round"]["validator_round_id"] for validator_round in item.get("validatorRounds", [])) for item in body)


@pytest.mark.asyncio
async def test_round_detail_and_agent_run_endpoints(client):
    payload = _make_submission_payload("404")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    validator_round_id = payload["round"]["validator_round_id"]
    agent_run_id = payload["agent_evaluation_runs"][0]["agent_run_id"]

    detail_response = await client.get(f"/api/v1/rounds/{validator_round_id}")
    assert detail_response.status_code == 200
    detail_body = detail_response.json()
    assert detail_body["success"] is True
    round_payload = detail_body["data"]["round"]
    assert round_payload["round"] == payload["round"]["round"]
    validator_entry = next(
        (entry for entry in round_payload.get("validatorRounds", []) if entry["validatorRoundId"] == validator_round_id),
        None,
    )
    assert validator_entry is not None
    assert len(validator_entry.get("agentEvaluationRuns", [])) == 1
    detail_run = validator_entry["agentEvaluationRuns"][0]
    assert detail_run["agent_run_id"] == agent_run_id
    assert len(detail_run["tasks"]) == 1
    assert len(detail_run["task_solutions"]) == 1
    assert len(detail_run["evaluation_results"]) == 1

    list_response = await client.get(f"/api/v1/rounds/{validator_round_id}/agent-runs")
    assert list_response.status_code == 200
    runs = list_response.json()
    assert len(runs) == 1
    assert runs[0]["agent_run_id"] == agent_run_id
    assert runs[0]["tasks"]  # tasks included for details

    run_response = await client.get(f"/api/v1/rounds/agent-runs/{agent_run_id}")
    assert run_response.status_code == 200
    run_detail = run_response.json()
    assert run_detail["agent_run_id"] == agent_run_id
    assert len(run_detail["tasks"]) == 1


@pytest.mark.asyncio
async def test_round_detail_accepts_numeric_identifier(client):
    payload = _make_submission_payload("606")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    validator_round_id = payload["round"]["validator_round_id"]
    numeric_identifier = int(validator_round_id.split("_")[1])

    response = await client.get(f"/api/v1/rounds/{numeric_identifier}")
    assert response.status_code == 200
    detail_body = response.json()
    assert detail_body["success"] is True
    round_payload = detail_body["data"]["round"]
    assert round_payload["round"] == numeric_identifier
    assert any(entry["validatorRoundId"] == validator_round_id for entry in round_payload.get("validatorRounds", []))


@pytest.mark.asyncio
async def test_agent_runs_endpoints(client):
    payload = _make_submission_payload("505")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    run_id = payload["agent_evaluation_runs"][0]["agent_run_id"]

    list_response = await client.get("/api/v1/agent-runs")
    assert list_response.status_code == 200
    data = list_response.json()
    assert data["success"] is True
    assert any(run["runId"] == run_id for run in data["data"]["runs"])

    detail_response = await client.get(f"/api/v1/agent-runs/{run_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["success"] is True
    assert detail["data"]["run"]["runId"] == run_id

    personas_response = await client.get(f"/api/v1/agent-runs/{run_id}/personas")
    assert personas_response.status_code == 200
    personas = personas_response.json()
    assert personas["data"]["personas"]["round"]["id"] > 0

    stats_response = await client.get(f"/api/v1/agent-runs/{run_id}/stats")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["data"]["stats"]["totalTasks"] >= 1

    summary_response = await client.get(f"/api/v1/agent-runs/{run_id}/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["data"]["summary"]["runId"] == run_id

    tasks_response = await client.get(f"/api/v1/agent-runs/{run_id}/tasks")
    assert tasks_response.status_code == 200
    tasks = tasks_response.json()
    assert len(tasks["data"]["tasks"]) >= 1

    timeline_response = await client.get(f"/api/v1/agent-runs/{run_id}/timeline")
    assert timeline_response.status_code == 200
    timeline = timeline_response.json()
    assert len(timeline["data"]["timeline"]) >= 1

    logs_response = await client.get(f"/api/v1/agent-runs/{run_id}/logs")
    assert logs_response.status_code == 200
    logs = logs_response.json()
    assert logs["success"] is True

    metrics_response = await client.get(f"/api/v1/agent-runs/{run_id}/metrics")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["success"] is True

    compare_response = await client.post(
        "/api/v1/agent-runs/compare",
        json={"runIds": [run_id]},
    )
    assert compare_response.status_code == 200
    compare = compare_response.json()
    assert compare["success"] is True
    assert compare["data"]["runs"]

    missing_response = await client.get("/api/v1/agent-runs/non-existent")
    assert missing_response.status_code == 404
    error = missing_response.json()
    assert error["detail"]["code"] == "AGENT_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_evaluations_endpoints(client):
    payload = _make_submission_payload("606")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    evaluation_id = payload["evaluation_results"][0]["evaluation_id"]
    run_id = payload["agent_evaluation_runs"][0]["agent_run_id"]

    list_response = await client.get("/api/v1/evaluations")
    assert list_response.status_code == 200
    evaluations = list_response.json()
    assert evaluations["success"] is True
    assert any(item["evaluationId"] == evaluation_id for item in evaluations["data"]["evaluations"])

    detail_response = await client.get(f"/api/v1/evaluations/{evaluation_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["success"] is True
    evaluation = detail["data"]["evaluation"]
    assert evaluation["evaluationId"] == evaluation_id
    assert evaluation["runId"] == run_id
    assert evaluation["task"]


@pytest.mark.asyncio
async def test_tasks_endpoints(client):
    payload = _make_submission_payload("707")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    task_id = payload["tasks"][0]["task_id"]

    list_response = await client.get("/api/v1/tasks")
    assert list_response.status_code == 200
    tasks = list_response.json()
    assert tasks["success"] is True
    assert any(task["taskId"] == task_id for task in tasks["data"]["tasks"])

    search_response = await client.get(f"/api/v1/tasks/search?query={task_id}")
    assert search_response.status_code == 200
    search = search_response.json()
    assert search["success"] is True
    assert search["data"]["facets"]["websites"]

    detail_response = await client.get(f"/api/v1/tasks/{task_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["success"] is True
    assert detail["data"]["task"]["taskId"] == task_id

    details_response = await client.get(f"/api/v1/tasks/{task_id}/details")
    assert details_response.status_code == 200

    personas_response = await client.get(f"/api/v1/tasks/{task_id}/personas")
    assert personas_response.status_code == 200
    personas = personas_response.json()
    assert personas["success"] is True
    assert personas["data"]["personas"]["task"]["id"] == task_id

    statistics_response = await client.get(f"/api/v1/tasks/{task_id}/statistics")
    assert statistics_response.status_code == 200
    statistics = statistics_response.json()
    assert statistics["success"] is True

    actions_response = await client.get(f"/api/v1/tasks/{task_id}/actions")
    assert actions_response.status_code == 200
    actions = actions_response.json()
    assert actions["success"] is True

    screenshots_response = await client.get(f"/api/v1/tasks/{task_id}/screenshots")
    assert screenshots_response.status_code == 200
    screenshots = screenshots_response.json()
    assert screenshots["success"] is True

    logs_response = await client.get(f"/api/v1/tasks/{task_id}/logs")
    assert logs_response.status_code == 200
    logs = logs_response.json()
    assert logs["success"] is True

    metrics_response = await client.get(f"/api/v1/tasks/{task_id}/metrics")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["success"] is True

    results_response = await client.get(f"/api/v1/tasks/{task_id}/results")
    assert results_response.status_code == 200


@pytest.mark.asyncio
async def test_agents_endpoints(client):
    payload = _make_submission_payload("808")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    miner_uid = payload["agent_evaluation_runs"][0]["miner_uid"]
    agent_id = f"agent-{miner_uid}"

    list_response = await client.get("/api/v1/agents")
    assert list_response.status_code == 200
    agents = list_response.json()
    assert agents["success"] is True
    assert any(agent["id"] == agent_id for agent in agents["data"]["agents"])

    detail_response = await client.get(f"/api/v1/agents/{agent_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["success"] is True
    assert detail["data"]["agent"]["id"] == agent_id

    runs_response = await client.get(f"/api/v1/agents/{agent_id}/runs")
    assert runs_response.status_code == 200
    runs = runs_response.json()
    assert runs["success"] is True
    assert len(runs["data"]["runs"]) >= 1

    performance_response = await client.get(f"/api/v1/agents/{agent_id}/performance")
    assert performance_response.status_code == 200
    performance = performance_response.json()
    assert performance["success"] is True
    assert performance["data"]["metrics"]["agentId"] == agent_id

    activity_response = await client.get(f"/api/v1/agents/{agent_id}/activity")
    assert activity_response.status_code == 200
    activity = activity_response.json()
    assert activity["success"] is True

    stats_response = await client.get("/api/v1/agents/statistics")
    assert stats_response.status_code == 200
    stats = stats_response.json()
    assert stats["data"]["statistics"]["totalAgents"] >= 1

    all_activity_response = await client.get("/api/v1/agents/activity")
    assert all_activity_response.status_code == 200
    all_activity = all_activity_response.json()
    assert all_activity["success"] is True

    compare_response = await client.post(
        "/api/v1/agents/compare",
        json={"agentIds": [agent_id]},
    )
    assert compare_response.status_code == 200
    compare = compare_response.json()
    assert compare["success"] is True
    assert compare["data"]["agents"]


@pytest.mark.asyncio
async def test_miners_endpoints(client):
    payload = _make_submission_payload("909")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    miner_uid = payload["agent_evaluation_runs"][0]["miner_uid"]

    list_response = await client.get("/api/v1/miners")
    assert list_response.status_code == 200
    miners = list_response.json()
    assert miners["success"] is True
    assert any(miner["uid"] == miner_uid for miner in miners["data"]["miners"])

    detail_response = await client.get(f"/api/v1/miners/{miner_uid}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["success"] is True
    assert detail["data"]["miner"]["uid"] == miner_uid


@pytest.mark.asyncio
async def test_overview_endpoints(client):
    payload = _make_submission_payload("1010")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    metrics_response = await client.get("/api/v1/overview")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["success"] is True
    assert metrics["data"]["metrics"]["totalValidators"] >= 1
    statistics_response = await client.get("/api/v1/overview/statistics")
    assert statistics_response.status_code == 200
    statistics = statistics_response.json()
    assert "networkUptime" in statistics["data"]["statistics"]

    validators_response = await client.get("/api/v1/overview/validators")
    assert validators_response.status_code == 200
    validators = validators_response.json()
    assert validators["success"] is True
    assert validators["data"]["total"] >= 1

    rounds_response = await client.get("/api/v1/overview/rounds")
    assert rounds_response.status_code == 200
    rounds = rounds_response.json()
    assert rounds["success"] is True
    assert rounds["data"]["total"] >= 1

    leaderboard_response = await client.get("/api/v1/overview/leaderboard")
    assert leaderboard_response.status_code == 200
    leaderboard = leaderboard_response.json()
    assert leaderboard["success"] is True
    assert len(leaderboard["data"]["leaderboard"]) >= 1
    assert "timeRange" in leaderboard["data"]

    # Test with "7D" (legacy format - still supported)
    leaderboard_7d_response = await client.get("/api/v1/overview/leaderboard", params={"timeRange": "7D"})
    assert leaderboard_7d_response.status_code == 200
    leaderboard_7d = leaderboard_7d_response.json()
    assert leaderboard_7d["success"] is True
    assert len(leaderboard_7d["data"]["leaderboard"]) <= 7

    # Test with "7R" (new rounds format)
    leaderboard_7r_response = await client.get("/api/v1/overview/leaderboard", params={"timeRange": "7R"})
    assert leaderboard_7r_response.status_code == 200
    leaderboard_7r = leaderboard_7r_response.json()
    assert leaderboard_7r["success"] is True
    assert len(leaderboard_7r["data"]["leaderboard"]) <= 7

    leaderboard_all_response = await client.get("/api/v1/overview/leaderboard", params={"timeRange": "all"})
    assert leaderboard_all_response.status_code == 200
    leaderboard_all = leaderboard_all_response.json()
    assert leaderboard_all["success"] is True
    assert len(leaderboard_all["data"]["leaderboard"]) >= len(leaderboard_7d["data"]["leaderboard"])

    filter_response = await client.get("/api/v1/overview/validators/filter")
    assert filter_response.status_code == 200
    validator_filter = filter_response.json()
    assert validator_filter["success"] is True
    assert validator_filter["data"]["validators"]


@pytest.mark.asyncio
async def test_overview_metrics_uses_previous_round_when_current_active(client):
    # Seed two completed rounds to provide historical context.
    payload_round_1 = _make_submission_payload("201")
    response_round_1 = await submit_round_via_validator_endpoints(client, payload_round_1)
    assert response_round_1.status_code == 200

    payload_round_2 = _make_submission_payload("202")
    response_round_2 = await submit_round_via_validator_endpoints(client, payload_round_2)
    assert response_round_2.status_code == 200

    # Seed a new active round by omitting ended_at so it remains running.
    payload_round_3 = _make_submission_payload("203")
    payload_round_3["round"]["ended_at"] = None
    payload_round_3["round"]["status"] = "active"
    payload_round_3["agent_evaluation_runs"][0]["ended_at"] = None
    response_round_3 = await submit_round_via_validator_endpoints(client, payload_round_3)
    assert response_round_3.status_code == 200

    metrics_response = await client.get("/api/v1/overview")
    assert metrics_response.status_code == 200
    metrics_payload = metrics_response.json()
    assert metrics_payload["success"] is True

    metrics = metrics_payload["data"]["metrics"]
    assert metrics["currentRound"] == 203
    assert metrics["metricsRound"] == 202
    assert metrics["metricsRound"] == metrics["currentRound"] - 1


@pytest.mark.asyncio
async def test_overview_metrics_does_not_encode_round_with_season_prefix(client):
    payload_round_8 = _make_submission_payload("008")
    response_round_8 = await submit_round_via_validator_endpoints(client, payload_round_8)
    assert response_round_8.status_code == 200

    payload_round_9 = _make_submission_payload("009")
    payload_round_9["round"]["ended_at"] = None
    payload_round_9["round"]["status"] = "active"
    payload_round_9["agent_evaluation_runs"][0]["ended_at"] = None
    response_round_9 = await submit_round_via_validator_endpoints(client, payload_round_9)
    assert response_round_9.status_code == 200

    metrics_response = await client.get("/api/v1/overview")
    assert metrics_response.status_code == 200
    metrics_payload = metrics_response.json()
    assert metrics_payload["success"] is True

    metrics = metrics_payload["data"]["metrics"]
    assert metrics["currentSeason"] == 2
    assert metrics["currentRound"] == 1
    assert metrics["currentRoundInSeason"] == 1
    assert metrics["currentRound"] != 20001


@pytest.mark.asyncio
async def test_miner_list_endpoints(client):
    payload = _make_submission_payload("1111")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    miner_uid = payload["agent_evaluation_runs"][0]["miner_uid"]
    round_number = payload["round"]["round"]

    list_response = await client.get("/api/v1/miner-list")
    assert list_response.status_code == 200
    miner_list = list_response.json()
    assert miner_list["round"] == round_number
    assert any(item["uid"] == miner_uid for item in miner_list["miners"])

    detail_response = await client.get(f"/api/v1/miner-list/{miner_uid}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["miner"]["uid"] == miner_uid


@pytest.mark.asyncio
async def test_miner_list_falls_back_to_latest_round(client):
    payload = _make_submission_payload("1212")
    submit_response = await submit_round_via_validator_endpoints(client, payload)
    assert submit_response.status_code == 200

    miner_uid = payload["agent_evaluation_runs"][0]["miner_uid"]
    expected_round = payload["round"]["round"]

    list_response = await client.get("/api/v1/miner-list", params={"round": 9999})
    assert list_response.status_code == 200
    miner_list = list_response.json()

    assert miner_list["round"] == expected_round
    assert any(item["uid"] == miner_uid for item in miner_list["miners"])


@pytest.mark.asyncio
async def test_subnet_timeline(client):
    payload = _make_submission_payload("401")
    submission = await submit_round_via_validator_endpoints(client, payload)
    assert submission.status_code == 200

    response = await client.get("/api/v1/subnets/subnet-1/timeline")
    assert response.status_code == 200
    timeline = response.json()
    assert "timeline" in timeline
    assert timeline["timeline"]
