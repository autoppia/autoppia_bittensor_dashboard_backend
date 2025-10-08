from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings
import logging
import os

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_mock_client = None


def get_client():
    """Get or create the MongoDB client (real or mock)."""
    global _client, _mock_client
    
    # Check if we're in mock mode
    if os.getenv("USE_MOCK_DB", "false").lower() == "true":
        if _mock_client is None:
            from app.db.mock_mongo import get_mock_client
            _mock_client = get_mock_client()
            logger.info("Using Mock MongoDB for testing")
        return _mock_client
    else:
        if _client is None:
            _client = AsyncIOMotorClient(settings.MONGO_URI)
            logger.info(f"Connected to MongoDB at {settings.MONGO_URI}")
        return _client


def get_db():
    """Get the database instance (real or mock)."""
    client = get_client()
    db_name = settings.MONGO_DB if os.getenv("USE_MOCK_DB", "false").lower() != "true" else "autoppia_test"
    return client[db_name]


async def ensure_indexes():
    """Create all necessary indexes for the collections."""
    db = get_db()
    
    # Skip index creation in mock mode
    if os.getenv("USE_MOCK_DB", "false").lower() == "true":
        logger.info("Skipping index creation in mock mode")
        return
    
    try:
        # Rounds collection - unique constraint on validator_uid + round_id
        await db.rounds.create_index(
            [("validator_info.validator_uid", 1), ("round_id", 1)], 
            unique=True, 
            name="u_round"
        )
        logger.info("Created index for rounds collection")

        # Events collection - compound index for efficient queries
        await db.events.create_index(
            [("validator_info.validator_uid", 1), ("round_id", 1), ("ts", 1)], 
            name="e_vr_ts"
        )
        logger.info("Created index for events collection")

        # Task executions collection - unique constraint on validator_uid + round_id + task_id + miner_uid
        await db.task_executions.create_index(
            [("validator_info.validator_uid", 1), ("round_id", 1), ("task_id", 1), ("miner_info.miner_uid", 1)],
            unique=True, 
            name="u_task_execution"
        )
        logger.info("Created index for task_executions collection")

        # Agent evaluation runs collection - unique constraint on validator_uid + round_id + miner_uid
        await db.agent_evaluation_runs.create_index(
            [("validator_info.validator_uid", 1), ("round_id", 1), ("miner_info.miner_uid", 1)],
            unique=True, 
            name="u_agent_evaluation_run"
        )
        logger.info("Created index for agent_evaluation_runs collection")

        # Tasks collection - unique constraint on task_id
        await db.tasks.create_index(
            [("task_id", 1)], 
            unique=True, 
            name="u_task"
        )
        logger.info("Created index for tasks collection")

        # Optional: Create TTL index for idempotency if using persistent storage
        # await db.idempotency.create_index("created_at", expireAfterSeconds=settings.IDEMPOTENCY_TTL)

    except Exception as e:
        logger.error(f"Error creating indexes: {e}")
        raise


async def close_client():
    """Close the MongoDB client connection."""
    global _client
    if _client:
        _client.close()
        _client = None
        logger.info("Closed MongoDB connection")
