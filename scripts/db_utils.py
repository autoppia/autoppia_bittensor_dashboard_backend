#!/usr/bin/env python3
"""
Database URL resolver for the IWAP CLI.

Preference order (to honor the request "take DB connection from .env"):
1) Values from the project-root .env file (DATABASE_URL or POSTGRES_*),
2) Fallback to app.config.settings.DATABASE_URL (which may read OS env).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from dotenv import dotenv_values

from app.config import settings


def _project_env_path() -> Path:
    # scripts/ is one level below project root
    return Path(__file__).resolve().parents[1] / ".env"


def _dsn_from_env_file() -> str | None:
    env_path = _project_env_path()
    if not env_path.exists():
        return None
    values = dotenv_values(env_path)
    # Prefer DATABASE_URL if it explicitly points to Postgres
    db_url = values.get("DATABASE_URL")
    if db_url and isinstance(db_url, str) and db_url.strip():
        if db_url.startswith("postgres"):
            return db_url.strip()
        # If an unexpected driver is present (e.g., sqlite), ignore in favor of POSTGRES_*
    # Build from POSTGRES_* if present
    user = (values.get("POSTGRES_USER") or "").strip()
    password = (values.get("POSTGRES_PASSWORD") or "").strip().strip('"')
    host = (values.get("POSTGRES_HOST") or "").strip() or "127.0.0.1"
    port = (values.get("POSTGRES_PORT") or "").strip() or "5432"
    db = (values.get("POSTGRES_DB") or "").strip()
    if user and db:
        auth = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
        return f"postgresql+asyncpg://{auth}{host}:{port}/{db}"
    return None


def get_database_url() -> str:
    # 1) Try .env file in project root
    dsn = _dsn_from_env_file()
    if dsn:
        return dsn
    # 2) Fallback to app settings (may reflect OS env)
    return settings.DATABASE_URL
