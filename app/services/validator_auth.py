from __future__ import annotations

import base64
import threading
import time
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request, status

from app.config import settings

VALIDATOR_HOTKEY_HEADER = "x-validator-hotkey"
VALIDATOR_SIGNATURE_HEADER = "x-validator-signature"


class ValidatorAuthError(Exception):
    """Base class for validator authentication issues."""


class InvalidSignatureError(ValidatorAuthError):
    """Raised when signature verification fails."""


class StakeTooLowError(ValidatorAuthError):
    """Raised when the validator stake does not meet the minimum threshold."""


class AuthUnavailableError(ValidatorAuthError):
    """Raised when on-chain data cannot be fetched."""


class ValidatorAuthService:
    def __init__(self) -> None:
        self._cache_ttl = max(1, int(settings.VALIDATOR_AUTH_CACHE_TTL or 180))
        self._cache_lock = threading.Lock()
        self._stakes_cache: Dict[str, float] = {}
        self._cache_expiry = 0.0

    @staticmethod
    def _signature_bytes(signature_b64: str) -> bytes:
        try:
            return base64.b64decode(signature_b64, validate=True)
        except Exception as exc:  # pragma: no cover - defensive
            raise InvalidSignatureError("Signature must be a valid base64-encoded string") from exc

    def _load_metagraph_stakes(self) -> Dict[str, float]:
        try:
            import bittensor as bt  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise AuthUnavailableError(f"Bittensor library unavailable: {exc}") from exc

        subtensor_kwargs: Dict[str, str] = {}
        if settings.SUBTENSOR_NETWORK:
            subtensor_kwargs["network"] = settings.SUBTENSOR_NETWORK
        if settings.SUBTENSOR_ENDPOINT:
            subtensor_kwargs["chain_endpoint"] = settings.SUBTENSOR_ENDPOINT

        try:
            subtensor = bt.subtensor(**subtensor_kwargs)  # type: ignore[attr-defined]
            metagraph = subtensor.metagraph(netuid=settings.VALIDATOR_NETUID)
        except Exception as exc:  # pragma: no cover - defensive
            raise AuthUnavailableError(f"Unable to fetch metagraph: {exc}") from exc

        stakes: Dict[str, float] = {}
        hotkeys = getattr(metagraph, "hotkeys", []) or []
        stake_values = getattr(metagraph, "S", []) or []
        for index, hotkey in enumerate(hotkeys):
            raw_value: Optional[float] = None
            if index < len(stake_values):
                candidate = stake_values[index]
                try:
                    raw_value = float(candidate.item()) if hasattr(candidate, "item") else float(candidate)
                except Exception:
                    raw_value = None
            stakes[str(hotkey)] = float(raw_value or 0.0)
        return stakes

    def _get_cached_stakes(self) -> Dict[str, float]:
        now = time.time()
        if now < self._cache_expiry and self._stakes_cache:
            return self._stakes_cache

        with self._cache_lock:
            if now < self._cache_expiry and self._stakes_cache:
                return self._stakes_cache
            self._stakes_cache = self._load_metagraph_stakes()
            self._cache_expiry = time.time() + self._cache_ttl
            return self._stakes_cache

    def verify_signature(self, *, hotkey: str, signature_b64: str) -> None:
        try:
            import bittensor as bt  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise AuthUnavailableError(f"Bittensor library unavailable: {exc}") from exc

        signature = self._signature_bytes(signature_b64)
        message_bytes = settings.VALIDATOR_AUTH_MESSAGE.encode("utf-8")

        try:
            keypair = bt.Keypair(ss58_address=hotkey)  # type: ignore[attr-defined]
        except Exception as exc:
            raise InvalidSignatureError(f"Invalid validator hotkey address: {exc}") from exc

        if not keypair.verify(message_bytes, signature):
            raise InvalidSignatureError("Signature verification failed")

    def ensure_minimum_stake(self, hotkey: str) -> float:
        try:
            minimum = float(settings.MIN_VALIDATOR_STAKE)
        except (TypeError, ValueError):
            minimum = 0.0

        if minimum <= 0:
            return minimum

        stakes = self._get_cached_stakes()
        stake = stakes.get(hotkey)
        if stake is None:
            raise StakeTooLowError("Validator hotkey not found in metagraph")
        if stake < minimum:
            raise StakeTooLowError(
                f"Validator stake {stake:.3f} is below the required minimum {minimum:.3f}"
            )
        return stake


_validator_auth_service = ValidatorAuthService()


def get_validator_auth_service() -> ValidatorAuthService:
    return _validator_auth_service


async def require_validator_auth(
    request: Request,
    service: ValidatorAuthService = Depends(get_validator_auth_service),
) -> None:
    if settings.TESTING:
        return

    hotkey = request.headers.get(VALIDATOR_HOTKEY_HEADER)
    signature = request.headers.get(VALIDATOR_SIGNATURE_HEADER)

    if not hotkey or not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Validator authentication headers are required",
        )

    try:
        service.verify_signature(hotkey=hotkey, signature_b64=signature)
        service.ensure_minimum_stake(hotkey)
    except InvalidSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except StakeTooLowError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AuthUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
