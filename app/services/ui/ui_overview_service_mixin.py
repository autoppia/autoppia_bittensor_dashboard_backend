from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.services.metagraph_service import MetagraphError, get_validator_data
from app.services.round_config_service import get_config_season_round


def _normalize_stake_to_rao(stake: Any) -> float:
    if stake is None:
        return 0.0
    value = float(stake or 0.0)
    if value <= 0:
        return 0.0
    if value < 1.0:
        return value * 1_000_000_000
    return value


class UIOverviewServiceMixin:
    async def get_overview_metrics(self) -> Dict[str, Any]:
        runtime_config = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT minimum_validator_version
                    FROM config_app_runtime
                    WHERE id = 1
                    LIMIT 1
                    """
                    )
                )
            )
            .mappings()
            .first()
        )
        subnet_version = str(runtime_config["minimum_validator_version"]) if runtime_config and runtime_config.get("minimum_validator_version") else ""
        latest_any = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT s.season_number, r.round_number_in_season, r.round_id
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    ORDER BY s.season_number DESC, r.round_number_in_season DESC
                    LIMIT 1
                    """
                    )
                )
            )
            .mappings()
            .first()
        )

        latest_finished = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT s.season_number, r.round_number_in_season, r.round_id
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE lower(COALESCE(r.status, '')) IN ('finished', 'evaluating_finished')
                    ORDER BY s.season_number DESC, r.round_number_in_season DESC
                    LIMIT 1
                    """
                    )
                )
            )
            .mappings()
            .first()
        )

        if not latest_any:
            return {
                "leader": None,
                "season": None,
                "round": None,
                "totalMiners": 0,
                "tasksPerValidator": 0,
                "minerList": [],
                "subnetVersion": subnet_version,
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

        has_finished_round = latest_finished is not None
        metrics_source = latest_finished
        metrics_season = int(metrics_source["season_number"]) if metrics_source else None
        metrics_round_id = int(metrics_source["round_id"]) if metrics_source else None
        metrics_round_in_season = int(metrics_source["round_number_in_season"]) if metrics_source else None

        current_season = int(latest_any["season_number"])
        current_round_in_season = int(latest_any["round_number_in_season"])
        current_round_id = int(latest_any["round_id"])

        current_validators = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT rv.validator_uid)
                    FROM round_validators rv
                    WHERE rv.round_id = :round_id
                    """
                ),
                {"round_id": current_round_id},
            )
        ).scalar_one()

        current_total_miners = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT rvm.miner_uid)
                    FROM round_validator_miners rvm
                    WHERE rvm.round_id = :round_id
                    """
                ),
                {"round_id": current_round_id},
            )
        ).scalar_one()

        current_tasks_per_validator = (
            await self.session.execute(
                text(
                    """
                    SELECT COALESCE(MAX(cnt),0)
                    FROM (
                      SELECT round_validator_id, COUNT(*) AS cnt
                      FROM tasks
                      WHERE round_validator_id IN (
                        SELECT rv.round_validator_id
                        FROM round_validators rv
                        WHERE rv.round_id = :round_id
                      )
                      GROUP BY round_validator_id
                    ) x
                    """
                ),
                {"round_id": current_round_id},
            )
        ).scalar_one()
        configured_tasks_per_validator = (
            await self.session.execute(
                text(
                    """
                    SELECT MAX((rv.config->'round'->>'tasks_per_season')::INTEGER)
                    FROM round_validators rv
                    WHERE rv.round_id = :round_id
                      AND rv.config IS NOT NULL
                      AND rv.config->'round'->>'tasks_per_season' IS NOT NULL
                    """
                ),
                {"round_id": current_round_id},
            )
        ).scalar_one()
        effective_tasks_per_validator = int(configured_tasks_per_validator or current_tasks_per_validator or 0)

        round_duration_minutes = None
        season_duration_minutes = None
        season_rounds = None
        try:
            cfg = get_config_season_round()
            round_duration_minutes = int(round(cfg.round_blocks() * 12 / 60))
            season_duration_minutes = int(round(cfg.season_blocks() * 12 / 60))
            if cfg.round_size_epochs > 0:
                season_rounds = int(round(cfg.season_size_epochs / cfg.round_size_epochs))
        except Exception:
            cfg = None

        season_task_volume = (
            effective_tasks_per_validator * int(current_validators or 0) * int(season_rounds or 0)
            if effective_tasks_per_validator > 0 and int(current_validators or 0) > 0 and int(season_rounds or 0) > 0
            else None
        )

        previous_round_ref = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT r.round_id, r.round_number_in_season
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE s.season_number = :season
                          AND r.round_number_in_season < :round
                        ORDER BY r.round_number_in_season DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "season": current_season,
                        "round": current_round_in_season,
                    },
                )
            )
            .mappings()
            .first()
        )
        previous_round_id = int(previous_round_ref["round_id"]) if previous_round_ref and previous_round_ref.get("round_id") is not None else None

        miner_updates_this_round = 0
        if previous_round_id is not None:
            miner_updates_this_round = int(
                (
                    await self.session.execute(
                        text(
                            """
                            WITH current_urls AS (
                              SELECT miner_uid, github_url
                              FROM (
                                SELECT
                                  rvm.miner_uid,
                                  NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') AS github_url,
                                  ROW_NUMBER() OVER (
                                    PARTITION BY rvm.miner_uid
                                    ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                                  ) AS rn
                                FROM round_validator_miners rvm
                                WHERE rvm.round_id = :current_round_id
                                  AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                              ) ranked
                              WHERE rn = 1
                            ),
                            previous_urls AS (
                              SELECT miner_uid, github_url
                              FROM (
                                SELECT
                                  rvm.miner_uid,
                                  NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') AS github_url,
                                  ROW_NUMBER() OVER (
                                    PARTITION BY rvm.miner_uid
                                    ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                                  ) AS rn
                                FROM round_validator_miners rvm
                                WHERE rvm.round_id = :previous_round_id
                                  AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                              ) ranked
                              WHERE rn = 1
                            )
                            SELECT COUNT(*)::INTEGER
                            FROM current_urls c
                            JOIN previous_urls p USING (miner_uid)
                            WHERE c.github_url IS DISTINCT FROM p.github_url
                            """
                        ),
                        {
                            "current_round_id": current_round_id,
                            "previous_round_id": previous_round_id,
                        },
                    )
                ).scalar_one()
                or 0
            )

        new_agents_this_round = int(
            (
                await self.session.execute(
                    text(
                        """
                        WITH current_miners AS (
                          SELECT DISTINCT rvm.miner_uid
                          FROM round_validator_miners rvm
                          WHERE rvm.round_id = :current_round_id
                        )
                        SELECT COUNT(*)::INTEGER
                        FROM current_miners cm
                        WHERE NOT EXISTS (
                          SELECT 1
                          FROM round_validator_miners hist
                          JOIN rounds r ON r.round_id = hist.round_id
                          JOIN seasons s ON s.season_id = r.season_id
                          WHERE s.season_number = :season
                            AND r.round_number_in_season < :round
                            AND hist.miner_uid = cm.miner_uid
                        )
                        """
                    ),
                    {
                        "current_round_id": current_round_id,
                        "season": current_season,
                        "round": current_round_in_season,
                    },
                )
            ).scalar_one()
            or 0
        )

        latest_finished_pair = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          s.season_number,
                          r.round_number_in_season,
                          COALESCE(rs.leader_after_reward, 0) AS reward,
                          rs.leader_after_miner_uid,
                          (
                            SELECT COALESCE(NULLIF(TRIM(COALESCE(rvm.name, '')), ''), 'miner ' || rs.leader_after_miner_uid::text)
                            FROM round_validator_miners rvm
                            WHERE rvm.round_id = r.round_id
                              AND rvm.miner_uid = rs.leader_after_miner_uid
                            ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                            LIMIT 1
                          ) AS leader_name
                        FROM round_summary rs
                        JOIN rounds r ON r.round_id = rs.round_id
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE lower(COALESCE(r.status, '')) IN ('finished', 'evaluating_finished')
                        ORDER BY s.season_number DESC, r.round_number_in_season DESC
                        LIMIT 2
                        """
                    )
                )
            )
            .mappings()
            .all()
        )
        reward_delta_from_previous_round = None
        previous_round_leader_name = None
        previous_round_leader_reward = None
        previous_round_label = None
        if len(latest_finished_pair) >= 2:
            reward_delta_from_previous_round = float(latest_finished_pair[0]["reward"] or 0.0) - float(latest_finished_pair[1]["reward"] or 0.0)
            previous_round_leader_name = latest_finished_pair[1].get("leader_name")
            previous_round_leader_reward = float(latest_finished_pair[1]["reward"] or 0.0)
            if latest_finished_pair[1].get("season_number") is not None and latest_finished_pair[1].get("round_number_in_season") is not None:
                previous_round_label = f"Season {int(latest_finished_pair[1]['season_number'])} · Round {int(latest_finished_pair[1]['round_number_in_season'])}"

        if not has_finished_round:
            return {
                "hasFinishedRound": False,
                "leader": None,
                "season": None,
                "round": None,
                "currentSeason": current_season,
                "currentRound": current_round_in_season,
                "currentValidators": int(current_validators or 0),
                "totalMiners": int(current_total_miners or 0),
                "tasksPerValidator": effective_tasks_per_validator,
                "roundDurationMinutes": round_duration_minutes,
                "seasonDurationMinutes": season_duration_minutes,
                "seasonRounds": season_rounds,
                "seasonTaskVolume": season_task_volume,
                "minerUpdatesThisRound": miner_updates_this_round,
                "newAgentsThisRound": new_agents_this_round,
                "rewardDeltaFromPreviousRound": reward_delta_from_previous_round,
                "previousRoundLeaderName": previous_round_leader_name,
                "previousRoundLeaderReward": previous_round_leader_reward,
                "previousRoundLabel": previous_round_label,
                "minerList": [],
                "subnetVersion": subnet_version,
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

        leader = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      s.leader_miner_uid,
                      s.leader_reward,
                      (
                        SELECT rvm.name
                        FROM round_validator_miners rvm
                        JOIN rounds rr ON rr.round_id = rvm.round_id
                        WHERE rvm.miner_uid = s.leader_miner_uid
                          AND rr.season_id = s.season_id
                          AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                        ORDER BY rr.round_number_in_season DESC, rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                        LIMIT 1
                      ) AS leader_name
                    FROM seasons s
                    WHERE s.season_number = :season
                    LIMIT 1
                    """
                    ),
                    {"season": metrics_season},
                )
            )
            .mappings()
            .first()
        )
        if not leader or leader["leader_miner_uid"] is None:
            leader = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          rs.leader_after_miner_uid AS leader_miner_uid,
                          rs.leader_after_reward AS leader_reward,
                          (
                            SELECT rvm.name
                            FROM round_validator_miners rvm
                            WHERE rvm.round_id = rs.round_id
                              AND rvm.miner_uid = rs.leader_after_miner_uid
                              AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                            ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                            LIMIT 1
                          ) AS leader_name
                        FROM round_summary rs
                        JOIN rounds rr ON rr.round_id = rs.round_id
                        JOIN seasons ss ON ss.season_id = rr.season_id
                        ORDER BY ss.season_number DESC, rr.round_number_in_season DESC
                        LIMIT 1
                        """
                        )
                    )
                )
                .mappings()
                .first()
            )

        leader_uid = int(leader["leader_miner_uid"]) if leader and leader["leader_miner_uid"] is not None else None
        leader_row = None
        leader_summary = None
        if leader_uid is None:
            leader_row = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT
                              rvm.round_id,
                              rvm.miner_uid,
                              rvm.miner_hotkey,
                              rvm.image_url,
                              rvm.github_url,
                              COALESCE(NULLIF(TRIM(COALESCE(rvm.name, '')), ''), 'miner ' || rvm.miner_uid::text) AS name,
                              COALESCE(rvm.best_local_reward, rvm.post_consensus_avg_reward, rvm.local_avg_reward, 0) AS reward,
                              COALESCE(rvm.best_local_eval_cost, rvm.post_consensus_avg_eval_cost, rvm.local_avg_eval_cost) AS cost,
                              COALESCE(rvm.best_local_eval_score, rvm.post_consensus_avg_eval_score, rvm.local_avg_eval_score) AS score,
                              COALESCE(rvm.best_local_eval_time, rvm.post_consensus_avg_eval_time, rvm.local_avg_eval_time) AS time,
                              COALESCE(rvm.best_local_tasks_received, rvm.post_consensus_tasks_received, rvm.local_tasks_received, 0) AS tasks_received,
                              COALESCE(rvm.best_local_tasks_success, rvm.post_consensus_tasks_success, rvm.local_tasks_success, 0) AS tasks_success
                            FROM round_validator_miners rvm
                            JOIN rounds r ON r.round_id = rvm.round_id
                            JOIN seasons s ON s.season_id = r.season_id
                            WHERE s.season_number = :season
                            ORDER BY
                              COALESCE(rvm.best_local_reward, rvm.post_consensus_avg_reward, rvm.local_avg_reward, 0) DESC,
                              COALESCE(rvm.best_local_rank, rvm.post_consensus_rank, 9999) ASC,
                              r.round_number_in_season ASC,
                              rvm.updated_at DESC NULLS LAST,
                              rvm.created_at DESC NULLS LAST
                            LIMIT 1
                            """
                        ),
                        {"season": metrics_season},
                    )
                )
                .mappings()
                .first()
            )
            leader_uid = int(leader_row["miner_uid"]) if leader_row and leader_row.get("miner_uid") is not None else None
        if leader_uid is not None:
            if leader_row is None:
                leader_row = (
                    (
                        await self.session.execute(
                            text(
                                """
                                SELECT
                                  rvm.round_id,
                                  rvm.miner_uid,
                                  rvm.miner_hotkey,
                                  rvm.image_url,
                                  rvm.github_url,
                                  COALESCE(NULLIF(TRIM(COALESCE(rvm.name, '')), ''), 'miner ' || rvm.miner_uid::text) AS name,
                                  COALESCE(rvm.best_local_reward, rvm.post_consensus_avg_reward, rvm.local_avg_reward, 0) AS reward,
                                  COALESCE(rvm.best_local_eval_cost, rvm.post_consensus_avg_eval_cost, rvm.local_avg_eval_cost) AS cost,
                                  COALESCE(rvm.best_local_eval_score, rvm.post_consensus_avg_eval_score, rvm.local_avg_eval_score) AS score,
                                  COALESCE(rvm.best_local_eval_time, rvm.post_consensus_avg_eval_time, rvm.local_avg_eval_time) AS time,
                                  COALESCE(rvm.best_local_tasks_received, rvm.post_consensus_tasks_received, rvm.local_tasks_received, 0) AS tasks_received,
                                  COALESCE(rvm.best_local_tasks_success, rvm.post_consensus_tasks_success, rvm.local_tasks_success, 0) AS tasks_success
                                FROM round_validator_miners rvm
                                JOIN rounds r ON r.round_id = rvm.round_id
                                JOIN seasons s ON s.season_id = r.season_id
                                WHERE s.season_number = :season
                                  AND rvm.miner_uid = :miner_uid
                                ORDER BY
                                  COALESCE(rvm.best_local_reward, rvm.post_consensus_avg_reward, rvm.local_avg_reward, 0) DESC,
                                  COALESCE(rvm.best_local_rank, rvm.post_consensus_rank, 9999) ASC,
                                  r.round_number_in_season ASC,
                                  rvm.updated_at DESC NULLS LAST,
                                  rvm.created_at DESC NULLS LAST
                                LIMIT 1
                                """
                            ),
                            {"season": metrics_season, "miner_uid": leader_uid},
                        )
                    )
                    .mappings()
                    .first()
                )
            leader_summary = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT
                              rs.leader_after_reward,
                              rs.leader_after_eval_score,
                              rs.leader_after_eval_time,
                              rs.leader_after_eval_cost,
                              (rs.post_consensus_json->'summary'->'leader_after_round'->>'score')::DOUBLE PRECISION AS leader_after_eval_score_json,
                              (rs.post_consensus_json->'summary'->'leader_after_round'->>'time')::DOUBLE PRECISION AS leader_after_eval_time_json,
                              (rs.post_consensus_json->'summary'->'leader_after_round'->>'cost')::DOUBLE PRECISION AS leader_after_eval_cost_json
                            FROM round_summary rs
                            JOIN rounds r ON r.round_id = rs.round_id
                            JOIN seasons s ON s.season_id = r.season_id
                            WHERE s.season_number = :season
                              AND rs.leader_after_miner_uid = :miner_uid
                            ORDER BY r.round_number_in_season DESC, rs.updated_at DESC NULLS LAST
                            LIMIT 1
                            """
                        ),
                        {"season": metrics_season, "miner_uid": leader_uid},
                    )
                )
                .mappings()
                .first()
            )

        total_validators = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT rv.validator_uid)
                    FROM round_validators rv
                    JOIN rounds r ON r.round_id = rv.round_id
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season
                    """
                ),
                {"season": metrics_season},
            )
        ).scalar_one()

        total_websites = 0
        leader_round_id = int(leader_row["round_id"]) if leader_row and leader_row["round_id"] is not None else None
        if leader_round_id is not None:
            total_websites = int(
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT COUNT(DISTINCT t.web_project_id)
                            FROM tasks t
                            WHERE t.round_validator_id IN (
                              SELECT rv.round_validator_id
                              FROM round_validators rv
                              WHERE rv.round_id = :round_id
                            )
                            """
                        ),
                        {"round_id": leader_round_id},
                    )
                ).scalar_one()
                or 0
            )
        miners = (
            (
                await self.session.execute(
                    text(
                        """
                        WITH season_rows AS (
                          SELECT
                            rvm.miner_uid AS uid,
                            COALESCE(NULLIF(TRIM(COALESCE(rvm.name, '')), ''), 'miner '||rvm.miner_uid::text) AS name,
                            COALESCE(
                              rvm.best_local_reward,
                              rvm.post_consensus_avg_reward,
                              rvm.local_avg_reward,
                              0
                            ) AS best_local_reward,
                            COALESCE(
                              rvm.best_local_rank,
                              rvm.post_consensus_rank,
                              9999
                            ) AS best_local_rank,
                            COALESCE(
                              rvm.best_local_tasks_received,
                              rvm.post_consensus_tasks_received,
                              rvm.local_tasks_received,
                              0
                            ) AS tasks_received,
                            r.round_number_in_season
                          FROM round_validator_miners rvm
                          JOIN rounds r ON r.round_id = rvm.round_id
                          JOIN seasons s ON s.season_id = r.season_id
                          WHERE s.season_number = :season
                            AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                            AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                        ),
                        best_rows AS (
                          SELECT DISTINCT ON (uid)
                            uid,
                            name,
                            tasks_received,
                            best_local_reward,
                            best_local_rank,
                            round_number_in_season
                          FROM season_rows
                          ORDER BY uid, best_local_reward DESC, best_local_rank ASC, round_number_in_season ASC
                        )
                        SELECT uid, name
                        FROM best_rows
                        WHERE tasks_received > 0
                        ORDER BY best_local_reward DESC, best_local_rank ASC, uid ASC
                        """
                    ),
                    {"season": metrics_season},
                )
            )
            .mappings()
            .all()
        )
        tasks_per_validator = (
            await self.session.execute(
                text(
                    """
                    SELECT COALESCE(MAX(cnt),0)
                    FROM (
                      SELECT round_validator_id, COUNT(*) AS cnt
                      FROM tasks
                      WHERE round_validator_id IN (SELECT round_validator_id FROM round_validators WHERE round_id=:rid)
                      GROUP BY round_validator_id
                    ) x
                    """
                ),
                {"rid": metrics_round_id},
            )
        ).scalar_one()
        leader_payload = None
        if leader_uid is not None:
            leader_name = leader_row["name"] if leader_row and leader_row.get("name") else (leader["leader_name"] if leader else None)
            leader_reward = (
                float(leader_summary["leader_after_reward"])
                if leader_summary and leader_summary.get("leader_after_reward") is not None
                else float(leader_row["reward"])
                if leader_row and leader_row.get("reward") is not None
                else float(leader["leader_reward"] or 0.0)
                if leader
                else 0.0
            )
            leader_cost = (
                float(leader_summary["leader_after_eval_cost"])
                if leader_summary and leader_summary.get("leader_after_eval_cost") is not None
                else float(leader_summary["leader_after_eval_cost_json"])
                if leader_summary and leader_summary.get("leader_after_eval_cost_json") is not None
                else float(leader_row["cost"])
                if leader_row and leader_row.get("cost") is not None
                else None
            )
            leader_score = (
                float(leader_summary["leader_after_eval_score"])
                if leader_summary and leader_summary.get("leader_after_eval_score") is not None
                else float(leader_summary["leader_after_eval_score_json"])
                if leader_summary and leader_summary.get("leader_after_eval_score_json") is not None
                else float(leader_row["score"])
                if leader_row and leader_row.get("score") is not None
                else None
            )
            leader_time = (
                float(leader_summary["leader_after_eval_time"])
                if leader_summary and leader_summary.get("leader_after_eval_time") is not None
                else float(leader_summary["leader_after_eval_time_json"])
                if leader_summary and leader_summary.get("leader_after_eval_time_json") is not None
                else float(leader_row["time"])
                if leader_row and leader_row.get("time") is not None
                else None
            )
            leader_payload = {
                "minerUid": leader_uid,
                "minerHotkey": (leader_row["miner_hotkey"] if leader_row else None),
                "minerImage": (leader_row["image_url"] if leader_row else None),
                "minerGithubUrl": (leader_row["github_url"] if leader_row else None),
                "minerName": leader_name,
                "reward": leader_reward,
                "cost": leader_cost,
                "score": leader_score,
                "time": leader_time,
                "validators": int(total_validators or 0),
                "totalWebsitesEvaluated": total_websites,
                "tasksReceived": int(leader_row["tasks_received"] or 0) if leader_row else 0,
                "tasksSuccess": int(leader_row["tasks_success"] or 0) if leader_row else 0,
            }
        return {
            "hasFinishedRound": True,
            "leader": leader_payload,
            "season": metrics_season,
            "round": metrics_round_in_season,
            "currentSeason": current_season,
            "currentRound": current_round_in_season,
            "currentValidators": int(current_validators or 0),
            "totalMiners": len(miners),
            "tasksPerValidator": int(tasks_per_validator or effective_tasks_per_validator or 0),
            "roundDurationMinutes": round_duration_minutes,
            "seasonDurationMinutes": season_duration_minutes,
            "seasonRounds": season_rounds,
            "seasonTaskVolume": season_task_volume,
            "minerUpdatesThisRound": miner_updates_this_round,
            "newAgentsThisRound": new_agents_this_round,
            "rewardDeltaFromPreviousRound": reward_delta_from_previous_round,
            "previousRoundLeaderName": previous_round_leader_name,
            "previousRoundLeaderReward": previous_round_leader_reward,
            "previousRoundLabel": previous_round_label,
            "minerList": [dict(m) for m in miners],
            "subnetVersion": subnet_version,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }

    async def get_overview_validators_list(
        self,
        page: int,
        limit: int,
        status: Optional[str],
        sort_by: str,
        sort_order: str,
    ) -> Tuple[List[Dict[str, Any]], int]:
        current_round = await self.get_current_round()
        current_season_number = None
        current_round_in_season = None
        if isinstance(current_round, dict):
            try:
                current_season_number = int(current_round.get("season"))
            except Exception:
                current_season_number = None
            try:
                current_round_in_season = int(current_round.get("round"))
            except Exception:
                current_round_in_season = None

        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT rv.round_validator_id, rv.validator_uid, rv.validator_hotkey, rv.name, rv.image_url,
                           rv.version, rv.stake, rv.vtrust, rv.started_at, rv.finished_at,
                           rr.round_number_in_season, s.season_number, rr.status AS round_status
                    FROM round_validators rv
                    JOIN rounds rr ON rr.round_id = rv.round_id
                    JOIN seasons s ON s.season_id = rr.season_id
                    ORDER BY
                        CASE WHEN lower(coalesce(rr.status,''))='active' THEN 0 ELSE 1 END,
                        rv.started_at DESC NULLS LAST,
                        rv.finished_at DESC NULLS LAST
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        by_validator: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            uid = int(r["validator_uid"])
            if uid in by_validator:
                continue
            normalized_stake = _normalize_stake_to_rao(r.get("stake"))
            if normalized_stake <= 0:
                try:
                    fresh_data = get_validator_data(uid=uid)
                except MetagraphError:
                    fresh_data = None
                if fresh_data and fresh_data.get("stake") is not None:
                    normalized_stake = _normalize_stake_to_rao(fresh_data.get("stake"))
            total_tasks = (
                await self.session.execute(
                    text("SELECT COUNT(*) FROM tasks WHERE round_validator_id=:rvid"),
                    {"rvid": int(r["round_validator_id"])},
                )
            ).scalar_one()

            task_ctx = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT web_project_id, use_case
                            FROM tasks
                            WHERE round_validator_id=:rvid
                            ORDER BY id DESC
                            LIMIT 1
                            """
                        ),
                        {"rvid": int(r["round_validator_id"])},
                    )
                )
                .mappings()
                .first()
            )
            website = None
            use_case = None
            if task_ctx:
                website = task_ctx.get("web_project_id")
                uc = task_ctx.get("use_case")
                if isinstance(uc, dict):
                    use_case = uc.get("name") or uc.get("event")
            round_status = str(r.get("round_status") or "").lower()
            is_active = round_status == "active"
            is_current_round = (
                current_season_number is not None
                and current_round_in_season is not None
                and int(r["season_number"]) == current_season_number
                and int(r["round_number_in_season"]) == current_round_in_season
            )
            if is_active:
                status_label = "Evaluating"
            elif is_current_round:
                status_label = "Waiting"
            else:
                status_label = "Inactive"
            current_task = f"Round {int(r['round_number_in_season'])}" if is_active else "Idle"

            item = {
                "id": f"validator-{uid}",
                "validatorUid": uid,
                "name": r["name"] or f"Validator {uid}",
                "hotkey": r["validator_hotkey"] or "",
                "icon": r["image_url"] or "/validators/Other.png",
                "currentTask": current_task,
                "currentWebsite": website,
                "currentUseCase": use_case,
                "status": status_label,
                "totalTasks": int(total_tasks or 0),
                "weight": 1.0,
                "trust": float(r["vtrust"] or 0.0),
                "version": r["version"],
                "lastSeen": (r["finished_at"] or r["started_at"] or datetime.now(timezone.utc)).isoformat(),
                "uptime": 1.0,
                "stake": normalized_stake,
                "emission": 0,
                "validatorRoundId": f"validator_round_{int(r['round_validator_id'])}",
                "roundNumber": int(r["season_number"]) * 10000 + int(r["round_number_in_season"]),
                "lastSeenSeason": int(r["season_number"]),
                "lastSeenRoundInSeason": int(r["round_number_in_season"]),
                "lastRoundWinner": None,
            }
            by_validator[uid] = item
        items = list(by_validator.values())
        if status:
            items = [v for v in items if str(v.get("status", "")).lower() == str(status).lower()]
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "weight": lambda x: float(x.get("weight") or 0),
            "trust": lambda x: float(x.get("trust") or 0),
            "stake": lambda x: float(x.get("stake") or 0),
            "name": lambda x: str(x.get("name") or "").lower(),
            "lastSeen": lambda x: str(x.get("lastSeen") or ""),
        }
        sort_key = key_map.get(sort_by, key_map["weight"])
        items = sorted(items, key=sort_key, reverse=reverse)
        total = len(items)
        offset = (page - 1) * limit
        return items[offset : offset + limit], total

    async def get_overview_validators_filter(self) -> List[Dict[str, Any]]:
        validators, _ = await self.get_overview_validators_list(1, 500, None, "name", "asc")
        return [
            {
                "id": v["id"],
                "name": v["name"],
                "hotkey": v.get("hotkey"),
                "icon": v.get("icon"),
                "status": v.get("status"),
            }
            for v in validators
        ]

    async def get_overview_validator_detail(self, validator_id: str) -> Dict[str, Any]:
        validators, _ = await self.get_overview_validators_list(1, 1000, None, "lastSeen", "desc")
        target = None
        for v in validators:
            if validator_id == v["id"] or validator_id == str(v["validatorUid"]):
                target = v
                break
        if target is None:
            raise ValueError(f"Validator {validator_id} not found")
        return target

    async def get_overview_current_round(self) -> Optional[Dict[str, Any]]:
        return await self.get_current_round()

    async def get_overview_rounds_list(self, page: int, limit: int, status: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], int]:
        rounds, total = await self.get_rounds_list(page=page, limit=limit)
        if status:
            rounds = [r for r in rounds if str(r.get("status", "")).lower() == str(status).lower()]
            total = len(rounds)
        current = await self.get_current_round()
        return rounds, current, total

    async def get_overview_round_detail(self, validator_round_id: str) -> Dict[str, Any]:
        raw = str(validator_round_id).strip()
        if raw.startswith("validator_round_"):
            tail = raw.split("_")[-1]
            if tail.isdigit():
                row = (
                    await self.session.execute(
                        text("SELECT round_id FROM round_validators WHERE round_validator_id=:rvid LIMIT 1"),
                        {"rvid": int(tail)},
                    )
                ).scalar_one_or_none()
                if row is not None:
                    return await self.get_round_detail_by_round_id(int(row))
        return await self._round_detail_from_overview(raw)

    async def _round_detail_from_overview(self, raw: str) -> Dict[str, Any]:
        if "/" in raw:
            season_s, round_s = raw.split("/", 1)
            return await self.get_round_detail(int(season_s), int(round_s))
        parsed = int(raw)
        if parsed >= 10000 and (parsed % 10000) > 0:
            return await self.get_round_detail(parsed // 10000, parsed % 10000)
        return await self.get_round_detail_by_round_id(parsed)

    async def get_overview_leaderboard(self, limit: Optional[int]) -> Tuple[List[Dict[str, Any]], str]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                           s.season_number,
                           r.round_number_in_season,
                           r.ended_at,
                           rs.leader_after_miner_uid,
                           rs.leader_after_reward,
                           (
                             SELECT rvm.name
                             FROM round_validator_miners rvm
                             WHERE rvm.round_id = rs.round_id
                               AND rvm.miner_uid = rs.leader_after_miner_uid
                               AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                             ORDER BY rvm.updated_at DESC NULLS LAST, rvm.created_at DESC NULLS LAST
                             LIMIT 1
                           ) AS leader_name
                    FROM round_summary rs
                    JOIN rounds r ON r.round_id = rs.round_id
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE r.consensus_status = 'finalized'
                      AND rs.leader_after_miner_uid IS NOT NULL
                    ORDER BY s.season_number DESC, r.round_number_in_season DESC
                    LIMIT :lim
                    """
                    ),
                    {"lim": int(limit or 30)},
                )
            )
            .mappings()
            .all()
        )
        entries = []
        for r in rows:
            entries.append(
                {
                    "round": int(r["round_number_in_season"]),
                    "season": int(r["season_number"]),
                    "subnet36": float(r["leader_after_reward"] or 0.0),
                    "post_consensus_reward": float(r["leader_after_reward"] or 0.0),
                    "reward": float(r["leader_after_reward"] or 0.0),
                    "winnerUid": int(r["leader_after_miner_uid"]) if r["leader_after_miner_uid"] is not None else None,
                    "winnerName": r.get("leader_name"),
                    "timestamp": (r["ended_at"] or datetime.now(timezone.utc)).isoformat(),
                    "post_consensus_eval_score": None,
                    "post_consensus_eval_time": 0.0,
                    "time": 0.0,
                }
            )
        return entries, "all"

    async def get_overview_statistics(self) -> Dict[str, Any]:
        total_validators = (await self.session.execute(text("SELECT COUNT(DISTINCT validator_uid) FROM round_validators"))).scalar_one()
        total_miners = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT miner_uid)
                    FROM round_validator_miners
                    WHERE NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                    """
                )
            )
        ).scalar_one()
        total_tasks = (await self.session.execute(text("SELECT COUNT(*) FROM tasks"))).scalar_one()
        avg_score = (
            await self.session.execute(
                text(
                    """
                    SELECT COALESCE(AVG(post_consensus_avg_reward),0)
                    FROM round_validator_miners
                    WHERE NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                    """
                )
            )
        ).scalar_one()
        avg_trust = (await self.session.execute(text("SELECT COALESCE(AVG(vtrust),0) FROM round_validators"))).scalar_one()
        total_stake = (
            await self.session.execute(
                text("SELECT COALESCE(SUM(stake),0) FROM (SELECT DISTINCT ON (validator_uid) stake FROM round_validators ORDER BY validator_uid, finished_at DESC NULLS LAST) x")
            )
        ).scalar_one()
        return {
            "totalStake": int(float(total_stake or 0.0)),
            "totalEmission": 0,
            "averageTrust": float(avg_trust or 0.0),
            "networkUptime": 1.0,
            "activeValidators": int(total_validators or 0),
            "registeredMiners": int(total_miners or 0),
            "totalTasksCompleted": int(total_tasks or 0),
            "averageTaskScore": float(avg_score or 0.0),
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }

    async def get_overview_network_status(self) -> Dict[str, Any]:
        validators, _ = await self.get_overview_validators_list(1, 1000, None, "lastSeen", "desc")
        latest_round = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT s.season_number, r.round_number_in_season
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        ORDER BY s.season_number DESC, r.round_number_in_season DESC
                        LIMIT 1
                        """
                    )
                )
            )
            .mappings()
            .first()
        )
        return {
            "status": "healthy",
            "message": "Network operational",
            "lastChecked": datetime.now(timezone.utc).isoformat(),
            "activeValidators": len(validators),
            "networkLatency": 0,
            "season": int(latest_round["season_number"]) if latest_round and latest_round.get("season_number") is not None else None,
            "round": int(latest_round["round_number_in_season"]) if latest_round and latest_round.get("round_number_in_season") is not None else None,
        }

    async def get_overview_recent_activity(self, limit: int) -> List[Dict[str, Any]]:
        lim = max(limit, 1)

        season_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          season_id,
                          season_number,
                          status,
                          start_at,
                          end_at,
                          created_at,
                          updated_at
                        FROM seasons
                        ORDER BY COALESCE(end_at, start_at, updated_at, created_at) DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        round_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          r.round_id,
                          s.season_number,
                          r.round_number_in_season,
                          r.status,
                          r.consensus_status,
                          r.started_at,
                          r.ended_at,
                          r.created_at,
                          r.updated_at
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        ORDER BY COALESCE(r.ended_at, r.started_at, r.updated_at, r.created_at) DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        started_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          mer.agent_run_id,
                          mer.created_at AS timestamp,
                          rv.validator_round_id,
                          rv.validator_uid,
                          COALESCE(rv.name, 'validator ' || rv.validator_uid::text) AS validator_name,
                          mer.miner_uid,
                          COALESCE(rvm.name, 'miner ' || mer.miner_uid::text) AS miner_name,
                          s.season_number,
                          r.round_number_in_season
                        FROM miner_evaluation_runs mer
                        LEFT JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                        LEFT JOIN rounds r ON r.round_id = rv.round_id
                        LEFT JOIN seasons s ON s.season_id = r.season_id
                        LEFT JOIN round_validator_miners rvm
                          ON rvm.round_validator_id = mer.round_validator_id
                         AND rvm.miner_uid = mer.miner_uid
                        ORDER BY mer.created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        finished_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          mer.agent_run_id,
                          mer.updated_at AS timestamp,
                          rv.validator_round_id,
                          rv.validator_uid,
                          COALESCE(rv.name, 'validator ' || rv.validator_uid::text) AS validator_name,
                          mer.miner_uid,
                          COALESCE(rvm.name, 'miner ' || mer.miner_uid::text) AS miner_name,
                          s.season_number,
                          r.round_number_in_season,
                          COALESCE(rvm.local_avg_reward, rvm.best_local_reward, mer.average_reward) AS local_reward,
                          COALESCE(NULLIF(rvm.local_avg_eval_score, 0), NULLIF(rvm.best_local_eval_score, 0), mer.average_score) AS local_score,
                          COALESCE(rvm.local_avg_eval_time, rvm.best_local_eval_time, mer.average_execution_time) AS local_time,
                          COALESCE(
                            NULLIF(rvm.local_avg_eval_cost, 0),
                            NULLIF(rvm.best_local_eval_cost, 0),
                            (
                              SELECT AVG(eval_total_cost)
                              FROM (
                                SELECT e.evaluation_id, SUM(COALESCE(elu.cost, 0)) AS eval_total_cost
                                FROM evaluations e
                                JOIN evaluation_llm_usage elu ON elu.evaluation_id = e.evaluation_id
                                WHERE e.agent_run_id = mer.agent_run_id
                                GROUP BY e.evaluation_id
                              ) c
                            )
                          ) AS local_cost,
                          CASE
                            WHEN rvm.local_avg_reward IS NOT NULL
                              OR rvm.local_avg_eval_score IS NOT NULL
                              OR rvm.local_avg_eval_time IS NOT NULL
                              OR rvm.local_avg_eval_cost IS NOT NULL
                              THEN 'local'
                            WHEN rvm.best_local_reward IS NOT NULL
                              OR rvm.best_local_eval_score IS NOT NULL
                              OR rvm.best_local_eval_time IS NOT NULL
                              OR rvm.best_local_eval_cost IS NOT NULL
                              THEN 'best_local'
                            ELSE 'run'
                          END AS metric_source,
                          mer.success_tasks,
                          mer.failed_tasks,
                          mer.total_tasks
                        FROM miner_evaluation_runs mer
                        LEFT JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                        LEFT JOIN rounds r ON r.round_id = rv.round_id
                        LEFT JOIN seasons s ON s.season_id = r.season_id
                        LEFT JOIN round_validator_miners rvm
                          ON rvm.round_validator_id = mer.round_validator_id
                         AND rvm.miner_uid = mer.miner_uid
                        WHERE mer.ended_at IS NOT NULL
                        ORDER BY mer.updated_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        uploaded_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          rv.validator_round_id,
                          rv.updated_at AS timestamp,
                          rv.validator_uid,
                          COALESCE(rv.name, 'validator ' || rv.validator_uid::text) AS validator_name,
                          s.season_number,
                          r.round_number_in_season
                        FROM round_validators rv
                        LEFT JOIN rounds r ON r.round_id = rv.round_id
                        LEFT JOIN seasons s ON s.season_id = r.season_id
                        WHERE rv.ipfs_uploaded IS NOT NULL
                        ORDER BY rv.updated_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        consensus_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          rv.validator_round_id,
                          rv.updated_at AS timestamp,
                          rv.validator_uid,
                          COALESCE(rv.name, 'validator ' || rv.validator_uid::text) AS validator_name,
                          s.season_number,
                          r.round_number_in_season
                        FROM round_validators rv
                        LEFT JOIN rounds r ON r.round_id = rv.round_id
                        LEFT JOIN seasons s ON s.season_id = r.season_id
                        WHERE rv.post_consensus_json IS NOT NULL OR rv.ipfs_downloaded IS NOT NULL
                        ORDER BY rv.updated_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        leader_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          rs.round_summary_id,
                          rs.created_at AS timestamp,
                          rs.leader_after_miner_uid,
                          COALESCE(rvm.name, 'miner ' || rs.leader_after_miner_uid::text) AS miner_name,
                          rs.leader_after_reward,
                          s.season_number,
                          r.round_number_in_season
                        FROM round_summary rs
                        JOIN rounds r ON r.round_id = rs.round_id
                        JOIN seasons s ON s.season_id = r.season_id
                        LEFT JOIN round_validator_miners rvm
                          ON rvm.round_id = rs.round_id
                         AND rvm.miner_uid = rs.leader_after_miner_uid
                        ORDER BY rs.created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"lim": lim},
                )
            )
            .mappings()
            .all()
        )

        acts: List[Dict[str, Any]] = []

        for row in season_rows:
            started_at = row.get("start_at") or row.get("created_at")
            if started_at:
                acts.append(
                    {
                        "id": f"season-start-{row['season_id']}",
                        "type": "season_started",
                        "message": f"Season {int(row['season_number'])} opened",
                        "timestamp": started_at.isoformat(),
                        "metadata": {
                            "seasonNumber": row.get("season_number"),
                        },
                    }
                )
            ended_at = row.get("end_at")
            if ended_at:
                acts.append(
                    {
                        "id": f"season-end-{row['season_id']}",
                        "type": "season_finished",
                        "message": f"Season {int(row['season_number'])} closed",
                        "timestamp": ended_at.isoformat(),
                        "metadata": {
                            "seasonNumber": row.get("season_number"),
                        },
                    }
                )

        for row in round_rows:
            started_at = row.get("started_at") or row.get("created_at")
            if started_at:
                acts.append(
                    {
                        "id": f"round-start-{row['round_id']}",
                        "type": "round_started",
                        "message": (f"Season {int(row['season_number'])} · Round {int(row['round_number_in_season'])} started"),
                        "timestamp": started_at.isoformat(),
                        "metadata": {
                            "seasonNumber": row.get("season_number"),
                            "roundNumber": row.get("round_number_in_season"),
                        },
                    }
                )
            ended_at = row.get("ended_at")
            if ended_at:
                acts.append(
                    {
                        "id": f"round-end-{row['round_id']}",
                        "type": "round_ended",
                        "message": (f"Season {int(row['season_number'])} · Round {int(row['round_number_in_season'])} closed"),
                        "timestamp": ended_at.isoformat(),
                        "metadata": {
                            "seasonNumber": row.get("season_number"),
                            "roundNumber": row.get("round_number_in_season"),
                        },
                    }
                )

        for row in started_rows:
            if not row.get("timestamp"):
                continue
            validator_name = row.get("validator_name") or "Validator"
            miner_name = row.get("miner_name") or "Miner"
            acts.append(
                {
                    "id": f"run-start-{row['agent_run_id']}",
                    "type": "evaluation_started",
                    "message": f"{validator_name} started evaluating {miner_name}",
                    "timestamp": row["timestamp"].isoformat(),
                    "metadata": {
                        "validatorId": row.get("validator_round_id"),
                        "validatorUid": row.get("validator_uid"),
                        "validatorName": validator_name,
                        "minerUid": row.get("miner_uid"),
                        "minerName": miner_name,
                        "seasonNumber": row.get("season_number"),
                        "roundNumber": row.get("round_number_in_season"),
                    },
                }
            )

        for row in finished_rows:
            if not row.get("timestamp"):
                continue
            validator_name = row.get("validator_name") or "Validator"
            miner_name = row.get("miner_name") or "Miner"
            reward = row.get("local_reward")
            score = row.get("local_score")
            eval_time = row.get("local_time")
            cost = row.get("local_cost")
            acts.append(
                {
                    "id": f"run-finish-{row['agent_run_id']}",
                    "type": "evaluation_finished",
                    "message": f"{validator_name} finished evaluating {miner_name}",
                    "timestamp": row["timestamp"].isoformat(),
                    "metadata": {
                        "validatorId": row.get("validator_round_id"),
                        "validatorUid": row.get("validator_uid"),
                        "validatorName": validator_name,
                        "minerUid": row.get("miner_uid"),
                        "minerName": miner_name,
                        "seasonNumber": row.get("season_number"),
                        "roundNumber": row.get("round_number_in_season"),
                        "reward": float(reward) if reward is not None else None,
                        "score": float(score) if score is not None else None,
                        "time": float(eval_time) if eval_time is not None else None,
                        "cost": float(cost) if cost is not None else None,
                        "metricSource": row.get("metric_source"),
                    },
                }
            )

        for row in uploaded_rows:
            if not row.get("timestamp"):
                continue
            validator_name = row.get("validator_name") or "Validator"
            acts.append(
                {
                    "id": f"snapshot-upload-{row['validator_round_id']}",
                    "type": "consensus_waiting",
                    "message": f"{validator_name} uploaded a snapshot and is waiting for consensus",
                    "timestamp": row["timestamp"].isoformat(),
                    "metadata": {
                        "validatorId": row.get("validator_round_id"),
                        "validatorUid": row.get("validator_uid"),
                        "validatorName": validator_name,
                        "seasonNumber": row.get("season_number"),
                        "roundNumber": row.get("round_number_in_season"),
                    },
                }
            )

        for row in consensus_rows:
            if not row.get("timestamp"):
                continue
            validator_name = row.get("validator_name") or "Validator"
            acts.append(
                {
                    "id": f"consensus-entered-{row['validator_round_id']}",
                    "type": "consensus_entered",
                    "message": f"{validator_name} entered consensus",
                    "timestamp": row["timestamp"].isoformat(),
                    "metadata": {
                        "validatorId": row.get("validator_round_id"),
                        "validatorUid": row.get("validator_uid"),
                        "validatorName": validator_name,
                        "seasonNumber": row.get("season_number"),
                        "roundNumber": row.get("round_number_in_season"),
                    },
                }
            )

        for row in leader_rows:
            if not row.get("timestamp"):
                continue
            miner_name = row.get("miner_name") or "Miner"
            reward = row.get("leader_after_reward")
            acts.append(
                {
                    "id": f"leader-{row['round_summary_id']}",
                    "type": "leader_confirmed",
                    "message": (f"{miner_name} closed the round as season leader" + (f" · {(float(reward) * 100 if abs(float(reward)) <= 1 else float(reward)):.1f}%" if reward is not None else "")),
                    "timestamp": row["timestamp"].isoformat(),
                    "metadata": {
                        "minerUid": row.get("leader_after_miner_uid"),
                        "minerName": miner_name,
                        "seasonNumber": row.get("season_number"),
                        "roundNumber": row.get("round_number_in_season"),
                        "reward": float(reward) if reward is not None else None,
                    },
                }
            )

        acts = sorted(acts, key=lambda a: a["timestamp"], reverse=True)
        deduped: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in acts:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            deduped.append(item)
            if len(deduped) >= lim:
                break
        return deduped

    async def get_overview_performance_trends(self, days: int) -> List[Dict[str, Any]]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT DATE(created_at) AS day, COUNT(*) AS total_tasks
                    FROM tasks
                    WHERE created_at >= NOW() - (:days || ' days')::interval
                    GROUP BY DATE(created_at)
                    ORDER BY day ASC
                    """
                    ),
                    {"days": int(days)},
                )
            )
            .mappings()
            .all()
        )
        validators, _ = await self.get_overview_validators_list(1, 1000, None, "name", "asc")
        active_validators = len(validators)
        return [
            {
                "date": str(r["day"]),
                "totalTasks": int(r["total_tasks"] or 0),
                "activeValidators": active_validators,
            }
            for r in rows
        ]
