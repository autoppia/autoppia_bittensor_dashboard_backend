from __future__ import annotations

from hashlib import sha256
from typing import Optional

from app.config import settings
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

FALLBACK_MINER_IMAGES = tuple(f"/miners/{index}.svg" for index in range(50))


def _slugify(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "-")
        .replace("_", "-")
    )


def _ensure_absolute_url(candidate: Optional[str]) -> str:
    """
    Convert relative asset paths into fully qualified URLs using the configured asset base.
    """
    if not candidate:
        return ""
    candidate = candidate.strip()
    if not candidate:
        return ""
    if candidate.startswith(("http://", "https://", "data:")):
        # Rewrite GitHub blob URLs to raw content
        # e.g., https://github.com/org/repo/blob/branch/path -> https://raw.githubusercontent.com/org/repo/branch/path
        if candidate.startswith("https://github.com/") and "/blob/" in candidate:
            return candidate.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
        return candidate
    if candidate.startswith("//"):
        return f"https:{candidate}"
    base = settings.ASSET_BASE_URL.rstrip("/") if settings.ASSET_BASE_URL else ""
    normalized = candidate.lstrip("/")
    if base:
        return f"{base}/{normalized}"
    return f"/{normalized}"


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
                return _ensure_absolute_url(SOTA_IMAGE_OVERRIDES[slug])
            if slug.startswith("sota/"):
                # already mapped to a sota asset
                return _ensure_absolute_url(f"/{slug}")

        if existing:
            # Preserve explicitly configured assets (e.g., /sota/*.webp)
            if existing.startswith("/"):
                return _ensure_absolute_url(existing)
            slug = _slugify(existing)
            if slug in SOTA_IMAGE_OVERRIDES:
                return _ensure_absolute_url(SOTA_IMAGE_OVERRIDES[slug])

        slug = _slugify(info.agent_name or info.provider or "sota-agent")
        return _ensure_absolute_url(f"/sota/{slug}.webp")

    # Non-SOTA – return the stored image when available
    if info.agent_image:
        return _ensure_absolute_url(info.agent_image)

    return _ensure_absolute_url(_fallback_miner_image(info, existing))


def _fallback_miner_image(info: Optional[MinerInfo], existing: Optional[str]) -> str:
    """
    Provide a deterministic fallback miner image when none is supplied.

    - Preserve an explicitly supplied `existing` asset when present.
    - Choose a pseudo-random image from the `/miners` set based on stable miner identifiers
      (uid, hotkey, agent name, provider) so the same miner keeps the same asset between requests.
    """
    if existing:
        return _ensure_absolute_url(existing)

    identifier = None
    if info:
        candidates = [
            getattr(info, "hotkey", None),
            str(info.uid) if getattr(info, "uid", None) is not None else None,
            getattr(info, "agent_name", None),
            getattr(info, "provider", None),
        ]
        for candidate in candidates:
            if candidate:
                identifier = str(candidate).strip()
                if identifier:
                    break

    if not identifier:
        identifier = "autoppia-miner"

    digest = sha256(identifier.encode("utf-8")).digest()
    index = digest[0] % len(FALLBACK_MINER_IMAGES)
    return FALLBACK_MINER_IMAGES[index]


def resolve_validator_image(name: Optional[str], existing: Optional[str] = None) -> str:
    """
    Determine the best image for a validator card.

    We preserve explicit assets when available and otherwise fall back to
    bundled validator logos keyed by name.
    """
    candidate = (existing or "").strip()
    if candidate and candidate != "/validators/Autoppia.png":
        return _ensure_absolute_url(candidate)

    if name:
        slug = _slugify(name)
        if slug in VALIDATOR_IMAGE_OVERRIDES:
            return _ensure_absolute_url(VALIDATOR_IMAGE_OVERRIDES[slug])

    if candidate:
        return _ensure_absolute_url(candidate)

    return _ensure_absolute_url(DEFAULT_VALIDATOR_IMAGE)
