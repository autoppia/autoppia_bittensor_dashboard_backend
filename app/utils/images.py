from __future__ import annotations

from hashlib import sha256
from typing import Optional
from urllib.parse import urlparse

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

DEFAULT_ALLOWED_IMAGE_HOSTS = {
    "infinitewebarena.autoppia.com",
    "dev-infinitewebarena.autoppia.com",
}


def _slugify(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace(" ", "-")
        .replace("_", "-")
    )


def _normalize_allowed_host(entry: Optional[str]) -> Optional[str]:
    if entry is None:
        return None
    value = entry.strip().lower()
    return value or None


def _build_allowed_hosts() -> set[str]:
    hosts: set[str] = set(DEFAULT_ALLOWED_IMAGE_HOSTS)
    extra_hosts = getattr(settings, "ALLOWED_IMAGE_HOSTS", None) or []
    for candidate in extra_hosts:
        normalized = _normalize_allowed_host(str(candidate))
        if normalized:
            hosts.add(normalized)

    base_url = getattr(settings, "ASSET_BASE_URL", "") or ""
    try:
        parsed = urlparse(str(base_url))
        if parsed.hostname:
            hosts.add(parsed.hostname.lower())
    except Exception:
        pass

    return hosts


_ALLOWED_IMAGE_HOSTS = _build_allowed_hosts()


def _is_allowed_host(hostname: Optional[str]) -> bool:
    if not hostname:
        return False
    host = hostname.lower()
    if host in _ALLOWED_IMAGE_HOSTS:
        return True
    for allowed in _ALLOWED_IMAGE_HOSTS:
        if allowed.startswith("*.") and host.endswith(allowed[1:]):
            return True
    return False


def _rewrite_github_blob(url: str) -> str:
    if url.startswith("https://github.com/") and "/blob/" in url:
        return url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
    return url


def _normalize_relative_path(value: str) -> str:
    normalized = value.lstrip("/")
    return f"/{normalized}" if normalized else "/"


def _sanitize_url(candidate: Optional[str]) -> str:
    if not candidate:
        return ""
    value = candidate.strip()
    if not value:
        return ""

    if value.startswith("data:"):
        return value if value.startswith("data:image/") else ""

    if value.startswith("//"):
        value = f"https:{value}"

    if value.startswith("http://") or value.startswith("https://"):
        rewritten = _rewrite_github_blob(value)
        try:
            parsed = urlparse(rewritten)
        except Exception:
            return ""
        if _is_allowed_host(parsed.hostname):
            path = parsed.path or "/"
            query = f"?{parsed.query}" if parsed.query else ""
            return _normalize_relative_path(f"{path}{query}")
        return ""

    return _normalize_relative_path(value)


def _ensure_absolute_url(candidate: Optional[str], fallback: Optional[str] = None) -> str:
    primary = _sanitize_url(candidate)
    if primary:
        return primary
    return _sanitize_url(fallback)


def normalize_asset_path(candidate: Optional[str]) -> str:
    """
    Public helper that normalizes any candidate asset reference into a safe
    root-relative path (or empty string when not usable).
    """
    return _sanitize_url(candidate)


def resolve_agent_image(info: Optional[MinerInfo], existing: Optional[str] = None) -> str:
    """
    Determine the most appropriate image URL for a miner/agent.

    The function normalizes SOTA agents so that we return the bundled assets
    stored in the frontend's `public/sota` directory. For non-SOTA miners it
    prefers any explicit `agent_image` value, falling back to the supplied
    `existing` string (if provided) or an empty string.
    """

    existing_url = _ensure_absolute_url(existing)

    if info is None:
        return existing_url

    if info.is_sota:
        candidates = [
            info.agent_name or "",
            info.provider or "",
            existing_url or "",
        ]

        for candidate in candidates:
            if not candidate:
                continue
            slug = _slugify(candidate)
            if slug in SOTA_IMAGE_OVERRIDES:
                return _ensure_absolute_url(SOTA_IMAGE_OVERRIDES[slug], fallback=existing_url)
            if slug.startswith("sota/"):
                return _ensure_absolute_url(f"/{slug}", fallback=existing_url)

        if existing_url:
            return existing_url

        slug = _slugify(info.agent_name or info.provider or "sota-agent")
        return _ensure_absolute_url(f"/sota/{slug}.webp", fallback=existing_url)

    if info.agent_image:
        # Enforce miner image host restriction
        url = sanitize_miner_image(info.agent_image)
        return _ensure_absolute_url(url, fallback=existing_url)

    fallback_path = _fallback_miner_image(info, existing_url)
    return _ensure_absolute_url(fallback_path, fallback=existing_url)


def sanitize_miner_image(candidate: Optional[str]) -> str:
    """
    Enforce miner image allowed hosts. If not allowed, return the blocked asset URL.

    - Root-relative paths are accepted as-is (served by ASSET_BASE_URL).
    - Absolute URLs must have a hostname in settings.MINER_IMAGE_ALLOWED_HOSTS.
    - Otherwise returns ASSET_BASE_URL/BLOCKED_IMAGE_PATH.
    """
    blocked = _ensure_absolute_url(settings.BLOCKED_IMAGE_PATH or "/blocked.png")
    if not candidate or not isinstance(candidate, str):
        return ""
    value = candidate.strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = f"https:{value}"
    if value.startswith("/"):
        return _ensure_absolute_url(value)
    try:
        parsed = urlparse(value)
    except Exception:
        return blocked
    host = (parsed.hostname or "").lower()
    allowed = {h.lower() for h in (settings.MINER_IMAGE_ALLOWED_HOSTS or [])}
    if host and host in allowed:
        return _ensure_absolute_url(value)
    return blocked


def _fallback_miner_image(info: Optional[MinerInfo], existing: Optional[str]) -> str:
    if existing:
        return _ensure_absolute_url(existing)

    identifier: Optional[str] = None
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
    return _ensure_absolute_url(FALLBACK_MINER_IMAGES[index])


def resolve_validator_image(name: Optional[str], existing: Optional[str] = None) -> str:
    """
    Determine the best image for a validator card.

    We preserve explicit assets when available and otherwise fall back to
    bundled validator logos keyed by name.
    """

    existing_url = _ensure_absolute_url(existing)
    default_url = _ensure_absolute_url(DEFAULT_VALIDATOR_IMAGE)

    if name:
        slug = _slugify(name)
        if slug in VALIDATOR_IMAGE_OVERRIDES:
            return _ensure_absolute_url(VALIDATOR_IMAGE_OVERRIDES[slug], fallback=existing_url or default_url)

    if existing_url:
        return existing_url

    return default_url
