#!/usr/bin/env python3
"""
Fill agent_stats.recent_rounds with historical data from round_snapshots.

This script:
1. Reads all round_snapshots
2. For each agent found, extracts their performance per round
3. Updates agent_stats.recent_rounds with the last 20 rounds
"""
import asyncio
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))

from app.db.session import AsyncSessionLocal
from sqlalchemy import select
from app.db.models import AgentStatsORM, RoundSnapshotORM
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def fill_recent_rounds():
    """Fill recent_rounds for all agents from round_snapshots."""
    async with AsyncSessionLocal() as session:
        # Get all snapshots
        stmt = select(RoundSnapshotORM).order_by(RoundSnapshotORM.round_number)
        snapshots = list(await session.scalars(stmt))
        
        logger.info(f"Found {len(snapshots)} round snapshots")
        
        # Build agent performance data
        agent_rounds: Dict[int, List[dict]] = defaultdict(list)
        
        for snapshot in snapshots:
            round_number = snapshot.round_number
            snapshot_json = snapshot.snapshot_json
            validator_rounds = snapshot_json.get("validatorRounds", [])
            
            if not validator_rounds:
                logger.warning(f"Round {round_number} has no validatorRounds, skipping")
                continue
            
            # Extract agent data from all validators
            agents_in_round = {}
            
            for vr in validator_rounds:
                agent_runs = vr.get("agentEvaluationRuns", [])
                
                for run in agent_runs:
                    miner_uid = run.get("miner_uid")
                    if not miner_uid:
                        continue
                    
                    if miner_uid not in agents_in_round:
                        agents_in_round[miner_uid] = {
                            "round": round_number,
                            "scores": [],
                            "ranks": [],
                            "tasks": 0,
                            "completed": 0,
                        }
                    
                    # Aggregate across validators
                    score = run.get("average_score", 0)
                    rank = run.get("rank")
                    
                    if score is not None:
                        agents_in_round[miner_uid]["scores"].append(score)
                    if rank is not None:
                        agents_in_round[miner_uid]["ranks"].append(rank)
                    
                    agents_in_round[miner_uid]["tasks"] += run.get("total_tasks", 0)
                    agents_in_round[miner_uid]["completed"] += run.get("completed_tasks", 0)
            
            # Calculate averages and add to agent_rounds
            for miner_uid, data in agents_in_round.items():
                avg_score = sum(data["scores"]) / len(data["scores"]) if data["scores"] else 0.0
                best_rank = min(data["ranks"]) if data["ranks"] else None
                
                agent_rounds[miner_uid].append({
                    "round": round_number,
                    "score": round(avg_score, 4),
                    "rank": best_rank,
                    "tasks": data["tasks"],
                    "completed": data["completed"],
                })
        
        logger.info(f"Extracted data for {len(agent_rounds)} agents")
        
        # Update agent_stats
        updated_count = 0
        for agent_uid, rounds_data in agent_rounds.items():
            # Get agent_stats
            stmt = select(AgentStatsORM).where(AgentStatsORM.uid == agent_uid)
            agent_stats = await session.scalar(stmt)
            
            if not agent_stats:
                logger.warning(f"Agent {agent_uid} not found in agent_stats, skipping")
                continue
            
            # Sort by round number (most recent first) and keep last 20
            rounds_data_sorted = sorted(rounds_data, key=lambda x: x["round"], reverse=True)[:20]
            
            # Update recent_rounds
            agent_stats.recent_rounds = rounds_data_sorted
            updated_count += 1
            
            if updated_count % 10 == 0:
                logger.info(f"Updated {updated_count} agents...")
        
        await session.commit()
        
        logger.info(f"✅ Updated recent_rounds for {updated_count} agents")
        
        # Show sample
        if agent_rounds.get(105):
            logger.info(f"\nSample - Agent 105 recent rounds:")
            for r in agent_rounds[105][:5]:
                logger.info(f"  Round {r['round']}: score={r['score']:.3f}, rank={r['rank']}")


if __name__ == "__main__":
    asyncio.run(fill_recent_rounds())


