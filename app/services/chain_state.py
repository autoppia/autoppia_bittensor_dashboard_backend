from __future__ import annotations

from typing import Optional

from app.config import settings


def get_current_block() -> Optional[int]:
    """
    Return current chain block height using bittensor if available.

    Falls back to None if bittensor is not installed or the call fails.
    """
    try:
        import bittensor as bt  # type: ignore
    except Exception:
        return None

    kwargs = {}
    if settings.SUBTENSOR_NETWORK:
        kwargs["network"] = settings.SUBTENSOR_NETWORK
    if settings.SUBTENSOR_ENDPOINT:
        kwargs["chain_endpoint"] = settings.SUBTENSOR_ENDPOINT

    try:
        subtensor = bt.subtensor(**kwargs)  # type: ignore[attr-defined]
        # Prefer explicit getter when present; fallback to metagraph
        try:
            block = int(subtensor.get_current_block())
            return block
        except Exception:
            try:
                mg = subtensor.metagraph(settings.VALIDATOR_NETUID)
                return int(getattr(mg, "block", 0) or 0)
            except Exception:
                return None
    except Exception:
        return None

