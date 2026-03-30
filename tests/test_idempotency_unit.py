from __future__ import annotations

from app.config import settings
from app.services import idempotency


def test_get_cache_stats_counts_active_and_expired(monkeypatch) -> None:
    monkeypatch.setattr(settings, "IDEMPOTENCY_TTL", 10)
    monkeypatch.setattr(idempotency.time, "time", lambda: 100.0)
    monkeypatch.setattr(idempotency, "_cache", {"a": 95.0, "b": 80.0, "c": 100.0})

    stats = idempotency.get_cache_stats()

    assert stats["total_keys"] == 3
    assert stats["active_keys"] == 2
    assert stats["expired_keys"] == 1
    assert stats["ttl_seconds"] == 10
