#!/usr/bin/env python3
"""
Seeding utilities for IWAP.

These functions wrap the async seeding helpers under app.services.seed_utils
and provide synchronous, Python-friendly entry points for:
  - seed_validator_round
  - seed_single_validator_round
  - seed_round_for_validators
  - seed_round
  - seed_multiple_rounds

All DB configuration is sourced from .env via app.config.settings used by the
FastAPI app and its session management.
"""

from __future__ import annotations

import asyncio
import random
from typing import Dict, Iterable, List, Optional

from app.data import VALIDATOR_DIRECTORY
from app.services.seed_utils import (
    generate_validator_round_id,
    seed_validator_round as _async_seed_validator_round,
)


def _ensure_positive_ints(values: Iterable[int]) -> List[int]:
    deduped = sorted({int(v) for v in values if int(v) > 0})
    return deduped


def _default_count(value: Optional[int]) -> int:
    return int(value) if value is not None else random.randint(10, 20)


def seed_validator_round(
    validator_uid: int,
    round_number: int,
    *,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> dict:
    """Seed a single validator round and return a summary dict.

    Uses in-process ASGI calls to the FastAPI app; no external network required.
    """
    validator_uid = int(validator_uid)
    round_number = int(round_number)
    miners = _default_count(num_miners)
    tasks = _default_count(num_tasks)

    validator_round_id = generate_validator_round_id(validator_uid, round_number)
    result = asyncio.run(
        _async_seed_validator_round(
            validator_round_id=validator_round_id,
            validator_uid=validator_uid,
            num_tasks=tasks,
            num_miners=miners,
            round_number=round_number,
        )
    )
    return {
        "validator_uid": validator_uid,
        "round_number": round_number,
        "validator_round_id": validator_round_id,
        "saved": result.saved_entities,
    }


def seed_single_validator_round(
    validator_uid: int,
    round_number: int,
    *,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> dict:
    """Alias for seed_validator_round."""
    return seed_validator_round(
        validator_uid=validator_uid,
        round_number=round_number,
        num_miners=num_miners,
        num_tasks=num_tasks,
    )


def seed_round_for_validators(
    round_number: int,
    validator_uids: Iterable[int],
    *,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> Dict[int, dict]:
    """Seed the given round for a specific set of validators.

    Returns a mapping of validator_uid -> summary dict.
    """
    uids = _ensure_positive_ints(validator_uids)
    results: Dict[int, dict] = {}
    for uid in uids:
        results[uid] = seed_validator_round(
            validator_uid=uid,
            round_number=round_number,
            num_miners=num_miners,
            num_tasks=num_tasks,
        )
    return results


def seed_round(
    round_number: int,
    *,
    validator_uids: Optional[Iterable[int]] = None,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> Dict[int, dict]:
    """Seed a single logical round across validators.

    When `validator_uids` is None, seeds across all known validators from
    the static directory (augmented on-chain identities are handled within
    the underlying seed utils as needed).
    """
    if validator_uids is None:
        validator_uids = VALIDATOR_DIRECTORY.keys()
    return seed_round_for_validators(
        round_number=round_number,
        validator_uids=list(validator_uids),
        num_miners=num_miners,
        num_tasks=num_tasks,
    )


def seed_multiple_rounds(
    round_numbers: Iterable[int],
    *,
    validator_uids: Optional[Iterable[int]] = None,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> Dict[int, Dict[int, dict]]:
    """Seed several rounds across validators.

    Returns a mapping of round_number -> { validator_uid -> summary }.
    """
    results: Dict[int, Dict[int, dict]] = {}
    for rn in _ensure_positive_ints(round_numbers):
        results[rn] = seed_round(
            round_number=rn,
            validator_uids=validator_uids,
            num_miners=num_miners,
            num_tasks=num_tasks,
        )
    return results
