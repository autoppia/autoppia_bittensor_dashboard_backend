from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/validators", tags=["validators"])


@router.get("/{uid}/details")
async def get_validator_details(
    uid: int,
    round: Optional[int] = Query(None, description="Filter by round number"),
    website: Optional[str] = Query(None, description="Filter evaluations table by website (e.g., 'AutoCinema')"),
    useCase: Optional[str] = Query(None, description="Filter evaluations table by use case (e.g., 'SEARCH_FILM')"),
    session: AsyncSession = Depends(get_session),
):
    """
    Get detailed statistics for a validator, aggregated by web and use case.
    
    Args:
        uid: Validator UID
        round: Optional round number to filter evaluations. If not provided, returns all rounds.
        website: Optional website filter for evaluations table (e.g., "AutoCinema", "AutoBooks")
        useCase: Optional use case filter for evaluations table (e.g., "SEARCH_FILM", "CONTACT_BOOK")
    
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
    
    # Get all evaluations for this validator, optionally filtered by round
    evaluations_query = (
        select(EvaluationORM)
        .join(TaskORM, EvaluationORM.task_id == TaskORM.task_id)
        .where(EvaluationORM.validator_uid == uid)
        .options(selectinload(EvaluationORM.task))
    )
    
    # If round filter is provided, join with ValidatorRoundORM to filter by round
    if round is not None:
        evaluations_query = (
            evaluations_query
            .join(ValidatorRoundORM, EvaluationORM.validator_round_id == ValidatorRoundORM.validator_round_id)
            .where(ValidatorRoundORM.round_number == round)
        )
    
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
    
    # Get winner of last round for this validator
    last_round_winner = None
    last_round_winner_reward = None
    last_round_winner_weight = None
    last_round_winner_name = None
    last_round_winner_image = None
    last_round_winner_hotkey = None
    
    if last_round:
        # Get the winner from ValidatorRoundSummaryORM for this validator's last round (rondas finalizadas)
        winner_query = (
            select(ValidatorRoundSummaryORM)
            .join(ValidatorRoundORM, ValidatorRoundSummaryORM.validator_round_id == ValidatorRoundORM.validator_round_id)
            .where(
                and_(
                    ValidatorRoundORM.validator_round_id == last_round.validator_round_id,
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
                        ValidatorRoundMinerORM.validator_round_id == last_round.validator_round_id,
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
                .where(AgentEvaluationRunORM.validator_round_id == last_round.validator_round_id)
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
                            ValidatorRoundMinerORM.validator_round_id == last_round.validator_round_id,
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
    
    if not evaluations:
        # Return empty response if no evaluations found
        return {
            "success": True,
            "data": {
                "validator": {
                    "uid": uid,
                    "hotkey": validator_snapshot.validator_hotkey if validator_snapshot else None,
                    "stake": float(validator_snapshot.stake) if validator_snapshot and validator_snapshot.stake else None,
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
                },
                "webs": [],
            }
        }
    
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
    
    # Keep webs in natural order (as they come from database)
    # No sorting - maintain database order
    
    # Build response
    response = {
        "success": True,
        "data": {
            "validator": {
                "uid": uid,
                "hotkey": validator_snapshot.validator_hotkey if validator_snapshot else None,
                "stake": float(validator_snapshot.stake) if validator_snapshot and validator_snapshot.stake else None,
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
            "validatorImage": validator_snapshot.image_url if validator_snapshot else None,
            "webs": webs_list,
            "availableRounds": available_rounds,
        }
    }
    
    return response

