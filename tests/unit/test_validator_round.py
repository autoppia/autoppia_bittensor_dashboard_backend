import pytest
from uuid import uuid4
from pydantic import ValidationError

from app.models.core.info import ValidatorInfo
from app.models.core.validator_round import ValidatorRound


def _validator() -> ValidatorInfo:
    return ValidatorInfo(
        identifier="validator-1",
        uid=1,
        hotkey="test-hotkey",
    )


def test_validator_round_accepts_uuid_string():
    value = str(uuid4())
    validator_round = ValidatorRound(
        validator_round_id=value,
        validator=_validator(),
        n_tasks=5,
        n_winners=2,
    )
    assert validator_round.validator_round_id == value


def test_validator_round_rejects_invalid_uuid():
    with pytest.raises(ValidationError):
        ValidatorRound(
            validator_round_id="not-a-valid-uuid",
            validator=_validator(),
            n_tasks=5,
            n_winners=2,
        )
