from __future__ import annotations

import pytest
from botocore.stub import Stubber
from sqlalchemy import select

from app.config import settings
from app.db.models import EvaluationResultORM
from app.services import media_storage

from tests.test_validator_endpoints import _make_submission_payload


MINIMAL_GIF = (
    b"GIF89a"
    b"\x01\x00\x01\x00"
    b"\x80"
    b"\x00"
    b"\x00"
    b"\x00\x00\x00"
    b"\xff\xff\xff"
    b"\x21\xf9\x04\x00\x00\x00\x00\x00"
    b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00"
    b"\x02\x02\x44\x01\x00"
    b"\x3b"
)


@pytest.mark.asyncio
async def test_uploads_gif_and_returns_s3_url(client, db_session):
    payload = _make_submission_payload("205")
    submit_response = await client.post("/api/v1/rounds/submit", json=payload)
    assert submit_response.status_code == 200

    evaluation_id = payload["evaluation_results"][0]["evaluation_id"]
    files = {"gif": ("recording.gif", MINIMAL_GIF, "image/gif")}

    object_key = media_storage.build_gif_key(evaluation_id)
    client_stub = media_storage.get_s3_client()
    stubber = Stubber(client_stub)
    expected_params = {
        "Bucket": settings.AWS_S3_BUCKET,
        "Key": object_key,
        "Body": MINIMAL_GIF,
        "ContentType": "image/gif",
    }
    stubber.add_response("put_object", {"ETag": '"etag"'}, expected_params)
    stubber.activate()

    try:
        upload_response = await client.post(
            f"/api/v1/evaluations/{evaluation_id}/gif",
            files=files,
        )
    finally:
        stubber.assert_no_pending_responses()
        stubber.deactivate()

    assert upload_response.status_code == 201
    body = upload_response.json()
    assert body["success"] is True
    gif_url = body["data"]["gifUrl"]
    assert gif_url == media_storage.build_public_url(object_key)

    stored_row = await db_session.scalar(
        select(EvaluationResultORM).where(
            EvaluationResultORM.evaluation_id == evaluation_id
        )
    )
    assert stored_row is not None
    assert stored_row.gif_recording == gif_url


@pytest.mark.asyncio
async def test_upload_rejects_non_gif_images(client):
    payload = _make_submission_payload("206")
    submit_response = await client.post("/api/v1/rounds/submit", json=payload)
    assert submit_response.status_code == 200

    evaluation_id = payload["evaluation_results"][0]["evaluation_id"]
    files = {"gif": ("not-a-gif.png", b"\x89PNG\r\n\x1a\n", "image/png")}

    response = await client.post(
        f"/api/v1/evaluations/{evaluation_id}/gif",
        files=files,
    )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Only GIF images are supported"
