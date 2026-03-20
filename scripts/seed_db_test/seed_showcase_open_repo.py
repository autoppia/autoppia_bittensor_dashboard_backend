#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ValidatorSeed:
    uid: int
    hotkey: str
    coldkey: str
    name: str
    image_url: str
    stake: float
    vtrust: float
    is_main: bool


@dataclass(frozen=True)
class MinerSeed:
    uid: int
    hotkey: str
    coldkey: str
    name: str
    image_url: str
    github_url: str


VALIDATORS: tuple[ValidatorSeed, ...] = (
    ValidatorSeed(
        uid=21,
        hotkey="5DANs86MZknobepodgBt91DBp3gPiSxJjpopJw8BKDkpX3gZ",
        coldkey="5CMainValidatorColdkeyDemo111111111111111111111111",
        name="Autoppia Alpha",
        image_url="/validators/Other.png",
        stake=1250.0,
        vtrust=0.97,
        is_main=True,
    ),
    ValidatorSeed(
        uid=22,
        hotkey="5DmockValidator22XXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        coldkey="5CBackupValidatorColdkeyDemo22222222222222222222",
        name="Autoppia Beta",
        image_url="/validators/Other.png",
        stake=980.0,
        vtrust=0.93,
        is_main=False,
    ),
)

MINERS: tuple[MinerSeed, ...] = (
    MinerSeed(
        uid=120,
        hotkey="5FnQoU9uixjeYriNs4g8Lk9kDwJBD9o5Cepi9aCUrELJmN9E",
        coldkey="5CMinerAlphaColdkeyDemo120120120120120120120120",
        name="Miner Atlas",
        image_url="/miners/20.svg",
        github_url="https://github.com/autoppia/miner-atlas",
    ),
    MinerSeed(
        uid=121,
        hotkey="5G2mockHotkeyMiner121XXXXXXXXXXXXXXXXXXXXXXXX",
        coldkey="5CMinerBravoColdkeyDemo121121121121121121121121",
        name="Miner Bravo",
        image_url="/miners/21.svg",
        github_url="https://github.com/autoppia/miner-bravo",
    ),
    MinerSeed(
        uid=122,
        hotkey="5F8mockHotkeyMiner122XXXXXXXXXXXXXXXXXXXXXXXX",
        coldkey="5CMinerCometColdkeyDemo122122122122122122122122",
        name="Miner Comet",
        image_url="/miners/22.svg",
        github_url="https://github.com/autoppia/miner-comet",
    ),
)

WEBSITES = ("autobooks", "autocinema", "autotravel")


async def _exec(session, sql: str, params: dict[str, Any] | None = None):
    return await session.execute(text(sql), params or {})


def _run_shell_script(path: Path) -> None:
    result = subprocess.run(["bash", str(path)], cwd=ROOT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{path.name} failed with exit code {result.returncode}")


def _compute_round_metrics(
    season_number: int,
    round_number: int,
    validator_uid: int,
    miner: MinerSeed,
) -> dict[str, Any]:
    base_rewards = {
        120: [0.78, 0.81, 0.85, 0.84, 0.87, 0.90],
        121: [0.64, 0.68, 0.72, 0.74, 0.77, 0.79],
        122: [0.51, 0.57, 0.63, 0.66, 0.69, 0.73],
    }
    base_scores = {
        120: [0.77, 0.80, 0.83, 0.84, 0.86, 0.89],
        121: [0.62, 0.66, 0.70, 0.72, 0.75, 0.77],
        122: [0.49, 0.55, 0.60, 0.63, 0.67, 0.70],
    }
    base_times = {
        120: [195.0, 186.0, 178.0, 182.0, 176.0, 171.0],
        121: [214.0, 209.0, 205.0, 201.0, 197.0, 193.0],
        122: [231.0, 224.0, 218.0, 214.0, 208.0, 204.0],
    }

    slot = (season_number - 1) * 3 + (round_number - 1)
    validator_bias = 0.0 if validator_uid == 21 else -0.015

    reward = round(base_rewards[miner.uid][slot] + validator_bias, 4)
    score = round(base_scores[miner.uid][slot] + validator_bias, 4)
    elapsed = round(base_times[miner.uid][slot] + (6.0 if validator_uid == 22 else 0.0), 2)

    rank = {120: 1, 121: 2, 122: 3}[miner.uid]
    local_success = max(1, 4 - rank)
    post_success = max(1, 4 - rank)
    is_reused = validator_uid == 22 and miner.uid == 120 and round_number % 2 == 0

    return {
        "local_reward": reward,
        "local_score": score,
        "local_time": elapsed,
        "local_success": local_success,
        "post_rank": rank,
        "post_reward": reward,
        "post_score": score,
        "post_time": elapsed + 3.0,
        "post_success": post_success,
        "weight": 1.0 if rank == 1 else (0.45 if rank == 2 else 0.15),
        "is_reused": is_reused,
        "zero_reason": None if rank < 3 else "partial_failure",
    }


async def _reset_demo_data(session) -> None:
    for table in [
        "evaluation_llm_usage",
        "evaluations_execution_history",
        "evaluations",
        "task_solutions",
        "task_execution_logs",
        "tasks",
        "miner_evaluation_runs",
        "round_validator_miners",
        "round_summary",
        "round_validators",
        "rounds",
        "seasons",
        "config_season_round",
        "config_app_runtime",
    ]:
        await _exec(session, f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")


async def _seed_round(
    session,
    *,
    season_id: int,
    season_number: int,
    round_number: int,
    round_status: str,
    consensus_status: str,
    block_start: int,
    epoch_start: int,
    started_at: datetime,
    ended_at: datetime | None,
) -> tuple[int, int]:
    round_id = (
        await _exec(
            session,
            """
            INSERT INTO rounds (
                season_id,
                round_number_in_season,
                start_block,
                end_block,
                planned_start_block,
                planned_end_block,
                start_epoch,
                end_epoch,
                opened_by_validator_uid,
                authority_mode,
                started_at,
                ended_at,
                status,
                consensus_status
            )
            VALUES (
                :season_id,
                :round_number,
                :start_block,
                :end_block,
                :start_block,
                :end_block,
                :start_epoch,
                :end_epoch,
                :opened_by,
                'main',
                :started_at,
                :ended_at,
                :status,
                :consensus_status
            )
            RETURNING round_id
            """,
            {
                "season_id": season_id,
                "round_number": round_number,
                "start_block": block_start,
                "end_block": block_start + 99,
                "start_epoch": epoch_start,
                "end_epoch": epoch_start + 1,
                "opened_by": VALIDATORS[0].uid,
                "started_at": started_at,
                "ended_at": ended_at,
                "status": round_status,
                "consensus_status": consensus_status,
            },
        )
    ).scalar_one()

    validator_rows: list[int] = []
    for validator in VALIDATORS:
        validator_round_id = f"validator_round_s{season_number}_r{round_number}_v{validator.uid}"
        round_validator_id = (
            await _exec(
                session,
                """
                INSERT INTO round_validators (
                    round_id,
                    season_number,
                    round_number_in_season,
                    start_block,
                    end_block,
                    start_epoch,
                    end_epoch,
                    validator_uid,
                    validator_hotkey,
                    validator_coldkey,
                    validator_round_id,
                    name,
                    image_url,
                    version,
                    stake,
                    vtrust,
                    started_at,
                    finished_at,
                    config,
                    post_consensus_json,
                    ipfs_uploaded,
                    ipfs_downloaded,
                    s3_logs_url,
                    is_main_validator
                )
                VALUES (
                    :round_id,
                    :season_number,
                    :round_number,
                    :start_block,
                    :end_block,
                    :start_epoch,
                    :end_epoch,
                    :validator_uid,
                    :validator_hotkey,
                    :validator_coldkey,
                    :validator_round_id,
                    :name,
                    :image_url,
                    '1.0.0-demo',
                    :stake,
                    :vtrust,
                    :started_at,
                    :finished_at,
                    CAST(:config AS jsonb),
                    CAST(:post_consensus_json AS jsonb),
                    '{}'::jsonb,
                    '{}'::jsonb,
                    :logs_url,
                    :is_main
                )
                RETURNING round_validator_id
                """,
                {
                    "round_id": round_id,
                    "season_number": season_number,
                    "round_number": round_number,
                    "start_block": block_start,
                    "end_block": block_start + 99,
                    "start_epoch": epoch_start,
                    "end_epoch": epoch_start + 1,
                    "validator_uid": validator.uid,
                    "validator_hotkey": validator.hotkey,
                    "validator_coldkey": validator.coldkey,
                    "validator_round_id": validator_round_id,
                    "name": validator.name,
                    "image_url": validator.image_url,
                    "stake": validator.stake,
                    "vtrust": validator.vtrust,
                    "started_at": started_at,
                    "finished_at": ended_at,
                    "config": '{"max_tasks_per_miner": 3, "task_timeout_seconds": 180}',
                    "post_consensus_json": '{"summary": {"status": "demo"}}',
                    "logs_url": f"https://example.invalid/logs/{validator_round_id}.json",
                    "is_main": validator.is_main,
                },
            )
        ).scalar_one()
        validator_rows.append(round_validator_id)

        task_ids: list[str] = []
        for task_index, website in enumerate(WEBSITES, start=1):
            task_id = f"task_s{season_number}_r{round_number}_v{validator.uid}_{task_index}"
            task_ids.append(task_id)
            await _exec(
                session,
                """
                INSERT INTO tasks (
                    task_id,
                    round_validator_id,
                    web_project_id,
                    web_version,
                    url,
                    prompt,
                    specifications,
                    tests,
                    use_case,
                    created_at,
                    updated_at,
                    validator_round_id
                )
                VALUES (
                    :task_id,
                    :round_validator_id,
                    :website,
                    '0.1.0+showcase',
                    :url,
                    :prompt,
                    '{}'::jsonb,
                    '[]'::jsonb,
                    CAST(:use_case AS jsonb),
                    :created_at,
                    :updated_at,
                    :validator_round_id
                )
                """,
                {
                    "task_id": task_id,
                    "round_validator_id": round_validator_id,
                    "website": website,
                    "url": f"http://localhost:80{task_index}/showcase?s={season_number}&r={round_number}&v={validator.uid}",
                    "prompt": f"Demo task {task_index} for {validator.name} in season {season_number} round {round_number}",
                    "use_case": f'{{"name":"showcase_{website}","slug":"showcase-{website}"}}',
                    "created_at": started_at,
                    "updated_at": started_at,
                    "validator_round_id": validator_round_id,
                },
            )

        for miner in MINERS:
            metrics = _compute_round_metrics(season_number, round_number, validator.uid, miner)

            await _exec(
                session,
                """
                INSERT INTO round_validator_miners (
                    round_validator_id,
                    round_id,
                    miner_uid,
                    miner_hotkey,
                    miner_coldkey,
                    name,
                    image_url,
                    github_url,
                    is_sota,
                    version,
                    local_avg_reward,
                    local_avg_eval_score,
                    local_avg_eval_time,
                    local_tasks_received,
                    local_tasks_success,
                    post_consensus_rank,
                    post_consensus_avg_reward,
                    post_consensus_avg_eval_score,
                    post_consensus_avg_eval_time,
                    post_consensus_tasks_received,
                    post_consensus_tasks_success,
                    weight,
                    subnet_price,
                    best_local_rank,
                    best_local_reward,
                    best_local_eval_score,
                    best_local_eval_time,
                    local_avg_eval_cost,
                    post_consensus_avg_eval_cost,
                    best_local_eval_cost
                )
                VALUES (
                    :round_validator_id,
                    :round_id,
                    :miner_uid,
                    :miner_hotkey,
                    :miner_coldkey,
                    :name,
                    :image_url,
                    :github_url,
                    false,
                    '1.0.0-demo',
                    :local_avg_reward,
                    :local_avg_eval_score,
                    :local_avg_eval_time,
                    3,
                    :local_tasks_success,
                    :post_consensus_rank,
                    :post_consensus_avg_reward,
                    :post_consensus_avg_eval_score,
                    :post_consensus_avg_eval_time,
                    3,
                    :post_consensus_tasks_success,
                    :weight,
                    0.00416,
                    :best_local_rank,
                    :best_local_reward,
                    :best_local_eval_score,
                    :best_local_eval_time,
                    0.0012,
                    0.0012,
                    0.0012
                )
                """,
                {
                    "round_validator_id": round_validator_id,
                    "round_id": round_id,
                    "miner_uid": miner.uid,
                    "miner_hotkey": miner.hotkey,
                    "miner_coldkey": miner.coldkey,
                    "name": miner.name,
                    "image_url": miner.image_url,
                    "github_url": miner.github_url,
                    "local_avg_reward": metrics["local_reward"],
                    "local_avg_eval_score": metrics["local_score"],
                    "local_avg_eval_time": metrics["local_time"],
                    "local_tasks_success": metrics["local_success"],
                    "post_consensus_rank": metrics["post_rank"],
                    "post_consensus_avg_reward": metrics["post_reward"],
                    "post_consensus_avg_eval_score": metrics["post_score"],
                    "post_consensus_avg_eval_time": metrics["post_time"],
                    "post_consensus_tasks_success": metrics["post_success"],
                    "weight": metrics["weight"],
                    "best_local_rank": metrics["post_rank"],
                    "best_local_reward": metrics["local_reward"],
                    "best_local_eval_score": metrics["local_score"],
                    "best_local_eval_time": metrics["local_time"],
                },
            )

            run_id = f"agent_run_s{season_number}_r{round_number}_v{validator.uid}_m{miner.uid}"
            await _exec(
                session,
                """
                INSERT INTO miner_evaluation_runs (
                    agent_run_id,
                    round_validator_id,
                    miner_uid,
                    miner_hotkey,
                    started_at,
                    ended_at,
                    elapsed_sec,
                    average_score,
                    average_execution_time,
                    average_reward,
                    total_tasks,
                    success_tasks,
                    failed_tasks,
                    tasks_attempted,
                    zero_reason,
                    early_stop_reason,
                    early_stop_message,
                    validator_round_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    :agent_run_id,
                    :round_validator_id,
                    :miner_uid,
                    :miner_hotkey,
                    :started_at,
                    :ended_at,
                    :elapsed_sec,
                    :average_score,
                    :average_execution_time,
                    :average_reward,
                    3,
                    :success_tasks,
                    :failed_tasks,
                    3,
                    :zero_reason,
                    :early_stop_reason,
                    :early_stop_message,
                    :validator_round_id,
                    :created_at,
                    :updated_at
                )
                """,
                {
                    "agent_run_id": run_id,
                    "round_validator_id": round_validator_id,
                    "miner_uid": miner.uid,
                    "miner_hotkey": miner.hotkey,
                    "started_at": started_at.timestamp() + 30,
                    "ended_at": (ended_at or started_at).timestamp() if ended_at else None,
                    "elapsed_sec": metrics["local_time"],
                    "average_score": metrics["post_score"],
                    "average_execution_time": metrics["post_time"],
                    "average_reward": metrics["post_reward"],
                    "success_tasks": metrics["local_success"],
                    "failed_tasks": 3 - metrics["local_success"],
                    "zero_reason": metrics["zero_reason"],
                    "early_stop_reason": None if metrics["local_success"] == 3 else "partial_failure",
                    "early_stop_message": None if metrics["local_success"] == 3 else "One showcase task failed",
                    "validator_round_id": validator_round_id,
                    "created_at": started_at,
                    "updated_at": ended_at or started_at,
                },
            )

            for task_index, task_id in enumerate(task_ids, start=1):
                solution_id = f"solution_{run_id}_{task_index}"
                evaluation_id = f"evaluation_{run_id}_{task_index}"
                score = 1.0 if task_index <= metrics["local_success"] else 0.0
                reward = round(score * metrics["local_reward"], 4)
                eval_time = metrics["local_time"] + float(task_index)
                zero_reason = None if score > 0 else metrics["zero_reason"]

                await _exec(
                    session,
                    """
                    INSERT INTO task_solutions (
                        solution_id,
                        task_id,
                        agent_run_id,
                        actions,
                        created_at,
                        updated_at,
                        validator_round_id,
                        validator_uid,
                        validator_hotkey,
                        miner_uid,
                        miner_hotkey
                    )
                    VALUES (
                        :solution_id,
                        :task_id,
                        :agent_run_id,
                        '[{"type":"CLICK","attributes":{"selector":"#submit"}}]'::jsonb,
                        :created_at,
                        :updated_at,
                        :validator_round_id,
                        :validator_uid,
                        :validator_hotkey,
                        :miner_uid,
                        :miner_hotkey
                    )
                    """,
                    {
                        "solution_id": solution_id,
                        "task_id": task_id,
                        "agent_run_id": run_id,
                        "created_at": started_at,
                        "updated_at": ended_at or started_at,
                        "validator_round_id": validator_round_id,
                        "validator_uid": validator.uid,
                        "validator_hotkey": validator.hotkey,
                        "miner_uid": miner.uid,
                        "miner_hotkey": miner.hotkey,
                    },
                )

                await _exec(
                    session,
                    """
                    INSERT INTO evaluations (
                        evaluation_id,
                        task_id,
                        task_solution_id,
                        agent_run_id,
                        miner_uid,
                        miner_hotkey,
                        validator_uid,
                        validator_hotkey,
                        evaluation_score,
                        reward,
                        evaluation_time,
                        gif_recording,
                        extra_info,
                        zero_reason,
                        created_at,
                        updated_at,
                        validator_round_id
                    )
                    VALUES (
                        :evaluation_id,
                        :task_id,
                        :task_solution_id,
                        :agent_run_id,
                        :miner_uid,
                        :miner_hotkey,
                        :validator_uid,
                        :validator_hotkey,
                        :evaluation_score,
                        :reward,
                        :evaluation_time,
                        NULL,
                        '{}'::jsonb,
                        :zero_reason,
                        :created_at,
                        :updated_at,
                        :validator_round_id
                    )
                    """,
                    {
                        "evaluation_id": evaluation_id,
                        "task_id": task_id,
                        "task_solution_id": solution_id,
                        "agent_run_id": run_id,
                        "miner_uid": miner.uid,
                        "miner_hotkey": miner.hotkey,
                        "validator_uid": validator.uid,
                        "validator_hotkey": validator.hotkey,
                        "evaluation_score": score,
                        "reward": reward,
                        "evaluation_time": eval_time,
                        "zero_reason": zero_reason,
                        "created_at": started_at,
                        "updated_at": ended_at or started_at,
                        "validator_round_id": validator_round_id,
                    },
                )

                await _exec(
                    session,
                    """
                    INSERT INTO evaluations_execution_history (
                        evaluation_id,
                        execution_history,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :evaluation_id,
                        CAST(:execution_history AS jsonb),
                        :created_at,
                        :updated_at
                    )
                    """,
                    {
                        "evaluation_id": evaluation_id,
                        "execution_history": (
                            '[{"step":"open_url","status":"ok"},{"step":"click_submit","status":"ok"}]'
                            if score > 0
                            else '[{"step":"open_url","status":"ok"},{"step":"click_submit","status":"timeout"}]'
                        ),
                        "created_at": started_at,
                        "updated_at": ended_at or started_at,
                    },
                )

                await _exec(
                    session,
                    """
                    INSERT INTO evaluation_llm_usage (
                        evaluation_id,
                        provider,
                        model,
                        tokens,
                        cost,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :evaluation_id,
                        'openai',
                        'gpt-4.1-mini',
                        :tokens,
                        :cost,
                        :created_at,
                        :updated_at
                    )
                    """,
                    {
                        "evaluation_id": evaluation_id,
                        "tokens": 900 + (task_index * 120),
                        "cost": 0.001 + (task_index * 0.0002),
                        "created_at": started_at,
                        "updated_at": ended_at or started_at,
                    },
                )

                await _exec(
                    session,
                    """
                    INSERT INTO task_execution_logs (
                        task_id,
                        agent_run_id,
                        validator_round_id,
                        validator_uid,
                        miner_uid,
                        season,
                        round_in_season,
                        payload_ref,
                        payload_size,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        :task_id,
                        :agent_run_id,
                        :validator_round_id,
                        :validator_uid,
                        :miner_uid,
                        :season,
                        :round_in_season,
                        :payload_ref,
                        :payload_size,
                        :created_at,
                        :updated_at
                    )
                    """,
                    {
                        "task_id": task_id,
                        "agent_run_id": run_id,
                        "validator_round_id": validator_round_id,
                        "validator_uid": validator.uid,
                        "miner_uid": miner.uid,
                        "season": season_number,
                        "round_in_season": round_number,
                        "payload_ref": f"s3://autoppia-demo/task-logs/{evaluation_id}.json",
                        "payload_size": 2048 + (task_index * 256),
                        "created_at": started_at,
                        "updated_at": ended_at or started_at,
                    },
                )

    winner = MINERS[0]
    candidate = MINERS[1]
    if consensus_status == "finalized":
        await _exec(
            session,
            """
            INSERT INTO round_summary (
                round_id,
                source_round_validator_id,
                source_validator_uid,
                source_is_main_validator,
                leader_before_miner_uid,
                leader_before_miner_hotkey,
                leader_before_github_url,
                leader_before_reward,
                candidate_miner_uid,
                candidate_miner_hotkey,
                candidate_github_url,
                candidate_reward,
                leader_after_miner_uid,
                leader_after_miner_hotkey,
                leader_after_github_url,
                leader_after_reward,
                required_improvement_pct,
                required_reward_to_dethrone,
                dethroned,
                validators_count,
                miners_evaluated,
                tasks_evaluated,
                tasks_success,
                avg_reward,
                avg_eval_score,
                avg_eval_time,
                avg_eval_cost,
                leader_after_eval_score,
                leader_after_eval_time,
                leader_after_eval_cost,
                post_consensus_json,
                created_at,
                updated_at
            )
            VALUES (
                :round_id,
                :source_round_validator_id,
                :source_validator_uid,
                true,
                :leader_before_miner_uid,
                :leader_before_miner_hotkey,
                :leader_before_github_url,
                :leader_before_reward,
                :candidate_miner_uid,
                :candidate_miner_hotkey,
                :candidate_github_url,
                :candidate_reward,
                :leader_after_miner_uid,
                :leader_after_miner_hotkey,
                :leader_after_github_url,
                :leader_after_reward,
                0.05,
                :required_reward_to_dethrone,
                :dethroned,
                2,
                3,
                18,
                12,
                :avg_reward,
                :avg_eval_score,
                :avg_eval_time,
                0.0012,
                :leader_after_eval_score,
                :leader_after_eval_time,
                0.0012,
                '{"status":"finalized","source":"showcase_seed"}'::jsonb,
                :created_at,
                :updated_at
            )
            """,
            {
                "round_id": round_id,
                "source_round_validator_id": validator_rows[0],
                "source_validator_uid": VALIDATORS[0].uid,
                "leader_before_miner_uid": winner.uid,
                "leader_before_miner_hotkey": winner.hotkey,
                "leader_before_github_url": winner.github_url,
                "leader_before_reward": round(_compute_round_metrics(season_number, round_number, 21, winner)["post_reward"] - 0.02, 4),
                "candidate_miner_uid": candidate.uid,
                "candidate_miner_hotkey": candidate.hotkey,
                "candidate_github_url": candidate.github_url,
                "candidate_reward": _compute_round_metrics(season_number, round_number, 21, candidate)["post_reward"],
                "leader_after_miner_uid": winner.uid,
                "leader_after_miner_hotkey": winner.hotkey,
                "leader_after_github_url": winner.github_url,
                "leader_after_reward": _compute_round_metrics(season_number, round_number, 21, winner)["post_reward"],
                "required_reward_to_dethrone": round(_compute_round_metrics(season_number, round_number, 21, winner)["post_reward"] * 1.05, 4),
                "dethroned": False,
                "avg_reward": round(
                    sum(_compute_round_metrics(season_number, round_number, 21, miner)["post_reward"] for miner in MINERS) / len(MINERS),
                    4,
                ),
                "avg_eval_score": round(
                    sum(_compute_round_metrics(season_number, round_number, 21, miner)["post_score"] for miner in MINERS) / len(MINERS),
                    4,
                ),
                "avg_eval_time": round(
                    sum(_compute_round_metrics(season_number, round_number, 21, miner)["post_time"] for miner in MINERS) / len(MINERS),
                    2,
                ),
                "leader_after_eval_score": _compute_round_metrics(season_number, round_number, 21, winner)["post_score"],
                "leader_after_eval_time": _compute_round_metrics(season_number, round_number, 21, winner)["post_time"],
                "created_at": ended_at or started_at,
                "updated_at": ended_at or started_at,
            },
        )

    return round_id, validator_rows[0]


async def seed_showcase(*, truncate: bool) -> dict[str, Any]:
    from app.config import settings
    from app.db.session import AsyncSessionLocal

    now = datetime.now(timezone.utc).replace(microsecond=0)
    current_round_id: int | None = None

    async with AsyncSessionLocal() as session:
        if truncate:
            await _reset_demo_data(session)

        await _exec(
            session,
            """
            INSERT INTO config_app_runtime (
                id,
                main_validator_uid,
                main_validator_hotkey,
                minimum_validator_version,
                created_at,
                updated_at
            )
            VALUES (1, :main_uid, :main_hotkey, '1.0.0-demo', :now, :now)
            ON CONFLICT (id) DO UPDATE SET
                main_validator_uid = EXCLUDED.main_validator_uid,
                main_validator_hotkey = EXCLUDED.main_validator_hotkey,
                minimum_validator_version = EXCLUDED.minimum_validator_version,
                updated_at = EXCLUDED.updated_at
            """,
            {"main_uid": VALIDATORS[0].uid, "main_hotkey": VALIDATORS[0].hotkey, "now": now},
        )

        await _exec(
            session,
            """
            INSERT INTO config_season_round (
                id,
                round_size_epochs,
                season_size_epochs,
                minimum_start_block,
                blocks_per_epoch,
                updated_at,
                updated_by_validator_uid
            )
            VALUES (
                1,
                :round_size_epochs,
                :season_size_epochs,
                :minimum_start_block,
                :blocks_per_epoch,
                :updated_at,
                :updated_by_validator_uid
            )
            ON CONFLICT (id) DO UPDATE SET
                round_size_epochs = EXCLUDED.round_size_epochs,
                season_size_epochs = EXCLUDED.season_size_epochs,
                minimum_start_block = EXCLUDED.minimum_start_block,
                blocks_per_epoch = EXCLUDED.blocks_per_epoch,
                updated_at = EXCLUDED.updated_at,
                updated_by_validator_uid = EXCLUDED.updated_by_validator_uid
            """,
            {
                "round_size_epochs": float(settings.ROUND_SIZE_EPOCHS),
                "season_size_epochs": float(settings.SEASON_SIZE_EPOCHS),
                "minimum_start_block": 7702861,
                "blocks_per_epoch": int(settings.BLOCKS_PER_EPOCH),
                "updated_at": now,
                "updated_by_validator_uid": VALIDATORS[0].uid,
            },
        )

        for season_number in (1, 2):
            season_status = "finished" if season_number == 1 else "active"
            season_start = now - timedelta(days=14 - (season_number * 5))
            season_end = season_start + timedelta(days=3) if season_number == 1 else None
            leader = MINERS[0] if season_number == 2 else MINERS[1]
            leader_reward = 0.90 if season_number == 2 else 0.72

            season_id = (
                await _exec(
                    session,
                    """
                    INSERT INTO seasons (
                        season_number,
                        status,
                        start_block,
                        end_block,
                        start_at,
                        end_at,
                        required_improvement_pct,
                        leader_miner_uid,
                        leader_reward,
                        leader_github_url
                    )
                    VALUES (
                        :season_number,
                        :status,
                        :start_block,
                        :end_block,
                        :start_at,
                        :end_at,
                        0.05,
                        :leader_miner_uid,
                        :leader_reward,
                        :leader_github_url
                    )
                    RETURNING season_id
                    """,
                    {
                        "season_number": season_number,
                        "status": season_status,
                        "start_block": 7702861 + ((season_number - 1) * 500),
                        "end_block": 7702861 + ((season_number - 1) * 500) + 299 if season_number == 1 else None,
                        "start_at": season_start,
                        "end_at": season_end,
                        "leader_miner_uid": leader.uid,
                        "leader_reward": leader_reward,
                        "leader_github_url": leader.github_url,
                    },
                )
            ).scalar_one()

            for round_number in (1, 2, 3):
                is_current_round = season_number == 2 and round_number == 3
                round_status = "active" if is_current_round else "finished"
                consensus_status = "pending" if is_current_round else "finalized"
                round_started_at = season_start + timedelta(hours=(round_number - 1) * 8)
                round_ended_at = None if is_current_round else round_started_at + timedelta(hours=2)

                round_id, _ = await _seed_round(
                    session,
                    season_id=season_id,
                    season_number=season_number,
                    round_number=round_number,
                    round_status=round_status,
                    consensus_status=consensus_status,
                    block_start=7702861 + ((season_number - 1) * 500) + ((round_number - 1) * 100),
                    epoch_start=21131 + ((season_number - 1) * 6) + ((round_number - 1) * 2),
                    started_at=round_started_at,
                    ended_at=round_ended_at,
                )
                if is_current_round:
                    current_round_id = round_id

        await session.commit()

    return {
        "validators": len(VALIDATORS),
        "miners": len(MINERS),
        "seasons": 2,
        "rounds_per_season": 3,
        "current_round_id": current_round_id,
    }


async def smoke_endpoints() -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://seed-server") as client:
        urls = [
            "/api/v1/overview/metrics",
            "/api/v1/overview/rounds/current",
            "/api/v1/rounds/current",
            "/api/v1/agents/latest-round-top-miner",
            "/api/v1/agents/120/historical?season=2",
            "/api/v1/rounds/2/3",
            "/api/v1/agents/120?season=2&round=3",
        ]
        for url in urls:
            response = await client.get(url)
            print(f"GET {url} -> {response.status_code}")
            if response.status_code >= 400:
                raise RuntimeError(f"{url} failed: {response.status_code} {response.text}")


async def main_async(args: argparse.Namespace) -> None:
    if args.create_schema:
        _run_shell_script(ROOT / "scripts" / "bash" / "create_tables.sh")
    if args.truncate_with_script:
        _run_shell_script(ROOT / "scripts" / "bash" / "truncate_all_tables.sh")

    result = await seed_showcase(truncate=not args.no_truncate and not args.truncate_with_script)
    print(
        "Seeded showcase database: "
        f"{result['validators']} validators, {result['miners']} miners, "
        f"{result['seasons']} seasons, {result['rounds_per_season']} rounds/season, "
        f"current_round_id={result['current_round_id']}"
    )

    if args.smoke:
        await smoke_endpoints()
        print("Endpoint smoke OK")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a showcase dataset for opening the repo.")
    parser.add_argument("--create-schema", action="store_true", help="Run scripts/bash/create_tables.sh before seeding.")
    parser.add_argument(
        "--truncate-with-script",
        action="store_true",
        help="Run scripts/bash/truncate_all_tables.sh before seeding.",
    )
    parser.add_argument(
        "--no-truncate",
        action="store_true",
        help="Do not truncate demo tables inside the Python seed step.",
    )
    parser.add_argument("--smoke", action="store_true", help="Call a small set of API endpoints after seeding.")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
