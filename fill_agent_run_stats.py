#!/usr/bin/env python3
"""
Fill agent_evaluation_runs.stats_json by calling the stats endpoint for each run.

This precalculates stats so future requests are instant (0.01s vs 1.6s).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.db.session import AsyncSessionLocal
from sqlalchemy import select, update
from app.db.models import AgentEvaluationRunORM
from app.services.ui.agent_runs_service import AgentRunsService
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def fill_stats_for_runs(limit: int = None):
    """
    Fill stats_json for all agent runs by calculating stats once.
    
    Args:
        limit: Maximum number of runs to process (None = all)
    """
    async with AsyncSessionLocal() as session:
        # Get all runs without stats_json
        stmt = select(AgentEvaluationRunORM).where(
            AgentEvaluationRunORM.stats_json == None
        ).order_by(AgentEvaluationRunORM.id.desc())
        
        if limit:
            stmt = stmt.limit(limit)
        
        runs = list(await session.scalars(stmt))
        
        logger.info(f"Found {len(runs)} runs without stats_json")
        
        if not runs:
            logger.info("✅ All runs already have stats_json")
            return
        
        service = AgentRunsService(session)
        
        processed = 0
        failed = 0
        
        for run in runs:
            try:
                # Calculate stats
                stats = await service.get_statistics(run.agent_run_id)
                
                if stats:
                    # Convert to dict
                    stats_dict = stats.model_dump(mode='json') if hasattr(stats, 'model_dump') else stats
                    
                    # Update the run
                    run.stats_json = stats_dict
                    processed += 1
                    
                    if processed % 10 == 0:
                        logger.info(f"Processed {processed}/{len(runs)} runs...")
                        await session.commit()
                else:
                    logger.warning(f"No stats for run {run.agent_run_id}")
                    failed += 1
                    
            except Exception as e:
                logger.error(f"Failed to process run {run.agent_run_id}: {e}")
                failed += 1
                continue
        
        await session.commit()
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Stats fill complete!")
        logger.info(f"  Processed: {processed}")
        logger.info(f"  Failed:    {failed}")
        logger.info(f"  Total:     {len(runs)}")
        logger.info(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Fill stats_json for agent runs')
    parser.add_argument('--limit', type=int, default=None, help='Maximum number of runs to process')
    args = parser.parse_args()
    
    asyncio.run(fill_stats_for_runs(args.limit))

