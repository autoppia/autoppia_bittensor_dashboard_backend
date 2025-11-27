from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationResultORM,
    RoundORM,
    TaskORM,
    MinerAggregatesMV,
)
from app.models.core import ValidatorRound
from app.models.ui.overview import (
    ActivityMetadata,
    LeaderboardEntry,
    NetworkStatus,
    OverviewMetrics,
    PerformanceTrend,
    RecentActivity,
    RoundInfo,
    SubnetStatistics,
    ValidatorInfo,
)
from app.services.ui.rounds_service import AgentRunContext, RoundRecord, RoundsService
from app.services.chain_state import get_current_block_estimate
from app.services.round_calc import compute_boundaries_for_round, compute_round_number
from app.services.redis_cache import redis_cache
from app.services.metagraph_service import get_all_validators_data, MetagraphError
from app.config import settings
from app.utils.images import resolve_validator_image
from app.services.service_utils import rollback_on_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Temporarily enable INFO for debugging


class ValidatorState(Enum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    SENDING_TASKS = "sending_tasks"
    EVALUATING = "evaluating"
    WAITING = "waiting"
    OFFLINE = "offline"
    FINISHED = "finished"


STATUS_DISPLAY: Dict[ValidatorState, str] = {
    ValidatorState.NOT_STARTED: "Not Started",
    ValidatorState.STARTING: "Starting",
    ValidatorState.SENDING_TASKS: "Sending Tasks",
    ValidatorState.EVALUATING: "Evaluating",
    ValidatorState.WAITING: "Waiting",
    ValidatorState.OFFLINE: "Offline",
    ValidatorState.FINISHED: "Finished",
}

STATUS_DEFAULT_TASK: Dict[ValidatorState, str] = {
    ValidatorState.NOT_STARTED: "Awaiting round start",
    ValidatorState.STARTING: "Validator connected – awaiting task upload",
    ValidatorState.SENDING_TASKS: "Distributing tasks to agent runs",
    ValidatorState.EVALUATING: "Evaluating miner submissions",
    ValidatorState.WAITING: "Waiting for consensus",
    ValidatorState.OFFLINE: "No activity detected recently",
    ValidatorState.FINISHED: "Round completed",
}


@dataclass(frozen=True)
class ValidatorStatusInfo:
    """Normalized validator activity status for overview displays."""

    state: ValidatorState
    label: str
    default_task: str
    requires_prompt: bool

    @classmethod
    def from_state(cls, state: ValidatorState) -> "ValidatorStatusInfo":
        return cls(
            state=state,
            label=STATUS_DISPLAY[state],
            default_task=STATUS_DEFAULT_TASK[state],
            # Don't fetch task prompts for NOT_STARTED or FINISHED states
            # These should always show their generic messages
            requires_prompt=state
            not in {ValidatorState.NOT_STARTED, ValidatorState.FINISHED},
        )


def _round_id_to_int(round_id: str) -> int:
    if not round_id:
        return 0
    suffix = round_id
    if round_id.startswith("round_"):
        suffix = round_id.split("round_", 1)[1]
    elif "_" in round_id:
        suffix = round_id.split("_", 1)[1]
    digits: list[str] = []
    for char in suffix:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not digits:
        return 0
    try:
        return int("".join(digits))
    except ValueError:
        return 0


def _timestamp(value: Optional[float]) -> str:
    if value is None:
        value = datetime.now(timezone.utc).timestamp()
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()


class OverviewService:
    """Compute overview metrics from SQL-backed data."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    @rollback_on_error
    async def overview_metrics(self) -> OverviewMetrics:
        # Try to get from Redis cache first (10 minute TTL)
        cache_key = "overview:metrics:aggregate"
        cached = redis_cache.get(cache_key)
        if cached is not None:
            return cached

        records_with_contexts = await self._recent_round_records(
            limit=10,
            include_details=True,  # Load eval results so scores/winners are accurate
            context_limit=None,  # Include all agent runs per round
        )
        if not records_with_contexts:
            now_iso = datetime.now(timezone.utc).isoformat()
            return OverviewMetrics(
                topScore=0.0,
                totalWebsites=0,
                totalValidators=0,
                totalMiners=0,
                currentRound=0,
                metricsRound=0,
                subnetVersion="1.0.0",
                lastUpdated=now_iso,
            )

        def _round_number(record: RoundRecord) -> int:
            model = record.model
            return model.round_number or _round_id_to_int(model.validator_round_id)

        # Group records by round so we can easily pick the latest completed set.
        round_records_by_number: Dict[
            int, List[Tuple[RoundRecord, List[AgentRunContext]]]
        ] = {}
        completed_round_numbers: set[int] = set()
        for record, contexts in records_with_contexts:
            number = _round_number(record)
            if number:
                round_records_by_number.setdefault(number, []).append(
                    (record, contexts)
                )
                if record.model.ended_at:
                    completed_round_numbers.add(number)

        latest_completed_round = (
            max(completed_round_numbers) if completed_round_numbers else None
        )

        # ═══════════════════════════════════════════════════════════════════
        # SINGLE SOURCE OF TRUTH: Calculate current round from blockchain
        # This value is used for BOTH filtering and the API response
        # ═══════════════════════════════════════════════════════════════════
        try:
            current_block = get_current_block_estimate()
        except Exception:
            current_block = None

        # Calculate CURRENT round from blockchain (NOT from DB)
        current_round_value = 0
        if current_block is not None:
            try:
                current_round_value = compute_round_number(current_block)
                logger.debug(
                    f"Computed current round from blockchain: block={current_block}, round={current_round_value}"
                )
            except Exception as exc:
                logger.warning(
                    "Failed to compute current round from blockchain: %s", exc
                )

        # Fallback: if we can't get blockchain round, use max from DB
        if current_round_value <= 0:
            current_round_candidates = [
                _round_number(record)
                for record, _ in records_with_contexts
                if _round_number(record)
            ]
            if current_round_candidates:
                current_round_value = max(current_round_candidates)
                logger.warning(
                    f"Using fallback current round from DB max: {current_round_value}"
                )

        # Latest finished round should be current - 1
        preferred_previous_round: Optional[int] = None
        if current_round_value > 0:
            preferred_previous_round = max(current_round_value - 1, 0)
            logger.debug(
                f"Current round: {current_round_value}, Preferred previous (latest finished): {preferred_previous_round}"
            )

        candidate_round_numbers: List[int] = []
        if preferred_previous_round and preferred_previous_round > 0:
            candidate_round_numbers.append(preferred_previous_round)
        if (
            latest_completed_round is not None
            and latest_completed_round not in candidate_round_numbers
        ):
            candidate_round_numbers.append(latest_completed_round)

        for number in sorted(completed_round_numbers, reverse=True):
            if number not in candidate_round_numbers:
                candidate_round_numbers.append(number)

        for number in sorted(round_records_by_number.keys(), reverse=True):
            if number not in candidate_round_numbers:
                candidate_round_numbers.append(number)

        # ═══════════════════════════════════════════════════════════════════
        # Filter candidates: Latest finished MUST be < current round
        # ═══════════════════════════════════════════════════════════════════
        target_records: List[Tuple[RoundRecord, List[AgentRunContext]]] = []
        metrics_round_number = 0
        for number in candidate_round_numbers:
            # CRITICAL: Latest finished round MUST be < current round
            # They can NEVER be the same
            if current_round_value > 0 and number >= current_round_value:
                logger.debug(
                    f"Skipping round {number} for metrics: >= current round {current_round_value}"
                )
                continue
            candidates = round_records_by_number.get(number)
            if not candidates:
                continue

            # SIMPLE RULE: Round number < current_round_value means it's finished
            # No need to manually check boundaries - compute_round_number already did that
            # This candidate is valid for "Latest finished round"
            target_records = candidates
            metrics_round_number = number
            break

        if not target_records:
            target_records = [records_with_contexts[0]]
            metrics_round_number = _round_number(target_records[0][0])

        top_score = 0.0
        validators: set[int] = set()
        miners: set[str] = set()
        version_candidates: List[str] = []
        unique_websites: set[str] = set()
        miner_score_tracker: Dict[str, List[float]] = {}

        for record, contexts in target_records:
            round_obj = record.model
            if round_obj.validator_uid is not None:
                validators.add(round_obj.validator_uid)

            for validator_snapshot in getattr(round_obj, "validators", []) or []:
                if getattr(validator_snapshot, "uid", None) is not None:
                    validators.add(validator_snapshot.uid)
                version = getattr(validator_snapshot, "version", None)
                if version:
                    version_candidates.append(str(version))

            validator_info = getattr(round_obj, "validator_info", None)
            if validator_info and validator_info.version:
                version_candidates.append(str(validator_info.version))

            seen_tasks: set[str] = set()
            # Track agent_runs with NULL average_score to query from DB
            agent_runs_to_query: List[str] = []

            for ctx in contexts:
                # Ignore SOTA/baseline runs when computing miner leaderboard metrics
                if getattr(ctx.run, "is_sota", False):
                    continue
                miner_identifier = None
                if ctx.run.miner_uid is not None:
                    miner_identifier = f"uid:{ctx.run.miner_uid}"
                elif ctx.run.agent_run_id:
                    miner_identifier = f"run:{ctx.run.agent_run_id}"
                if miner_identifier:
                    miners.add(miner_identifier)

                score = self.rounds_service._context_score(ctx)

                # If score is 0 and average_score is NULL, we need to query from DB
                if (
                    score == 0.0
                    and ctx.run.average_score is None
                    and ctx.run.agent_run_id
                ):
                    agent_runs_to_query.append(ctx.run.agent_run_id)

                if miner_identifier:
                    tracker = miner_score_tracker.setdefault(miner_identifier, [])
                    tracker.append(score)

            # Query DB for agent_runs with NULL average_score
            if agent_runs_to_query:
                try:
                    from app.db.models import EvaluationORM

                    logger.info(
                        f"Querying DB for {len(agent_runs_to_query)} agent_runs with NULL average_score"
                    )

                    stmt = (
                        select(
                            AgentEvaluationRunORM.agent_run_id,
                            AgentEvaluationRunORM.miner_uid,
                            func.avg(EvaluationORM.final_score).label("avg_score"),
                        )
                        .join(
                            EvaluationORM,
                            EvaluationORM.agent_run_id
                            == AgentEvaluationRunORM.agent_run_id,
                        )
                        .where(
                            AgentEvaluationRunORM.agent_run_id.in_(agent_runs_to_query)
                        )
                        .group_by(
                            AgentEvaluationRunORM.agent_run_id,
                            AgentEvaluationRunORM.miner_uid,
                        )
                    )
                    result = await session.execute(stmt)
                    rows = result.all()

                    if rows:
                        logger.info(
                            f"✅ Found {len(rows)} agent_runs with evaluations in DB"
                        )
                        for row in rows:
                            agent_run_id, miner_uid, avg_score = row
                            if avg_score is not None and miner_uid is not None:
                                miner_identifier = f"uid:{miner_uid}"
                                # Replace the 0.0 score with the real score from DB
                                if miner_identifier in miner_score_tracker:
                                    # Remove the 0.0 we added earlier
                                    tracker = miner_score_tracker[miner_identifier]
                                    if 0.0 in tracker:
                                        tracker.remove(0.0)
                                    tracker.append(float(avg_score))
                                    logger.debug(
                                        f"  Updated {miner_identifier}: {avg_score}"
                                    )
                except Exception as e:
                    logger.error(f"Failed to query evaluations from DB: {e}")

                for task in ctx.tasks or []:
                    if task.task_id in seen_tasks:
                        continue
                    seen_tasks.add(task.task_id)
                    host = urlparse(task.url).netloc or task.url
                    if host:
                        unique_websites.add(host.lower())

            if not contexts:
                # If no contexts loaded, try winners first
                logger.info(
                    f"No contexts for round {round_obj.validator_round_id}, checking winners or DB..."
                )
                if round_obj.winners:
                    round_top = max(
                        winner.get("score", 0.0) for winner in round_obj.winners
                    )
                    top_score = max(top_score, round(round_top, 6))
                    logger.info(f"Using winners: top_score={round_top}")
                else:
                    # Fallback: query evaluations directly from DB for this round
                    logger.info(
                        f"No winners, querying DB for round {round_obj.validator_round_id}"
                    )
                    try:
                        from app.db.models import EvaluationORM

                        # Get all evaluations for this validator_round and calculate avg per miner
                        stmt = (
                            select(
                                AgentEvaluationRunORM.miner_uid,
                                func.avg(EvaluationORM.final_score).label("avg_score"),
                            )
                            .join(
                                EvaluationORM,
                                EvaluationORM.agent_run_id
                                == AgentEvaluationRunORM.agent_run_id,
                            )
                            .where(
                                AgentEvaluationRunORM.validator_round_id
                                == round_obj.validator_round_id
                            )
                            .group_by(AgentEvaluationRunORM.miner_uid)
                        )
                        result = await session.execute(stmt)
                        rows = result.all()

                        logger.info(
                            f"DB query returned {len(rows)} rows for round {round_obj.validator_round_id}"
                        )
                        if rows:
                            for row in rows:
                                miner_uid, avg_score = row
                                logger.debug(f"  Miner {miner_uid}: {avg_score}")
                                if avg_score is not None:
                                    miner_identifier = f"uid:{miner_uid}"
                                    miners.add(miner_identifier)
                                    tracker = miner_score_tracker.setdefault(
                                        miner_identifier, []
                                    )
                                    tracker.append(float(avg_score))
                            logger.info(
                                f"✅ Loaded {len(rows)} miner scores from DB for round {round_obj.validator_round_id}"
                            )
                        else:
                            logger.warning(
                                f"No evaluation data found in DB for round {round_obj.validator_round_id}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to load evaluations for round {round_obj.validator_round_id}: {e}"
                        )

        top_miner_uid = None
        top_miner_name = None

        if miner_score_tracker:
            # Calculate average score for each miner and find the top one
            miner_averages = {
                identifier: sum(scores) / len(scores)
                for identifier, scores in miner_score_tracker.items()
                if scores
            }
            if miner_averages:
                top_score = max(miner_averages.values())
                # Find the miner with the top score
                for identifier, avg_score in miner_averages.items():
                    if avg_score == top_score:
                        # Extract UID from identifier (format: "uid:80" or "run:...")
                        if identifier.startswith("uid:"):
                            try:
                                top_miner_uid = int(identifier.split(":", 1)[1])
                            except (ValueError, IndexError):
                                pass
                        # Try to find miner name from contexts
                        for record, contexts in target_records:
                            for ctx in contexts:
                                if ctx.run.miner_uid == top_miner_uid:
                                    miner_info = getattr(ctx.run, "miner_info", None)
                                    if miner_info and miner_info.agent_name:
                                        top_miner_name = miner_info.agent_name
                                        break
                            if top_miner_name:
                                break

                        # If not found in contexts, try to get from DB
                        if not top_miner_name and top_miner_uid:
                            try:
                                from app.db.models import MinerORM

                                stmt = select(MinerORM.name).where(
                                    MinerORM.uid == top_miner_uid
                                )
                                result = await session.execute(stmt)
                                miner_name_row = result.scalar_one_or_none()
                                if miner_name_row:
                                    top_miner_name = miner_name_row
                            except Exception as e:
                                logger.debug(
                                    f"Could not fetch miner name for UID {top_miner_uid}: {e}"
                                )
                        break
        else:
            # If we have no context data, fall back to best single score captured earlier.
            top_score = max(top_score, 0.0)

        subnet_version = version_candidates[0] if version_candidates else "1.0.0"
        total_websites = (
            len(unique_websites) if unique_websites else await self._total_websites()
        )

        display_metrics_round_number = int(metrics_round_number or 0)
        if display_metrics_round_number < 0:
            display_metrics_round_number = 0

        metrics = OverviewMetrics(
            topScore=round(top_score, 3),
            topMinerUid=top_miner_uid,
            topMinerName=top_miner_name,
            totalWebsites=total_websites,
            totalValidators=len(validators),
            totalMiners=len(miners),
            currentRound=current_round_value,
            metricsRound=display_metrics_round_number,
            subnetVersion=subnet_version,
            lastUpdated=datetime.now(timezone.utc).isoformat(),
        )

        # Cache metrics for 10 minutes in Redis (shared across all workers)
        redis_cache.set(cache_key, metrics, ttl=600)
        return metrics

    @rollback_on_error
    async def validators_list(
        self,
        page: int,
        limit: int,
        status: Optional[str],
        sort_by: str,
        sort_order: str,
    ) -> Tuple[List[Dict[str, Any]], int]:
        validators = await self._aggregate_validators()

        entries = list(validators.values())

        if status:
            entries = [entry for entry in entries if entry["status"] == status]

        reverse = sort_order.lower() == "desc"
        try:
            entries.sort(key=lambda item: item.get(sort_by), reverse=reverse)
        except Exception:  # noqa: BLE001
            pass

        total = len(entries)
        start = (page - 1) * limit
        end = start + limit
        return entries[start:end], total

    @rollback_on_error
    async def validator_detail(self, validator_id: str) -> Dict[str, Any]:
        validators = await self._aggregate_validators()
        validator = validators.get(validator_id)
        if not validator:
            raise ValueError(f"Validator {validator_id} not found")
        return validator

    @rollback_on_error
    async def validators_filter(self) -> List[Dict[str, Any]]:
        validators = await self._aggregate_validators()
        items: List[Dict[str, Any]] = []
        for identifier, data in validators.items():
            items.append(
                {
                    "id": identifier,
                    "name": data.get("name", identifier),
                    "hotkey": data.get("hotkey"),
                    "icon": data.get("icon"),
                    "status": data.get("status"),
                }
            )
        items.sort(key=lambda item: item["name"])
        return items

    @rollback_on_error
    async def current_round(self) -> Optional[RoundInfo]:
        """Get the ACTUAL current round (the one that's active on the blockchain).

        A round is only "current" if:
        1. The blockchain is within its start_block and end_block range
        2. It has NOT ended (ended_at is None or status is not "finished")
        """
        # Get current block from blockchain
        try:
            current_block = get_current_block_estimate()
        except Exception:
            current_block = None

        # Calculate which round SHOULD be active right now
        actual_current_round_number = 0
        if current_block is not None:
            try:
                actual_current_round_number = compute_round_number(current_block)
            except Exception:
                pass

        # Get recent rounds and find the one that matches the current round number
        rounds = await self._recent_rounds(limit=10)
        if not rounds:
            return None

        # Look for the round that matches the current round number
        for round_obj in rounds:
            round_num = round_obj.round_number or 0

            # Check if this is the actual current round (trust compute_round_number)
            if (
                actual_current_round_number > 0
                and round_num == actual_current_round_number
            ):
                # Verify it's not finished AND has actual data
                # A round is only "current" if it has started processing (has validator_rounds)
                has_data = (
                    round_obj.start_block is not None
                    and round_obj.start_block > 0
                    and len(round_obj.validators or []) > 0
                )
                if round_obj.status != "finished" and has_data:
                    return self._round_to_info(round_obj, current=True)

        # Fallback: if no active round found, return the most recent one marked as NOT current
        # This happens when blockchain is in Round N but DB doesn't have Round N yet
        # (e.g., current_block says Round 23, but validator hasn't started it)
        # We return Round 22 with current=False so frontend knows it's not actually active
        if rounds:
            return self._round_to_info(rounds[0], current=False)
        return None

    @rollback_on_error
    async def rounds_list(
        self, page: int, limit: int, status: Optional[str]
    ) -> Tuple[List[RoundInfo], Optional[RoundInfo], int]:
        rounds = await self._recent_rounds(limit=100)
        active_rounds = [round_obj for round_obj in rounds if not round_obj.ended_at]
        completed_rounds = [round_obj for round_obj in rounds if round_obj.ended_at]

        completed_rounds.sort(key=lambda r: r.started_at or 0, reverse=True)

        round_infos = [
            self._round_to_info(round_obj, current=False)
            for round_obj in completed_rounds
        ]

        if status:
            round_infos = [info for info in round_infos if info.status == status]

        total = len(round_infos)
        start = (page - 1) * limit
        end = start + limit
        paginated = round_infos[start:end]

        current_round_obj: Optional[ValidatorRound] = None
        if active_rounds:
            active_rounds.sort(key=lambda r: r.started_at or 0, reverse=True)
            current_round_obj = active_rounds[0]
        elif completed_rounds:
            current_round_obj = completed_rounds[0]

        current_round = (
            self._round_to_info(current_round_obj, current=True)
            if current_round_obj
            else None
        )
        return paginated, current_round, total

    @rollback_on_error
    async def round_detail(self, identifier: str) -> RoundInfo:
        if identifier.isdigit():
            validator_round_id = f"round_{identifier.zfill(3)}"
        else:
            validator_round_id = identifier

        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
            .where(RoundORM.validator_round_id == validator_round_id)
        )
        row = await self.session.scalar(stmt)
        if not row:
            raise ValueError(f"Round {identifier} not found")
        round_obj = self.rounds_service._deserialize_round(row)
        return self._round_to_info(round_obj, current=round_obj.ended_at is None)

    @rollback_on_error
    async def leaderboard(
        self,
        time_range: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Tuple[List[LeaderboardEntry], Dict[str, str]]:
        normalized_range = (time_range or "").strip().lower()
        # Support both "D" (legacy) and "R" (rounds) - both mean "last N rounds"
        range_limits = {
            "7d": 7,
            "15d": 15,
            "30d": 30,
            "7r": 7,
            "15r": 15,
            "30r": 30,
        }

        derived_limit: Optional[int] = None
        unlimited = False

        if normalized_range == "all":
            unlimited = True
        elif normalized_range in range_limits:
            derived_limit = range_limits[normalized_range]
        elif normalized_range.endswith("d") or normalized_range.endswith("r"):
            try:
                parsed_rounds = int(normalized_range[:-1])
                if parsed_rounds > 0:
                    derived_limit = parsed_rounds
            except ValueError:
                derived_limit = None

        if derived_limit is None and not unlimited:
            # Default to a sensible window when no explicit range is provided.
            derived_limit = 30

        if limit is not None:
            # When an explicit limit is provided, it takes precedence and disables the "all" flag.
            unlimited = False
            derived_limit = min(limit, derived_limit) if derived_limit else limit

        # Performance optimization: Try materialized view first (fast path)
        try:
            mv_entries = await self._leaderboard_from_materialized_view(
                derived_limit=derived_limit,
                unlimited=unlimited,
            )
            if mv_entries:
                logger.info(f"Leaderboard: Using materialized view (fast path)")
                return mv_entries
        except Exception as mv_exc:
            logger.warning(
                f"Leaderboard: Materialized view failed, falling back to full aggregation: {mv_exc}"
            )

        # Fallback: Original implementation (slow but complete)
        # Performance optimization: Even with "unlimited" (timeRange=all),
        # limit to 365 rounds (max ~1 year) to prevent loading thousands of rounds
        if unlimited:
            fetch_limit = 365  # Max 1 year of rounds (reasonable for "all" time range)
        else:
            # Fetch a wider window than requested so we can collapse multiple validator rounds
            # for the same logical day into a single aggregated round.
            fetch_limit = max((derived_limit or 30) * 5, derived_limit or 1)

        records_with_contexts = await self._recent_round_records(
            limit=fetch_limit,
            include_details=True,  # Need eval results to compute scores correctly
            context_limit=None,  # Don't truncate agent runs; winners may be beyond first 20
        )
        logger.info(f"Leaderboard: Loaded {len(records_with_contexts)} round records")
        if not records_with_contexts:
            now_iso = datetime.now(timezone.utc).isoformat()
            logger.warning("Leaderboard: No round records found, returning empty")
            return [], {"start": now_iso, "end": now_iso}

        def _scores_for_provider(
            contexts: List[AgentRunContext],
            provider_tokens: List[str],
        ) -> List[float]:
            scores: List[float] = []
            for ctx in contexts:
                miner_info = ctx.run.miner_info
                source_parts: List[str] = []
                if miner_info:
                    if getattr(miner_info, "agent_name", None):
                        source_parts.append(str(miner_info.agent_name))
                    if getattr(miner_info, "github", None):
                        source_parts.append(str(miner_info.github))

                metadata = getattr(ctx.run, "metadata", None)
                if isinstance(metadata, dict):
                    for value in metadata.values():
                        if isinstance(value, str):
                            source_parts.append(value)

                provider_hint = " ".join(source_parts).lower()
                if provider_hint and any(
                    token in provider_hint for token in provider_tokens
                ):
                    scores.append(self.rounds_service._context_score(ctx))
            return scores

        # Get current block to determine which rounds are officially finished
        try:
            current_block = get_current_block_estimate()
        except Exception:
            current_block = None

        # Calculate CURRENT round number (the one in progress). If the chain
        # height cannot be resolved or returns an obviously low value, fall
        # back to permissive behaviour (include recent rounds even if status
        # isn't "finished").
        current_round_number = 0
        if current_block is not None:
            try:
                current_round_number = compute_round_number(current_block)
            except Exception:
                current_round_number = 0
        if current_round_number is not None and current_round_number <= 1:
            current_round_number = 0

        total_rounds = len(records_with_contexts)
        entries: List[LeaderboardEntry] = []
        for idx, (record, contexts) in enumerate(records_with_contexts):
            round_obj = record.model
            round_number = round_obj.round_number or 0

            # Decide whether this round should be included. Prefer chain signal,
            # but fall back to presence of data even if status is still "active".
            include_round = True
            if current_round_number > 0:
                if round_number >= current_round_number:
                    if not (
                        round_obj.ended_at
                        or round_obj.status in ("finished", "evaluating_finished")
                    ):
                        include_round = False
            else:
                if (
                    round_obj.status not in ("finished", "evaluating_finished")
                    and not contexts
                ):
                    include_round = False

            if not include_round:
                logger.info(
                    "Leaderboard: Skipping round %s (round_number=%s, status=%s, current_round=%s)",
                    round_obj.validator_round_id,
                    round_number,
                    round_obj.status,
                    current_round_number,
                )
                continue

            logger.info(
                f"Leaderboard: Including round {round_number}, contexts={len(contexts)}"
            )

            non_sota_contexts = [
                ctx for ctx in contexts if not getattr(ctx.run, "is_sota", False)
            ]
            run_scores = [
                self.rounds_service._context_score(ctx) for ctx in non_sota_contexts
            ]

            average_score = (
                round_obj.average_score
                if round_obj.average_score is not None
                else (sum(run_scores) / len(run_scores) if run_scores else 0.0)
            )

            # Find the winner (highest score)
            winner_uid: Optional[int] = None
            winner_name: Optional[str] = None
            if non_sota_contexts:
                winner_ctx = max(
                    non_sota_contexts,
                    key=lambda ctx: self.rounds_service._context_score(ctx),
                )
                winner_uid = winner_ctx.run.miner_uid
                miner_info = getattr(winner_ctx.run, "miner_info", None)
                if miner_info and miner_info.agent_name:
                    winner_name = miner_info.agent_name

            openai_scores = _scores_for_provider(contexts, ["openai"])
            anthropic_scores = _scores_for_provider(contexts, ["anthropic"])
            browser_scores = _scores_for_provider(contexts, ["browser"])

            def _avg(values: List[float]) -> Optional[float]:
                if not values:
                    return None
                return round(sum(values) / len(values), 3)

            timestamp = datetime.fromtimestamp(
                round_obj.started_at or datetime.now(timezone.utc).timestamp(),
                tz=timezone.utc,
            ).isoformat()

            round_number = round_obj.round_number or _round_id_to_int(
                round_obj.validator_round_id
            )
            if not round_number or round_number <= 0:
                round_number = total_rounds - idx

            entries.append(
                LeaderboardEntry(
                    round=round_number,
                    subnet36=round(average_score, 3),
                    winnerUid=winner_uid,
                    winnerName=winner_name,
                    openai_cua=_avg(openai_scores),
                    anthropic_cua=_avg(anthropic_scores),
                    browser_use=_avg(browser_scores),
                    timestamp=timestamp,
                )
            )

        grouped_entries: Dict[int, List[LeaderboardEntry]] = defaultdict(list)
        for entry in entries:
            grouped_entries[entry.round].append(entry)

        aggregated_entries: List[LeaderboardEntry] = []
        for round_number, round_entries in grouped_entries.items():
            latest_timestamp = max(entry.timestamp for entry in round_entries)
            subnet36_values = [entry.subnet36 for entry in round_entries]
            openai_values = [
                value
                for value in (entry.openai_cua for entry in round_entries)
                if value is not None
            ]
            anthropic_values = [
                value
                for value in (entry.anthropic_cua for entry in round_entries)
                if value is not None
            ]
            browser_values = [
                value
                for value in (entry.browser_use for entry in round_entries)
                if value is not None
            ]

            # Get winner from the entry with highest score
            winner_entry = max(round_entries, key=lambda e: e.subnet36, default=None)
            winner_uid = winner_entry.winnerUid if winner_entry else None
            winner_name = winner_entry.winnerName if winner_entry else None

            aggregated_entries.append(
                LeaderboardEntry(
                    round=round_number,
                    subnet36=round(max(subnet36_values), 3) if subnet36_values else 0.0,
                    winnerUid=winner_uid,
                    winnerName=winner_name,
                    openai_cua=round(max(openai_values), 3) if openai_values else None,
                    anthropic_cua=(
                        round(max(anthropic_values), 3) if anthropic_values else None
                    ),
                    browser_use=(
                        round(max(browser_values), 3) if browser_values else None
                    ),
                    timestamp=latest_timestamp,
                )
            )

        aggregated_entries.sort(key=lambda entry: entry.timestamp, reverse=True)

        if not unlimited and derived_limit:
            aggregated_entries = aggregated_entries[:derived_limit]

        if not aggregated_entries:
            now_iso = datetime.now(timezone.utc).isoformat()
            return [], {"start": now_iso, "end": now_iso}

        start = min(entry.timestamp for entry in aggregated_entries)
        end = max(entry.timestamp for entry in aggregated_entries)
        return aggregated_entries, {"start": start, "end": end}

    async def _leaderboard_from_materialized_view(
        self,
        derived_limit: Optional[int],
        unlimited: bool,
    ) -> Optional[Tuple[List[LeaderboardEntry], Dict[str, str]]]:
        """
        Fast path: Build leaderboard from materialized view.
        Groups by round_number using JSONB rounds data.
        """
        from sqlalchemy import cast, Float, Integer

        # Get all miners from materialized view
        stmt = select(MinerAggregatesMV).where(
            MinerAggregatesMV.is_sota == False  # Only non-SOTA miners
        )
        result = await self.session.execute(stmt)
        miners = list(result.scalars().all())

        if not miners:
            return None

        # Extract round data from JSONB and group by round_number
        round_data: Dict[int, Dict[str, Any]] = defaultdict(
            lambda: {
                "scores": [],
                "top_score": 0.0,
                "winner_uid": None,
                "winner_name": None,
            }
        )

        for miner in miners:
            rounds_json = miner.rounds or {}
            for round_key, round_info in rounds_json.items():
                try:
                    round_number = int(round_key)
                    if round_number <= 0:
                        continue

                    avg_score = float(round_info.get("avgScore", 0.0))
                    if avg_score <= 0:
                        continue

                    round_data[round_number]["scores"].append(avg_score)

                    # Track winner (highest score)
                    if avg_score > round_data[round_number]["top_score"]:
                        round_data[round_number]["top_score"] = avg_score
                        round_data[round_number]["winner_uid"] = miner.uid
                        round_data[round_number]["winner_name"] = miner.name or f"agent-{miner.uid}"
                except (ValueError, TypeError, KeyError):
                    continue

        if not round_data:
            return None

        # Get round timestamps from validator_rounds (lightweight query)
        round_numbers = sorted(round_data.keys(), reverse=True)
        max_rounds = 365 if unlimited else (derived_limit or 30)
        round_numbers = round_numbers[:max_rounds]

        # Query timestamps for these rounds
        stmt = select(
            RoundORM.round_number,
            func.min(RoundORM.started_at).label("min_started_at"),
        ).where(
            RoundORM.round_number.in_(round_numbers),
            RoundORM.round_number.isnot(None),
        ).group_by(RoundORM.round_number)

        result = await self.session.execute(stmt)
        round_timestamps: Dict[int, float] = {
            row.round_number: row.min_started_at
            for row in result
            if row.round_number and row.min_started_at
        }

        # Build leaderboard entries
        entries: List[LeaderboardEntry] = []
        for round_number in sorted(round_data.keys(), reverse=True)[:max_rounds]:
            data = round_data[round_number]
            scores = data["scores"]
            if not scores:
                continue

            # Calculate average score (subnet36)
            average_score = sum(scores) / len(scores)

            # Get timestamp
            timestamp_ts = round_timestamps.get(round_number)
            if timestamp_ts is None:
                # Fallback: use current time if round not found
                timestamp_ts = datetime.now(timezone.utc).timestamp()

            timestamp = datetime.fromtimestamp(timestamp_ts, tz=timezone.utc).isoformat()

            entries.append(
                LeaderboardEntry(
                    round=round_number,
                    subnet36=round(average_score, 3),
                    winnerUid=data["winner_uid"],
                    winnerName=data["winner_name"],
                    openai_cua=None,  # Not available in MV, would need additional logic
                    anthropic_cua=None,  # Not available in MV
                    browser_use=None,  # Not available in MV
                    timestamp=timestamp,
                )
            )

        if not entries:
            return None

        # Sort by timestamp descending
        entries.sort(key=lambda e: e.timestamp, reverse=True)

        # Apply limit
        if not unlimited and derived_limit:
            entries = entries[:derived_limit]

        start = min(entry.timestamp for entry in entries)
        end = max(entry.timestamp for entry in entries)

        return entries, {"start": start, "end": end}

    @rollback_on_error
    async def statistics(self) -> SubnetStatistics:
        validators = await self._aggregate_validators()

        def _to_float(value: Any) -> float:
            if value is None:
                return 0.0
            if isinstance(value, (int, float)):
                return float(value)
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        total_stake = int(
            sum(_to_float(entry.get("stake")) for entry in validators.values())
        )
        total_emission = int(
            sum(_to_float(entry.get("emission")) for entry in validators.values())
        )
        total_runs = await self._total_runs()
        success_total = int(total_runs * 0.85)

        average_trust = (
            sum(_to_float(entry.get("trust")) for entry in validators.values())
            / len(validators)
            if validators
            else 0.0
        )
        average_uptime = (
            sum(_to_float(entry.get("uptime", 0.0)) for entry in validators.values())
            / len(validators)
            if validators
            else 0.0
        )

        average_score = await self._average_score()
        total_tasks_completed = sum(
            entry["completedTasks"] for entry in validators.values()
        )

        return SubnetStatistics(
            totalStake=total_stake,
            totalEmission=total_emission,
            averageTrust=round(average_trust, 3),
            networkUptime=round(average_uptime, 2),
            activeValidators=len(validators),
            registeredMiners=len(validators),  # Approximate
            totalTasksCompleted=total_tasks_completed,
            averageTaskScore=round(average_score, 3),
            lastUpdated=datetime.now(timezone.utc).isoformat(),
        )

    @rollback_on_error
    async def network_status(self) -> NetworkStatus:
        validators = await self._aggregate_validators()
        rounds = await self._recent_rounds(limit=5)
        now = datetime.now(timezone.utc)
        network_latency_samples = [
            round_obj.elapsed_sec for round_obj in rounds if round_obj.elapsed_sec
        ]
        average_latency = (
            int(sum(network_latency_samples) / len(network_latency_samples))
            if network_latency_samples
            else 0
        )

        # Simplified policy: Always show healthy/live except when last round was > 6 hours ago.
        # If there are no rounds yet, treat as healthy.
        if not rounds:
            return NetworkStatus(
                status="healthy",
                message="Awaiting first round",
                lastChecked=now.isoformat(),
                activeValidators=len(validators),
                networkLatency=average_latency,
            )

        last_round = max(
            rounds,
            key=lambda r: (r.ended_at or r.started_at or 0),
        )
        last_activity_ts = last_round.ended_at or last_round.started_at
        last_activity_dt = (
            datetime.fromtimestamp(last_activity_ts, tz=timezone.utc)
            if last_activity_ts
            else None
        )

        # If timestamp missing, still treat as healthy per simplified policy
        if not last_activity_ts:
            status = "healthy"
            message = "Awaiting next round"
        else:
            delta = max(now.timestamp() - last_activity_ts, 0.0)
            delta_hours = delta / 3600

            def _humanize(seconds: float) -> str:
                total_seconds = int(max(seconds, 0))
                days, remainder = divmod(total_seconds, 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes, _ = divmod(remainder, 60)
                parts: List[str] = []
                if days:
                    parts.append(f"{days} day{'s' if days != 1 else ''}")
                if hours:
                    parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes and not days:
                    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
                if not parts:
                    parts.append("just now")
                return " ".join(parts)

            human_delta = _humanize(delta)
            timestamp_label = (
                last_activity_dt.strftime("%Y-%m-%d %H:%M UTC")
                if last_activity_dt
                else "unknown time"
            )
            round_label = _round_id_to_int(last_round.validator_round_id)

            if delta_hours > 6:
                status = "degraded"
                message = (
                    f"No round activity for {human_delta} — last known round #{round_label} "
                    f"completed at {timestamp_label}"
                )
            else:
                status = "healthy"
                message = (
                    f"Awaiting next round — last recorded round #{round_label} finished {human_delta} ago "
                    f"({timestamp_label})"
                )

        return NetworkStatus(
            status=status,
            message=message,
            lastChecked=now.isoformat(),
            activeValidators=len(validators),
            networkLatency=average_latency,
        )

    @rollback_on_error
    async def recent_activity(self, limit: int) -> List[RecentActivity]:
        rounds = await self._recent_rounds(limit=limit)
        activities: List[RecentActivity] = []
        for round_obj in rounds:
            if not round_obj.winners:
                continue
            winner = round_obj.winners[0]
            miner_uid = winner.get("miner_uid", 0)
            activity = RecentActivity(
                id=f"round_{round_obj.validator_round_id}_winner",
                type="task_completed",
                message=f"Miner {miner_uid} completed top task in round {round_obj.validator_round_id}",
                timestamp=_timestamp(round_obj.ended_at or round_obj.started_at),
                metadata=ActivityMetadata(
                    validatorId=str(winner.get("validator_uid")),
                    taskId=str(winner.get("task_id")),
                    score=float(winner.get("score", 0.0)),
                    roundId=round_obj.validator_round_id,
                ),
            )
            activities.append(activity)
        return activities[:limit]

    @rollback_on_error
    async def performance_trends(self, days: int) -> List[PerformanceTrend]:
        rounds = await self._recent_rounds(limit=days)
        trends: List[PerformanceTrend] = []
        for round_obj in rounds:
            average_score = 0.0
            if round_obj.winners:
                scores = [winner.get("score", 0.0) for winner in round_obj.winners]
                average_score = sum(scores) / len(scores) if scores else 0.0
            trend = PerformanceTrend(
                date=datetime.fromtimestamp(
                    round_obj.started_at or datetime.now(timezone.utc).timestamp(),
                    tz=timezone.utc,
                ).strftime("%Y-%m-%d"),
                averageScore=round(average_score, 3),
                totalTasks=round_obj.n_tasks,
                activeValidators=len(round_obj.validators),
            )
            trends.append(trend)
        trends.sort(key=lambda t: t.date)
        return trends

    @rollback_on_error
    async def _recent_round_records(
        self,
        limit: int = 20,
        include_details: bool = False,
        fetch_contexts: bool = True,
        context_limit: Optional[int] = 20,
    ) -> List[Tuple[RoundRecord, List[AgentRunContext]]]:
        """
        Fetch recent round records with optional agent run contexts.

        Args:
            limit: Max number of rounds to fetch
            include_details: Include detailed data in contexts
            fetch_contexts: If False, skips loading agent run contexts (performance optimization)

        Performance note: When fetch_contexts=False, eliminates N+1 queries
        for callers that only need round metadata.
        """
        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
            .order_by(RoundORM.id.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = await self.session.scalars(stmt)
        records: List[Tuple[RoundRecord, List[AgentRunContext]]] = []
        for row in rows:
            try:
                round_model = self.rounds_service._deserialize_round(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to parse round %s: %s", row.validator_round_id, exc
                )
                continue

            if round_model.started_at is None:
                logger.debug(
                    "Skipping round %s due to missing started_at timestamp",
                    row.validator_round_id,
                )
                continue

            record = RoundRecord(row=row, model=round_model)
            contexts: List[AgentRunContext] = []

            # Performance optimization: Only load contexts if explicitly requested
            if fetch_contexts:
                try:
                    contexts = await self.rounds_service.list_agent_run_contexts(
                        validator_round_id=row.validator_round_id,
                        include_details=include_details,
                        limit=context_limit,  # Allow callers to override (None loads all)
                        skip=0,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to load agent run contexts for %s: %s",
                        row.validator_round_id,
                        exc,
                    )
                if contexts:
                    self.rounds_service._recalculate_round_from_contexts(
                        record, contexts
                    )

            records.append((record, contexts))
        return records

    @rollback_on_error
    async def _recent_rounds(self, limit: int = 20) -> List[ValidatorRound]:
        """Get recent rounds without loading agent run contexts (optimization)."""
        records = await self._recent_round_records(
            limit=limit,
            include_details=False,
            fetch_contexts=False,  # Don't load contexts since we discard them anyway
        )
        return [record.model for record, _ in records]

    @rollback_on_error
    async def _total_websites(self) -> int:
        """
        Count distinct websites (URLs) from tasks.

        Performance note: Loads only the data column (not full rows) and
        extracts unique URLs in Python. With ~2-3K tasks this is acceptable.
        """
        stmt = select(TaskORM)
        rows = await self.session.scalars(stmt)
        urls = set()
        for row in rows:
            data = row.data or {}
            url = data.get("url")
            if url:
                urls.add(url)
        return len(urls)

    @rollback_on_error
    async def _total_runs(self) -> int:
        """
        Count total agent evaluation runs efficiently using database aggregation.

        Performance optimization: Uses COUNT(*) in PostgreSQL instead of
        loading all run IDs into Python memory.
        """
        stmt = select(func.count()).select_from(AgentEvaluationRunORM)
        result = await self.session.scalar(stmt)
        return int(result or 0)

    def _derive_validator_status(
        self,
        current_record: Optional[RoundRecord],
        *,
        total_runs: int,
        successful_runs: int,
        has_scores: bool,
        seconds_since_activity: Optional[float],
    ) -> ValidatorStatusInfo:
        if current_record is None:
            return ValidatorStatusInfo.from_state(ValidatorState.NOT_STARTED)

        validator_round = current_record.model

        # Check validator_round.status to distinguish between:
        # - "finished": Round officially ended on blockchain → FINISHED
        # - "evaluating_finished": Validator finished but round ongoing
        #   → Check if still has activity, otherwise WAITING
        # - "active": Round in progress → determine state below
        if validator_round.status == "finished":
            return ValidatorStatusInfo.from_state(ValidatorState.FINISHED)
        elif validator_round.status == "evaluating_finished":
            # Validator called finish_round, but blockchain round hasn't ended
            # If there's recent activity or scores, still show as EVALUATING
            # Otherwise, show as WAITING (for consensus)
            if successful_runs > 0 or has_scores:
                if seconds_since_activity is None or seconds_since_activity < 3600:
                    # Recent activity, still evaluating
                    return ValidatorStatusInfo.from_state(ValidatorState.EVALUATING)
            # No recent activity, waiting for consensus
            return ValidatorStatusInfo.from_state(ValidatorState.WAITING)

        # Round is active - determine state based on activity
        state = ValidatorState.STARTING
        if total_runs > 0:
            state = ValidatorState.SENDING_TASKS
        if successful_runs > 0 or has_scores:
            state = ValidatorState.EVALUATING

        if seconds_since_activity is not None:
            if seconds_since_activity > 86400:
                state = ValidatorState.OFFLINE
            elif seconds_since_activity > 3600 and total_runs == 0:
                state = ValidatorState.WAITING

        return ValidatorStatusInfo.from_state(state)

    @rollback_on_error
    async def _average_score(self) -> float:
        """
        Calculate average score across all evaluation results.

        Performance optimization: Uses AVG() in PostgreSQL instead of
        loading all evaluation results into Python memory.
        """
        stmt = select(func.avg(EvaluationResultORM.final_score))
        result = await self.session.scalar(stmt)
        return float(result or 0.0)

    @staticmethod
    def _map_website_port_to_name(url: Optional[str]) -> Optional[str]:
        """Map localhost port to website name (e.g., localhost:8005 → AutoMail)."""
        if not url:
            return None

        # Port to website name mapping (matches frontend LOCALHOST_PORT_MAPPING)
        PORT_MAPPING = {
            "8000": "AutoCinema",
            "8001": "AutoBooks",
            "8002": "Autozone",
            "8003": "AutoDining",
            "8004": "AutoCRM",
            "8005": "AutoMail",
            "8006": "AutoDelivery",
            "8007": "AutoLodge",
            "8008": "AutoConnect",
            "8009": "AutoWork",
            "8010": "AutoCalendar",
            "8011": "AutoList",
            "8012": "AutoDrive",
            "8013": "AutoHealth",
            "8014": "AutoFinance",
        }

        # Extract port from URL (e.g., "http://localhost:8005/?seed=123" → "8005")
        port_match = re.search(r"localhost:(\d+)", url)
        if port_match:
            port = port_match.group(1)
            return PORT_MAPPING.get(port, f"Web Project ({port})")

        # If not localhost, return cleaned URL
        return url

    @staticmethod
    def _normalize_task_meta(
        prompt: Optional[str],
        url: Optional[str],
        relevant_data: Optional[Dict[str, Any]],
        use_case: Optional[Any],
    ) -> Dict[str, Optional[str]]:
        """Normalize task metadata into the structure consumed by the UI."""

        website: Optional[str] = None
        if isinstance(relevant_data, dict):
            website = relevant_data.get("website") or None
        if not website:
            website = url

        # Map localhost port to friendly name
        if website:
            website = OverviewService._map_website_port_to_name(website)

        use_case_name: Optional[str] = None
        if isinstance(use_case, dict):
            use_case_name = use_case.get("name") or None
        elif isinstance(use_case, str):
            use_case_name = use_case

        return {
            "prompt": prompt if prompt else None,
            "website": website or None,
            "useCase": use_case_name or None,
        }

    @rollback_on_error
    async def _latest_evaluated_task_meta(
        self, validator_round_id: str
    ) -> Optional[Dict[str, Optional[str]]]:
        """Fetch latest evaluated task prompt + website + use case for a validator round."""

        stmt = (
            select(
                TaskORM.prompt,
                TaskORM.url,
                TaskORM.relevant_data,
                TaskORM.use_case,
            )
            .join(EvaluationResultORM, EvaluationResultORM.task_id == TaskORM.task_id)
            .where(EvaluationResultORM.validator_round_id == validator_round_id)
            .order_by(
                EvaluationResultORM.created_at.desc(), EvaluationResultORM.id.desc()
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if not row:
            return None
        prompt, url, relevant_data, use_case = row
        return self._normalize_task_meta(prompt, url, relevant_data, use_case)

    @rollback_on_error
    async def _latest_task_meta(
        self, validator_round_id: str
    ) -> Optional[Dict[str, Optional[str]]]:
        """Fetch the most recent task metadata, falling back to stored tasks if needed."""

        meta = await self._latest_evaluated_task_meta(validator_round_id)
        if meta:
            return meta

        stmt = (
            select(
                TaskORM.prompt,
                TaskORM.url,
                TaskORM.relevant_data,
                TaskORM.use_case,
            )
            .where(TaskORM.validator_round_id == validator_round_id)
            .order_by(
                TaskORM.created_at.desc(),
                TaskORM.id.desc(),
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if not row:
            return None
        prompt, url, relevant_data, use_case = row
        return self._normalize_task_meta(prompt, url, relevant_data, use_case)

    @rollback_on_error
    async def _aggregate_validators(self) -> Dict[str, Dict[str, Any]]:
        # Try to get from Redis cache first (10 minute TTL - shared across all workers)
        cache_key = "overview:validators:aggregate"
        cached = redis_cache.get(cache_key)
        if cached is not None:
            return cached

        # Performance optimization: Don't load agent run contexts since we only need
        # round metadata for validator aggregation (eliminates N+1 queries)
        records_with_contexts = await self._recent_round_records(
            limit=20, include_details=False, fetch_contexts=False
        )
        aggregates: Dict[str, Dict[str, Any]] = {}

        # ═══════════════════════════════════════════════════════════════════
        # Determine CURRENT round from blockchain (not max in DB)
        # This ensures we show validators as "waiting" when DB is behind
        # ═══════════════════════════════════════════════════════════════════
        try:
            current_block = get_current_block_estimate()
            current_round_from_blockchain = 0
            if current_block is not None:
                try:
                    current_round_from_blockchain = compute_round_number(current_block)
                    logger.debug(
                        f"[_aggregate_validators] Current round from blockchain: {current_round_from_blockchain}"
                    )
                except Exception:
                    pass
        except Exception:
            current_block = None
            current_round_from_blockchain = 0

        # Also track max round in DB for fallback
        round_numbers: List[int] = []
        for record, _ in records_with_contexts:
            num = record.model.round_number or _round_id_to_int(
                record.model.validator_round_id
            )
            if num:
                round_numbers.append(num)
        round_numbers = sorted(set(round_numbers), reverse=True)
        max_round_in_db = round_numbers[0] if round_numbers else 0

        if max_round_in_db == 0 and records_with_contexts:
            fallback_record = records_with_contexts[0][0]
            max_round_in_db = fallback_record.model.round_number or _round_id_to_int(
                fallback_record.model.validator_round_id
            )

        # Use blockchain round as "current", fallback to DB max if blockchain unavailable
        current_round_number = (
            current_round_from_blockchain
            if current_round_from_blockchain > 0
            else max_round_in_db
        )

        logger.debug(
            f"[_aggregate_validators] current_round_number={current_round_number}, "
            f"blockchain={current_round_from_blockchain}, db_max={max_round_in_db}"
        )

        # Build helper maps:
        # - current_round_entries: entries for the CURRENT round from blockchain
        # - last_entry_by_uid: last participation for each validator (used for last seen round info)
        current_round_entries: Dict[int, Tuple[RoundRecord, List[AgentRunContext]]] = {}
        last_entry_by_uid: Dict[int, Tuple[RoundRecord, List[AgentRunContext]]] = {}
        for record, contexts in records_with_contexts:
            round_number = record.model.round_number or _round_id_to_int(
                record.model.validator_round_id
            )
            validator_uid = record.model.validator_uid or record.validator_uid
            if validator_uid is None:
                continue
            # Track most recent record per validator overall
            prev_last = last_entry_by_uid.get(validator_uid)
            prev_ts = (
                (prev_last[0].model.ended_at or prev_last[0].model.started_at or 0.0)
                if prev_last
                else -1
            )
            curr_ts = record.model.ended_at or record.model.started_at or 0.0
            if prev_last is None or curr_ts >= prev_ts:
                last_entry_by_uid[validator_uid] = (record, contexts)
            # Track latest record for the CURRENT BLOCKCHAIN round only
            # This will be empty if validator hasn't started current round yet
            if round_number == current_round_number:
                existing = current_round_entries.get(validator_uid)
                if existing is None or (record.model.started_at or 0.0) > (
                    existing[0].model.started_at or 0.0
                ):
                    current_round_entries[validator_uid] = (record, contexts)

        now_ts = datetime.now(timezone.utc).timestamp()
        meta_cache: Dict[str, Optional[Dict[str, Optional[str]]]] = {}

        # Limit expected validators to those who participated in recent rounds instead of a static directory.
        # Include validators from the current round and the last few completed rounds (configurable).
        lookback = settings.OVERVIEW_VALIDATORS_LOOKBACK_ROUNDS or 0
        if lookback < 0:
            lookback = 0
        recent_round_numbers = set(round_numbers[:lookback]) if round_numbers else set()
        recent_participant_uids: set[int] = set()
        for record, _ in records_with_contexts:
            rn = record.model.round_number or _round_id_to_int(
                record.model.validator_round_id
            )
            uid = record.model.validator_uid or record.validator_uid
            if uid is None:
                continue
            if rn in recent_round_numbers:
                recent_participant_uids.add(uid)

        known_validator_uids = (
            set(current_round_entries.keys()) | recent_participant_uids
        )

        # Fetch fresh metagraph data for all validators
        fresh_metagraph_data: Dict[int, Dict[str, Any]] = {}
        try:
            fresh_metagraph_data = get_all_validators_data()
            logger.info(
                f"Loaded fresh metagraph data for {len(fresh_metagraph_data)} validators"
            )
        except MetagraphError as exc:
            logger.warning(
                f"Failed to fetch fresh metagraph data, will use DB snapshots: {exc}"
            )

        for validator_uid in sorted(known_validator_uids):
            entry = current_round_entries.get(validator_uid)
            last_entry = last_entry_by_uid.get(validator_uid)

            current_record = entry[0] if entry else None
            current_contexts = entry[1] if entry else []

            display_record = current_record or (last_entry[0] if last_entry else None)
            display_round = display_record.model if display_record else None
            validator_info = (
                getattr(display_round, "validator_info", None)
                if display_round
                else None
            )

            contexts_flat = current_contexts

            # Use last participation for display of last seen round when not currently running
            # If validator has never participated, show current blockchain round
            if last_entry is not None:
                round_number = last_entry[0].model.round_number or _round_id_to_int(
                    last_entry[0].model.validator_round_id
                )
            else:
                round_number = current_round_number or None

            display_name = (
                validator_info.name if validator_info and validator_info.name else None
            )
            if not display_name and display_round:
                display_name = (
                    getattr(display_round, "metadata", {}).get("validator_name") or None
                )
            if not display_name:
                display_name = f"Validator {validator_uid}"

            hotkey_candidates = []
            if validator_info and validator_info.hotkey:
                hotkey_candidates.append(validator_info.hotkey)
            if display_round and display_round.validator_hotkey:
                hotkey_candidates.append(display_round.validator_hotkey)
            if current_record and current_record.model.validator_hotkey:
                hotkey_candidates.append(current_record.model.validator_hotkey)
            hotkey = next(
                (candidate for candidate in hotkey_candidates if candidate), None
            )

            existing_icon = (
                getattr(validator_info, "image_url", None) if validator_info else None
            )

            # DEBUG: Log validator image resolution
            logger.debug(
                f"[Validator {validator_uid}] Image resolution: "
                f"validator_info={validator_info is not None}, "
                f"existing_icon={existing_icon}, "
                f"display_name={display_name}"
            )

            icon = resolve_validator_image(display_name, existing=existing_icon)

            logger.debug(f"[Validator {validator_uid}] Final icon={icon}")

            # Get fresh data from metagraph, fallback to DB snapshot if unavailable
            fresh_data = fresh_metagraph_data.get(validator_uid)

            stake_value: float = 0.0
            trust_value: float = 0.0
            version = None

            # Get stake and vtrust from metagraph (preferred) or DB (fallback)
            if fresh_data:
                # Use fresh metagraph data for stake/vtrust (preferred)
                stake_value = fresh_data.get("stake") or 0.0
                trust_value = fresh_data.get("vtrust") or 0.0
                logger.debug(
                    f"[Validator {validator_uid}] Using fresh metagraph data: "
                    f"stake={stake_value:.2f}, vtrust={trust_value:.4f}"
                )
            else:
                # Fallback to DB snapshot data for stake/vtrust
                if validator_info and validator_info.stake is not None:
                    try:
                        stake_value = float(validator_info.stake)
                    except (TypeError, ValueError):
                        stake_value = 0.0

                if validator_info and validator_info.vtrust is not None:
                    try:
                        trust_value = float(validator_info.vtrust)
                    except (TypeError, ValueError):
                        trust_value = 0.0

                logger.debug(
                    f"[Validator {validator_uid}] Using DB snapshot data: "
                    f"stake={stake_value:.2f}, vtrust={trust_value:.4f}"
                )

            # Version is ALWAYS from DB (not in metagraph)
            if validator_info and validator_info.version:
                version = str(validator_info.version)

            if display_round:
                total_tasks = display_round.n_tasks or 0
                completed_tasks = self.rounds_service._estimate_completed_tasks(
                    display_round
                )
                validator_round_id = display_round.validator_round_id
            else:
                total_tasks = 0
                completed_tasks = 0
                validator_round_id = None

            total_runs = len(contexts_flat)
            successful_runs = len(
                [
                    ctx
                    for ctx in contexts_flat
                    if self.rounds_service._context_score(ctx) >= 0.5
                ]
            )
            has_scores = any(
                self.rounds_service._context_score(ctx) > 0.0 for ctx in contexts_flat
            )

            last_activity_candidates: List[float] = []
            # Prefer current round activity; otherwise fall back to last participation
            source_entry = entry or last_entry
            if source_entry:
                record_ref, ctx_list = source_entry
                round_model = record_ref.model
                if round_model.ended_at:
                    last_activity_candidates.append(round_model.ended_at)
                if round_model.started_at:
                    last_activity_candidates.append(round_model.started_at)
                for context in ctx_list:
                    if context.run.ended_at:
                        last_activity_candidates.append(context.run.ended_at)
                    elif context.run.started_at:
                        last_activity_candidates.append(context.run.started_at)

            last_activity_ts = (
                max(last_activity_candidates) if last_activity_candidates else None
            )
            seconds_since_activity = (
                max(0.0, now_ts - last_activity_ts)
                if last_activity_ts is not None
                else None
            )

            validator_round = current_record.model if current_record else None

            status_info = self._derive_validator_status(
                current_record,
                total_runs=total_runs,
                successful_runs=successful_runs,
                has_scores=has_scores,
                seconds_since_activity=seconds_since_activity,
            )
            status = status_info.label
            current_task = status_info.default_task

            cache_key = validator_round.validator_round_id if validator_round else None
            current_website: Optional[str] = None
            current_use_case: Optional[str] = None
            # Always fetch task metadata if we have a validator_round_id, even for finished rounds
            # This allows showing the last task processed before the round ended
            if cache_key:
                if cache_key not in meta_cache:
                    meta_cache[cache_key] = await self._latest_task_meta(cache_key)
                meta = meta_cache.get(cache_key)
                if meta:
                    # For finished/not_started states, still populate website/use case from last task
                    # but keep the default task message
                    if status_info.requires_prompt and meta.get("prompt"):
                        current_task = meta.get("prompt") or current_task
                    # Always populate website and use case if available (even for finished rounds)
                    current_website = meta.get("website") or None
                    current_use_case = meta.get("useCase") or None

            uptime = round(
                min(
                    100.0,
                    (completed_tasks / total_tasks * 100.0) if total_tasks else 0.0,
                ),
                1,
            )

            emission_value: Optional[int] = None
            if isinstance(stake_value, (int, float)):
                emission_value = int(float(stake_value) * 0.05)

            key = f"validator-{validator_uid}"
            aggregates[key] = {
                "id": key,
                "name": display_name,
                "hotkey": hotkey,
                "icon": icon,
                "currentTask": current_task,
                "currentWebsite": current_website,
                "currentUseCase": current_use_case,
                "status": status,
                "statusCode": status_info.state.value,
                "totalTasks": int(total_tasks),
                "weight": stake_value,
                "trust": trust_value,
                "version": version,
                "lastSeen": _timestamp(last_activity_ts),
                "stake": stake_value,
                "emission": emission_value,
                "uptime": uptime,
                "completedTasks": int(completed_tasks),
                "validatorRoundId": validator_round_id,
                "roundNumber": round_number,
                "validatorUid": validator_uid,
            }

        # Cache the aggregated validators for 10 minutes in Redis (shared across all workers)
        redis_cache.set(cache_key, aggregates, ttl=600)
        return aggregates

    def _round_to_info(self, round_obj: ValidatorRound, current: bool) -> RoundInfo:
        total_tasks = round_obj.n_tasks
        completed_tasks = len(round_obj.winners or [])
        average_score = 0.0
        top_score = 0.0
        if round_obj.winners:
            scores = [winner.get("score", 0.0) for winner in round_obj.winners]
            if scores:
                average_score = sum(scores) / len(scores)
                top_score = max(scores)

        derived_status = round_obj.status or ("active" if current else "finished")
        if round_obj.ended_at:
            derived_status = "finished"
        elif not round_obj.ended_at and current:
            derived_status = "active"

        return RoundInfo(
            id=int(
                round_obj.round_number or _round_id_to_int(round_obj.validator_round_id)
            ),
            startBlock=round_obj.start_block,
            endBlock=round_obj.end_block or round_obj.start_block + 360,
            current=current,
            startTime=_timestamp(round_obj.started_at),
            endTime=_timestamp(round_obj.ended_at) if round_obj.ended_at else None,
            status=derived_status,
            totalTasks=total_tasks,
            completedTasks=completed_tasks,
            averageScore=round(average_score, 3),
            topScore=round(top_score, 3),
        )
