"""Evaluation-focused UI endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models.ui.agent_runs import Action, Log, LogLevel
from app.models.ui.evaluations import (
    EvaluationDetail,
    EvaluationDetailResponse,
    EvaluationListItem,
    EvaluationListResponse,
    EvaluationStatus,
    EvaluationTaskInfo,
)
from app.services.ui.data_adapter import EvaluationContext, UIDataAdapter, _fmt, _round_number

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


async def _adapter(session: AsyncSession) -> UIDataAdapter:
    return UIDataAdapter(session)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _primary_score(context: EvaluationContext) -> float:
    return context.score


def _reward(context: EvaluationContext) -> float:
    result = context.evaluation.evaluation_result
    if result.reward is not None:
        return float(result.reward)
    if result.score is not None:
        return float(result.score)
    return 0.0


def _response_time(context: EvaluationContext) -> float:
    result = context.evaluation.evaluation_result
    if result.response_time is not None:
        return float(result.response_time)
    metrics = result.metrics or {}
    for key in ("response_time", "responseTime", "latency", "latency_ms"):
        value = metrics.get(key)
        if value is not None:
            try:
                value_float = float(value)
                if key == "latency_ms" and value_float > 10:
                    return value_float / 1000.0
                return value_float
            except (TypeError, ValueError):
                continue
    return 0.0


def _status(context: EvaluationContext) -> EvaluationStatus:
    score = _primary_score(context)
    if score >= 0.5:
        return EvaluationStatus.PASSED
    if score > 0:
        return EvaluationStatus.FAILED
    return EvaluationStatus.PENDING


def _created_at(context: EvaluationContext) -> Optional[str]:
    created = _aware(context.created_at)
    if created is None and context.run.started_at:
        created = _aware(context.run.started_at)
    return _fmt(created) if created else None


def _updated_at(context: EvaluationContext) -> Optional[str]:
    result_obj = getattr(context.evaluation, "evaluation_result", None)
    updated = _aware(getattr(result_obj, "created_at", None)) if result_obj is not None else None
    if updated is None:
        updated = _aware(context.run.ended_at) or _aware(context.created_at)
    return _fmt(updated) if updated else None


def _task_info(context: EvaluationContext) -> EvaluationTaskInfo:
    task = context.task
    url = task.url if task else "https://unknown.task"
    prompt = task.prompt if task else "Task prompt unavailable"
    scope = task.scope if task else "local"
    use_case_meta: Dict[str, Any] = task.use_case if task and task.use_case else {}
    use_case = use_case_meta.get("name") if isinstance(use_case_meta, dict) else None
    return EvaluationTaskInfo(
        id=context.task_id,
        url=url,
        prompt=prompt,
        scope=scope,
        useCase=use_case,
        useCaseMetadata=dict(use_case_meta) if isinstance(use_case_meta, dict) else {},
    )


def _build_actions(context: EvaluationContext) -> List[Action]:
    actions: List[Action] = []
    created_at = _aware(context.created_at) or _aware(context.run.started_at) or datetime.now(timezone.utc)
    for index, action in enumerate(context.evaluation.actions or []):
        timestamp = action.timestamp or created_at
        actions.append(
            Action(
                id=action.id or f"{context.task_id}_action_{index}",
                type=action.type,
                selector=action.selector,
                value=action.value,
                timestamp=_fmt(_aware(timestamp)),
                duration=float(action.duration) if action.duration is not None else 0.0,
                success=True if action.success is None else bool(action.success),
            )
        )
    return actions


def _build_logs(context: EvaluationContext) -> List[Log]:
    logs: List[Log] = []
    for raw in context.evaluation.logs or []:
        timestamp = _aware(raw.timestamp) or _aware(context.run.started_at) or datetime.now(timezone.utc)
        level_value = (raw.level or "info").lower()
        level = LogLevel(level_value) if level_value in LogLevel._value2member_map_ else LogLevel.INFO  # type: ignore[attr-defined]
        logs.append(
            Log(
                timestamp=_fmt(timestamp),
                level=level,
                message=raw.message,
                metadata=raw.metadata or {},
            )
        )
    logs.sort(key=lambda entry: entry.timestamp or "")
    return logs


def _build_evaluation_item(context: EvaluationContext) -> EvaluationListItem:
    return EvaluationListItem(
        evaluationId=context.evaluation_id,
        runId=context.run.agent_run_id,
        agentId=f"agent-{context.run.miner.uid}",
        validatorId=f"validator_{context.round.validator.uid}",
        roundId=_round_number(context.round.round_id),
        taskId=context.task_id,
        taskUrl=context.task.url if context.task else "https://unknown.task",
        status=_status(context),
        score=round(_primary_score(context), 3),
        reward=round(_reward(context), 3),
        responseTime=round(_response_time(context), 3),
        createdAt=_created_at(context),
        updatedAt=_updated_at(context),
    )


def _build_evaluation_detail(context: EvaluationContext) -> EvaluationDetail:
    item = _build_evaluation_item(context)
    result_dict = context.evaluation.evaluation_result.model_dump(mode="json") if context.evaluation.evaluation_result else {}
    solution_dict = context.evaluation.task_solution.model_dump(mode="json") if context.evaluation.task_solution else {}
    return EvaluationDetail(
        **item.model_dump(),
        task=_task_info(context),
        actions=_build_actions(context),
        logs=_build_logs(context),
        screenshots=list(context.evaluation.screenshots or []),
        taskSolution=solution_dict,
        evaluationResult=result_dict,
        extras=dict(context.evaluation.extras or {}),
    )


@router.get("", response_model=EvaluationListResponse)
async def list_evaluations(
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    runId: Optional[str] = Query(None),
    agentId: Optional[str] = Query(None),
    validatorId: Optional[str] = Query(None),
    taskId: Optional[str] = Query(None),
    roundId: Optional[int] = Query(None),
) -> EvaluationListResponse:
    adapter = await _adapter(session)
    contexts = list((await adapter.evaluation_index()).values())

    if runId is not None:
        contexts = [ctx for ctx in contexts if ctx.run.agent_run_id == runId]
    if agentId is not None:
        try:
            agent_uid = int(agentId.split("-")[1]) if "-" in agentId else int(agentId)
            contexts = [ctx for ctx in contexts if ctx.run.miner.uid == agent_uid]
        except ValueError:
            contexts = []
    if validatorId is not None:
        try:
            validator_uid = int(validatorId.split("_")[1]) if "_" in validatorId else int(validatorId)
            contexts = [ctx for ctx in contexts if ctx.round.validator.uid == validator_uid]
        except ValueError:
            contexts = []
    if taskId is not None:
        contexts = [ctx for ctx in contexts if ctx.task_id == taskId]
    if roundId is not None:
        contexts = [ctx for ctx in contexts if _round_number(ctx.round.round_id) == roundId]

    contexts.sort(key=lambda ctx: _aware(ctx.created_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    total = len(contexts)
    start = (page - 1) * limit
    end = start + limit
    items = [_build_evaluation_item(ctx) for ctx in contexts[start:end]]

    return EvaluationListResponse(data={"evaluations": items, "total": total, "page": page, "limit": limit})


@router.get("/{evaluation_id}", response_model=EvaluationDetailResponse)
async def get_evaluation_detail(
    evaluation_id: str,
    session: AsyncSession = Depends(get_session),
) -> EvaluationDetailResponse:
    adapter = await _adapter(session)
    context = await adapter.get_evaluation(evaluation_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    return EvaluationDetailResponse(data={"evaluation": _build_evaluation_detail(context)})


@router.get("/runs/{run_id}", response_model=EvaluationListResponse)
async def list_evaluations_for_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> EvaluationListResponse:
    adapter = await _adapter(session)
    contexts = await adapter.evaluations_for_run(run_id)
    contexts.sort(key=lambda ctx: _aware(ctx.created_at) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    total = len(contexts)
    start = (page - 1) * limit
    end = start + limit
    items = [_build_evaluation_item(ctx) for ctx in contexts[start:end]]
    return EvaluationListResponse(data={"evaluations": items, "total": total, "page": page, "limit": limit})
