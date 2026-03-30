from __future__ import annotations

import pytest


def test_get_current_block_delegates_to_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chain_state

    monkeypatch.setattr(chain_state, "get_current_block_estimate", lambda: 123)
    assert chain_state.get_current_block() == 123


def test_get_current_block_returns_none_without_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import chain_state

    monkeypatch.setattr(chain_state, "get_current_block_estimate", lambda: None)
    assert chain_state.get_current_block() is None
