from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class PersistenceResult:
    validator_uid: int
    saved_entities: Dict[str, Any]


class RoundConflictError(ValueError):
    """Raised when a validator attempts to register the same round twice."""


class DuplicateIdentifierError(ValueError):
    """Raised when an identifier that must be unique already exists."""


def _non_empty_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return value or {}


def _clean_meta_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Clean metadata dict: remove empty/useless fields and normalize heavy payloads.
    llm_usage detail is not stored here (lives in evaluation_llm_usage).
    llm_calls (prompt/response traces) are stored in compact form for observability.
    timeout (and similar) are not stored here: use zero_reason column instead.
    """
    if not value:
        return {}

    skip_keys = {"llm_usage", "timeout", "timeout_reason"}
    useless_fields = {
        "notes": "",
        "error_message": "",
        "version_ok": True,
        "evaluation_score": 0.0,
        "reward": 0.0,
    }

    def _truncate_text(raw: Any, max_len: int = 6000) -> Any:
        if not isinstance(raw, str):
            return raw
        if len(raw) <= max_len:
            return raw
        return raw[:max_len] + f"... [truncated {len(raw) - max_len} chars]"

    def _compact_llm_calls(raw_calls: Any) -> List[Dict[str, Any]]:
        """
        Persist prompt/response traces in DB with conservative size bounds.
        """
        if not isinstance(raw_calls, list):
            return []
        compact: List[Dict[str, Any]] = []
        # Keep at most 40 calls per evaluation to avoid oversized JSON payloads.
        for call in raw_calls[:40]:
            if not isinstance(call, dict):
                continue
            compact.append(
                {
                    "provider": call.get("provider"),
                    "model": call.get("model"),
                    "tokens": call.get("tokens"),
                    "cost": call.get("cost"),
                    "timestamp": call.get("timestamp"),
                    "input": _truncate_text(call.get("input")),
                    "output": _truncate_text(call.get("output")),
                }
            )
        return compact

    cleaned = {}
    for key, val in value.items():
        if key in skip_keys:
            continue
        if key == "llm_calls":
            llm_calls = _compact_llm_calls(val)
            if llm_calls:
                cleaned[key] = llm_calls
            continue
        if key in useless_fields and val == useless_fields[key]:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        cleaned[key] = val

    return cleaned


def _action_dump(actions: Iterable[Any]) -> List[Dict[str, Any]]:
    dumped: List[Dict[str, Any]] = []
    for action in actions:
        if hasattr(action, "model_dump"):
            dumped.append(action.model_dump(mode="json", exclude_none=True))
        else:
            dumped.append(dict(action))
    return dumped


def _optional_dump(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return value
