from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api.validator import validator_round_handlers_lifecycle as lifecycle
from app.models.core import Validator, ValidatorRound, ValidatorRoundValidator
from app.services.round_calc import is_inside_window
from app.services.validator.validator_storage_common import RoundConflictError


class _RowResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


def _make_request(headers: dict[str, str] | None = None) -> Request:
    raw_headers = []
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    return Request({"type": "http", "headers": raw_headers})


def _make_payload(*, uid: int = 196, hotkey: str = "5FMainValidatorHotkey111111111111111111111111111", start_block: int = 100):
    validator_round_id = f"validator_round_{uid}_{start_block}"
    validator_identity = Validator(uid=uid, hotkey=hotkey, coldkey="5CColdkey111111111111111111111111111111111111")
    validator_round = ValidatorRound(
        validator_round_id=validator_round_id,
        season_number=1,
        round_number_in_season=1,
        validator_uid=uid,
        validator_hotkey=hotkey,
        validator_coldkey="5CColdkey111111111111111111111111111111111111",
        start_block=start_block,
        end_block=None,
        start_epoch=10,
        end_epoch=None,
        started_at=1_700_000_000.0,
        ended_at=None,
        n_tasks=5,
        status="active",
    )
    validator_snapshot = ValidatorRoundValidator(
        validator_round_id=validator_round_id,
        validator_uid=uid,
        validator_hotkey=hotkey,
        validator_coldkey="5CColdkey111111111111111111111111111111111111",
        name="Validator",
        stake=123.0,
        vtrust=0.99,
        image_url="https://example.com/validator.png",
        version="20.0.0",
        config={"round": {"tasks_per_season": 5}},
    )
    return SimpleNamespace(
        validator_identity=validator_identity,
        validator_round=validator_round,
        validator_snapshot=validator_snapshot,
    )


async def _noop(*args, **kwargs):
    return None


def _patch_round_context(monkeypatch, *, current_block: int, round_blocks: int = 20):
    monkeypatch.setattr(lifecycle, "_ensure_config_season_round_cache_loaded", _noop)
    monkeypatch.setattr(lifecycle, "get_current_block", lambda: current_block)
    monkeypatch.setattr(lifecycle, "get_validator_metadata", lambda uid: {})  # noqa: ARG005
    monkeypatch.setattr(lifecycle, "resolve_validator_image", lambda name, existing=None: existing or "https://example.com/validator.png")  # noqa: ARG005
    monkeypatch.setattr("app.services.round_calc._round_blocks", lambda: round_blocks)
    monkeypatch.setattr("app.services.round_calc.block_to_epoch", lambda block: float(block) / 10.0)


@pytest.mark.no_db
def test_is_inside_window_accepts_exact_start_block():
    bounds = SimpleNamespace(start_block=100, end_block=120)
    assert is_inside_window(100, bounds) is True
    assert is_inside_window(99, bounds) is False
    assert is_inside_window(120, bounds) is True
    assert is_inside_window(121, bounds) is False


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_accepts_exact_start_block(monkeypatch):
    _patch_round_context(monkeypatch, current_block=100)
    payload = _make_payload(start_block=100)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), execute=AsyncMock())
    service = SimpleNamespace(
        start_round=AsyncMock(),
        upsert_shadow_round_start=AsyncMock(),
        _is_main_validator_identity=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(lifecycle, "ValidatorRoundPersistenceService", lambda session_obj: service)  # noqa: ARG005

    response = Response()
    result = await lifecycle.start_round(
        payload=payload,
        request=_make_request(),
        response=response,
        force=False,
        session=session,
    )

    assert response.status_code == 200
    assert result["message"] == "Validator round created"
    assert payload.validator_round.start_block == 100
    assert payload.validator_round.end_block == 120
    assert payload.validator_round.start_epoch == 10
    assert payload.validator_round.end_epoch == 12
    service.start_round.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_rejects_before_exact_start_block(monkeypatch):
    _patch_round_context(monkeypatch, current_block=99)
    payload = _make_payload(start_block=100)

    with pytest.raises(HTTPException) as exc:
        await lifecycle.start_round(
            payload=payload,
            request=_make_request(),
            response=Response(),
            force=False,
            session=SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "round window not active"
    assert exc.value.detail["currentBlock"] == 99
    assert exc.value.detail["startBlock"] == 100
    assert exc.value.detail["endBlock"] == 120


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_accepts_exact_end_block(monkeypatch):
    _patch_round_context(monkeypatch, current_block=120)
    payload = _make_payload(start_block=100)
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), execute=AsyncMock())
    service = SimpleNamespace(
        start_round=AsyncMock(),
        upsert_shadow_round_start=AsyncMock(),
        _is_main_validator_identity=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(lifecycle, "ValidatorRoundPersistenceService", lambda session_obj: service)  # noqa: ARG005

    response = Response()
    result = await lifecycle.start_round(
        payload=payload,
        request=_make_request(),
        response=response,
        force=False,
        session=session,
    )

    assert response.status_code == 200
    assert result["validator_round_id"] == payload.validator_round.validator_round_id
    service.start_round.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_rejects_after_end_block(monkeypatch):
    _patch_round_context(monkeypatch, current_block=121)
    payload = _make_payload(start_block=100)

    with pytest.raises(HTTPException) as exc:
        await lifecycle.start_round(
            payload=payload,
            request=_make_request(),
            response=Response(),
            force=False,
            session=SimpleNamespace(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "round window not active"
    assert exc.value.detail["currentBlock"] == 121


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_attaches_non_main_validator_in_shadow_mode(monkeypatch):
    _patch_round_context(monkeypatch, current_block=101)
    payload = _make_payload(uid=71, hotkey="5FBackupValidatorHotkey1111111111111111111111111", start_block=100)
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        execute=AsyncMock(return_value=_RowResult((987, "active"))),
    )
    service = SimpleNamespace(
        start_round=AsyncMock(),
        upsert_shadow_round_start=AsyncMock(),
        _is_main_validator_identity=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(lifecycle, "ValidatorRoundPersistenceService", lambda session_obj: service)  # noqa: ARG005

    response = Response()
    result = await lifecycle.start_round(
        payload=payload,
        request=_make_request(),
        response=response,
        force=False,
        session=session,
    )

    assert response.status_code == 202
    assert result["shadow_mode"] is True
    assert result["attach_mode"] == "attached_to_main_round"
    assert result["canonical_round_id"] == 987
    service.start_round.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_shadow_attach_candidate_returns_false_for_main_validator(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock())
    service = SimpleNamespace(_is_main_validator_identity=AsyncMock(return_value=True))
    payload = _make_payload(uid=196)

    attach, canonical_round_id = await lifecycle._detect_shadow_attach_candidate(
        session=session,
        service=service,
        validator_round=payload.validator_round,
    )

    assert attach is False
    assert canonical_round_id is None
    session.execute.assert_not_called()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_shadow_attach_candidate_returns_false_for_inactive_canonical_round(monkeypatch):
    session = SimpleNamespace(execute=AsyncMock(return_value=_RowResult((321, "finished"))))
    service = SimpleNamespace(_is_main_validator_identity=AsyncMock(return_value=False))
    payload = _make_payload(uid=71, hotkey="5FBackupValidatorHotkey1111111111111111111111111")

    attach, canonical_round_id = await lifecycle._detect_shadow_attach_candidate(
        session=session,
        service=service,
        validator_round=payload.validator_round,
    )

    assert attach is False
    assert canonical_round_id is None
    session.execute.assert_awaited_once()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_authority_guard_persists_shadow_mode(monkeypatch):
    _patch_round_context(monkeypatch, current_block=100)
    payload = _make_payload(uid=71, hotkey="5FBackupValidatorHotkey1111111111111111111111111", start_block=100)
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        execute=AsyncMock(return_value=_RowResult(None)),
    )
    service = SimpleNamespace(
        start_round=AsyncMock(
            side_effect=RoundConflictError("Only main validator can open a new season/round before fallback grace elapses (current_block=100, planned_start_block=100, grace_blocks=25)")
        ),
        upsert_shadow_round_start=AsyncMock(),
        _is_main_validator_identity=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(lifecycle, "ValidatorRoundPersistenceService", lambda session_obj: service)  # noqa: ARG005

    response = Response()
    result = await lifecycle.start_round(
        payload=payload,
        request=_make_request(),
        response=response,
        force=False,
        session=session,
    )

    assert response.status_code == 202
    assert result["shadow_mode"] is True
    assert result["message"] == "Validator round accepted in shadow mode"
    service.upsert_shadow_round_start.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.no_db
@pytest.mark.asyncio
async def test_start_round_non_active_existing_round_is_not_treated_as_shadow_attach(monkeypatch):
    _patch_round_context(monkeypatch, current_block=101)
    payload = _make_payload(uid=71, hotkey="5FBackupValidatorHotkey1111111111111111111111111", start_block=100)
    session = SimpleNamespace(
        commit=AsyncMock(),
        rollback=AsyncMock(),
        execute=AsyncMock(return_value=_RowResult((987, "finished"))),
    )
    service = SimpleNamespace(
        start_round=AsyncMock(side_effect=RoundConflictError("Cannot attach validator run to a non-active round (season=1, round=1, status=finished)")),
        upsert_shadow_round_start=AsyncMock(),
        _is_main_validator_identity=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(lifecycle, "ValidatorRoundPersistenceService", lambda session_obj: service)  # noqa: ARG005

    with pytest.raises(HTTPException) as exc:
        await lifecycle.start_round(
            payload=payload,
            request=_make_request(),
            response=Response(),
            force=False,
            session=session,
        )

    assert exc.value.status_code == 409
    assert "non-active round" in exc.value.detail
    service.upsert_shadow_round_start.assert_not_called()
