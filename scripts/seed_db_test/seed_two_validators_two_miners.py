#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def _exec(session, sql: str, params: dict | None = None):
    return await session.execute(text(sql), params or {})


async def seed_new_schema() -> tuple[int, int, list[int]]:
    from app.db.session import AsyncSessionLocal

    miners = [
        (120, "5FnQoU9uixjeYriNs4g8Lk9kDwJBD9o5Cepi9aCUrELJmN9E", "autoppia miner 1", "https://github.com/autoppia/miner1/commit/mock001"),
        (121, "5G2mockHotkeyMiner121XXXXXXXXXXXXXXXXXXXXXXXX", "autoppia miner 2", "https://github.com/autoppia/miner2/commit/mock002"),
    ]
    validators = [
        (21, "5DANs86MZknobepodgBt91DBp3gPiSxJjpopJw8BKDkpX3gZ", "Autoppia Validator 1", True),
        (22, "5DmockValidator22XXXXXXXXXXXXXXXXXXXXXXXXXXXXX", "Autoppia Validator 2", False),
    ]
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # cleanup
        for table in [
            "evaluation_llm_usage",
            "evaluations_execution_history",
            "evaluations",
            "task_solutions",
            "task_execution_logs",
            "tasks",
            "miner_evaluation_runs",
            "round_validator_miners",
            "round_outcomes",
            "round_validators",
            "rounds",
            "seasons",
        ]:
            await _exec(session, f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")

        # season + round
        season_id = (
            await _exec(
                session,
                """
                INSERT INTO seasons (season_number, start_block, end_block, start_at, end_at, status, required_improvement_pct, leader_miner_uid, leader_reward, leader_github_url)
                VALUES (1, 7607373, 7609000, :now, :now, 'active', 0.05, 120, 0.81, :leader_github)
                RETURNING season_id
                """,
                {"now": now, "leader_github": miners[0][3]},
            )
        ).scalar_one()

        round_id = (
            await _exec(
                session,
                """
                INSERT INTO rounds (season_id, round_number_in_season, start_block, end_block, start_epoch, end_epoch, started_at, ended_at, status, consensus_status)
                VALUES (:season_id, 1, 7607373, 7607423, 21131, 21132, :now, :now, 'finished', 'finalized')
                RETURNING round_id
                """,
                {"season_id": season_id, "now": now},
            )
        ).scalar_one()

        round_validator_ids: list[int] = []
        for uid, hotkey, name, is_main in validators:
            rv_id = (
                await _exec(
                    session,
                    """
                    INSERT INTO round_validators (
                        round_id, validator_uid, validator_hotkey, validator_coldkey, name, image_url, version, stake, vtrust,
                        started_at, finished_at, config, local_summary_json, post_consensus_summary, ipfs_uploaded, ipfs_downloaded, is_main_validator
                    )
                    VALUES (
                        :round_id, :uid, :hotkey, :coldkey, :name, '/validators/Other.png', '1.0.0', 1000, 0.95,
                        :now, :now, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, :is_main
                    )
                    RETURNING round_validator_id
                    """,
                    {
                        "round_id": round_id,
                        "uid": uid,
                        "hotkey": hotkey,
                        "coldkey": f"coldkey_{uid}",
                        "name": name,
                        "now": now,
                        "is_main": is_main,
                    },
                )
            ).scalar_one()
            round_validator_ids.append(rv_id)

            # tasks per validator
            for i, website in enumerate(["autobooks", "autocinema", "autobooks"], start=1):
                await _exec(
                    session,
                    """
                    INSERT INTO tasks (task_id, round_validator_id, is_web_real, web_project_id, web_version, url, prompt, specifications, tests, use_case, created_at, updated_at, validator_round_id)
                    VALUES (:task_id, :rvid, true, :website, '0.1.0+seed', :url, :prompt, '{}'::jsonb, '[]'::jsonb, CAST(:use_case AS jsonb), :now, :now, :legacy_round_id)
                    """,
                    {
                        "task_id": f"task_r{round_id}_v{uid}_{i}",
                        "rvid": rv_id,
                        "website": website,
                        "url": f"http://localhost:80{i}/?seed={uid}{i}",
                        "prompt": f"Seed task {i} for validator {uid}",
                        "use_case": f'{{"name":"use_case_{i}","slug":"use-case-{i}"}}',
                        "now": now,
                        "legacy_round_id": f"validator_round_{round_id}_{uid}",
                    },
                )

            for m_uid, m_hotkey, m_name, m_github in miners:
                is_reused = uid == 22 and m_uid == 120
                await _exec(
                    session,
                    """
                    INSERT INTO round_validator_miners (
                        round_validator_id, round_id, miner_uid, miner_hotkey, miner_coldkey, name, image_url, github_url, is_sota, version,
                        is_reused, reused_from_agent_run_id, reused_from_round_id,
                        local_rank, local_avg_reward, local_avg_eval_score, local_avg_eval_time, local_tasks_received, local_tasks_success,
                        post_consensus_rank, post_consensus_avg_reward, post_consensus_avg_eval_score, post_consensus_avg_eval_time, post_consensus_tasks_received, post_consensus_tasks_success,
                        weight, subnet_price, effective_rank, effective_reward, effective_eval_score, effective_eval_time, local_avg_eval_cost, post_consensus_avg_eval_cost, effective_eval_cost
                    )
                    VALUES (
                        :rvid, :round_id, :m_uid, :m_hotkey, :m_coldkey, :m_name, :img, :github, false, '1.0.0',
                        :is_reused, :reused_run, :reused_round,
                        :local_rank, :local_reward, :local_score, :local_time, 3, :local_success,
                        :post_rank, :post_reward, :post_score, :post_time, 3, :post_success,
                        :weight, 0.00416, :eff_rank, :eff_reward, :eff_score, :eff_time, 0.0012, 0.0012, 0.0012
                    )
                    """,
                    {
                        "rvid": rv_id,
                        "round_id": round_id,
                        "m_uid": m_uid,
                        "m_hotkey": m_hotkey,
                        "m_coldkey": f"coldkey_miner_{m_uid}",
                        "m_name": m_name,
                        "img": f"/miners/{m_uid % 100}.svg",
                        "github": m_github,
                        "is_reused": is_reused,
                        "reused_run": f"agent_run_{m_uid}_{round_id}_{round_validator_ids[0]}" if is_reused else None,
                        "reused_round": round_id if is_reused else None,
                        "local_rank": 1 if m_uid == 120 else 2,
                        "local_reward": 0.81 if m_uid == 120 else 0.62,
                        "local_score": 0.8 if m_uid == 120 else 0.6,
                        "local_time": 180.0 if m_uid == 120 else 210.0,
                        "local_success": 2 if m_uid == 120 else 1,
                        "post_rank": 1 if m_uid == 120 else 2,
                        "post_reward": 0.81 if m_uid == 120 else 0.62,
                        "post_score": 0.8 if m_uid == 120 else 0.6,
                        "post_time": 185.0 if m_uid == 120 else 215.0,
                        "post_success": 2 if m_uid == 120 else 1,
                        "weight": 1.0 if m_uid == 120 else 0.0,
                        "eff_rank": 1 if m_uid == 120 else 2,
                        "eff_reward": 0.81 if m_uid == 120 else 0.62,
                        "eff_score": 0.8 if m_uid == 120 else 0.6,
                        "eff_time": 185.0 if m_uid == 120 else 215.0,
                    },
                )

                run_id = f"agent_run_{m_uid}_{round_id}_{rv_id}"
                await _exec(
                    session,
                    """
                    INSERT INTO miner_evaluation_runs (
                        agent_run_id, round_validator_id, miner_uid, miner_hotkey, started_at, ended_at, elapsed_sec,
                        local_average_score, local_average_execution_time, local_average_reward, local_average_cost,
                        total_tasks, success_tasks, failed_tasks, is_reused, reused_from_agent_run_id, zero_reason, extra_info, validator_round_id,
                        average_score, average_execution_time, average_reward, meta, created_at, updated_at
                    )
                    VALUES (
                        :run_id, :rvid, :m_uid, :m_hotkey, :started_at, :ended_at, :elapsed,
                        :score, :time, :reward, 0.0012, 3, :success, :failed, :is_reused, :reused_from, :zero_reason, '{}'::jsonb, :legacy_round_id,
                        :score, :time, :reward, '{}'::jsonb, :now, :now
                    )
                    """,
                    {
                        "run_id": run_id,
                        "rvid": rv_id,
                        "m_uid": m_uid,
                        "m_hotkey": m_hotkey,
                        "started_at": now.timestamp() - 300,
                        "ended_at": now.timestamp() - 10,
                        "elapsed": 290.0,
                        "score": 0.8 if m_uid == 120 else 0.6,
                        "time": 185.0 if m_uid == 120 else 215.0,
                        "reward": 0.81 if m_uid == 120 else 0.62,
                        "success": 2 if m_uid == 120 else 1,
                        "failed": 1 if m_uid == 120 else 2,
                        "is_reused": is_reused,
                        "reused_from": f"agent_run_{m_uid}_{round_id}_{round_validator_ids[0]}" if is_reused else None,
                        "zero_reason": None if m_uid == 120 else "task_timeout",
                        "legacy_round_id": f"validator_round_{round_id}_{uid}",
                        "now": now,
                    },
                )

                task_rows = (await _exec(session, "SELECT task_id FROM tasks WHERE round_validator_id=:rvid ORDER BY id ASC", {"rvid": rv_id})).scalars().all()
                for idx, task_id in enumerate(task_rows, start=1):
                    solution_id = f"solution_{run_id}_{idx}"
                    score = 1.0 if idx < 3 else 0.0
                    await _exec(
                        session,
                        """
                        INSERT INTO task_solutions (
                            solution_id, task_id, agent_run_id, actions, created_at, updated_at,
                            validator_round_id, validator_uid, validator_hotkey, miner_uid, miner_hotkey
                        )
                        VALUES (:sid, :task_id, :run_id, '[{\"type\":\"CLICK\",\"attributes\":{\"selector\":\"#submit\"}}]'::jsonb, :now, :now, :legacy_round_id, :v_uid, :v_hotkey, :m_uid, :m_hotkey)
                        """,
                        {
                            "sid": solution_id,
                            "task_id": task_id,
                            "run_id": run_id,
                            "now": now,
                            "legacy_round_id": f"validator_round_{round_id}_{uid}",
                            "v_uid": uid,
                            "v_hotkey": hotkey,
                            "m_uid": m_uid,
                            "m_hotkey": m_hotkey,
                        },
                    )
                    await _exec(
                        session,
                        """
                        INSERT INTO evaluations (
                            evaluation_id, task_id, task_solution_id, agent_run_id, miner_uid, miner_hotkey, validator_uid, validator_hotkey,
                            evaluation_score, reward, evaluation_time, gif_recording, extra_info, zero_reason, created_at, updated_at, validator_round_id
                        )
                        VALUES (:eid, :task_id, :sid, :run_id, :m_uid, :m_hotkey, :v_uid, :v_hotkey, :score, :reward, :etime, NULL, '{}'::jsonb, :zero_reason, :now, :now, :legacy_round_id)
                        """,
                        {
                            "eid": f"evaluation_{run_id}_{idx}",
                            "task_id": task_id,
                            "sid": solution_id,
                            "run_id": run_id,
                            "m_uid": m_uid,
                            "m_hotkey": m_hotkey,
                            "v_uid": uid,
                            "v_hotkey": hotkey,
                            "score": score,
                            "reward": score * (0.81 if m_uid == 120 else 0.62),
                            "etime": 160.0 + idx,
                            "zero_reason": None if score > 0 else "task_timeout",
                            "now": now,
                            "legacy_round_id": f"validator_round_{round_id}_{uid}",
                        },
                    )

        # round consensus outcome
        await _exec(
            session,
            """
            INSERT INTO round_outcomes (
                round_id, winner_miner_uid, winner_score, reigning_miner_uid_before_round, reigning_score_before_round,
                top_candidate_miner_uid, top_candidate_score, required_improvement_pct, dethroned,
                validators_count, miners_evaluated, tasks_evaluated, tasks_success, avg_reward, avg_eval_score, avg_eval_time,
                computed_at, summary_json, source_round_validator_id, created_at, updated_at
            )
            VALUES (:round_id, 120, 0.81, 120, 0.79, 121, 0.62, 0.05, false, 2, 2, 6, 4, 0.715, 0.7, 200.0, :now, '{}'::jsonb, :source_rv_id, :now, :now)
            """,
            {"round_id": round_id, "source_rv_id": round_validator_ids[0], "now": now},
        )

        await session.commit()
        return season_id, round_id, round_validator_ids


async def smoke_endpoints() -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://seed-server") as client:
        urls = [
            "/api/v1/overview/metrics",
            "/api/v1/agents/latest-round-top-miner",
            "/api/v1/agents/120/historical?season=1",
            "/api/v1/rounds/1/1",
            "/api/v1/agents/120?season=1&round=1",
        ]
        for url in urls:
            r = await client.get(url)
            print(f"GET {url} -> {r.status_code}")
            if r.status_code >= 400:
                raise RuntimeError(f"{url} failed: {r.status_code} {r.text}")


async def main() -> None:
    season_id, round_id, rv_ids = await seed_new_schema()
    print(f"Seeded season_id={season_id}, round_id={round_id}, round_validator_ids={rv_ids}")
    await smoke_endpoints()
    print("Seed + endpoint smoke OK")


if __name__ == "__main__":
    asyncio.run(main())
