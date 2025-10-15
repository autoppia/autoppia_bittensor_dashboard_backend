from fastapi.testclient import TestClient

from app.main import app
from app.data import get_validator_metadata


client = TestClient(app)


def _find_validator(validators, validator_id: str):
    for entry in validators:
        if entry["id"] == validator_id:
            return entry
    raise AssertionError(f"Validator {validator_id} not found in payload")


def test_validator_directory_returns_known_metadata():
    metadata = get_validator_metadata(124)
    assert metadata["name"] == "Autoppia"
    assert metadata["hotkey"] == "5DUmbx...gSDe8j"
    assert metadata["coldkey"] == "5DPtMd...LVT3EF"
    assert metadata["image"] == "images/icons/validators/Autoppia.png"


def test_validator_directory_returns_defaults_for_unknown():
    metadata = get_validator_metadata(9999)
    assert metadata["name"] == "Validator 9999"
    assert metadata["hotkey"] == ""
    assert metadata["coldkey"] == ""
    assert metadata["image"] == "images/icons/validators/Autoppia.png"


def test_overview_validators_uses_directory_metadata():
    response = client.get("/api/v1/overview/validators?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    validators = body["data"]["validators"]
    present = {int(entry["id"].split("_")[1]): entry for entry in validators}
    assert present, "Expected at least one validator in overview response"

    known_uids = {124, 129, 133, 135, 137}
    matched = known_uids & set(present.keys())
    assert matched, "Overview payload did not include any known validators"

    for uid in matched:
        metadata = get_validator_metadata(uid)
        entry = present[uid]
        assert entry["name"] == metadata["name"]
        assert entry["icon"] == metadata["image"]
        assert entry["coldkey"] == metadata["coldkey"]
        assert entry["hotkey"] == metadata["hotkey"]


def test_round_validators_uses_directory_metadata():
    # Fetch a recent round to inspect its validators
    rounds_response = client.get("/api/v1/rounds?limit=1")
    assert rounds_response.status_code == 200
    rounds_body = rounds_response.json()
    validator_round_id = rounds_body["data"]["rounds"][0]["id"]

    validators_response = client.get(f"/api/v1/rounds/{validator_round_id}/validators")
    assert validators_response.status_code == 200
    validators_body = validators_response.json()
    assert validators_body["success"] is True

    for uid, metadata in {
        124: get_validator_metadata(124),
        129: get_validator_metadata(129),
        133: get_validator_metadata(133),
        135: get_validator_metadata(135),
        137: get_validator_metadata(137),
    }.items():
        validator = _find_validator(
            validators_body["data"]["validators"],
            f"validator_{uid}",
        )
        assert validator["name"] == metadata["name"]
        assert validator["hotkey"] == metadata["hotkey"]
        assert validator["coldkey"] == metadata["coldkey"]
        assert validator["icon"] == metadata["image"]
