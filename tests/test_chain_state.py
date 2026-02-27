from __future__ import annotations

from typing import Optional

import pytest


@pytest.fixture(autouse=True)
def _reset_chain_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with a clean chain_state module."""
    from app.services import chain_state

    monkeypatch.setattr(chain_state, "_cached_block", None)
    monkeypatch.setattr(chain_state, "_cached_at", 0.0)
    monkeypatch.setattr(chain_state, "_fetch_in_progress", False)
    monkeypatch.setattr(chain_state, "_last_fetch_attempt", 0.0)
    monkeypatch.setattr(chain_state, "_FAILURE_RETRY_SECONDS", 0.1, raising=False)


def test_get_current_block_caches_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chain_state

    calls = {"count": 0}

    def fake_fetch() -> Optional[int]:
        calls["count"] += 1
        return 123

    monkeypatch.setattr(chain_state, "_fetch_current_block", fake_fetch)

    first = chain_state.get_current_block()
    second = chain_state.get_current_block()

    assert first == 123
    assert second >= 123
    assert calls["count"] == 1


def test_get_current_block_throttles_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chain_state

    calls = {"count": 0}

    def fake_fetch_failure() -> Optional[int]:
        calls["count"] += 1
        return None

    monkeypatch.setattr(chain_state, "_fetch_current_block", fake_fetch_failure)

    first = chain_state.get_current_block()
    second = chain_state.get_current_block()

    assert first is None
    assert second is None
    assert calls["count"] == 1
