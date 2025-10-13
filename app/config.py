from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Autoppia Leaderboard API"
    API_V1_PREFIX: str = "/v1"
    DEBUG: bool = False

    # MongoDB Configuration
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB: str = "leaderboard"

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


settings = Settings()
