"""
Progressive validator round ingestion endpoints.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.db.mock_mongo import get_mock_db
from app.models.schemas import (
    Round,
    Task,
    AgentEvaluationRun,
    TaskSolution,
    EvaluationResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/validator-rounds", tags=["validator-rounds"])


def _require_non_empty(value: str, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    trimmed = str(value).strip()
    if not trimmed:
        raise ValueError(f"{field_name} cannot be blank")
    return trimmed


class StartRoundRequest(BaseModel):
    validator_round_id: str = Field(..., description="External validator round identifier")
    round: Round

    @field_validator("validator_round_id")
    @classmethod
    def _ensure_id(cls, value: str) -> str:
        return _require_non_empty(value, "validator_round_id")


class SetTasksRequest(BaseModel):
    tasks: list[Task] = Field(default_factory=list)


class StartAgentRunRequest(BaseModel):
    agent_run: AgentEvaluationRun


class AddEvaluationRequest(BaseModel):
    task: Task
    task_solution: TaskSolution
    evaluation_result: EvaluationResult


class FinishRoundRequest(BaseModel):
    status: str = Field(default="completed", description="Final status for the round")
    winners: list[Dict[str, Any]] = Field(default_factory=list)
    winner_scores: list[float] = Field(default_factory=list)
    weights: Dict[str, float] = Field(default_factory=dict)
    ended_at: float | None = Field(default=None, description="Epoch timestamp when the round finished")
    summary: Dict[str, int] | None = Field(default=None, description="Optional summary metadata")


async def _get_round_by_validator_round_id(validator_round_id: str) -> Dict[str, Any]:
    db = get_mock_db()
    round_doc = await db.rounds.find_one({"validator_round_id": validator_round_id})
    if not round_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Validator round {validator_round_id} not found",
        )
    return round_doc


@router.post("/start")
async def start_round(payload: StartRoundRequest):
    """Register a new validator round."""
    db = get_mock_db()
    existing = await db.rounds.find_one({"validator_round_id": payload.validator_round_id})
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Validator round {payload.validator_round_id} already exists",
        )

    round_doc = payload.round.model_dump()
    round_doc["validator_round_id"] = payload.validator_round_id
    round_doc.setdefault("status", "in_progress")
    round_doc.setdefault("started_at", round_doc.get("started_at", time.time()))

    await db.rounds.insert_one(round_doc)
    logger.info("Started validator round %s for round %s", payload.validator_round_id, payload.round.round_id)
    return {"message": "Validator round created", "validator_round_id": payload.validator_round_id, "round_id": payload.round.round_id}


@router.post("/{validator_round_id}/tasks")
async def set_tasks(validator_round_id: str, payload: SetTasksRequest):
    """Add or replace tasks definitions for a validator round."""
    db = get_mock_db()
    round_doc = await _get_round_by_validator_round_id(validator_round_id)

    tasks_saved = 0
    for task in payload.tasks:
        if task.round_id != round_doc["round_id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Task {task.task_id} round mismatch",
            )

        task_data = task.model_dump()
        if not task_data.get("agent_run_id"):
            task_data["agent_run_id"] = None
        task_data["validator_round_id"] = validator_round_id

        await db.tasks.update_one(
            {"task_id": task.task_id},
            {"$set": task_data},
            upsert=True,
        )
        tasks_saved += 1

    logger.info(
        "Set %d task definitions for validator round %s",
        tasks_saved,
        validator_round_id,
    )
    return {"message": "Tasks stored", "count": tasks_saved}


@router.post("/{validator_round_id}/agent-runs/start")
async def start_agent_run(validator_round_id: str, payload: StartAgentRunRequest):
    """Register the beginning of an agent run."""
    db = get_mock_db()
    round_doc = await _get_round_by_validator_round_id(validator_round_id)
    agent_run = payload.agent_run

    if agent_run.round_id != round_doc["round_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent run {agent_run.agent_run_id} round mismatch",
        )

    existing = await db.agent_evaluation_runs.find_one({"agent_run_id": agent_run.agent_run_id})
    if existing and existing.get("validator_round_id") != validator_round_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent run {agent_run.agent_run_id} already registered for another validator round",
        )

    agent_run_doc = agent_run.model_dump()
    agent_run_doc["validator_round_id"] = validator_round_id

    await db.agent_evaluation_runs.update_one(
        {"agent_run_id": agent_run.agent_run_id},
        {"$set": agent_run_doc},
        upsert=True,
    )
    logger.info(
        "Registered agent run %s for validator round %s",
        agent_run.agent_run_id,
        validator_round_id,
    )
    return {"message": "Agent run registered", "agent_run_id": agent_run.agent_run_id}


@router.post("/{validator_round_id}/agent-runs/{agent_run_id}/evaluations")
async def add_evaluation(validator_round_id: str, agent_run_id: str, payload: AddEvaluationRequest):
    """Add evaluation results for a specific agent run and task."""
    db = get_mock_db()
    round_doc = await _get_round_by_validator_round_id(validator_round_id)
    agent_run_doc = await db.agent_evaluation_runs.find_one({"agent_run_id": agent_run_id})
    if not agent_run_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {agent_run_id} not found",
        )
    if agent_run_doc.get("validator_round_id") != validator_round_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent run {agent_run_id} is not associated with validator round {validator_round_id}",
        )

    task = payload.task
    solution = payload.task_solution
    evaluation = payload.evaluation_result

    expected_round_id = round_doc["round_id"]
    if task.round_id != expected_round_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task round mismatch")
    if solution.round_id != expected_round_id or evaluation.round_id != expected_round_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Round mismatch in solution or evaluation")

    # Enforce agent run linkage
    task_agent_run_id = task.agent_run_id or agent_run_id
    if task_agent_run_id != agent_run_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task agent_run_id mismatch")

    if solution.agent_run_id != agent_run_id or evaluation.agent_run_id != agent_run_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agent_run_id mismatch in solution or evaluation")

    if solution.task_id != task.task_id or evaluation.task_id != task.task_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id mismatch across payload")

    if evaluation.task_solution_id != solution.solution_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="evaluation references unknown task solution")

    validator_uid = agent_run_doc.get("validator_uid")
    if solution.validator_uid != validator_uid or evaluation.validator_uid != validator_uid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="validator_uid mismatch")

    miner_uid = agent_run_doc.get("miner_uid")
    if solution.miner_uid != miner_uid or evaluation.miner_uid != miner_uid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="miner_uid mismatch")

    task_doc = task.model_dump()
    task_doc["agent_run_id"] = agent_run_id
    task_doc["validator_round_id"] = validator_round_id

    await db.tasks.update_one(
        {"task_id": task.task_id},
        {"$set": task_doc},
        upsert=True,
    )

    solution_doc = solution.model_dump()
    solution_doc["validator_round_id"] = validator_round_id
    await db.task_solutions.update_one(
        {"solution_id": solution.solution_id},
        {"$set": solution_doc},
        upsert=True,
    )

    evaluation_doc = evaluation.model_dump()
    evaluation_doc["validator_round_id"] = validator_round_id
    await db.evaluation_results.update_one(
        {"evaluation_id": evaluation.evaluation_id},
        {"$set": evaluation_doc},
        upsert=True,
    )

    logger.info(
        "Stored evaluation %s for task %s (agent run %s)",
        evaluation.evaluation_id,
        task.task_id,
        agent_run_id,
    )

    return {"message": "Evaluation stored", "evaluation_id": evaluation.evaluation_id}


@router.post("/{validator_round_id}/finish")
async def finish_round(validator_round_id: str, payload: FinishRoundRequest):
    """Mark a validator round as finished and persist summary data."""
    db = get_mock_db()
    round_doc = await _get_round_by_validator_round_id(validator_round_id)

    update_fields: Dict[str, Any] = {
        "status": payload.status,
        "winners": payload.winners,
        "winner_scores": payload.winner_scores,
        "weights": payload.weights,
        "ended_at": payload.ended_at or time.time(),
        "n_winners": len(payload.winners),
    }
    if payload.summary is not None:
        update_fields["summary"] = payload.summary

    await db.rounds.update_one(
        {"validator_round_id": validator_round_id},
        {"$set": update_fields},
        upsert=False,
    )
    logger.info(
        "Finished validator round %s (round %s)",
        validator_round_id,
        round_doc.get("round_id"),
    )
    return {"message": "Validator round finalized", "validator_round_id": validator_round_id}
