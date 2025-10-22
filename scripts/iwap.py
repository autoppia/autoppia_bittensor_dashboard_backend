#!/usr/bin/env python3
"""
Utilities for seeding validator rounds through the public REST ingestion API.

Usage examples:
    # Seed round 1 for every known validator (random miners/tasks)
    python -m scripts.seed_round --round 1

    # Seed rounds 2, 3, and 4 only for validator UID 124 with fixed miner/task counts
    python -m scripts.seed_round --round 2 3 4 --validators 124 --num-miners 12 --num-tasks 15
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, TYPE_CHECKING

BACKEND_DIR = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = BACKEND_DIR.parent

for candidate in (BACKEND_DIR, MONOREPO_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.append(candidate_str)

if TYPE_CHECKING:  # pragma: no cover - import for static type checkers only
    from app.services.validator.validator_storage import PersistenceResult

MIN_MINERS = 10
MAX_MINERS = 20
MIN_TASKS = 10
MAX_TASKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _available_validator_uids() -> List[int]:
    """Return the known validator UIDs in sorted order."""
    from app.data.validator_directory import VALIDATOR_DIRECTORY  # local import

    return sorted(VALIDATOR_DIRECTORY.keys())


def _ensure_validator_uid(candidate: Optional[int]) -> int:
    """Validate and/or resolve a validator UID."""
    if candidate is None:
        try:
            return next(iter(_available_validator_uids()))
        except StopIteration as exc:  # pragma: no cover - defensive guard
            raise RuntimeError("No predefined validators available to seed rounds") from exc

    from app.data.validator_directory import (  # local import for runtime flexibility
        VALIDATOR_DIRECTORY,
        get_validator_metadata,
    )

    metadata = get_validator_metadata(candidate)
    if metadata.get("uid") != candidate or candidate not in VALIDATOR_DIRECTORY:
        raise ValueError(f"Validator UID {candidate} is not registered in VALIDATOR_DIRECTORY")
    return candidate


async def _seed_async(
    validator_round_id: str,
    validator_uid: int,
    round_number: int,
    num_miners: int,
    num_tasks: int,
) -> "PersistenceResult":
    """Internal async helper that delegates to the shared seeding utilities."""
    from app.db.session import engine, init_db  # local import
    from app.services.seed_utils import (  # local import
        seed_validator_round as _seed_validator_round,
    )

    try:
        await init_db()
        return await _seed_validator_round(
            validator_round_id=validator_round_id,
            validator_uid=validator_uid,
            num_tasks=num_tasks,
            num_miners=num_miners,
            round_number=round_number,
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed_validator_round(
    validator_uid: int,
    round_number: int,
    *,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> "PersistenceResult":
    """
    Seed a single validator round by exercising the public REST ingestion endpoints.

    The number of simulated miners/tasks defaults to a random value within
    [MIN_MINERS, MAX_MINERS] and [MIN_TASKS, MAX_TASKS] unless explicitly provided.

    Args:
        validator_uid: The UID of the validator to seed.
        round_number: The logical round number to seed.
        num_miners: Optional number of miners to simulate. Defaults to random value.
        num_tasks: Optional number of tasks per miner. Defaults to random value.

    Returns:
        PersistenceResult containing details of the saved entities.
    """
    from app.services.seed_utils import generate_validator_round_id  # local import

    resolved_uid = _ensure_validator_uid(validator_uid)
    miners = num_miners if num_miners is not None else random.randint(MIN_MINERS, MAX_MINERS)
    tasks = num_tasks if num_tasks is not None else random.randint(MIN_TASKS, MAX_TASKS)
    validator_round_id = generate_validator_round_id(resolved_uid, round_number)

    return asyncio.run(
        _seed_async(
            validator_round_id=validator_round_id,
            validator_uid=resolved_uid,
            round_number=round_number,
            num_miners=miners,
            num_tasks=tasks,
        )
    )


def seed_round(
    round_number: int,
    *,
    validator_uids: Optional[Iterable[int]] = None,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> List["PersistenceResult"]:
    """
    Seed the specified round number across multiple validators.

    This function seeds a single logical round for multiple validators,
    ensuring all persistence happens through the same ingestion flow a real
    validator would trigger.

    Args:
        round_number: The logical round number to seed.
        validator_uids: Optional subset of validator UIDs. Defaults to all known validators.
        num_miners: Optional number of miners to simulate per validator.
        num_tasks: Optional number of tasks per miner per validator.

    Returns:
        List of PersistenceResult objects, one per validator.
    """
    selected_uids = list(validator_uids) if validator_uids is not None else _available_validator_uids()
    if not selected_uids:
        raise RuntimeError("No validator UIDs available to seed rounds")

    results: List["PersistenceResult"] = []
    for uid in selected_uids:
        results.append(
            seed_validator_round(
                validator_uid=uid,
                round_number=round_number,
                num_miners=num_miners,
                num_tasks=num_tasks,
            )
        )
    return results


def seed_multiple_rounds(
    round_numbers: Iterable[int],
    *,
    validator_uids: Optional[Iterable[int]] = None,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> Dict[int, List["PersistenceResult"]]:
    """
    Seed several logical rounds, each spanning one validator round per validator UID.

    Args:
        round_numbers: Iterable of logical round numbers to seed (e.g., [1, 2, 3]).
        validator_uids: Optional subset of validator UIDs. Defaults to all known validators.
        num_miners: Optional number of miners to simulate per validator.
        num_tasks: Optional number of tasks per miner per validator.

    Returns:
        Mapping of round number -> list of persistence results for each validator.
    """
    seeded_rounds: Dict[int, List["PersistenceResult"]] = {}
    for number in round_numbers:
        seeded_rounds[number] = seed_round(
            round_number=number,
            validator_uids=validator_uids,
            num_miners=num_miners,
            num_tasks=num_tasks,
        )
    return seeded_rounds


# Backwards-compatible aliases.
seed_single_validator_round = seed_validator_round
seed_round_for_validators = seed_round


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed validator rounds via REST APIs.")
    parser.add_argument(
        "--round",
        "--rounds",
        dest="rounds",
        metavar="ROUND",
        type=int,
        nargs="+",
        help="One or more logical round numbers to seed.",
    )
    parser.add_argument(
        "--round-number",
        dest="round_number",
        type=int,
        default=None,
        help="(Deprecated) Single logical round number to seed. Prefer --round/--rounds.",
    )
    parser.add_argument(
        "--validator-uid",
        dest="validator_uid",
        type=int,
        default=None,
        help="(Deprecated) Seed only the specified validator UID. Prefer --validators.",
    )
    parser.add_argument(
        "--validators",
        dest="validators",
        metavar="UID",
        type=int,
        nargs="+",
        default=None,
        help="Optional list of validator UIDs to seed. Defaults to all validators.",
    )
    parser.add_argument(
        "--num-miners",
        dest="num_miners",
        type=int,
        default=None,
        help=f"Override number of miners (default random in [{MIN_MINERS}, {MAX_MINERS}]).",
    )
    parser.add_argument(
        "--num-tasks",
        dest="num_tasks",
        type=int,
        default=None,
        help=f"Override number of tasks per miner (default random in [{MIN_TASKS}, {MAX_TASKS}]).",
    )
    return parser


def _format_result(result: "PersistenceResult") -> str:
    saved = result.saved_entities
    round_id = saved.get("validator_round") or saved.get("round")
    agent_runs = saved.get("agent_evaluation_runs", [])
    tasks = saved.get("tasks", [])
    task_solutions = saved.get("task_solutions", [])
    evaluations = saved.get("evaluations", [])
    evaluation_results = saved.get("evaluation_results", [])
    return (
        f"Validator UID: {result.validator_uid}\n"
        f"Round ID: {round_id}\n"
        f"Agent runs: {len(agent_runs)}\n"
        f"Tasks: {len(tasks)}\n"
        f"Task solutions: {len(task_solutions)}\n"
        f"Evaluations: {len(evaluations) or len(evaluation_results)}"
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    rounds: List[int] = []
    if args.rounds:
        rounds.extend(args.rounds)
    if args.round_number is not None:
        rounds.append(args.round_number)

    if not rounds:
        parser.error("At least one round number must be provided via --round/--rounds.")

    unique_rounds = sorted(set(rounds))

    validator_uids: Optional[List[int]]
    if args.validators is not None:
        validator_uids = sorted(set(args.validators))
    elif args.validator_uid is not None:
        validator_uids = [args.validator_uid]
    else:
        validator_uids = None

    num_miners = args.num_miners
    num_tasks = args.num_tasks

    if len(unique_rounds) == 1:
        round_number = unique_rounds[0]
        results = seed_round(
            round_number=round_number,
            validator_uids=validator_uids,
            num_miners=num_miners,
            num_tasks=num_tasks,
        )
        print(f"✅ Seeded round {round_number} for {len(results)} validators.")
        for res in results:
            print("-" * 40)
            print(_format_result(res))
    else:
        seeded = seed_multiple_rounds(
            round_numbers=unique_rounds,
            validator_uids=validator_uids,
            num_miners=num_miners,
            num_tasks=num_tasks,
        )
        total_runs = sum(len(results) for results in seeded.values())
        print(
            f"✅ Seeded {len(unique_rounds)} rounds "
            f"covering {total_runs} validator runs."
        )
        for round_number in unique_rounds:
            results = seeded.get(round_number, [])
            print("=" * 40)
            print(f"Round {round_number}: {len(results)} validators seeded.")
            for res in results:
                print("-" * 40)
                print(_format_result(res))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
