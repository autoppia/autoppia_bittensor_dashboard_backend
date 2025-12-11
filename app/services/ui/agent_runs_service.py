from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    RoundORM,
    ValidatorRoundSummaryORM,
)

from app.models.core import Evaluation, Task, TaskSolution
from app.models.ui.agent_runs import (
    Action,
    AgentRun,
    AgentInfo,
    Event,
    EventType,
    Log,
    LogLevel,
    Metrics,
    Metric,
    PerformanceByUseCase,
    PerformanceByWebsite,
    Personas,
    RecentActivity,
    RoundInfo,
    RunStatus,
    ScoreDistribution,
    Statistics,
    Summary,
    Task as UITask,
    TaskStatus,
    TopPerformingUseCase,
    TopPerformingWebsite,
    ValidatorInfo,
    Website,
)
from app.services.redis_cache import REDIS_CACHE_TTL, redis_cache
from app.services.service_utils import rollback_on_error
from app.services.ui.rounds_service import (
    AgentRunContext,
    RoundsService,
    _get_validator_uid_from_context,
)
from app.data import get_validator_metadata
from app.utils.images import resolve_agent_image, resolve_validator_image

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)  # Reduce verbosity


AGENT_RUN_STATS_CACHE_PREFIX = "agent_run_statistics"
AGENT_RUN_STATS_CACHE_TTL = REDIS_CACHE_TTL.get(
    "agent_run_statistics_final",
    7 * 24 * 3600,
)
AGENT_RUN_STATS_ACTIVE_TTL = 60


def _ts_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _extract_host(url: Optional[str]) -> str:
    if not url:
        return "unknown"
    parsed = urlparse(url)
    return parsed.netloc or parsed.path or "unknown"


def _map_website_port_to_name(url: Optional[str]) -> str:
    """
    Map localhost:PORT URLs to friendly website names.
    Returns the friendly name if found, otherwise returns the host as-is.
    """
    if not url:
        return "unknown"

    # Port to name mapping (aligned with overview_service.py and frontend)
    PORT_TO_NAME = {
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

    try:
        # Extract port from URL
        parsed = urlparse(url if url.startswith("http") else f"http://{url}")
        port = str(parsed.port) if parsed.port else None

        if port and port in PORT_TO_NAME:
            return PORT_TO_NAME[port]
    except Exception:
        pass

    # Fallback to extracting host
    return _extract_host(url)


def _safe_int(value: Optional[float]) -> int:
    if value is None:
        return 0
    return int(round(value))


class AgentRunsService:
    """SQL-backed business logic for agent evaluation runs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    @rollback_on_error
    async def list_agent_runs(
        self,
        page: int = 1,
        limit: int = 20,
        round_number: Optional[int] = None,
        validator_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        query: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        sort_by: str = "startTime",
        sort_order: str = "desc",
    ) -> Dict[str, object]:
        skip = max(0, (page - 1) * limit)

        status_filter = status.lower() if status else None
        validator_uid = _parse_identifier(validator_id) if validator_id else None
        miner_uid = _parse_identifier(agent_id) if agent_id else None
        start_ts = _to_timestamp(start_date)
        end_ts = _to_timestamp(end_date)
        query_term = query.lower() if query else None

        sort_columns: Dict[str, Any] = {
            "startTime": AgentEvaluationRunORM.started_at,
            "endTime": AgentEvaluationRunORM.ended_at,
            "averageScore": AgentEvaluationRunORM.average_score,
            "score": AgentEvaluationRunORM.average_score,
            "overallScore": AgentEvaluationRunORM.average_score,
            "totalTasks": AgentEvaluationRunORM.total_tasks,
            "completedTasks": AgentEvaluationRunORM.success_tasks,
        }

        if sort_by in {"successRate"}:
            sort_columns["successRate"] = func.coalesce(
                AgentEvaluationRunORM.success_tasks
                * 100.0
                / func.nullif(AgentEvaluationRunORM.total_tasks, 0),
                0.0,
            )

        order_expr = sort_columns.get(sort_by, AgentEvaluationRunORM.started_at)
        if isinstance(order_expr, (int, float)):
            order_expr = AgentEvaluationRunORM.started_at

        if sort_order.lower() == "desc":
            order_clause = order_expr.desc()
        else:
            order_clause = order_expr.asc()

        filters: List[Any] = []
        if validator_uid is not None:
            filters.append(AgentEvaluationRunORM.validator_uid == validator_uid)
        if miner_uid is not None:
            filters.append(AgentEvaluationRunORM.miner_uid == miner_uid)
        if round_number is not None:
            filters.append(
                AgentEvaluationRunORM.validator_round.has(
                    RoundORM.round_number == round_number
                )
            )
        if start_ts is not None:
            filters.append(AgentEvaluationRunORM.started_at >= start_ts)
        if end_ts is not None:
            filters.append(AgentEvaluationRunORM.started_at <= end_ts)

        if status_filter == RunStatus.COMPLETED.value:
            filters.append(AgentEvaluationRunORM.ended_at.is_not(None))
        elif status_filter == RunStatus.RUNNING.value:
            filters.append(
                and_(
                    AgentEvaluationRunORM.started_at.is_not(None),
                    AgentEvaluationRunORM.ended_at.is_(None),
                )
            )
        elif status_filter == RunStatus.PENDING.value:
            filters.append(AgentEvaluationRunORM.started_at.is_(None))
        elif status_filter in {RunStatus.FAILED.value, RunStatus.CANCELLED.value}:
            available_rounds = await self._list_available_round_numbers()
            return {
                "runs": [],
                "total": 0,
                "page": page,
                "limit": limit,
                "availableRounds": available_rounds,
                "selectedRound": round_number,
            }

        if query_term:
            like_pattern = f"%{query_term}%"
            filters.append(
                or_(
                    func.lower(AgentEvaluationRunORM.agent_run_id).like(like_pattern),
                    func.lower(AgentEvaluationRunORM.miner_hotkey).like(like_pattern),
                    func.lower(AgentEvaluationRunORM.validator_hotkey).like(
                        like_pattern
                    ),
                    cast(AgentEvaluationRunORM.validator_uid, String).like(
                        like_pattern
                    ),
                    cast(AgentEvaluationRunORM.miner_uid, String).like(like_pattern),
                )
            )

        base_stmt = (
            select(
                AgentEvaluationRunORM.agent_run_id,
                func.count().over().label("full_count"),
            )
            .where(*filters)
            .order_by(
                order_clause,
                AgentEvaluationRunORM.agent_run_id.desc(),
            )
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(base_stmt)
        rows = result.all()

        agent_run_ids: List[str] = [row.agent_run_id for row in rows]
        total: int = int(rows[0].full_count) if rows else 0

        if not agent_run_ids:
            available_rounds = await self._list_available_round_numbers()
            return {
                "runs": [],
                "total": total,
                "page": page,
                "limit": limit,
                "availableRounds": available_rounds,
                "selectedRound": round_number,
            }

        contexts: List[AgentRunContext] = await self.rounds_service.list_agent_run_contexts(
            include_details=True,
            agent_run_ids=agent_run_ids,
        )

        # Calculate ranks for all contexts (with score + time tiebreaker)
        await self._calculate_ranks_for_contexts(contexts)

        # Fetch consensus scores for all contexts
        consensus_scores = await self._fetch_consensus_scores_for_contexts(contexts)

        runs = [
            self._build_run_summary(context, consensus_scores.get(context.run.agent_run_id))
            for context in contexts
        ]

        available_rounds = await self._list_available_round_numbers()

        result = {
            "runs": runs,
            "total": total,
            "page": page,
            "limit": limit,
            "availableRounds": available_rounds,
            "selectedRound": round_number,
        }

        # If round_number and agent_id are provided, include validators data with local and post-consensus
        if round_number is not None and miner_uid is not None:
            try:
                validators_data = await self.rounds_service.get_aggregated_metrics(round_number)
                result["validators"] = validators_data.get("validators", [])
                result["post_consensus_summary"] = validators_data.get("post_consensus_summary", {})
            except Exception as e:
                logger.warning(f"Failed to fetch validators data for round {round_number}: {e}")
                # Continue without validators data if there's an error

        return result

    async def _list_available_round_numbers(self) -> List[int]:
        stmt = (
            select(func.distinct(RoundORM.round_number))
            .where(RoundORM.round_number.is_not(None))
            .order_by(RoundORM.round_number.desc())
            .limit(2)  # fuerza a devolver solo 2 registros
        )
        result = await self.session.scalars(stmt)
        return [int(value) for value in result if value is not None]

    async def _calculate_ranks_for_contexts(self, contexts: List[AgentRunContext]) -> None:
        """
        Calculate ranks for multiple contexts from the same validator_round.
        This is more efficient than calculating one by one.
        """
        if not contexts:
            return
        
        # Group by validator_round_id
        from collections import defaultdict
        grouped: Dict[str, List[AgentRunContext]] = defaultdict(list)
        for ctx in contexts:
            grouped[ctx.round.validator_round_id].append(ctx)
        
        # Calculate ranks for each validator_round
        for validator_round_id, round_contexts in grouped.items():
            # Get all agent_runs in this validator_round (not just the ones in contexts)
            stmt = (
                select(AgentEvaluationRunORM)
                .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
                .options(selectinload(AgentEvaluationRunORM.evaluations))
            )
            all_runs = await self.session.scalars(stmt)
            
            # Calculate metrics for all runs
            run_metrics = []
            for run in all_runs:
                # is_sota removed from schema; skip runs without miner_uid
                if run.miner_uid is None:
                    continue
                
                eval_results = getattr(run, 'evaluations', []) or []
                if eval_results:
                    scores = [getattr(er, 'eval_score', getattr(er, 'final_score', None)) for er in eval_results if getattr(er, 'eval_score', getattr(er, 'final_score', None)) is not None]
                    avg_score = sum(scores) / len(scores) if scores else 0.0
                    times = [er.evaluation_time for er in eval_results if er.evaluation_time is not None and er.evaluation_time > 0]
                    avg_time = sum(times) / len(times) if times else float('inf')
                else:
                    avg_score = run.average_score or 0.0
                    avg_time = run.average_execution_time or float('inf')
                
                run_metrics.append({
                    'agent_run_id': run.agent_run_id,
                    'score': avg_score,
                    'avg_time': avg_time
                })
            
            # Sort by score (desc) then by time (asc)
            run_metrics.sort(key=lambda x: (-x['score'], x['avg_time']))
            
            # Assign ranks
            ranks_map = {}
            last_score = None
            last_time = None
            current_rank = 0
            for position, run_info in enumerate(run_metrics, start=1):
                score = run_info['score']
                time = run_info['avg_time']
                
                if (last_score is None or abs(score - last_score) > 1e-6 or 
                    (abs(score - last_score) < 1e-6 and abs(time - last_time) > 1e-6)):
                    current_rank = position
                    last_score = score
                    last_time = time
                
                ranks_map[run_info['agent_run_id']] = current_rank
            
            # Apply ranks to contexts
            for ctx in round_contexts:
                ctx.run.rank = ranks_map.get(ctx.run.agent_run_id, 0)

    async def _calculate_rank_for_context(self, context: AgentRunContext) -> None:
        """
        Calculate rank for this agent_run by comparing with all runs in the same validator_round.
        Ranking criteria:
        1. Higher score is better
        2. In case of tie, lower average evaluation time is better (faster)
        """
        validator_round_id = context.round.validator_round_id
        
        # Get all agent_runs in this validator_round
        stmt = (
            select(AgentEvaluationRunORM)
            .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
            .options(selectinload(AgentEvaluationRunORM.evaluations))
        )
        all_runs = await self.session.scalars(stmt)
        
        # Calculate scores and times for all runs
        run_metrics = []
        for run in all_runs:
            # is_sota is not directly on AgentEvaluationRunORM, it's in miner_snapshots
            # For now, skip if miner_uid is None (SOTA runs typically don't have miner_uid)
            if run.miner_uid is None:
                continue  # Skip runs without miner_uid (likely SOTA or invalid)
            
            # Calculate average score from evaluations
            eval_results = getattr(run, 'evaluations', []) or []
            if eval_results:
                scores = [getattr(er, 'eval_score', getattr(er, 'final_score', None)) for er in eval_results if getattr(er, 'eval_score', getattr(er, 'final_score', None)) is not None]
                avg_score = sum(scores) / len(scores) if scores else 0.0
                
                # Calculate average evaluation time
                times = [er.evaluation_time for er in eval_results if er.evaluation_time is not None and er.evaluation_time > 0]
                avg_time = sum(times) / len(times) if times else float('inf')
            else:
                avg_score = run.average_score or 0.0
                avg_time = run.average_execution_time or float('inf')
            
            run_metrics.append({
                'agent_run_id': run.agent_run_id,
                'miner_uid': run.miner_uid,
                'score': avg_score,
                'avg_time': avg_time
            })
        
        # Sort by score (descending) then by time (ascending - lower is better)
        run_metrics.sort(key=lambda x: (-x['score'], x['avg_time']))
        
        # Assign ranks (handle ties: same score AND same time get same rank)
        last_score = None
        last_time = None
        current_rank = 0
        for position, run_info in enumerate(run_metrics, start=1):
            score = run_info['score']
            time = run_info['avg_time']
            
            # Only increment rank if score OR time is different
            if (last_score is None or abs(score - last_score) > 1e-6 or 
                (abs(score - last_score) < 1e-6 and abs(time - last_time) > 1e-6)):
                current_rank = position
                last_score = score
                last_time = time
            
            if run_info['agent_run_id'] == context.run.agent_run_id:
                context.run.rank = current_rank
                break

    @rollback_on_error
    async def _fetch_consensus_score_for_context(
        self, context: AgentRunContext
    ) -> Optional[float]:
        """Fetch post_consensus_avg_reward from validator_round_summary_miners for a context."""
        if not context.run.miner_uid:
            return None
        
        stmt = select(ValidatorRoundSummaryORM.post_consensus_avg_reward).where(
            ValidatorRoundSummaryORM.validator_round_id == context.round.validator_round_id,
            ValidatorRoundSummaryORM.miner_uid == context.run.miner_uid,
        )
        result = await self.session.scalar(stmt)
        return float(result) if result is not None else None

    @rollback_on_error
    async def _fetch_consensus_scores_for_contexts(
        self, contexts: List[AgentRunContext]
    ) -> Dict[str, Optional[float]]:
        """Fetch post_consensus_avg_reward from validator_round_summary_miners for multiple contexts."""
        if not contexts:
            return {}
        
        # Build a map of (validator_round_id, miner_uid) -> agent_run_id
        score_map: Dict[str, Optional[float]] = {}
        queries: List[Tuple[str, str, int]] = []
        
        for context in contexts:
            if context.run.miner_uid:
                queries.append((
                    context.run.agent_run_id,
                    context.round.validator_round_id,
                    context.run.miner_uid,
                ))
        
        if not queries:
            return {}
        
        # Query all at once
        validator_round_ids = list(set(q[1] for q in queries))
        miner_uids = list(set(q[2] for q in queries))
        
        stmt = select(
            ValidatorRoundSummaryORM.validator_round_id,
            ValidatorRoundSummaryORM.miner_uid,
            ValidatorRoundSummaryORM.post_consensus_avg_reward,
        ).where(
            ValidatorRoundSummaryORM.validator_round_id.in_(validator_round_ids),
            ValidatorRoundSummaryORM.miner_uid.in_(miner_uids),
        )
        
        result = await self.session.execute(stmt)
        rows = result.all()
        
        # Build a lookup map: (validator_round_id, miner_uid) -> post_consensus_avg_reward
        score_lookup: Dict[Tuple[str, int], float] = {}
        for row in rows:
            if row.post_consensus_avg_reward is not None:
                score_lookup[(row.validator_round_id, row.miner_uid)] = float(row.post_consensus_avg_reward)
        
        # Map back to agent_run_id
        for agent_run_id, validator_round_id, miner_uid in queries:
            key = (validator_round_id, miner_uid)
            score_map[agent_run_id] = score_lookup.get(key)
        
        return score_map

    @rollback_on_error
    async def get_agent_run(self, agent_run_id: str) -> Optional[AgentRun]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        
        # Calculate rank by comparing with all other runs in the same validator_round
        await self._calculate_rank_for_context(context)
        
        # Fetch consensus score if available
        consensus_score = await self._fetch_consensus_score_for_context(context)
        
        return self._build_agent_run(context, consensus_score)

    @rollback_on_error
    async def get_personas(self, agent_run_id: str) -> Optional[Personas]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_personas(context)

    @rollback_on_error
    async def get_statistics(self, agent_run_id: str) -> Optional[Statistics]:
        cache_key = f"{AGENT_RUN_STATS_CACHE_PREFIX}:{agent_run_id}"

        cached_stats = redis_cache.get(cache_key)
        if cached_stats is not None:
            logger.debug("agent_run_statistics cache hit for %s", agent_run_id)
            return cached_stats

        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        statistics = self._build_statistics(context)

        if statistics is None:
            return None

        run_finished = bool(getattr(context.run, "ended_at", None))
        ttl = AGENT_RUN_STATS_CACHE_TTL if run_finished else AGENT_RUN_STATS_ACTIVE_TTL
        redis_cache.set(cache_key, statistics, ttl=ttl)
        logger.debug(
            "agent_run_statistics cached for %s (ttl=%ss, finished=%s)",
            agent_run_id,
            ttl,
            run_finished,
        )

        return statistics

    @rollback_on_error
    async def get_summary(self, agent_run_id: str) -> Optional[Summary]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        return self._build_summary(context)

    @rollback_on_error
    async def get_tasks(self, agent_run_id: str) -> Optional[List[UITask]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        _, _, task_map = self._index_results(context)
        return list(task_map.values())

    @rollback_on_error
    async def get_timeline(self, agent_run_id: str) -> Optional[List[Event]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        events: List[Event] = []
        start_time = (
            _ts_to_iso(context.run.started_at) or datetime.now(timezone.utc).isoformat()
        )
        events.append(
            Event(
                timestamp=start_time,
                type=EventType.RUN_STARTED,
                message="Agent run started",
            )
        )

        for evaluation in context.evaluations:
            task_event_time = start_time
            # stats field removed - use evaluation_time or created_at if needed
            # if evaluation.stats and evaluation.stats.start_time:
            #     task_event_time = _ts_to_iso(evaluation.stats.start_time) or start_time
            events.append(
                Event(
                    timestamp=task_event_time,
                    type=EventType.TASK_COMPLETED,
                    message=f"Task {evaluation.task_id} evaluated",
                    taskId=evaluation.task_id,
                )
            )

        if context.run.ended_at:
            events.append(
                Event(
                    timestamp=_ts_to_iso(context.run.ended_at) or start_time,
                    type=EventType.RUN_COMPLETED,
                    message="Agent run completed",
                )
            )

        return events

    @rollback_on_error
    async def get_logs(self, agent_run_id: str) -> Optional[List[Log]]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        logs: List[Log] = []
        for evaluation in context.evaluations:
            if evaluation.feedback and evaluation.feedback.execution_history:
                for entry in evaluation.feedback.execution_history:
                    message = str(entry)
                    logs.append(
                        Log(
                            timestamp=_ts_to_iso(context.run.started_at) or "",
                            level=LogLevel.INFO,
                            message=message,
                        )
                    )
        return logs

    @rollback_on_error
    async def get_metrics(self, agent_run_id: str) -> Optional[Metrics]:
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None

        timestamps = []
        if context.run.started_at:
            timestamps.append(context.run.started_at)
        if context.run.ended_at:
            timestamps.append(context.run.ended_at)
        if not timestamps:
            timestamps.append(datetime.now(timezone.utc).timestamp())

        metrics_time = [
            Metric(timestamp=_ts_to_iso(ts) or "", value=float(index + 1))
            for index, ts in enumerate(sorted(timestamps))
        ]

        duration = int(
            (context.run.ended_at or context.run.started_at or 0)
            - (context.run.started_at or 0)
        )

        return Metrics(
            cpu=metrics_time,
            memory=metrics_time,
            network=metrics_time,
            duration=duration,
            peakCpu=max((metric.value for metric in metrics_time), default=0.0),
            peakMemory=max((metric.value for metric in metrics_time), default=0.0),
            totalNetworkTraffic=len(metrics_time) * 100,
        )

    async def get_agent_run_complete(self, agent_run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get all agent run data in a single call, similar to get-evaluation.
        Returns: statistics, tasks, info
        """
        try:
            context = await self.rounds_service.get_agent_run_context(agent_run_id)
        except ValueError:
            return None
        
        # Calculate rank and fetch consensus score
        await self._calculate_rank_for_context(context)
        consensus_score = await self._fetch_consensus_score_for_context(context)
        
        # Build all data - only statistics, tasks, and info (no run, no summary)
        statistics = self._build_statistics_simplified(context)
        _, _, task_map = self._index_results(context)
        
        # Build evaluations list (not tasks) - each evaluation represents a task+miner combination
        evaluations_list = []
        for evaluation in context.evaluations:
            # Find corresponding task for this evaluation
            task_id = getattr(evaluation, "task_id", None)
            task = task_map.get(task_id) if task_id else None
            
            if not task:
                continue
            
            # Get task info
            task_dict = task.model_dump()
            # Remove actions, screenshots, logs
            task_dict.pop("actions", None)
            task_dict.pop("screenshots", None)
            task_dict.pop("logs", None)
            
            # eval_score is 0 or 1 (passed or failed)
            eval_score = getattr(evaluation, 'eval_score', getattr(evaluation, 'final_score', 0.0))
            eval_score_binary = 1.0 if eval_score >= 0.5 else 0.0
            # eval_time from evaluation
            eval_time = getattr(evaluation, 'evaluation_time', 0.0) or 0.0
            # reward from evaluation
            reward = getattr(evaluation, 'reward', None) or eval_score  # Fallback to eval_score if no reward
            
            # Build evaluation object with evaluationId instead of taskId
            evaluation_dict = {
                "evaluationId": evaluation.evaluation_id,
                "taskId": task_id,  # Keep taskId for reference
                "prompt": task_dict.get("prompt"),
                "website": task_dict.get("website"),
                "useCase": task_dict.get("useCase"),
                "status": task_dict.get("status"),
                "eval_score": eval_score_binary,  # 0 or 1
                "eval_time": eval_time,
                "reward": reward,
                "startTime": task_dict.get("startTime"),
                "endTime": task_dict.get("endTime"),
            }
            
            evaluations_list.append(evaluation_dict)
        
        # Build info object
        info = self._build_agent_run_info(context)
        
        return {
            "statistics": statistics if statistics else None,
            "evaluations": evaluations_list,  # Changed from "tasks" to "evaluations"
            "info": info,
        }
    
    def _build_timeline_events(self, context: AgentRunContext) -> List[Event]:
        """Build timeline events from context."""
        events: List[Event] = []
        start_time = (
            _ts_to_iso(context.run.started_at) or datetime.now(timezone.utc).isoformat()
        )
        events.append(
            Event(
                timestamp=start_time,
                type=EventType.RUN_STARTED,
                message="Agent run started",
            )
        )

        for evaluation in context.evaluations:
            task_event_time = start_time
            events.append(
                Event(
                    timestamp=task_event_time,
                    type=EventType.TASK_COMPLETED,
                    message=f"Task {evaluation.task_id} evaluated",
                    taskId=evaluation.task_id,
                )
            )

        if context.run.ended_at:
            end_time = _ts_to_iso(context.run.ended_at) or datetime.now(timezone.utc).isoformat()
            events.append(
                Event(
                    timestamp=end_time,
                    type=EventType.RUN_COMPLETED,
                    message="Agent run completed",
                )
            )

        return sorted(events, key=lambda e: e.timestamp)

    def _build_logs_from_context(self, context: AgentRunContext) -> List[Log]:
        """Build logs from context."""
        logs: List[Log] = []
        for evaluation in context.evaluations:
            if evaluation.feedback:
                feedback_data = evaluation.feedback
                if isinstance(feedback_data, dict):
                    log_entries = feedback_data.get("logs", [])
                    for entry in log_entries:
                        if isinstance(entry, dict):
                            level_str = entry.get("level", "info").upper()
                            try:
                                level = LogLevel(level_str)
                            except ValueError:
                                level = LogLevel.INFO
                            logs.append(
                                Log(
                                    timestamp=entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                                    level=level,
                                    message=entry.get("message", ""),
                                    taskId=evaluation.task_id,
                                )
                            )
        return sorted(logs, key=lambda l: l.timestamp)

    def _build_metrics_from_context(self, context: AgentRunContext) -> Optional[Metrics]:
        """Build metrics from context."""
        timestamps = []
        if context.run.started_at:
            timestamps.append(context.run.started_at)
        if context.run.ended_at:
            timestamps.append(context.run.ended_at)
        if not timestamps:
            timestamps.append(datetime.now(timezone.utc).timestamp())

        metrics_time = [
            Metric(timestamp=_ts_to_iso(ts) or "", value=float(index + 1))
            for index, ts in enumerate(sorted(timestamps))
        ]

        duration = int(
            (context.run.ended_at or context.run.started_at or 0)
            - (context.run.started_at or 0)
        )

        return Metrics(
            cpu=metrics_time,
            memory=metrics_time,
            network=metrics_time,
            duration=duration,
            peakCpu=max((metric.value for metric in metrics_time), default=0.0),
            peakMemory=max((metric.value for metric in metrics_time), default=0.0),
            totalNetworkTraffic=len(metrics_time) * 100,
        )

    def _build_agent_run_info(self, context: AgentRunContext) -> Dict[str, Any]:
        """Build info object with agent run metadata."""
        from app.utils.images import resolve_validator_image, resolve_agent_image
        
        validator_uid = _get_validator_uid_from_context(context)
        validator_model: Optional[ValidatorInfo] = None
        if context.round.validators:
            validator_model = next(
                (val for val in context.round.validators if val.uid == validator_uid),
                context.round.validators[0] if context.round.validators else None
            )

        if validator_model is None:
            validator_model = ValidatorInfo(
                uid=validator_uid or 0,
                hotkey=_format_validator_id(validator_uid) if validator_uid else "unknown",
                coldkey=None,
                stake=0.0,
                vtrust=0.0,
                name=None,
                version=None
            )

        validator_info = {
            "uid": abs(int(validator_model.uid)) if validator_model.uid is not None else 0,
            "hotkey": validator_model.hotkey,
            "coldkey": validator_model.coldkey,
            "name": validator_model.name,
            "stake": float(getattr(validator_model, "stake", 0.0) or 0.0),
            "vtrust": float(getattr(validator_model, "vtrust", 0.0) or 0.0),
            "version": getattr(validator_model, "version", None),
            "image": resolve_validator_image(name=validator_model.name, existing=getattr(validator_model, "image_url", None)),
        }

        miner_model = context.run.miner_info
        miner_info = {
            "uid": abs(int(miner_model.uid)) if (miner_model and miner_model.uid is not None) else abs(int(context.run.miner_uid)),
            "hotkey": miner_model.hotkey if miner_model else None,
            "name": (miner_model.agent_name if miner_model and miner_model.agent_name else _format_agent_id(context.run.miner_uid)),
            "github": getattr(miner_model, "github", None) if miner_model else None,
            "image": resolve_agent_image(miner_model),
            "isSota": context.run.is_sota,
        }

        start_epoch_val = getattr(context.round, "start_epoch", None)
        end_epoch_val = getattr(context.round, "end_epoch", None)
        if end_epoch_val is None:
            try:
                status_lower = str(context.round.status or "").lower()
                if status_lower in {"completed", "finished", "complete"}:
                    from app.services.ui.rounds_service import compute_boundaries_for_round
                    bounds = compute_boundaries_for_round(int(context.round.round_number or 0))
                    end_epoch_val = int(bounds.end_epoch)
                    if start_epoch_val is None:
                        start_epoch_val = int(bounds.start_epoch)
            except Exception:
                pass

        round_info = {
            "validatorRoundId": context.round.validator_round_id,
            "roundNumber": context.round.round_number,
            "status": context.round.status,
            "startedAt": _ts_to_iso(context.round.started_at) if context.round.started_at else None,
            "endedAt": _ts_to_iso(context.round.ended_at) if context.round.ended_at else None,
            "startEpoch": start_epoch_val,
            "endEpoch": end_epoch_val,
        }

        return {
            "agentRunId": context.run.agent_run_id,
            "round": round_info,
            "validator": validator_info,
            "miner": miner_info,
        }

    async def compare_runs(self, run_ids: List[str]) -> Dict[str, Any]:
        contexts: List[AgentRunContext] = []
        for run_id in run_ids:
            try:
                context = await self.rounds_service.get_agent_run_context(run_id)
            except ValueError:
                continue
            contexts.append(context)

        # Fetch consensus scores for all contexts
        consensus_scores = await self._fetch_consensus_scores_for_contexts(contexts)

        runs: List[AgentRun] = [
            self._build_agent_run(context, consensus_scores.get(context.run.agent_run_id))
            for context in contexts
        ]

        if not runs:
            return {
                "runs": [],
                "comparison": {
                    "bestScore": "",
                    "fastest": "",
                    "mostTasks": "",
                    "bestSuccessRate": "",
                },
            }

        def _success_rate(run: AgentRun) -> float:
            return (
                run.successfulTasks / run.totalTasks * 100.0 if run.totalTasks else 0.0
            )

        best_score_run = max(
            runs, key=lambda run: run.score if run.score is not None else 0.0
        )
        fastest_run = min(
            runs,
            key=lambda run: run.duration if run.duration is not None else float("inf"),
        )
        most_tasks_run = max(runs, key=lambda run: run.totalTasks)
        best_success_run = max(runs, key=_success_rate)

        return {
            "runs": [run.model_dump() for run in runs],
            "comparison": {
                "bestScore": best_score_run.runId,
                "fastest": fastest_run.runId,
                "mostTasks": most_tasks_run.runId,
                "bestSuccessRate": best_success_run.runId,
            },
        }

    def _build_agent_run(
        self, context: AgentRunContext, consensus_score: Optional[float] = None
    ) -> AgentRun:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)

        # Prefer consensus_score if available, otherwise compute from evaluations
        if consensus_score is not None:
            average_score = float(consensus_score)
        else:
            average_score = self._compute_average_score(context.evaluations)
        overall_score = _safe_int(average_score * 100)
        average_evaluation_time = self._average_evaluation_time(context)

        validator_name, validator_image = self._resolve_validator_identity(context)
        (
            agent_name,
            agent_image,
            agent_uid,
            agent_hotkey,
            agent_identifier,
            agent_description,
        ) = self._resolve_agent_identity(context)
        round_id_value = context.round.round_number
        if round_id_value is None:
            round_id_value = _round_id_to_int(context.round.validator_round_id)
        return AgentRun(
            runId=context.run.agent_run_id,
            agentId=agent_identifier,
            agentUid=agent_uid,
            agentHotkey=agent_hotkey,
            agentName=agent_name,
            roundId=round_id_value or 0,
            validatorId=_format_validator_id(_get_validator_uid_from_context(context) or 0),
            validatorName=validator_name,
            validatorImage=validator_image,
            startTime=_ts_to_iso(context.run.started_at) or "",
            endTime=_ts_to_iso(context.run.ended_at) or "",
            status=self._run_status(context),
            totalTasks=total_tasks,
            completedTasks=success_count,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            score=average_score,
            ranking=context.run.rank or 0,
            duration=_safe_int(
                (context.run.ended_at or context.run.started_at or 0)
                - (context.run.started_at or 0)
            ),
            overallScore=overall_score,
            averageEvaluationTime=(
                round(average_evaluation_time, 3)
                if average_evaluation_time is not None
                else None
            ),
            totalWebsites=len(websites),
            websites=websites,
            tasks=ui_tasks,
            metadata={
                **(context.run.metadata or {}),
                "agentImage": agent_image,
                "agentDescription": agent_description,
            },
        )

    def _build_personas(self, context: AgentRunContext) -> Personas:
        validator_name, validator_image = self._resolve_validator_identity(context)
        (
            agent_name,
            agent_image,
            agent_uid,
            agent_hotkey,
            agent_identifier,
            agent_description,
        ) = self._resolve_agent_identity(context)

        round_number_value = context.round.round_number
        if round_number_value is None:
            round_number_value = _round_id_to_int(context.round.validator_round_id)

        round_info = RoundInfo(
            id=round_number_value or 0,
            name=context.round.validator_round_id,
            status=context.round.status,
            startTime=_ts_to_iso(context.round.started_at) or "",
            endTime=_ts_to_iso(context.round.ended_at),
        )

        validator_uid = _get_validator_uid_from_context(context)
        validator_info = ValidatorInfo(
            id=_format_validator_id(validator_uid) if validator_uid else "unknown",
            name=validator_name,
            image=validator_image,
            description="",
            website="",
            github="",
        )

        agent_info = AgentInfo(
            id=agent_identifier,
            uid=agent_uid,
            hotkey=agent_hotkey,
            name=agent_name,
            type="sota" if context.run.is_sota else "miner",
            image=agent_image,
            description=agent_description,
        )

        return Personas(round=round_info, validator=validator_info, agent=agent_info)

    def _summarize_ui_tasks(
        self,
        ui_tasks: List[UITask],
        task_id_to_reward: Optional[Dict[str, float]] = None,
    ) -> Tuple[
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, float]],
        Dict[str, Dict[str, Dict[str, float]]],
        float,
    ]:
        website_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0.0,
                "successful": 0.0,
                "score_sum": 0.0,
                "reward_sum": 0.0,  # Added for averageReward calculation
                "duration_sum": 0.0,
            }
        )
        use_case_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0.0,
                "successful": 0.0,
                "score_sum": 0.0,
                "reward_sum": 0.0,  # Added for averageReward calculation
                "duration_sum": 0.0,
            }
        )
        # New: website + use_case combined stats
        website_usecase_stats: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "tasks": 0.0,
                    "successful": 0.0,
                    "score_sum": 0.0,
                    "reward_sum": 0.0,  # Added for averageReward calculation
                    "duration_sum": 0.0,
                }
            )
        )
        total_duration = 0.0

        for task in ui_tasks:
            duration = float(getattr(task, "duration", 0) or 0)
            score = float(getattr(task, "score", 0.0) or 0.0)
            success = task.status == TaskStatus.COMPLETED
            # Get reward from mapping if available, otherwise use score as fallback
            reward = task_id_to_reward.get(task.taskId, score) if task_id_to_reward else score

            total_duration += duration

            host = _map_website_port_to_name(task.website)
            host_stats = website_stats[host]
            host_stats["tasks"] += 1
            host_stats["score_sum"] += score
            host_stats["reward_sum"] += reward
            host_stats["duration_sum"] += duration
            if success:
                host_stats["successful"] += 1

            use_case = task.useCase or "unknown"
            use_case_entry = use_case_stats[use_case]
            use_case_entry["tasks"] += 1
            use_case_entry["score_sum"] += score
            use_case_entry["reward_sum"] += reward
            use_case_entry["duration_sum"] += duration
            if success:
                use_case_entry["successful"] += 1

            # Track stats for (website, use_case) combination
            website_uc_entry = website_usecase_stats[host][use_case]
            website_uc_entry["tasks"] += 1
            website_uc_entry["score_sum"] += score
            website_uc_entry["reward_sum"] += reward
            website_uc_entry["duration_sum"] += duration
            if success:
                website_uc_entry["successful"] += 1

        return website_stats, use_case_stats, website_usecase_stats, total_duration

    def _build_statistics(self, context: AgentRunContext) -> Statistics:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)
        overall_score = _safe_int(
            self._compute_average_score(context.evaluations) * 100
        )

        website_stats_map, use_case_stats_map, website_usecase_stats, total_duration = (
            self._summarize_ui_tasks(ui_tasks, None)  # No reward mapping needed for this method
        )

        performance_by_website = []
        for website_key, values in website_stats_map.items():
            # Build use cases specific to this website
            use_cases_for_website = []
            if website_key in website_usecase_stats:
                for uc_name, uc_values in website_usecase_stats[website_key].items():
                    use_cases_for_website.append(
                        PerformanceByUseCase(
                            useCase=uc_name,
                            tasks=int(uc_values["tasks"]),
                            successful=int(uc_values["successful"]),
                            failed=int(
                                max(uc_values["tasks"] - uc_values["successful"], 0)
                            ),
                            averageScore=(
                                (uc_values["score_sum"] / uc_values["tasks"])
                                if uc_values["tasks"]
                                else 0.0
                            ),
                            averageDuration=(
                                (uc_values["duration_sum"] / uc_values["tasks"])
                                if uc_values["tasks"]
                                else 0.0
                            ),
                        )
                    )

            performance_by_website.append(
                PerformanceByWebsite(
                    website=website_key,
                    tasks=int(values["tasks"]),
                    successful=int(values["successful"]),
                    failed=int(max(values["tasks"] - values["successful"], 0)),
                    averageScore=(
                        (values["score_sum"] / values["tasks"])
                        if values["tasks"]
                        else 0.0
                    ),
                    averageDuration=(
                        (values["duration_sum"] / values["tasks"])
                        if values["tasks"]
                        else 0.0
                    ),
                    useCases=use_cases_for_website,
                )
            )

        excellent = len(
            [er for er in context.evaluations if getattr(er, 'eval_score', getattr(er, 'final_score', 0.0)) >= 0.9]
        )
        good = len(
            [er for er in context.evaluations if 0.7 <= getattr(er, 'eval_score', getattr(er, 'final_score', 0.0)) < 0.9]
        )
        average = len(
            [er for er in context.evaluations if 0.5 <= getattr(er, 'eval_score', getattr(er, 'final_score', 0.0)) < 0.7]
        )
        poor = len(context.evaluations) - excellent - good - average

        score_distribution = ScoreDistribution(
            excellent=excellent,
            good=good,
            average=average,
            poor=max(poor, 0),
        )

        return Statistics(
            runId=context.run.agent_run_id,
            overallScore=overall_score,
            totalTasks=total_tasks,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            websites=len(website_stats_map) or len(websites),
            averageTaskDuration=(total_duration / total_tasks) if total_tasks else 0.0,
            successRate=(success_count / total_tasks * 100) if total_tasks else 0.0,
            scoreDistribution=score_distribution,
            performanceByWebsite=performance_by_website,
        )
    
    def _build_statistics_simplified(self, context: AgentRunContext) -> Dict[str, Any]:
        """
        Build simplified statistics with only essential info:
        totalTasks, websites, avg_score, avg_reward, avg_time,
        successfulTasks, failedTasks, performanceByWebsite
        
        NOTE: totalTasks now counts EVALUATIONS, not unique tasks.
        Each evaluation represents a task+miner combination.
        """
        # Get task_map to map task_id to website/useCase
        _, _, task_map = self._index_results(context)
        
        # Count EVALUATIONS, not unique tasks
        total_evaluations = len(context.evaluations)
        
        # Count successful evaluations
        # eval_score can be decimal (0.0-1.0) or binary (0 or 1)
        # Consider successful if eval_score >= 0.5 (same logic as in get_agent_run_complete)
        successful_evaluations = 0
        for er in context.evaluations:
            eval_score_val = getattr(er, 'eval_score', getattr(er, 'final_score', None))
            if eval_score_val is not None:
                eval_score_float = float(eval_score_val)
                if eval_score_float >= 0.5:
                    successful_evaluations += 1
        failed_evaluations = max(total_evaluations - successful_evaluations, 0)
        
        # Calculate avg_score (average of eval_score from evaluations)
        eval_scores = [
            getattr(er, 'eval_score', getattr(er, 'final_score', None))
            for er in context.evaluations
            if getattr(er, 'eval_score', getattr(er, 'final_score', None)) is not None
        ]
        avg_score = sum(eval_scores) / len(eval_scores) if eval_scores else 0.0
        
        # Calculate avg_reward (average of reward from evaluations)
        # Filter out None values, but keep 0.0 values (they are valid)
        rewards = [
            getattr(er, 'reward', None)
            for er in context.evaluations
            if getattr(er, 'reward', None) is not None
        ]
        if rewards:
            avg_reward = sum(rewards) / len(rewards)
            # 🔍 CRITICAL FIX: If avg_reward is 0.0 but avg_score > 0, it means rewards weren't calculated properly
            # If avg_score = 1.0, reward must be at least 0.995 (EVAL_SCORE_WEIGHT), never 0.0
            # Use a minimum reward based on avg_score to ensure consistency
            if avg_reward == 0.0 and avg_score > 0.0:
                # If score is 1.0, reward should be at least 0.995 (EVAL_SCORE_WEIGHT)
                # If score is between 0 and 1, use score as minimum (shouldn't happen with binary scores)
                if avg_score >= 1.0:
                    avg_reward = 0.995  # Minimum reward for completed tasks (EVAL_SCORE_WEIGHT)
                else:
                    avg_reward = avg_score  # Fallback to eval_score for partial scores
        else:
            # No rewards available - use avg_score as fallback, but ensure minimum for completed tasks
            if avg_score >= 1.0:
                avg_reward = 0.995  # Minimum reward for completed tasks
            else:
                avg_reward = avg_score  # Fallback to avg_score if no reward
        
        # Calculate avg_time (average of evaluation_time from evaluations)
        times = [
            er.evaluation_time
            for er in context.evaluations
            if er.evaluation_time is not None and er.evaluation_time > 0
        ]
        avg_time = sum(times) / len(times) if times else 0.0
        
        # Group evaluations by website and useCase
        from collections import defaultdict
        website_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {
                "evaluations": 0.0,
                "successful": 0.0,
                "reward_sum": 0.0,
                "duration_sum": 0.0,
            }
        )
        
        # Group by website + useCase for statsByUsecase
        website_usecase_stats: Dict[str, Dict[str, Dict[str, float]]] = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "evaluations": 0.0,
                    "successful": 0.0,
                    "failed": 0.0,
                    "reward_sum": 0.0,
                    "duration_sum": 0.0,
                    "score_sum": 0.0,
                }
            )
        )
        
        websites_set = set()
        
        for evaluation in context.evaluations:
            task_id = getattr(evaluation, "task_id", None)
            task = task_map.get(task_id) if task_id else None
            
            if not task:
                continue
            
            # Get website and useCase from task
            website = getattr(task, "website", None) or "unknown"
            use_case = getattr(task, "useCase", None) or "unknown"
            websites_set.add(website)
            
            # Get evaluation metrics
            eval_score = getattr(evaluation, 'eval_score', getattr(evaluation, 'final_score', 0.0)) or 0.0
            eval_score_float = float(eval_score)
            # Consider successful if eval_score >= 0.5 (same logic as in get_agent_run_complete)
            is_successful = eval_score_float >= 0.5
            reward = getattr(evaluation, 'reward', None) or 0.0
            eval_time = getattr(evaluation, 'evaluation_time', 0.0) or 0.0
            
            # Update website stats
            website_stats[website]["evaluations"] += 1
            if is_successful:
                website_stats[website]["successful"] += 1
            website_stats[website]["reward_sum"] += float(reward)
            website_stats[website]["duration_sum"] += float(eval_time)
            
            # Update website + useCase stats
            website_usecase_stats[website][use_case]["evaluations"] += 1
            if is_successful:
                website_usecase_stats[website][use_case]["successful"] += 1
            else:
                website_usecase_stats[website][use_case]["failed"] += 1
            website_usecase_stats[website][use_case]["reward_sum"] += float(reward)
            website_usecase_stats[website][use_case]["duration_sum"] += float(eval_time)
            website_usecase_stats[website][use_case]["score_sum"] += eval_score_float
        
        # Build simplified performance by website
        performance_by_website = []
        for website_key, values in website_stats.items():
            evaluations_count = int(values["evaluations"])
            successful_count = int(values["successful"])
            # averageScore is success rate (0-1): successful evaluations / total evaluations
            success_rate = (successful_count / evaluations_count) if evaluations_count > 0 else 0.0
            # averageReward is average of reward values (0-1)
            average_reward = (values["reward_sum"] / evaluations_count) if evaluations_count > 0 else 0.0
            # averageDuration is average of eval_time
            average_duration = (values["duration_sum"] / evaluations_count) if evaluations_count > 0 else 0.0
            
            # Build statsByUsecase for this website
            stats_by_usecase = []
            if website_key in website_usecase_stats:
                for use_case_key, use_case_values in website_usecase_stats[website_key].items():
                    use_case_evaluations = int(use_case_values["evaluations"])
                    use_case_successful = int(use_case_values["successful"])
                    use_case_failed = int(use_case_values["failed"])
                    
                    # avgScore is success rate (0-1): successful / total (same as website level)
                    avg_score = (use_case_successful / use_case_evaluations) if use_case_evaluations > 0 else 0.0
                    # avg_reward is average of reward (0-1)
                    avg_reward = (use_case_values["reward_sum"] / use_case_evaluations) if use_case_evaluations > 0 else 0.0
                    # avg_time is average of eval_time
                    avg_time = (use_case_values["duration_sum"] / use_case_evaluations) if use_case_evaluations > 0 else 0.0
                    
                    stats_by_usecase.append({
                        "useCase": use_case_key,
                        "total": use_case_evaluations,
                        "successful": use_case_successful,
                        "failed": use_case_failed,
                        "avgScore": avg_score,  # Success rate (0-1): successful/total
                        "avgReward": avg_reward,  # Average reward (0-1)
                        "avgTime": avg_time,  # Average eval_time
                    })
            
            performance_by_website.append({
                "website": website_key,
                "tasks": evaluations_count,  # Keep "tasks" key for backward compatibility, but it's actually evaluations
                "successful": successful_count,
                "failed": int(max(evaluations_count - successful_count, 0)),
                "averageScore": success_rate,  # Success rate (0-1)
                "averageReward": average_reward,  # Average reward (0-1)
                "averageDuration": average_duration,
                "statsByUsecase": stats_by_usecase,  # Added stats by use case
            })
        
        return {
            "totalTasks": total_evaluations,  # Actually total evaluations
            "websites": len(websites_set),
            "avg_score": avg_score,
            "avg_reward": avg_reward,
            "avg_time": avg_time,
            "successfulTasks": successful_evaluations,  # Actually successful evaluations
            "failedTasks": failed_evaluations,  # Actually failed evaluations
            "performanceByWebsite": performance_by_website,
        }

    def _build_summary(self, context: AgentRunContext) -> Summary:
        websites, ui_tasks, success_count = self._build_websites_and_tasks(context)
        total_tasks = len(ui_tasks)
        failed_tasks = max(total_tasks - success_count, 0)
        overall_score = _safe_int(
            self._compute_average_score(context.evaluations) * 100
        )
        agent_name, _, agent_uid, agent_hotkey, agent_identifier, _ = (
            self._resolve_agent_identity(context)
        )

        website_stats_map, use_case_stats_map, _, _ = self._summarize_ui_tasks(ui_tasks)

        top_website_name = "unknown"
        top_website_score = 0.0
        top_website_tasks = 0
        top_website_entry = max(
            website_stats_map.items(),
            key=lambda item: (
                (item[1]["score_sum"] / item[1]["tasks"]) if item[1]["tasks"] else 0.0
            ),
            default=None,
        )
        if top_website_entry:
            name, values = top_website_entry
            top_website_name = name
            top_website_score = (
                (values["score_sum"] / values["tasks"]) if values["tasks"] else 0.0
            )
            top_website_tasks = int(values["tasks"])
        elif websites:
            top_candidate = max(websites, key=lambda w: w.score, default=None)
            if top_candidate:
                top_website_name = top_candidate.website
                top_website_score = top_candidate.score
                top_website_tasks = top_candidate.tasks

        top_use_case_name = "unknown"
        top_use_case_score = 0.0
        top_use_case_tasks = 0
        top_use_case_entry = max(
            use_case_stats_map.items(),
            key=lambda item: (
                (item[1]["score_sum"] / item[1]["tasks"]) if item[1]["tasks"] else 0.0
            ),
            default=None,
        )
        if top_use_case_entry:
            name, values = top_use_case_entry
            top_use_case_name = name
            top_use_case_score = (
                (values["score_sum"] / values["tasks"]) if values["tasks"] else 0.0
            )
            top_use_case_tasks = int(values["tasks"])
        elif ui_tasks:
            candidate = ui_tasks[0]
            top_use_case_name = candidate.useCase or "unknown"
            top_use_case_score = candidate.score or 0.0
            top_use_case_tasks = 1

        recent_activity = [
            RecentActivity(
                timestamp=_ts_to_iso(context.run.started_at) or "",
                action="Run started",
                details="Agent run initiated",
            )
        ]

        round_id_value = context.round.round_number
        if round_id_value is None:
            round_id_value = _round_id_to_int(context.round.validator_round_id)

        return Summary(
            runId=context.run.agent_run_id,
            agentId=agent_identifier,
            agentUid=agent_uid,
            agentHotkey=agent_hotkey,
            agentName=agent_name,
            roundId=round_id_value or 0,
            validatorId=_format_validator_id(_get_validator_uid_from_context(context) or 0),
            startTime=_ts_to_iso(context.run.started_at) or "",
            endTime=_ts_to_iso(context.run.ended_at),
            status=self._run_status(context),
            overallScore=overall_score,
            totalTasks=total_tasks,
            successfulTasks=success_count,
            failedTasks=failed_tasks,
            duration=_safe_int(
                (context.run.ended_at or context.run.started_at or 0)
                - (context.run.started_at or 0)
            ),
            ranking=context.run.rank or 0,
            topPerformingWebsite=TopPerformingWebsite(
                website=top_website_name,
                score=top_website_score,
                tasks=top_website_tasks,
            ),
            topPerformingUseCase=TopPerformingUseCase(
                useCase=top_use_case_name,
                score=top_use_case_score,
                tasks=top_use_case_tasks,
            ),
            recentActivity=recent_activity,
        )

    def _build_run_summary(
        self, context: AgentRunContext, consensus_score: Optional[float] = None
    ) -> Dict[str, object]:
        run_model = context.run

        total_tasks = (
            getattr(run_model, "n_tasks_total", None)
            or run_model.total_tasks
            or len(context.tasks)
        )

        success_tasks = (
            getattr(run_model, "n_tasks_completed", None)
            or run_model.success_tasks
            or 0
        )
        failed_tasks = (
            getattr(run_model, "n_tasks_failed", None) or run_model.failed_tasks or 0
        )

        if success_tasks == 0 and context.evaluations:
            success_tasks = sum(
                1
                for evaluation in context.evaluations
                if getattr(evaluation, 'eval_score', getattr(evaluation, 'final_score', 0.0)) >= 0.5
            )

        if failed_tasks == 0 and total_tasks:
            failed_tasks = max(total_tasks - success_tasks, 0)

        # Prefer consensus_score if available, otherwise fall back to average_score or computed score
        if consensus_score is not None:
            average_score = float(consensus_score)
        else:
            average_score = (
                getattr(run_model, "avg_eval_score", None) or run_model.average_score
            )
            if average_score is None:
                average_score = self._compute_average_score(context.evaluations)
            average_score = float(average_score or 0.0)

        average_evaluation_time = self._average_evaluation_time(context)

        validator_name, validator_image = self._resolve_validator_identity(context)
        agent_name, _, agent_uid, agent_hotkey, agent_identifier, _ = (
            self._resolve_agent_identity(context)
        )
        success_count = success_tasks
        success_rate = (success_count / total_tasks * 100.0) if total_tasks else 0.0
        overall_score = _safe_int(average_score * 100)

        round_id_value = context.round.round_number
        if round_id_value is None:
            round_id_value = _round_id_to_int(context.round.validator_round_id)

        duration_sec = None
        if getattr(run_model, "elapsed_sec", None) not in (None, 0):
            duration_sec = run_model.elapsed_sec
        if duration_sec is None:
            duration_sec = (run_model.ended_at or run_model.started_at or 0) - (
                run_model.started_at or 0
            )

        # Compute unique websites involved in this run only (based on
        # tasks that have a solution and/or evaluation result).
        websites_count = 0
        try:
            relevant_task_ids = set()
            try:
                relevant_task_ids.update(
                    result.task_id for result in (context.evaluations or [])
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                relevant_task_ids.update(
                    solution.task_id for solution in (context.task_solutions or [])
                )
            except Exception:  # noqa: BLE001
                pass

            if relevant_task_ids:
                task_by_id = {
                    getattr(t, "task_id", None): t for t in (context.tasks or [])
                }
                hosts = set()
                for task_id in relevant_task_ids:
                    task = task_by_id.get(task_id)
                    if not task:
                        continue
                    website = None
                    if isinstance(getattr(task, "relevant_data", None), dict):
                        website = task.relevant_data.get("website")
                    if not website:
                        website = getattr(task, "url", None)
                    hosts.add(_map_website_port_to_name(website))
                websites_count = len(hosts)
        except Exception:  # noqa: BLE001
            websites_count = 0

        return {
            "runId": run_model.agent_run_id,
            "agentId": agent_identifier,
            "agentUid": agent_uid,
            "agentHotkey": agent_hotkey,
            "agentName": agent_name,
            "roundId": round_id_value or 0,
            "validatorId": _format_validator_id(run_model.validator_uid),
            "validatorName": validator_name,
            "validatorImage": validator_image,
            "status": self._run_status(context).value,
            "startTime": _ts_to_iso(run_model.started_at),
            "endTime": _ts_to_iso(run_model.ended_at),
            "totalTasks": int(total_tasks),
            "completedTasks": int(success_tasks),
            "successfulTasks": int(success_count),
            "failedTasks": int(failed_tasks),
            "averageScore": average_score,
            "score": average_score,
            "successRate": success_rate,
            "overallScore": overall_score,
            "ranking": run_model.rank or 0,
            "duration": _safe_int(duration_sec),
            # Provide both keys for UI compatibility
            "websitesCount": websites_count,
            "totalWebsites": websites_count,
            "averageEvaluationTime": (
                round(average_evaluation_time, 3)
                if average_evaluation_time is not None
                else None
            ),
        }

    def _sort_runs(
        self, runs: List[Dict[str, object]], sort_by: str, sort_order: str
    ) -> List[Dict[str, object]]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(
                runs, key=lambda item: item.get(sort_by) or 0, reverse=reverse
            )
        except Exception:  # noqa: BLE001
            return runs

    def _build_websites_and_tasks(
        self,
        context: AgentRunContext,
    ) -> Tuple[List[Website], List[UITask], int]:
        host_stats: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"tasks": 0, "successful": 0, "score_sum": 0.0}
        )
        success_count = 0

        evaluation_map, solution_map, task_map = self._index_results(context)

        relevant_task_ids = set(evaluation_map.keys()) | set(solution_map.keys())
        if not relevant_task_ids:
            relevant_task_ids = set(task_map.keys())

        ui_tasks: List[UITask] = []
        for task_id in sorted(relevant_task_ids):
            ui_task = task_map.get(task_id)
            if ui_task is None:
                continue
            evaluation = evaluation_map.get(task_id)
            eval_score = getattr(evaluation, 'eval_score', getattr(evaluation, 'final_score', 0.0)) if evaluation else 0.0
            success = evaluation is not None and eval_score >= 0.5
            host = _map_website_port_to_name(ui_task.website)

            host_stats_entry = host_stats[host]
            host_stats_entry["tasks"] += 1
            host_stats_entry["score_sum"] += eval_score
            if success:
                host_stats_entry["successful"] += 1
                success_count += 1

            ui_tasks.append(ui_task)

        websites = [
            Website(
                website=host,
                tasks=int(stats["tasks"]),
                successful=int(stats["successful"]),
                failed=int(stats["tasks"] - stats["successful"]),
                score=(stats["score_sum"] / stats["tasks"]) if stats["tasks"] else 0.0,
            )
            for host, stats in host_stats.items()
        ]

        return websites, ui_tasks, success_count

    def _index_results(
        self,
        context: AgentRunContext,
    ) -> Tuple[Dict[str, Evaluation], Dict[str, TaskSolution], Dict[str, UITask]]:
        evaluation_by_task = {
            result.task_id: result for result in context.evaluations
        }
        solution_by_task = {
            solution.task_id: solution for solution in context.task_solutions
        }
        task_map: Dict[str, UITask] = {}

        for task in context.tasks:
            evaluation = evaluation_by_task.get(task.task_id)
            solution = solution_by_task.get(task.task_id)
            task_map[task.task_id] = self._build_ui_task(
                task, solution, evaluation, context.run, context.round
            )

        return evaluation_by_task, solution_by_task, task_map

    def _build_ui_task(
        self,
        task: Task,
        solution: Optional[TaskSolution],
        evaluation: Optional[Evaluation],
        run,
        round_obj: ValidatorRound,
    ) -> UITask:
        eval_score = getattr(evaluation, 'eval_score', getattr(evaluation, 'final_score', 0.0)) if evaluation else 0.0
        status = (
            TaskStatus.COMPLETED
            if evaluation and eval_score >= 0.5
            else TaskStatus.FAILED
        )
        score = eval_score

        # Use evaluation_time directly from the database
        # This is the time the evaluator took to process the task
        duration = 0.0
        if evaluation and evaluation.evaluation_time:
            duration = float(evaluation.evaluation_time)

        logger.debug(
            f"📊 Task {task.task_id}: duration={duration}s (from evaluation_time)"
        )

        actions = []
        if solution and solution.actions:
            for index, action in enumerate(solution.actions):
                # Normalize action type for display (prefer 'input' over ambiguous 'type')
                raw_type = (
                    action.type
                    if hasattr(action, "type")
                    else action.get("type", "action")
                )
                try:
                    type_key = (
                        str(raw_type)
                        .lower()
                        .replace("action", "")
                        .replace("-", "_")
                        .strip()
                    )
                except Exception:
                    type_key = str(raw_type)
                if type_key in {"type", "type_text", "sendkeysiwa"}:
                    type_key = "input"

                # Extract selector and value, ensuring they are strings
                selector_raw = (
                    getattr(action, "attributes", {}).get("selector")
                    if hasattr(action, "attributes")
                    else action.get("attributes", {}).get("selector")
                )
                value_raw = (
                    getattr(action, "attributes", {}).get("value")
                    if hasattr(action, "attributes")
                    else action.get("attributes", {}).get("value")
                )

                # Convert to strings if they're dicts or other non-string types
                selector_str = None
                if selector_raw is not None:
                    if isinstance(selector_raw, str):
                        selector_str = selector_raw
                    elif isinstance(selector_raw, dict):
                        selector_str = json.dumps(selector_raw)
                    else:
                        selector_str = str(selector_raw)

                value_str = None
                if value_raw is not None:
                    if isinstance(value_raw, str):
                        value_str = value_raw
                    elif isinstance(value_raw, dict):
                        value_str = json.dumps(value_raw)
                    else:
                        value_str = str(value_raw)

                actions.append(
                    Action(
                        id=f"{task.task_id}_action_{index}",
                        type=type_key or "action",
                        selector=selector_str,
                        value=value_str,
                        timestamp=_ts_to_iso(run.started_at) or "",
                        duration=float(getattr(action, "duration", 0.0)),
                        success=bool(getattr(action, "success", True)),
                    )
                )

        website = (
            task.relevant_data.get("website")
            if isinstance(task.relevant_data, dict)
            else None
        )
        if not website:
            website = task.url

        # Normalize website to friendly name
        website = _map_website_port_to_name(website)

        use_case = _extract_use_case(task)

        return UITask(
            taskId=task.task_id,
            roundNumber=round_obj.round_number
            or _round_id_to_int(round_obj.validator_round_id),
            website=website,
            useCase=use_case,
            prompt=task.prompt,
            status=status,
            score=score,
            duration=round(duration, 2),  # Keep as float with 2 decimal places
            startTime=_ts_to_iso(run.started_at) or "",
            endTime=_ts_to_iso(run.ended_at),
            actions=actions,
            screenshots=list(getattr(evaluation, "screenshots", []) or []),
            logs=[],
        )

    @staticmethod
    def _average_evaluation_time(context: AgentRunContext) -> Optional[float]:
        durations: List[float] = []
        for result in context.evaluations:
            value = getattr(result, "evaluation_time", None)
            if value is None:
                continue
            try:
                durations.append(abs(float(value)))
            except (TypeError, ValueError):
                continue
        if not durations:
            return None
        return sum(durations) / len(durations)

    @staticmethod
    def _compute_average_score(evaluations: List[Evaluation]) -> float:
        if not evaluations:
            return 0.0
        return sum(getattr(result, 'eval_score', getattr(result, 'final_score', 0.0)) for result in evaluations) / len(
            evaluations
        )

    @staticmethod
    def _run_status(context: AgentRunContext) -> RunStatus:
        if context.run.ended_at:
            return RunStatus.COMPLETED
        return RunStatus.RUNNING if context.run.started_at else RunStatus.PENDING

    @staticmethod
    def _find_validator(context: AgentRunContext):
        validator_uid = _get_validator_uid_from_context(context)
        return next(
            (
                validator
                for validator in context.round.validators
                if validator.uid == validator_uid
            ),
            None,
        )

    @staticmethod
    def _find_miner(context: AgentRunContext):
        if context.round.miners:
            return next(
                (
                    miner
                    for miner in context.round.miners
                    if miner.uid == context.run.miner_uid
                ),
                None,
            )
        return context.run.miner_info

    def _resolve_agent_identity(
        self,
        context: AgentRunContext,
    ) -> Tuple[str, str, Optional[int], Optional[str], str, str]:
        miner = self._find_miner(context)
        agent_uid = getattr(miner, "uid", None)
        if agent_uid is None:
            agent_uid = context.run.miner_uid

        agent_hotkey = getattr(miner, "hotkey", None) or getattr(
            context.run, "miner_hotkey", None
        )

        agent_name = getattr(miner, "agent_name", None) or getattr(miner, "name", None)
        if not agent_name:
            if agent_hotkey:
                agent_name = agent_hotkey
            elif agent_uid is not None:
                agent_name = f"Agent {agent_uid}"
            else:
                agent_name = "Agent"

        agent_image = resolve_agent_image(miner)
        agent_description = (getattr(miner, "description", "") or "") if miner else ""

        identifier = agent_hotkey or (
            f"agent-{agent_uid}" if agent_uid is not None else context.run.agent_run_id
        )

        return (
            agent_name,
            agent_image,
            agent_uid,
            agent_hotkey,
            identifier,
            agent_description,
        )

    def _resolve_validator_identity(self, context: AgentRunContext) -> Tuple[str, str]:
        validator = self._find_validator(context)
        validator_uid = _get_validator_uid_from_context(context)

        validator_info = getattr(context.round, "validator_info", None)
        metadata = (
            get_validator_metadata(validator_uid) if validator_uid is not None else {}
        )

        name_candidates = [
            getattr(validator, "name", None) if validator else None,
            getattr(validator_info, "name", None) if validator_info else None,
            metadata.get("name"),
            f"Validator {validator_uid}" if validator_uid is not None else "Validator",
        ]
        validator_name = next(
            (candidate for candidate in name_candidates if candidate), "Validator"
        )

        image_candidates = [
            getattr(validator, "image_url", None) if validator else None,
            getattr(validator_info, "image_url", None) if validator_info else None,
            metadata.get("image"),
        ]
        existing_image = next(
            (candidate for candidate in image_candidates if candidate), None
        )
        validator_image = resolve_validator_image(
            validator_name, existing=existing_image
        )

        return validator_name, validator_image


def _format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def _format_validator_id(validator_uid: Optional[int]) -> str:
    return (
        f"validator-{validator_uid}"
        if validator_uid is not None
        else "validator-unknown"
    )


def _round_id_to_int(round_id: str) -> int:
    if not round_id:
        return 0
    matches = re.findall(r"\d+", round_id)
    if not matches:
        return 0
    try:
        return int(matches[-1])
    except ValueError:
        return 0


def _parse_identifier(identifier: str) -> int:
    if "-" in identifier:
        identifier = identifier.split("-", 1)[1]
    if "_" in identifier:
        identifier = identifier.split("_", 1)[1]
    return int(identifier)


def _to_timestamp(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _extract_use_case(task: Task) -> str:
    if isinstance(task.use_case, dict):
        return task.use_case.get("name", "unknown")
    if isinstance(task.use_case, str):
        return task.use_case
    return "unknown"
