from __future__ import annotations

import os
from typing import Dict, List, Tuple

import pytest

from app.services.validator.validator_auth import ValidatorAuthService
from app.config import settings

# Skip live stake tests unless explicitly enabled (they hit the chain)
if os.getenv("RUN_LIVE_TESTS", "0").lower() not in ("1", "true", "yes", "on"):
    import pytest  # noqa: WPS433

    pytest.skip("Skipping live stake tests (set RUN_LIVE_TESTS=1 to run)", allow_module_level=True)


@pytest.mark.slow
def test_live_validator_stakes_and_threshold_printout():
    """
    Integration check: fetch on-chain stakes and print validators above threshold.

    - Ensures we can load the metagraph and coerce stakes per hotkey.
    - Cross-checks a sample of values against bittensor.metagraph.stake.
    - Prints the validator hotkeys with stake strictly greater than MIN_VALIDATOR_STAKE.

    This test does not assert on the count of validators crossing the threshold
    to avoid flakiness across networks, but it validates that at least some
    stakes are present and non-negative.
    """

    # Ensure DATABASE_URL is set (PostgreSQL required for tests)
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not configured - PostgreSQL required for tests")

    # Network selection: default to finney unless overridden via env
    network = settings.SUBTENSOR_NETWORK or "finney"
    netuid = int(settings.VALIDATOR_NETUID)
    threshold = float(settings.MIN_VALIDATOR_STAKE or 0.0)

    service = ValidatorAuthService()
    stakes_map: Dict[str, float] = service._load_metagraph_stakes()

    # Basic sanity: we must have some stakes mapped
    assert isinstance(stakes_map, dict) and len(stakes_map) > 0
    assert all(isinstance(k, str) and isinstance(v, float) and v >= 0.0 for k, v in stakes_map.items())

    # Pull metagraph directly for cross-check and validator filtering
    import bittensor as bt  # type: ignore

    subtensor = bt.subtensor(network=network)
    mg = subtensor.metagraph(netuid=netuid)

    validator_mask = getattr(mg, "validator_permit", None)
    assert validator_mask is not None, "metagraph.validator_permit should be available"

    # Create (hotkey, stake) pairs for validators
    validators: List[Tuple[str, float]] = []
    for uid, is_validator in enumerate(validator_mask):
        if not is_validator:
            continue
        hotkey = str(mg.hotkeys[uid])
        stake_val = float(mg.stake[uid])
        # Cross-check service map coherence (allow tiny chain rounding jitter)
        mapped = float(stakes_map.get(hotkey, 0.0))
        assert abs(mapped - stake_val) < 2e-2
        validators.append((hotkey, stake_val))

    # Print validators above threshold for operator visibility
    above: List[Tuple[str, float]] = [(hk, s) for hk, s in validators if s > threshold]
    print("Network:", network, "netuid:", netuid)
    print("MIN_VALIDATOR_STAKE:", threshold)
    print("Total validators discovered:", len(validators))
    print("Validators above threshold (hotkey, stake):")
    for hk, s in above:
        print(hk, s)

    # Final sanity: at least one validator exists (regardless of threshold)
    assert len(validators) >= 1
