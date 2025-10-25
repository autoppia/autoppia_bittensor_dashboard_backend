from __future__ import annotations

import pytest


def test_api_cache_force_overrides_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import cache

    cache.api_cache.clear()
    monkeypatch.setattr(cache.api_cache, "_disabled", True)

    try:
        cache.api_cache.set("final:test", {"value": 1}, ttl=60, force=True)

        assert cache.api_cache.get("final:test") is None
        assert cache.api_cache.get("final:test", force=True) == {"value": 1}
    finally:
        cache.api_cache._disabled = False
