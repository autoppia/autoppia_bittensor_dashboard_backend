from __future__ import annotations

import pytest

from tests.test_validator_endpoints import _make_submission_payload


@pytest.mark.asyncio
async def test_subnet_timeline_reflects_persisted_scores(client):
    first_payload = _make_submission_payload("301")
    second_payload = _make_submission_payload("302")

    first_response = await client.post("/api/v1/rounds/submit", json=first_payload)
    assert first_response.status_code == 200

    second_response = await client.post("/api/v1/rounds/submit", json=second_payload)
    assert second_response.status_code == 200

    timeline_response = await client.get("/api/v1/subnets/test-subnet/timeline?rounds=2&miners=5")
    assert timeline_response.status_code == 200
    body = timeline_response.json()

    assert body["subnet_id"] == "test-subnet"
    assert len(body["timeline"]) == 2
    assert body["meta"]["round_count"] == 2
    assert body["meta"]["start_round"] == 301
    assert body["meta"]["end_round"] == 302

    first_snapshots = body["timeline"][0]["snapshots"]
    assert first_snapshots, "Expected snapshots for first round"

    expected_miner_id = f"miner-{first_payload['agent_evaluation_runs'][0]['miner_uid']}"
    snapshot = first_snapshots[0]
    assert snapshot["miner_id"] == expected_miner_id
    expected_score = first_payload["evaluation_results"][0]["final_score"] * 100
    assert pytest.approx(snapshot["score"], rel=1e-3) == expected_score

    roster_ids = {entry["miner_id"] for entry in body["roster"]}
    assert expected_miner_id in roster_ids
