#!/usr/bin/env python3
"""
Shared helpers for database connection setup.
Reads .env with POSTGRES_* vars and builds DATABASE_URL safely.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy.engine import URL

# Load environment variables from .env (project root)
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def get_database_url() -> str:
    """Construct a SQLAlchemy async DSN from POSTGRES_* env vars, safely handling special chars."""
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD", "")
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port_str = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB")

    if not user or not db:
        raise RuntimeError("Missing POSTGRES_USER or POSTGRES_DB in .env")

    try:
        port = int(port_str) if port_str else 5432
    except ValueError as exc:
        raise RuntimeError(f"Invalid POSTGRES_PORT: {port_str!r}") from exc

    url = URL.create(
        drivername="postgresql+asyncpg",
        username=user,
        password=password,  # safe: URL.create handles quoting
        host=host,
        port=port,
        database=db,
    )
    return str(url)
