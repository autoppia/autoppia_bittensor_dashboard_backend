from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

if TYPE_CHECKING:
    from app.models.core import AgentEvaluationRun


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


# Keys in agent_run metadata that are redundant with DB columns (is_reused, reused_from_agent_run_id)
_AGENT_RUN_META_REDUNDANT_KEYS = frozenset({"handshake_note", "reused_from_round"})


def _agent_run_meta_for_storage(model: "AgentEvaluationRun") -> Dict[str, Any]:
    """Store only useful agent_run metadata; omit handshake_note/reused_from_round (already in is_reused/reused_from_agent_run_id)."""
    meta = getattr(model, "metadata", None) or {}
    if not meta:
        return {}
    cleaned = {k: v for k, v in meta.items() if k not in _AGENT_RUN_META_REDUNDANT_KEYS}
    return _non_empty_dict(cleaned)


def _clean_meta_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Clean metadata dict: remove empty/useless fields and heavy LLM payloads.
    llm_calls / llm_usage detail is not stored here (lives in evaluation_llm_usage and AWS logs).
    timeout (and similar) are not stored here: use zero_reason column instead.
    """
    if not value:
        return {}

    skip_keys = {"llm_calls", "llm_usage", "timeout", "timeout_reason"}
    useless_fields = {
        "notes": "",
        "error_message": "",
        "version_ok": True,
        "evaluation_score": 0.0,
        "reward": 0.0,
    }

    cleaned = {}
    for key, val in value.items():
        if key in skip_keys:
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
