"""
Service for materializing round snapshots and agent statistics.
"""
import logging
import json
from typing import Any, Dict, List, Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ValidatorRoundORM,
    RoundSnapshotORM,
    AgentStatsORM,
    AgentEvaluationRunORM,
    utcnow,
)
from app.services.ui.rounds_service import RoundsService

logger = logging.getLogger(__name__)


class SnapshotService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    async def materialize_round_snapshot(
        self,
        round_number: int,
        payload: Any = None,  # FinishRoundRequest
    ) -> Optional[RoundSnapshotORM]:
        """
        Materialize a full snapshot of the round for instant retrieval.
        If payload is not provided, it will be reconstructed from the DB.
        """
        try:
            logger.info(f"🔄 Materializing COMPLETE snapshot for round {round_number}...")
            
            # Use RoundsService.get_round() to get the COMPLETE data
            # This includes all validatorRounds with tasks, evaluations, miners, etc.
            # Same data structure that the API endpoint returns
            snapshot_json = await self.rounds_service.get_round(round_number)

            # Calculate size
            json_str = json.dumps(snapshot_json)
            data_size = len(json_str.encode('utf-8'))

            # 4. Save to DB
            existing = await self.session.get(RoundSnapshotORM, round_number)
            if existing:
                existing.snapshot_json = snapshot_json
                existing.data_size_bytes = data_size
                existing.updated_at = utcnow()
                logger.info(f"✅ Updated existing snapshot for round {round_number}")
                return existing
            else:
                new_snapshot = RoundSnapshotORM(
                    round_number=round_number,
                    snapshot_json=snapshot_json,
                    snapshot_version=1,
                    data_size_bytes=data_size,
                )
                self.session.add(new_snapshot)
                logger.info(f"✅ Created new snapshot for round {round_number}")
                return new_snapshot

        except Exception as e:
            logger.error(f"❌ Failed to materialize snapshot for round {round_number}: {e}", exc_info=True)
            return None

    async def update_agent_stats(
        self,
        round_number: int,
        payload: Any = None,  # FinishRoundRequest
    ) -> None:
        """
        Incrementally update agent statistics based on this round's results.
        """
        try:
            logger.info(f"🔄 Updating agent stats for round {round_number}...")
            
            stmt_round = select(ValidatorRoundORM).where(ValidatorRoundORM.round_number == round_number)
            round_row = await self.session.scalar(stmt_round)
            if not round_row:
                return

            # 1. Get all agent runs
            stmt = (
                select(AgentEvaluationRunORM)
                .where(AgentEvaluationRunORM.validator_round_id == round_row.validator_round_id)
                .options(selectinload(AgentEvaluationRunORM.evaluation_results))
            )
            runs = list(await self.session.scalars(stmt))
            
            if not runs:
                logger.warning(f"No agent runs found for round {round_number}")
                return

            # 2. Group by miner UID
            miners_in_round: Dict[int, List[AgentEvaluationRunORM]] = {}
            for run in runs:
                uid = run.miner_uid
                if uid is None:
                    continue
                if uid not in miners_in_round:
                    miners_in_round[uid] = []
                miners_in_round[uid].append(run)

            # Prepare payload data helpers
            winners_map = {}
            agent_runs_meta = {}
            
            if payload:
                for w in payload.winners:
                    w_uid = w.get("miner_uid") if isinstance(w, dict) else getattr(w, "miner_uid", None)
                    if w_uid is not None:
                        winners_map[w_uid] = w.get("rank") if isinstance(w, dict) else getattr(w, "rank", None)
                
                if payload.agent_runs:
                    for ar in payload.agent_runs:
                        agent_runs_meta[ar.agent_run_id] = ar.weight
            else:
                # Fallback to DB meta
                if round_row.meta and "winners" in round_row.meta:
                    for w in round_row.meta["winners"]:
                        w_uid = w.get("miner_uid")
                        if w_uid is not None:
                            winners_map[w_uid] = w.get("rank")
                # Note: weights for specific agent runs might be harder to get without payload
                # but we can try to infer from runs if they have 'weight' field updated
                pass

            # 3. Update each miner
            updated_count = 0
            for uid, miner_runs in miners_in_round.items():
                # Calculate aggregate metrics for this round
                round_scores = []
                round_tasks_total = 0
                round_tasks_completed = 0
                
                for run in miner_runs:
                    # Score
                    if run.evaluation_results:
                        scores = [
                            er.final_score 
                            for er in run.evaluation_results 
                            if er.final_score is not None
                        ]
                        if scores:
                            round_scores.append(sum(scores) / len(scores))
                    
                    # Tasks
                    round_tasks_total += (run.total_tasks or 0)
                    round_tasks_completed += (run.completed_tasks or 0)

                if not round_scores:
                    continue

                round_avg_score = sum(round_scores) / len(round_scores)
                round_best_score = max(round_scores)

                # Get or create AgentStats
                stmt_stats = select(AgentStatsORM).where(AgentStatsORM.uid == uid)
                stats = await self.session.scalar(stmt_stats)

                if not stats:
                    stats = AgentStatsORM(
                        uid=uid,
                        first_seen=utcnow(),
                        avg_score=0.0,
                        best_score=0.0,
                        worst_score=1.0,
                        recent_rounds=[],
                    )
                    self.session.add(stats)

                # Update identity metadata from first run
                first_run = miner_runs[0]
                if first_run.meta:
                    agent_name = first_run.meta.get("agent_name")
                    agent_image = first_run.meta.get("agent_image_url")
                    if agent_name and not stats.name:
                        stats.name = agent_name
                    if agent_image and not stats.image_url:
                        stats.image_url = agent_image
                
                if first_run.miner_hotkey and not stats.hotkey:
                    stats.hotkey = first_run.miner_hotkey
                if first_run.is_sota:
                    stats.is_sota = True

                # Incremental updates
                prev_total = stats.total_rounds
                stats.total_rounds += 1
                stats.total_runs += len(miner_runs)

                if prev_total == 0:
                    stats.avg_score = round_avg_score
                else:
                    # Weighted average update
                    stats.avg_score = (
                        (stats.avg_score * prev_total) + round_avg_score
                    ) / stats.total_rounds

                stats.best_score = max(stats.best_score, round_best_score)
                stats.worst_score = min(stats.worst_score, round_avg_score)

                if round_avg_score >= 0.5:
                    stats.successful_runs += 1

                stats.total_tasks += round_tasks_total
                stats.completed_tasks += round_tasks_completed

                stats.last_seen = utcnow()
                stats.last_round_number = round_number
                stats.updated_at = utcnow()

                # Recent rounds history
                rank = winners_map.get(uid)
                
                # Weight: try to find it in payload meta OR existing run rows
                weight = None
                for run in miner_runs:
                    if run.agent_run_id in agent_runs_meta:
                        weight = agent_runs_meta[run.agent_run_id]
                        break
                    if run.weight is not None:
                        weight = run.weight
                        break

                recent = stats.recent_rounds if isinstance(stats.recent_rounds, list) else []
                recent.append({
                    "round": round_number,
                    "score": round(round_avg_score, 4),
                    "rank": rank,
                    "weight": float(weight) if weight is not None else None
                })
                
                # Keep last 20
                stats.recent_rounds = recent[-20:]
                updated_count += 1
            
            logger.info(f"✅ Updated agent stats for {updated_count} miners in round {round_number}")

        except Exception as e:
            logger.error(f"❌ Failed to update agent stats for round {round_number}: {e}", exc_info=True)

    async def _get_miners_data(self, round_id: str) -> dict:
        try:
            return await self.rounds_service.get_round_miners(
                round_identifier=round_id,
                page=1, limit=1000, sort_by="score", sort_order="desc"
            )
        except Exception:
            return {"miners": [], "total": 0}

    async def _get_validators_data(self, round_id: str) -> dict:
        try:
            return await self.rounds_service.get_round_validators(round_id)
        except Exception:
            return {"validators": [], "total": 0}

    async def _get_statistics_data(self, round_id: str) -> dict:
        try:
            return await self.rounds_service.get_round_statistics(round_id)
        except Exception:
            return {}

