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
    "autoppia-subnet.s3.eu-west-1.amazonaws.com",  # S3 bucket for validators/miners/gifs
    "autoppia-subnet.s3.amazonaws.com",  # S3 default region URL
}


def _slugify(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


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
        return url.replace(
            "https://github.com/", "https://raw.githubusercontent.com/"
        ).replace("/blob/", "/")
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
            # Block access to backups folder for security
            if path.startswith("/backups/"):
                return ""
            # For S3 URLs, return the FULL URL (not relative path)
            # This allows Next.js Image component to load from S3
            hostname_lower = (parsed.hostname or "").lower()
            if (
                "s3.amazonaws.com" in hostname_lower
                or "s3.eu-west-1.amazonaws.com" in hostname_lower
            ):
                return rewritten  # Return full S3 URL
            # For other allowed hosts, convert to relative path
            query = f"?{parsed.query}" if parsed.query else ""
            return _normalize_relative_path(f"{path}{query}")
        return ""

    return _normalize_relative_path(value)


def _ensure_absolute_url(
    candidate: Optional[str], fallback: Optional[str] = None
) -> str:
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


def resolve_agent_image(
    info: Optional[MinerInfo], existing: Optional[str] = None
) -> str:
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
                return _ensure_absolute_url(
                    SOTA_IMAGE_OVERRIDES[slug], fallback=existing_url
                )
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
    Validate that miner image URL is from authorized S3 paths ONLY.

    ONLY allows ABSOLUTE S3 URLs:
    - https://autoppia-subnet.s3.eu-west-1.amazonaws.com/images-miner/*
    - https://autoppia-subnet.s3.amazonaws.com/images-miner/*

    Blocks everything else:
    - ❌ GitHub, imgur, other external URLs
    - ❌ Other S3 folders (backups, gifs, images-validator, etc.)
    - ❌ Relative paths (/miners/1.svg) - miners must use S3

    Returns empty string if invalid (triggers fallback to generated image).
    """
    if not candidate or not isinstance(candidate, str):
        return ""

    value = candidate.strip()
    if not value:
        return ""

    # REJECT relative paths - miners MUST use S3
    if not value.startswith("http"):
        return ""

    # ONLY allow HTTPS S3 URLs in images-miner folder
    if value.startswith("https://"):
        try:
            parsed = urlparse(value)
            hostname = (parsed.hostname or "").lower()
            path = parsed.path or "/"

            # MUST be our S3 bucket
            if hostname not in (
                "autoppia-subnet.s3.eu-west-1.amazonaws.com",
                "autoppia-subnet.s3.amazonaws.com",
            ):
                return ""  # ❌ Reject external URLs

            # MUST be in /images-miner/ folder
            if not path.startswith("/images-miner/"):
                return ""  # ❌ Reject other S3 folders

            # ✅ Valid S3 miner image - return full URL
            return value
        except Exception:
            return ""

    # Reject anything else (http://, malformed, etc.)
    return ""


def _fallback_miner_image(info: Optional[MinerInfo], existing: Optional[str]) -> str:
    if existing:
        return _ensure_absolute_url(existing)

    # Use UID directly for deterministic image selection
    # UID 1 -> /miners/1.svg, UID 80 -> /miners/30.svg (80 % 50), etc.
    if info and hasattr(info, "uid") and info.uid is not None:
        index = int(info.uid) % len(FALLBACK_MINER_IMAGES)
        return _ensure_absolute_url(FALLBACK_MINER_IMAGES[index])

    # Fallback: use hash if UID is not available
    identifier: Optional[str] = None
    if info:
        candidates = [
            getattr(info, "hotkey", None),
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


def _validate_validator_image_url(url: Optional[str]) -> Optional[str]:
    """
    Validate that validator image URL is from authorized S3 paths ONLY.

    ONLY allows ABSOLUTE S3 URLs:
    - https://autoppia-subnet.s3.eu-west-1.amazonaws.com/images-validator/*
    - https://autoppia-subnet.s3.amazonaws.com/images-validator/*

    Blocks everything else:
    - ❌ GitHub, imgur, other external URLs
    - ❌ Other S3 folders (backups, gifs, images-miner, etc.)
    - ❌ Relative paths (/validators/Other.png) - validators must use S3
    """
    import logging

    logger = logging.getLogger(__name__)

    if not url:
        logger.debug(f"[_validate_validator_image_url] URL is None or empty")
        return None

    url_clean = url.strip()

    # REJECT relative paths - validators MUST use S3
    if not url_clean.startswith("http"):
        logger.debug(
            f"[_validate_validator_image_url] Rejecting relative path: {url_clean}"
        )
        return None

    # ONLY allow HTTPS S3 URLs in images-validator folder
    if url_clean.startswith("https://"):
        try:
            parsed = urlparse(url_clean)
            hostname = (parsed.hostname or "").lower()
            path = parsed.path or "/"

            # MUST be our S3 bucket
            if hostname not in (
                "autoppia-subnet.s3.eu-west-1.amazonaws.com",
                "autoppia-subnet.s3.amazonaws.com",
            ):
                logger.debug(
                    f"[_validate_validator_image_url] Rejecting external hostname: {hostname}"
                )
                return None  # ❌ Reject external URLs

            # MUST be in /images-validator/ folder
            if not path.startswith("/images-validator/"):
                logger.debug(
                    f"[_validate_validator_image_url] Rejecting non-validator path: {path}"
                )
                return None  # ❌ Reject other S3 folders

            # ✅ Valid S3 validator image
            logger.debug(
                f"[_validate_validator_image_url] ✅ Valid S3 URL: {url_clean}"
            )
            return url_clean
        except Exception as exc:
            logger.warning(
                f"[_validate_validator_image_url] Failed to parse URL {url_clean}: {exc}"
            )
            return None

    # Reject anything else (http://, malformed, etc.)
    logger.debug(
        f"[_validate_validator_image_url] Rejecting non-https URL: {url_clean}"
    )
    return None


def resolve_validator_image(name: Optional[str], existing: Optional[str] = None) -> str:
    """
    Determine the best image for a validator card.

    Priority:
    1. Use existing URL from validator_snapshot.image_url (ONLY if from S3 images-validator/)
    2. Use name-based override if configured
    3. Use default placeholder
    """
    import logging

    logger = logging.getLogger(__name__)

    # Validate and sanitize the existing URL
    validated_existing = _validate_validator_image_url(existing)
    default_url = _ensure_absolute_url(DEFAULT_VALIDATOR_IMAGE)

    logger.debug(
        f"[resolve_validator_image] name={name}, existing={existing}, "
        f"validated={validated_existing}, default={default_url}"
    )

    # PRIORITY 1: Always prefer explicit image_url from validator (if valid)
    if validated_existing:
        logger.debug(
            f"[resolve_validator_image] Using validated S3 URL: {validated_existing}"
        )
        return validated_existing

    # PRIORITY 2: Use name-based override if no explicit image
    if name:
        slug = _slugify(name)
        if slug in VALIDATOR_IMAGE_OVERRIDES:
            override = _ensure_absolute_url(
                VALIDATOR_IMAGE_OVERRIDES[slug], fallback=default_url
            )
            logger.debug(
                f"[resolve_validator_image] Using name override for '{slug}': {override}"
            )
            return override

    # PRIORITY 3: Default placeholder
    logger.debug(f"[resolve_validator_image] Using default placeholder: {default_url}")
    return default_url
