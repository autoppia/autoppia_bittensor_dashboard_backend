from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, load_only

from app.db.session import get_session
from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    TaskORM,
    ValidatorRoundORM,
    ValidatorRoundValidatorORM,
    ValidatorRoundSummaryORM,
    ValidatorRoundMinerORM,
)
from app.services.redis_cache import cache
from app.utils.images import resolve_validator_image
from app.services.metagraph_service import get_validator_data, MetagraphError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/validators", tags=["validators"])

# Máximo de evaluaciones a procesar si se especifica un límite (para evitar timeouts si el cliente lo solicita)
MAX_EVALUATIONS_LIMIT = 50000
# Límite por defecto para evitar cargar 213k+ evaluaciones sin límite
DEFAULT_EVALUATIONS_LIMIT = 10000


@router.get("/{uid}/details")
@cache("validator_details", ttl=120)  # Cache 2 minutos como recomienda Codex
async def get_validator_details(
    uid: int,
    round: Optional[int] = Query(None, description="Filter by round number"),
    website: Optional[str] = Query(None, description="Filter evaluations table by website (e.g., 'AutoCinema')"),
    useCase: Optional[str] = Query(None, description="Filter evaluations table by use case (e.g., 'SEARCH_FILM')"),
    limit: Optional[int] = Query(DEFAULT_EVALUATIONS_LIMIT, ge=1, le=MAX_EVALUATIONS_LIMIT, description="Limit number of evaluations to process. Default: 10000 (to prevent performance issues with 213k+ evaluations)"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get detailed statistics for a validator, aggregated by web and use case.
    
    Args:
        uid: Validator UID
        round: Optional round number to filter evaluations. If not provided, returns all rounds.
        website: Optional website filter for evaluations table (e.g., "AutoCinema", "AutoBooks")
        useCase: Optional use case filter for evaluations table (e.g., "SEARCH_FILM", "CONTACT_BOOK")
        limit: Optional limit on number of evaluations to process (max 10000)
    
    Returns:
    - Validator info (uid, hotkey, stake, weight, lastRoundEvaluated)
    - Global aggregated stats (totalEvaluations, counts for score=1, score=0, null, failed)
    - Context info (winner of last round, reward, weight)
    - Stats by web (with nested stats by use case)
    - Available rounds list for this validator
    """
    
    # Get validator information from the most recent validator snapshot
    validator_snapshot_query = (
        select(ValidatorRoundValidatorORM)
        .where(ValidatorRoundValidatorORM.validator_uid == uid)
        .order_by(ValidatorRoundValidatorORM.round_number.desc())
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
            .where(ValidatorRoundValidatorORM.validator_uid == uid)
            .where(ValidatorRoundValidatorORM.stake.isnot(None))
            .order_by(ValidatorRoundValidatorORM.round_number.desc())
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
    
    # Get available rounds for this validator
    rounds_query = (
        select(ValidatorRoundORM.round_number)
        .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
        .where(ValidatorRoundValidatorORM.validator_uid == uid)
        .distinct()
        .order_by(ValidatorRoundORM.round_number.desc())
    )
    rounds_result = await session.execute(rounds_query)
    available_rounds = [r[0] for r in rounds_result.all()]
    
    # Optimización: Usar load_only para traer solo columnas necesarias
    # Solo necesitamos: eval_score, meta, feedback, task_id de EvaluationORM
    # Y task_id, prompt, web_project_id, use_case de TaskORM
    evaluations_query = (
        select(EvaluationORM)
        .join(TaskORM, EvaluationORM.task_id == TaskORM.task_id)
        .where(EvaluationORM.validator_uid == uid)
        .options(
            load_only(
                EvaluationORM.eval_score,
                EvaluationORM.meta,
                EvaluationORM.feedback,
                EvaluationORM.task_id,
                EvaluationORM.validator_round_id,
            ),
            selectinload(EvaluationORM.task).load_only(
                TaskORM.task_id,
                TaskORM.prompt,
                TaskORM.web_project_id,
                TaskORM.web_version,
                TaskORM.use_case,
            )
        )
    )
    
    # Aplicar filtros opcionales
    if website is not None:
        evaluations_query = evaluations_query.where(TaskORM.web_project_id == website)
    
    # Filtro de useCase: ahora en SQL para mejor rendimiento
    # use_case es JSONB, usamos el operador ->> para extraer el campo 'name'
    if useCase is not None:
        # Normalizar useCase: manejar tanto "SEARCH_FILM" como "SEARCH FILM"
        use_case_normalized = useCase.upper().replace(" ", "_")
        evaluations_query = evaluations_query.where(
            func.upper(func.replace(TaskORM.use_case["name"].astext, " ", "_")) == use_case_normalized
        )
    
    # If round filter is provided, join with ValidatorRoundORM to filter by round
    if round is not None:
        evaluations_query = (
            evaluations_query
            .join(ValidatorRoundORM, EvaluationORM.validator_round_id == ValidatorRoundORM.validator_round_id)
            .where(ValidatorRoundORM.round_number == round)
        )
    
    # SIEMPRE aplicar límite (ahora tiene default de 10000 para evitar cargar 213k registros)
    evaluations_query = evaluations_query.limit(limit)
    
    evaluations_result = await session.execute(evaluations_query)
    evaluations = evaluations_result.scalars().all()
    
    # Get last round number for this validator (needed even if no evaluations)
    last_round_query = (
        select(ValidatorRoundORM)
        .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
        .where(ValidatorRoundValidatorORM.validator_uid == uid)
        .order_by(ValidatorRoundORM.round_number.desc())
        .limit(1)
    )
    last_round_result = await session.execute(last_round_query)
    last_round = last_round_result.scalar_one_or_none()
    last_round_number = last_round.round_number if last_round else None
    
    # Determine which round to use for winner lookup
    # If round filter is provided, use that round; otherwise use last round
    target_round = None
    if round is not None:
        # Get the specific round requested
        target_round_query = (
            select(ValidatorRoundORM)
            .join(ValidatorRoundValidatorORM, ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id)
            .where(
                and_(
                    ValidatorRoundValidatorORM.validator_uid == uid,
                    ValidatorRoundORM.round_number == round
                )
            )
            .limit(1)
        )
        target_round_result = await session.execute(target_round_query)
        target_round = target_round_result.scalar_one_or_none()
    else:
        # Use last round if no filter
        target_round = last_round
    
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
                    ValidatorRoundSummaryORM.post_consensus_rank == 1
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
                select(ValidatorRoundMinerORM)
                .where(
                    and_(
                        ValidatorRoundMinerORM.validator_round_id == target_round.validator_round_id,
                        ValidatorRoundMinerORM.miner_uid == winner.miner_uid
                    )
                )
                .limit(1)
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
                .where(AgentEvaluationRunORM.validator_round_id == target_round.validator_round_id)
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
                    .where(
                        and_(
                            ValidatorRoundMinerORM.validator_round_id == target_round.validator_round_id,
                            ValidatorRoundMinerORM.miner_uid == top_run.miner_uid
                        )
                    )
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
            .where(ValidatorRoundSummaryORM.validator_round_id == target_round.validator_round_id)
            .order_by(
                ValidatorRoundSummaryORM.post_consensus_rank.asc().nulls_last(),
                ValidatorRoundSummaryORM.post_consensus_avg_reward.desc().nulls_last()
            )
        )
        miners_result = await session.execute(miners_query)
        miners_summaries = miners_result.scalars().all()
        
        # Get unique miner UIDs
        unique_miner_uids = set()
        for summary in miners_summaries:
            if summary.miner_uid is not None:
                unique_miner_uids.add(summary.miner_uid)
        
        unique_miners_count = len(unique_miner_uids)
        
        # Get miner snapshots for names and images
        if unique_miner_uids:
            miner_snapshots_query = (
                select(ValidatorRoundMinerORM)
                .where(
                    and_(
                        ValidatorRoundMinerORM.validator_round_id == target_round.validator_round_id,
                        ValidatorRoundMinerORM.miner_uid.in_(unique_miner_uids)
                    )
                )
            )
            miner_snapshots_result = await session.execute(miner_snapshots_query)
            miner_snapshots = miner_snapshots_result.scalars().all()
            
            # Create a map of miner_uid -> snapshot
            miner_snapshot_map = {snapshot.miner_uid: snapshot for snapshot in miner_snapshots}
            
            # Build miners list with post-consensus scores
            for summary in miners_summaries:
                if summary.miner_uid is None:
                    continue
                
                snapshot = miner_snapshot_map.get(summary.miner_uid)
                
                # Use post-consensus data (includes miners with 0 reward)
                reward = float(summary.post_consensus_avg_reward) if summary.post_consensus_avg_reward is not None else 0.0
                eval_score = float(summary.post_consensus_avg_eval_score) if summary.post_consensus_avg_eval_score is not None else 0.0
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
                    "evalScore": eval_score * 100,  # As percentage
                    "evalTime": eval_time,  # In seconds
                    "tasksCompleted": tasks_completed,
                    "tasksTotal": tasks_total,
                }
                miners_list.append(miner_data)
            
            # Already sorted by rank and reward in the query
    
    if not evaluations:
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
                        "lastRoundWinnerReward": float(last_round_winner_reward) if last_round_winner_reward else None,
                        "lastRoundWinnerWeight": float(last_round_winner_weight) if last_round_winner_weight else None,
                        "lastRoundWinnerName": last_round_winner_name,
                        "lastRoundWinnerImage": last_round_winner_image,
                        "lastRoundWinnerHotkey": last_round_winner_hotkey,
                    },
                    "validatorImage": resolve_validator_image(
                        validator_snapshot.name if validator_snapshot else None,
                        existing=validator_snapshot.image_url if validator_snapshot else None
                    ),
                    "webs": [],
                    "availableRounds": available_rounds,
                    "roundDetails": {
                        "minersParticipated": unique_miners_count,
                        "miners": miners_list,
                    },
                    "totalEvaluationsProcessed": 0,
                    "hasMore": False,
                }
            },
            headers={"Cache-Control": "public, max-age=60"}
        )
    
    # Aggregate statistics
    global_stats = {
        "totalEvaluations": 0,
        "successCount": 0,
        "zeroCount": 0,
        "nullCount": 0,
        "failedCount": 0,
    }
    
    # Group by web and use_case
    web_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "webName": None,
        "webId": None,
        "webVersion": None,
        "totalEvaluations": 0,
        "successCount": 0,
        "zeroCount": 0,
        "nullCount": 0,
        "failedCount": 0,
        "useCases": defaultdict(lambda: {
            "useCaseName": None,
            "useCaseId": None,
            "totalEvaluations": 0,
            "successCount": 0,
            "zeroCount": 0,
            "nullCount": 0,
            "failedCount": 0,
            "tasks": [],  # List of individual tasks
        }),
    })
    
    
    # Process each evaluation
    for eval in evaluations:
        task = eval.task
        if not task:
            continue
        
        # Extract web and use_case
        web_id = task.web_project_id or "unknown"
        
        # Nota: El filtro de useCase ya se aplicó en SQL (línea 167-172)
        # No necesitamos filtrar nuevamente en Python
        
        use_case_dict = task.use_case or {}
        # Use case can be a dict with 'name' or 'id', or a string
        use_case_name = "unknown"
        use_case_id = "unknown"
        if isinstance(use_case_dict, dict):
            # Try to get name or id from dict
            use_case_name = use_case_dict.get("name") or use_case_dict.get("id") or use_case_dict.get("use_case") or "unknown"
            use_case_id = use_case_dict.get("id") or use_case_dict.get("name") or use_case_name
            # If still unknown, try to get first non-empty string value
            if use_case_id == "unknown":
                for key, value in use_case_dict.items():
                    if isinstance(value, str) and value:
                        use_case_id = value
                        use_case_name = value
                        break
        elif isinstance(use_case_dict, str):
            use_case_name = use_case_dict
            use_case_id = use_case_dict
        else:
            use_case_name = str(use_case_dict) if use_case_dict else "unknown"
            use_case_id = use_case_name
        
        # Initialize web stats if needed
        if web_id not in web_stats:
            web_stats[web_id]["webId"] = web_id
            web_stats[web_id]["webName"] = web_id  # Use web_id as name if no better name available
            web_stats[web_id]["webVersion"] = task.web_version  # Add web version from task
        
        # Initialize use case stats if needed
        use_case_key = f"{web_id}:{use_case_id}"
        if use_case_key not in web_stats[web_id]["useCases"]:
            web_stats[web_id]["useCases"][use_case_key]["useCaseId"] = use_case_id
            web_stats[web_id]["useCases"][use_case_key]["useCaseName"] = use_case_name
            web_stats[web_id]["useCases"][use_case_key]["tasks"] = []
        
        # Count evaluation
        eval_score = eval.eval_score
        is_failed = False
        
        # Check if evaluation failed (check meta, feedback, or error fields)
        meta = eval.meta or {}
        feedback = eval.feedback or {}
        if isinstance(meta, dict):
            # Check for error indicators in meta
            if any(key in meta for key in ["error", "error_message", "exception", "status"]):
                error_value = meta.get("error") or meta.get("error_message") or meta.get("exception")
                status_value = meta.get("status")
                if error_value or (status_value and str(status_value).lower() not in ["ok", "success", "completed"]):
                    is_failed = True
        if isinstance(feedback, dict):
            if any(key in feedback for key in ["error", "error_message", "exception"]):
                is_failed = True
        
        # Update global stats
        global_stats["totalEvaluations"] += 1
        if is_failed:
            global_stats["failedCount"] += 1
        elif eval_score is None:
            global_stats["nullCount"] += 1
        elif eval_score == 1.0:
            global_stats["successCount"] += 1
        elif eval_score == 0.0:
            global_stats["zeroCount"] += 1
        else:
            # Score between 0 and 1, count as success if >= 0.5, otherwise zero
            if eval_score >= 0.5:
                global_stats["successCount"] += 1
            else:
                global_stats["zeroCount"] += 1
        
        # Update web stats
        web_stats[web_id]["totalEvaluations"] += 1
        if is_failed:
            web_stats[web_id]["failedCount"] += 1
        elif eval_score is None:
            web_stats[web_id]["nullCount"] += 1
        elif eval_score == 1.0:
            web_stats[web_id]["successCount"] += 1
        elif eval_score == 0.0:
            web_stats[web_id]["zeroCount"] += 1
        else:
            if eval_score >= 0.5:
                web_stats[web_id]["successCount"] += 1
            else:
                web_stats[web_id]["zeroCount"] += 1
        
        # Update use case stats
        use_case_stats = web_stats[web_id]["useCases"][use_case_key]
        use_case_stats["totalEvaluations"] += 1
        if is_failed:
            use_case_stats["failedCount"] += 1
        elif eval_score is None:
            use_case_stats["nullCount"] += 1
        elif eval_score == 1.0:
            use_case_stats["successCount"] += 1
        elif eval_score == 0.0:
            use_case_stats["zeroCount"] += 1
        else:
            if eval_score >= 0.5:
                use_case_stats["successCount"] += 1
            else:
                use_case_stats["zeroCount"] += 1
        
        # Add task to use case tasks list
        task_status = "failed" if is_failed else ("null" if eval_score is None else ("success" if (eval_score == 1.0 or (eval_score is not None and eval_score >= 0.5)) else "zero"))
        
        # Check if task already exists in the list (same task_id)
        task_exists = False
        for existing_task in use_case_stats["tasks"]:
            if existing_task.get("taskId") == task.task_id:
                # Update existing task stats
                existing_task["totalEvaluations"] += 1
                if task_status == "success":
                    existing_task["successCount"] += 1
                elif task_status == "zero":
                    existing_task["zeroCount"] += 1
                elif task_status == "null":
                    existing_task["nullCount"] += 1
                elif task_status == "failed":
                    existing_task["failedCount"] += 1
                # Update percentages
                total = existing_task["totalEvaluations"]
                existing_task["successPct"] = (existing_task["successCount"] / total * 100) if total > 0 else 0.0
                existing_task["zeroPct"] = (existing_task["zeroCount"] / total * 100) if total > 0 else 0.0
                existing_task["nullPct"] = (existing_task["nullCount"] / total * 100) if total > 0 else 0.0
                existing_task["failedPct"] = (existing_task["failedCount"] / total * 100) if total > 0 else 0.0
                task_exists = True
                break
        
        if not task_exists:
            # Add new task
            task_total = 1
            task_success = 1 if task_status == "success" else 0
            task_zero = 1 if task_status == "zero" else 0
            task_null = 1 if task_status == "null" else 0
            task_failed = 1 if task_status == "failed" else 0
            
            use_case_stats["tasks"].append({
                "taskId": task.task_id,
                "taskPrompt": task.prompt[:100] if task.prompt else "",  # Truncate prompt
                "totalEvaluations": task_total,
                "successCount": task_success,
                "zeroCount": task_zero,
                "nullCount": task_null,
                "failedCount": task_failed,
                "successPct": (task_success / task_total * 100) if task_total > 0 else 0.0,
                "zeroPct": (task_zero / task_total * 100) if task_total > 0 else 0.0,
                "nullPct": (task_null / task_total * 100) if task_total > 0 else 0.0,
                "failedPct": (task_failed / task_total * 100) if task_total > 0 else 0.0,
            })
    
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
        
        # Convert use cases to list
        use_cases_list = []
        for use_case_key, use_case_data in web_data["useCases"].items():
            uc_total = use_case_data["totalEvaluations"]
            use_case_data["successPct"] = (use_case_data["successCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["zeroPct"] = (use_case_data["zeroCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["nullPct"] = (use_case_data["nullCount"] / uc_total * 100) if uc_total > 0 else 0.0
            use_case_data["failedPct"] = (use_case_data["failedCount"] / uc_total * 100) if uc_total > 0 else 0.0
            # Ensure tasks is a list (not defaultdict)
            if "tasks" not in use_case_data:
                use_case_data["tasks"] = []
            # Sort tasks by successCount descending
            use_case_data["tasks"].sort(key=lambda t: t.get("successCount", 0), reverse=True)
            use_cases_list.append(use_case_data)
        
        # Sort use cases by successCount descending
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
        # Check if web_id contains any of the web names
        for web_name, order in WEB_ORDER.items():
            if web_name in web_id_lower:
                return order
        # If not found, return a large number to put it at the end
        return 999
    
    webs_list.sort(key=lambda w: get_web_order(w.get("webId", "")))
    
    # Contar total de evaluaciones procesadas vs total disponible
    total_evaluations_processed = len(evaluations)
    # Si se aplicó un límite, el total procesado puede ser menor que el real
    # Devolvemos el número procesado y si hay más disponibles
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
                "lastRoundWinnerReward": float(last_round_winner_reward) if last_round_winner_reward else None,
                "lastRoundWinnerWeight": float(last_round_winner_weight) if last_round_winner_weight else None,
                "lastRoundWinnerName": last_round_winner_name,
                "lastRoundWinnerImage": last_round_winner_image,
                "lastRoundWinnerHotkey": last_round_winner_hotkey,
            },
            "validatorImage": resolve_validator_image(
                validator_snapshot.name if validator_snapshot else None,
                existing=validator_snapshot.image_url if validator_snapshot else None
            ),
            "webs": webs_list,
            "availableRounds": available_rounds,
            "roundDetails": {
                "minersParticipated": unique_miners_count,
                "miners": miners_list,
            },
            "totalEvaluationsProcessed": total_evaluations_processed,
            "hasMore": has_more,
        }
    }
    
    # Devolver JSONResponse con headers de caché HTTP
    return JSONResponse(
        content=response_data,
        headers={"Cache-Control": "public, max-age=60"}  # Cache HTTP por 60 segundos
    )

