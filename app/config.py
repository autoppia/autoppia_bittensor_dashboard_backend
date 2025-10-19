from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Autoppia Leaderboard API"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # SQL Database Configuration
    DATABASE_URL: str = ""

    # Asset handling
    ASSET_BASE_URL: str = "https://dev-infinitewebarena.autoppia.com"

    # Authentication
    API_KEYS: list[str] = ["dev-token-123"]  # replace with real keys or load from vault

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
        """Ensure required CORS origins are present."""
        if not self.DATABASE_URL:
            backend_root = Path(__file__).resolve().parents[1]
            db_path = backend_root / "autoppia.db"
            self.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"

        if self.ASSET_BASE_URL:
            self.ASSET_BASE_URL = self.ASSET_BASE_URL.rstrip("/")

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
