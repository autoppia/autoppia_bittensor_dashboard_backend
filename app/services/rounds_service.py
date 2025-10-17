from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationResultORM,
    RoundORM,
    TaskORM,
    TaskSolutionORM,
)
from app.models.core import (
    AgentEvaluationRun,
    AgentEvaluationRunWithDetails,
    EvaluationResult,
    ValidatorRound,
    ValidatorRoundWithDetails,
    Task,
    TaskSolution,
)
from app.data import get_validator_metadata
from app.utils.images import resolve_agent_image, resolve_validator_image

logger = logging.getLogger(__name__)


@dataclass
class AgentRunContext:
    """In-memory representation of an agent evaluation run with its related data."""

    round: ValidatorRound
    run: AgentEvaluationRun
    tasks: List[Task]
    task_solutions: List[TaskSolution]
    evaluation_results: List[EvaluationResult]


@dataclass
class RoundRecord:
    """Bundle of a persisted round row and its deserialized model."""

    row: RoundORM
    model: ValidatorRound

    @property
    def validator_round_id(self) -> str:
        return self.row.validator_round_id

    @property
    def validator_uid(self) -> Optional[int]:
        return self.row.validator_uid


@dataclass
class ValidatorRoundAggregate:
    """Aggregated view of a validator round with its contexts."""

    record: RoundRecord
    contexts: List[AgentRunContext]

    @property
    def round(self) -> ValidatorRound:
        return self.record.model

    @property
    def validator_round_id(self) -> str:
        return self.record.validator_round_id

    @property
    def validator_uid(self) -> Optional[int]:
        return self.record.validator_uid


@dataclass
class AggregatedRound:
    """Aggregated view of all validator rounds participating in a logical round."""

    round_number: int
    validator_rounds: List[ValidatorRoundAggregate]

    @property
    def contexts(self) -> List[AgentRunContext]:
        items: List[AgentRunContext] = []
        for entry in self.validator_rounds:
            items.extend(entry.contexts)
        return items


def _round_id_to_int(value: str) -> int:
    if not value:
        return 0
    matches = re.findall(r"\d+", value)
    if not matches:
        return 0
    try:
        return int(matches[-1])
    except ValueError:
        return 0


def _iso_timestamp(value: Optional[float]) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()


def _time_remaining(seconds: float) -> Dict[str, int]:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return {
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
    }


def _parse_iso8601(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _round_number_from_model(round_model: ValidatorRound, fallback_identifier: str) -> Optional[int]:
    candidate = getattr(round_model, "round_number", None)
    if candidate is not None:
        try:
            return int(candidate)
        except (TypeError, ValueError):
            pass

    extras = getattr(round_model, "model_extra", None) or {}
    for key in ("round", "roundNumber", "round_number"):
        value = extras.get(key)
        if value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    parsed = _round_id_to_int(fallback_identifier)
    return parsed or None


def _aggregate_status(statuses: List[str]) -> str:
    normalized = [status.lower() for status in statuses if status]
    if not normalized:
        return "completed"
    if any(status == "active" for status in normalized):
        return "active"
    if any(status == "pending" for status in normalized):
        return "pending"
    return normalized[0]


class RoundsService:
    """Read operations for rounds stored in the SQL database."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_rounds(self, limit: int, skip: int) -> List[ValidatorRoundWithDetails]:
        stmt = (
            select(RoundORM)
            .order_by(RoundORM.validator_round_id.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.scalars(stmt)
        rounds: List[ValidatorRoundWithDetails] = []

        for round_row in result:
            try:
                round_model = self._deserialize_round(round_row)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize round %s from SQL: %s",
                    round_row.validator_round_id,
                    exc,
                )
                continue

            rounds.append(
                ValidatorRoundWithDetails(
                    **round_model.model_dump(),
                    agent_evaluation_runs=[],
                )
            )

        return rounds

    async def get_round(self, round_identifier: Union[str, int]) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        current = await self.get_current_round_overview()
        latest_round_number = current["round"] if current else aggregated.round_number
        records = [entry.record for entry in aggregated.validator_rounds]
        overview = self._build_round_day_overview_from_records(
            aggregated.round_number,
            records,
            latest_round_number,
        )

        detailed_validator_rounds: List[Dict[str, Any]] = []
        for entry in aggregated.validator_rounds:
            summary = self._summarize_validator_round(entry.record)
            agent_runs = [
                AgentEvaluationRunWithDetails(
                    **ctx.run.model_dump(),
                    tasks=ctx.tasks,
                    task_solutions=ctx.task_solutions,
                    evaluation_results=ctx.evaluation_results,
                ).model_dump()
                for ctx in entry.contexts
            ]
            summary["agentEvaluationRuns"] = agent_runs
            summary["roundData"] = entry.round.model_dump()
            detailed_validator_rounds.append(summary)

        overview["validatorRounds"] = detailed_validator_rounds
        overview["id"] = aggregated.round_number
        overview["round"] = aggregated.round_number
        overview["roundNumber"] = aggregated.round_number
        return overview

    async def list_agent_runs(
        self,
        validator_round_id: str,
        limit: int,
        skip: int,
        include_details: bool = True,
    ) -> List[AgentEvaluationRunWithDetails]:
        stmt = (
            select(AgentEvaluationRunORM)
            .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
            .order_by(AgentEvaluationRunORM.id.desc())
            .offset(skip)
            .limit(limit)
        )

        if include_details:
            stmt = stmt.options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )

        result = await self.session.scalars(stmt)
        run_rows = list(result)

        tasks_by_round: Dict[str, Dict[str, Task]] = {}
        if include_details:
            round_ids = {row.validator_round_id for row in run_rows}
            tasks_by_round = await self._load_tasks_for_rounds(round_ids)

        return [
            self._convert_agent_run(
                run_row,
                include_details=include_details,
                tasks_by_round=tasks_by_round,
            )
            for run_row in run_rows
        ]

    async def get_agent_run(self, agent_run_id: str) -> AgentEvaluationRunWithDetails:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        )
        run_row = await self.session.scalar(stmt)
        if not run_row:
            raise ValueError(f"Agent run {agent_run_id} not found")
        tasks_by_round = await self._load_tasks_for_rounds([run_row.validator_round_id])
        return self._convert_agent_run(
            run_row,
            include_details=True,
            tasks_by_round=tasks_by_round,
        )

    async def list_agent_run_contexts(
        self,
        validator_round_id: Optional[str] = None,
        limit: Optional[int] = 100,
        skip: int = 0,
        include_details: bool = True,
    ) -> List[AgentRunContext]:
        stmt = select(AgentEvaluationRunORM).options(
            selectinload(AgentEvaluationRunORM.round),
        )

        if include_details:
            stmt = stmt.options(
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )

        stmt = stmt.order_by(AgentEvaluationRunORM.id.desc())

        if skip:
            stmt = stmt.offset(skip)
        if limit is not None:
            stmt = stmt.limit(limit)

        if validator_round_id:
            stmt = stmt.where(
                AgentEvaluationRunORM.validator_round_id == validator_round_id
            )

        result = await self.session.scalars(stmt)
        run_rows = list(result)

        tasks_by_round: Dict[str, Dict[str, Task]] = {}
        if include_details:
            round_ids = {row.validator_round_id for row in run_rows}
            tasks_by_round = await self._load_tasks_for_rounds(round_ids)

        return [
            self._build_agent_run_context(
                run_row,
                include_details=include_details,
                tasks_for_round=(tasks_by_round.get(run_row.validator_round_id) if include_details else None),
            )
            for run_row in run_rows
        ]

    async def get_agent_run_context(self, agent_run_id: str) -> AgentRunContext:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.round),
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        )
        run_row = await self.session.scalar(stmt)
        if not run_row:
            raise ValueError(f"Agent run {agent_run_id} not found")
        tasks_by_round = await self._load_tasks_for_rounds([run_row.validator_round_id])
        return self._build_agent_run_context(
            run_row,
            include_details=True,
            tasks_for_round=tasks_by_round.get(run_row.validator_round_id),
        )

    async def _get_all_round_records(self) -> List[RoundRecord]:
        stmt = select(RoundORM).order_by(RoundORM.id.desc())
        rows = await self.session.scalars(stmt)
        records: List[RoundRecord] = []
        for row in rows:
            try:
                model = self._deserialize_round(row)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize round %s: %s",
                    row.validator_round_id,
                    exc,
                )
                continue
            if model.started_at is None:
                logger.debug(
                    "Skipping round %s due to missing started_at timestamp",
                    row.validator_round_id,
                )
                continue
            records.append(RoundRecord(row=row, model=model))
        return records

    async def _fetch_round_records_by_number(self, round_number: int) -> List[RoundRecord]:
        records = await self._get_all_round_records()
        matched: List[RoundRecord] = []
        for record in records:
            value = _round_number_from_model(record.model, record.validator_round_id)
            if value is None:
                continue
            if value == round_number:
                matched.append(record)
        return matched

    async def _resolve_round_number(self, round_identifier: Union[str, int]) -> int:
        if isinstance(round_identifier, int):
            if round_identifier <= 0:
                raise ValueError(f"Invalid round identifier: {round_identifier}")
            return round_identifier

        raw = str(round_identifier or "").strip()
        if not raw:
            raise ValueError("Round identifier is required")

        if raw.isdigit():
            number = int(raw)
            if number <= 0:
                raise ValueError(f"Invalid round identifier: {round_identifier}")
            return number

        try:
            row = await self._get_round_row(raw)
        except ValueError as exc:
            parsed = _round_id_to_int(raw)
            if parsed:
                return parsed
            raise exc

        record = RoundRecord(row=row, model=self._deserialize_round(row))
        value = _round_number_from_model(record.model, record.validator_round_id)
        if value is None:
            parsed = _round_id_to_int(record.validator_round_id)
            if parsed:
                return parsed
            raise ValueError(f"Unable to resolve round number for {round_identifier}")
        return value

    async def _fetch_aggregated_round(
        self,
        round_identifier: Union[str, int],
        include_details: bool = True,
    ) -> AggregatedRound:
        round_number = await self._resolve_round_number(round_identifier)
        records = await self._fetch_round_records_by_number(round_number)
        if not records:
            raise ValueError(f"Round {round_identifier} not found")

        validator_rounds: List[ValidatorRoundAggregate] = []
        for record in records:
            contexts = await self.list_agent_run_contexts(
                validator_round_id=record.validator_round_id,
                limit=None,
                skip=0,
                include_details=include_details,
            )
            validator_rounds.append(
                ValidatorRoundAggregate(
                    record=record,
                    contexts=contexts,
                )
            )
        return AggregatedRound(round_number=round_number, validator_rounds=validator_rounds)

    @staticmethod
    def _estimate_completed_tasks(round_obj: ValidatorRound) -> int:
        completed = len(round_obj.winners or [])
        if completed == 0 and round_obj.weights:
            completed = int(round_obj.n_tasks * 0.5)
        if completed == 0 and getattr(round_obj, "n_winners", None):
            completed = round_obj.n_winners or 0
        total_tasks = round_obj.n_tasks or 0
        if total_tasks and completed > total_tasks:
            return total_tasks
        return completed

    def _summarize_validator_round(self, record: RoundRecord) -> Dict[str, Any]:
        round_obj = record.model
        validator = (
            round_obj.validator_info
            or (round_obj.validators[0] if round_obj.validators else None)
        )
        validator_uid = validator.uid if validator else record.validator_uid
        metadata: Dict[str, Any] = get_validator_metadata(validator_uid) if validator_uid is not None else {}

        validator_name = validator.name if validator and validator.name else metadata.get("name")
        existing_icon = metadata.get("image")
        icon = resolve_validator_image(validator_name, existing=existing_icon)

        completed_tasks = self._estimate_completed_tasks(round_obj)

        return {
            "validatorRoundId": record.validator_round_id,
            "validatorUid": validator.uid if validator else record.validator_uid,
            "validatorName": validator.name if validator else None,
            "validatorHotkey": validator.hotkey if validator else None,
            "status": (round_obj.status or "completed"),
            "startTime": _iso_timestamp(round_obj.started_at),
            "endTime": _iso_timestamp(round_obj.ended_at) if round_obj.ended_at else None,
            "averageScore": round(round_obj.average_score or 0.0, 3),
            "topScore": round(round_obj.top_score or 0.0, 3),
            "totalTasks": round_obj.n_tasks,
            "completedTasks": completed_tasks,
            "icon": icon,
        }

    def _build_round_day_overview_from_records(
        self,
        round_number: int,
        records: List[RoundRecord],
        latest_round_number: int,
    ) -> Dict[str, Any]:
        if not records:
            raise ValueError("Cannot build overview without records")

        start_block = min(record.model.start_block for record in records)
        end_block_candidates: List[int] = []
        for record in records:
            round_obj = record.model
            end_block = round_obj.end_block
            if end_block is None:
                end_block = round_obj.start_block + round_obj.max_blocks
            end_block_candidates.append(end_block)
        end_block_value = max(end_block_candidates)

        started_at_values = [record.model.started_at for record in records if record.model.started_at is not None]
        ended_at_values = [record.model.ended_at for record in records if record.model.ended_at is not None]
        started_at = min(started_at_values) if started_at_values else None
        ended_at = max(ended_at_values) if ended_at_values else None

        statuses = [(record.model.status or "completed") for record in records]
        status = _aggregate_status(statuses)
        total_tasks = sum(record.model.n_tasks or 0 for record in records)
        completed_tasks = sum(self._estimate_completed_tasks(record.model) for record in records)

        score_weights: List[Tuple[float, int]] = []
        top_scores: List[float] = []
        for record in records:
            score = record.model.average_score
            if score is not None:
                score_weights.append((score, record.model.n_tasks or 1))
            if record.model.top_score is not None:
                top_scores.append(record.model.top_score)

        if score_weights:
            numerator = sum(score * weight for score, weight in score_weights)
            denominator = sum(weight for _, weight in score_weights)
            average_score = numerator / denominator if denominator else 0.0
        else:
            average_score = 0.0

        top_score = max(top_scores) if top_scores else 0.0

        progress_ratio = 1.0 if status == "completed" else (
            min(1.0, completed_tasks / total_tasks) if total_tasks else 0.0
        )
        current_block = int(start_block + (end_block_value - start_block) * progress_ratio)
        current_block = min(current_block, end_block_value)
        blocks_remaining = max(end_block_value - current_block, 0)

        round_key = f"round_{round_number}"
        is_current = round_number == latest_round_number and status == "active"

        return {
            "id": round_number,
            "round": round_number,
            "roundNumber": round_number,
            "roundKey": round_key,
            "startBlock": start_block,
            "endBlock": end_block_value,
            "current": is_current,
            "startTime": _iso_timestamp(started_at),
            "endTime": _iso_timestamp(ended_at) if ended_at else None,
            "status": status,
            "totalTasks": total_tasks,
            "completedTasks": completed_tasks,
            "averageScore": round(average_score, 3),
            "topScore": round(top_score, 3),
            "currentBlock": current_block,
            "blocksRemaining": blocks_remaining,
            "progress": round(progress_ratio, 3),
            "validatorRoundCount": len(records),
            "validatorRounds": [self._summarize_validator_round(record) for record in records],
        }

    @staticmethod
    def _sort_round_entries(
        entries: List[Dict[str, Any]],
        sort_by: str,
        sort_order: str,
    ) -> None:
        reverse = sort_order.lower() == "desc"
        numeric_fields = {
            "id",
            "round",
            "roundNumber",
            "totalTasks",
            "completedTasks",
            "averageScore",
            "topScore",
            "currentBlock",
            "blocksRemaining",
            "progress",
            "validatorRoundCount",
        }

        def _sort_value(entry: Dict[str, Any]) -> Any:
            value = entry.get(sort_by)
            if value is None:
                return 0 if sort_by in numeric_fields else ""
            return value

        try:
            entries.sort(key=_sort_value, reverse=reverse)
        except Exception:  # noqa: BLE001
            pass

    def _compute_aggregated_progress(
        self,
        records: List[RoundRecord],
        completed_tasks: int,
        total_tasks: int,
    ) -> Dict[str, Any]:
        if not records:
            return {
                "startBlock": 0,
                "endBlock": 0,
                "currentBlock": 0,
                "blocksRemaining": 0,
                "progress": 0.0,
                "estimatedTimeRemaining": _time_remaining(0),
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

        start_block = min(record.model.start_block for record in records)
        end_block_candidates: List[int] = []
        statuses: List[str] = []
        elapsed_values: List[float] = []
        for record in records:
            round_obj = record.model
            end_block = round_obj.end_block
            if end_block is None:
                end_block = round_obj.start_block + round_obj.max_blocks
            end_block_candidates.append(end_block)
            statuses.append((round_obj.status or "").lower())
            if round_obj.elapsed_sec is not None:
                elapsed_values.append(round_obj.elapsed_sec)

        end_block_value = max(end_block_candidates)
        is_completed = all(status == "completed" for status in statuses if status)

        if total_tasks:
            progress_ratio = min(1.0, max(0.0, completed_tasks / total_tasks))
        else:
            progress_ratio = 1.0 if is_completed else 0.0
        if is_completed:
            progress_ratio = 1.0

        current_block = int(start_block + (end_block_value - start_block) * progress_ratio)
        current_block = min(current_block, end_block_value)
        blocks_remaining = max(end_block_value - current_block, 0)

        average_elapsed = sum(elapsed_values) / len(elapsed_values) if elapsed_values else None
        average_task_time = (average_elapsed / completed_tasks) if average_elapsed and completed_tasks else 0.0
        estimated_seconds_remaining = (
            blocks_remaining * (average_task_time / total_tasks)
            if total_tasks and average_task_time
            else 0.0
        )

        return {
            "startBlock": start_block,
            "endBlock": end_block_value,
            "currentBlock": current_block,
            "blocksRemaining": blocks_remaining,
            "progress": round(progress_ratio, 3),
            "estimatedTimeRemaining": _time_remaining(estimated_seconds_remaining),
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }

    async def list_rounds_paginated(
        self,
        page: int,
        limit: int,
        status: Optional[str] = None,
        sort_by: str = "id",
        sort_order: str = "desc",
    ) -> Tuple[List[Dict[str, Any]], int]:
        records = await self._get_all_round_records()
        if not records:
            return [], 0

        grouped: Dict[int, List[RoundRecord]] = {}
        for record in records:
            number = _round_number_from_model(record.model, record.validator_round_id)
            if number is None:
                continue
            grouped.setdefault(number, []).append(record)

        if not grouped:
            return [], 0

        latest_round_number = max(grouped.keys())
        entries = [
            self._build_round_day_overview_from_records(number, group, latest_round_number)
            for number, group in grouped.items()
        ]

        entries = [entry for entry in entries if entry.get("validatorRoundCount", 0) > 0]

        if status:
            entries = [entry for entry in entries if entry["status"] == status]

        self._sort_round_entries(entries, sort_by, sort_order)

        total = len(entries)
        start = max(0, (page - 1) * limit)
        end = start + limit
        return entries[start:end], total

    async def get_current_round_overview(self) -> Optional[Dict[str, Any]]:
        records = await self._get_all_round_records()
        if not records:
            return None
        grouped: Dict[int, List[RoundRecord]] = {}
        for record in records:
            number = _round_number_from_model(record.model, record.validator_round_id)
            if number is None:
                continue
            grouped.setdefault(number, []).append(record)
        if not grouped:
            return None
        latest_round_number = max(grouped.keys())
        group = grouped[latest_round_number]
        return self._build_round_day_overview_from_records(
            latest_round_number,
            group,
            latest_round_number,
        )

    async def get_round_overview(self, round_identifier: Union[str, int]) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier, include_details=False)
        current = await self.get_current_round_overview()
        latest_round_number = current["round"] if current else aggregated.round_number
        records = [entry.record for entry in aggregated.validator_rounds]
        return self._build_round_day_overview_from_records(
            aggregated.round_number,
            records,
            latest_round_number,
        )

    async def get_round_statistics(self, round_identifier: Union[str, int]) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        contexts = aggregated.contexts

        miner_ids: set[int] = set()
        completed_tasks = 0
        total_tasks = 0
        scores: List[float] = []
        validator_top_scores: List[float] = []
        durations: List[float] = []
        total_stake = 0.0
        active_miner_ids: set[int] = set()

        for entry in aggregated.validator_rounds:
            round_obj = entry.round
            if round_obj.miners:
                for miner in round_obj.miners:
                    if miner.uid is not None:
                        miner_ids.add(miner.uid)

            per_validator_scores: List[float] = []

            for ctx in entry.contexts:
                if ctx.run.is_sota:
                    continue
                if ctx.run.miner_uid is not None:
                    miner_ids.add(ctx.run.miner_uid)
                completed = ctx.run.n_tasks_completed
                if completed is None:
                    completed = len(
                        [er for er in ctx.evaluation_results if er.final_score >= 0.5]
                    )
                completed_tasks += completed

                total = ctx.run.n_tasks_total
                if total is None:
                    total = len(ctx.tasks)
                total_tasks += total

                score = ctx.run.avg_eval_score
                if score is None and ctx.evaluation_results:
                    score = sum(er.final_score for er in ctx.evaluation_results) / len(ctx.evaluation_results)
                if score is not None:
                    scores.append(score)
                    per_validator_scores.append(score)

                if ctx.run.started_at and ctx.run.ended_at:
                    durations.append(ctx.run.ended_at - ctx.run.started_at)

                if completed > 0 and ctx.run.miner_uid is not None:
                    active_miner_ids.add(ctx.run.miner_uid)

            for value in (round_obj.weights or {}).values():
                try:
                    total_stake += float(value)
                except (TypeError, ValueError):
                    continue

            top_score_candidate = round_obj.top_score
            if top_score_candidate is None and per_validator_scores:
                top_score_candidate = max(per_validator_scores)
            if top_score_candidate is not None:
                validator_top_scores.append(top_score_candidate)

        top_score = max(scores) if scores else 0.0
        average_score = (
            sum(validator_top_scores) / len(validator_top_scores)
            if validator_top_scores
            else (sum(scores) / len(scores) if scores else 0.0)
        )
        success_rate = (completed_tasks / total_tasks * 100.0) if total_tasks else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        total_emission = int(total_stake * 0.05) if total_stake else 0

        return {
            "roundId": aggregated.round_number,
            "totalMiners": len(miner_ids),
            "activeMiners": len(active_miner_ids),
            "totalTasks": total_tasks,
            "completedTasks": completed_tasks,
            "averageScore": round(average_score, 3),
            "topScore": round(top_score, 3),
            "successRate": round(success_rate, 2),
            "averageDuration": round(average_duration, 2),
            "totalStake": int(total_stake),
            "totalEmission": total_emission,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }

    async def get_round_miners(
        self,
        round_identifier: Union[str, int],
        page: int,
        limit: int,
        sort_by: str = "score",
        sort_order: str = "desc",
        success: Optional[bool] = None,
        min_score: Optional[float] = None,
        max_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)

        miners: List[Dict[str, Any]] = []
        benchmark_map: Dict[str, Dict[str, Any]] = {}

        for entry_data in aggregated.validator_rounds:
            round_obj = entry_data.round
            weights = round_obj.weights or {}

            for ctx in entry_data.contexts:
                entry = self._build_miner_performance(ctx, round_obj, weights)

                if success is not None and entry["success"] != success:
                    continue
                if min_score is not None and entry["score"] < min_score:
                    continue
                if max_score is not None and entry["score"] > max_score:
                    continue

                if entry.get("isSota"):
                    key = (
                        entry.get("provider")
                        or entry.get("name")
                        or str(entry.get("uid"))
                    )
                    existing = benchmark_map.get(key)
                    if existing is None:
                        record = dict(entry)
                        sources = []
                        if entry.get("validatorId"):
                            sources.append(entry["validatorId"])
                        record["validatorSources"] = sources
                        benchmark_map[key] = record
                    else:
                        if entry["score"] > existing.get("score", 0.0):
                            existing.update(entry)
                        sources = existing.get("validatorSources") or []
                        if entry.get("validatorId") and entry["validatorId"] not in sources:
                            sources.append(entry["validatorId"])
                        existing["validatorSources"] = sources
                else:
                    miners.append(entry)

        reverse = sort_order.lower() == "desc"
        key_map = {
            "score": lambda item: item.get("score", 0.0),
            "duration": lambda item: item.get("duration", 0.0),
            "ranking": lambda item: item.get("ranking", 0),
            "uid": lambda item: item.get("uid", 0),
        }
        sort_key = key_map.get(sort_by, key_map["score"])
        miners.sort(key=sort_key, reverse=reverse)

        total = len(miners)
        start = max(0, (page - 1) * limit)
        end = start + limit
        paginated = miners[start:end]

        benchmarks = list(benchmark_map.values())
        benchmarks.sort(key=lambda item: item.get("score", 0.0), reverse=True)

        return {
            "miners": paginated,
            "benchmarks": benchmarks,
            "total": total,
            "page": page,
            "limit": limit,
        }

    async def get_round_validators(
        self,
        round_identifier: Union[str, int],
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        validator_map: Dict[str, Dict[str, Any]] = {}

        for entry in aggregated.validator_rounds:
            round_obj = entry.round
            contexts_by_validator: Dict[int, List[AgentRunContext]] = {}
            for ctx in entry.contexts:
                contexts_by_validator.setdefault(ctx.run.validator_uid, []).append(ctx)

            last_seen = _iso_timestamp(round_obj.ended_at or round_obj.started_at)

            for validator in round_obj.validators:
                runs = contexts_by_validator.get(validator.uid, [])
                total_tasks = sum(
                    run.run.n_tasks_total or len(run.tasks) for run in runs
                )
                completed_tasks = sum(
                    run.run.n_tasks_completed
                    or len(
                        [er for er in run.evaluation_results if er.final_score >= 0.5]
                    )
                    for run in runs
                )
                scores: List[float] = []
                for run in runs:
                    score = run.run.avg_eval_score
                    if score is None and run.evaluation_results:
                        score = sum(er.final_score for er in run.evaluation_results) / len(run.evaluation_results)
                    if score is not None:
                        scores.append(score)
                average_score = sum(scores) / len(scores) if scores else 0.0
                top_score = max(scores) if scores else 0.0
                completion_rate = (completed_tasks / total_tasks) if total_tasks else 0.0

                status = "inactive"
                if runs:
                    first = runs[0]
                    if first.run.ended_at:
                        if (datetime.now(timezone.utc).timestamp() - first.run.ended_at) > 3600:
                            status = "inactive"
                        else:
                            status = "active"
                    else:
                        status = "active"
                elif round_obj.status == "active":
                    status = "active"

                weight = validator.stake or 0.0
                trust = validator.vtrust or 0.0
                version = validator.version or "1"
                metadata = get_validator_metadata(validator.uid)
                validator_name = validator.name or metadata.get("name")
                existing_icon = metadata.get("image")
                icon = resolve_validator_image(validator_name, existing=existing_icon)

                key = f"{entry.validator_round_id}:{validator.uid}"
                validator_map[key] = {
                    "id": f"validator-{validator.uid}",
                    "validatorRoundId": entry.validator_round_id,
                    "name": validator.name or f"Validator {validator.uid}",
                    "hotkey": validator.hotkey,
                    "icon": icon,
                    "status": status,
                    "totalTasks": total_tasks,
                    "completedTasks": completed_tasks,
                    "averageScore": round(average_score, 3),
                    "topScore": round(top_score, 3),
                    "weight": int(weight),
                    "trust": round(trust, 3),
                    "version": int(version.split(".")[0]) if version else 1,
                    "stake": int(weight),
                    "emission": int(weight * 0.05),
                    "lastSeen": last_seen,
                    "uptime": round(min(100.0, completion_rate * 100.0), 2),
                }

        validators = list(validator_map.values())
        validators.sort(key=lambda item: (item["validatorRoundId"], item["name"]))
        return {"validators": validators, "total": len(validators)}

    async def get_round_validator(
        self,
        round_identifier: Union[str, int],
        validator_identifier: Union[str, int],
    ) -> Dict[str, Any]:
        data = await self.get_round_validators(round_identifier)
        for validator in data["validators"]:
            if (
                validator["id"] == str(validator_identifier)
                or validator["id"].split("-", 1)[-1] == str(validator_identifier)
                or validator.get("validatorRoundId") == str(validator_identifier)
            ):
                return validator
        raise ValueError(f"Validator {validator_identifier} not found in round {round_identifier}")

    async def get_round_activity(
        self,
        round_identifier: Union[str, int],
        limit: int,
        offset: int = 0,
        activity_type: Optional[str] = None,
        since: Optional[str] = None,
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        events: List[Dict[str, Any]] = []

        since_dt = _parse_iso8601(since)

        aggregated_key = f"round_{aggregated.round_number}"
        start_candidates = [
            entry.round.started_at for entry in aggregated.validator_rounds if entry.round.started_at is not None
        ]
        end_candidates = [
            entry.round.ended_at for entry in aggregated.validator_rounds if entry.round.ended_at is not None
        ]
        started_ts = _iso_timestamp(min(start_candidates) if start_candidates else None)
        events.append(
            {
                "id": f"{aggregated_key}_started",
                "type": "round_started",
                "message": f"Round {aggregated.round_number} started",
                "timestamp": started_ts,
                "metadata": {"roundId": aggregated_key},
            }
        )
        if end_candidates:
            events.append(
                {
                    "id": f"{aggregated_key}_ended",
                    "type": "round_ended",
                    "message": f"Round {aggregated.round_number} completed",
                    "timestamp": _iso_timestamp(max(end_candidates)),
                    "metadata": {"roundId": aggregated_key},
                }
            )

        for entry in aggregated.validator_rounds:
            round_obj = entry.round
            round_key = entry.validator_round_id
            events.append(
                {
                    "id": f"{round_key}_started",
                    "type": "validator_round_started",
                    "message": f"Validator round {round_key} started",
                    "timestamp": _iso_timestamp(round_obj.started_at),
                    "metadata": {"roundId": aggregated_key, "validatorRoundId": round_key},
                }
            )
            if round_obj.ended_at:
                events.append(
                    {
                        "id": f"{round_key}_ended",
                        "type": "validator_round_ended",
                        "message": f"Validator round {round_key} completed",
                        "timestamp": _iso_timestamp(round_obj.ended_at),
                        "metadata": {"roundId": aggregated_key, "validatorRoundId": round_key},
                    }
                )

            for ctx in entry.contexts:
                run_start = _iso_timestamp(ctx.run.started_at)
                events.append(
                    {
                        "id": f"{ctx.run.agent_run_id}_started",
                        "type": "task_started",
                        "message": f"Agent {ctx.run.agent_run_id} started evaluation",
                        "timestamp": run_start,
                        "metadata": {
                            "minerUid": ctx.run.miner_uid,
                            "validatorId": f"validator-{ctx.run.validator_uid}",
                            "validatorRoundId": round_key,
                        },
                    }
                )
                for evaluation in ctx.evaluation_results:
                    eval_ts: Optional[float] = None
                    created_at = getattr(evaluation, "created_at", None)
                    if isinstance(created_at, datetime):
                        eval_ts = created_at.timestamp()
                    elif isinstance(created_at, (int, float)):
                        eval_ts = float(created_at)
                    events.append(
                        {
                            "id": f"{ctx.run.agent_run_id}_{evaluation.task_id}",
                            "type": "task_completed",
                            "message": f"Task {evaluation.task_id} evaluated",
                            "timestamp": _iso_timestamp(eval_ts or ctx.run.ended_at or ctx.run.started_at),
                            "metadata": {
                                "minerUid": ctx.run.miner_uid,
                                "validatorId": f"validator-{ctx.run.validator_uid}",
                                "validatorRoundId": round_key,
                                "taskId": evaluation.task_id,
                                "score": getattr(evaluation, "final_score", None),
                            },
                        }
                    )
                if ctx.run.ended_at:
                    events.append(
                        {
                            "id": f"{ctx.run.agent_run_id}_completed",
                            "type": "task_completed",
                            "message": f"Agent {ctx.run.agent_run_id} completed evaluation",
                            "timestamp": _iso_timestamp(ctx.run.ended_at),
                            "metadata": {
                                "minerUid": ctx.run.miner_uid,
                                "validatorId": f"validator-{ctx.run.validator_uid}",
                                "validatorRoundId": round_key,
                            },
                        }
                    )

        if since_dt:
            filtered_events: List[Dict[str, Any]] = []
            for event in events:
                timestamp = _parse_iso8601(event["timestamp"])
                if timestamp and timestamp >= since_dt:
                    filtered_events.append(event)
            events = filtered_events

        if activity_type:
            events = [event for event in events if event["type"] == activity_type]

        events.sort(key=lambda item: item["timestamp"], reverse=True)
        total = len(events)
        paginated = events[offset : offset + limit]
        return {"activities": paginated, "total": total}

    async def get_round_progress(
        self,
        round_identifier: Union[str, int],
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier, include_details=False)
        records = [entry.record for entry in aggregated.validator_rounds]
        total_tasks = sum(record.model.n_tasks or 0 for record in records)
        completed_tasks = sum(self._estimate_completed_tasks(record.model) for record in records)
        progress = self._compute_aggregated_progress(records, completed_tasks, total_tasks)
        return {
            "roundId": aggregated.round_number,
            "currentBlock": progress["currentBlock"],
            "startBlock": progress["startBlock"],
            "endBlock": progress["endBlock"],
            "blocksRemaining": progress["blocksRemaining"],
            "progress": progress["progress"],
            "estimatedTimeRemaining": progress["estimatedTimeRemaining"],
            "lastUpdated": progress["lastUpdated"],
        }

    async def get_top_miners(
        self,
        round_identifier: Union[str, int],
        limit: int,
    ) -> Dict[str, Any]:
        data = await self.get_round_miners(round_identifier, page=1, limit=limit, sort_by="score", sort_order="desc")
        miners = data.get("miners", [])[:limit]
        benchmarks = (data.get("benchmarks") or [])[:limit]
        return {
            "miners": miners,
            "benchmarks": benchmarks,
            "total": len(miners),
            "page": 1,
            "limit": limit,
        }

    async def get_round_miner(
        self,
        round_identifier: Union[str, int],
        miner_uid: int,
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        for entry in aggregated.validator_rounds:
            round_obj = entry.round
            weights = round_obj.weights or {}
            for ctx in entry.contexts:
                if ctx.run.miner_uid == miner_uid:
                    return self._build_miner_performance(ctx, round_obj, weights)
        raise ValueError(f"Miner {miner_uid} not found in round {round_identifier}")

    async def compare_rounds(self, round_ids: List[int]) -> List[Dict[str, Any]]:
        comparisons: List[Dict[str, Any]] = []
        for round_id in round_ids:
            aggregated = await self._fetch_aggregated_round(round_id)
            statistics = await self.get_round_statistics(round_id)
            miner_entries: List[Dict[str, Any]] = []
            for entry in aggregated.validator_rounds:
                weights = entry.round.weights or {}
                for ctx in entry.contexts:
                    miner_entries.append(
                        self._build_miner_performance(ctx, entry.round, weights)
                    )
            miner_entries.sort(key=lambda item: item.get("score", 0.0), reverse=True)
            top_miners = [
                {
                    "uid": entry.get("uid"),
                    "score": entry.get("score"),
                    "ranking": entry.get("ranking"),
                }
                for entry in miner_entries[:5]
            ]
            comparisons.append(
                {
                    "roundId": statistics["roundId"],
                    "statistics": statistics,
                    "topMiners": top_miners,
                }
            )
        return comparisons

    async def get_round_timeline(
        self,
        round_identifier: Union[str, int],
    ) -> List[Dict[str, Any]]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        contexts = aggregated.contexts
        points: List[Dict[str, Any]] = []

        timestamps: List[float] = []
        for ctx in contexts:
            if ctx.run.started_at:
                timestamps.append(ctx.run.started_at)
            if ctx.run.ended_at:
                timestamps.append(ctx.run.ended_at)
            for evaluation in ctx.evaluation_results:
                created_at = getattr(evaluation, "created_at", None)
                if isinstance(created_at, datetime):
                    timestamps.append(created_at.timestamp())

        timestamps = sorted(set(timestamps))
        start_candidates = [
            entry.round.started_at for entry in aggregated.validator_rounds if entry.round.started_at is not None
        ]
        if not timestamps and start_candidates:
            timestamps = [min(start_candidates)]

        start_block = min(entry.round.start_block for entry in aggregated.validator_rounds)
        end_block_candidates: List[int] = []
        for entry in aggregated.validator_rounds:
            end_block = entry.round.end_block
            if end_block is None:
                end_block = entry.round.start_block + entry.round.max_blocks
            end_block_candidates.append(end_block)
        end_block_value = max(end_block_candidates)
        block_span = max(1, end_block_value - start_block)

        total_tasks = sum(entry.round.n_tasks or 0 for entry in aggregated.validator_rounds) or 1

        for ts in timestamps:
            completed_tasks = 0
            scores: List[float] = []
            active_miners = 0
            for ctx in contexts:
                if ctx.run.started_at and ctx.run.started_at <= ts:
                    active_miners += 1
                if ctx.run.ended_at and ctx.run.ended_at <= ts:
                    completed_tasks += ctx.run.n_tasks_completed or 0
                    if ctx.run.avg_eval_score is not None:
                        scores.append(ctx.run.avg_eval_score)
            average_score = sum(scores) / len(scores) if scores else 0.0
            progress_ratio = min(1.0, (completed_tasks / total_tasks))
            block = int(start_block + block_span * progress_ratio)
            points.append(
                {
                    "timestamp": _iso_timestamp(ts),
                    "block": block,
                    "completedTasks": completed_tasks,
                    "averageScore": round(average_score, 3),
                    "activeMiners": active_miners,
                }
            )

        points.sort(key=lambda item: item["timestamp"])
        return points

    async def get_round_summary_card(
        self,
        round_identifier: Union[str, int],
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier, include_details=False)
        statistics = await self.get_round_statistics(round_identifier)
        records = [entry.record for entry in aggregated.validator_rounds]
        total_tasks = sum(record.model.n_tasks or 0 for record in records)
        completed_tasks = sum(self._estimate_completed_tasks(record.model) for record in records)
        progress = self._compute_aggregated_progress(records, completed_tasks, total_tasks)
        status = _aggregate_status([entry.round.status or "completed" for entry in aggregated.validator_rounds])

        return {
            "roundId": aggregated.round_number,
            "status": status,
            "progress": progress["progress"],
            "totalMiners": statistics["totalMiners"],
            "averageScore": statistics["averageScore"],
            "topScore": statistics["topScore"],
            "timeRemaining": None if progress["progress"] >= 1 else f"{progress['estimatedTimeRemaining']['hours']}h {progress['estimatedTimeRemaining']['minutes']}m",
        }

    async def _load_tasks_for_rounds(
        self,
        round_ids: Iterable[str],
    ) -> Dict[str, Dict[str, Task]]:
        identifiers = {round_id for round_id in round_ids if round_id}
        if not identifiers:
            return {}

        stmt = select(TaskORM).where(TaskORM.validator_round_id.in_(identifiers))
        rows = await self.session.scalars(stmt)
        grouped: Dict[str, List[TaskORM]] = defaultdict(list)
        for task_row in rows:
            grouped[task_row.validator_round_id].append(task_row)

        tasks_by_round: Dict[str, Dict[str, Task]] = {}
        for round_id, task_rows in grouped.items():
            task_models = self._convert_tasks(task_rows)
            tasks_by_round[round_id] = {task.task_id: task for task in task_models}
        return tasks_by_round

    def _convert_agent_run(
        self,
        run_row: AgentEvaluationRunORM,
        include_details: bool = True,
        parent_round_row: Optional[RoundORM] = None,
        tasks_by_round: Optional[Dict[str, Dict[str, Task]]] = None,
    ) -> AgentEvaluationRunWithDetails:
        if include_details:
            tasks_by_round = tasks_by_round or {}

        context = self._build_agent_run_context(
            run_row,
            parent_round_row=parent_round_row,
            include_details=include_details,
            tasks_for_round=(tasks_by_round.get(run_row.validator_round_id) if tasks_by_round else None),
        )

        tasks = context.tasks if include_details else []
        task_solutions = context.task_solutions if include_details else []
        evaluation_results = context.evaluation_results if include_details else []

        return AgentEvaluationRunWithDetails(
            **context.run.model_dump(),
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results,
        )

    def _build_agent_run_context(
        self,
        run_row: AgentEvaluationRunORM,
        parent_round_row: Optional[RoundORM] = None,
        include_details: bool = True,
        tasks_for_round: Optional[Dict[str, Task]] = None,
    ) -> AgentRunContext:
        round_row = parent_round_row or run_row.round
        if round_row is None:
            raise ValueError(
                f"Agent run {run_row.agent_run_id} is missing round relationship"
            )

        round_model = self._deserialize_round(round_row)
        agent_run_model = self._deserialize_agent_run(run_row)
        if include_details and tasks_for_round is not None:
            task_lookup = tasks_for_round
            if agent_run_model.task_ids:
                tasks = [task_lookup[task_id] for task_id in agent_run_model.task_ids if task_id in task_lookup]
            else:
                tasks = list(task_lookup.values())
        else:
            tasks = []
        task_solutions = (
            self._convert_task_solutions(run_row.task_solutions) if include_details else []
        )
        evaluation_results = (
            self._convert_evaluations(run_row.evaluation_results) if include_details else []
        )

        return AgentRunContext(
            round=round_model,
            run=agent_run_model,
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results,
        )

    def _build_miner_performance(
        self,
        context: AgentRunContext,
        round_obj: ValidatorRound,
        weights: Dict[str, float],
    ) -> Dict[str, Any]:
        miner_uid = context.run.miner_uid or -1
        miner_info = self._resolve_miner_info(context, round_obj)
        name = miner_info.agent_name if miner_info and miner_info.agent_name else f"Miner {miner_uid}"
        hotkey = miner_info.hotkey if miner_info else None
        provider = getattr(miner_info, "provider", None) if miner_info else None
        image_url = resolve_agent_image(miner_info)

        score = context.run.avg_eval_score
        if score is None and context.evaluation_results:
            score = sum(er.final_score for er in context.evaluation_results) / len(context.evaluation_results)
        score = score or 0.0

        duration = context.run.elapsed_sec
        if duration is None and context.run.started_at and context.run.ended_at:
            duration = context.run.ended_at - context.run.started_at
        duration = duration or 0.0

        tasks_total = context.run.n_tasks_total or len(context.tasks)
        completed_tasks = context.run.n_tasks_completed
        if completed_tasks is None:
            completed_tasks = len([er for er in context.evaluation_results if er.final_score >= 0.5])

        success = (context.run.n_tasks_failed or 0) == 0
        if tasks_total:
            success = success and completed_tasks >= tasks_total
        weight = weights.get(str(miner_uid)) or weights.get(str(context.run.agent_run_id)) or 0.0
        stake = int(weight) if weight > 1 else int(weight * 1000)
        emission = int(stake * 0.05)

        return {
            "uid": miner_uid,
            "name": name,
            "hotkey": hotkey,
            "success": success,
            "score": round(score, 3),
            "duration": round(duration, 2),
            "ranking": context.run.rank or 0,
            "tasksCompleted": completed_tasks,
            "tasksTotal": tasks_total,
            "stake": stake,
            "emission": emission,
            "lastSeen": _iso_timestamp(context.run.ended_at or context.run.started_at),
            "validatorId": f"validator-{context.run.validator_uid}",
            "isSota": context.run.is_sota,
            "provider": provider,
            "imageUrl": image_url,
        }

    async def _get_round_row(
        self,
        round_identifier: Union[str, int],
        load_relationships: bool = False,
    ) -> RoundORM:
        candidates = self._round_identifier_candidates(round_identifier)
        for candidate in candidates:
            stmt = select(RoundORM)
            if load_relationships:
                stmt = stmt.options(
                    selectinload(RoundORM.agent_runs)
                    .selectinload(AgentEvaluationRunORM.task_solutions),
                    selectinload(RoundORM.agent_runs)
                    .selectinload(AgentEvaluationRunORM.evaluation_results),
                )
            stmt = stmt.where(RoundORM.validator_round_id == candidate)
            row = await self.session.scalar(stmt)
            if row:
                return row
        raise ValueError(f"Round {round_identifier} not found")

    @staticmethod
    def _round_identifier_candidates(round_identifier: Union[str, int]) -> List[str]:
        candidates: List[str] = []

        def add_candidate(value: Optional[Union[str, int]]) -> None:
            if value is None:
                return
            text = str(value).strip()
            if not text or text in candidates:
                return
            candidates.append(text)

        if isinstance(round_identifier, int):
            num = round_identifier
            add_candidate(f"round_{num:03d}")
            add_candidate(f"round_{num}")
            add_candidate(num)
            return candidates

        raw = str(round_identifier).strip()
        add_candidate(raw)

        if raw.isdigit():
            num = int(raw)
            add_candidate(f"round_{num:03d}")
            add_candidate(f"round_{num}")
            return candidates

        if raw.startswith("round_"):
            suffix = raw.split("round_", 1)[1]
            if suffix.isdigit():
                num = int(suffix)
                add_candidate(f"round_{num:03d}")
                add_candidate(f"round_{num}")
                add_candidate(num)
            return candidates

        add_candidate(f"round_{raw}")
        return candidates

    @staticmethod
    def _resolve_miner_info(context: AgentRunContext, round_obj: ValidatorRound):
        if context.run.miner_info:
            return context.run.miner_info
        if round_obj.miners:
            for miner in round_obj.miners:
                if miner.uid == context.run.miner_uid:
                    return miner
        if round_obj.sota_agents:
            for miner in round_obj.sota_agents:
                if miner.uid == context.run.miner_uid:
                    return miner
        return None

    def _deserialize_round(self, round_row: RoundORM) -> ValidatorRound:
        payload = dict(round_row.data or {})
        payload.setdefault("validator_round_id", round_row.validator_round_id)
        return ValidatorRound(**payload)

    def _deserialize_agent_run(self, run_row: AgentEvaluationRunORM) -> AgentEvaluationRun:
        payload = dict(run_row.data or {})
        payload.setdefault("agent_run_id", run_row.agent_run_id)
        payload.setdefault("validator_round_id", run_row.validator_round_id)
        payload.setdefault("validator_uid", run_row.validator_uid)
        payload.setdefault("miner_uid", run_row.miner_uid)
        payload.setdefault("is_sota", run_row.is_sota)
        return AgentEvaluationRun(**payload)

    @staticmethod
    def _convert_tasks(task_rows: List[TaskORM]) -> List[Task]:
        tasks: List[Task] = []
        for task_row in task_rows:
            data = dict(task_row.data or {})
            data.setdefault("task_id", task_row.task_id)
            data.setdefault("validator_round_id", task_row.validator_round_id)
            try:
                tasks.append(Task(**data))
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to deserialize task %s: %s", task_row.task_id, exc)
        return tasks

    @staticmethod
    def _convert_task_solutions(
        solution_rows: List[TaskSolutionORM],
    ) -> List[TaskSolution]:
        solutions: List[TaskSolution] = []
        for solution_row in solution_rows:
            data = dict(solution_row.data or {})
            data.setdefault("solution_id", solution_row.solution_id)
            data.setdefault("task_id", solution_row.task_id)
            data.setdefault("agent_run_id", solution_row.agent_run_id)
            data.setdefault("validator_round_id", solution_row.validator_round_id)
            data.setdefault("validator_uid", solution_row.validator_uid)
            data.setdefault("miner_uid", solution_row.miner_uid)
            try:
                solutions.append(TaskSolution(**data))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize task solution %s: %s",
                    solution_row.solution_id,
                    exc,
                )
        return solutions

    @staticmethod
    def _convert_evaluations(
        evaluation_rows: List[EvaluationResultORM],
    ) -> List[EvaluationResult]:
        evaluations: List[EvaluationResult] = []
        for evaluation_row in evaluation_rows:
            data = dict(evaluation_row.data or {})
            data.setdefault("evaluation_id", evaluation_row.evaluation_id)
            data.setdefault("task_id", evaluation_row.task_id)
            data.setdefault("task_solution_id", evaluation_row.task_solution_id)
            data.setdefault("agent_run_id", evaluation_row.agent_run_id)
            data.setdefault("validator_round_id", evaluation_row.validator_round_id)
            data.setdefault("validator_uid", evaluation_row.validator_uid)
            data.setdefault("miner_uid", evaluation_row.miner_uid)
            try:
                evaluations.append(EvaluationResult(**data))
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize evaluation result %s: %s",
                    evaluation_row.evaluation_id,
                    exc,
                )
        return evaluations
