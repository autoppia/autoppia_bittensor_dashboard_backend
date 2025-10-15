"""
Compatibility shim exposing core models to legacy import paths.
"""
from __future__ import annotations

from .. import core as _core

__all__ = list(getattr(_core, "__all__", []))

globals().update({name: getattr(_core, name) for name in __all__})
