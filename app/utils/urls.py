from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode


TAOSTATS_SUBNET_BASE = "https://taostats.io/subnets/36/metagraph"


def build_taostats_miner_url(hotkey: Optional[str]) -> Optional[str]:
    """
    Construct a taostats link filtered by miner hotkey.

    Args:
        hotkey: SS58 hotkey address reported by the validator payload.

    Returns:
        Fully-qualified taostats URL with the filter query parameter applied,
        or None when the hotkey is missing/blank.
    """
    if hotkey is None:
        return None

    normalized = hotkey.strip()
    if not normalized:
        return None

    query = urlencode(
        {
            "order": "stake:desc",
            "filter": normalized,
        }
    )
    return f"{TAOSTATS_SUBNET_BASE}?{query}"
