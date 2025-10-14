"""
Compatibility shim exposing schema models to legacy import paths.
"""
from __future__ import annotations

from . import definitions as _definitions

__all__ = list(getattr(_definitions, "__all__", []))

globals().update({name: getattr(_definitions, name) for name in __all__})
