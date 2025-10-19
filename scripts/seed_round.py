#!/usr/bin/env python3
"""
Helper script to seed logical rounds into the local SQLite database.

This utility wraps ``seeding.validator_round`` so we can easily seed a batch of
round numbers (and optionally a subset of validators) in one go.

Example:
    python scripts/seed_round.py --rounds 1 2 3 4 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from seeding.validator_round import (  # type: ignore  # noqa: E402
    seed_round as seed_round_for_validators,
    seed_validator_round,
)
from app.data.validator_directory import (  # type: ignore  # noqa: E402
    VALIDATOR_DIRECTORY,
)


def _available_validator_uids() -> List[int]:
    """Return sorted validator UIDs configured for seeding."""
    return sorted(VALIDATOR_DIRECTORY.keys())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed one or more logical rounds into the local database.",
    )
    parser.add_argument(
        "--rounds",
        metavar="ROUND",
        type=int,
        nargs="+",
        required=True,
        help="One or more round numbers to seed.",
    )
    parser.add_argument(
        "--validators",
        metavar="UID",
        type=int,
        nargs="+",
        default=None,
        help="Optional list of validator UIDs to seed. Defaults to all known validators.",
    )
    parser.add_argument(
        "--num-miners",
        type=int,
        default=None,
        help="Override simulated miner count for each seed run.",
    )
    parser.add_argument(
        "--num-tasks",
        type=int,
        default=None,
        help="Override simulated task count for each seed run.",
    )
    return parser.parse_args()


def _seed_single_round(
    round_number: int,
    validator_uids: Optional[Iterable[int]],
    num_miners: Optional[int],
    num_tasks: Optional[int],
) -> None:
    """Seed a round for the requested validators."""
    selected_uids: List[int]
    if validator_uids is None:
        selected_uids = _available_validator_uids()
    else:
        selected_uids = sorted(set(validator_uids))

    if not selected_uids:
        raise SystemExit("No validator UIDs provided or discovered for seeding.")

    # If we do not need custom miner/task counts we can rely on the helper that
    # seeds the whole round in one call. Otherwise call the per-validator helper.
    if num_miners is None and num_tasks is None:
        results = seed_round_for_validators(round_number, validator_uids=selected_uids)
    else:
        results = [
            seed_validator_round(
                validator_uid=uid,
                round_number=round_number,
                num_miners=num_miners,
                num_tasks=num_tasks,
            )
            for uid in selected_uids
        ]

    print(f"✅ Seeded round {round_number} for {len(results)} validators.")
    for result in results:
        saved = result.saved_entities
        print("-" * 40)
        print(
            f"Validator UID: {result.validator_uid}\n"
            f"Round ID: {saved.get('round')}\n"
            f"Agent runs: {len(saved.get('agent_evaluation_runs', []))}\n"
            f"Tasks: {len(saved.get('tasks', []))}\n"
            f"Task solutions: {len(saved.get('task_solutions', []))}\n"
            f"Evaluations: {len(saved.get('evaluation_results', []))}"
        )


def main() -> int:
    args = _parse_args()
    for round_number in sorted(set(args.rounds)):
        _seed_single_round(
            round_number=round_number,
            validator_uids=args.validators,
            num_miners=args.num_miners,
            num_tasks=args.num_tasks,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

