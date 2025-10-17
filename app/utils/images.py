from __future__ import annotations

from typing import Optional

from app.models.core import MinerInfo

DEFAULT_VALIDATOR_IMAGE = "/validators/Other.png"
VALIDATOR_IMAGE_OVERRIDES = {
    "autoppia": "/validators/Autoppia.png",
    "roundtable21": "/validators/RoundTable21.png",
    "round-table21": "/validators/RoundTable21.png",
    "tao5": "/validators/tao5.png",
    "kraken": "/validators/Kraken.png",
    "yuma": "/validators/Yuma.png",
}

SOTA_IMAGE_OVERRIDES = {
    "openai": "/sota/openai.webp",
    "anthropic": "/sota/anthropic.webp",
    "browser-use": "/sota/browser-use.webp",
    "stagehand": "/sota/stagehand.webp",
    "bittensor": "/sota/bittensor.webp",
}


def _slugify(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "-")
        .replace("_", "-")
    )


def resolve_agent_image(info: Optional[MinerInfo], existing: Optional[str] = None) -> str:
    """
    Determine the most appropriate image URL for a miner/agent.

    The function normalizes SOTA agents so that we return the bundled assets
    stored in the frontend's `public/sota` directory. For non-SOTA miners it
    prefers any explicit `agent_image` value, falling back to the supplied
    `existing` string (if provided) or an empty string.
    """
    if info is None:
        return existing or ""

    if info.is_sota:
        candidates = [
            info.agent_name or "",
            info.provider or "",
            existing or "",
        ]

        for candidate in candidates:
            if not candidate:
                continue
            slug = _slugify(candidate)
            if slug in SOTA_IMAGE_OVERRIDES:
                return SOTA_IMAGE_OVERRIDES[slug]
            if slug.startswith("sota/"):
                # already mapped to a sota asset
                return f"/{slug}"

        if existing:
            # Preserve explicitly configured assets (e.g., /sota/*.webp)
            if existing.startswith("/"):
                return existing
            slug = _slugify(existing)
            if slug in SOTA_IMAGE_OVERRIDES:
                return SOTA_IMAGE_OVERRIDES[slug]

        slug = _slugify(info.agent_name or info.provider or "sota-agent")
        return f"/sota/{slug}.webp"

    # Non-SOTA – return the stored image when available
    if info.agent_image:
        return info.agent_image

    return existing or ""


def resolve_validator_image(name: Optional[str], existing: Optional[str] = None) -> str:
    """
    Determine the best image for a validator card.

    We preserve explicit assets when available and otherwise fall back to
    bundled validator logos keyed by name.
    """
    candidate = (existing or "").strip()
    if candidate and candidate != "/validators/Autoppia.png":
        return candidate

    if name:
        slug = _slugify(name)
        if slug in VALIDATOR_IMAGE_OVERRIDES:
            return VALIDATOR_IMAGE_OVERRIDES[slug]

    if candidate:
        return candidate

    return DEFAULT_VALIDATOR_IMAGE
