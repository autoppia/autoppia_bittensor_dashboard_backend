from __future__ import annotations

import logging
import re
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
    EvaluationORM,
    RoundORM,
    TaskORM,
    ValidatorRoundSummaryORM,
    ValidatorRoundMinerORM,
    ValidatorRoundValidatorORM,
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
)
from app.services.ui.rounds_service import AgentRunContext, RoundRecord, RoundsService
from app.services.chain_state import get_current_block_estimate
from app.services.round_calc import (
    compute_round_number,
    compute_round_number_in_season,
    compute_season_number,
)
from app.services.redis_cache import redis_cache
from app.services.metagraph_service import get_all_validators_data, MetagraphError
from app.config import settings
from app.utils.images import resolve_validator_image
from app.services.service_utils import rollback_on_error

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # Enable INFO for debugging top miner query


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
            requires_prompt=state not in {ValidatorState.NOT_STARTED, ValidatorState.FINISHED},
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
        # Try to get from Redis cache first (15 minute TTL)
        cache_key = "overview:metrics:aggregate"
        cached = redis_cache.get(cache_key)
        if cached is not None:
            cached_top_uid = getattr(cached, "topMinerUid", None)
            cached_top_reward = getattr(cached, "topReward", 0)
            logger.info("Returning cached overview metrics (topMinerUid=%s, topReward=%s)", cached_top_uid, cached_top_reward)
            # If cache has null/0 values, invalidate and recompute
            if cached_top_uid is None and cached_top_reward == 0.0:
                logger.warning("Cache has null/0 values - invalidating and recomputing")
                redis_cache.delete(cache_key)
            else:
                return cached

        logger.info("Cache miss - computing fresh overview metrics")

        # Defensive rollback so we start with a clean transaction (avoids InFailedSQLTransactionError
        # if the connection was returned to the pool in an aborted state by a previous request).
        try:
            await self.session.rollback()
        except Exception:  # noqa: BLE001
            pass

        records_with_contexts = await self._recent_round_records(
            limit=10,
            include_details=True,  # Load eval results so scores/winners are accurate
            context_limit=None,  # Include all agent runs per round
        )
        if not records_with_contexts:
            now_iso = datetime.now(timezone.utc).isoformat()
            return OverviewMetrics(
                topReward=0.0,
                totalWebsites=0,
                totalValidators=0,
                totalMiners=0,
                currentRound=0,
                currentSeason=None,
                currentRoundInSeason=None,
                metricsRound=0,
                metricsSeason=None,
                metricsRoundInSeason=None,
                subnetVersion="1.0.0",
                lastUpdated=now_iso,
            )

        def _round_number(record: RoundRecord) -> int:
            model = record.model
            return model.round_number or _round_id_to_int(model.validator_round_id)

        # Group records by round so we can easily pick the latest completed set.
        round_records_by_number: Dict[int, List[Tuple[RoundRecord, List[AgentRunContext]]]] = {}
        completed_round_numbers: set[int] = set()
        for record, contexts in records_with_contexts:
            number = _round_number(record)
            if number:
                round_records_by_number.setdefault(number, []).append((record, contexts))
                if record.model.ended_at:
                    completed_round_numbers.add(number)

        latest_completed_round = max(completed_round_numbers) if completed_round_numbers else None

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
                logger.debug(f"Computed current round from blockchain: block={current_block}, round={current_round_value}")
            except Exception as exc:
                logger.warning("Failed to compute current round from blockchain: %s", exc)

        # Get current round model to extract season and round_in_season
        current_season = None
        current_round_in_season = None
        current_round_model = None
        if current_block is not None:
            try:
                round_block_length = int(settings.ROUND_SIZE_EPOCHS * settings.BLOCKS_PER_EPOCH)
                current_round_in_season = compute_round_number_in_season(
                    current_block,
                    round_block_length,
                )
                # compute_season_number expects a start block, but the same formula works for current block
                current_season = compute_season_number(current_block)
            except Exception as exc:
                logger.warning("Failed to compute current season/round-in-season from blockchain: %s", exc)

        # Fallback: if we can't get blockchain round, use max from DB
        if current_round_value <= 0:
            current_round_candidates = [_round_number(record) for record, _ in records_with_contexts if _round_number(record)]
            if current_round_candidates:
                current_round_value = max(current_round_candidates)
                logger.warning(f"Using fallback current round from DB max: {current_round_value}")

        # Find the current round model to extract season/round_in_season (fallback only)
        for record, _ in records_with_contexts:
            if _round_number(record) == current_round_value:
                current_round_model = record.model
                if current_season is None:
                    current_season = getattr(current_round_model, "season_number", None)
                if current_round_in_season is None:
                    current_round_in_season = getattr(current_round_model, "round_number_in_season", None)
                break

        # Latest finished round should be current - 1
        preferred_previous_round: Optional[int] = None
        if current_round_value > 0:
            preferred_previous_round = max(current_round_value - 1, 0)
            logger.debug(f"Current round: {current_round_value}, Preferred previous (latest finished): {preferred_previous_round}")

        candidate_round_numbers: List[int] = []
        if preferred_previous_round and preferred_previous_round > 0:
            candidate_round_numbers.append(preferred_previous_round)
        if latest_completed_round is not None and latest_completed_round not in candidate_round_numbers:
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
                logger.debug(f"Skipping round {number} for metrics: >= current round {current_round_value}")
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

        # Extract season and round_in_season from the metrics round record
        metrics_season = None
        metrics_round_in_season = None
        if target_records:
            metrics_round_model = target_records[0][0].model
            metrics_season = getattr(metrics_round_model, "season_number", None)
            metrics_round_in_season = getattr(metrics_round_model, "round_number_in_season", None)

        # This top_score is for aggregating scores from contexts (legacy, not used for top miner)
        context_top_score = 0.0
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

            # First, try to fetch consensus scores for all contexts in this round
            consensus_scores_map: Dict[Tuple[str, int], float] = {}
            if contexts:
                validator_round_id = contexts[0].round.validator_round_id
                miner_uids = [ctx.run.miner_uid for ctx in contexts if ctx.run.miner_uid is not None]
                if miner_uids:
                    try:
                        stmt = select(
                            ValidatorRoundSummaryORM.miner_uid,
                            ValidatorRoundSummaryORM.post_consensus_avg_reward,
                        ).where(
                            ValidatorRoundSummaryORM.validator_round_id == validator_round_id,
                            ValidatorRoundSummaryORM.miner_uid.in_(miner_uids),
                        )
                        result = await self.session.execute(stmt)
                        rows = result.all()
                        for row in rows:
                            if row.post_consensus_avg_reward is not None:
                                consensus_scores_map[(validator_round_id, row.miner_uid)] = float(row.post_consensus_avg_reward)
                    except Exception as e:
                        logger.debug(f"Could not fetch consensus scores: {e}")
                        try:
                            await self.session.rollback()
                        except Exception:  # noqa: BLE001
                            pass

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

                # Prefer consensus_score if available, otherwise use _context_score
                score = None
                if ctx.run.miner_uid is not None:
                    consensus_key = (ctx.round.validator_round_id, ctx.run.miner_uid)
                    score = consensus_scores_map.get(consensus_key)

                if score is None:
                    score = self.rounds_service._context_score(ctx)

                # If score is 0 and average_score is NULL, we need to query from DB
                if score == 0.0 and ctx.run.average_score is None and ctx.run.agent_run_id:
                    agent_runs_to_query.append(ctx.run.agent_run_id)

                if miner_identifier:
                    tracker = miner_score_tracker.setdefault(miner_identifier, [])
                    tracker.append(score)

            # Query DB for agent_runs with NULL average_score
            if agent_runs_to_query:
                try:
                    from app.db.models import EvaluationORM

                    logger.info(f"Querying DB for {len(agent_runs_to_query)} agent_runs with NULL average_score")

                    stmt = (
                        select(
                            AgentEvaluationRunORM.agent_run_id,
                            AgentEvaluationRunORM.miner_uid,
                            func.avg(EvaluationORM.eval_score).label("avg_score"),
                        )
                        .join(
                            EvaluationORM,
                            EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
                        )
                        .where(AgentEvaluationRunORM.agent_run_id.in_(agent_runs_to_query))
                        .group_by(
                            AgentEvaluationRunORM.agent_run_id,
                            AgentEvaluationRunORM.miner_uid,
                        )
                    )
                    result = await self.session.execute(stmt)
                    rows = result.all()

                    if rows:
                        logger.info(f"✅ Found {len(rows)} agent_runs with evaluations in DB")
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
                                    logger.debug(f"  Updated {miner_identifier}: {avg_score}")
                except Exception as e:
                    logger.error(f"Failed to query evaluations from DB: {e}")
                    try:
                        await self.session.rollback()
                    except Exception:  # noqa: BLE001
                        pass

                for task in ctx.tasks or []:
                    if task.task_id in seen_tasks:
                        continue
                    seen_tasks.add(task.task_id)
                    host = urlparse(task.url).netloc or task.url
                    if host:
                        unique_websites.add(host.lower())

            if not contexts:
                # If no contexts loaded, try winners first
                logger.info(f"No contexts for round {round_obj.validator_round_id}, checking winners or DB...")
                if round_obj.winners:
                    round_top = max(winner.get("score", 0.0) for winner in round_obj.winners)
                    context_top_score = max(context_top_score, round(round_top, 6))
                    logger.info(f"Using winners: context_top_score={round_top}")
                else:
                    # Fallback: first try consensus scores, then query evaluations directly from DB
                    logger.info(f"No winners, querying DB for round {round_obj.validator_round_id}")
                    try:
                        # First try to get consensus scores
                        stmt = select(
                            ValidatorRoundSummaryORM.miner_uid,
                            ValidatorRoundSummaryORM.post_consensus_avg_reward,
                        ).where(ValidatorRoundSummaryORM.validator_round_id == round_obj.validator_round_id)
                        result = await self.session.execute(stmt)
                        consensus_rows = result.all()

                        if consensus_rows:
                            logger.info(f"Found {len(consensus_rows)} consensus scores for round {round_obj.validator_round_id}")
                            for row in consensus_rows:
                                miner_uid, post_consensus_reward = row
                                if post_consensus_reward is not None:
                                    miner_identifier = f"uid:{miner_uid}"
                                    miners.add(miner_identifier)
                                    tracker = miner_score_tracker.setdefault(miner_identifier, [])
                                    tracker.append(float(post_consensus_reward))
                            logger.info(f"✅ Loaded {len(consensus_rows)} consensus scores from DB for round {round_obj.validator_round_id}")
                        else:
                            # Fallback to evaluations if no consensus scores
                            from app.db.models import EvaluationORM

                            # Get all evaluations for this validator_round and calculate avg per miner
                            stmt = (
                                select(
                                    AgentEvaluationRunORM.miner_uid,
                                    func.avg(EvaluationORM.eval_score).label("avg_score"),
                                )
                                .join(
                                    EvaluationORM,
                                    EvaluationORM.agent_run_id == AgentEvaluationRunORM.agent_run_id,
                                )
                                .where(AgentEvaluationRunORM.validator_round_id == round_obj.validator_round_id)
                                .group_by(AgentEvaluationRunORM.miner_uid)
                            )
                            result = await self.session.execute(stmt)
                            rows = result.all()

                            logger.info(f"DB query returned {len(rows)} rows for round {round_obj.validator_round_id}")
                            if rows:
                                for row in rows:
                                    miner_uid, avg_score = row
                                    logger.debug(f"  Miner {miner_uid}: {avg_score}")
                                    if avg_score is not None:
                                        miner_identifier = f"uid:{miner_uid}"
                                        miners.add(miner_identifier)
                                        tracker = miner_score_tracker.setdefault(miner_identifier, [])
                                        tracker.append(float(avg_score))
                                logger.info(f"✅ Loaded {len(rows)} miner scores from DB for round {round_obj.validator_round_id}")
                            else:
                                logger.warning(f"No evaluation data found in DB for round {round_obj.validator_round_id}")
                    except Exception as e:
                        logger.error(f"Failed to load scores for round {round_obj.validator_round_id}: {e}")
                        try:
                            await self.session.rollback()
                        except Exception:  # noqa: BLE001
                            pass

        top_miner_uid = None
        top_miner_name = None
        top_score = 0.0

        # Query: Get the latest FINISHED validator_round_id from Autoppia validators
        # 60 = dev/test, 83/124 = production
        try:
            autoppia_uids = [60, 83, 124]
            logger.info(
                "Starting optimized query for top miner from Autoppia validators: %s",
                autoppia_uids,
            )

            # Step 1: Find the latest finished round for Autoppia validators (optimized with indexes)
            stmt_latest_round = (
                select(
                    RoundORM.validator_round_id,
                    RoundORM.season_number,
                    RoundORM.round_number_in_season,
                    RoundORM.status,
                    ValidatorRoundValidatorORM.validator_uid,
                )
                .select_from(
                    RoundORM.__table__.join(
                        ValidatorRoundValidatorORM.__table__,
                        RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
                    )
                )
                .where(
                    ValidatorRoundValidatorORM.validator_uid.in_(autoppia_uids),
                    RoundORM.season_number.is_not(None),
                    RoundORM.round_number_in_season.is_not(None),
                    RoundORM.status == "finished",
                )
                .order_by(
                    RoundORM.season_number.desc(),
                    RoundORM.round_number_in_season.desc(),
                    func.coalesce(RoundORM.ended_at, RoundORM.started_at).desc(),
                )
                .limit(1)
            )
            result_latest_round = await self.session.execute(stmt_latest_round)
            latest_round_row = result_latest_round.first()

            if latest_round_row:
                latest_validator_round_id = latest_round_row.validator_round_id
                latest_season = latest_round_row.season_number
                latest_round_in_season = latest_round_row.round_number_in_season
                latest_status = latest_round_row.status
                validator_uid_found = latest_round_row.validator_uid

                logger.info(
                    "✅ Found latest round: validator_round_id=%s (season %s, round %s, validator UID %s, status=%s)",
                    latest_validator_round_id,
                    latest_season,
                    latest_round_in_season,
                    validator_uid_found,
                    latest_status,
                )

                # Step 2: Get top miner + name from that specific round with JOIN (optimized)
                stmt_top_miner = (
                    select(
                        ValidatorRoundSummaryORM.miner_uid,
                        ValidatorRoundSummaryORM.post_consensus_avg_reward,
                        ValidatorRoundMinerORM.name,
                    )
                    .outerjoin(
                        ValidatorRoundMinerORM,
                        (ValidatorRoundSummaryORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id) & (ValidatorRoundSummaryORM.miner_uid == ValidatorRoundMinerORM.miner_uid),
                    )
                    .where(
                        ValidatorRoundSummaryORM.validator_round_id == latest_validator_round_id,
                        ValidatorRoundSummaryORM.post_consensus_avg_reward.is_not(None),
                    )
                    .order_by(ValidatorRoundSummaryORM.post_consensus_avg_reward.desc())
                    .limit(1)
                )
                result_top_miner = await self.session.execute(stmt_top_miner)
                top_miner_row = result_top_miner.first()

                if top_miner_row:
                    top_miner_uid = top_miner_row.miner_uid
                    top_score = float(top_miner_row.post_consensus_avg_reward) if top_miner_row.post_consensus_avg_reward else 0.0
                    top_miner_name = top_miner_row.name

                    logger.info(
                        "✅ Found top miner: miner_uid=%s, reward=%s, name=%s",
                        top_miner_uid,
                        top_score,
                        top_miner_name,
                    )
                else:
                    logger.warning(
                        "❌ No top miner found in round %s",
                        latest_validator_round_id,
                    )
            else:
                logger.warning(
                    "❌ No finished round found for Autoppia validators (%s)",
                    autoppia_uids,
                )
        except Exception as e:
            logger.error(f"❌ Error fetching top miner from latest round: {e}", exc_info=True)
            try:
                await self.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            # Don't let the error prevent the rest of the metrics from being calculated
            # top_miner_uid, top_miner_name, and top_score will remain None/None/0.0

        subnet_version = version_candidates[0] if version_candidates else "1.0.0"
        try:
            total_websites = len(unique_websites) if unique_websites else await self._total_websites()
        except Exception as e:
            logger.warning("Failed to get total_websites: %s", e)
            try:
                await self.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            total_websites = len(unique_websites) if unique_websites else 0

        display_metrics_round_number = int(metrics_round_number or 0)
        if display_metrics_round_number < 0:
            display_metrics_round_number = 0

        # Count all unique miners from the metrics round by querying validator_round_summary_miners
        # This ensures we get all miners from the round, not just those in loaded contexts
        total_miners_count = len(miners)  # Fallback to context-based count
        if metrics_season is not None and metrics_round_in_season is not None:
            try:
                # RoundORM is already imported at the top of the file
                stmt_total_miners = (
                    select(func.count(func.distinct(ValidatorRoundSummaryORM.miner_uid)))
                    .join(
                        RoundORM,
                        ValidatorRoundSummaryORM.validator_round_id == RoundORM.validator_round_id,
                    )
                    .where(
                        RoundORM.season_number == metrics_season,
                        RoundORM.round_number_in_season == metrics_round_in_season,
                        ValidatorRoundSummaryORM.miner_uid.is_not(None),
                    )
                )
                result_total_miners = await self.session.execute(stmt_total_miners)
                total_miners_count = result_total_miners.scalar() or len(miners)
            except Exception as e:
                logger.debug(f"Could not fetch total miners count for season {metrics_season} round {metrics_round_in_season}: {e}")
                try:
                    await self.session.rollback()
                except Exception:  # noqa: BLE001
                    pass
                # Fallback to context-based count

        metrics = OverviewMetrics(
            topReward=round(top_score, 3),
            topMinerUid=top_miner_uid,
            topMinerName=top_miner_name,
            totalWebsites=total_websites,
            totalValidators=len(validators),
            totalMiners=total_miners_count,
            currentRound=current_round_value,
            currentSeason=current_season,
            currentRoundInSeason=current_round_in_season,
            metricsRound=display_metrics_round_number,
            metricsSeason=metrics_season,
            metricsRoundInSeason=metrics_round_in_season,
            subnetVersion=subnet_version,
            lastUpdated=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(
            "✅ Final metrics computed: topMinerUid=%s, topMinerName=%s, topReward=%s, metricsRound=%s",
            metrics.topMinerUid,
            metrics.topMinerName,
            metrics.topReward,
            metrics.metricsRound,
        )

        # Only cache if we have valid top miner data, otherwise don't cache to force recalculation
        if metrics.topMinerUid is not None and metrics.topReward > 0.0:
            # Cache metrics for 15 minutes in Redis (shared across all workers)
            redis_cache.set(cache_key, metrics, ttl=900)
            logger.info("Cached metrics in Redis with key: %s (TTL: 15 minutes) - topMinerUid=%s, topReward=%s", cache_key, metrics.topMinerUid, metrics.topReward)
        else:
            logger.warning("NOT caching metrics - topMinerUid=%s, topReward=%s (will force recalculation next time)", metrics.topMinerUid, metrics.topReward)
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
        # Optimización: Primero intentar obtener del caché completo de validators
        # Si no está disponible, construir solo el validador solicitado en lugar de todos
        cache_key_aggregate = "overview:validators:aggregate"
        cached_validators = redis_cache.get(cache_key_aggregate)

        if cached_validators is not None:
            validator = cached_validators.get(validator_id)
            if validator:
                # Crear una copia del diccionario para poder modificarlo (puede venir de cache)
                validator = dict(validator)
            else:
                raise ValueError(f"Validator {validator_id} not found")
        else:
            # Si no hay caché, cargar todos los validadores (necesario para mantener consistencia)
            # pero esto debería ser raro ya que el caché tiene TTL de 10 minutos
            validators = await self._aggregate_validators()
            validator = validators.get(validator_id)
            if not validator:
                raise ValueError(f"Validator {validator_id} not found")
            # Crear una copia del diccionario para poder modificarlo
            validator = dict(validator)

        # Agregar información del ganador de la última ronda
        # Intentar extraer el UID del validator_id (puede ser "validator-83" o "83")
        try:
            validator_uid = int(validator_id.split("-")[-1]) if "-" in validator_id else int(validator_id)
        except (ValueError, IndexError):
            # Si no se puede extraer el UID, devolver validator sin lastRoundWinner
            return validator

        # Optimización: Obtener la última ronda y el ganador en una sola query con JOINs
        # Primero intentar obtener ganador de ronda finalizada (post_consensus_rank == 1)
        winner_query = (
            select(
                RoundORM.validator_round_id,
                RoundORM.season_number,
                RoundORM.round_number_in_season,
                ValidatorRoundSummaryORM.miner_uid,
                ValidatorRoundSummaryORM.miner_hotkey,
                ValidatorRoundSummaryORM.post_consensus_avg_reward,
                ValidatorRoundSummaryORM.weight,
                ValidatorRoundMinerORM.name,
                ValidatorRoundMinerORM.image_url,
            )
            .select_from(
                RoundORM.__table__.join(ValidatorRoundValidatorORM.__table__, RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
                .join(ValidatorRoundSummaryORM.__table__, RoundORM.validator_round_id == ValidatorRoundSummaryORM.validator_round_id)
                .outerjoin(
                    ValidatorRoundMinerORM.__table__,
                    (RoundORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id) & (ValidatorRoundSummaryORM.miner_uid == ValidatorRoundMinerORM.miner_uid),
                )
            )
            .where(ValidatorRoundValidatorORM.validator_uid == validator_uid, ValidatorRoundSummaryORM.post_consensus_rank == 1)
            .order_by(RoundORM.season_number.desc(), RoundORM.round_number_in_season.desc())
            .limit(1)
        )
        winner_result = await self.session.execute(winner_query)
        winner_row = winner_result.first()

        if winner_row:
            # Ronda finalizada con ganador
            validator["lastRoundWinner"] = {
                "uid": winner_row.miner_uid,
                "name": winner_row.name if winner_row.name else f"Miner {winner_row.miner_uid}",
                "image": winner_row.image_url if winner_row.image_url else None,
                "hotkey": winner_row.miner_hotkey,
                "reward": float(winner_row.post_consensus_avg_reward) if winner_row.post_consensus_avg_reward else None,
                "weight": float(winner_row.weight) if winner_row.weight else None,
            }
        else:
            # Si no hay datos en ValidatorRoundSummaryORM (ronda activa),
            # buscar el top miner en AgentEvaluationRunORM por average_reward
            # Optimización: Combinar con JOIN para obtener snapshot del miner en una query
            top_run_query = (
                select(
                    RoundORM.validator_round_id,
                    AgentEvaluationRunORM.miner_uid,
                    AgentEvaluationRunORM.miner_hotkey,
                    AgentEvaluationRunORM.average_reward,
                    ValidatorRoundMinerORM.name,
                    ValidatorRoundMinerORM.image_url,
                )
                .select_from(
                    RoundORM.__table__.join(ValidatorRoundValidatorORM.__table__, RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
                    .join(AgentEvaluationRunORM.__table__, RoundORM.validator_round_id == AgentEvaluationRunORM.validator_round_id)
                    .outerjoin(
                        ValidatorRoundMinerORM.__table__,
                        (RoundORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id) & (AgentEvaluationRunORM.miner_uid == ValidatorRoundMinerORM.miner_uid),
                    )
                )
                .where(ValidatorRoundValidatorORM.validator_uid == validator_uid)
                .order_by(RoundORM.season_number.desc(), RoundORM.round_number_in_season.desc(), AgentEvaluationRunORM.average_reward.desc())
                .limit(1)
            )
            top_run_result = await self.session.execute(top_run_query)
            top_run_row = top_run_result.first()

            if top_run_row:
                validator["lastRoundWinner"] = {
                    "uid": top_run_row.miner_uid,
                    "name": top_run_row.name if top_run_row.name else f"Miner {top_run_row.miner_uid}",
                    "image": top_run_row.image_url if top_run_row.image_url else None,
                    "hotkey": top_run_row.miner_hotkey,
                    "reward": float(top_run_row.average_reward) if top_run_row.average_reward else None,
                    "weight": None,  # Weight no disponible para rondas activas
                }

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
            if actual_current_round_number > 0 and round_num == actual_current_round_number:
                # Verify it's not finished AND has actual data
                # A round is only "current" if it has started processing (has validator_rounds)
                has_data = round_obj.start_block is not None and round_obj.start_block > 0 and len(round_obj.validators or []) > 0
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
    async def rounds_list(self, page: int, limit: int, status: Optional[str]) -> Tuple[List[RoundInfo], Optional[RoundInfo], int]:
        rounds = await self._recent_rounds(limit=100)
        active_rounds = [round_obj for round_obj in rounds if not round_obj.ended_at]
        completed_rounds = [round_obj for round_obj in rounds if round_obj.ended_at]

        completed_rounds.sort(key=lambda r: r.started_at or 0, reverse=True)

        round_infos = [self._round_to_info(round_obj, current=False) for round_obj in completed_rounds]

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

        current_round = self._round_to_info(current_round_obj, current=True) if current_round_obj else None
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
                selectinload(RoundORM.validator_snapshot),  # 1:1 relationship
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
        """
        Obtiene el leaderboard usando las nuevas tablas:
        - validator_rounds: round_number, ended_at
        - validator_round_summary_miners: post_consensus_avg_reward, miner_uid (ganador = max post_consensus_avg_reward por round)
        - validator_round_miners: name (para el winnerUid)
        """
        normalized_range = (time_range or "").strip().lower()
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
            derived_limit = 30

        if limit is not None:
            unlimited = False
            derived_limit = min(limit, derived_limit) if derived_limit else limit

        if unlimited:
            fetch_limit = 10000  # Para "all", obtener muchos rounds (ajustar según necesidad)
        else:
            fetch_limit = derived_limit or 7  # Default a 7 rounds si no se especifica

        # Query SQL simplificada usando las nuevas tablas
        # Para cada round_number, obtenemos el miner_uid con el máximo post_consensus_avg_reward
        # FILTRADO POR VALIDADOR AUTOPPIA (60=dev, 83/124=prod)

        autoppia_uids = [60, 83, 124]

        # Subquery para obtener el máximo post_consensus_avg_reward por season_number y round_number_in_season
        # Solo del validador Autoppia (UID 83 o 124)
        max_scores_subq = (
            select(
                RoundORM.season_number,
                RoundORM.round_number_in_season,
                func.max(ValidatorRoundSummaryORM.post_consensus_avg_reward).label("max_post_consensus_reward"),
            )
            .select_from(
                RoundORM.__table__.join(
                    ValidatorRoundSummaryORM.__table__,
                    RoundORM.validator_round_id == ValidatorRoundSummaryORM.validator_round_id,
                ).join(
                    ValidatorRoundValidatorORM.__table__,
                    RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
                )
            )
            .where(RoundORM.season_number.isnot(None))
            .where(RoundORM.round_number_in_season.isnot(None))
            .where(RoundORM.status == "finished")
            .where(ValidatorRoundSummaryORM.post_consensus_avg_reward.isnot(None))
            .where(ValidatorRoundValidatorORM.validator_uid.in_(autoppia_uids))  # Autoppia (83 o 124)
            .group_by(RoundORM.season_number, RoundORM.round_number_in_season)
            .subquery()
        )

        # Query principal: obtener el ganador (miner_uid con max post_consensus_avg_reward) por round
        # Solo del validador Autoppia (UID 83 o 124)
        # Incluye reward, score y time del post_consensus
        # Primero obtenemos todos los rounds con datos del validador Autoppia, luego limitamos
        stmt = (
            select(
                RoundORM.season_number,
                RoundORM.round_number_in_season,
                ValidatorRoundSummaryORM.post_consensus_avg_reward,
                ValidatorRoundSummaryORM.post_consensus_avg_eval_score,
                ValidatorRoundSummaryORM.post_consensus_avg_eval_time,
                ValidatorRoundSummaryORM.miner_uid,
                ValidatorRoundMinerORM.name,
                RoundORM.ended_at,
            )
            .select_from(
                RoundORM.__table__.join(
                    ValidatorRoundSummaryORM.__table__,
                    RoundORM.validator_round_id == ValidatorRoundSummaryORM.validator_round_id,
                )
                .join(
                    ValidatorRoundValidatorORM.__table__,
                    RoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
                )
                .join(
                    max_scores_subq,
                    (RoundORM.season_number == max_scores_subq.c.season_number)
                    & (RoundORM.round_number_in_season == max_scores_subq.c.round_number_in_season)
                    & (ValidatorRoundSummaryORM.post_consensus_avg_reward == max_scores_subq.c.max_post_consensus_reward),
                )
            )
            .outerjoin(
                ValidatorRoundMinerORM,
                (RoundORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id) & (ValidatorRoundSummaryORM.miner_uid == ValidatorRoundMinerORM.miner_uid),
            )
            .where(RoundORM.season_number.isnot(None))
            .where(RoundORM.round_number_in_season.isnot(None))
            .where(RoundORM.status == "finished")
            .where(ValidatorRoundSummaryORM.post_consensus_avg_reward.isnot(None))
            .where(ValidatorRoundValidatorORM.validator_uid.in_(autoppia_uids))  # Autoppia (83 o 124)
            .order_by(
                RoundORM.season_number.desc(),  # Ordenar por season_number descendente (más reciente primero)
                RoundORM.round_number_in_season.desc(),  # Luego por round_number_in_season
                ValidatorRoundSummaryORM.post_consensus_avg_reward.desc(),
                ValidatorRoundSummaryORM.miner_uid.asc(),
            )
        )

        # Si no es "all", limitar los rounds en la query
        if not unlimited:
            stmt = stmt.limit(fetch_limit * 2)  # Multiplicar por 2 para asegurar que tenemos suficientes después de agrupar

        result = await self.session.execute(stmt)
        rows = result.all()

        if not rows:
            now_iso = datetime.now(timezone.utc).isoformat()
            return [], {"start": now_iso, "end": now_iso}

        # Agrupar por season_number y round_number_in_season y tomar el primero (ganador con max post_consensus_avg_reward)
        # Como la query está ordenada por post_consensus_avg_reward DESC, el primero es el ganador
        seen_rounds: Dict[Tuple[int, int], bool] = {}
        entries: List[LeaderboardEntry] = []

        for row in rows:
            season_number, round_number_in_season, post_consensus_reward, post_consensus_score, post_consensus_time, miner_uid, miner_name, ended_at = row

            if season_number is None or round_number_in_season is None or post_consensus_reward is None:
                continue

            season_num = int(season_number)
            round_num = int(round_number_in_season)
            round_key = (season_num, round_num)
            # Solo tomar el primer registro por season/round (el ganador)
            if round_key in seen_rounds:
                continue
            seen_rounds[round_key] = True

            # Use round_number_in_season for the round field (for compatibility)
            display_round = round_num

            # Convertir ended_at (float timestamp) a ISO string
            if ended_at:
                timestamp = datetime.fromtimestamp(ended_at, tz=timezone.utc).isoformat()
            else:
                timestamp = datetime.now(timezone.utc).isoformat()

            entries.append(
                LeaderboardEntry(
                    round=display_round,  # round_number_in_season
                    season=season_num,
                    subnet36=round(float(post_consensus_reward), 3),  # Mantener por compatibilidad
                    post_consensus_reward=round(float(post_consensus_reward), 3),
                    winnerUid=int(miner_uid) if miner_uid is not None else None,
                    winnerName="Burned" if miner_uid == 5 else (str(miner_name) if miner_name else None),
                    openai_cua=None,
                    anthropic_cua=None,
                    browser_use=None,
                    timestamp=timestamp,
                    post_consensus_eval_score=round(float(post_consensus_score), 3) if post_consensus_score is not None else None,
                    post_consensus_eval_time=round(float(post_consensus_time), 2) if post_consensus_time is not None else None,
                    # Campos legacy (mantener por compatibilidad)
                    score=round(float(post_consensus_score), 3) if post_consensus_score is not None else None,
                    time=round(float(post_consensus_time), 2) if post_consensus_time is not None else None,
                )
            )

        # Ordenar por season y round_number_in_season descendente (más reciente primero)
        # Esto asegura que los rounds más altos (más recientes) aparezcan primero
        entries.sort(key=lambda e: (e.season or 0, e.round), reverse=True)

        # Aplicar límite después de ordenar
        if not unlimited and derived_limit:
            entries = entries[:derived_limit]

        if not entries:
            now_iso = datetime.now(timezone.utc).isoformat()
            return [], {"start": now_iso, "end": now_iso}

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

        total_stake = int(sum(_to_float(entry.get("stake")) for entry in validators.values()))
        total_emission = int(sum(_to_float(entry.get("emission")) for entry in validators.values()))

        average_trust = sum(_to_float(entry.get("trust")) for entry in validators.values()) / len(validators) if validators else 0.0
        average_uptime = sum(_to_float(entry.get("uptime", 0.0)) for entry in validators.values()) / len(validators) if validators else 0.0

        average_score = await self._average_score()
        total_tasks_completed = sum(entry["completedTasks"] for entry in validators.values())

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
        # elapsed_sec field removed - calculate from started_at/ended_at
        network_latency_samples = []
        for round_obj in rounds:
            if round_obj.started_at and round_obj.ended_at:
                elapsed = round_obj.ended_at - round_obj.started_at
                network_latency_samples.append(elapsed)
        average_latency = int(sum(network_latency_samples) / len(network_latency_samples)) if network_latency_samples else 0

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
        last_activity_dt = datetime.fromtimestamp(last_activity_ts, tz=timezone.utc) if last_activity_ts else None

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
            timestamp_label = last_activity_dt.strftime("%Y-%m-%d %H:%M UTC") if last_activity_dt else "unknown time"
            round_label = _round_id_to_int(last_round.validator_round_id)

            if delta_hours > 6:
                status = "degraded"
                message = f"No round activity for {human_delta} — last known round #{round_label} completed at {timestamp_label}"
            else:
                status = "healthy"
                message = f"Awaiting next round — last recorded round #{round_label} finished {human_delta} ago ({timestamp_label})"

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
                selectinload(RoundORM.validator_snapshot),  # 1:1 relationship (singular)
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
                logger.warning("Failed to parse round %s: %s", row.validator_round_id, exc)
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
                    self.rounds_service._recalculate_round_from_contexts(record, contexts)

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
        stmt = select(func.avg(EvaluationORM.eval_score))
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
    async def _latest_evaluated_task_meta(self, validator_round_id: str) -> Optional[Dict[str, Optional[str]]]:
        """Fetch latest evaluated task prompt + website + use case for a validator round."""

        stmt = (
            select(
                TaskORM.prompt,
                TaskORM.url,
                TaskORM.relevant_data,
                TaskORM.use_case,
            )
            .join(EvaluationORM, EvaluationORM.task_id == TaskORM.task_id)
            .where(EvaluationORM.validator_round_id == validator_round_id)
            .order_by(EvaluationORM.created_at.desc(), EvaluationORM.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if not row:
            return None
        prompt, url, relevant_data, use_case = row
        return self._normalize_task_meta(prompt, url, relevant_data, use_case)

    @rollback_on_error
    async def _latest_task_meta(self, validator_round_id: str) -> Optional[Dict[str, Optional[str]]]:
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
        aggregate_cache_key = "overview:validators:aggregate"
        cached = redis_cache.get(aggregate_cache_key)
        if cached is not None:
            return cached

        # Performance optimization: Don't load agent run contexts since we only need
        # round metadata for validator aggregation (eliminates N+1 queries)
        records_with_contexts = await self._recent_round_records(limit=20, include_details=False, fetch_contexts=False)
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
                    logger.debug(f"[_aggregate_validators] Current round from blockchain: {current_round_from_blockchain}")
                except Exception:
                    pass
        except Exception:
            current_block = None
            current_round_from_blockchain = 0

        # Also track max round in DB for fallback
        round_numbers: List[int] = []
        for record, _ in records_with_contexts:
            num = record.model.round_number or _round_id_to_int(record.model.validator_round_id)
            if num:
                round_numbers.append(num)
        round_numbers = sorted(set(round_numbers), reverse=True)
        max_round_in_db = round_numbers[0] if round_numbers else 0

        if max_round_in_db == 0 and records_with_contexts:
            fallback_record = records_with_contexts[0][0]
            max_round_in_db = fallback_record.model.round_number or _round_id_to_int(fallback_record.model.validator_round_id)

        # Use blockchain round as "current", fallback to DB max if blockchain unavailable
        current_round_number = current_round_from_blockchain if current_round_from_blockchain > 0 else max_round_in_db

        logger.debug(f"[_aggregate_validators] current_round_number={current_round_number}, blockchain={current_round_from_blockchain}, db_max={max_round_in_db}")

        # Build helper maps:
        # - current_round_entries: entries for the CURRENT round from blockchain
        # - last_entry_by_uid: last participation for each validator (used for last seen round info)
        current_round_entries: Dict[int, Tuple[RoundRecord, List[AgentRunContext]]] = {}
        last_entry_by_uid: Dict[int, Tuple[RoundRecord, List[AgentRunContext]]] = {}
        for record, contexts in records_with_contexts:
            round_number = record.model.round_number or _round_id_to_int(record.model.validator_round_id)
            validator_uid = record.model.validator_uid or record.validator_uid
            if validator_uid is None:
                continue
            # Track most recent record per validator overall
            prev_last = last_entry_by_uid.get(validator_uid)
            prev_ts = (prev_last[0].model.ended_at or prev_last[0].model.started_at or 0.0) if prev_last else -1
            curr_ts = record.model.ended_at or record.model.started_at or 0.0
            if prev_last is None or curr_ts >= prev_ts:
                last_entry_by_uid[validator_uid] = (record, contexts)
            # Track latest record for the CURRENT BLOCKCHAIN round only
            # This will be empty if validator hasn't started current round yet
            if round_number == current_round_number:
                existing = current_round_entries.get(validator_uid)
                if existing is None or (record.model.started_at or 0.0) > (existing[0].model.started_at or 0.0):
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
            rn = record.model.round_number or _round_id_to_int(record.model.validator_round_id)
            uid = record.model.validator_uid or record.validator_uid
            if uid is None:
                continue
            if rn in recent_round_numbers:
                recent_participant_uids.add(uid)

        known_validator_uids = set(current_round_entries.keys()) | recent_participant_uids

        # Fetch fresh metagraph data for all validators
        fresh_metagraph_data: Dict[int, Dict[str, Any]] = {}
        try:
            fresh_metagraph_data = get_all_validators_data()
            logger.info(f"Loaded fresh metagraph data for {len(fresh_metagraph_data)} validators")
        except MetagraphError as exc:
            logger.warning(f"Failed to fetch fresh metagraph data, will use DB snapshots: {exc}")

        for validator_uid in sorted(known_validator_uids):
            entry = current_round_entries.get(validator_uid)
            last_entry = last_entry_by_uid.get(validator_uid)

            current_record = entry[0] if entry else None
            current_contexts = entry[1] if entry else []

            display_record = current_record or (last_entry[0] if last_entry else None)
            display_round = display_record.model if display_record else None
            validator_info = getattr(display_round, "validator_info", None) if display_round else None

            contexts_flat = current_contexts

            # Use last participation for display of last seen round when not currently running
            # If validator has never participated, show current blockchain round
            if last_entry is not None:
                round_number = last_entry[0].model.round_number or _round_id_to_int(last_entry[0].model.validator_round_id)
            else:
                round_number = current_round_number or None

            display_name = validator_info.name if validator_info and validator_info.name else None
            if not display_name and display_round:
                display_name = getattr(display_round, "metadata", {}).get("validator_name") or None
            if not display_name:
                display_name = f"Validator {validator_uid}"

            hotkey_candidates = []
            if validator_info and validator_info.hotkey:
                hotkey_candidates.append(validator_info.hotkey)
            if display_round and display_round.validator_hotkey:
                hotkey_candidates.append(display_round.validator_hotkey)
            if current_record and current_record.model.validator_hotkey:
                hotkey_candidates.append(current_record.model.validator_hotkey)
            hotkey = next((candidate for candidate in hotkey_candidates if candidate), None)

            existing_icon = getattr(validator_info, "image_url", None) if validator_info else None

            # DEBUG: Log validator image resolution
            logger.debug(f"[Validator {validator_uid}] Image resolution: validator_info={validator_info is not None}, existing_icon={existing_icon}, display_name={display_name}")

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
                logger.debug(f"[Validator {validator_uid}] Using fresh metagraph data: stake={stake_value:.2f}, vtrust={trust_value:.4f}")
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

                logger.debug(f"[Validator {validator_uid}] Using DB snapshot data: stake={stake_value:.2f}, vtrust={trust_value:.4f}")

            # Version is ALWAYS from DB (not in metagraph)
            if validator_info and validator_info.version:
                version = str(validator_info.version)

            if display_round:
                total_tasks = display_round.n_tasks or 0
                completed_tasks = self.rounds_service._estimate_completed_tasks(display_round)
                validator_round_id = display_round.validator_round_id
            else:
                total_tasks = 0
                completed_tasks = 0
                validator_round_id = None

            total_runs = len(contexts_flat)
            successful_runs = len([ctx for ctx in contexts_flat if self.rounds_service._context_score(ctx) >= 0.5])
            has_scores = any(self.rounds_service._context_score(ctx) > 0.0 for ctx in contexts_flat)

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

            last_activity_ts = max(last_activity_candidates) if last_activity_candidates else None
            seconds_since_activity = max(0.0, now_ts - last_activity_ts) if last_activity_ts is not None else None

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

            round_cache_key = validator_round.validator_round_id if validator_round else None
            current_website: Optional[str] = None
            current_use_case: Optional[str] = None
            if round_cache_key and status_info.requires_prompt:
                if round_cache_key not in meta_cache:
                    meta_cache[round_cache_key] = await self._latest_task_meta(round_cache_key)
                meta = meta_cache.get(round_cache_key)
                if meta:
                    if meta.get("prompt"):
                        current_task = meta.get("prompt") or current_task
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
                "stake": stake_value,  # Keep as float to preserve decimal values
                "emission": emission_value,
                "uptime": uptime,
                "completedTasks": int(completed_tasks),
                "validatorRoundId": validator_round_id,
                "roundNumber": round_number,
                "validatorUid": validator_uid,
            }

        # Cache the aggregated validators for 10 minutes in Redis (shared across all workers)
        redis_cache.set(aggregate_cache_key, aggregates, ttl=600)
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
            id=int(round_obj.round_number or _round_id_to_int(round_obj.validator_round_id)),
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
