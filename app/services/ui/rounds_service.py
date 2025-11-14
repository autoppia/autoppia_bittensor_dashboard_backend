from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from sqlalchemy import func, select
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
    Action,
    AgentEvaluationRun,
    AgentEvaluationRunWithDetails,
    EvaluationResult,
    TestResult,
    ValidatorRound,
    ValidatorRoundWithDetails,
    ValidatorInfo,
    MinerInfo,
    Task,
    TaskSolution,
)
from app.services.cache import CACHE_TTL, api_cache
from app.utils.images import resolve_agent_image, resolve_validator_image
from app.config import settings
from app.services.chain_state import get_current_block_estimate
from app.services.round_calc import compute_boundaries_for_round
from app.services.metagraph_service import get_validator_data, MetagraphError

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
    latest_round_number: int
    validator_rounds: List[ValidatorRoundAggregate]
    status: str = "active"  # Aggregated status from all validator rounds

    @property
    def contexts(self) -> List[AgentRunContext]:
        items: List[AgentRunContext] = []
        for entry in self.validator_rounds:
            items.extend(entry.contexts)
        return items


@dataclass
class MinerAggregate:
    """Aggregated metrics for a miner across all validator runs in a round."""

    uid: int
    name: str
    hotkey: Optional[str]
    image_url: Optional[str]
    is_sota: bool
    total_score: float = 0.0
    score_count: int = 0
    total_tasks_completed: int = 0
    total_tasks: int = 0
    total_duration: float = 0.0
    duration_count: int = 0
    total_stake: int = 0
    total_emission: int = 0
    success_runs: int = 0
    total_runs: int = 0
    last_seen_ts: float = 0.0
    last_seen_iso: Optional[str] = None
    best_run_score: float = float("-inf")
    best_validator_id: Optional[str] = None

    def update(
        self,
        performance: Dict[str, Any],
        evaluation_scores: List[float],
        tasks_total: int,
    ) -> None:
        """Update aggregate metrics with a new run performance snapshot."""
        self.total_runs += 1
        if performance.get("success"):
            self.success_runs += 1

        name = performance.get("name")
        if name and (not self.name or self.name.startswith("Miner ")):
            self.name = name

        hotkey = performance.get("hotkey")
        if hotkey and not self.hotkey:
            self.hotkey = hotkey

        image_url = performance.get("imageUrl")
        if image_url and not self.image_url:
            self.image_url = image_url

        self.total_tasks_completed += performance.get("tasksCompleted") or 0
        self.total_tasks += performance.get("tasksTotal") or 0

        duration = performance.get("duration") or 0.0
        if duration:
            self.total_duration += float(duration)
            self.duration_count += 1

        stake = performance.get("stake") or 0
        self.total_stake += int(stake)

        emission = performance.get("emission")
        if emission:
            self.total_emission += int(emission)

        last_seen_str = performance.get("lastSeen")
        if last_seen_str:
            ts = _timestamp_from_iso(last_seen_str)
            if ts >= self.last_seen_ts:
                self.last_seen_ts = ts
                self.last_seen_iso = last_seen_str

        if evaluation_scores:
            self.total_score += sum(float(score) for score in evaluation_scores)
            self.score_count += len(evaluation_scores)
        else:
            weight = int(tasks_total) if tasks_total else 1
            score_value = performance.get("score") or 0.0
            self.total_score += float(score_value) * weight
            if weight:
                self.score_count += weight

        score = performance.get("score") or 0.0
        if score > self.best_run_score:
            self.best_run_score = float(score)
            self.best_validator_id = performance.get("validatorId")

    @property
    def average_score(self) -> float:
        if self.score_count == 0:
            return 0.0
        return self.total_score / self.score_count

    @property
    def average_duration(self) -> float:
        if self.duration_count == 0:
            return 0.0
        return self.total_duration / self.duration_count

    def to_performance(self, ranking: int) -> Dict[str, Any]:
        emission = self.total_emission or int(self.total_stake * 0.05)
        return {
            "uid": self.uid,
            "name": self.name,
            "hotkey": self.hotkey,
            "success": self.success_runs > 0 and self.success_runs == self.total_runs,
            "score": round(self.average_score, 3),
            "duration": round(self.average_duration, 2),
            "ranking": ranking,
            "tasksCompleted": self.total_tasks_completed,
            "tasksTotal": self.total_tasks,
            "stake": self.total_stake,
            "emission": emission,
            "lastSeen": self.last_seen_iso or _iso_timestamp(None),
            "validatorId": self.best_validator_id,
            "isSota": self.is_sota,
            "imageUrl": self.image_url,
        }


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


def _timestamp_from_iso(value: Optional[str]) -> float:
    if not value:
        return 0.0
    normalized = value
    if value.endswith("Z"):
        normalized = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        try:
            return (
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except ValueError:
            return 0.0


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


def _round_number_from_model(
    round_model: ValidatorRound, fallback_identifier: str
) -> Optional[int]:
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
        return "finished"
    if any(status == "active" for status in normalized):
        return "active"
    if any(status == "pending" for status in normalized):
        return "pending"
    return normalized[0]


class RoundsService:
    """Read operations for rounds stored in the SQL database."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _snapshot_for_validator(round_row: RoundORM, validator_uid: Optional[int]):
        if validator_uid is None:
            return None
        snapshots = getattr(round_row, "validator_snapshots", None) or []
        for snapshot in snapshots:
            if snapshot.validator_uid == validator_uid:
                return snapshot
        return None

    def _build_validator_profile(
        self,
        *,
        round_row: Optional[RoundORM],
        validator_uid: Optional[int],
        fallback_hotkey: Optional[str] = None,
        fallback_name: Optional[str] = None,
        use_fresh_data: bool = True,
    ) -> Dict[str, Any]:
        """
        Build validator profile combining DB snapshot data with optional fresh metagraph data.

        Args:
            round_row: Database round row containing validator snapshots
            validator_uid: UID of the validator
            fallback_hotkey: Hotkey to use if not found in snapshots
            fallback_name: Name to use if not found in snapshots
            use_fresh_data: If True, attempts to fetch fresh data from metagraph first

        Returns:
            Dictionary with validator profile data
        """
        profile: Dict[str, Any] = {
            "uid": validator_uid,
            "hotkey": fallback_hotkey or "",
            "name": fallback_name,
            "stake": 0.0,
            "vtrust": 0.0,
            "version": None,
            "image_url": None,
        }

        # First, try to get fresh data from metagraph if requested
        if use_fresh_data and validator_uid is not None:
            try:
                fresh_data = get_validator_data(uid=validator_uid)
                if fresh_data:
                    if fresh_data.get("stake") is not None:
                        profile["stake"] = float(fresh_data["stake"])
                    if fresh_data.get("vtrust") is not None:
                        profile["vtrust"] = float(fresh_data["vtrust"])
                    if fresh_data.get("version") is not None:
                        profile["version"] = str(
                            fresh_data["version"]
                        )  # Keep as string
                    logger.debug(
                        f"[Validator {validator_uid}] Using fresh metagraph data in profile: "
                        f"stake={profile['stake']:.2f}, vtrust={profile['vtrust']:.4f}, version={profile['version']}"
                    )
            except MetagraphError as exc:
                logger.debug(
                    f"[Validator {validator_uid}] Fresh metagraph data unavailable: {exc}"
                )

        # Then, overlay snapshot data (for name, hotkey, image which aren't in metagraph)
        # and as fallback for stake/vtrust/version if fresh data wasn't available
        if round_row is not None:
            snapshot = self._snapshot_for_validator(round_row, validator_uid)
            if snapshot is not None:
                if snapshot.validator_hotkey:
                    profile["hotkey"] = snapshot.validator_hotkey
                if snapshot.name:
                    profile["name"] = snapshot.name
                # Only use snapshot stake/vtrust/version if we don't have fresh data
                if profile["stake"] == 0.0 and snapshot.stake is not None:
                    profile["stake"] = float(snapshot.stake)
                if profile["vtrust"] == 0.0 and snapshot.vtrust is not None:
                    profile["vtrust"] = float(snapshot.vtrust)
                if profile["version"] is None and snapshot.version:
                    profile["version"] = snapshot.version
                if snapshot.image_url:
                    profile["image_url"] = snapshot.image_url

            if not profile["hotkey"]:
                if (
                    round_row.validator_uid == validator_uid
                    and round_row.validator_hotkey
                ):
                    profile["hotkey"] = round_row.validator_hotkey

        if validator_uid is not None and not profile.get("name"):
            profile["name"] = f"Validator {validator_uid}"

        return profile

    async def list_rounds(
        self, limit: int, skip: int
    ) -> List[ValidatorRoundWithDetails]:
        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
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

    async def list_round_ids(
        self,
        limit: int = 500,
        status: Optional[str] = None,
        sort_order: str = "desc",
    ) -> List[int]:
        """
        Get lightweight list of round numbers only (no nested data).
        Super fast - use this for dropdowns and lists.
        Returns up to 500 round IDs.
        """
        stmt = select(RoundORM.round_number).distinct()

        if status:
            stmt = stmt.where(RoundORM.status == status)

        if sort_order.lower() == "desc":
            stmt = stmt.order_by(RoundORM.round_number.desc())
        else:
            stmt = stmt.order_by(RoundORM.round_number.asc())

        if limit > 0:
            stmt = stmt.limit(limit)

        result = await self.session.execute(stmt)
        round_numbers = [row[0] for row in result.all() if row[0] is not None]
        return round_numbers

    async def get_round_basic(
        self, round_identifier: Union[str, int]
    ) -> Dict[str, Any]:
        """
        Get basic round info without nested agent runs, tasks, solutions, or evaluations.
        Returns only essential fields for round page header and status display.
        """
        aggregated = await self._fetch_aggregated_round(round_identifier)

        current = await self.get_current_round_overview()
        latest_round_number = current["round"] if current else aggregated.round_number
        records = [entry.record for entry in aggregated.validator_rounds]
        overview = self._build_round_day_overview_from_records(
            aggregated.round_number,
            records,
            latest_round_number,
        )

        # Only include basic validator round info without nested agent runs
        basic_validator_rounds: List[Dict[str, Any]] = []
        for entry in aggregated.validator_rounds:
            summary = self._summarize_validator_round(entry.record)
            # Only include status, no nested agentEvaluationRuns
            basic_validator_rounds.append(
                {
                    "validatorRoundId": summary.get("validatorRoundId"),
                    "validatorUid": summary.get("validatorUid"),
                    "validatorName": summary.get("validatorName"),
                    "validatorHotkey": summary.get("validatorHotkey"),
                    "status": summary.get("status"),
                    "startTime": summary.get("startTime"),
                    "endTime": summary.get("endTime"),
                    "totalTasks": summary.get("totalTasks"),
                    "completedTasks": summary.get("completedTasks"),
                }
            )

        overview["validatorRounds"] = basic_validator_rounds
        overview["id"] = aggregated.round_number
        overview["round"] = aggregated.round_number
        overview["roundNumber"] = aggregated.round_number

        return overview

    async def get_round(self, round_identifier: Union[str, int]) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)

        cache_key: Optional[str] = None
        if self._is_final_round(aggregated):
            cache_key = self._round_cache_key("round:detail", aggregated.round_number)
            cached_payload = api_cache.get(
                cache_key,
                force=settings.ENABLE_FINAL_ROUND_CACHE,
            )
            if cached_payload is not None:
                return cached_payload

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

        if cache_key and settings.ENABLE_FINAL_ROUND_CACHE:
            api_cache.set(
                cache_key,
                overview,
                CACHE_TTL["round_detail_final"],
                force=True,
            )
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
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.miner_snapshots
                ),
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.validator_snapshots
                ),
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
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.miner_snapshots
                ),
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.validator_snapshots
                ),
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
        agent_run_ids: Optional[Iterable[str]] = None,
    ) -> List[AgentRunContext]:
        stmt = select(AgentEvaluationRunORM).options(
            selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                RoundORM.miner_snapshots
            ),
            selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                RoundORM.validator_snapshots
            ),
        )

        if include_details:
            stmt = stmt.options(
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluation_results),
            )

        stmt = stmt.order_by(AgentEvaluationRunORM.id.desc())

        run_id_list: Optional[List[str]] = None
        if agent_run_ids is not None:
            run_id_list = [run_id for run_id in agent_run_ids]
            if not run_id_list:
                return []
            stmt = stmt.where(AgentEvaluationRunORM.agent_run_id.in_(run_id_list))
        else:
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

        contexts = [
            self._build_agent_run_context(
                run_row,
                include_details=include_details,
                tasks_for_round=(
                    tasks_by_round.get(run_row.validator_round_id)
                    if include_details
                    else None
                ),
            )
            for run_row in run_rows
        ]
        self._assign_ranks(contexts)

        if run_id_list:
            order_map = {run_id: index for index, run_id in enumerate(run_id_list)}
            contexts.sort(
                key=lambda ctx: order_map.get(ctx.run.agent_run_id, len(order_map))
            )
        return contexts

    async def get_agent_run_context(self, agent_run_id: str) -> AgentRunContext:
        stmt = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.miner_snapshots
                ),
                selectinload(AgentEvaluationRunORM.validator_round).selectinload(
                    RoundORM.validator_snapshots
                ),
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
        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
            .order_by(RoundORM.id.desc())
            .limit(200)  # Limitar a los 200 rounds más recientes para mejor performance
        )
        rows = await self.session.scalars(stmt)
        records: List[RoundRecord] = []
        for row in rows:
            try:
                model = self._deserialize_round(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to deserialize round %s: %s (skipping)",
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

    async def _count_distinct_rounds(self) -> int:
        stmt = select(func.count(func.distinct(RoundORM.round_number))).where(
            RoundORM.round_number.is_not(None)
        )
        result = await self.session.scalar(stmt)
        return int(result or 0)

    async def _fetch_round_numbers_page(
        self,
        *,
        offset: int,
        limit: int,
        sort_order: str,
    ) -> List[int]:
        order_desc = sort_order.lower() != "asc"
        order_clause = (
            RoundORM.round_number.desc() if order_desc else RoundORM.round_number.asc()
        )
        stmt = (
            select(RoundORM.round_number)
            .where(RoundORM.round_number.is_not(None))
            .group_by(RoundORM.round_number)
            .order_by(order_clause)
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return [int(number) for number in result]

    async def _get_round_records_for_round_numbers(
        self,
        round_numbers: Iterable[int],
    ) -> Dict[int, List[RoundRecord]]:
        numbers = [number for number in round_numbers if number is not None]
        if not numbers:
            return {}

        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
            .where(RoundORM.round_number.in_(numbers))
        )
        rows = await self.session.scalars(stmt)
        record_map: Dict[int, List[RoundRecord]] = {number: [] for number in numbers}
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

            value = _round_number_from_model(model, row.validator_round_id)
            if value is None:
                continue
            if value in record_map:
                record_map[value].append(RoundRecord(row=row, model=model))

        # Remove empty entries to match the exact dataset present in the database.
        return {key: records for key, records in record_map.items() if records}

    async def _get_latest_round_number(self) -> Optional[int]:
        stmt = (
            select(RoundORM.round_number)
            .where(RoundORM.round_number.is_not(None))
            .order_by(RoundORM.round_number.desc())
            .limit(1)
        )
        return await self.session.scalar(stmt)

    async def _fetch_round_records_by_number(
        self, round_number: int
    ) -> Tuple[List[RoundRecord], int]:
        record_map = await self._get_round_records_for_round_numbers([round_number])
        matched = record_map.get(round_number, [])
        latest_round_number = await self._get_latest_round_number()
        if latest_round_number is None:
            latest_round_number = round_number
        return matched, latest_round_number

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
            row = await self._get_round_row(raw, load_relationships=True)
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
        # Ensure UI aggregation does not trigger unintended writes.
        # NOTE: SQLAlchemy's `no_autoflush` is a synchronous context manager,
        # even when using `AsyncSession`. Using `async with` here raises
        # TypeError: '_GeneratorContextManager' does not support async protocol.
        with self.session.no_autoflush:
            round_number = await self._resolve_round_number(round_identifier)
            records, latest_round_number = await self._fetch_round_records_by_number(
                round_number
            )
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
                self._recalculate_round_from_contexts(record, contexts)
                validator_rounds.append(
                    ValidatorRoundAggregate(
                        record=record,
                        contexts=contexts,
                    )
                )
            # Calculate aggregated status from all validator rounds
            statuses = [vr.record.model.status or "finished" for vr in validator_rounds]
            aggregated_status = _aggregate_status(statuses)

            return AggregatedRound(
                round_number=round_number,
                latest_round_number=latest_round_number or round_number,
                validator_rounds=validator_rounds,
                status=aggregated_status,
            )

    def _aggregate_round_data(
        self,
        aggregated: AggregatedRound,
    ) -> Tuple[Dict[int, MinerAggregate], Dict[str, Dict[str, Any]], Dict[str, Any]]:
        miner_aggregates: Dict[int, MinerAggregate] = {}
        best_by_validator: Dict[str, Dict[str, Any]] = {}
        miner_ids: set[int] = set()
        active_miner_ids: set[int] = set()
        completed_tasks = 0
        total_tasks = 0
        tasks_per_validator: List[float] = []
        scores: List[float] = []
        validator_top_scores: List[float] = []
        durations: List[float] = []
        total_stake = 0.0

        for entry in aggregated.validator_rounds:
            round_obj = entry.round
            if round_obj.miners:
                for miner in round_obj.miners:
                    if miner.uid is not None:
                        miner_ids.add(miner.uid)

            contexts = entry.contexts
            non_sota_contexts = [ctx for ctx in contexts if not ctx.run.is_sota]
            if non_sota_contexts:
                total_tasks_for_contexts = sum(
                    (ctx.run.n_tasks_total or len(ctx.tasks) or 0)
                    for ctx in non_sota_contexts
                )
                avg_tasks_for_validator = (
                    total_tasks_for_contexts / len(non_sota_contexts)
                    if non_sota_contexts
                    else 0.0
                )
                tasks_per_validator.append(avg_tasks_for_validator)
            elif round_obj.n_tasks is not None:
                tasks_per_validator.append(float(round_obj.n_tasks))

            weights = round_obj.weights or {}
            per_validator_scores: List[float] = []

            for ctx in contexts:
                if ctx.run.is_sota:
                    continue

                miner_uid = ctx.run.miner_uid
                if miner_uid is not None:
                    miner_ids.add(miner_uid)

                performance = self._build_miner_performance(ctx, round_obj, weights)
                uid = performance["uid"]

                aggregate = miner_aggregates.get(uid)
                if aggregate is None:
                    aggregate = MinerAggregate(
                        uid=uid,
                        name=performance.get("name") or f"Miner {uid}",
                        hotkey=performance.get("hotkey"),
                        image_url=performance.get("imageUrl"),
                        is_sota=bool(performance.get("isSota")),
                    )
                    miner_aggregates[uid] = aggregate

                evaluation_scores = [
                    er.final_score
                    for er in ctx.evaluation_results
                    if er.final_score is not None
                ]
                tasks_total = ctx.run.n_tasks_total
                if tasks_total is None:
                    tasks_total = len(ctx.tasks)
                aggregate.update(performance, evaluation_scores, tasks_total or 0)

                validator_id = performance.get("validatorId")
                if validator_id:
                    current_best = best_by_validator.get(validator_id)
                    if current_best is None or performance.get(
                        "score", 0.0
                    ) > current_best.get("score", 0.0):
                        best_by_validator[validator_id] = performance

                completed = ctx.run.n_tasks_completed
                if completed is None:
                    completed = len(
                        [
                            er
                            for er in ctx.evaluation_results
                            if er.final_score is not None and er.final_score >= 0.5
                        ]
                    )
                completed_tasks += completed

                total = ctx.run.n_tasks_total
                if total is None:
                    total = len(ctx.tasks)
                total_tasks += total

                score = performance.get("score")
                if score is not None:
                    scores.append(score)
                    per_validator_scores.append(score)

                if ctx.run.started_at and ctx.run.ended_at:
                    durations.append(ctx.run.ended_at - ctx.run.started_at)

                if completed > 0 and miner_uid is not None:
                    active_miner_ids.add(miner_uid)

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

        metrics = {
            "miner_ids": miner_ids,
            "active_miner_ids": active_miner_ids,
            "completed_tasks": completed_tasks,
            "total_tasks": total_tasks,
            "tasks_per_validator": tasks_per_validator,
            "scores": scores,
            "validator_top_scores": validator_top_scores,
            "durations": durations,
            "total_stake": total_stake,
        }
        return miner_aggregates, best_by_validator, metrics

    @staticmethod
    def _estimate_completed_tasks(round_obj: ValidatorRound) -> int:
        summary = getattr(round_obj, "summary", {}) or {}
        completed = summary.get("completed_tasks") or summary.get("completedTasks")
        if completed is None:
            completed = summary.get("task_solutions")
        if completed is None and round_obj.status == "finished":
            completed = round_obj.n_tasks or 0
        if completed is None:
            completed = 0
        completed = int(completed)
        total_tasks = round_obj.n_tasks or 0
        if total_tasks and completed > total_tasks:
            return total_tasks
        return completed

    def _summarize_validator_round(self, record: RoundRecord) -> Dict[str, Any]:
        round_obj = record.model
        validator_uid = round_obj.validator_uid or record.validator_uid
        validator_info = getattr(round_obj, "validator_info", None)
        profile = self._build_validator_profile(
            round_row=record.row,
            validator_uid=validator_uid,
            fallback_hotkey=round_obj.validator_hotkey
            or (validator_info.hotkey if validator_info else None),
            fallback_name=validator_info.name if validator_info else None,
        )

        validator_name = profile.get("name")
        if not validator_name:
            validator_name = (
                f"Validator {validator_uid}"
                if validator_uid is not None
                else "Validator"
            )
        validator_hotkey = profile.get("hotkey") or ""
        icon = resolve_validator_image(
            validator_name, existing=profile.get("image_url")
        )

        completed_tasks = self._estimate_completed_tasks(round_obj)

        return {
            "validatorRoundId": record.validator_round_id,
            "validatorUid": validator_uid,
            "validatorName": validator_name,
            "validatorHotkey": validator_hotkey,
            "status": (round_obj.status or "finished"),
            "startTime": _iso_timestamp(round_obj.started_at),
            "endTime": (
                _iso_timestamp(round_obj.ended_at) if round_obj.ended_at else None
            ),
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

        started_at_values = [
            record.model.started_at
            for record in records
            if record.model.started_at is not None
        ]
        ended_at_values = [
            record.model.ended_at
            for record in records
            if record.model.ended_at is not None
        ]
        started_at = min(started_at_values) if started_at_values else None
        ended_at = max(ended_at_values) if ended_at_values else None

        statuses = [(record.model.status or "finished") for record in records]
        status = _aggregate_status(statuses)

        # Chain-aware override: if the chain has already moved past this
        # round's window, mark it as completed regardless of any stale
        # validator row statuses (e.g., a validator that never called /finish).
        try:
            current_block_est = get_current_block_estimate()
        except Exception:
            current_block_est = None
        if current_block_est is not None:
            bounds = compute_boundaries_for_round(round_number)
            if current_block_est > bounds.end_block:
                status = "finished"
            elif current_block_est <= bounds.start_block:
                # If chain hasn't reached the window yet, prefer pending
                # unless DB already says completed (keep completed if so).
                if status != "finished":
                    status = "pending"
        total_tasks = sum(record.model.n_tasks or 0 for record in records)
        completed_tasks = sum(
            self._estimate_completed_tasks(record.model) for record in records
        )

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

        progress_ratio = (
            1.0
            if status == "finished"
            else (min(1.0, completed_tasks / total_tasks) if total_tasks else 0.0)
        )
        current_block = int(
            start_block + (end_block_value - start_block) * progress_ratio
        )
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
            "validatorRounds": [
                self._summarize_validator_round(record) for record in records
            ],
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

    @staticmethod
    def _determine_latest_round_number(grouped: Dict[int, List[RoundRecord]]) -> int:
        """
        Identify the most recent logical round from grouped validator rounds.

        Preference order:
            1. Groups containing an active/pending validator round.
            2. Newest start timestamp within the group.
            3. Highest round number as a final tie-breaker.
        """
        if not grouped:
            raise ValueError("Cannot determine latest round from empty grouping")

        active_statuses = {
            "active",
            "running",
            "pending",
            "in_progress",
            "evaluating",
            "waiting",
        }
        best_key: Optional[Tuple[int, float, int]] = None
        best_number: Optional[int] = None

        for number, records in grouped.items():
            if not records:
                continue

            has_active = False
            latest_start = 0.0

            for record in records:
                status = (record.model.status or "").strip().lower()
                if status in active_statuses:
                    has_active = True

                started_at = record.model.started_at
                if started_at is None:
                    started_at = getattr(record.row, "started_at", None)
                if started_at is None:
                    created_at = getattr(record.row, "created_at", None)
                    if isinstance(created_at, datetime):
                        started_at = created_at.timestamp()
                    else:
                        try:
                            started_at = (
                                float(created_at) if created_at is not None else None
                            )
                        except (TypeError, ValueError):
                            started_at = None

                try:
                    numeric_start = float(started_at) if started_at is not None else 0.0
                except (TypeError, ValueError):
                    numeric_start = 0.0
                latest_start = max(latest_start, numeric_start)

            key = (1 if has_active else 0, latest_start, number)
            if best_key is None or key > best_key:
                best_key = key
                best_number = number

        if best_number is not None:
            return best_number
        return max(grouped.keys())

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

        start_candidates: List[int] = []
        for record in records:
            start_value = record.model.start_block
            if start_value is None:
                start_value = getattr(record.row, "start_block", None)
            if start_value is not None:
                try:
                    start_candidates.append(int(start_value))
                except (TypeError, ValueError):
                    continue
        start_block = min(start_candidates) if start_candidates else 0

        end_block_candidates: List[int] = []
        statuses: List[str] = []
        elapsed_values: List[float] = []
        for record in records:
            round_obj = record.model
            end_block = round_obj.end_block
            if end_block is None:
                start_value = round_obj.start_block
                if start_value is None:
                    start_value = getattr(record.row, "start_block", start_block)
                max_blocks = round_obj.max_blocks or 0
                try:
                    end_block = int(start_value or 0) + int(max_blocks)
                except (TypeError, ValueError):
                    end_block = None
            end_block_candidates.append(end_block)
            statuses.append((round_obj.status or "").lower())
            if round_obj.elapsed_sec is not None:
                elapsed_values.append(round_obj.elapsed_sec)

        safe_end_candidates = [
            value for value in end_block_candidates if isinstance(value, (int, float))
        ]
        end_block_value = (
            int(max(safe_end_candidates)) if safe_end_candidates else start_block
        )
        is_completed = all(status == "finished" for status in statuses if status)

        if total_tasks:
            progress_ratio = min(1.0, max(0.0, completed_tasks / total_tasks))
        else:
            progress_ratio = 1.0 if is_completed else 0.0
        if is_completed:
            progress_ratio = 1.0

        try:
            current_block = int(
                start_block + (end_block_value - start_block) * progress_ratio
            )
        except TypeError:
            current_block = start_block
        current_block = min(current_block, end_block_value)
        blocks_remaining = max(end_block_value - current_block, 0)

        average_elapsed = (
            sum(elapsed_values) / len(elapsed_values) if elapsed_values else None
        )
        average_task_time = (
            (average_elapsed / completed_tasks)
            if average_elapsed and completed_tasks
            else 0.0
        )
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
        optimized_sort_fields = {"id", "round", "roundNumber"}
        if status is None and sort_by in optimized_sort_fields:
            return await self._list_rounds_paginated_optimized(
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            )

        # Fallback: keep legacy behaviour for complex sort or status filters.
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

        latest_round_number = self._determine_latest_round_number(grouped)
        entries = [
            self._build_round_day_overview_from_records(
                number, group, latest_round_number
            )
            for number, group in grouped.items()
        ]

        entries = [
            entry for entry in entries if entry.get("validatorRoundCount", 0) > 0
        ]

        if status:
            entries = [entry for entry in entries if entry["status"] == status]

        self._sort_round_entries(entries, sort_by, sort_order)

        dataset = entries

        total = len(dataset)
        start = max(0, (page - 1) * limit)
        end = start + limit
        return dataset[start:end], total

    async def _list_rounds_paginated_optimized(
        self,
        *,
        page: int,
        limit: int,
        sort_by: str,
        sort_order: str,
    ) -> Tuple[List[Dict[str, Any]], int]:
        total_rounds = await self._count_distinct_rounds()
        if total_rounds == 0:
            return [], 0

        offset = max(0, (page - 1) * limit)
        round_numbers = await self._fetch_round_numbers_page(
            offset=offset,
            limit=limit,
            sort_order=sort_order,
        )
        if not round_numbers:
            return [], total_rounds

        record_map = await self._get_round_records_for_round_numbers(round_numbers)
        latest_round_number = await self._get_latest_round_number()
        if latest_round_number is None and round_numbers:
            latest_round_number = max(round_numbers)

        entries: List[Dict[str, Any]] = []
        for number in round_numbers:
            records = record_map.get(number, [])
            if not records:
                continue
            entry = self._build_round_day_overview_from_records(
                number,
                records,
                latest_round_number or number,
            )
            if entry.get("validatorRoundCount", 0) > 0:
                entries.append(entry)

        return entries, total_rounds

    async def get_current_round_overview(self) -> Optional[Dict[str, Any]]:
        """Return the current round based on chain height, with DB fallback.

        Preference order:
          1) Use bittensor chain height to compute the current logical round
             via compute_round_number(). If we have DB records for that round,
             aggregate them. Otherwise, synthesize a minimal overview from the
             chain boundaries so the UI can still render.
          2) If chain height is unavailable, fall back to the latest round
             inferred from DB rows.
        """
        try:
            from app.services.chain_state import get_current_block_estimate
            from app.services.round_calc import (
                compute_round_number,
                compute_boundaries_for_round,
                progress_for_block,
            )
        except Exception:
            get_current_block_estimate = None  # type: ignore
            compute_round_number = None  # type: ignore
            compute_boundaries_for_round = None  # type: ignore
            progress_for_block = None  # type: ignore

        # Chain-based path using cached estimate only (no live fetch per request)
        if get_current_block_estimate is not None and compute_round_number is not None:
            current_block = get_current_block_estimate()
            if current_block is not None and int(current_block) > 0:
                try:
                    number = int(compute_round_number(int(current_block)))  # type: ignore[arg-type]
                except Exception:
                    number = 0
                if number > 0:
                    # If we have DB records for this chain-derived round, aggregate
                    try:
                        records, _ = await self._fetch_round_records_by_number(number)
                    except Exception:
                        records = []
                    if records:
                        return self._build_round_day_overview_from_records(
                            number, records, number
                        )
                    # Don't synthesize a minimal overview for rounds without data
                    # Instead, fall through to DB fallback to return the most recent round with actual data
                    # This prevents showing empty rounds that validators haven't started yet

        # Fallback: infer from DB rows
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
        latest_round_number = self._determine_latest_round_number(grouped)
        group = grouped[latest_round_number]
        return self._build_round_day_overview_from_records(
            latest_round_number,
            group,
            latest_round_number,
        )

    async def get_round_overview(
        self, round_identifier: Union[str, int]
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(
            round_identifier, include_details=False
        )
        current = await self.get_current_round_overview()
        latest_round_number = current["round"] if current else aggregated.round_number
        records = [entry.record for entry in aggregated.validator_rounds]
        return self._build_round_day_overview_from_records(
            aggregated.round_number,
            records,
            latest_round_number,
        )

    async def get_round_statistics(
        self, round_identifier: Union[str, int]
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        cache_key: Optional[str] = None
        if self._is_final_round(aggregated):
            cache_key = self._round_cache_key(
                "round:statistics", aggregated.round_number
            )
            cached_payload = api_cache.get(
                cache_key,
                force=settings.ENABLE_FINAL_ROUND_CACHE,
            )
            if cached_payload is not None and "winnerAverageScore" in cached_payload:
                return cached_payload
        miner_aggregates, _, metrics = self._aggregate_round_data(aggregated)

        total_validators = len(aggregated.validator_rounds) or 0
        total_tasks = metrics["total_tasks"]
        completed_tasks = metrics["completed_tasks"]
        total_stake = metrics["total_stake"]
        tasks_per_validator = metrics["tasks_per_validator"]
        scores = metrics["scores"]
        validator_top_scores = metrics["validator_top_scores"]
        durations = metrics["durations"]

        winner_uid: Optional[int] = None
        winner_average = 0.0
        for uid, aggregate in miner_aggregates.items():
            avg_score = aggregate.average_score
            if avg_score > winner_average:
                winner_average = avg_score
                winner_uid = uid

        validator_average_top = (
            sum(validator_top_scores) / len(validator_top_scores)
            if validator_top_scores
            else (sum(scores) / len(scores) if scores else 0.0)
        )
        top_score = max(scores) if scores else 0.0
        success_rate = (completed_tasks / total_tasks * 100.0) if total_tasks else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        total_emission = int(total_stake * 0.05) if total_stake else 0
        average_tasks_per_validator_per_miner = (
            sum(tasks_per_validator) / len(tasks_per_validator)
            if tasks_per_validator
            else 0.0
        )

        payload = {
            "roundId": aggregated.round_number,
            "totalMiners": len(metrics["miner_ids"]),
            "activeMiners": len(metrics["active_miner_ids"]),
            "totalTasks": total_tasks,
            "completedTasks": completed_tasks,
            "totalValidators": total_validators,
            "averageTasksPerValidator": round(average_tasks_per_validator_per_miner, 2),
            "averageScore": round(winner_average, 3),
            "winnerAverageScore": round(winner_average, 3),
            "winnerMinerUid": winner_uid,
            "validatorAverageTopScore": round(validator_average_top, 3),
            "topScore": round(top_score, 3),
            "successRate": round(success_rate, 2),
            "averageDuration": round(average_duration, 2),
            "totalStake": int(total_stake),
            "totalEmission": total_emission,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }
        if cache_key and settings.ENABLE_FINAL_ROUND_CACHE:
            api_cache.set(
                cache_key,
                payload,
                CACHE_TTL["round_statistics_final"],
                force=True,
            )
        return payload

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

        cache_key: Optional[str] = None
        if self._is_final_round(aggregated):
            cache_key = self._round_cache_key(
                "round:miners",
                aggregated.round_number,
                {
                    "page": page,
                    "limit": limit,
                    "sort": sort_by,
                    "order": sort_order,
                    "success": success,
                    "min": min_score,
                    "max": max_score,
                },
            )
            cached_payload = api_cache.get(
                cache_key,
                force=settings.ENABLE_FINAL_ROUND_CACHE,
            )
            if cached_payload is not None:
                return cached_payload

        miners: List[Dict[str, Any]] = []
        benchmark_map: Dict[str, Dict[str, Any]] = {}

        for entry_data in aggregated.validator_rounds:
            round_obj = entry_data.round
            weights = round_obj.weights or {}

            for ctx in entry_data.contexts:
                miner_entry = self._build_miner_performance(ctx, round_obj, weights)

                if success is not None and miner_entry["success"] != success:
                    continue
                if min_score is not None and miner_entry["score"] < min_score:
                    continue
                if max_score is not None and miner_entry["score"] > max_score:
                    continue

                if miner_entry.get("isSota"):
                    key = (
                        entry_data.validator_round_id
                        or str(entry_data.validator_uid)
                        or str(miner_entry.get("uid"))
                    )
                    existing = benchmark_map.get(key)
                    if existing is None:
                        record = dict(miner_entry)
                        sources = []
                        if miner_entry.get("validatorId"):
                            sources.append(miner_entry["validatorId"])
                        record["validatorSources"] = sources
                        benchmark_map[key] = record
                    else:
                        if miner_entry["score"] > existing.get("score", 0.0):
                            existing.update(miner_entry)
                        sources = existing.get("validatorSources") or []
                        if (
                            miner_entry.get("validatorId")
                            and miner_entry["validatorId"] not in sources
                        ):
                            sources.append(miner_entry["validatorId"])
                        existing["validatorSources"] = sources
                else:
                    miners.append(miner_entry)

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

        response_payload = {
            "miners": paginated,
            "benchmarks": benchmarks,
            "total": total,
            "page": page,
            "limit": limit,
        }
        if cache_key and settings.ENABLE_FINAL_ROUND_CACHE:
            api_cache.set(
                cache_key,
                response_payload,
                CACHE_TTL["round_miners_final"],
                force=True,
            )
        return response_payload

    async def get_round_validators(
        self,
        round_identifier: Union[str, int],
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(
            round_identifier, include_details=True
        )

        cache_key: Optional[str] = None
        if self._is_final_round(aggregated):
            cache_key = self._round_cache_key(
                "round:validators", aggregated.round_number
            )
            cached_payload = api_cache.get(
                cache_key,
                force=settings.ENABLE_FINAL_ROUND_CACHE,
            )
            if cached_payload is not None:
                return cached_payload
        validator_map: Dict[str, Dict[str, Any]] = {}

        for entry in aggregated.validator_rounds:
            round_obj = entry.round
            weights = round_obj.weights or {}
            contexts_by_validator: Dict[int, List[AgentRunContext]] = {}
            for ctx in entry.contexts:
                contexts_by_validator.setdefault(ctx.run.validator_uid, []).append(ctx)

            last_seen = _iso_timestamp(round_obj.ended_at or round_obj.started_at)

            for validator in round_obj.validators:
                runs = contexts_by_validator.get(validator.uid, [])
                valid_runs = [run for run in runs if not run.run.is_sota]
                if valid_runs:
                    total_tasks_avg = sum(
                        run.run.n_tasks_total or len(run.tasks) or 0
                        for run in valid_runs
                    ) / len(valid_runs)
                    completed_tasks_avg = sum(
                        (
                            run.run.n_tasks_completed
                            if run.run.n_tasks_completed is not None
                            else len(
                                [
                                    er
                                    for er in run.evaluation_results
                                    if er.final_score >= 0.5
                                ]
                            )
                        )
                        for run in valid_runs
                    ) / len(valid_runs)
                else:
                    total_tasks_avg = float(round_obj.n_tasks or 0)
                    completed_tasks_avg = float(round_obj.n_tasks or 0)
                total_tasks = int(round(total_tasks_avg))
                completed_tasks = int(round(completed_tasks_avg))
                miner_ids = {
                    run.run.miner_uid
                    for run in runs
                    if run.run.miner_uid is not None and not run.run.is_sota
                }
                total_miners = len(miner_ids)
                if total_miners == 0:
                    snapshot_ids = {
                        snapshot.miner_uid
                        for snapshot in getattr(round_obj, "miners", []) or []
                        if getattr(snapshot, "miner_uid", None) is not None
                        and not getattr(snapshot, "is_sota", False)
                    }
                    total_miners = len(snapshot_ids)
                    if total_miners == 0 and round_obj.n_miners:
                        try:
                            total_miners = int(round(round_obj.n_miners))
                        except (TypeError, ValueError):
                            total_miners = 0
                active_miners = len(
                    [
                        run
                        for run in runs
                        if (run.run.n_tasks_completed or 0) > 0
                        and not run.run.is_sota
                        and run.run.miner_uid is not None
                    ]
                )
                scores: List[float] = []
                for run in runs:
                    score = run.run.avg_eval_score
                    if score is None and run.evaluation_results:
                        score = sum(
                            er.final_score for er in run.evaluation_results
                        ) / len(run.evaluation_results)
                    if score is not None:
                        scores.append(score)
                average_score = sum(scores) / len(scores) if scores else 0.0
                top_score = max(scores) if scores else 0.0
                completion_rate = (
                    (completed_tasks_avg / total_tasks_avg) if total_tasks_avg else 0.0
                )

                status = "inactive"
                if runs:
                    first = runs[0]
                    if first.run.ended_at:
                        if (
                            datetime.now(timezone.utc).timestamp() - first.run.ended_at
                        ) > 3600:
                            status = "inactive"
                        else:
                            status = "active"
                    else:
                        status = "active"
                elif round_obj.status == "active":
                    status = "active"

                profile = self._build_validator_profile(
                    round_row=entry.record.row,
                    validator_uid=validator.uid,
                    fallback_hotkey=validator.hotkey,
                    fallback_name=validator.name,
                )
                validator_name = (
                    validator.name
                    or profile.get("name")
                    or (
                        f"Validator {validator.uid}"
                        if validator.uid is not None
                        else "Validator"
                    )
                )
                hotkey = profile.get("hotkey") or validator.hotkey
                icon = resolve_validator_image(
                    validator_name, existing=profile.get("image_url")
                )

                weight = (
                    float(validator.stake)
                    if validator.stake is not None
                    else float(profile.get("stake") or 0.0)
                )
                trust = (
                    float(validator.vtrust)
                    if validator.vtrust is not None
                    else float(profile.get("vtrust") or 0.0)
                )
                version_text = validator.version or profile.get("version")

                top_miner_entry: Optional[Dict[str, Any]] = None
                if runs:
                    best_run: Optional[AgentRunContext] = None
                    best_score = float("-inf")
                    for run in runs:
                        if run.run.is_sota or run.run.miner_uid is None:
                            continue
                        run_score = run.run.avg_eval_score
                        if run_score is None and run.evaluation_results:
                            run_score = sum(
                                er.final_score for er in run.evaluation_results
                            ) / len(run.evaluation_results)
                        run_score = run_score or 0.0
                        if run_score > best_score:
                            best_score = run_score
                            best_run = run
                    if best_run is not None:
                        top_miner_entry = self._build_miner_performance(
                            best_run, round_obj, weights
                        )

                key = f"{entry.validator_round_id}:{validator.uid}"
                validator_map[key] = {
                    "id": f"validator-{validator.uid}",
                    "validatorRoundId": entry.validator_round_id,
                    "name": validator_name,
                    "hotkey": hotkey or "",
                    "icon": icon,
                    "status": status,
                    "totalTasks": total_tasks,
                    "completedTasks": completed_tasks,
                    "totalMiners": total_miners,
                    "activeMiners": active_miners,
                    "averageScore": round(average_score, 3),
                    "topScore": round(top_score, 3),
                    "weight": int(weight),
                    "trust": round(trust, 3),
                    "version": (
                        version_text if version_text else None
                    ),  # Keep as string: "10.1.0"
                    "stake": int(weight),
                    "emission": int(weight * 0.05),
                    "lastSeen": last_seen,
                    "uptime": round(min(100.0, completion_rate * 100.0), 2),
                    **({"topMiner": top_miner_entry} if top_miner_entry else {}),
                }

        validators = list(validator_map.values())
        validators.sort(key=lambda item: (item["validatorRoundId"], item["name"]))
        payload = {"validators": validators, "total": len(validators)}
        if cache_key and settings.ENABLE_FINAL_ROUND_CACHE:
            api_cache.set(
                cache_key,
                payload,
                CACHE_TTL["round_validators_final"],
                force=True,
            )
        return payload

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
        raise ValueError(
            f"Validator {validator_identifier} not found in round {round_identifier}"
        )

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
            entry.round.started_at
            for entry in aggregated.validator_rounds
            if entry.round.started_at is not None
        ]
        end_candidates = [
            entry.round.ended_at
            for entry in aggregated.validator_rounds
            if entry.round.ended_at is not None
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
                    "metadata": {
                        "roundId": aggregated_key,
                        "validatorRoundId": round_key,
                    },
                }
            )
            if round_obj.ended_at:
                events.append(
                    {
                        "id": f"{round_key}_ended",
                        "type": "validator_round_ended",
                        "message": f"Validator round {round_key} completed",
                        "timestamp": _iso_timestamp(round_obj.ended_at),
                        "metadata": {
                            "roundId": aggregated_key,
                            "validatorRoundId": round_key,
                        },
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
                            "timestamp": _iso_timestamp(
                                eval_ts or ctx.run.ended_at or ctx.run.started_at
                            ),
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
        try:
            from app.services.chain_state import get_current_block_estimate
            from app.services.round_calc import (
                compute_boundaries_for_round,
                progress_for_block,
                block_to_epoch,
            )

            current_block = get_current_block_estimate()
        except Exception:
            current_block = None

        if current_block is not None:
            round_number = await self._resolve_round_number(round_identifier)
            records, _ = await self._fetch_round_records_by_number(round_number)
            if not records:
                raise ValueError(f"Round {round_identifier} not found")

            statuses = [record.model.status or "finished" for record in records]
            aggregated_status = _aggregate_status(statuses)

            bounds = compute_boundaries_for_round(round_number)

            # If round is officially finished, force 100% progress
            if aggregated_status == "finished":
                progress_value = 1.0
                blocks_remaining = 0
                display_block = bounds.end_block
            else:
                # Active or evaluating_finished: use real current block
                progress_value = progress_for_block(current_block, bounds)
                blocks_remaining = max(bounds.end_block - current_block, 0)
                display_block = current_block

            seconds_remaining = blocks_remaining * 12
            return {
                "roundId": round_number,
                "currentBlock": display_block,
                "startBlock": bounds.start_block,
                "endBlock": bounds.end_block,
                "blocksRemaining": blocks_remaining,
                "progress": progress_value,
                "startEpoch": bounds.start_epoch,
                "endEpoch": bounds.end_epoch,
                "currentEpoch": block_to_epoch(current_block),
                "estimatedTimeRemaining": _time_remaining(seconds_remaining),
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

        aggregated = await self._fetch_aggregated_round(
            round_identifier, include_details=False
        )

        # Fallback to task-based estimate when chain is unavailable
        records = [entry.record for entry in aggregated.validator_rounds]
        total_tasks = sum(record.model.n_tasks or 0 for record in records)
        completed_tasks = sum(
            self._estimate_completed_tasks(record.model) for record in records
        )
        progress = self._compute_aggregated_progress(
            records, completed_tasks, total_tasks
        )
        return {
            "roundId": aggregated.round_number,
            "currentBlock": progress.get("currentBlock", 0),
            "startBlock": progress.get("startBlock", 0),
            "endBlock": progress.get("endBlock", 0),
            "blocksRemaining": progress.get("blocksRemaining", 0),
            "progress": progress.get("progress", 0.0),
            "estimatedTimeRemaining": progress.get(
                "estimatedTimeRemaining", _time_remaining(0)
            ),
            "lastUpdated": progress.get(
                "lastUpdated", datetime.now(timezone.utc).isoformat()
            ),
        }

    async def get_top_miners(
        self,
        round_identifier: Union[str, int],
        limit: int,
    ) -> Dict[str, Any]:
        aggregated = await self._fetch_aggregated_round(round_identifier)
        miner_aggregates, best_by_validator, _ = self._aggregate_round_data(aggregated)

        sorted_aggregates = sorted(
            miner_aggregates.values(),
            key=lambda aggregate: aggregate.average_score,
            reverse=True,
        )

        network_pairs: List[Tuple[Dict[str, Any], str]] = []
        for idx, aggregate in enumerate(sorted_aggregates, start=1):
            entry = aggregate.to_performance(idx)
            network_pairs.append((entry, "network"))

        effective_limit = max(limit, len(best_by_validator))
        selected_pairs: List[Tuple[Dict[str, Any], str]] = network_pairs[
            :effective_limit
        ]
        seen_keys = {
            (entry.get("validatorId"), entry.get("uid")) for entry, _ in selected_pairs
        }

        for performance in best_by_validator.values():
            key = (performance.get("validatorId"), performance.get("uid"))
            if key not in seen_keys:
                selected_pairs.append((dict(performance), "validator"))
                seen_keys.add(key)

        selected_pairs.sort(
            key=lambda pair: (
                0 if pair[1] == "network" else 1,
                -pair[0].get("score", 0.0),
            )
        )

        selected: List[Dict[str, Any]] = []
        current_rank = 1
        for entry, scope in selected_pairs:
            if scope == "network":
                entry["ranking"] = current_rank
                current_rank += 1
            selected.append(entry)

        return {
            "miners": selected,
            "benchmarks": [],
            "total": len(selected),
            "page": 1,
            "limit": len(selected),
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
            entry.round.started_at
            for entry in aggregated.validator_rounds
            if entry.round.started_at is not None
        ]
        if not timestamps and start_candidates:
            timestamps = [min(start_candidates)]

        start_block = min(
            entry.round.start_block for entry in aggregated.validator_rounds
        )
        end_block_candidates: List[int] = []
        for entry in aggregated.validator_rounds:
            end_block = entry.round.end_block
            if end_block is None:
                end_block = entry.round.start_block + entry.round.max_blocks
            end_block_candidates.append(end_block)
        end_block_value = max(end_block_candidates)
        block_span = max(1, end_block_value - start_block)

        total_tasks = (
            sum(entry.round.n_tasks or 0 for entry in aggregated.validator_rounds) or 1
        )

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
        aggregated = await self._fetch_aggregated_round(
            round_identifier, include_details=False
        )
        statistics = await self.get_round_statistics(round_identifier)
        records = [entry.record for entry in aggregated.validator_rounds]
        total_tasks = sum(record.model.n_tasks or 0 for record in records)
        completed_tasks = sum(
            self._estimate_completed_tasks(record.model) for record in records
        )
        progress = self._compute_aggregated_progress(
            records, completed_tasks, total_tasks
        )
        status = _aggregate_status(
            [entry.round.status or "finished" for entry in aggregated.validator_rounds]
        )

        progress_ratio = progress.get("progress", 0.0)
        time_remaining_metrics = progress.get("estimatedTimeRemaining") or {}
        hours_remaining = time_remaining_metrics.get("hours", 0)
        minutes_remaining = time_remaining_metrics.get("minutes", 0)

        return {
            "roundId": aggregated.round_number,
            "status": status,
            "progress": round(progress_ratio, 3),
            "totalMiners": statistics.get("totalMiners", 0),
            "averageScore": statistics.get("averageScore", 0.0),
            "topScore": statistics.get("topScore", 0.0),
            "timeRemaining": (
                None
                if progress_ratio >= 1
                else f"{hours_remaining}h {minutes_remaining}m"
            ),
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
            tasks_for_round=(
                tasks_by_round.get(run_row.validator_round_id)
                if tasks_by_round
                else None
            ),
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
        round_row = parent_round_row or run_row.validator_round
        if round_row is None:
            raise ValueError(
                f"Agent run {run_row.agent_run_id} is missing round relationship"
            )

        round_model = self._deserialize_round(round_row)
        agent_run_model = self._deserialize_agent_run(
            run_row,
            include_details=include_details,
        )

        miner_info = None
        candidate_uid = agent_run_model.miner_uid
        candidate_hotkey = agent_run_model.miner_hotkey

        for snapshot in getattr(round_model, "miners", []) or []:
            if candidate_uid is not None and snapshot.uid == candidate_uid:
                miner_info = snapshot
                break
            if candidate_hotkey and snapshot.hotkey == candidate_hotkey:
                miner_info = snapshot
                break

        if miner_info is None:
            for snapshot in getattr(round_model, "sota_agents", []) or []:
                if candidate_uid is not None and snapshot.uid == candidate_uid:
                    miner_info = snapshot
                    break
                if candidate_hotkey and snapshot.hotkey == candidate_hotkey:
                    miner_info = snapshot
                    break

        agent_run_model.miner_info = miner_info

        if include_details and tasks_for_round is not None:
            task_lookup = tasks_for_round
            if agent_run_model.task_ids:
                tasks = [
                    task_lookup[task_id]
                    for task_id in agent_run_model.task_ids
                    if task_id in task_lookup
                ]
            else:
                tasks = list(task_lookup.values())
        else:
            tasks = []
        task_solutions = (
            self._convert_task_solutions(run_row.task_solutions)
            if include_details
            else []
        )
        evaluation_results = (
            self._convert_evaluations(run_row.evaluation_results)
            if include_details
            else []
        )

        return AgentRunContext(
            round=round_model,
            run=agent_run_model,
            tasks=tasks,
            task_solutions=task_solutions,
            evaluation_results=evaluation_results,
        )

    @staticmethod
    def _context_score(context: AgentRunContext) -> float:
        score = getattr(context.run, "average_score", None)
        if score is None:
            score = getattr(context.run, "avg_eval_score", None)
        if score is None and context.evaluation_results:
            score = sum(er.final_score for er in context.evaluation_results) / len(
                context.evaluation_results
            )
        try:
            return float(score or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _assign_ranks(self, contexts: Iterable[AgentRunContext]) -> None:
        grouped: Dict[str, List[AgentRunContext]] = defaultdict(list)
        for context in contexts:
            round_id = getattr(context.round, "validator_round_id", None)
            if not round_id:
                continue
            grouped[round_id].append(context)

        for context_list in grouped.values():
            ranked_candidates = [
                ctx
                for ctx in context_list
                if not ctx.run.is_sota and ctx.run.miner_uid is not None
            ]
            if not ranked_candidates:
                continue
            ranked_candidates.sort(key=self._context_score, reverse=True)
            last_score: Optional[float] = None
            current_rank = 0
            for position, ctx in enumerate(ranked_candidates, start=1):
                score = self._context_score(ctx)
                if last_score is None or abs(score - last_score) > 1e-6:
                    current_rank = position
                    last_score = score
                ctx.run.rank = current_rank

    def _recalculate_round_from_contexts(
        self,
        record: RoundRecord,
        contexts: List[AgentRunContext],
    ) -> None:
        round_model = record.model
        non_sota_contexts = [
            ctx
            for ctx in contexts
            if not ctx.run.is_sota and ctx.run.miner_uid is not None
        ]

        all_scores = [self._context_score(ctx) for ctx in non_sota_contexts]
        average_score = sum(all_scores) / len(all_scores) if all_scores else None
        top_score = max(all_scores) if all_scores else None

        desired_winners = round_model.n_winners or min(3, len(non_sota_contexts))
        sorted_contexts = sorted(
            non_sota_contexts, key=self._context_score, reverse=True
        )
        selected_contexts = sorted_contexts[:desired_winners]

        winners: List[Dict[str, Any]] = []
        for rank, ctx in enumerate(selected_contexts, start=1):
            score = self._context_score(ctx)
            winners.append(
                {
                    "miner_uid": ctx.run.miner_uid,
                    "rank": rank,
                    "score": round(score, 6),
                    "validator_uid": ctx.run.validator_uid,
                    "validator_round_id": round_model.validator_round_id,
                    "agent_run_id": ctx.run.agent_run_id,
                }
            )

        winner_scores = [winner["score"] for winner in winners]

        round_model.n_winners = len(winners)
        round_model.winners = winners
        round_model.average_score = average_score
        round_model.top_score = top_score

        metadata = dict(round_model.metadata or {})
        metadata["winners"] = winners
        metadata["winner_scores"] = winner_scores
        round_model.metadata = metadata

        summary = dict(round_model.summary or {})
        summary["winning_miners"] = len(winners)
        round_model.summary = summary
        # NOTE: Do NOT mutate ORM rows here.
        # This method runs within UI read flows. Writing to record.row would mark
        # the session dirty and can trigger an autoflush (UPDATE) before later
        # SELECTs, which can deadlock under concurrent writer/reader traffic.
        # Persisted aggregates must only be written by validator endpoints.

    def _build_miner_performance(
        self,
        context: AgentRunContext,
        round_obj: ValidatorRound,
        weights: Dict[str, float],
    ) -> Dict[str, Any]:
        miner_uid = context.run.miner_uid if context.run.miner_uid is not None else -1
        miner_info = self._resolve_miner_info(context, round_obj)
        name = (
            miner_info.agent_name
            if miner_info and miner_info.agent_name
            else f"Miner {miner_uid}"
        )
        hotkey = miner_info.hotkey if miner_info else None
        image_url = resolve_agent_image(miner_info)

        score = context.run.avg_eval_score
        if score is None and context.evaluation_results:
            score = sum(er.final_score for er in context.evaluation_results) / len(
                context.evaluation_results
            )
        score = score or 0.0

        duration = context.run.elapsed_sec
        if duration is None and context.run.started_at and context.run.ended_at:
            duration = context.run.ended_at - context.run.started_at
        duration = duration or 0.0

        tasks_total = context.run.n_tasks_total or len(context.tasks)
        completed_tasks = context.run.n_tasks_completed
        if completed_tasks is None:
            completed_tasks = len(
                [er for er in context.evaluation_results if er.final_score >= 0.5]
            )

        success = (context.run.n_tasks_failed or 0) == 0
        if tasks_total:
            success = success and completed_tasks >= tasks_total
        weight = 0.0
        if context.run.miner_uid is not None and str(context.run.miner_uid) in weights:
            weight = weights[str(context.run.miner_uid)]
        elif str(context.run.agent_run_id) in weights:
            weight = weights[str(context.run.agent_run_id)]
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
            "imageUrl": image_url,
        }

    @staticmethod
    def _round_cache_key(prefix: str, round_number: int, *components: Any) -> str:
        extras = (
            ":".join(
                json.dumps(component, sort_keys=True, default=str)
                for component in components
            )
            if components
            else ""
        )
        if extras:
            return f"{prefix}:{round_number}:{extras}"
        return f"{prefix}:{round_number}"

    @staticmethod
    def _is_final_round(aggregated: AggregatedRound) -> bool:
        return aggregated.latest_round_number > aggregated.round_number

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
                    selectinload(RoundORM.agent_runs).selectinload(
                        AgentEvaluationRunORM.task_solutions
                    ),
                    selectinload(RoundORM.agent_runs).selectinload(
                        AgentEvaluationRunORM.evaluation_results
                    ),
                    selectinload(RoundORM.validator_snapshots),
                    selectinload(RoundORM.miner_snapshots),
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
        meta = dict(round_row.meta or {})
        summary = dict(round_row.summary or {})

        profile = self._build_validator_profile(
            round_row=round_row,
            validator_uid=round_row.validator_uid,
            fallback_hotkey=round_row.validator_hotkey,
        )

        validator_info = ValidatorInfo(
            uid=round_row.validator_uid or profile.get("uid") or 0,
            hotkey=profile.get("hotkey") or "",
            coldkey=round_row.validator_coldkey,
            stake=float(profile.get("stake") or 0.0),
            vtrust=float(profile.get("vtrust") or 0.0),
            name=profile.get("name"),
            version=profile.get("version"),
            image_url=profile.get("image_url"),
        )

        miners: List[MinerInfo] = []
        for miner_snapshot in getattr(round_row, "miner_snapshots", []) or []:
            miners.append(
                MinerInfo(
                    uid=miner_snapshot.miner_uid,
                    hotkey=miner_snapshot.miner_hotkey,
                    coldkey=miner_snapshot.miner_coldkey,
                    agent_name=miner_snapshot.agent_name or "",
                    agent_image=miner_snapshot.image_url or "",
                    github=miner_snapshot.github_url or "",
                    is_sota=bool(getattr(miner_snapshot, "is_sota", False)),
                    description=getattr(miner_snapshot, "description", None),
                )
            )

        validators = [validator_info]
        for snapshot in getattr(round_row, "validator_snapshots", []) or []:
            if snapshot.validator_uid == validator_info.uid:
                continue
            validators.append(
                ValidatorInfo(
                    uid=snapshot.validator_uid,
                    hotkey=snapshot.validator_hotkey or "",
                    coldkey=None,
                    stake=float(snapshot.stake or 0.0),
                    vtrust=float(snapshot.vtrust or 0.0),
                    name=snapshot.name,
                    version=snapshot.version,
                )
            )

        meta.setdefault(
            "validatorProfile",
            {
                "uid": validator_info.uid,
                "hotkey": validator_info.hotkey,
                "name": validator_info.name,
                "stake": validator_info.stake,
                "vtrust": validator_info.vtrust,
                "version": validator_info.version,
                "image": profile.get("image_url"),
            },
        )

        winners = meta.get("winners")
        winner_scores = meta.get("winner_scores") or summary.get("winner_scores") or []
        weights = meta.get("weights")

        return ValidatorRound(
            validator_round_id=round_row.validator_round_id,
            round_number=round_row.round_number,
            validator_uid=validator_info.uid,
            validator_hotkey=validator_info.hotkey,
            validators=validators,
            validator_info=validator_info,
            start_block=round_row.start_block or 0,
            start_epoch=round_row.start_epoch or 0,
            end_block=round_row.end_block,
            end_epoch=round_row.end_epoch,
            started_at=round_row.started_at or datetime.now(timezone.utc).timestamp(),
            ended_at=round_row.ended_at,
            elapsed_sec=round_row.elapsed_sec,
            max_epochs=round_row.max_epochs or 0,
            max_blocks=round_row.max_blocks or 0,
            n_tasks=round_row.n_tasks or 0,
            n_miners=round_row.n_miners or 0,
            n_winners=round_row.n_winners or 0,
            miners=miners,
            sota_agents=[],
            winners=winners,
            winner_scores=list(winner_scores),
            weights=weights,
            average_score=round_row.average_score,
            top_score=round_row.top_score,
            status=round_row.status or "finished",
            summary=summary,
            metadata=meta,
            model_extra={
                "meta": meta,
                "summary": summary,
            },
        )

    def _deserialize_agent_run(
        self,
        run_row: AgentEvaluationRunORM,
        *,
        include_details: bool = True,
    ) -> AgentEvaluationRun:
        metadata = dict(run_row.meta or {})

        task_id_set: set[str] = set()
        if include_details:
            for solution in getattr(run_row, "task_solutions", []) or []:
                task_id = getattr(solution, "task_id", None)
                if task_id:
                    task_id_set.add(task_id)
            for evaluation in getattr(run_row, "evaluation_results", []) or []:
                task_id = getattr(evaluation, "task_id", None)
                if task_id:
                    task_id_set.add(task_id)
        metadata_task_ids = metadata.get("task_ids")
        if not task_id_set and isinstance(metadata_task_ids, list):
            for task_id in metadata_task_ids:
                if isinstance(task_id, str) and task_id:
                    task_id_set.add(task_id)
        task_ids = sorted(task_id_set)

        run_model = AgentEvaluationRun(
            agent_run_id=run_row.agent_run_id,
            validator_round_id=run_row.validator_round_id,
            validator_uid=run_row.validator_uid,
            validator_hotkey=run_row.validator_hotkey,
            miner_uid=run_row.miner_uid,
            miner_hotkey=run_row.miner_hotkey,
            is_sota=bool(run_row.is_sota),
            version=run_row.version,
            started_at=run_row.started_at or datetime.now(timezone.utc).timestamp(),
            ended_at=run_row.ended_at,
            elapsed_sec=run_row.elapsed_sec,
            average_score=run_row.average_score,
            average_execution_time=run_row.average_execution_time,
            average_reward=run_row.average_reward,
            total_reward=run_row.total_reward,
            total_tasks=run_row.total_tasks or len(task_ids),
            completed_tasks=run_row.completed_tasks or 0,
            failed_tasks=run_row.failed_tasks or 0,
            rank=run_row.rank,
            weight=run_row.weight,
            metadata=metadata,
        )

        # Compatibility attributes used throughout UI services
        run_model.task_ids = task_ids
        run_model.avg_eval_score = run_row.average_score
        run_model.avg_execution_time = run_row.average_execution_time
        run_model.avg_reward = run_row.average_reward
        run_model.n_tasks_total = run_model.total_tasks
        run_model.n_tasks_completed = run_model.completed_tasks
        run_model.n_tasks_failed = run_model.failed_tasks
        run_model.miner_info = None

        return run_model

    @staticmethod
    def _convert_tasks(task_rows: List[TaskORM]) -> List[Task]:
        tasks: List[Task] = []
        for task_row in task_rows:
            try:
                tasks.append(
                    Task(
                        task_id=task_row.task_id,
                        validator_round_id=task_row.validator_round_id,
                        is_web_real=bool(task_row.is_web_real),
                        web_project_id=task_row.web_project_id,
                        url=task_row.url or "",
                        prompt=task_row.prompt or "",
                        specifications=task_row.specifications or {},
                        tests=[],
                        relevant_data=task_row.relevant_data or {},
                        use_case=task_row.use_case or {},
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to deserialize task %s: %s", task_row.task_id, exc)
        return tasks

    @staticmethod
    def _convert_task_solutions(
        solution_rows: List[TaskSolutionORM],
    ) -> List[TaskSolution]:
        solutions: List[TaskSolution] = []
        for solution_row in solution_rows:
            try:
                actions = []
                for action_payload in solution_row.actions or []:
                    action_type = action_payload.get("type", "")
                    # Support both shapes:
                    # 1) { type, attributes: {...} }
                    # 2) { type, url/selector/... }
                    if isinstance(action_payload.get("attributes"), dict):
                        attributes = dict(action_payload.get("attributes") or {})
                    else:
                        attributes = {
                            key: value
                            for key, value in action_payload.items()
                            if key != "type"
                        }
                    actions.append(Action(type=action_type, attributes=attributes))

                solutions.append(
                    TaskSolution(
                        solution_id=solution_row.solution_id,
                        task_id=solution_row.task_id,
                        validator_round_id=solution_row.validator_round_id,
                        agent_run_id=solution_row.agent_run_id,
                        validator_uid=solution_row.validator_uid,
                        validator_hotkey=solution_row.validator_hotkey,
                        miner_uid=solution_row.miner_uid,
                        miner_hotkey=solution_row.miner_hotkey,
                        actions=actions,
                        web_agent_id=solution_row.web_agent_id,
                    )
                )
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
            try:
                matrix = []
                for row in evaluation_row.test_results_matrix or []:
                    test_row: List[TestResult] = []
                    for item in row:
                        if isinstance(item, dict):
                            test_row.append(
                                TestResult(
                                    success=bool(item.get("success")),
                                    extra_data=item.get("extra_data"),
                                )
                            )
                        else:
                            test_row.append(TestResult(success=False, extra_data=None))
                    matrix.append(test_row)

                evaluations.append(
                    EvaluationResult(
                        evaluation_id=evaluation_row.evaluation_id,
                        task_id=evaluation_row.task_id,
                        task_solution_id=evaluation_row.task_solution_id,
                        validator_round_id=evaluation_row.validator_round_id,
                        agent_run_id=evaluation_row.agent_run_id,
                        miner_uid=evaluation_row.miner_uid,
                        validator_uid=evaluation_row.validator_uid,
                        final_score=evaluation_row.final_score or 0.0,
                        test_results_matrix=matrix,
                        execution_history=evaluation_row.execution_history or [],
                        feedback=evaluation_row.feedback,
                        web_agent_id=evaluation_row.web_agent_id,
                        raw_score=evaluation_row.raw_score or 0.0,
                        evaluation_time=evaluation_row.evaluation_time or 0.0,
                        stats=evaluation_row.stats,
                        gif_recording=evaluation_row.gif_recording,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to deserialize evaluation result %s: %s",
                    evaluation_row.evaluation_id,
                    exc,
                )
        return evaluations
