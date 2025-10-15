from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.core import AgentEvaluationRunWithDetails, RoundWithDetails
from app.services.rounds_service import RoundsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds"])


@router.get("/", response_model=List[RoundWithDetails])
async def list_rounds(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
) -> List[RoundWithDetails]:
    """Return recent rounds stored in the database."""
    service = RoundsService(session)
    try:
        rounds = await service.list_rounds(limit=limit, skip=skip)
        logger.info("Fetched %d rounds (limit=%d, skip=%d)", len(rounds), limit, skip)
        return rounds
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to list rounds: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch rounds") from exc


@router.get("/{validator_round_id}", response_model=RoundWithDetails)
async def get_round(
    validator_round_id: str,
    session: AsyncSession = Depends(get_session),
) -> RoundWithDetails:
    service = RoundsService(session)
    try:
        return await service.get_round(validator_round_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch round %s: %s", validator_round_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch round") from exc


@router.get(
    "/{validator_round_id}/agent-runs",
    response_model=List[AgentEvaluationRunWithDetails],
)
async def list_round_agent_runs(
    validator_round_id: str,
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
) -> List[AgentEvaluationRunWithDetails]:
    service = RoundsService(session)
    try:
        return await service.list_agent_runs(
            validator_round_id=validator_round_id,
            limit=limit,
            skip=skip,
            include_details=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Failed to list agent runs for round %s: %s",
            validator_round_id,
            exc,
        )
        raise HTTPException(status_code=500, detail="Failed to fetch agent runs") from exc


@router.get(
    "/agent-runs/{agent_run_id}",
    response_model=AgentEvaluationRunWithDetails,
)
async def get_agent_run(
    agent_run_id: str,
    session: AsyncSession = Depends(get_session),
) -> AgentEvaluationRunWithDetails:
    service = RoundsService(session)
    try:
        return await service.get_agent_run(agent_run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to fetch agent run %s: %s", agent_run_id, exc)
        raise HTTPException(status_code=500, detail="Failed to fetch agent run") from exc
