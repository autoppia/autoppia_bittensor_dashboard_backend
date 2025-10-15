"""
Canonical registry of known validators.

This static mapping lets the application attach consistent metadata such as
hotkeys, coldkeys, and image assets regardless of the source round data.
"""
from typing import Dict, Any

# Known validator metadata keyed by validator UID.
VALIDATOR_DIRECTORY: Dict[int, Dict[str, Any]] = {
    124: {
        "uid": 124,
        "name": "Autoppia",
        "hotkey": "5DUmbx...gSDe8j",
        "coldkey": "5DPtMd...LVT3EF",
        "image": "images/icons/validators/Autoppia.png",
    },
    129: {
        "uid": 129,
        "name": "tao5",
        "hotkey": "5CsvRJ...5A2zVp",
        "coldkey": "5EJAqc...6RYzX2",
        "image": "images/icons/validators/tao5.png",
    },
    133: {
        "uid": 133,
        "name": "RoundTable21",
        "hotkey": "5C5hkv...XGsn36",
        "coldkey": "5GZSAg...BMKpGQ",
        "image": "images/icons/validators/RoundTable21.png",
    },
    135: {
        "uid": 135,
        "name": "Kraken",
        "hotkey": "5C5xWa...Vhhs36",
        "coldkey": "5Fuzgv...3Kkrzo",
        "image": "images/icons/validators/Kraken.png",
    },
    137: {
        "uid": 137,
        "name": "Yuma",
        "hotkey": "5DLDdE...GuJjst",
        "coldkey": "5E9fVY...HeYc5p",
        "image": "images/icons/validators/Yuma.png",
    },
}


def get_validator_metadata(validator_uid: int) -> Dict[str, Any]:
    """
    Safely retrieve validator metadata.

    Returns an empty structure with sensible defaults when the validator is
    unknown so downstream code can rely on the expected keys being present.
    """
    default = {
        "uid": validator_uid,
        "name": f"Validator {validator_uid}",
        "hotkey": "",
        "coldkey": "",
        "image": "images/icons/validators/Autoppia.png",
    }
    return VALIDATOR_DIRECTORY.get(validator_uid, default)
