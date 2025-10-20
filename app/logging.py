# app/logging.py
"""
Centralized logging setup with per-library levels driven by Settings.

Environment overrides (examples):
  LOG_LEVEL=INFO
  SQLALCHEMY_LOG_LEVEL=WARNING
  BITTENSOR_LOG_LEVEL=ERROR
  UVICORN_LOG_LEVEL=INFO
  UVICORN_ACCESS_LOG=true|false
"""
from __future__ import annotations

import sys
import logging as _logging
from typing import Dict, Tuple

SQLALCHEMY_LOGGER_NAMES = (
    "sqlalchemy",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
    "sqlalchemy.orm",
    "sqlalchemy.orm.mapper.Mapper",
    "sqlalchemy.orm.relationships.RelationshipProperty",
    "sqlalchemy.orm.strategies.LazyLoader",
)

_KNOWN_LEVELS: Dict[str, int] = {
    "CRITICAL": _logging.CRITICAL,
    "ERROR": _logging.ERROR,
    "WARNING": _logging.WARNING,
    "INFO": _logging.INFO,
    "DEBUG": _logging.DEBUG,
    "NOTSET": _logging.NOTSET,
}


def _parse_level(v: str | int | None, default: int) -> int:
    if v is None:
        return default
    if isinstance(v, int):
        return v if v in _KNOWN_LEVELS.values() else default
    try:
        n = int(v)
        return n if n in _KNOWN_LEVELS.values() else default
    except (TypeError, ValueError):
        return _KNOWN_LEVELS.get(str(v).upper(), default)


class _SuppressSqlalchemyInfoFilter(_logging.Filter):
    """Drop noisy SQLAlchemy < WARNING records (safety net)."""
    noisy_prefixes = ("sqlalchemy.engine", "sqlalchemy.pool", "sqlalchemy.orm")

    def filter(self, record: _logging.LogRecord) -> bool:  # pragma: no cover
        return not (
            record.levelno < _logging.WARNING
            and any(record.name.startswith(p) for p in self.noisy_prefixes)
        )


def _apply_filter_to_active_handlers(filt: _logging.Filter) -> None:
    """Attach filter to existing handlers (root and uvicorn family)."""
    targets = [
        _logging.getLogger(),  # root
        _logging.getLogger("uvicorn"),
        _logging.getLogger("uvicorn.error"),
        _logging.getLogger("uvicorn.access"),
    ]
    for lg in targets:
        for h in lg.handlers:
            if filt not in h.filters:
                h.addFilter(filt)


def _configure_uvicorn(level: int) -> None:
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _logging.getLogger(name).setLevel(level)


def _configure_sqlalchemy(level: int) -> None:
    # Set levels and stop propagation so nothing bubbles up
    for name in SQLALCHEMY_LOGGER_NAMES:
        lg = _logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = False


def _configure_bittensor(level: int) -> None:
    """Keep bittensor at requested level and avoid its debug sinks."""
    try:
        import bittensor as bt  # type: ignore
    except Exception:
        _logging.getLogger("bittensor").setLevel(level)
        return

    try:
        # Prefer no debug/trace sinks (they can be very chatty)
        bt.logging.set_debug(False)
        bt.logging.set_trace(False)
        # Leave info True so important events surface when level <= INFO
        bt.logging.set_info(True)
    except Exception:
        pass

    bt_logger = _logging.getLogger("bittensor")
    bt_logger.setLevel(level)


def init_logging(settings) -> Tuple[_logging.Logger, int]:
    """
    Initialize logging EARLY (call from app.main before importing DB/ORM modules).
    Returns: (logger, effective_level)
    """
    level = _parse_level(getattr(settings, "LOG_LEVEL", "WARNING"), _logging.WARNING)
    sa_level = _parse_level(getattr(settings, "SQLALCHEMY_LOG_LEVEL", "ERROR"), _logging.ERROR)
    bt_level = _parse_level(getattr(settings, "BITTENSOR_LOG_LEVEL", "WARNING"), _logging.WARNING)
    uvicorn_level = _parse_level(getattr(settings, "UVICORN_LOG_LEVEL", "WARNING"), _logging.WARNING)

    # If DEBUG=true and general level was INFO, allow bump to DEBUG
    if getattr(settings, "DEBUG", False) and level == _logging.INFO:
        level = _logging.DEBUG

    # Base config first, so subsequent per-logger configs stick
    _logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    # Per-library tuning
    _configure_uvicorn(uvicorn_level)
    _configure_sqlalchemy(sa_level)
    _configure_bittensor(bt_level)

    # Extra safety: attach a filter that kills SQLA < WARNING at handler level
    filt = _SuppressSqlalchemyInfoFilter()
    _apply_filter_to_active_handlers(filt)

    # Keep a couple of known-noisy libs at least INFO+
    for name in ("btdecode", "aiosqlite"):
        _logging.getLogger(name).setLevel(max(level, _logging.INFO))

    logger = _logging.getLogger("app")
    return logger, level


def reapply_handler_filters_after_uvicorn_started() -> None:
    """
    Call this during FastAPI startup to ensure our filters remain attached
    even if Uvicorn has (re)created handlers.
    """
    filt = _SuppressSqlalchemyInfoFilter()
    _apply_filter_to_active_handlers(filt)
