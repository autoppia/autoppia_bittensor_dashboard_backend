#!/usr/bin/env python3
"""
Utilities to reset the SQLite database used for seeding scenarios.

This module provides functionality to flush and reinitialize the database.

Usage:
    python -m scripts.flush_db --yes --database-url sqlite+aiosqlite:///autoppia.db
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset the Autoppia backend SQLite database used for seeding.",
    )
    parser.add_argument(
        "--database-url",
        dest="database_url",
        default=None,
        help="Optional explicit DATABASE_URL override.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt.",
    )
    return parser


def _resolve_database_url(candidate: str | None) -> str:
    if candidate:
        os.environ["DATABASE_URL"] = candidate
        return candidate

    from app.config import settings  # local import to respect env overrides

    database_url = settings.DATABASE_URL
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return database_url


async def flush_database(database_url: str, *, assume_yes: bool) -> None:
    """
    Remove the SQLite file backing the service and recreate the schema.

    Args:
        database_url: SQLAlchemy-compatible database URL pointing at SQLite.
        assume_yes: Skip confirmation prompt when True.
    """
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
        project_root = BACKEND_DIR
        file_path = (project_root / file_path).resolve()

    if not assume_yes:
        response = input(f"This will delete {file_path}. Continue? [y/N]: ").strip().lower()
        if response not in {"y", "yes"}:
            print("Aborted.")
            return

    from app.db.session import engine, init_db  # type: ignore  # noqa: E402

    await engine.dispose()

    if file_path.exists():
        file_path.unlink()
        print(f"Removed {file_path}")
    else:
        print(f"{file_path} does not exist. Continuing with clean initialisation.")

    file_path.parent.mkdir(parents=True, exist_ok=True)

    await init_db()
    print("Database schema recreated.")


def flush_seed_database(
    database_url: str | None = None,
    *,
    assume_yes: bool = True,
) -> None:
    """
    Reset the SQLite database backing the backend service.

    Args:
        database_url: Optional explicit DATABASE_URL override. If omitted, the
            application settings (environment/.env) will be used.
        assume_yes: When True, skips the interactive confirmation prompt.
    """
    resolved_url = _resolve_database_url(database_url)
    asyncio.run(flush_database(resolved_url, assume_yes=assume_yes))


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    flush_seed_database(
        database_url=args.database_url,
        assume_yes=args.yes,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
