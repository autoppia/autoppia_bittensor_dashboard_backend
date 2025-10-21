# app/config.py
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Autoppia Leaderboard API"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    TESTING: bool = False

    # SQL Database Configuration
    DATABASE_URL: str = ""
    POSTGRES_USER: str = "autoppia"
    POSTGRES_PASSWORD: str = "password"
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "autoppia_db"

    # Asset handling
    ASSET_BASE_URL: str = "https://dev-infinitewebarena.autoppia.com"

    # AWS / S3 configuration
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_SESSION_TOKEN: Optional[str] = None
    AWS_REGION: str = "eu-west-1"
    AWS_S3_BUCKET: str = ""
    AWS_S3_ENDPOINT_URL: Optional[str] = None
    AWS_S3_GIF_PREFIX: str = "gifs"
    AWS_S3_PUBLIC_BASE_URL: Optional[str] = None

    # Authentication
    API_KEYS: list[str] = ["dev-token-123"]  # replace with real keys or load from vault
    VALIDATOR_AUTH_MESSAGE: str = "I am a honest validator"
    MIN_VALIDATOR_STAKE: float = 50_000.0
    VALIDATOR_NETUID: int = 36
    SUBTENSOR_NETWORK: Optional[str] = None
    SUBTENSOR_ENDPOINT: Optional[str] = None
    VALIDATOR_AUTH_CACHE_TTL: int = 180
    API_CACHE_DISABLED: bool = False
    AUTH_DISABLED: bool = False

    # ---------- Logging configuration (all configurable via env) ----------
    # General app log level
    LOG_LEVEL: str = "WARNING"  # quiet by default

    # Specific library levels
    SQLALCHEMY_LOG_LEVEL: str = "ERROR"  # kills SQL/ORM chatter by default
    BITTENSOR_LOG_LEVEL: str = "WARNING"  # keep to warnings+
    UVICORN_LOG_LEVEL: str = "WARNING"  # quiet server
    UVICORN_ACCESS_LOG: bool = False  # hide access log lines by default

    # File logging
    LOG_TO_FILE: bool = False  # enable file logging
    LOG_FILE_PATH: str = "logs/app.log"  # path to log file

    # Detailed request/response logging
    LOG_REQUEST_BODY: bool = False  # log request bodies
    LOG_RESPONSE_BODY: bool = False  # log response bodies
    # ---------------------------------------------------------------------

    # Overview / validators list behavior
    OVERVIEW_VALIDATORS_LOOKBACK_ROUNDS: int = 2

    # CORS Configuration
    CORS_ORIGINS: list[str] = ["*"]

    # Idempotency Configuration (seconds to keep)
    IDEMPOTENCY_TTL: int = 600

    # Server Configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Ensure required defaults and normalization."""
        # Database default to local Postgres service
        if not self.DATABASE_URL:
            user = quote_plus(self.POSTGRES_USER)
            password = quote_plus(self.POSTGRES_PASSWORD) if self.POSTGRES_PASSWORD else ""
            auth = f"{user}:{password}@" if password else f"{user}@"
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{auth}{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
            )

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

        # Ensure required CORS origins unless wildcard
        if "*" in self.CORS_ORIGINS:
            return

        required_origins = {
            "https://dev-infinitewebarena.autoppia.com",
            "https://infinitewebarena.autoppia.com",
        }
        missing = required_origins.difference(self.CORS_ORIGINS)
        if missing:
            self.CORS_ORIGINS.extend(sorted(missing))


settings = Settings()
