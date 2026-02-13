from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def llm_summary_from_usage(
    usage_list: Optional[Iterable[Any]],
) -> Dict[str, Optional[Any]]:
    """Derive llm_cost, llm_tokens, llm_provider, llm_model from llm_usage rows (dict or ORM)."""
    out = {
        "llm_cost": None,
        "llm_tokens": None,
        "llm_provider": None,
        "llm_model": None,
    }
    if not usage_list:
        return out
    items = list(usage_list)
    if not items:
        return out

    def _cost(u: Any) -> float:
        if hasattr(u, "cost") and u.cost is not None:
            return float(u.cost)
        if isinstance(u, dict):
            v = u.get("cost")
            return float(v) if v is not None else 0.0
        return 0.0

    def _tokens(u: Any) -> int:
        if hasattr(u, "tokens") and u.tokens is not None:
            return int(u.tokens)
        if isinstance(u, dict):
            v = u.get("tokens")
            return int(v) if v is not None else 0
        return 0

    def _provider(u: Any) -> Optional[str]:
        if hasattr(u, "provider"):
            return getattr(u, "provider", None)
        return u.get("provider") if isinstance(u, dict) else None

    def _model(u: Any) -> Optional[str]:
        if hasattr(u, "model"):
            return getattr(u, "model", None)
        return u.get("model") if isinstance(u, dict) else None

    total_cost = sum(_cost(u) for u in items)
    total_tokens = sum(_tokens(u) for u in items)
    out["llm_cost"] = total_cost if total_cost else None
    out["llm_tokens"] = total_tokens if total_tokens else None
    if len(items) == 1:
        out["llm_provider"] = _provider(items[0])
        out["llm_model"] = _model(items[0])
    return out


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def rollback_on_error(func: F) -> F:
    """
    Decorator for service methods that ensures the AsyncSession is rolled back when an
    exception bubbles up. This prevents connections from staying in a dirty state.
    """

    @wraps(func)
    async def wrapper(self, *args: Any, **kwargs: Any):
        try:
            return await func(self, *args, **kwargs)
        except Exception:
            session = getattr(self, "session", None)
            if isinstance(session, AsyncSession):
                try:
                    await session.rollback()
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(
                        "Failed to rollback session after %s: %s",
                        func.__name__,
                        rollback_exc,
                    )
            raise

    return wrapper  # type: ignore[return-value]


__all__ = ["llm_summary_from_usage", "rollback_on_error"]
