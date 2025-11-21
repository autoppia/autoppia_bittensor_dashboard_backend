"""
Backfill snapshots for historical rounds.

This script materializes snapshots for all completed rounds that don't have one yet.
Run once after creating the round_snapshots and agent_stats tables.

Usage:
    python -m app.scripts.backfill_snapshots
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.db.models import ValidatorRoundORM, RoundSnapshotORM

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def backfill_snapshots(max_rounds: int = None):
    """
    Backfill snapshots for all completed rounds that don't have one yet.
    
    Args:
        max_rounds: Maximum number of rounds to process (None = all)
    """
    # Import here to avoid circular imports
    from app.api.validator.validator_round import _materialize_round_snapshot, FinishRoundRequest
    
    async with AsyncSessionLocal() as session:
        # Get all completed rounds
        stmt = (
            select(ValidatorRoundORM)
            .where(ValidatorRoundORM.ended_at != None)
            .where(ValidatorRoundORM.round_number != None)
            .order_by(ValidatorRoundORM.round_number.desc())
        )
        
        if max_rounds:
            stmt = stmt.limit(max_rounds)
        
        rounds = list(await session.scalars(stmt))
        
        logger.info(f"Found {len(rounds)} completed rounds")
        
        processed = 0
        skipped = 0
        failed = 0
        
        for round_row in rounds:
            round_number = round_row.round_number
            
            # Check if snapshot already exists
            existing = await session.get(RoundSnapshotORM, round_number)
            if existing:
                logger.info(f"✅ Round {round_number} already has snapshot, skipping")
                skipped += 1
                continue
            
            # Create mock payload from round data
            winners = []
            weights = {}
            
            # Extract winners from meta if available
            if round_row.meta:
                if "winners" in round_row.meta:
                    winners = round_row.meta["winners"]  # Just use the dict as-is
                
                if "weights" in round_row.meta:
                    weights = round_row.meta["weights"]
            
            payload = FinishRoundRequest(
                status=round_row.status or "completed",
                winners=winners,
                winner_scores=[],
                weights=weights,
                ended_at=round_row.ended_at or 0.0,
                summary=round_row.summary or {},
                agent_runs=[],
            )
            
            try:
                await _materialize_round_snapshot(session, round_row, payload)
                # Commit after each round
                await session.commit()
                
                logger.info(f"✅ Materialized snapshot for round {round_number}")
                processed += 1
                
            except Exception as e:
                logger.error(f"❌ Failed to materialize round {round_number}: {e}", exc_info=True)
                await session.rollback()
                failed += 1
                continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Backfill complete!")
        logger.info(f"  Processed: {processed}")
        logger.info(f"  Skipped:   {skipped}")
        logger.info(f"  Failed:    {failed}")
        logger.info(f"  Total:     {len(rounds)}")
        logger.info(f"{'='*60}\n")


async def backfill_agent_stats(max_rounds: int = None):
    """
    Backfill agent stats for historical rounds.
    
    Args:
        max_rounds: Maximum number of rounds to process (None = all)
    """
    # Import here to avoid circular imports
    from app.api.validator.validator_round import _update_agent_stats, FinishRoundRequest
    
    async with AsyncSessionLocal() as session:
        # Get all completed rounds
        stmt = (
            select(ValidatorRoundORM)
            .where(ValidatorRoundORM.ended_at != None)
            .where(ValidatorRoundORM.round_number != None)
            .order_by(ValidatorRoundORM.round_number.asc())  # Oldest first for stats
        )
        
        if max_rounds:
            stmt = stmt.limit(max_rounds)
        
        rounds = list(await session.scalars(stmt))
        
        logger.info(f"Found {len(rounds)} completed rounds for agent stats")
        
        processed = 0
        failed = 0
        
        for round_row in rounds:
            round_number = round_row.round_number
            
            # Create mock payload from round data
            winners = []
            weights = {}
            
            # Extract winners from meta if available
            if round_row.meta:
                if "winners" in round_row.meta:
                    winners = round_row.meta["winners"]  # Just use the dict as-is
                
                if "weights" in round_row.meta:
                    weights = round_row.meta["weights"]
            
            payload = FinishRoundRequest(
                status=round_row.status or "completed",
                winners=winners,
                winner_scores=[],
                weights=weights,
                ended_at=round_row.ended_at or 0.0,
                summary=round_row.summary or {},
                agent_runs=[],
            )
            
            try:
                await _update_agent_stats(session, round_row, payload)
                # Commit after each round
                await session.commit()
                
                logger.info(f"✅ Updated agent stats for round {round_number}")
                processed += 1
                
            except Exception as e:
                logger.error(f"❌ Failed to update agent stats for round {round_number}: {e}", exc_info=True)
                await session.rollback()
                failed += 1
                continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Agent stats backfill complete!")
        logger.info(f"  Processed: {processed}")
        logger.info(f"  Failed:    {failed}")
        logger.info(f"  Total:     {len(rounds)}")
        logger.info(f"{'='*60}\n")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill snapshots and agent stats')
    parser.add_argument(
        '--mode',
        choices=['snapshots', 'stats', 'both'],
        default='both',
        help='What to backfill: snapshots, stats, or both'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='Maximum number of rounds to process (default: all)'
    )
    
    args = parser.parse_args()
    
    logger.info(f"Starting backfill (mode={args.mode}, max_rounds={args.max_rounds})")
    
    if args.mode in ['snapshots', 'both']:
        logger.info("\n" + "="*60)
        logger.info("Backfilling snapshots...")
        logger.info("="*60 + "\n")
        await backfill_snapshots(max_rounds=args.max_rounds)
    
    if args.mode in ['stats', 'both']:
        logger.info("\n" + "="*60)
        logger.info("Backfilling agent stats...")
        logger.info("="*60 + "\n")
        await backfill_agent_stats(max_rounds=args.max_rounds)
    
    logger.info("\n✅ All backfill operations complete!")


if __name__ == "__main__":
    asyncio.run(main())

