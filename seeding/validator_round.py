"""
Utilities for seeding validator rounds through the public REST ingestion API.

Usage examples:
    # Seed round 1 for every known validator (random miners/tasks)
    python -m seeding.validator_round --round-number 1

    # Seed round 2 only for validator UID 124
    python -m seeding.validator_round --round-number 2 --validator-uid 124
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = BACKEND_DIR.parent

for candidate in (BACKEND_DIR, MONOREPO_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.append(candidate_str)

from app.data.validator_directory import (  # type: ignore  # noqa: E402
    VALIDATOR_DIRECTORY,
    get_validator_metadata,
)
from app.db.session import init_db  # type: ignore  # noqa: E402
from app.services.seed_utils import (  # type: ignore  # noqa: E402
    generate_validator_round_id,
    seed_validator_round as _seed_validator_round,
)
from app.services.validator_storage import PersistenceResult  # type: ignore  # noqa: E402

MIN_MINERS = 10
MAX_MINERS = 20
MIN_TASKS = 10
MAX_TASKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _available_validator_uids() -> List[int]:
    """Return the known validator UIDs in sorted order."""
    return sorted(VALIDATOR_DIRECTORY.keys())


def _ensure_validator_uid(candidate: Optional[int]) -> int:
    """Validate and/or resolve a validator UID."""
    if candidate is None:
        try:
            return next(iter(_available_validator_uids()))
        except StopIteration as exc:  # pragma: no cover - defensive guard
            raise RuntimeError("No predefined validators available to seed rounds") from exc

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
) -> PersistenceResult:
    """Internal async helper that delegates to the shared seeding utilities."""
    await init_db()
    return await _seed_validator_round(
        validator_round_id=validator_round_id,
        validator_uid=validator_uid,
        num_tasks=num_tasks,
        num_miners=num_miners,
        round_number=round_number,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seed_single_validator_round(
    validator_uid: int,
    round_number: int,
    *,
    num_miners: Optional[int] = None,
    num_tasks: Optional[int] = None,
) -> PersistenceResult:
    """
    Seed a single validator round by exercising the public REST ingestion endpoints.

    The number of simulated miners/tasks defaults to a random value within
    [MIN_MINERS, MAX_MINERS] and [MIN_TASKS, MAX_TASKS] unless explicitly provided.
    """
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


def seed_round_for_validators(
    round_number: int,
    *,
    validator_uids: Optional[Iterable[int]] = None,
) -> List[PersistenceResult]:
    """
    Seed the specified round number across multiple validators.

    This helper delegates to `seed_single_validator_round` per validator UID,
    ensuring all persistence happens through the same ingestion flow a real
    validator would trigger.
    """
    selected_uids = list(validator_uids) if validator_uids is not None else _available_validator_uids()
    if not selected_uids:
        raise RuntimeError("No validator UIDs available to seed rounds")

    results: List[PersistenceResult] = []
    for uid in selected_uids:
        results.append(
            seed_single_validator_round(
                validator_uid=uid,
                round_number=round_number,
            )
        )
    return results


def seed_multiple_rounds(
    round_numbers: Iterable[int],
    *,
    validator_uids: Optional[Iterable[int]] = None,
) -> Dict[int, List[PersistenceResult]]:
    """
    Seed several logical rounds, each spanning one validator round per validator UID.

    Args:
        round_numbers: Iterable of logical round numbers to seed (e.g., [1, 2, 3]).
        validator_uids: Optional subset of validator UIDs. Defaults to all known validators.

    Returns:
        Mapping of round number -> list of persistence results for each validator.
    """
    seeded_rounds: Dict[int, List[PersistenceResult]] = {}
    for number in round_numbers:
        seeded_rounds[number] = seed_round_for_validators(
            round_number=number,
            validator_uids=validator_uids,
        )
    return seeded_rounds


# Backwards-compatible aliases.
seed_validator_round = seed_single_validator_round
seed_round = seed_round_for_validators


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed validator rounds via REST APIs.")
    parser.add_argument(
        "--round-number",
        dest="round_number",
        type=int,
        required=True,
        help="Logical round number to seed.",
    )
    parser.add_argument(
        "--validator-uid",
        dest="validator_uid",
        type=int,
        default=None,
        help="Seed only the specified validator UID. Defaults to all validators.",
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


def _format_result(result: PersistenceResult) -> str:
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
    args = parser.parse_args(argv)

    if args.validator_uid is not None:
        result = seed_single_validator_round(
            validator_uid=args.validator_uid,
            round_number=args.round_number,
            num_miners=args.num_miners,
            num_tasks=args.num_tasks,
        )
        print("✅ Seeded validator round successfully:")
        print(_format_result(result))
    else:
        results = seed_round_for_validators(
            round_number=args.round_number,
        )
        print(
            f"✅ Seeded round {args.round_number} for {len(results)} validators "
            f"({MIN_MINERS}-{MAX_MINERS} miners each, {MIN_TASKS}-{MAX_TASKS} tasks each)."
        )
        for res in results:
            print("-" * 40)
            print(_format_result(res))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
