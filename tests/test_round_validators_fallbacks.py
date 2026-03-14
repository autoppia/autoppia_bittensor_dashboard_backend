from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.db.models import ValidatorRoundORM, ValidatorRoundSummaryORM, ValidatorRoundValidatorORM
from app.services.ui.ui_data_service import UIDataService
from app.services.validator.validator_storage import ValidatorRoundPersistenceService


@pytest.mark.asyncio
async def test_round_validators_views_reconstruct_local_state_from_runs_and_downloaded_ipfs(db_session):
    """
    Scenario:
    Round 1 already has canonical round rows, but some validators never completed
    `finish_round` in IWAP. One validator only survives via local
    `miner_evaluation_runs` and another only survives inside the main validator's
    downloaded IPFS payload.

    What this test proves:
    the rounds UI can rebuild each validator's local competition state from
    durable sources instead of showing everything as 0.00%.
    """

    await db_session.execute(
        text(
            """
            CREATE TABLE config_app_runtime (
              id INTEGER PRIMARY KEY,
              main_validator_uid INTEGER
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            CREATE TABLE seasons (
              season_id INTEGER PRIMARY KEY,
              season_number INTEGER NOT NULL,
              leader_miner_uid INTEGER,
              leader_reward DOUBLE PRECISION,
              status VARCHAR(32),
              start_block BIGINT,
              end_block BIGINT,
              required_improvement_pct DOUBLE PRECISION,
              created_at TIMESTAMPTZ NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            CREATE TABLE rounds (
              round_id INTEGER PRIMARY KEY,
              season_id INTEGER NOT NULL,
              round_number_in_season INTEGER NOT NULL,
              start_block BIGINT,
              end_block BIGINT,
              planned_start_block BIGINT,
              planned_end_block BIGINT,
              start_epoch INTEGER,
              end_epoch INTEGER,
              started_at TIMESTAMPTZ,
              ended_at TIMESTAMPTZ,
              status VARCHAR(32),
              consensus_status VARCHAR(32),
              created_at TIMESTAMPTZ NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            CREATE TABLE round_validators (
              round_validator_id INTEGER PRIMARY KEY,
              round_id INTEGER NOT NULL,
              season_number INTEGER,
              round_number_in_season INTEGER,
              start_block BIGINT,
              end_block BIGINT,
              start_epoch INTEGER,
              end_epoch INTEGER,
              validator_uid INTEGER NOT NULL,
              validator_hotkey TEXT,
              validator_round_id TEXT,
              name TEXT,
              image_url TEXT,
              version TEXT,
              stake DOUBLE PRECISION,
              vtrust DOUBLE PRECISION,
              started_at TIMESTAMPTZ,
              finished_at TIMESTAMPTZ,
              ipfs_uploaded JSONB,
              ipfs_downloaded JSONB,
              post_consensus_json JSONB,
              is_main_validator BOOLEAN,
              created_at TIMESTAMPTZ NOT NULL,
              updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            CREATE TABLE round_validator_miners (
              id INTEGER PRIMARY KEY,
              round_validator_id INTEGER NOT NULL,
              round_id INTEGER NOT NULL,
              miner_uid INTEGER,
              name TEXT,
              miner_hotkey TEXT,
              github_url TEXT,
              image_url TEXT,
              local_avg_reward DOUBLE PRECISION,
              local_avg_eval_score DOUBLE PRECISION,
              local_avg_eval_time DOUBLE PRECISION,
              local_avg_eval_cost DOUBLE PRECISION,
              best_local_rank INTEGER,
              best_local_reward DOUBLE PRECISION,
              best_local_eval_score DOUBLE PRECISION,
              best_local_eval_time DOUBLE PRECISION,
              best_local_eval_cost DOUBLE PRECISION,
              post_consensus_tasks_received INTEGER
            )
            """
        )
    )
    await db_session.execute(text("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS round_validator_id INTEGER"))
    await db_session.execute(text("INSERT INTO config_app_runtime (id, main_validator_uid) VALUES (1, 83)"))

    await db_session.execute(
        text(
            """
            INSERT INTO seasons (
              season_id, season_number, status, start_block, end_block,
              required_improvement_pct, created_at, updated_at
            ) VALUES (
              1, 1, 'active', 7736300, 7738100, 0.05, NOW(), NOW()
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            INSERT INTO rounds (
              round_id, season_id, round_number_in_season, start_block, end_block,
              planned_start_block, planned_end_block, start_epoch, end_epoch,
              started_at, status, consensus_status, created_at, updated_at
            ) VALUES (
              1, 1, 1, 7736300, 7738100,
              7736300, 7738100, 21489, 21494,
              NOW(), 'finished', 'completed', NOW(), NOW()
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            INSERT INTO validator_rounds (
              validator_round_id, season_number, round_number_in_season,
              start_block, end_block, start_epoch, end_epoch, started_at,
              n_tasks, status, created_at, updated_at
            ) VALUES (
              'validator_round_1_1_20hash', 1, 1,
              7736300, 7738100, 21489, 21494, 1.0,
              100, 'finished', NOW(), NOW()
            )
            """
        )
    )

    validator_55_downloaded_payload = {
        "uid": 55,
        "validator_uid": 55,
        "hk": "validator-55-hotkey",
        "validator_hotkey": "validator-55-hotkey",
        "validator_round_id": "validator_round_1_1_55hash",
        "miners": [
            {
                "uid": 48,
                "hotkey": "miner-48-hotkey",
                "miner_name": "autoppia operator",
                "best_run": {
                    "reward": 0.12,
                    "score": 0.14,
                    "time": 91.0,
                    "cost": 0.031,
                    "github_url": "https://github.com/autoppia/operator/tree/main",
                },
                "current_run": {
                    "reward": 0.09,
                    "score": 0.10,
                    "time": 95.0,
                    "cost": 0.033,
                    "github_url": "https://github.com/autoppia/operator/tree/main",
                },
            },
            {
                "uid": 196,
                "hotkey": "miner-196-hotkey",
                "miner_name": "OJO Agent",
                "best_run": {
                    "reward": 0.11,
                    "score": 0.12,
                    "time": 98.0,
                    "cost": 0.041,
                    "github_url": "https://github.com/ojo/agent/tree/main",
                },
                "current_run": None,
            },
        ],
        "summary": {"validator_all_runs_zero": False},
    }
    ipfs_downloaded = {
        "validators_participated": 2,
        "payloads": [
            {
                "validator_uid": 55,
                "validator_hotkey": "validator-55-hotkey",
                "cid": "bafy55",
                "payload": validator_55_downloaded_payload,
            }
        ],
    }

    _ = (
        await db_session.execute(
            text(
                """
                INSERT INTO round_validators (
                  round_validator_id, round_id, season_number, round_number_in_season,
                  start_block, end_block, start_epoch, end_epoch, validator_uid,
                  validator_hotkey, validator_round_id, name, image_url, version,
                  stake, vtrust, started_at, is_main_validator, created_at, updated_at
                ) VALUES (
                  20, 1, 1, 1, 7736300, 7738100, 21489, 21494, 20,
                  'validator-20-hotkey', 'validator_round_1_1_20hash', 'RT21',
                  '/validators/rt21.png', '16.0.0', 84846.71, 0.92, NOW(), FALSE,
                  NOW(), NOW()
                )
                RETURNING round_validator_id
                """
            )
        )
    ).scalar_one()
    await db_session.execute(
        text(
            """
            INSERT INTO round_validators (
              round_validator_id, round_id, season_number, round_number_in_season,
              start_block, end_block, start_epoch, end_epoch, validator_uid,
              validator_hotkey, validator_round_id, name, image_url, version,
              stake, vtrust, started_at, is_main_validator, created_at, updated_at
            ) VALUES (
              55, 1, 1, 1, 7736300, 7738100, 21489, 21494, 55,
              'validator-55-hotkey', 'validator_round_1_1_55hash', 'Yuma',
              '/validators/yuma.png', '16.0.0', 217429.46, 0.99, NOW(), FALSE,
              NOW(), NOW()
            )
            """
        )
    )
    await db_session.execute(
        text(
            """
            INSERT INTO round_validators (
              round_validator_id, round_id, season_number, round_number_in_season,
              start_block, end_block, start_epoch, end_epoch, validator_uid,
              validator_hotkey, validator_round_id, name, image_url, version,
              stake, vtrust, started_at, finished_at, ipfs_downloaded,
              is_main_validator, created_at, updated_at
            ) VALUES (
              83, 1, 1, 1, 7736300, 7738100, 21489, 21494, 83,
              'validator-83-hotkey', 'validator_round_1_1_83hash', 'Autoppia',
              '/validators/autoppia.png', '16.0.0', 1551560.50, 0.999, NOW(),
              NOW(), CAST(:ipfs_downloaded AS JSONB), TRUE, NOW(), NOW()
            )
            """
        ),
        {"ipfs_downloaded": json.dumps(ipfs_downloaded)},
    )

    await db_session.execute(
        text(
            """
            INSERT INTO miner_evaluation_runs (
              agent_run_id, validator_round_id, miner_uid, miner_hotkey,
              started_at, ended_at, elapsed_sec, average_score, average_execution_time,
              average_reward, total_tasks, success_tasks, failed_tasks, created_at, updated_at
            ) VALUES
              (
                'run-20-48', 'validator_round_1_1_20hash', 48, 'miner-48-hotkey',
                1.0, 2.0, 1.0, 0.28, 64.9224,
                0.2604146231, 25, 7, 18, NOW(), NOW()
              ),
              (
                'run-20-196', 'validator_round_1_1_20hash', 196, 'miner-196-hotkey',
                1.0, 2.0, 1.0, 0.15, 59.0477,
                0.1390282884, 20, 3, 17, NOW(), NOW()
              )
            """
        )
    )

    await db_session.commit()

    service = UIDataService(db_session)
    body = await service.get_round_validators_view(1, 1)
    validators_by_uid = {int(item["validator_uid"]): item for item in body["validators"]}

    validator_20 = validators_by_uid[20]
    assert validator_20["finished_at"] is None
    assert validator_20["competition_state"]["top_reward"] == pytest.approx(0.2604146231)
    miner_20_48 = next(miner for miner in validator_20["competition_state"]["miners"] if int(miner["uid"]) == 48)
    assert miner_20_48["best_local_reward"] == pytest.approx(0.2604146231)
    assert miner_20_48["local_avg_reward"] == pytest.approx(0.2604146231)

    validator_55 = validators_by_uid[55]
    assert validator_55["finished_at"] is None
    assert validator_55["competition_state"]["top_reward"] == pytest.approx(0.12)
    miner_55_48 = next(miner for miner in validator_55["competition_state"]["miners"] if int(miner["uid"]) == 48)
    assert miner_55_48["best_local_reward"] == pytest.approx(0.12)
    assert miner_55_48["local_avg_reward"] == pytest.approx(0.09)

    compact = await service.get_round_validators_data("1")
    compact_by_id = {item["id"]: item for item in compact["validators"]}
    assert compact_by_id["validator-20"]["topReward"] == pytest.approx(0.2604146231)
    assert compact_by_id["validator-55"]["topReward"] == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_post_consensus_preserves_effective_validators_participated_from_payload(db_session):
    """
    Scenario:
    A validator payload already reports that two validators participated in
    consensus, but backend enrichment only sees one validator snapshot row for
    this specific validator_round and would otherwise collapse the metadata to 1.

    What this test proves:
    backend preserves the effective `validators_participated` from the
    consensus payload instead of overwriting it with the per-validator row
    count fallback.
    """

    round_row = ValidatorRoundORM(
        validator_round_id="validator_round_1_1_55hash",
        season_number=1,
        round_number_in_season=1,
        start_block=7740561,
        end_block=7742361,
        start_epoch=21500,
        end_epoch=21505,
        started_at=1.0,
        ended_at=2.0,
        n_tasks=100,
        status="finished",
        validator_summary={
            "evaluation_post_consensus": {
                "season": 1,
                "round": 1,
                "consensus_type": "stake_weighted",
                "validators_participated": 2,
                "miners": [
                    {
                        "uid": 48,
                        "hotkey": "miner-48-hotkey",
                        "best_run_consensus": {
                            "reward": 0.02752420449615931,
                            "score": 0.03,
                            "time": 79.0,
                            "cost": 0.03,
                            "tasks_received": 200,
                            "tasks_success": 8,
                            "rank": 1,
                            "weight": 0.075,
                        },
                    }
                ],
                "summary": {
                    "percentage_to_dethrone": 0.05,
                    "dethroned": False,
                    "leader_before_round": None,
                    "candidate_this_round": {"uid": 48, "reward": 0.02752420449615931},
                    "leader_after_round": {"uid": 48, "reward": 0.02752420449615931, "weight": 0.075},
                },
            }
        },
    )
    db_session.add(round_row)
    db_session.add(
        ValidatorRoundValidatorORM(
            validator_round_id=round_row.validator_round_id,
            validator_uid=55,
            validator_hotkey="validator-55-hotkey",
            name="Yuma",
            version="20.0.0",
        )
    )
    db_session.add(
        ValidatorRoundSummaryORM(
            validator_round_id=round_row.validator_round_id,
            miner_uid=48,
            miner_hotkey="miner-48-hotkey",
            post_consensus_rank=1,
            post_consensus_avg_reward=0.02752420449615931,
            post_consensus_avg_eval_score=0.03,
            post_consensus_avg_eval_time=79.0,
            post_consensus_avg_eval_cost=0.03,
            post_consensus_tasks_received=200,
            post_consensus_tasks_success=8,
            weight=0.075,
        )
    )
    await db_session.flush()

    service = ValidatorRoundPersistenceService(db_session)
    await service._enrich_validator_summary_post_consensus_from_db(round_row)

    enriched = round_row.validator_summary["evaluation_post_consensus"]
    assert enriched["validators_participated"] == 2
