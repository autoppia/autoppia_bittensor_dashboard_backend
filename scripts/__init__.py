"""
Utilities for managing the Autoppia backend database and seeding data.

This package provides convenience helpers for:
- Flushing and resetting the database
- Seeding validator rounds through the public REST API
- Interactive CLI (IWAP) for common operations
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

__all__ = [
    "flush_database",
    "flush_seed_database",
    "seed_multiple_rounds",
    "seed_round",
    "seed_round_for_validators",
    "seed_single_validator_round",
    "seed_validator_round",
]

_EXPORT_TO_MODULE: Dict[str, str] = {
    "flush_database": ".flush_db",
    "flush_seed_database": ".flush_db",
    "seed_multiple_rounds": ".seed_round",
    "seed_round": ".seed_round",
    "seed_round_for_validators": ".seed_round",
    "seed_single_validator_round": ".seed_round",
    "seed_validator_round": ".seed_round",
}


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORT_TO_MODULE[name]
    except KeyError as exc:  # pragma: no cover - standard import behaviour
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
