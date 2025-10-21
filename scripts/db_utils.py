#!/usr/bin/env python3
"""
Shared helpers for database connection setup.
Reads .env with POSTGRES_* vars and builds DATABASE_URL.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env (project root)
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)

def get_database_url() -> str:
    """Construct SQLAlchemy async DSN from POSTGRES_* env vars."""
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD", "").strip('"')
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB")

    if not all([user, db]):
        raise RuntimeError("Missing POSTGRES_USER or POSTGRES_DB in .env")

    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"
