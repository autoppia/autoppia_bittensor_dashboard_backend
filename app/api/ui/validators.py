from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    TaskORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
    ValidatorRoundSummaryORM,
    ValidatorRoundValidatorORM,
)
from app.db.session import get_session
from app.services.metagraph_service import MetagraphError, get_validator_data
from app.services.redis_cache import cache
from app.utils.images import resolve_validator_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/validators", tags=["validators"])

# Máximo de evaluaciones a procesar si se especifica un límite (para evitar timeouts si el cliente lo solicita)
# Límite máximo para poder obtener todas las evaluaciones disponibles
# 500k debería ser suficiente para cualquier validator (el máximo actual es ~95k)
MAX_EVALUATIONS_LIMIT = 500000
# Límite por defecto: usar el máximo para devolver todas las evaluaciones disponibles
DEFAULT_EVALUATIONS_LIMIT = 500000


@router.get("/{uid}/details")
@cache("validator_details", ttl=120)  # Cache 2 minutos como recomienda Codex
async def get_validator_details(
    uid: int,
    round: Optional[str] = Query(None, description="Filter by round (format: 'season/round', e.g., '1/1')"),
    website: Optional[str] = Query(None, description="Filter evaluations table by website (e.g., 'AutoCinema')"),
    useCase: Optional[str] = Query(None, description="Filter evaluations table by use case (e.g., 'SEARCH_FILM')"),
    limit: Optional[int] = Query(
        DEFAULT_EVALUATIONS_LIMIT, ge=1, le=MAX_EVALUATIONS_LIMIT, description=f"Limit number of evaluations to process. Default: {DEFAULT_EVALUATIONS_LIMIT}, Max: {MAX_EVALUATIONS_LIMIT}"
    ),
    session: AsyncSession = Depends(get_session),
):
    """
    Get detailed statistics for a validator, aggregated by web and use case.

    Args:
        uid: Validator UID
        round: Optional round filter in "season/round" format (e.g., "1/1"). If not provided, returns all rounds.
        website: Optional website filter for evaluations table (e.g., "AutoCinema", "AutoBooks")
        useCase: Optional use case filter for evaluations table (e.g., "SEARCH_FILM", "CONTACT_BOOK")
        limit: Optional limit on number of evaluations to process (max 500000)

    Returns:
    - Validator info (uid, hotkey, stake, weight, lastRoundEvaluated)
    - Global aggregated stats (totalEvaluations, counts for score=1, score=0, null, failed)
    - Context info (winner of last round, reward, weight)
    - Stats by web (with nested stats by use case)
    - Available rounds list for this validator
    """

    # Parse round parameter if provided
    season_filter = None
    round_filter = None
    if round is not None:
        try:
            parts = round.split("/")
            if len(parts) == 2:
                season_filter = int(parts[0])
                round_filter = int(parts[1])
        except (ValueError, AttributeError):
            pass  # Ignore invalid format

    # When filtering by round, reused runs may point to source runs from previous rounds.
    # Collect those source run IDs so the analytics can show inherited evaluations.
    filtered_round_ids: list[str] = []
    reused_source_run_ids: list[str] = []
    if season_filter is not None and round_filter is not None:
        filtered_round_ids_query = (
            select(ValidatorRoundORM.validator_round_id)
            .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
            .where(
                and_(
                    ValidatorRoundValidatorORM.validator_uid == uid,
                    ValidatorRoundORM.season_number == season_filter,
                    ValidatorRoundORM.round_number_in_season == round_filter,
                )
            )
        )
        filtered_round_ids_result = await session.execute(filtered_round_ids_query)
        filtered_round_ids = [str(rid) for (rid,) in filtered_round_ids_result.all() if rid]

        if filtered_round_ids:
            reused_source_run_ids = []

    # Get validator information from the most recent validator snapshot
    validator_snapshot_query = (
        select(ValidatorRoundValidatorORM)
        .join(ValidatorRoundORM, ValidatorRoundValidatorORM.validator_round_id == ValidatorRoundORM.validator_round_id)
        .where(ValidatorRoundValidatorORM.validator_uid == uid)
        .order_by(ValidatorRoundORM.season_number.desc(), ValidatorRoundORM.round_number_in_season.desc())
        .limit(1)
    )
    validator_snapshot_result = await session.execute(validator_snapshot_query)
    validator_snapshot = validator_snapshot_result.scalar_one_or_none()

    # Get stake from metagraph (preferred) or DB (fallback)
    # This ensures consistency with the overview/validators list endpoint
    # Stake is in RAO (not converted to TAO) to match overview endpoint
    validator_stake = None
    try:
        fresh_data = get_validator_data(uid=uid)
        if fresh_data and fresh_data.get("stake") is not None:
            validator_stake = float(fresh_data.get("stake") or 0.0)
            logger.debug(f"Validator {uid} stake from metagraph: {validator_stake:.2f} RAO")
    except MetagraphError:
        logger.debug(f"Metagraph data unavailable for validator {uid}, using DB fallback")

    # Fallback to DB if metagraph data not available
    # Note: DB stake might be in TAO, so we need to convert it to RAO for consistency
    if validator_stake is None:
        # Get the most recent stake from DB
        stake_query = (
            select(ValidatorRoundValidatorORM.stake)
            .join(ValidatorRoundORM, ValidatorRoundValidatorORM.validator_round_id == ValidatorRoundORM.validator_round_id)
            .where(ValidatorRoundValidatorORM.validator_uid == uid)
            .where(ValidatorRoundValidatorORM.stake.isnot(None))
            .order_by(ValidatorRoundORM.season_number.desc(), ValidatorRoundORM.round_number_in_season.desc())
            .limit(1)
        )
        stake_result = await session.execute(stake_query)
        recent_stake = stake_result.scalar_one_or_none()

        if recent_stake is not None:
            db_stake = float(recent_stake)
            # If DB stake is very small (< 1), assume it's in TAO and convert to RAO
            # Otherwise, assume it's already in RAO
            if db_stake < 1.0:
                validator_stake = db_stake * 1_000_000_000  # Convert TAO to RAO
                logger.debug(f"Validator {uid} stake from DB (converted TAO->RAO): {validator_stake:.2f} RAO")
            else:
                validator_stake = db_stake
                logger.debug(f"Validator {uid} stake from DB: {validator_stake:.2f} RAO")
        elif validator_snapshot and validator_snapshot.stake is not None:
            db_stake = float(validator_snapshot.stake)
            # If DB stake is very small (< 1), assume it's in TAO and convert to RAO
            if db_stake < 1.0:
                validator_stake = db_stake * 1_000_000_000  # Convert TAO to RAO
                logger.debug(f"Validator {uid} stake from snapshot (converted TAO->RAO): {validator_stake:.2f} RAO")
            else:
                validator_stake = db_stake
                logger.debug(f"Validator {uid} stake from snapshot: {validator_stake:.2f} RAO")

    # Log for debugging (can be removed in production)
    if validator_stake == 0:
        logger.debug(f"Validator {uid} stake is 0 (this may be correct if validator has no stake)")

    # Get available rounds for this validator (return as "season/round" strings)
    rounds_query = (
        select(ValidatorRoundORM.season_number, ValidatorRoundORM.round_number_in_season)
        .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
        .where(ValidatorRoundValidatorORM.validator_uid == uid)
        .distinct()
        .order_by(ValidatorRoundORM.season_number.desc(), ValidatorRoundORM.round_number_in_season.desc())
    )
    rounds_result = await session.execute(rounds_query)
    available_rounds = [f"{season}/{round_num}" for season, round_num in rounds_result.all() if season is not None and round_num is not None]

    # Agregación en SQL para evitar traer todas las evaluaciones a Python
    use_case_name_expr = func.coalesce(
        TaskORM.use_case["name"].astext,
        TaskORM.use_case["id"].astext,
        TaskORM.use_case["use_case"].astext,
        literal("unknown"),
    )
    use_case_id_expr = func.coalesce(
        TaskORM.use_case["id"].astext,
        TaskORM.use_case["name"].astext,
        TaskORM.use_case["use_case"].astext,
        literal("unknown"),
    )

    base_evaluations_query = (
        select(
            EvaluationORM.evaluation_score.label("evaluation_score"),
            EvaluationORM.task_id.label("task_id"),
            TaskORM.web_project_id.label("web_id"),
            TaskORM.web_version.label("web_version"),
            use_case_name_expr.label("use_case_name"),
            use_case_id_expr.label("use_case_id"),
            func.substr(TaskORM.prompt, 1, 120).label("task_prompt"),
        )
        .join(TaskORM, EvaluationORM.task_id == TaskORM.task_id)
        .where(EvaluationORM.validator_uid == uid)
    )

    # Aplicar filtros opcionales
    if website is not None:
        base_evaluations_query = base_evaluations_query.where(TaskORM.web_project_id == website)

    # Filtro de useCase en SQL (JSONB -> 'name')
    if useCase is not None:
        use_case_normalized = useCase.upper().replace(" ", "_")
        base_evaluations_query = base_evaluations_query.where(func.upper(func.replace(TaskORM.use_case["name"].astext, " ", "_")) == use_case_normalized)

    # Filtro por round (including reused runs source data when applicable)
    if season_filter is not None and round_filter is not None:
        round_predicates = []
        if filtered_round_ids:
            round_predicates.append(EvaluationORM.validator_round_id.in_(filtered_round_ids))
        if reused_source_run_ids:
            round_predicates.append(EvaluationORM.agent_run_id.in_(reused_source_run_ids))

        if round_predicates:
            base_evaluations_query = base_evaluations_query.where(or_(*round_predicates))
        else:
            # No rounds found for this validator/selector -> force empty result
            base_evaluations_query = base_evaluations_query.where(literal(False))

    # Aplicar límite configurable (por defecto 500k)
    base_evaluations_query = base_evaluations_query.limit(limit)

    evaluations_subquery = base_evaluations_query.subquery()

    # Agregados globales
    global_counts_stmt = select(
        func.count().label("total"),
        func.count().filter(evaluations_subquery.c.evaluation_score >= 0.5).label("success"),
        func.count().filter(and_(evaluations_subquery.c.evaluation_score < 0.5, evaluations_subquery.c.evaluation_score.isnot(None))).label("zero"),
        func.count().filter(evaluations_subquery.c.evaluation_score.is_(None)).label("null_count"),
    ).select_from(evaluations_subquery)
    global_counts_row = (await session.execute(global_counts_stmt)).one()
    total_evaluations_processed = int(global_counts_row.total or 0)

    # Agregados por web/useCase/task
    grouped_stmt = (
        select(
            evaluations_subquery.c.web_id,
            evaluations_subquery.c.web_version,
            evaluations_subquery.c.use_case_name,
            evaluations_subquery.c.use_case_id,
            evaluations_subquery.c.task_id,
            func.max(evaluations_subquery.c.task_prompt).label("task_prompt"),
            func.count().label("total"),
            func.count().filter(evaluations_subquery.c.evaluation_score >= 0.5).label("success"),
            func.count().filter(and_(evaluations_subquery.c.evaluation_score < 0.5, evaluations_subquery.c.evaluation_score.isnot(None))).label("zero"),
            func.count().filter(evaluations_subquery.c.evaluation_score.is_(None)).label("null_count"),
        )
        .select_from(evaluations_subquery)
        .group_by(
            evaluations_subquery.c.web_id,
            evaluations_subquery.c.web_version,
            evaluations_subquery.c.use_case_name,
            evaluations_subquery.c.use_case_id,
            evaluations_subquery.c.task_id,
        )
    )
    grouped_rows = (await session.execute(grouped_stmt)).all()

    # Get last round for this validator (needed even if no evaluations)
    last_round_query = (
        select(ValidatorRoundORM)
        .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
        .where(ValidatorRoundValidatorORM.validator_uid == uid)
        .order_by(ValidatorRoundORM.season_number.desc(), ValidatorRoundORM.round_number_in_season.desc())
        .limit(1)
    )
    last_round_result = await session.execute(last_round_query)
    last_round = last_round_result.scalar_one_or_none()
    last_round_number = (
        f"{last_round.season_number}/{last_round.round_number_in_season}" if last_round and last_round.season_number is not None and last_round.round_number_in_season is not None else None
    )

    # Determine which round to use for winner lookup
    # If round filter is provided, use that round; otherwise use last FINISHED round
    target_round = None
    if season_filter is not None and round_filter is not None:
        # Get the specific round requested
        target_round_query = (
            select(ValidatorRoundORM)
            .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
            .where(and_(ValidatorRoundValidatorORM.validator_uid == uid, ValidatorRoundORM.season_number == season_filter, ValidatorRoundORM.round_number_in_season == round_filter))
            .limit(1)
        )
        target_round_result = await session.execute(target_round_query)
        target_round = target_round_result.scalar_one_or_none()
    else:
        # Use last FINISHED round if no filter (so we can get weight from ValidatorRoundSummaryORM)
        # A finished round has ended_at not null and status = 'finished'
        last_finished_round_query = (
            select(ValidatorRoundORM)
            .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
            .where(and_(ValidatorRoundValidatorORM.validator_uid == uid, ValidatorRoundORM.ended_at.isnot(None), ValidatorRoundORM.status == "finished"))
            .order_by(ValidatorRoundORM.season_number.desc(), ValidatorRoundORM.round_number_in_season.desc())
            .limit(1)
        )
        last_finished_round_result = await session.execute(last_finished_round_query)
        target_round = last_finished_round_result.scalar_one_or_none()

    # Get winner of target round for this validator
    last_round_winner = None
    last_round_winner_reward = None
    last_round_winner_weight = None
    last_round_winner_name = None
    last_round_winner_image = None
    last_round_winner_hotkey = None

    if target_round:
        # Get the winner from ValidatorRoundSummaryORM for this validator's target round (rondas finalizadas)
        winner_query = (
            select(ValidatorRoundSummaryORM)
            .join(ValidatorRoundORM, ValidatorRoundSummaryORM.validator_round_id == ValidatorRoundORM.validator_round_id)
            .where(
                and_(
                    ValidatorRoundORM.validator_round_id == target_round.validator_round_id,
                    ValidatorRoundSummaryORM.post_consensus_rank == 1,
                    ValidatorRoundSummaryORM.miner_uid != settings.BURN_UID,
                )
            )
            .limit(1)
        )
        winner_result = await session.execute(winner_query)
        winner = winner_result.scalar_one_or_none()

        if winner:
            last_round_winner = winner.miner_uid
            last_round_winner_reward = winner.post_consensus_avg_reward
            last_round_winner_weight = winner.weight
            last_round_winner_hotkey = winner.miner_hotkey

            # Get miner snapshot for name and image
            miner_snapshot_query = (
                select(ValidatorRoundMinerORM).where(and_(ValidatorRoundMinerORM.validator_round_id == target_round.validator_round_id, ValidatorRoundMinerORM.miner_uid == winner.miner_uid)).limit(1)
            )
            miner_snapshot_result = await session.execute(miner_snapshot_query)
            miner_snapshot = miner_snapshot_result.scalar_one_or_none()

            if miner_snapshot:
                last_round_winner_name = miner_snapshot.name
                last_round_winner_image = miner_snapshot.image_url
        else:
            # Si no hay datos en ValidatorRoundSummaryORM (ronda activa),
            # buscar el top miner en AgentEvaluationRunORM por average_reward
            top_run_query = (
                select(AgentEvaluationRunORM)
                .where(
                    and_(
                        AgentEvaluationRunORM.validator_round_id == target_round.validator_round_id,
                        AgentEvaluationRunORM.miner_uid != settings.BURN_UID,
                    )
                )
                .order_by(AgentEvaluationRunORM.average_reward.desc())
                .limit(1)
            )
            top_run_result = await session.execute(top_run_query)
            top_run = top_run_result.scalar_one_or_none()

            if top_run:
                last_round_winner = top_run.miner_uid
                last_round_winner_reward = top_run.average_reward
                last_round_winner_weight = None  # Weight no disponible para rondas activas
                last_round_winner_hotkey = top_run.miner_hotkey

                # Get miner snapshot for name and image
                miner_snapshot_query = (
                    select(ValidatorRoundMinerORM)
                    .where(and_(ValidatorRoundMinerORM.validator_round_id == target_round.validator_round_id, ValidatorRoundMinerORM.miner_uid == top_run.miner_uid))
                    .limit(1)
                )
                miner_snapshot_result = await session.execute(miner_snapshot_query)
                miner_snapshot = miner_snapshot_result.scalar_one_or_none()

                if miner_snapshot:
                    last_round_winner_name = miner_snapshot.name
                    last_round_winner_image = miner_snapshot.image_url

    # Get miners data for the target round
    miners_list = []
    unique_miners_count = 0
    if target_round:
        # Get miners from ValidatorRoundSummaryORM (post-consensus data)
        # This includes ALL miners that participated, even those with 0 reward
        miners_query = (
            select(ValidatorRoundSummaryORM)
            .where(
                and_(
                    ValidatorRoundSummaryORM.validator_round_id == target_round.validator_round_id,
                    ValidatorRoundSummaryORM.miner_uid != settings.BURN_UID,
                )
            )
            .order_by(ValidatorRoundSummaryORM.post_consensus_rank.asc().nulls_last(), ValidatorRoundSummaryORM.post_consensus_avg_reward.desc().nulls_last())
        )
        miners_result = await session.execute(miners_query)
        miners_summaries = miners_result.scalars().all()

        # Get unique miner UIDs
        unique_miner_uids = set()
        for summary in miners_summaries:
            if summary.miner_uid is not None and summary.miner_uid != settings.BURN_UID:
                unique_miner_uids.add(summary.miner_uid)

        unique_miners_count = len(unique_miner_uids)

        # Get miner snapshots for names and images
        if unique_miner_uids:
            miner_snapshots_query = select(ValidatorRoundMinerORM).where(
                and_(ValidatorRoundMinerORM.validator_round_id == target_round.validator_round_id, ValidatorRoundMinerORM.miner_uid.in_(unique_miner_uids))
            )
            miner_snapshots_result = await session.execute(miner_snapshots_query)
            miner_snapshots = miner_snapshots_result.scalars().all()

            # Create a map of miner_uid -> snapshot
            miner_snapshot_map = {snapshot.miner_uid: snapshot for snapshot in miner_snapshots}

            # Build miners list with post-consensus scores
            for summary in miners_summaries:
                if summary.miner_uid is None or summary.miner_uid == settings.BURN_UID:
                    continue

                snapshot = miner_snapshot_map.get(summary.miner_uid)

                # Use post-consensus data (includes miners with 0 reward)
                reward = float(summary.post_consensus_avg_reward) if summary.post_consensus_avg_reward is not None else 0.0
                evaluation_score = float(summary.post_consensus_avg_eval_score) if summary.post_consensus_avg_eval_score is not None else 0.0
                eval_time = float(summary.post_consensus_avg_eval_time) if summary.post_consensus_avg_eval_time is not None else 0.0
                tasks_completed = summary.post_consensus_tasks_success or 0
                tasks_total = summary.post_consensus_tasks_received or 0

                miner_data = {
                    "uid": summary.miner_uid,
                    "name": snapshot.name if snapshot else None,
                    "image": snapshot.image_url if snapshot else None,
                    "hotkey": summary.miner_hotkey or (snapshot.miner_hotkey if snapshot else None),
                    "score": reward,
                    "reward": reward * 100,  # As percentage
                    "evalScore": evaluation_score * 100,  # As percentage
                    "evalTime": eval_time,  # In seconds
                    "tasksCompleted": tasks_completed,
                    "tasksTotal": tasks_total,
                }
                miners_list.append(miner_data)

            # Already sorted by rank and reward in the query

    if total_evaluations_processed == 0:
        # Return empty response if no evaluations found
        return JSONResponse(
            content={
                "success": True,
                "data": {
                    "validator": {
                        "uid": uid,
                        "hotkey": validator_snapshot.validator_hotkey if validator_snapshot else None,
                        "stake": validator_stake,
                        "weight": None,  # Weight is per-round, not global
                        "lastRoundEvaluated": last_round_number,
                    },
                    "globalStats": {
                        "totalEvaluations": 0,
                        "successCount": 0,
                        "zeroCount": 0,
                        "nullCount": 0,
                        "failedCount": 0,
                        "successPct": 0.0,
                        "zeroPct": 0.0,
                        "nullPct": 0.0,
                        "failedPct": 0.0,
                    },
                    "context": {
                        "lastRoundWinner": last_round_winner,
                        "lastRoundWinnerReward": float(last_round_winner_reward) if last_round_winner_reward is not None else None,
                        "lastRoundWinnerWeight": float(last_round_winner_weight) if last_round_winner_weight is not None else None,
                        "lastRoundWinnerName": last_round_winner_name,
                        "lastRoundWinnerImage": last_round_winner_image,
                        "lastRoundWinnerHotkey": last_round_winner_hotkey,
                    },
                    "validatorImage": resolve_validator_image(validator_snapshot.name if validator_snapshot else None, existing=validator_snapshot.image_url if validator_snapshot else None),
                    "webs": [],
                    "availableRounds": available_rounds,
                    "roundDetails": {
                        "minersParticipated": unique_miners_count,
                        "miners": miners_list,
                    },
                    "totalEvaluationsProcessed": 0,
                    "hasMore": False,
                },
            },
            headers={"Cache-Control": "public, max-age=60"},
        )

    # Aggregate statistics using SQL results
    global_stats = {
        "totalEvaluations": total_evaluations_processed,
        "successCount": int(global_counts_row.success or 0),
        "zeroCount": int(global_counts_row.zero or 0),
        "nullCount": int(global_counts_row.null_count or 0),
        "failedCount": 0,
    }

    # Group by web and use_case from grouped rows
    web_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "webName": None,
            "webId": None,
            "webVersion": None,
            "totalEvaluations": 0,
            "successCount": 0,
            "zeroCount": 0,
            "nullCount": 0,
            "failedCount": 0,
            "useCases": defaultdict(
                lambda: {
                    "useCaseName": None,
                    "useCaseId": None,
                    "totalEvaluations": 0,
                    "successCount": 0,
                    "zeroCount": 0,
                    "nullCount": 0,
                    "failedCount": 0,
                    "tasks": [],
                }
            ),
        }
    )

    for row in grouped_rows:
        web_id = row.web_id or "unknown"
        web_version = row.web_version
        use_case_id = row.use_case_id or "unknown"
        use_case_name = row.use_case_name or use_case_id

        total = int(row.total or 0)
        success = int(row.success or 0)
        zero = int(row.zero or 0)
        null_count = int(row.null_count or 0)
        failed = 0  # failedCount remains for schema compatibility

        web_entry = web_stats[web_id]
        if web_entry["webId"] is None:
            web_entry["webId"] = web_id
            web_entry["webName"] = web_id
            web_entry["webVersion"] = web_version

        web_entry["totalEvaluations"] += total
        web_entry["successCount"] += success
        web_entry["zeroCount"] += zero
        web_entry["nullCount"] += null_count

        use_case_key = f"{web_id}:{use_case_id}"
        use_case_stats = web_entry["useCases"][use_case_key]
        if use_case_stats["useCaseId"] is None:
            use_case_stats["useCaseId"] = use_case_id
            use_case_stats["useCaseName"] = use_case_name

        use_case_stats["totalEvaluations"] += total
        use_case_stats["successCount"] += success
        use_case_stats["zeroCount"] += zero
        use_case_stats["nullCount"] += null_count

        task_total = total
        task_success = success
        task_zero = zero
        task_null = null_count
        task_failed = failed

        use_case_stats["tasks"].append(
            {
                "taskId": row.task_id,
                "taskPrompt": row.task_prompt or "",
                "totalEvaluations": task_total,
                "successCount": task_success,
                "zeroCount": task_zero,
                "nullCount": task_null,
                "failedCount": task_failed,
                "successPct": (task_success / task_total * 100) if task_total > 0 else 0.0,
                "zeroPct": (task_zero / task_total * 100) if task_total > 0 else 0.0,
                "nullPct": (task_null / task_total * 100) if task_total > 0 else 0.0,
                "failedPct": (task_failed / task_total * 100) if task_total > 0 else 0.0,
            }
        )

    # Calculate percentages for global stats
    total = global_stats["totalEvaluations"]
    global_stats["successPct"] = (global_stats["successCount"] / total * 100) if total > 0 else 0.0
    global_stats["zeroPct"] = (global_stats["zeroCount"] / total * 100) if total > 0 else 0.0
    global_stats["nullPct"] = (global_stats["nullCount"] / total * 100) if total > 0 else 0.0
    global_stats["failedPct"] = (global_stats["failedCount"] / total * 100) if total > 0 else 0.0

    # Calculate percentages for web and use case stats, and convert to list format
    webs_list = []
    for web_id, web_data in web_stats.items():
        web_total = web_data["totalEvaluations"]
        web_data["successPct"] = (web_data["successCount"] / web_total * 100) if web_total > 0 else 0.0
        web_data["zeroPct"] = (web_data["zeroCount"] / web_total * 100) if web_total > 0 else 0.0
        web_data["nullPct"] = (web_data["nullCount"] / web_total * 100) if web_total > 0 else 0.0
        web_data["failedPct"] = (web_data["failedCount"] / web_total * 100) if web_total > 0 else 0.0

        use_cases_list = []
        for use_case_key, use_case_data in web_data["useCases"].items():
            uc_total = use_case_data["totalEvaluations"]
            use_case_data["successPct"] = (use_case_data["successCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["zeroPct"] = (use_case_data["zeroCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["nullPct"] = (use_case_data["nullCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["failedPct"] = (use_case_data["failedCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["tasks"].sort(key=lambda t: t.get("successCount", 0), reverse=True)
            use_cases_list.append(use_case_data)

        use_cases_list.sort(key=lambda uc: uc.get("successCount", 0), reverse=True)
        web_data["useCases"] = use_cases_list
        webs_list.append(web_data)

    # Sort webs by specific order
    WEB_ORDER = {
        "autocinema": 1,
        "autobooks": 2,
        "autozone": 3,
        "autodining": 4,
        "autocrm": 5,
        "automail": 6,
        "autodelivery": 7,
        "autolodge": 8,
        "autoconnect": 9,
        "autowork": 10,
        "autocalendar": 11,
        "autolist": 12,
        "autodrive": 13,
        "autohealth": 14,
    }

    def get_web_order(web_id: str) -> int:
        """Get order for web_id based on web name"""
        web_id_lower = web_id.lower()
        for web_name, order in WEB_ORDER.items():
            if web_name in web_id_lower:
                return order
        return 999

    webs_list.sort(key=lambda w: get_web_order(w.get("webId", "")))

    # Contar total de evaluaciones procesadas vs total disponible
    has_more = limit is not None and total_evaluations_processed >= limit

    # Build response
    response_data = {
        "success": True,
        "data": {
            "validator": {
                "uid": uid,
                "hotkey": validator_snapshot.validator_hotkey if validator_snapshot else None,
                "stake": validator_stake,
                "weight": None,  # Weight is per-round, not global
                "lastRoundEvaluated": last_round_number,
            },
            "globalStats": global_stats,
            "context": {
                "lastRoundWinner": last_round_winner,
                "lastRoundWinnerReward": float(last_round_winner_reward) if last_round_winner_reward is not None else None,
                "lastRoundWinnerWeight": float(last_round_winner_weight) if last_round_winner_weight is not None else None,
                "lastRoundWinnerName": last_round_winner_name,
                "lastRoundWinnerImage": last_round_winner_image,
                "lastRoundWinnerHotkey": last_round_winner_hotkey,
            },
            "validatorImage": resolve_validator_image(validator_snapshot.name if validator_snapshot else None, existing=validator_snapshot.image_url if validator_snapshot else None),
            "webs": webs_list,
            "availableRounds": available_rounds,
            "roundDetails": {
                "minersParticipated": unique_miners_count,
                "miners": miners_list,
            },
            "totalEvaluationsProcessed": total_evaluations_processed,
            "hasMore": has_more,
        },
    }

    # Devolver JSONResponse con headers de caché HTTP
    return JSONResponse(
        content=response_data,
        headers={"Cache-Control": "public, max-age=60"},  # Cache HTTP por 60 segundos
    )
