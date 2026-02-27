from __future__ import annotations

import asyncio
import os
import time

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text


async def main() -> None:
    os.environ.setdefault("AUTH_DISABLED", "true")
    os.environ.setdefault("TESTING", "true")

    from app.db.session import AsyncSessionLocal, init_db
    from app.main import app
    from app.services.round_calc import compute_season_number

    await init_db()

    now = time.time()
    start_block = 7_620_000
    season_number = int(compute_season_number(start_block))
    round_number = 1
    validator_round_id = f"validator_round_smoke_{season_number}_{round_number}_{int(now)}"
    validator_uid = 21
    validator_hotkey = "5DANs86MZknobepodgBt91DBp3gPiSxJjpopJw8BKDkpX3gZ"
    miner_uid = 120
    miner_hotkey = "5FnQoU9uixjeYriNs4g8Lk9kDwJBD9o5Cepi9aCUrELJmN9E"
    agent_run_id = f"agent_run_{miner_uid}_{int(now)}"
    task_id = f"task_{int(now)}"
    task_solution_id = f"solution_{int(now)}"
    evaluation_id = f"evaluation_{int(now)}"

    await app.router.startup()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            start_resp = await client.post(
                "/api/v1/validator-rounds/start?force=true",
                json={
                    "validator_identity": {
                        "uid": validator_uid,
                        "hotkey": validator_hotkey,
                        "coldkey": None,
                    },
                    "validator_round": {
                        "validator_round_id": validator_round_id,
                        "season_number": season_number,
                        "round_number_in_season": round_number,
                        "validator_uid": validator_uid,
                        "validator_hotkey": validator_hotkey,
                        "validator_coldkey": None,
                        "start_block": start_block,
                        "end_block": start_block + 100,
                        "start_epoch": 1,
                        "end_epoch": 2,
                        "started_at": now,
                        "ended_at": None,
                        "n_tasks": 1,
                        "status": "active",
                        "metadata": {},
                    },
                    "validator_snapshot": {
                        "validator_round_id": validator_round_id,
                        "validator_uid": validator_uid,
                        "validator_hotkey": validator_hotkey,
                        "validator_coldkey": None,
                        "name": "Autoppia Validator 1",
                        "stake": 1234.5,
                        "vtrust": 0.8,
                        "image_url": "/validators/Other.png",
                        "version": "1.0.0",
                        "config": {"round": {"timeout": 180}},
                        "validator_config": None,
                    },
                },
            )
            assert start_resp.status_code == 200, start_resp.text

            tasks_resp = await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/tasks?force=true",
                json={
                    "tasks": [
                        {
                            "task_id": task_id,
                            "validator_round_id": validator_round_id,
                            "is_web_real": False,
                            "web_project_id": "autobooks",
                            "web_version": "v1",
                            "url": "https://example.org",
                            "prompt": "Open homepage",
                            "specifications": {},
                            "tests": [],
                            "use_case": {"id": "OPEN_HOME"},
                        }
                    ]
                },
            )
            assert tasks_resp.status_code == 200, tasks_resp.text

            agent_resp = await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/start?force=true",
                json={
                    "agent_run": {
                        "agent_run_id": agent_run_id,
                        "validator_round_id": validator_round_id,
                        "miner_uid": miner_uid,
                        "miner_hotkey": miner_hotkey,
                        "started_at": now + 1,
                        "ended_at": None,
                        "elapsed_sec": None,
                        "average_score": None,
                        "average_execution_time": None,
                        "average_reward": None,
                        "total_tasks": 0,
                        "completed_tasks": 0,
                        "failed_tasks": 0,
                        "metadata": {},
                        "is_reused": False,
                        "reused_from_agent_run_id": None,
                        "zero_reason": None,
                    },
                    "miner_identity": {
                        "uid": miner_uid,
                        "hotkey": miner_hotkey,
                        "coldkey": None,
                    },
                    "miner_snapshot": {
                        "validator_round_id": validator_round_id,
                        "miner_uid": miner_uid,
                        "miner_hotkey": miner_hotkey,
                        "miner_coldkey": None,
                        "agent_name": "autoppia miner 1",
                        "image_url": "/miners/20.svg",
                        "github_url": "https://github.com/autoppia/autoppia_operator/commit/f6ce88878b3b1251eee24fc28067bee4a6e2bb31",
                        "is_sota": False,
                        "version": "1.0.0",
                    },
                },
            )
            assert agent_resp.status_code == 200, agent_resp.text

            eval_resp = await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/agent-runs/{agent_run_id}/evaluations?force=true",
                json={
                    "task": {
                        "task_id": task_id,
                        "validator_round_id": validator_round_id,
                        "is_web_real": False,
                        "web_project_id": "autobooks",
                        "web_version": "v1",
                        "url": "https://example.org",
                        "prompt": "Open homepage",
                        "specifications": {},
                        "tests": [],
                        "use_case": {"id": "OPEN_HOME"},
                    },
                    "task_solution": {
                        "solution_id": task_solution_id,
                        "task_id": task_id,
                        "agent_run_id": agent_run_id,
                        "validator_round_id": validator_round_id,
                        "validator_uid": validator_uid,
                        "validator_hotkey": validator_hotkey,
                        "miner_uid": miner_uid,
                        "miner_hotkey": miner_hotkey,
                        "actions": [],
                    },
                    "evaluation": {
                        "evaluation_id": evaluation_id,
                        "validator_round_id": validator_round_id,
                        "agent_run_id": agent_run_id,
                        "task_id": task_id,
                        "task_solution_id": task_solution_id,
                        "miner_uid": miner_uid,
                        "miner_hotkey": miner_hotkey,
                        "validator_uid": validator_uid,
                        "validator_hotkey": validator_hotkey,
                        "evaluation_score": 0.0,
                        "reward": 0.0,
                        "evaluation_time": 181.2,
                        "execution_history": [],
                        "gif_recording": None,
                        "metadata": {"timeout": True, "cost": 0.019},
                        "zero_reason": "task_timeout",
                        "llm_usage": [],
                    },
                },
            )
            assert eval_resp.status_code == 200, eval_resp.text

            finish_resp = await client.post(
                f"/api/v1/validator-rounds/{validator_round_id}/finish?force=true",
                json={
                    "status": "finished",
                    "ended_at": now + 200,
                    "agent_runs": [
                        {
                            "agent_run_id": agent_run_id,
                            "rank": 1,
                            "weight": 1.0,
                            "avg_reward": 0.0,
                            "avg_evaluation_time": 181.2,
                            "tasks_attempted": 1,
                            "tasks_completed": 0,
                            "tasks_failed": 1,
                            "zero_reason": "task_timeout",
                            "is_reused": False,
                            "reused_from_agent_run_id": None,
                        }
                    ],
                    "round": {
                        "round_number": round_number,
                        "started_at": now,
                        "ended_at": now + 200,
                        "start_block": start_block,
                        "end_block": start_block + 100,
                        "start_epoch": 1,
                        "end_epoch": 2,
                        "tasks_total": 1,
                        "tasks_completed": 0,
                        "miners_responded_handshake": 1,
                        "miners_evaluated": 1,
                    },
                    "local_evaluation": {
                        "summary": {
                            "miners": [
                                {
                                    "miner_uid": miner_uid,
                                    "miner_hotkey": miner_hotkey,
                                    "rank": 1,
                                    "avg_reward": 0.0,
                                    "avg_eval_score": 0.0,
                                    "avg_evaluation_time": 181.2,
                                    "tasks_attempted": 1,
                                    "tasks_completed": 0,
                                }
                            ]
                        }
                    },
                    "post_consensus_evaluation": {
                        "summary": {
                            "miners": [
                                {
                                    "miner_uid": miner_uid,
                                    "miner_hotkey": miner_hotkey,
                                    "rank": 1,
                                    "consensus_reward": 0.0,
                                    "avg_eval_score": 0.0,
                                    "avg_eval_time": 181.2,
                                    "tasks_sent": 1,
                                    "tasks_success": 0,
                                    "weight": 1.0,
                                }
                            ],
                            "round_summary": {
                                "winner": {"miner_uid": miner_uid, "score": 0.0},
                                "decision": {
                                    "reigning_uid_before_round": miner_uid,
                                    "reigning_score_before_round": 0.0,
                                    "top_candidate_uid": miner_uid,
                                    "top_candidate_score": 0.0,
                                    "required_improvement_pct": 0.05,
                                    "dethroned": False,
                                },
                            },
                            "season_summary": {
                                "current_winner_uid": miner_uid,
                                "current_winner_score": 0.0,
                                "required_improvement_pct": 0.05,
                                "dethroned": False,
                            },
                        }
                    },
                    "ipfs_uploaded": {"cid": "bafy-smoke", "payload": {"ok": True}},
                    "ipfs_downloaded": {"count": 0, "payloads": []},
                    "s3_logs": {"round": "s3://bucket/round.log"},
                },
            )
            assert finish_resp.status_code == 200, finish_resp.text

            agents_resp = await client.get(f"/api/v1/agents/{miner_uid}?season={season_number}&round={round_number}&agent={miner_uid}")
            assert agents_resp.status_code == 200, agents_resp.text

    finally:
        await app.router.shutdown()

    async with AsyncSessionLocal() as session:
        rv = await session.execute(
            text(
                """
                SELECT rv.validator_round_id, rv.validator_uid, rv.round_id, ro.winner_miner_uid
                FROM round_validators rv
                LEFT JOIN round_outcomes ro ON ro.source_round_validator_id = rv.round_validator_id
                WHERE rv.validator_round_id = :vrid
                """
            ),
            {"vrid": validator_round_id},
        )
        row = rv.mappings().first()
        assert row is not None
        assert int(row["validator_uid"]) == validator_uid
        assert int(row["winner_miner_uid"]) == miner_uid

    print("IWAP endpoint smoke flow OK")


if __name__ == "__main__":
    asyncio.run(main())
