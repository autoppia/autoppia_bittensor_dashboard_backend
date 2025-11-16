from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

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


__all__ = ["rollback_on_error"]
