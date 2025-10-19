"""
Utilities to reset the SQLite database used for seeding scenarios.

This module wraps ``scripts.flush_db`` so it can be invoked alongside the other
seeding helpers:

    python -m seeding.flush --yes --database-url sqlite+aiosqlite:///autoppia.db
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.flush_db import flush_database as _flush_database


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
    if database_url:
        os.environ["DATABASE_URL"] = database_url

    if database_url is None:
        from app.config import settings  # local import to respect env overrides

        database_url = settings.DATABASE_URL

    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    asyncio.run(_flush_database(database_url, assume_yes=assume_yes))


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
