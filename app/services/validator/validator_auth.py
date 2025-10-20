from __future__ import annotations

import base64
import threading
import time
import logging
from typing import Dict, Optional

from fastapi import Depends, HTTPException, Request, status

from app.config import settings

VALIDATOR_HOTKEY_HEADER = "x-validator-hotkey"
VALIDATOR_SIGNATURE_HEADER = "x-validator-signature"

logger = logging.getLogger(__name__)


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
        self._log_signature_payloads = bool(
            str(getattr(settings, "LOG_VALIDATOR_SIGNATURES", "")).lower() not in {"", "0", "false", "none"}
        )

    @staticmethod
    def _redact_signature(signature_b64: str, *, head: int = 8) -> str:
        signature_b64 = signature_b64.strip()
        if not signature_b64:
            return "<empty>"
        if len(signature_b64) <= head:
            return signature_b64
        return f"{signature_b64[:head]}… ({len(signature_b64)} chars)"

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

        hotkeys_raw = getattr(metagraph, "hotkeys", None)
        if hotkeys_raw is None:
            hotkeys = []
        elif hasattr(hotkeys_raw, "tolist"):
            hotkeys = list(hotkeys_raw.tolist())
        elif isinstance(hotkeys_raw, (list, tuple)):
            hotkeys = list(hotkeys_raw)
        else:
            try:
                hotkeys = list(hotkeys_raw)
            except TypeError:
                hotkeys = [hotkeys_raw]

        stake_values: list[object]
        stake_values_raw = getattr(metagraph, "S", None)
        if stake_values_raw is None:
            stake_values = []
        elif hasattr(stake_values_raw, "tolist"):
            stake_values = list(stake_values_raw.tolist())
        elif isinstance(stake_values_raw, (list, tuple)):
            stake_values = list(stake_values_raw)
        else:
            try:
                stake_values = list(stake_values_raw)
            except TypeError:
                stake_values = [stake_values_raw]

        stakes: Dict[str, float] = {}
        for index, hotkey in enumerate(hotkeys):
            raw_value: Optional[float] = None
            if index < len(stake_values):
                candidate = stake_values[index]
                try:
                    raw_value = float(candidate.item()) if hasattr(candidate, "item") else float(candidate)
                except Exception:
                    logger.debug(
                        "Unable to coerce stake value for hotkey=%s candidate=%r",
                        hotkey,
                        candidate,
                        exc_info=True,
                    )
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

        redacted = self._redact_signature(signature_b64)
        logger.debug(
            "Validator signature received for hotkey=%s payload=%s",
            hotkey,
            redacted if self._log_signature_payloads else "<redacted>",
        )

        signature = self._signature_bytes(signature_b64)
        message_bytes = settings.VALIDATOR_AUTH_MESSAGE.encode("utf-8")

        try:
            keypair = bt.Keypair(ss58_address=hotkey)  # type: ignore[attr-defined]
        except Exception as exc:
            raise InvalidSignatureError(f"Invalid validator hotkey address: {exc}") from exc

        try:
            is_valid = bool(keypair.verify(message_bytes, signature))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Validator signature verification failed unexpectedly for hotkey=%s", hotkey)
            raise AuthUnavailableError(f"Signature verification unavailable: {exc}") from exc

        if not is_valid:
            logger.warning("Validator signature did not verify for hotkey=%s", hotkey)
            raise InvalidSignatureError("Signature verification failed")
        logger.info("Validator signature verified successfully for hotkey=%s", hotkey)

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
    if getattr(settings, "AUTH_DISABLED", False):
        return

    hotkey = request.headers.get(VALIDATOR_HOTKEY_HEADER)
    signature = request.headers.get(VALIDATOR_SIGNATURE_HEADER)

    if not hotkey or not signature:
        logger.warning(
            "Validator auth missing header(s): hotkey_present=%s signature_present=%s",
            bool(hotkey),
            bool(signature),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Validator authentication headers are required",
        )

    try:
        service.verify_signature(hotkey=hotkey, signature_b64=signature)
        service.ensure_minimum_stake(hotkey)
    except InvalidSignatureError as exc:
        logger.warning("Validator auth failed: invalid signature for hotkey=%s", hotkey)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except StakeTooLowError as exc:
        logger.warning("Validator auth failed: stake too low for hotkey=%s", hotkey)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AuthUnavailableError as exc:
        logger.error("Validator auth unavailable: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValidatorAuthError as exc:  # pragma: no cover - defensive
        logger.exception("Unexpected validator auth error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Unhandled validator auth failure")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Validator authentication failed unexpectedly",
        ) from exc
