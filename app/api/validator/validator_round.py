"""
Progressive validator round ingestion endpoints.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.core import (
    Round,
    Task,
    AgentEvaluationRun,
    TaskSolution,
    EvaluationResult,
)
from app.services.validator_storage import RoundPersistenceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/validator-rounds", tags=["validator-rounds"])


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


@router.post("/start")
async def start_round(
    payload: StartRoundRequest,
    session: AsyncSession = Depends(get_session),
):
    """Register a new validator round."""
    service = RoundPersistenceService(session)

    round_model = payload.round
    if round_model.validator_round_id != payload.validator_round_id:
        round_model = round_model.model_copy(update={"validator_round_id": payload.validator_round_id})

    async with session.begin():
        existing = await service.get_round(payload.validator_round_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Validator round {payload.validator_round_id} already exists",
            )

        try:
            round_row, validator_uid = await service.ensure_round(round_model)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    logger.info(
        "Started validator round %s for round %s (validator_uid=%s)",
        payload.validator_round_id,
        round_model.validator_round_id,
        validator_uid,
    )
    return {"message": "Validator round created", "validator_round_id": round_model.validator_round_id}


@router.post("/{validator_round_id}/tasks")
async def set_tasks(
    validator_round_id: str,
    payload: SetTasksRequest,
    session: AsyncSession = Depends(get_session),
):
    """Add or replace tasks definitions for a validator round."""
    service = RoundPersistenceService(session)

    async with session.begin():
        round_row = await service.get_round(validator_round_id)
        if not round_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Validator round {validator_round_id} not found",
            )

        expected_round_id = round_row.validator_round_id
        tasks_saved = 0
        for task in payload.tasks:
            if task.validator_round_id != expected_round_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Task {task.task_id} round mismatch",
                )

            await service.upsert_task_entry(task)
            tasks_saved += 1

    logger.info(
        "Set %d task definitions for validator round %s",
        tasks_saved,
        validator_round_id,
    )
    return {"message": "Tasks stored", "count": tasks_saved}


@router.post("/{validator_round_id}/agent-runs/start")
async def start_agent_run(
    validator_round_id: str,
    payload: StartAgentRunRequest,
    session: AsyncSession = Depends(get_session),
):
    """Register the beginning of an agent run."""
    service = RoundPersistenceService(session)

    async with session.begin():
        round_row = await service.get_round(validator_round_id)
        if not round_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Validator round {validator_round_id} not found",
            )

        agent_run = payload.agent_run
        if agent_run.validator_round_id != round_row.validator_round_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Agent run {agent_run.agent_run_id} round mismatch",
            )

        existing = await service.get_agent_run(agent_run.agent_run_id)
        if existing and existing.validator_round_id != validator_round_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Agent run {agent_run.agent_run_id} already registered for another validator round",
            )

        await service.upsert_agent_run_entry(round_row, agent_run)

    logger.info(
        "Registered agent run %s for validator round %s",
        agent_run.agent_run_id,
        validator_round_id,
    )
    return {"message": "Agent run registered", "agent_run_id": agent_run.agent_run_id}


@router.post("/{validator_round_id}/agent-runs/{agent_run_id}/evaluations")
async def add_evaluation(
    validator_round_id: str,
    agent_run_id: str,
    payload: AddEvaluationRequest,
    session: AsyncSession = Depends(get_session),
):
    """Add evaluation results for a specific agent run and task."""
    service = RoundPersistenceService(session)

    async with session.begin():
        round_row = await service.get_round(validator_round_id)
        if not round_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Validator round {validator_round_id} not found",
            )

        agent_run_row = await service.get_agent_run(agent_run_id)
        if not agent_run_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent run {agent_run_id} not found",
            )
        if agent_run_row.validator_round_id != validator_round_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Agent run {agent_run_id} is not associated with validator round {validator_round_id}",
            )

        agent_run_obj = AgentEvaluationRun(**agent_run_row.data)

        task = payload.task
        solution = payload.task_solution
        evaluation = payload.evaluation_result

        expected_validator_round_id = round_row.validator_round_id
        if task.validator_round_id != expected_validator_round_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task round mismatch")
        if (
            solution.validator_round_id != expected_validator_round_id
            or evaluation.validator_round_id != expected_validator_round_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Round mismatch in solution or evaluation"
            )

        # Enforce agent run linkage
        task_agent_run_id = task.agent_run_id or agent_run_id
        if task_agent_run_id != agent_run_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task agent_run_id mismatch")

        if solution.agent_run_id != agent_run_id or evaluation.agent_run_id != agent_run_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="agent_run_id mismatch in solution or evaluation"
            )

        if solution.task_id != task.task_id or evaluation.task_id != task.task_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id mismatch across payload")

        if evaluation.task_solution_id != solution.solution_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="evaluation references unknown task solution"
            )

        validator_uid = agent_run_obj.validator_uid
        if solution.validator_uid != validator_uid or evaluation.validator_uid != validator_uid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="validator_uid mismatch")

        miner_uid = agent_run_obj.miner_uid
        if solution.miner_uid != miner_uid or evaluation.miner_uid != miner_uid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="miner_uid mismatch")

        task_to_store = task if task.agent_run_id == agent_run_id else task.model_copy(update={"agent_run_id": agent_run_id})
        await service.upsert_task_entry(task_to_store)
        await service.upsert_task_solution_entry(solution)
        await service.upsert_evaluation_entry(evaluation)

    logger.info(
        "Stored evaluation %s for task %s (agent run %s)",
        payload.evaluation_result.evaluation_id,
        payload.task.task_id,
        agent_run_id,
    )

    return {"message": "Evaluation stored", "evaluation_id": payload.evaluation_result.evaluation_id}


@router.post("/{validator_round_id}/finish")
async def finish_round(
    validator_round_id: str,
    payload: FinishRoundRequest,
    session: AsyncSession = Depends(get_session),
):
    """Mark a validator round as finished and persist summary data."""
    service = RoundPersistenceService(session)

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

    async with session.begin():
        round_row = await service.update_round_fields(validator_round_id, **update_fields)

    logger.info(
        "Finished validator round %s (round %s)",
        validator_round_id,
        round_row.validator_round_id,
    )
    return {"message": "Validator round finalized", "validator_round_id": validator_round_id}
