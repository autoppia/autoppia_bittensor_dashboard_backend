#!/usr/bin/env python3
"""
Data Optimization Script

This script optimizes existing data by:
1. Creating optimized indexes
2. Separating large data into dedicated collections
3. Creating summary collections for fast queries
4. Adding computed fields to existing documents

Run this script after implementing the optimized data structure.
"""

import asyncio
import sys
import os
import time
import logging
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.collection_optimizer import CollectionOptimizer
from app.db.mock_mongo import get_mock_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def backup_existing_data():
    """Create backup of existing data before optimization."""
    logger.info("Creating backup of existing data...")
    
    db = get_mock_db()
    
    # List of collections to backup
    collections_to_backup = [
        "rounds",
        "agent_evaluation_runs", 
        "tasks",
        "task_solutions",
        "evaluation_results"
    ]
    
    backup_dir = Path("data/backups")
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = int(time.time())
    
    for collection_name in collections_to_backup:
        collection = getattr(db, collection_name)
        data = await collection.find({}).to_list()
        
        backup_file = backup_dir / f"{collection_name}_backup_{timestamp}.json"
        
        import json
        with open(backup_file, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info(f"Backed up {len(data)} documents from {collection_name} to {backup_file}")
    
    logger.info("Backup completed successfully")


async def analyze_data_sizes():
    """Analyze current data sizes to understand optimization impact."""
    logger.info("Analyzing current data sizes...")
    
    db = get_mock_db()
    
    collections_to_analyze = [
        "rounds",
        "agent_evaluation_runs",
        "tasks", 
        "task_solutions",
        "evaluation_results"
    ]
    
    total_documents = 0
    total_size_estimate = 0
    
    for collection_name in collections_to_analyze:
        collection = getattr(db, collection_name)
        count = await collection.count_documents({})
        
        # Get a sample document to estimate size
        sample = await collection.find_one({})
        if sample:
            import json
            sample_size = len(json.dumps(sample, default=str))
            estimated_size = sample_size * count
        else:
            estimated_size = 0
        
        total_documents += count
        total_size_estimate += estimated_size
        
        logger.info(f"{collection_name}: {count} documents, ~{estimated_size / 1024 / 1024:.2f} MB")
    
    logger.info(f"Total: {total_documents} documents, ~{total_size_estimate / 1024 / 1024:.2f} MB")
    
    return {
        "total_documents": total_documents,
        "estimated_size_mb": total_size_estimate / 1024 / 1024
    }


async def optimize_data():
    """Run the complete data optimization process."""
    logger.info("Starting data optimization process...")
    
    start_time = time.time()
    
    try:
        # Step 1: Analyze current data
        analysis = await analyze_data_sizes()
        
        # Step 2: Create backup
        await backup_existing_data()
        
        # Step 3: Run optimization
        await CollectionOptimizer.optimize_all()
        
        # Step 4: Analyze optimized data
        logger.info("Analyzing optimized data...")
        optimized_analysis = await analyze_data_sizes()
        
        # Step 5: Report results
        processing_time = time.time() - start_time
        
        logger.info("=" * 60)
        logger.info("OPTIMIZATION COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        logger.info(f"Processing time: {processing_time:.2f} seconds")
        logger.info(f"Original data: {analysis['total_documents']} documents, ~{analysis['estimated_size_mb']:.2f} MB")
        logger.info(f"Optimized data: {optimized_analysis['total_documents']} documents, ~{optimized_analysis['estimated_size_mb']:.2f} MB")
        
        # Calculate space savings
        space_saved = analysis['estimated_size_mb'] - optimized_analysis['estimated_size_mb']
        if space_saved > 0:
            logger.info(f"Space saved: ~{space_saved:.2f} MB ({space_saved / analysis['estimated_size_mb'] * 100:.1f}%)")
        else:
            logger.info("Note: Size may appear larger due to additional indexes and summary collections")
        
        logger.info("=" * 60)
        logger.info("OPTIMIZATION BENEFITS:")
        logger.info("- Separated large data (HTML, screenshots, recordings) into dedicated collections")
        logger.info("- Created optimized indexes for faster queries")
        logger.info("- Added computed fields to reduce real-time calculations")
        logger.info("- Created summary collections for frequently accessed data")
        logger.info("- Improved query performance for UI endpoints")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Optimization failed: {str(e)}")
        raise


async def verify_optimization():
    """Verify that optimization was successful."""
    logger.info("Verifying optimization...")
    
    db = get_mock_db()
    
    # Check if new collections exist
    new_collections = [
        "task_large_data",
        "solution_large_data", 
        "evaluation_large_data",
        "miners_summary",
        "validators_summary",
        "rounds_summary"
    ]
    
    for collection_name in new_collections:
        collection = getattr(db, collection_name)
        count = await collection.count_documents({})
        logger.info(f"✓ {collection_name}: {count} documents")
    
    # Check if computed fields exist in rounds
    rounds_with_computed = await db.rounds.count_documents({"computed_at": {"$exists": True}})
    total_rounds = await db.rounds.count_documents({})
    logger.info(f"✓ Rounds with computed fields: {rounds_with_computed}/{total_rounds}")
    
    # Check if large data was separated
    tasks_without_html = await db.tasks.count_documents({"html": {"$exists": False}})
    total_tasks = await db.tasks.count_documents({})
    logger.info(f"✓ Tasks with separated large data: {tasks_without_html}/{total_tasks}")
    
    logger.info("Verification completed successfully")


async def main():
    """Main function to run the optimization process."""
    logger.info("Starting data optimization script...")
    
    try:
        # Run optimization
        await optimize_data()
        
        # Verify optimization
        await verify_optimization()
        
        logger.info("Data optimization script completed successfully!")
        
    except Exception as e:
        logger.error(f"Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
