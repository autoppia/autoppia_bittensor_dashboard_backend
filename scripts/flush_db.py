#!/usr/bin/env python3
"""
Utility to reset the SQLite database used by the Autoppia backend.

The script deletes the underlying SQLite file (if it exists) and recreates the
schema using the project models. By default it honours the DATABASE_URL from
environment variables or .env files, but you can override it with the
``--database-url`` flag when needed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete and recreate the SQLite database for the Autoppia backend.",
    )
    parser.add_argument(
        "--database-url",
        dest="database_url",
        help=(
            "Database URL to reset. Defaults to DATABASE_URL env var or the backend "
            "configuration fallback."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt.",
    )
    return parser.parse_args()


async def flush_database(database_url: str, assume_yes: bool) -> None:
    """Remove the SQLite file and recreate the schema."""
    try:
        url = make_url(database_url)
    except ArgumentError as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Invalid DATABASE_URL: {exc}") from exc

    backend = url.get_backend_name()
    if backend != "sqlite":
        raise SystemExit(
            f"This utility only works with SQLite connections. Current backend: {backend}"
        )

    db_path = url.database
    if not db_path or db_path == ":memory:":
        raise SystemExit("Cannot reset in-memory SQLite database.")

    file_path = Path(db_path).expanduser()
    if not file_path.is_absolute():
        # Resolve relative paths against the backend project root for consistency.
        project_root = Path(__file__).resolve().parents[1]
        file_path = (project_root / file_path).resolve()

    if not assume_yes:
        response = input(f"This will delete {file_path}. Continue? [y/N]: ").strip().lower()
        if response not in {"y", "yes"}:
            print("Aborted.")
            return

    # Import after potential DATABASE_URL override to reflect the intended target.
    from app.db.session import engine, init_db  # type: ignore

    # Ensure there are no open connections before deleting the file.
    await engine.dispose()

    if file_path.exists():
        file_path.unlink()
        print(f"Removed {file_path}")
    else:
        print(f"{file_path} does not exist. Continuing with clean initialisation.")

    file_path.parent.mkdir(parents=True, exist_ok=True)

    await init_db()
    print("Database schema recreated.")


def main() -> int:
    args = parse_args()

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    from app.config import settings  # type: ignore

    database_url = settings.DATABASE_URL
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured.")

    asyncio.run(flush_database(database_url, assume_yes=args.yes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
