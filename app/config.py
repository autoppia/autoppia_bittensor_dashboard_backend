# app/config.py
from __future__ import annotations

import os
from typing import Any, ClassVar, Optional
from urllib.parse import quote_plus

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env early to determine environment mode
load_dotenv()


# ═══════════════════════════════════════════════════════════════════════════
# ENVIRONMENT MODE (local, development, production)
# ═══════════════════════════════════════════════════════════════════════════
def _str_to_bool(value: str) -> bool:
    """Convert string to boolean."""
    return value.lower().strip() in ("true", "1", "yes", "on")


ENVIRONMENT = os.getenv("ENVIRONMENT", "local").lower().strip()

# Validate environment
if ENVIRONMENT not in ("local", "development", "production"):
    raise ValueError(
        f"Invalid ENVIRONMENT: {ENVIRONMENT}. Must be 'local', 'development', or 'production'"
    )

# TESTING mode: Independent of ENVIRONMENT
# TESTING=true → use testing round config (ROUND_SIZE_EPOCHS=0.347)
# TESTING=false → use production round config (ROUND_SIZE_EPOCHS=3.0)
# This allows running in production environment but with testing round sizes
_legacy_testing = os.getenv("TESTING")
if _legacy_testing is not None:
    TESTING_MODE = _str_to_bool(_legacy_testing)
else:
    # If TESTING not set, default based on ENVIRONMENT (backward compatibility)
    TESTING_MODE = ENVIRONMENT in ("local", "development")


# ═══════════════════════════════════════════════════════════════════════════
# HELPER: Get environment-specific variable
# ═══════════════════════════════════════════════════════════════════════════
def _env_var(base_name: str, default: Any = None) -> Any:
    """
    Get environment variable with suffix based on current ENVIRONMENT.

    Example:
        ENVIRONMENT=local → POSTGRES_USER_LOCAL
        ENVIRONMENT=development → POSTGRES_USER_DEVELOPMENT
        ENVIRONMENT=production → POSTGRES_USER_PRODUCTION

    Fallback order:
    1. {base_name}_{ENVIRONMENT.upper()}  (e.g., POSTGRES_USER_LOCAL)
    2. {base_name}                         (e.g., POSTGRES_USER)
    3. default parameter
    """
    env_suffix = ENVIRONMENT.upper()
    specific_var = f"{base_name}_{env_suffix}"

    # Try specific var first (e.g., POSTGRES_USER_LOCAL)
    value = os.getenv(specific_var)
    if value is not None:
        return value

    # Fallback to generic var (e.g., POSTGRES_USER)
    value = os.getenv(base_name)
    if value is not None:
        return value

    # Use default
    return default


class Settings(BaseSettings):
    APP_NAME: str = os.getenv("APP_NAME", "Autoppia Leaderboard API")
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = _str_to_bool(os.getenv("DEBUG", "false"))
    ENVIRONMENT: str = ENVIRONMENT  # Use the pre-computed value

    # ═══════════════════════════════════════════════════════════════════════════
    # DATABASE CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    # Reads from .env with environment suffix:
    # POSTGRES_USER_LOCAL, POSTGRES_USER_DEVELOPMENT, POSTGRES_USER_PRODUCTION
    DATABASE_URL: str = ""
    POSTGRES_USER: str = _env_var("POSTGRES_USER", "autoppia_user")
    POSTGRES_PASSWORD: str = _env_var("POSTGRES_PASSWORD", "password")
    POSTGRES_HOST: str = _env_var("POSTGRES_HOST", "127.0.0.1")
    POSTGRES_PORT: int = int(_env_var("POSTGRES_PORT", "5432"))
    POSTGRES_DB: str = _env_var("POSTGRES_DB", "autoppia_db")

    # Asset handling
    ASSET_BASE_URL: str = "https://infinitewebarena.autoppia.com"

    # ═══════════════════════════════════════════════════════════════════════════
    # ROUND CONFIGURATION (chain-derived, matches subnet validator/config.py)
    # ═══════════════════════════════════════════════════════════════════════════
    # Reads from .env with environment suffix:
    # ROUND_SIZE_EPOCHS_LOCAL, ROUND_SIZE_EPOCHS_DEVELOPMENT, etc.
    # 
    # TESTING mode: Use same ROUND_SIZE_EPOCHS as validator (0.347 for testing, 3.0 for production)
    # This ensures round_number calculation matches between backend and validator
    # When TESTING=true: ROUND_SIZE_EPOCHS=0.347 (matches validator testing mode)
    # When TESTING=false: ROUND_SIZE_EPOCHS=3.0 (matches validator production mode)
    if TESTING_MODE:
        # Testing mode: Short rounds (~25 minutes) - matches validator TESTING=true
        ROUND_SIZE_EPOCHS: float = float(_env_var("ROUND_SIZE_EPOCHS", "0.347"))
    else:
        # Production mode: Long rounds (~4.8 hours) - matches validator TESTING=false
        ROUND_SIZE_EPOCHS: float = float(_env_var("ROUND_SIZE_EPOCHS", "3.0"))
    BLOCKS_PER_EPOCH: int = int(_env_var("BLOCKS_PER_EPOCH", "360"))
    DZ_STARTING_BLOCK: int = int(_env_var("DZ_STARTING_BLOCK", "7084250"))
    SEASON_SIZE_EPOCHS: float = float(_env_var("SEASON_SIZE_EPOCHS", "280.0"))

    # Chain state
    CHAIN_BLOCK_CACHE_TTL_SECONDS: int = 15 * 60
    CHAIN_BLOCK_TIME_SECONDS: int = 12

    # Scoring weights (must stay in sync with validator defaults)
    # If env vars are not set, fallback to validator defaults: 0.995 and 0.005
    EVAL_SCORE_WEIGHT: float = float(_env_var("EVAL_SCORE_WEIGHT", "0.995"))
    TIME_WEIGHT: float = float(_env_var("TIME_WEIGHT", "0.005"))

    # ═══════════════════════════════════════════════════════════════════════════
    # ALPHA EMISSION CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    # Alpha emission per epoch (148.0 is the standard emission rate)
    # Alpha earned per round = ALPHA_EMISSION_PER_EPOCH * round_epochs * weight
    # To convert to TAO: tao = alpha * subnet_price
    ALPHA_EMISSION_PER_EPOCH: float = float(_env_var("ALPHA_EMISSION_PER_EPOCH", "148.0"))

    # ═══════════════════════════════════════════════════════════════════════════
    # BITTENSOR CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    # Burn UID: The UID used for burning tokens/rewards in Bittensor
    # This UID should never have agent_runs and should be excluded from all listings
    BURN_UID: int = int(_env_var("BURN_UID", "5"))

    # Miner image host allowlist and blocked asset
    MINER_IMAGE_ALLOWED_HOSTS: list[str] = [
        "infinitewebarena.autoppia.com",
        "dev-infinitewebarena.autoppia.com",
    ]
    BLOCKED_IMAGE_PATH: str = "/blocked.png"

    # AWS / S3 configuration
    # Reads from .env with environment suffix:
    # AWS_S3_BUCKET_LOCAL, AWS_S3_BUCKET_DEVELOPMENT, AWS_S3_BUCKET_PRODUCTION
    AWS_ACCESS_KEY_ID: Optional[str] = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: Optional[str] = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_SESSION_TOKEN: Optional[str] = os.getenv("AWS_SESSION_TOKEN")
    AWS_REGION: str = os.getenv("AWS_REGION", "eu-west-1")
    AWS_S3_BUCKET: str = _env_var("AWS_S3_BUCKET", "")
    AWS_S3_ENDPOINT_URL: Optional[str] = os.getenv("AWS_S3_ENDPOINT_URL")
    AWS_S3_GIF_PREFIX: str = os.getenv("AWS_S3_GIF_PREFIX", "gifs")
    AWS_S3_PUBLIC_BASE_URL: Optional[str] = _env_var("AWS_S3_PUBLIC_BASE_URL", "")

    # Authentication
    # Reads from .env with environment suffix:
    # MIN_VALIDATOR_STAKE_LOCAL, AUTH_DISABLED_LOCAL, etc.
    VALIDATOR_AUTH_MESSAGE: str = "I am a honest validator"
    MIN_VALIDATOR_STAKE: float = float(_env_var("MIN_VALIDATOR_STAKE", "0.0"))
    VALIDATOR_NETUID: int = 36
    SUBTENSOR_NETWORK: Optional[str] = os.getenv("SUBTENSOR_NETWORK")
    # Back-compat / alias envs (preferred names many users expect)
    BITTENSOR_NETWORK: Optional[str] = os.getenv("BITTENSOR_NETWORK")
    # Common typo alias to reduce friction
    ITTENSOR_NETWORK: Optional[str] = os.getenv("ITTENSOR_NETWORK")
    VALIDATOR_AUTH_CACHE_TTL: int = 180
    API_CACHE_DISABLED: bool = _str_to_bool(_env_var("API_CACHE_DISABLED", "false"))
    AUTH_DISABLED: bool = _str_to_bool(_env_var("AUTH_DISABLED", "false"))

    # ---------- Logging configuration (all configurable via env) ----------
    # General app log level
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "WARNING")

    # Specific library levels
    SQLALCHEMY_LOG_LEVEL: str = os.getenv("SQLALCHEMY_LOG_LEVEL", "ERROR")
    BITTENSOR_LOG_LEVEL: str = os.getenv("BITTENSOR_LOG_LEVEL", "WARNING")
    UVICORN_LOG_LEVEL: str = os.getenv("UVICORN_LOG_LEVEL", "WARNING")
    UVICORN_ACCESS_LOG: bool = _str_to_bool(os.getenv("UVICORN_ACCESS_LOG", "false"))

    # File logging
    LOG_TO_FILE: bool = _str_to_bool(os.getenv("LOG_TO_FILE", "false"))
    LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", "logs/app.log")

    # Detailed request/response logging
    LOG_REQUEST_BODY: bool = _str_to_bool(os.getenv("LOG_REQUEST_BODY", "false"))
    LOG_RESPONSE_BODY: bool = _str_to_bool(os.getenv("LOG_RESPONSE_BODY", "false"))
    # ---------------------------------------------------------------------

    # Overview / validators list behavior
    OVERVIEW_VALIDATORS_LOOKBACK_ROUNDS: int = 2

    # CORS Configuration
    # Prefer explicit origins to support credentials; fallback to wildcard in local env
    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://dev-infinitewebarena.autoppia.com",
        "https://infinitewebarena.autoppia.com",
        "https://devdeviwa.autoppia.com",  # Frontend development
        "https://dev-api-leaderboard.autoppia.com",  # Dev API frontend
        "https://api-leaderboard.autoppia.com",  # Prod API frontend
    ]
    # Optional regex to allow subdomains (e.g., all *.autoppia.com)
    # Default regex allows all HTTPS subdomains of autoppia.com
    CORS_ALLOW_ORIGIN_REGEX: Optional[str] = os.getenv(
        "CORS_ALLOW_ORIGIN_REGEX",
        r"https://.*\.autoppia\.com",
    )

    # Idempotency Configuration (seconds to keep)
    IDEMPOTENCY_TTL: int = int(os.getenv("IDEMPOTENCY_TTL", "600"))

    # ═══════════════════════════════════════════════════════════════════════════
    # REDIS CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    REDIS_ENABLED: bool = _str_to_bool(_env_var("REDIS_ENABLED", "true"))
    REDIS_HOST: str = _env_var("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(_env_var("REDIS_PORT", "6379"))
    REDIS_DB: int = int(_env_var("REDIS_DB", "0"))
    # Redis password - reads from REDIS_PASSWORD_{ENVIRONMENT}
    redis_password_raw: ClassVar[str] = _env_var("REDIS_PASSWORD", "")
    REDIS_PASSWORD: Optional[str] = redis_password_raw if redis_password_raw else None
    REDIS_SOCKET_TIMEOUT: int = int(_env_var("REDIS_SOCKET_TIMEOUT", "2"))
    REDIS_SOCKET_CONNECT_TIMEOUT: int = int(
        _env_var("REDIS_SOCKET_CONNECT_TIMEOUT", "2")
    )
    # Default TTL for completed/immutable rounds and tasks (7 days)
    REDIS_FINAL_DATA_TTL: int = int(
        _env_var("REDIS_FINAL_DATA_TTL", str(7 * 24 * 3600))
    )

    # Server Configuration
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # UI caching toggles
    ENABLE_FINAL_ROUND_CACHE: bool = _str_to_bool(
        os.getenv("ENABLE_FINAL_ROUND_CACHE", "true")
    )
    ENABLE_CURRENT_ROUND_CACHE: bool = _str_to_bool(
        os.getenv("ENABLE_CURRENT_ROUND_CACHE", "true")
    )
    _AGENT_AGGREGATE_REQUIRE_DEFAULT = (
        "true" if ENVIRONMENT == "production" else "false"
    )
    AGENT_AGGREGATES_REQUIRE_WARM_CACHE: bool = _str_to_bool(
        os.getenv(
            "AGENT_AGGREGATES_REQUIRE_WARM_CACHE",
            _AGENT_AGGREGATE_REQUIRE_DEFAULT,
        )
    )

    # Overview cache warmer (precalienta endpoints críticos cada 10 min)
    ENABLE_OVERVIEW_CACHE_WARMER: bool = _str_to_bool(
        os.getenv("ENABLE_OVERVIEW_CACHE_WARMER", "true")
    )

    # Subnet price fallback (alpha → τ). Used when on-chain query fails.
    SUBNET_PRICE_FALLBACK: float = float(_env_var("SUBNET_PRICE_FALLBACK", "0.004178"))

    # Chain block refresher (seconds). If <=0 disables refresher.
    CHAIN_BLOCK_REFRESH_PERIOD: int = int(_env_var("CHAIN_BLOCK_REFRESH_PERIOD", "30"))

    model_config = SettingsConfigDict(
        # env_file disabled because we use load_dotenv() + _env_var() for environment-specific vars
        case_sensitive=True,
        extra="ignore",
    )

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Normalize and validate configuration after Pydantic initialization."""
        # Build DATABASE_URL from components if not explicitly set
        if not self.DATABASE_URL:
            user = quote_plus(self.POSTGRES_USER)
            password = (
                quote_plus(self.POSTGRES_PASSWORD) if self.POSTGRES_PASSWORD else ""
            )
            auth = f"{user}:{password}@" if password else f"{user}@"
            self.DATABASE_URL = f"postgresql+asyncpg://{auth}{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

        # Normalize asset paths
        if self.ASSET_BASE_URL:
            self.ASSET_BASE_URL = self.ASSET_BASE_URL.rstrip("/")

        if self.AWS_S3_GIF_PREFIX:
            self.AWS_S3_GIF_PREFIX = self.AWS_S3_GIF_PREFIX.strip("/") or "gifs"

        # Normalize log level strings
        def _norm(v: Optional[str], default: str) -> str:
            return (v or default).strip().upper()

        self.LOG_LEVEL = _norm(self.LOG_LEVEL, "WARNING")
        self.SQLALCHEMY_LOG_LEVEL = _norm(self.SQLALCHEMY_LOG_LEVEL, "ERROR")
        self.BITTENSOR_LOG_LEVEL = _norm(self.BITTENSOR_LOG_LEVEL, "WARNING")
        self.UVICORN_LOG_LEVEL = _norm(self.UVICORN_LOG_LEVEL, "WARNING")

        # Normalize chain cache settings (ensure sensible positive integers)
        try:
            ttl = int(self.CHAIN_BLOCK_CACHE_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = 900
        self.CHAIN_BLOCK_CACHE_TTL_SECONDS = max(0, ttl)

        try:
            blk = int(self.CHAIN_BLOCK_TIME_SECONDS)
        except (TypeError, ValueError):
            blk = 12
        self.CHAIN_BLOCK_TIME_SECONDS = max(1, blk)

        # Map alias env vars (BITTENSOR_*, legacy typo) to internal SUBTENSOR_NETWORK
        # Only use aliases if SUBTENSOR_NETWORK is not set
        if not self.SUBTENSOR_NETWORK:
            aliases = [
                (self.BITTENSOR_NETWORK or "").strip(),
                (self.ITTENSOR_NETWORK or "").strip(),
                os.getenv("BITTENSOR_ENDPOINT", "").strip(),
            ]
            for candidate in aliases:
                if candidate:
                    self.SUBTENSOR_NETWORK = candidate
                    break

        # Ensure required CORS origins if no regex is provided
        if not self.CORS_ALLOW_ORIGIN_REGEX:
            required_origins = {
                "https://dev-infinitewebarena.autoppia.com",
                "https://infinitewebarena.autoppia.com",
                "https://devdeviwa.autoppia.com",  # IWA Frontend
            }
            # Avoid duplicates and preserve values from env
            existing = set(self.CORS_ORIGINS or [])
            missing = required_origins.difference(existing)
            if missing:
                self.CORS_ORIGINS.extend(sorted(missing))

    @property
    def TESTING(self) -> bool:
        """Backward compatibility: TESTING is True for local or development."""
        return self.ENVIRONMENT in ("local", "development")


settings = Settings()
