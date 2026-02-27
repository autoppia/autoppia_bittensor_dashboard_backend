from __future__ import annotations

from typing import Optional


def parse_identifier(identifier: str) -> int:
    if "-" in identifier:
        identifier = identifier.split("-", 1)[1]
    if "_" in identifier:
        identifier = identifier.split("_", 1)[1]
    return int(identifier)


def round_id_to_int(round_id: str) -> int:
    if not round_id:
        return 0
    suffix = round_id
    if round_id.startswith("round_"):
        suffix = round_id.split("round_", 1)[1]
    elif "_" in round_id:
        suffix = round_id.split("_", 1)[1]
    digits: list[str] = []
    for char in suffix:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not digits:
        return 0
    try:
        return int("".join(digits))
    except ValueError:
        return 0


def format_agent_id(miner_uid: Optional[int]) -> str:
    return f"agent-{miner_uid}" if miner_uid is not None else "agent-unknown"


def format_validator_id(validator_uid: Optional[int]) -> str:
    return f"validator-{validator_uid}" if validator_uid is not None else "validator-unknown"


def safe_round(value: float, digits: int = 3) -> float:
    try:
        return round(float(value), digits)
    except Exception:  # noqa: BLE001
        return 0.0
