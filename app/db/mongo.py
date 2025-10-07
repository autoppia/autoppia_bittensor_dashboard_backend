from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    """Get or create the MongoDB client."""
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.MONGO_URI)
        logger.info(f"Connected to MongoDB at {settings.MONGO_URI}")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    """Get the database instance."""
    return get_client()[settings.MONGO_DB]


async def ensure_indexes():
    """Create all necessary indexes for the collections."""
    db = get_db()
    
    try:
        # Rounds collection - unique constraint on validator_uid + round_id
        await db.rounds.create_index(
            [("validator_uid", 1), ("round_id", 1)], 
            unique=True, 
            name="u_round"
        )
        logger.info("Created index for rounds collection")

        # Events collection - compound index for efficient queries
        await db.events.create_index(
            [("validator_uid", 1), ("round_id", 1), ("ts", 1)], 
            name="e_vr_ts"
        )
        logger.info("Created index for events collection")

        # Task runs collection - unique constraint on validator_uid + round_id + task_id + miner_uid
        await db.task_runs.create_index(
            [("validator_uid", 1), ("round_id", 1), ("task_id", 1), ("miner_uid", 1)],
            unique=True, 
            name="u_task_run"
        )
        logger.info("Created index for task_runs collection")

        # Agent runs collection - unique constraint on validator_uid + round_id + miner_uid
        await db.agent_runs.create_index(
            [("validator_uid", 1), ("round_id", 1), ("miner_uid", 1)],
            unique=True, 
            name="u_agent_run"
        )
        logger.info("Created index for agent_runs collection")

        # Weights collection - unique constraint on validator_uid + round_id
        await db.weights.create_index(
            [("validator_uid", 1), ("round_id", 1)], 
            unique=True, 
            name="u_weights"
        )
        logger.info("Created index for weights collection")

        # Round results collection - unique constraint on validator_uid + round_id
        await db.round_results.create_index(
            [("validator_uid", 1), ("round_id", 1)], 
            unique=True, 
            name="u_round_results"
        )
        logger.info("Created index for round_results collection")

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
