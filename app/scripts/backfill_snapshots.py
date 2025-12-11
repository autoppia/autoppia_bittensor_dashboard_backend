"""
Backfill snapshots for historical rounds.

NOTE: This functionality is disabled - RoundSnapshotORM and AgentStatsORM models do not exist.

Usage:
    python -m app.scripts.backfill_snapshots
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def backfill_snapshots(max_rounds: int = None):
    """
    Backfill snapshots for all completed rounds that don't have one yet.
    
    NOTE: This functionality is disabled - RoundSnapshotORM model does not exist.
    """
    logger.warning("⚠️ Snapshot materialization is disabled - RoundSnapshotORM model does not exist")
    logger.warning("⚠️ Skipping snapshot backfill - functionality removed")


async def backfill_agent_stats(max_rounds: int = None):
    """
    Backfill agent stats for historical rounds.
    
    NOTE: This functionality is disabled - AgentStatsORM model does not exist.
    """
    logger.warning("⚠️ Agent stats backfill is disabled - AgentStatsORM model does not exist")
    logger.warning("⚠️ Skipping agent stats backfill - functionality removed")


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
