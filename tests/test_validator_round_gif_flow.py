from __future__ import annotations

import base64

import pytest
from botocore.stub import Stubber
from sqlalchemy import select

from app.config import settings
from app.db.models import EvaluationORM
from app.db.session import AsyncSessionLocal
from app.services import media_storage

pytestmark = pytest.mark.xfail(reason="Legacy gif flow payload contract", strict=False)

# Pre-converted GIF bytes generated from
# https://github.com/autoppia/autoppia_web_agents_subnet/blob/main/icon48.png
ICON48_GIF_BASE64 = """
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
"""

ICON48_GIF_BYTES = base64.b64decode("".join(ICON48_GIF_BASE64.split()))


@pytest.mark.asyncio
async def test_validator_round_flow_with_gif_upload(client, db_session, monkeypatch):
    original_prefix = settings.AWS_S3_GIF_PREFIX
    prefixed_value = "tests" if not original_prefix else f"tests/{original_prefix.strip('/')}"
    settings.AWS_S3_GIF_PREFIX = prefixed_value

    try:
        base_payload = {
            "round": {
                "validator_round_id": "round_gif_flow",
                "round": 777,
                "validators": [
                    {
                        "uid": 4001,
                        "hotkey": "validator_hotkey_gif",
                        "coldkey": "validator_coldkey_gif",
                        "stake": 2500.0,
                        "vtrust": 0.93,
                        "name": "Gif Guardian",
                        "version": "8.0.0",
                    }
                ],
                "start_block": 4200,
                "start_epoch": 42,
                "n_tasks": 1,
                "started_at": 1_700_100_000.0,
                "status": "in_progress",
            },
            "tasks": [
                {
                    "task_id": "task_gif_flow",
                    "validator_round_id": "round_gif_flow",
                    "is_web_real": False,
                    "web_project_id": None,
                    "url": "https://example.com/gif-flow",
                    "prompt": "Validate GIF upload workflow.",
                    "specifications": {},
                    "tests": [],
                    "use_case": {"name": "GifFlow"},
                }
            ],
            "agent_run": {
                "agent_run_id": "agent_run_gif_flow",
                "validator_round_id": "round_gif_flow",
                "validator_uid": 4001,
                "miner_uid": 501,
                "miner_info": {
                    "uid": 501,
                    "hotkey": "miner_hotkey_gif_flow",
                    "coldkey": "miner_coldkey_gif_flow",
                    "agent_name": "Miner GIF Flow",
                    "agent_image": "",
                    "github": "https://github.com/miner-gif-flow",
                    "is_sota": False,
                },
                "is_sota": False,
                "version": "1.0",
                "task_ids": ["task_gif_flow"],
                "started_at": 1_700_100_050.0,
                "metadata": {},
            },
        }

        start_payload = {
            "validator_round_id": base_payload["round"]["validator_round_id"],
            "round": base_payload["round"],
        }
        # Ensure chain inside the requested round window (777)
        from app.config import settings as _settings

        blocks_per_round = int(_settings.ROUND_SIZE_EPOCHS * _settings.BLOCKS_PER_EPOCH)
        dz = int(_settings.DZ_STARTING_BLOCK)

        def inside_round(n: int) -> int:
            return dz + (n - 1) * blocks_per_round + 1

        monkeypatch.setattr(
            "app.api.validator.validator_round_handlers_lifecycle.get_current_block",
            lambda: inside_round(777),
        )

        start_response = await client.post("/api/v1/validator-rounds/start", json=start_payload)
        assert start_response.status_code == 200

        tasks_response = await client.post(
            f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/tasks",
            json={"tasks": base_payload["tasks"]},
        )
        assert tasks_response.status_code == 200

        start_agent_response = await client.post(
            f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/agent-runs/start",
            json={"agent_run": base_payload["agent_run"]},
        )
        assert start_agent_response.status_code == 200

        task_payload = base_payload["tasks"][0]
        evaluation_payload = {
            "task": task_payload,
            "task_solution": {
                "solution_id": "solution_gif_flow",
                "task_id": task_payload["task_id"],
                "validator_round_id": base_payload["round"]["validator_round_id"],
                "agent_run_id": base_payload["agent_run"]["agent_run_id"],
                "miner_uid": 501,
                "validator_uid": 4001,
                "actions": [],
                "web_agent_id": "miner-gif-flow",
            },
            "evaluation_result": {
                "evaluation_id": "evaluation_gif_flow",
                "task_id": task_payload["task_id"],
                "task_solution_id": "solution_gif_flow",
                "validator_round_id": base_payload["round"]["validator_round_id"],
                "agent_run_id": base_payload["agent_run"]["agent_run_id"],
                "miner_uid": 501,
                "validator_uid": 4001,
                "evaluation_score": 0.95,
                "test_results_matrix": [[{"success": True}]],
                "execution_history": [],
                "feedback": None,
                "web_agent_id": "miner-gif-flow",
                "raw_score": 0.95,
                "evaluation_time": 12.0,
                "stats": None,
                "gif_recording": None,
            },
        }

        evaluation_response = await client.post(
            f"/api/v1/validator-rounds/{base_payload['round']['validator_round_id']}/agent-runs/{base_payload['agent_run']['agent_run_id']}/evaluations",
            json=evaluation_payload,
        )
        assert evaluation_response.status_code == 200

        evaluation_id = evaluation_payload["evaluation_result"]["evaluation_id"]

        stored_before = await db_session.scalar(select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id))
        assert stored_before is not None
        assert stored_before.gif_recording is None

        object_key = media_storage.build_gif_key(evaluation_id)
        client_stub = media_storage.get_s3_client()
        stubber = Stubber(client_stub)
        expected_params = {
            "Bucket": settings.AWS_S3_BUCKET,
            "Key": object_key,
            "Body": ICON48_GIF_BYTES,
            "ContentType": "image/gif",
        }
        stubber.add_response("put_object", {"ETag": '"etag"'}, expected_params)
        stubber.activate()

        files = {
            "gif": ("icon48.gif", ICON48_GIF_BYTES, "image/gif"),
        }

        try:
            upload_response = await client.post(
                f"/api/v1/evaluations/{evaluation_id}/gif",
                files=files,
            )
        finally:
            stubber.assert_no_pending_responses()
            stubber.deactivate()

        assert upload_response.status_code == 201
        response_body = upload_response.json()
        assert response_body["success"] is True
        gif_url = response_body["data"]["gifUrl"]
        assert gif_url == media_storage.build_public_url(object_key)
        assert "tests" in gif_url

        async with AsyncSessionLocal() as verify_session:
            stored_after = await verify_session.scalar(select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation_id))
            assert stored_after is not None
            assert stored_after.gif_recording == gif_url
            assert stored_after.validator_round_id == base_payload["round"]["validator_round_id"]
    finally:
        settings.AWS_S3_GIF_PREFIX = original_prefix
