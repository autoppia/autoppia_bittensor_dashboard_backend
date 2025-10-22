#!/usr/bin/env python3
"""
Small helper to retrieve the database URL from app settings.

Single source of truth is `app.config.settings`, which reads from .env.
"""

from __future__ import annotations

from app.config import settings


def get_database_url() -> str:
    return settings.DATABASE_URL
