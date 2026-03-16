from __future__ import annotations

import pytest

from app.models.core import MinerInfo
from app.utils.images import _validate_validator_image_url, resolve_agent_image, sanitize_miner_image

pytestmark = pytest.mark.no_db


def test_sanitize_miner_image_accepts_environment_prefixed_s3_path() -> None:
    url = "https://autoppia-subnet.s3.eu-west-1.amazonaws.com/production/images-miners/autoppia.png"
    assert sanitize_miner_image(url) == url


def test_sanitize_miner_image_rejects_other_s3_folders() -> None:
    url = "https://autoppia-subnet.s3.eu-west-1.amazonaws.com/production/gifs/autoppia.png"
    assert sanitize_miner_image(url) == ""


def test_resolve_agent_image_uses_valid_prefixed_s3_image() -> None:
    url = "https://autoppia-subnet.s3.eu-west-1.amazonaws.com/production/images-miners/autoppia.png"
    miner = MinerInfo(
        uid=48,
        hotkey="5GWDNpCQYRKjnAsmr32L498ofW82nFMuoES5cyo3XgRTdBxb",
        agent_name="autoppia operator",
        agent_image=url,
        github="https://github.com/autoppia/autoppia_operator/commit/77d1466fdcb1fbda7b3562a871b46643544876f3",
        is_sota=False,
    )
    assert resolve_agent_image(miner) == url


def test_validate_validator_image_url_accepts_environment_prefixed_s3_path() -> None:
    url = "https://autoppia-subnet.s3.eu-west-1.amazonaws.com/production/images-validators/autoppia.png"
    assert _validate_validator_image_url(url) == url
