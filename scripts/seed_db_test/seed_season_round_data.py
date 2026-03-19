"""
Script para insertar datos de prueba de Season 1, Round 1 usando los endpoints reales.
Este script NO limpia la base de datos, solo añade los datos.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.round_calc import compute_season_number  # noqa: E402


async def seed_data():
    """Seed Season 1, Round 1 data via endpoints."""

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set before running this script")

    # Use the configured database URL instead of embedding credentials in the script
    os.environ["DATABASE_URL"] = database_url

    # Mock current block to be at start of Season 1, Round 1
    start_block = int(settings.DZ_STARTING_BLOCK)  # 4493500

    # Verify season calculation
    expected_season = compute_season_number(start_block)
    print(f"📊 Calculated season from start_block {start_block}: {expected_season}")

    validator_round_id = "validator_round_1_1_abc123def456"
    validator_uid = 0
    validator_hotkey = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"

    # Start app
    await app.router.startup()

    try:
        # Mock get_current_block to return start_block
        import app.api.validator.validator_round as vmod
        import app.services.chain_state as chain_state_mod

        original_get_block = getattr(vmod, "get_current_block", None)
        original_chain_get_block = getattr(chain_state_mod, "get_current_block", None)

        def mock_get_current_block():
            return start_block

        vmod.get_current_block = mock_get_current_block
        chain_state_mod.get_current_block = mock_get_current_block

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Mock validator auth - bypass authentication
            from app.main import app as app_instance
            from app.services.validator.validator_auth import require_validator_auth

            async def mock_require_validator_auth():
                return None

            app_instance.dependency_overrides[require_validator_auth] = mock_require_validator_auth

            try:
                # 1. Start Round - Season 1, Round 1
                print(f"\n🚀 Starting Round: Season {expected_season}, Round 1")
                start_round_payload = {
                    "validator_identity": {
                        "uid": validator_uid,
                        "hotkey": validator_hotkey,
                        "coldkey": "5DTestValidator1234567890123456789012345678901234",
                    },
                    "validator_round": {
                        "validator_round_id": validator_round_id,
                        "season_number": expected_season,
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

                headers = {
                    "x-validator-hotkey": validator_hotkey,
                    "x-validator-signature": "test_signature_for_testing_only",
                }

                start_response = await client.post(
                    "/api/v1/validator-rounds/start",
                    json=start_round_payload,
                    headers=headers,
                    params={"force": True},
                )

                if start_response.status_code != 200:
                    print(f"❌ Error starting round: {start_response.status_code}")
                    print(start_response.text)
                    return

                print(f"✅ Round started: {start_response.json()}")

                # 2. Send 10 tasks (only for round 1 of season)
                print("\n📋 Adding 10 tasks...")
                tasks = []
                for i in range(1, 11):
                    task_id = f"task_{i}_s1_abc123"
                    tasks.append(
                        {
                            "task_id": task_id,
                            "validator_round_id": validator_round_id,
                            "is_web_real": False,
                            "web_project_id": f"project_{i % 5}",
                            "web_version": "v1",
                            "url": f"https://demo.autoppia.ai/project_{i % 5}",
                            "prompt": f"Task {i} prompt: Execute action {i}",
                            "specifications": {"action": f"action_{i}"},
                            "tests": [],
                            "use_case": {"name": f"use_case_{i}"},
                        }
                    )

                tasks_response = await client.post(
                    f"/api/v1/validator-rounds/{validator_round_id}/tasks",
                    json={"tasks": tasks},
                    headers=headers,
                    params={"force": True},
                )

                if tasks_response.status_code != 200:
                    print(f"❌ Error adding tasks: {tasks_response.status_code}")
                    print(tasks_response.text)
                    return

                print(f"✅ Tasks added: {tasks_response.json()}")

                # 3. Add miners via start_agent_run (simulating handshake)
                print("\n👥 Adding 3 miners...")
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
                            "miner_hotkey": miner["hotkey"],
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

                    if agent_run_response.status_code != 200:
                        print(f"❌ Error adding miner {miner['agent_name']}: {agent_run_response.status_code}")
                        print(agent_run_response.text)
                        return
                    else:
                        print(f"✅ Miner {miner['agent_name']} added")

                # Add task solutions for each miner
                print("\n📝 Adding task solutions...")
                task_ids = [f"task_{i}_s1_abc123" for i in range(1, 11)]

                for miner in miners:
                    agent_run_id = f"agent_run_{miner['uid']}_{validator_round_id}"
                    # Each miner solves 5 tasks (tasks 1-5)
                    for task_idx in range(5):
                        task_id = task_ids[task_idx]
                        solution_id = f"solution_{miner['uid']}_{task_id}"

                        add_evaluation_payload = {
                            "task": {
                                "task_id": task_id,
                                "validator_round_id": validator_round_id,
                                "is_web_real": False,
                                "web_project_id": f"project_{task_idx % 5}",
                                "web_version": "v1",
                                "url": f"https://demo.autoppia.ai/project_{task_idx % 5}",
                                "prompt": f"Task {task_idx + 1} prompt: Execute action {task_idx + 1}",
                                "specifications": {"action": f"action_{task_idx + 1}"},
                                "tests": [],
                                "use_case": {"name": f"use_case_{task_idx + 1}"},
                            },
                            "task_solution": {
                                "solution_id": solution_id,
                                "task_id": task_id,
                                "agent_run_id": agent_run_id,
                                "validator_round_id": validator_round_id,
                                "validator_uid": validator_uid,
                                "validator_hotkey": validator_hotkey,
                                "miner_uid": miner["uid"],
                                "miner_hotkey": miner["hotkey"],
                                "actions": [
                                    {
                                        "type": "click",
                                        "selector": f"button_{task_idx + 1}",
                                        "timestamp": 1700000200.0 + task_idx,
                                    },
                                    {
                                        "type": "input",
                                        "selector": f"input_{task_idx + 1}",
                                        "value": f"test_value_{task_idx + 1}",
                                        "timestamp": 1700000201.0 + task_idx,
                                    },
                                ],
                            },
                            "evaluation": {
                                "evaluation_id": f"eval_{miner['uid']}_{task_id}",
                                "task_id": task_id,
                                "task_solution_id": solution_id,
                                "agent_run_id": agent_run_id,
                                "validator_round_id": validator_round_id,
                                "validator_uid": validator_uid,
                                "validator_hotkey": validator_hotkey,
                                "miner_uid": miner["uid"],
                                "miner_hotkey": miner["hotkey"],
                                "evaluation_score": 0.8 + (task_idx * 0.02),  # Scores from 0.8 to 0.88
                                "reward": 0.8 + (task_idx * 0.02),
                                "evaluation_time": 2.5 + task_idx * 0.1,
                                "status": "completed",
                                "meta": {},
                            },
                        }

                        eval_response = await client.post(
                            f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations",
                            json=add_evaluation_payload,
                            headers=headers,
                            params={"force": True},
                        )

                        if eval_response.status_code == 200:
                            print(f"  ✅ Solution for {miner['agent_name']} - Task {task_idx + 1}")
                        else:
                            print(f"  ❌ Error adding solution for {miner['agent_name']} - Task {task_idx + 1}: {eval_response.status_code}")
                            print(f"     {eval_response.text}")

                # Finish the round to create summary records with local and post-consensus evaluation
                print("\n🏁 Finishing round to create summary records...")

                # Create local_evaluation (from this validator's perspective)
                local_evaluation = {
                    "miners": [
                        {
                            "miner_uid": 10,
                            "miner_hotkey": "5FMiner1xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                            "rank": 1,
                            "avg_reward": 0.995,
                            "avg_eval_score": 0.84,
                            "avg_evaluation_time": 2.7,
                            "tasks_attempted": 5,
                            "tasks_completed": 5,
                        },
                        {
                            "miner_uid": 15,
                            "miner_hotkey": "5FMiner2xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                            "rank": 2,
                            "avg_reward": 0.995,
                            "avg_eval_score": 0.84,
                            "avg_evaluation_time": 2.7,
                            "tasks_attempted": 5,
                            "tasks_completed": 5,
                        },
                        {
                            "miner_uid": 20,
                            "miner_hotkey": "5FMiner3xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                            "rank": 3,
                            "avg_reward": 0.995,
                            "avg_eval_score": 0.84,
                            "avg_evaluation_time": 2.7,
                            "tasks_attempted": 5,
                            "tasks_completed": 5,
                        },
                    ]
                }

                # Create post_consensus_evaluation (aggregated from all validators)
                # Simula que hay 3 validators y el consensus da estos resultados
                post_consensus_evaluation = {
                    "miners": [
                        {
                            "miner_uid": 10,
                            "miner_hotkey": "5FMiner1xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                            "rank": 1,  # Mejor miner según consensus
                            "consensus_reward": 0.98,  # Promedio ponderado de todos los validators
                            "avg_eval_score": 0.85,
                            "avg_eval_time": 2.6,
                            "tasks_sent": 5,
                            "tasks_success": 5,
                            "weight": 0.40,  # 40% del peso total (ganador)
                        },
                        {
                            "miner_uid": 15,
                            "miner_hotkey": "5FMiner2xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                            "rank": 2,
                            "consensus_reward": 0.96,
                            "avg_eval_score": 0.83,
                            "avg_eval_time": 2.8,
                            "tasks_sent": 5,
                            "tasks_success": 5,
                            "weight": 0.35,  # 35% del peso
                        },
                        {
                            "miner_uid": 20,
                            "miner_hotkey": "5FMiner3xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
                            "rank": 3,
                            "consensus_reward": 0.94,
                            "avg_eval_score": 0.82,
                            "avg_eval_time": 2.9,
                            "tasks_sent": 5,
                            "tasks_success": 4,  # Este miner falló 1 task
                            "weight": 0.25,  # 25% del peso
                        },
                    ]
                }

                finish_payload = {
                    "status": "finished",
                    "ended_at": 1700000300.0,
                    "local_evaluation": local_evaluation,
                    "post_consensus_evaluation": post_consensus_evaluation,
                }

                finish_response = await client.post(
                    f"/api/v1/validator-rounds/{validator_round_id}/finish",
                    json=finish_payload,
                    headers=headers,
                    params={"force": True},
                )

                if finish_response.status_code == 200:
                    print(f"✅ Round finished: {finish_response.json()}")
                else:
                    print(f"❌ Error finishing round: {finish_response.status_code}")
                    print(f"   {finish_response.text}")

                print("\n✅ All data seeded successfully!")
                print(f"   - Round ID: {validator_round_id}")
                print(f"   - Season: {expected_season}, Round: 1")
                print("   - 10 tasks")
                print("   - 3 miners")
                print("   - 15 task solutions (5 per miner)")
                print("   - 15 evaluations")
                print("   - 3 summary records")

            finally:
                # Clean up dependency override
                app_instance.dependency_overrides.pop(require_validator_auth, None)
                # Restore original get_current_block
                if original_get_block:
                    vmod.get_current_block = original_get_block
                if original_chain_get_block:
                    chain_state_mod.get_current_block = original_chain_get_block

    finally:
        await app.router.shutdown()


if __name__ == "__main__":
    asyncio.run(seed_data())
