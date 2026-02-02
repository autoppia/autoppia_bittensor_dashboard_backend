"""
Test Season and Round endpoints with new season_number and round_number_in_season structure.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import (
    ValidatorRoundORM,
    TaskORM,
    ValidatorRoundValidatorORM,
    ValidatorRoundMinerORM,
)
from app.services.round_calc import compute_season_number


@pytest.mark.asyncio
async def test_start_round_season_1_round_1_with_tasks(client, monkeypatch, db_session):
    """
    Test creating Season 1, Round 1 with 10 tasks via endpoints.
    This simulates what the validator would do:
    1. Start round with season_number and round_number_in_season
    2. Send tasks (only for round 1 of season)
    """
    from app.config import settings
    from app.main import app
    from app.services.validator.validator_auth import require_validator_auth
    
    # Mock validator auth to bypass authentication
    async def mock_require_validator_auth():
        return None
    
    app.dependency_overrides[require_validator_auth] = mock_require_validator_auth
    
    try:
        # Mock current block to be at start of Season 1, Round 1
        start_block = int(settings.DZ_STARTING_BLOCK)  # 4493500
        monkeypatch.setattr(
            "app.api.validator.validator_round.get_current_block",
            lambda: start_block,
        )
        monkeypatch.setattr(
            "app.services.chain_state.get_current_block",
            lambda: start_block,
        )
        
        # Verify season calculation
        expected_season = compute_season_number(start_block)
        assert expected_season == 1, f"Expected season 1, got {expected_season}"
        
        validator_round_id = "validator_round_1_1_abc123def456"
        validator_uid = 0
        validator_hotkey = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
        
        # 1. Start Round - Season 1, Round 1
        start_round_payload = {
        "validator_identity": {
            "uid": validator_uid,
            "hotkey": validator_hotkey,
            "coldkey": "5DTestValidator1234567890123456789012345678901234",
        },
        "validator_round": {
            "validator_round_id": validator_round_id,
            "season_number": 1,
            "round_number_in_season": 1,
            "validator_uid": validator_uid,
            "validator_hotkey": validator_hotkey,
            "validator_coldkey": "5DTestValidator1234567890123456789012345678901234",
            "start_block": start_block,
            "end_block": start_block + 720,  # 2 epochs
            "start_epoch": 12481,
            "end_epoch": 12483,
            "started_at": 1700000000.0,
            "n_tasks": 10,
            "n_miners": 3,
            "n_winners": 1,
            "status": "active",
            "metadata": {},
        },
        "validator_snapshot": {
            "validator_round_id": validator_round_id,
            "validator_uid": validator_uid,
            "validator_hotkey": validator_hotkey,
            "validator_coldkey": "5DTestValidator1234567890123456789012345678901234",
            "name": "Test Validator",
            "stake": 1000.0,
            "vtrust": 0.95,
            "image_url": "https://example.com/validator.png",
            "version": "1.0.0",
            "config": {"test": True},
        },
        }
        
        # Add auth header
        headers = {
        "x-validator-hotkey": validator_hotkey,
        "x-validator-signature": "test_signature_for_testing_only",
        }
        
        start_response = await client.post(
        "/api/v1/validator-rounds/start",
        json=start_round_payload,
        headers=headers,
        params={"force": True},  # Skip validation for testing
    )
    
        assert start_response.status_code == 200, f"Start round failed: {start_response.text}"
        start_body = start_response.json()
        assert "validator_round_id" in start_body
        
        # Verify round was created in DB
        stmt = select(ValidatorRoundORM).where(
        ValidatorRoundORM.validator_round_id == validator_round_id
    )
        round_row = await db_session.scalar(stmt)
        assert round_row is not None
        assert round_row.season_number == 1
        assert round_row.round_number_in_season == 1
        assert round_row.start_block == start_block
        
        # 2. Send 10 tasks (only for round 1 of season)
        tasks = []
        for i in range(1, 11):
            task_id = f"task_{i}_s1_abc123"
            tasks.append({
            "task_id": task_id,
            "validator_round_id": validator_round_id,
            "is_web_real": False,
            "web_project_id": f"project_{i % 5}",
            "web_version": "v1",
            "url": f"https://demo.autoppia.ai/project_{i % 5}",
            "prompt": f"Task {i} prompt: Execute action {i}",
            "specifications": {"action": f"action_{i}"},
            "tests": [],
            "relevant_data": {},
                "use_case": {"name": f"use_case_{i}"},
            })
        
        tasks_response = await client.post(
        f"/api/v1/validator-rounds/{validator_round_id}/tasks",
        json={"tasks": tasks},
        headers=headers,
        params={"force": True},
    )
    
        assert tasks_response.status_code == 200, f"Set tasks failed: {tasks_response.text}"
        
        # Verify tasks were created in DB
        stmt = select(TaskORM).where(
        TaskORM.validator_round_id == validator_round_id
    )
        task_rows = (await db_session.scalars(stmt)).all()
        assert len(task_rows) == 10, f"Expected 10 tasks, got {len(task_rows)}"
        
        # Verify task details
        task_ids = {task.task_id for task in task_rows}
        expected_task_ids = {f"task_{i}_s1_abc123" for i in range(1, 11)}
        assert task_ids == expected_task_ids
        
        # 3. Add miners via start_agent_run (simulating handshake)
        miners = [
        {
            "uid": 10,
            "hotkey": "5FMiner1xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
            "coldkey": "5DMiner1Cold1234567890123456789012345678901234",
            "agent_name": "AgentAlpha",
            "github_url": "https://github.com/miner/alpha/commit/abc123",
        },
        {
            "uid": 15,
            "hotkey": "5FMiner2xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
            "coldkey": "5DMiner2Cold1234567890123456789012345678901234",
            "agent_name": "AgentBeta",
            "github_url": "https://github.com/miner/beta/commit/def456",
        },
        {
            "uid": 20,
            "hotkey": "5FMiner3xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
            "coldkey": "5DMiner3Cold1234567890123456789012345678901234",
            "agent_name": "AgentGamma",
                "github_url": "https://github.com/miner/gamma/commit/ghi789",
            },
        ]
        
        for miner in miners:
            agent_run_id = f"agent_run_{miner['uid']}_{validator_round_id}"
            start_agent_run_payload = {
                "agent_run": {
                    "agent_run_id": agent_run_id,
                    "validator_round_id": validator_round_id,
                    "miner_uid": miner["uid"],
                    "github_url": miner["github_url"],
                    "started_at": 1700000100.0,
                    "status": "active",
                },
                "miner_identity": {
                    "uid": miner["uid"],
                    "hotkey": miner["hotkey"],
                    "coldkey": miner["coldkey"],
                    "agent_name": miner["agent_name"],
                    "agent_image": "",
                    "github": miner["github_url"],
                    "is_sota": False,
                },
                "miner_snapshot": {
                    "validator_round_id": validator_round_id,
                    "miner_uid": miner["uid"],
                    "miner_hotkey": miner["hotkey"],
                    "miner_coldkey": miner["coldkey"],
                    "agent_name": miner["agent_name"],
                    "image_url": "",
                    "github_url": miner["github_url"],
                    "is_sota": False,
                },
            }
            
            agent_run_response = await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start",
                json=start_agent_run_payload,
                headers=headers,
                params={"force": True},
            )
            
            assert agent_run_response.status_code == 200, f"Start agent run failed: {agent_run_response.text}"
        
        # Verify miners were created
        stmt = select(ValidatorRoundMinerORM).where(
            ValidatorRoundMinerORM.validator_round_id == validator_round_id
        )
        miner_rows = (await db_session.scalars(stmt)).all()
        assert len(miner_rows) == 3, f"Expected 3 miners, got {len(miner_rows)}"
        
        miner_uids = {miner.miner_uid for miner in miner_rows}
        expected_uids = {10, 15, 20}
        assert miner_uids == expected_uids
        
        # Final verification: Check complete round structure
        stmt = select(ValidatorRoundORM).where(
        ValidatorRoundORM.validator_round_id == validator_round_id
    )
        final_round = await db_session.scalar(stmt)
        assert final_round is not None
        assert final_round.season_number == 1
        assert final_round.round_number_in_season == 1
        assert final_round.n_tasks == 10
        assert final_round.n_miners == 3
        assert final_round.status == "active"
        
        print(f"✅ Test passed! Created Season {final_round.season_number}, Round {final_round.round_number_in_season}")
        print(f"   - {len(task_rows)} tasks")
        print(f"   - {len(miner_rows)} miners")
        print(f"   - Round ID: {validator_round_id}")
    
    finally:
        # Clean up dependency override
        app.dependency_overrides.pop(require_validator_auth, None)
