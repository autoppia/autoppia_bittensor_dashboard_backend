from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text

from app.config import settings
from app.services.media_storage import build_public_url


class UIAgentsRunsServiceMixin:
    @staticmethod
    def _derive_agent_run_status(*, ended_at: Any, zero_reason: Any, total_tasks: int, successful_tasks: int) -> str:
        zero_reason_text = str(zero_reason or "").strip().lower()
        if zero_reason_text == "task_timeout":
            return "timeout"
        if zero_reason_text:
            return "failed"
        if ended_at is None:
            return "running"
        if successful_tasks == 0 and total_tasks > 0:
            return "failed"
        return "completed"

    async def get_agent_detail(self, miner_uid: int, season: Optional[int], round_in_season: Optional[int]) -> Dict[str, Any]:
        requested_round_in_season = round_in_season
        if season is None:
            season = await self.get_latest_season_number()
        if season is None:
            raise ValueError(f"Agent {miner_uid} not found")
        main_validator_uid = await self._get_main_validator_uid()

        if round_in_season is None:
            best_round_ref = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          s.season_number,
                          r.round_number_in_season
                        FROM round_validator_miners rvm
                        JOIN rounds r ON r.round_id = rvm.round_id
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE rvm.miner_uid = :miner_uid
                          AND s.season_number = :season
                        ORDER BY
                          CASE
                            WHEN rvm.post_consensus_avg_reward IS NOT NULL
                              OR rvm.post_consensus_rank IS NOT NULL
                              OR rvm.post_consensus_tasks_received IS NOT NULL
                            THEN 0
                            ELSE 1
                          END ASC,
                          COALESCE(rvm.post_consensus_avg_reward, 0) DESC,
                          COALESCE(rvm.post_consensus_rank, 9999) ASC,
                          r.round_number_in_season ASC,
                          r.round_id ASC
                        LIMIT 1
                        """
                        ),
                        {
                            "miner_uid": miner_uid,
                            "season": season,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if best_round_ref:
                round_in_season = int(best_round_ref["round_number_in_season"])
        if season is None or round_in_season is None:
            raise ValueError(f"Agent {miner_uid} not found")

        season_round_rows = (
            (
                await self.session.execute(
                    text(
                        """
                    WITH ranked_rounds AS (
                      SELECT
                        s.season_number,
                        r.round_number_in_season,
                        COALESCE(rvm.post_consensus_avg_reward, 0) AS reward,
                        COALESCE(rvm.post_consensus_rank, 9999) AS rank,
                        COALESCE(rvm.post_consensus_avg_eval_score, 0) AS eval_score,
                        COALESCE(rvm.post_consensus_avg_eval_time, 0) AS eval_time,
                        rvm.post_consensus_avg_eval_cost AS eval_cost,
                        COALESCE(rvm.post_consensus_tasks_received, 0) AS tasks_received,
                        COALESCE(rvm.post_consensus_tasks_success, 0) AS tasks_success,
                        rs.leader_after_reward AS top_reward,
                        ROW_NUMBER() OVER (
                          PARTITION BY r.round_id
                          ORDER BY
                            COALESCE(rvm.post_consensus_avg_reward, 0) DESC,
                            COALESCE(rvm.post_consensus_rank, 9999) ASC,
                            rvm.round_validator_id ASC
                        ) AS row_num
                      FROM round_validator_miners rvm
                      JOIN rounds r ON r.round_id = rvm.round_id
                      JOIN seasons s ON s.season_id = r.season_id
                      LEFT JOIN round_summary rs ON rs.round_id = r.round_id
                      WHERE rvm.miner_uid = :miner_uid
                        AND s.season_number = :season
                    )
                    SELECT
                      season_number,
                      round_number_in_season,
                      reward,
                      rank,
                      eval_score,
                      eval_time,
                      eval_cost,
                      tasks_received,
                      tasks_success,
                      top_reward
                    FROM ranked_rounds
                    WHERE row_num = 1
                    ORDER BY round_number_in_season DESC
                    """
                    ),
                    {
                        "miner_uid": miner_uid,
                        "season": season,
                    },
                )
            )
            .mappings()
            .all()
        )
        if not season_round_rows:
            raise ValueError(f"Agent {miner_uid} not found")

        season_rank_row = (
            (
                await self.session.execute(
                    text(
                        """
                    WITH season_rows AS (
                      SELECT
                        rvm.miner_uid AS uid,
                        COALESCE(rvm.post_consensus_avg_reward, 0) AS best_reward,
                        COALESCE(rvm.post_consensus_rank, 9999) AS best_rank,
                        r.round_number_in_season AS round_number
                      FROM round_validator_miners rvm
                      JOIN rounds r ON r.round_id = rvm.round_id
                      JOIN seasons s ON s.season_id = r.season_id
                      WHERE s.season_number = :season
                        AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                        AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                        AND (
                          rvm.post_consensus_avg_reward IS NOT NULL
                          OR rvm.post_consensus_rank IS NOT NULL
                        )
                    ),
                    best_rows AS (
                      SELECT DISTINCT ON (uid)
                        uid,
                        best_reward,
                        best_rank,
                        round_number
                      FROM season_rows
                      ORDER BY uid, best_reward DESC, best_rank ASC, round_number ASC
                    ),
                    ranked AS (
                      SELECT
                        uid,
                        best_reward,
                        round_number,
                        ROW_NUMBER() OVER (
                          ORDER BY best_reward DESC, best_rank ASC, uid ASC
                        ) AS season_rank
                      FROM best_rows
                    )
                    SELECT season_rank
                    FROM ranked
                    WHERE uid = :miner_uid
                    LIMIT 1
                    """
                    ),
                    {
                        "miner_uid": miner_uid,
                        "season": season,
                    },
                )
            )
            .mappings()
            .first()
        )
        season_rank = int(season_rank_row["season_rank"]) if season_rank_row and season_rank_row.get("season_rank") is not None else None

        best_round_history_row = max(
            season_round_rows,
            key=lambda row: (
                float(row["reward"] or 0.0),
                -(int(row["rank"]) if row["rank"] is not None else 9999),
                -(int(row["round_number_in_season"]) if row["round_number_in_season"] is not None else 0),
            ),
        )
        best_rank_history_row = min(
            season_round_rows,
            key=lambda row: (
                int(row["rank"]) if row["rank"] is not None else 9999,
                -(float(row["reward"] or 0.0)),
                int(row["round_number_in_season"]) if row["round_number_in_season"] is not None else 9999,
            ),
        )

        ref = await self._round_ref(season, round_in_season)
        if not ref:
            raise ValueError(f"Agent {miner_uid} not found")
        round_id = int(ref["round_id"])

        miner_rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT rvm.*
                    FROM round_validator_miners rvm
                    JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                    WHERE rvm.round_id = :round_id
                      AND rvm.miner_uid = :miner_uid
                      AND rv.validator_uid = :main_validator_uid
                    ORDER BY rvm.round_validator_id ASC
                    """
                    ),
                    {
                        "round_id": round_id,
                        "miner_uid": miner_uid,
                        "main_validator_uid": main_validator_uid,
                    },
                )
            )
            .mappings()
            .all()
        )
        if not miner_rows:
            raise ValueError(f"Agent {miner_uid} not found")

        first = miner_rows[0]
        validator_rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT validator_uid, validator_hotkey, name
                        FROM round_validators
                        WHERE round_id = :round_id
                        ORDER BY validator_uid ASC
                        """
                    ),
                    {"round_id": round_id},
                )
            )
            .mappings()
            .all()
        )
        validators = [
            {
                "uid": int(vr["validator_uid"]),
                "hotkey": vr["validator_hotkey"],
                "name": vr["name"],
            }
            for vr in validator_rows
            if vr.get("validator_uid") is not None
        ]

        selected_round_history_row = next(
            (row for row in season_round_rows if int(row["round_number_in_season"]) == int(round_in_season)),
            None,
        )
        local_total_tasks = int(sum(int(r["local_tasks_received"] or 0) for r in miner_rows))
        local_success_tasks = int(sum(int(r["local_tasks_success"] or 0) for r in miner_rows))
        canonical_total_tasks = int(selected_round_history_row["tasks_received"]) if selected_round_history_row and selected_round_history_row.get("tasks_received") is not None else None
        canonical_success_tasks = int(selected_round_history_row["tasks_success"]) if selected_round_history_row and selected_round_history_row.get("tasks_success") is not None else None
        canonical_avg_time = float(selected_round_history_row["eval_time"]) if selected_round_history_row and selected_round_history_row.get("eval_time") is not None else None
        total_tasks = canonical_total_tasks if canonical_total_tasks is not None else local_total_tasks
        success_tasks = canonical_success_tasks if canonical_success_tasks is not None else local_success_tasks
        avg_time = canonical_avg_time if canonical_avg_time is not None else 0.0
        canonical_avg_cost = float(selected_round_history_row["eval_cost"]) if selected_round_history_row and selected_round_history_row.get("eval_cost") is not None else None
        reward = (
            float(selected_round_history_row["reward"])
            if selected_round_history_row and selected_round_history_row.get("reward") is not None
            else float(first["post_consensus_avg_reward"] or first["best_local_reward"] or first["local_avg_reward"] or 0.0)
        )
        rank = (
            int(selected_round_history_row["rank"])
            if selected_round_history_row and selected_round_history_row.get("rank") is not None and int(selected_round_history_row["rank"]) < 9999
            else int(first["post_consensus_rank"] or first["best_local_rank"] or 0)
        )
        selected_top_reward = float(selected_round_history_row["top_reward"]) if selected_round_history_row and selected_round_history_row.get("top_reward") is not None else reward

        runs_count = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM miner_evaluation_runs mer
                    JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                    WHERE mer.miner_uid = :uid
                      AND rv.validator_uid = :main_validator_uid
                    """
                ),
                {"uid": miner_uid, "main_validator_uid": main_validator_uid},
            )
        ).scalar_one()
        success_runs = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM miner_evaluation_runs mer
                    JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                    WHERE mer.miner_uid = :uid
                      AND rv.validator_uid = :main_validator_uid
                      AND mer.success_tasks > 0
                    """
                ),
                {"uid": miner_uid, "main_validator_uid": main_validator_uid},
            )
        ).scalar_one()
        rounds_participated = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT rvm.round_id)
                    FROM round_validator_miners rvm
                    JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                    WHERE rvm.miner_uid = :uid
                      AND rv.validator_uid = :main_validator_uid
                    """
                ),
                {"uid": miner_uid, "main_validator_uid": main_validator_uid},
            )
        ).scalar_one()
        rounds_won = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM round_summary WHERE leader_after_miner_uid = :uid"),
                {"uid": miner_uid},
            )
        ).scalar_one()

        run_ctx_rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      mer.agent_run_id,
                      mer.zero_reason
                    FROM miner_evaluation_runs mer
                    WHERE mer.miner_uid = :uid
                      AND mer.round_validator_id = :round_validator_id
                    ORDER BY mer.started_at DESC NULLS LAST, mer.created_at DESC NULLS LAST
                    """
                    ),
                    {
                        "uid": miner_uid,
                        "round_validator_id": int(first["round_validator_id"]),
                    },
                )
            )
            .mappings()
            .all()
        )
        run_ctx = run_ctx_rows[0] if run_ctx_rows else None
        run_agent_run_id = run_ctx["agent_run_id"] if run_ctx else None
        zero_reason = run_ctx["zero_reason"] if run_ctx else None

        performance_by_website: List[Dict[str, Any]] = []
        avg_cost_per_task: Optional[float] = None

        def _website_key_from_url(raw_url: Optional[str]) -> str:
            if not isinstance(raw_url, str) or not raw_url.strip():
                return "unknown"
            parsed = urlparse(raw_url)
            host = (parsed.hostname or "").strip()
            port = parsed.port
            if not host:
                return "unknown"
            # In local env, multiple IWA websites may share hostname (localhost)
            # and differ only by port (e.g., 8000, 8001). Keep port to avoid collapsing.
            if host in ("localhost", "127.0.0.1") and port:
                return f"{host}:{port}"
            return host

        source_agent_run_ids: List[str] = []
        if run_ctx_rows:
            for rc in run_ctx_rows:
                source_agent_run_ids.append(str(rc.get("agent_run_id")))
        elif run_agent_run_id:
            source_agent_run_ids.append(str(run_agent_run_id))

        if not source_agent_run_ids:
            target_reward = float(selected_round_history_row["reward"] or 0.0) if selected_round_history_row else reward
            source_round_rows = (
                (
                    await self.session.execute(
                        text(
                            """
                        WITH source_rounds AS (
                          SELECT
                            r.round_id,
                            r.round_number_in_season,
                            COALESCE(rvm.post_consensus_avg_reward, 0) AS reward,
                            ABS(COALESCE(rvm.post_consensus_avg_reward, 0) - :target_reward) AS reward_delta,
                            COUNT(mer.agent_run_id) AS run_count
                          FROM round_validator_miners rvm
                          JOIN round_validators rv
                            ON rv.round_validator_id = rvm.round_validator_id
                          JOIN rounds r
                            ON r.round_id = rvm.round_id
                          JOIN seasons s
                            ON s.season_id = r.season_id
                          LEFT JOIN miner_evaluation_runs mer
                            ON mer.round_validator_id = rv.round_validator_id
                           AND mer.miner_uid = rvm.miner_uid
                          WHERE rvm.miner_uid = :miner_uid
                            AND rv.validator_uid = :main_validator_uid
                            AND s.season_number = :season
                            AND r.round_number_in_season <= :round_in_season
                          GROUP BY r.round_id, r.round_number_in_season, rvm.post_consensus_avg_reward
                        )
                        SELECT round_id
                        FROM source_rounds
                        WHERE run_count > 0
                        ORDER BY reward_delta ASC, round_number_in_season DESC
                        LIMIT 1
                        """
                        ),
                        {
                            "miner_uid": miner_uid,
                            "main_validator_uid": main_validator_uid,
                            "season": season,
                            "round_in_season": round_in_season,
                            "target_reward": target_reward,
                        },
                    )
                )
                .mappings()
                .all()
            )
            if source_round_rows:
                source_round_id = int(source_round_rows[0]["round_id"])
                fallback_run_rows = (
                    (
                        await self.session.execute(
                            text(
                                """
                            SELECT mer.agent_run_id
                            FROM miner_evaluation_runs mer
                            JOIN round_validators rv
                              ON rv.round_validator_id = mer.round_validator_id
                            WHERE mer.miner_uid = :miner_uid
                              AND rv.validator_uid = :main_validator_uid
                              AND rv.round_id = :round_id
                            ORDER BY mer.started_at DESC NULLS LAST, mer.created_at DESC NULLS LAST
                            """
                            ),
                            {
                                "miner_uid": miner_uid,
                                "main_validator_uid": main_validator_uid,
                                "round_id": source_round_id,
                            },
                        )
                    )
                    .mappings()
                    .all()
                )
                for rc in fallback_run_rows:
                    source_agent_run_ids.append(str(rc.get("agent_run_id")))
                source_agent_run_ids = list(dict.fromkeys([run_id for run_id in source_agent_run_ids if run_id]))

        if source_agent_run_ids:
            by_website: Dict[str, Dict[str, Any]] = {}
            total_tasks_for_cost = 0
            total_llm_cost = 0.0
            has_llm_usage = False
            for source_agent_run_id in source_agent_run_ids:
                task_rows = (
                    (
                        await self.session.execute(
                            text(
                                """
                            SELECT
                              ts.task_id,
                              ts.actions,
                              t.web_project_id,
                              t.url AS task_url,
                              e.evaluation_score,
                              COALESCE((
                                SELECT SUM(COALESCE(u.cost, 0.0))
                                FROM evaluation_llm_usage u
                                WHERE u.evaluation_id = e.evaluation_id
                              ), 0.0) AS llm_cost,
                              COALESCE((
                                SELECT COUNT(*)
                                FROM evaluation_llm_usage u
                                WHERE u.evaluation_id = e.evaluation_id
                              ), 0) AS llm_usage_count
                            FROM task_solutions ts
                            LEFT JOIN tasks t
                              ON t.task_id = ts.task_id
                            LEFT JOIN evaluations e
                              ON e.task_solution_id = ts.solution_id
                            WHERE ts.agent_run_id = :agent_run_id
                            """
                            ),
                            {"agent_run_id": source_agent_run_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                for row in task_rows:
                    actions = row.get("actions") or []
                    website = str(row.get("web_project_id") or "").strip() or _website_key_from_url(row.get("task_url"))
                    action_url = None
                    if isinstance(actions, list) and actions:
                        first_action = actions[0] if isinstance(actions[0], dict) else {}
                        action_url = first_action.get("url")
                    if website == "unknown":
                        website = _website_key_from_url(action_url)
                    item = by_website.setdefault(
                        website,
                        {"website": website, "tasks_received": 0, "tasks_success": 0, "success_rate": 0.0},
                    )
                    item["tasks_received"] += 1
                    score = float(row.get("evaluation_score") or 0.0)
                    if score > 0:
                        item["tasks_success"] += 1
                    total_tasks_for_cost += 1
                    total_llm_cost += float(row.get("llm_cost") or 0.0)
                    if int(row.get("llm_usage_count") or 0) > 0:
                        has_llm_usage = True
            for entry in by_website.values():
                tasks_received = int(entry["tasks_received"] or 0)
                tasks_success = int(entry["tasks_success"] or 0)
                entry["success_rate"] = (tasks_success / tasks_received) if tasks_received > 0 else 0.0
                performance_by_website.append(entry)
            performance_by_website.sort(key=lambda x: x["tasks_received"], reverse=True)
            if has_llm_usage and total_tasks_for_cost > 0:
                avg_cost_per_task = total_llm_cost / float(total_tasks_for_cost)
        if avg_cost_per_task is None and canonical_avg_cost is not None and canonical_avg_cost > 0:
            avg_cost_per_task = canonical_avg_cost

        season_leadership_row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      leader_after_miner_uid,
                      leader_after_reward,
                      leader_before_miner_uid,
                      leader_before_reward,
                      candidate_miner_uid,
                      candidate_reward,
                      required_improvement_pct,
                      dethroned
                    FROM round_summary
                    WHERE round_id = :round_id
                    LIMIT 1
                    """
                    ),
                    {"round_id": round_id},
                )
            )
            .mappings()
            .first()
        )
        season_leadership = None
        if season_leadership_row:
            season_leader_uid = int(season_leadership_row["leader_after_miner_uid"]) if season_leadership_row["leader_after_miner_uid"] is not None else None
            season_leader_reward = float(season_leadership_row["leader_after_reward"]) if season_leadership_row["leader_after_reward"] is not None else None
            reigning_uid_before_round = int(season_leadership_row["leader_before_miner_uid"]) if season_leadership_row["leader_before_miner_uid"] is not None else None
            reigning_reward_before_round = float(season_leadership_row["leader_before_reward"]) if season_leadership_row["leader_before_reward"] is not None else None
            top_candidate_uid = int(season_leadership_row["candidate_miner_uid"]) if season_leadership_row["candidate_miner_uid"] is not None else None
            top_candidate_reward = float(season_leadership_row["candidate_reward"]) if season_leadership_row["candidate_reward"] is not None else None
            dethroned = bool(season_leadership_row["dethroned"]) if season_leadership_row["dethroned"] is not None else False

            season_leadership = {
                "round_winner_uid": top_candidate_uid,
                "round_winner_reward": top_candidate_reward,
                "season_leader_uid": season_leader_uid,
                "season_leader_reward": season_leader_reward,
                "reigning_uid_before_round": reigning_uid_before_round,
                "reigning_reward_before_round": reigning_reward_before_round,
                "top_candidate_uid": top_candidate_uid,
                "top_candidate_reward": top_candidate_reward,
                "required_improvement_pct": (float(season_leadership_row["required_improvement_pct"]) if season_leadership_row["required_improvement_pct"] is not None else 0.05),
                "dethroned": dethroned,
            }

        best_round_number = int(best_round_history_row["round_number_in_season"]) if best_round_history_row.get("round_number_in_season") is not None else round_in_season
        best_round_matches_selected = requested_round_in_season is None or int(round_in_season) == int(best_round_number)
        best_round_payload = (
            {
                "round": int(best_round_number),
                "post_consensus_avg_reward": reward,
                "post_consensus_avg_eval_time": avg_time,
                "tasks_received": total_tasks,
                "tasks_success": success_tasks,
                "validators_count": len(validators),
                "post_consensus_avg_cost": avg_cost_per_task,
                "performanceByWebsite": performance_by_website,
                "websites_count": len(performance_by_website),
                "season_leadership": season_leadership,
            }
            if best_round_matches_selected
            else None
        )

        return {
            "agent": {
                "id": f"agent-{miner_uid}",
                "uid": miner_uid,
                "name": first["name"] or f"miner {miner_uid}",
                "hotkey": first["miner_hotkey"],
                "type": "autoppia",
                "imageUrl": f"/miners/{miner_uid % 100}.svg",
                "githubUrl": first["github_url"],
                "taostatsUrl": f"https://taostats.io/subnets/36/metagraph?filter={first['miner_hotkey']}",
                "isSota": bool(first["is_sota"]),
                "description": "",
                "version": first["version"],
                "status": "active",
                "totalRuns": int(runs_count or 0),
                "successfulRuns": int(success_runs or 0),
                "currentReward": reward,
                "currentTopReward": selected_top_reward,
                "currentRank": (season_rank if requested_round_in_season is None else rank),
                "seasonRank": season_rank,
                "bestRankEver": (int(best_rank_history_row["rank"]) if best_rank_history_row.get("rank") is not None else rank),
                "bestRankRoundId": (int(best_rank_history_row["round_number_in_season"]) if best_rank_history_row.get("round_number_in_season") is not None else round_in_season),
                "roundsParticipated": int(rounds_participated or 0),
                "roundsWon": int(rounds_won or 0),
                "alphaWonInPrizes": 0.0,
                "taoWonInPrizes": 0.0,
                "bestRoundReward": float(best_round_history_row["reward"] or reward),
                "bestRoundId": (int(best_round_history_row["round_number_in_season"]) if best_round_history_row.get("round_number_in_season") is not None else round_in_season),
                "averageResponseTime": round(avg_time, 2),
                "totalTasks": total_tasks,
                "completedTasks": success_tasks,
                "lastSeen": None,
                "createdAt": None,
                "updatedAt": None,
            },
            "bestRound": best_round_payload,
            "zero_reason": zero_reason,
        }

    async def get_miner_historical(self, miner_uid: int, season: Optional[int]) -> Dict[str, Any]:
        main_validator_uid = await self._get_main_validator_uid()
        where = "WHERE round_validator_miners.miner_uid = :uid"
        params: Dict[str, Any] = {"uid": miner_uid, "main_validator_uid": main_validator_uid}
        if season is not None:
            where += " AND round_validator_miners.round_id IN (SELECT r.round_id FROM rounds r JOIN seasons s ON s.season_id=r.season_id WHERE s.season_number=:season)"
            params["season"] = season
        rows = (
            (
                await self.session.execute(
                    text(
                        f"""
                    SELECT round_validator_miners.round_id AS round_id, post_consensus_rank, post_consensus_avg_reward,
                           post_consensus_avg_eval_score, post_consensus_avg_eval_time,
                           post_consensus_avg_eval_cost,
                           post_consensus_tasks_received, post_consensus_tasks_success,
                           subnet_price, weight
                    FROM round_validator_miners
                    {where}
                    ORDER BY round_id DESC
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ValueError(f"Miner {miner_uid} not found in any round")

        by_round: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            rid = int(r["round_id"])
            current = by_round.get(rid)
            incoming_rank = r["post_consensus_rank"]
            current_rank = current["post_consensus_rank"] if current else None
            replace = current is None
            if not replace:
                if incoming_rank is not None and (current_rank is None or int(incoming_rank) < int(current_rank)):
                    replace = True
                elif incoming_rank == current_rank:
                    replace = float(r["post_consensus_avg_reward"] or 0.0) > float(current["post_consensus_avg_reward"] or 0.0)
            if replace:
                by_round[rid] = dict(r)

        rounds_history = []
        for r in sorted(by_round.values(), key=lambda item: int(item["round_id"]), reverse=True):
            sr = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT
                                s.season_number,
                                ro.round_number_in_season,
                                ro.start_epoch,
                                ro.end_epoch,
                                (
                                  SELECT COUNT(DISTINCT rv.validator_uid)
                                  FROM round_validators rv
                                  WHERE rv.round_id = ro.round_id
                                ) AS validators_count,
                                (
                                  SELECT COUNT(DISTINCT t.web_project_id)
                                  FROM tasks t
                                  JOIN round_validators rv2 ON rv2.validator_round_id = t.validator_round_id
                                  WHERE rv2.round_id = ro.round_id
                                ) AS websites_count
                            FROM rounds ro
                            JOIN seasons s ON s.season_id = ro.season_id
                            WHERE ro.round_id = :rid
                            """
                        ),
                        {"rid": int(r["round_id"])},
                    )
                )
                .mappings()
                .first()
            )
            start_epoch = int(sr["start_epoch"]) if sr and sr.get("start_epoch") is not None else None
            end_epoch = int(sr["end_epoch"]) if sr and sr.get("end_epoch") is not None else None
            if start_epoch is not None and end_epoch is not None:
                # Some rounds are persisted with equal epochs; treat them as at least 1 epoch.
                round_epochs = max(end_epoch - start_epoch, 1)
            else:
                # Round timing is DB-only via config_season_round.
                from app.services.round_config_from_db import get_round_blocks_from_latest_round
                from app.services.round_config_service import get_config_season_round

                round_epochs = None
                round_blocks = await get_round_blocks_from_latest_round(self.session)
                cfg = get_config_season_round()
                if round_blocks and cfg.blocks_per_epoch:
                    round_epochs = max(int(round_blocks / cfg.blocks_per_epoch), 1)
                if round_epochs is None:
                    round_epochs = max(int(float(cfg.round_size_epochs)), 1)
            weight = float(r["weight"] or 0.0)
            subnet_price = float(r["subnet_price"] or 0.0)
            alpha_earned = float(settings.ALPHA_EMISSION_PER_EPOCH) * float(round_epochs) * weight
            tao_earned = alpha_earned * subnet_price
            rounds_history.append(
                {
                    "round": f"{int(sr['season_number'])}/{int(sr['round_number_in_season'])}" if sr else str(int(r["round_id"])),
                    "post_consensus_rank": r["post_consensus_rank"],
                    "post_consensus_avg_reward": float(r["post_consensus_avg_reward"] or 0.0),
                    "post_consensus_avg_eval_score": float(r["post_consensus_avg_eval_score"] or 0.0),
                    "post_consensus_avg_eval_time": float(r["post_consensus_avg_eval_time"] or 0.0),
                    "post_consensus_avg_eval_cost": (float(r["post_consensus_avg_eval_cost"]) if r["post_consensus_avg_eval_cost"] is not None else None),
                    "tasks_received": int(r["post_consensus_tasks_received"] or 0),
                    "tasks_success": int(r["post_consensus_tasks_success"] or 0),
                    "tasks_failed": max(int(r["post_consensus_tasks_received"] or 0) - int(r["post_consensus_tasks_success"] or 0), 0),
                    "is_winner": (r["post_consensus_rank"] == 1),
                    "validators_count": int(sr["validators_count"] or 0) if sr else 0,
                    "websites_count": int(sr["websites_count"] or 0) if sr else 0,
                    "post_consensus_available": any(
                        r.get(key) is not None
                        for key in (
                            "post_consensus_rank",
                            "post_consensus_avg_reward",
                            "post_consensus_avg_eval_score",
                            "post_consensus_avg_eval_time",
                            "post_consensus_avg_eval_cost",
                            "post_consensus_tasks_received",
                            "post_consensus_tasks_success",
                        )
                    ),
                    "subnet_price": subnet_price,
                    "weight": weight,
                    "round_epochs": round_epochs,
                    "alpha_earned": alpha_earned,
                    "tao_earned": tao_earned,
                }
            )

        profile_where = "WHERE rvm.miner_uid = :uid"
        if season is not None:
            profile_where += " AND s.season_number = :season"
        miner_profile = (
            (
                await self.session.execute(
                    text(
                        f"""
                        SELECT
                            COALESCE(NULLIF(rvm.name, ''), CONCAT('miner ', rvm.miner_uid)::VARCHAR(256)) AS name,
                            NULLIF(rvm.miner_hotkey, '') AS miner_hotkey,
                            rvm.image_url,
                            NULLIF(rvm.github_url, '') AS github_url
                        FROM round_validator_miners rvm
                        JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                        JOIN rounds r ON r.round_id = rv.round_id
                        JOIN seasons s ON s.season_id = r.season_id
                        {profile_where}
                          AND rv.validator_uid = :main_validator_uid
                        ORDER BY r.round_id DESC, rv.round_validator_id DESC, rvm.updated_at DESC NULLS LAST
                        LIMIT 1
                        """
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
        distinct_github_urls = (
            await self.session.execute(
                text(
                    f"""
                    SELECT COUNT(DISTINCT NULLIF(rvm.github_url, ''))::INTEGER
                    FROM round_validator_miners rvm
                    JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                    JOIN rounds r ON r.round_id = rv.round_id
                    JOIN seasons s ON s.season_id = r.season_id
                    {profile_where}
                      AND rv.validator_uid = :main_validator_uid
                    """
                ),
                params,
            )
        ).scalar_one()

        season_rank_value: Optional[int] = None
        season_rank_round: Optional[str] = None
        if season is not None:
            season_rank_row = (
                (
                    await self.session.execute(
                        text(
                            """
                        WITH season_rows AS (
                          SELECT
                            rvm.miner_uid AS uid,
                            COALESCE(rvm.post_consensus_avg_reward, 0) AS best_reward,
                            COALESCE(rvm.post_consensus_rank, 9999) AS best_rank,
                            r.round_number_in_season AS round_number
                          FROM round_validator_miners rvm
                          JOIN rounds r ON r.round_id = rvm.round_id
                          JOIN seasons s ON s.season_id = r.season_id
                          WHERE s.season_number = :season
                            AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                            AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                            AND (
                              rvm.post_consensus_avg_reward IS NOT NULL
                              OR rvm.post_consensus_rank IS NOT NULL
                            )
                        ),
                        best_rows AS (
                          SELECT DISTINCT ON (uid)
                            uid,
                            best_reward,
                            best_rank,
                            round_number
                          FROM season_rows
                          ORDER BY uid, best_reward DESC, best_rank ASC, round_number ASC
                        ),
                        ranked AS (
                          SELECT
                            uid,
                            round_number,
                            ROW_NUMBER() OVER (
                              ORDER BY best_reward DESC, best_rank ASC, uid ASC
                            ) AS season_rank
                          FROM best_rows
                        )
                        SELECT season_rank, round_number
                        FROM ranked
                        WHERE uid = :uid
                        LIMIT 1
                        """
                        ),
                        {
                            "uid": miner_uid,
                            "season": season,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if season_rank_row:
                season_rank_value = int(season_rank_row["season_rank"]) if season_rank_row.get("season_rank") is not None else None
                if season_rank_row.get("round_number") is not None:
                    season_rank_round = f"{season}/{int(season_rank_row['round_number'])}"

        best = min([x["post_consensus_rank"] for x in rounds_history if x["post_consensus_rank"] is not None] or [None], default=None)
        best_score = max([x["post_consensus_avg_reward"] for x in rounds_history] or [0.0])
        best_score_round = None
        best_rank_round = None
        best_round_season: Optional[int] = None
        best_round_in_season: Optional[int] = None
        if rounds_history:
            best_score_row = max(
                rounds_history,
                key=lambda x: float(x.get("post_consensus_avg_reward") or 0.0),
            )
            best_score_round = best_score_row["round"]
            if isinstance(best_score_round, str) and "/" in best_score_round:
                season_s, round_s = best_score_round.split("/", 1)
                best_round_season = int(season_s)
                best_round_in_season = int(round_s)
            rankable = [x for x in rounds_history if x.get("post_consensus_rank") is not None]
            if rankable:
                best_rank_round = min(rankable, key=lambda x: int(x["post_consensus_rank"]))["round"]
        total_tasks = sum(x["tasks_received"] for x in rounds_history)
        total_success = sum(x["tasks_success"] for x in rounds_history)
        rounds_won = sum(1 for x in rounds_history if x["is_winner"])
        total_alpha_earned = sum(float(x.get("alpha_earned") or 0.0) for x in rounds_history)
        total_tao_earned = sum(float(x.get("tao_earned") or 0.0) for x in rounds_history)

        performance_by_website_best_round: List[Dict[str, Any]] = []
        if best_round_in_season is not None and (season is None or best_round_season == season):
            try:
                best_round_detail = await self.get_agent_detail(miner_uid, best_round_season, best_round_in_season)
                best_round_ref = await self._round_ref(best_round_season, best_round_in_season)
                best_round_id = int(best_round_ref["round_id"]) if best_round_ref and best_round_ref.get("round_id") is not None else None
                source_agent_run_ids: List[str] = []
                if best_round_id is not None:
                    best_round_run_ctx_rows = (
                        (
                            await self.session.execute(
                                text(
                                    """
                                SELECT
                                  mer.agent_run_id
                                FROM miner_evaluation_runs mer
                                JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                                WHERE mer.miner_uid = :uid
                                  AND rv.round_id = :round_id
                                  AND rv.validator_uid = :main_validator_uid
                                ORDER BY mer.started_at DESC NULLS LAST, mer.created_at DESC NULLS LAST
                                """
                                ),
                                {
                                    "uid": miner_uid,
                                    "round_id": best_round_id,
                                    "main_validator_uid": main_validator_uid,
                                },
                            )
                        )
                        .mappings()
                        .all()
                    )
                    for rc in best_round_run_ctx_rows:
                        source_agent_run_ids.append(str(rc.get("agent_run_id")))
                source_agent_run_ids = list(dict.fromkeys([run_id for run_id in source_agent_run_ids if run_id]))

                by_website_with_use_cases: Dict[str, Dict[str, Any]] = {}
                if source_agent_run_ids:
                    task_rows = (
                        (
                            await self.session.execute(
                                text(
                                    """
                                SELECT
                                  ts.task_id,
                                  ts.agent_run_id,
                                  t.web_project_id,
                                  t.url AS task_url,
                                  t.prompt,
                                  t.use_case,
                                  e.evaluation_id,
                                  e.evaluation_score,
                                  e.evaluation_time,
                                  e.reward
                                FROM task_solutions ts
                                LEFT JOIN tasks t
                                  ON t.task_id = ts.task_id
                                LEFT JOIN evaluations e
                                  ON e.task_solution_id = ts.solution_id
                                WHERE ts.agent_run_id = ANY(:agent_run_ids)
                                ORDER BY ts.created_at ASC NULLS LAST, ts.task_id ASC
                                """
                                ),
                                {"agent_run_ids": source_agent_run_ids},
                            )
                        )
                        .mappings()
                        .all()
                    )

                    def _historical_website_key(raw_url: Optional[str]) -> str:
                        if not isinstance(raw_url, str) or not raw_url.strip():
                            return "unknown"
                        parsed = urlparse(raw_url)
                        host = (parsed.hostname or "").strip()
                        port = parsed.port
                        if not host:
                            return "unknown"
                        if host in ("localhost", "127.0.0.1") and port:
                            return f"{host}:{port}"
                        return host

                    for row in task_rows:
                        website = str(row.get("web_project_id") or "").strip() or _historical_website_key(row.get("task_url"))
                        if not website:
                            website = "unknown"
                        use_case_value = row.get("use_case")
                        use_case_name = "Unknown use case"
                        if isinstance(use_case_value, dict):
                            use_case_name = str(use_case_value.get("name") or use_case_value.get("event") or "Unknown use case")
                        elif isinstance(use_case_value, str) and use_case_value.strip():
                            use_case_name = use_case_value.strip()

                        website_entry = by_website_with_use_cases.setdefault(
                            website,
                            {
                                "website": website,
                                "tasks": 0,
                                "successful": 0,
                                "failed": 0,
                                "averageDuration": 0.0,
                                "useCases": [],
                            },
                        )
                        use_case_map = website_entry.setdefault("_use_case_map", {})
                        use_case_entry = use_case_map.setdefault(
                            use_case_name,
                            {
                                "useCase": use_case_name,
                                "tasks": 0,
                                "successful": 0,
                                "failed": 0,
                                "averageDuration": 0.0,
                                "taskDetails": [],
                            },
                        )

                        score = float(row.get("evaluation_score") or 0.0)
                        evaluation_time = float(row.get("evaluation_time") or 0.0)
                        is_success = score >= 1.0
                        task_status = "successful" if is_success else "failed"

                        website_entry["tasks"] += 1
                        website_entry["successful"] += 1 if is_success else 0
                        website_entry["failed"] += 0 if is_success else 1
                        website_entry["averageDuration"] += evaluation_time

                        use_case_entry["tasks"] += 1
                        use_case_entry["successful"] += 1 if is_success else 0
                        use_case_entry["failed"] += 0 if is_success else 1
                        use_case_entry["averageDuration"] += evaluation_time
                        use_case_entry["taskDetails"].append(
                            {
                                "taskId": row.get("task_id"),
                                "evaluationId": row.get("evaluation_id"),
                                "agentRunId": row.get("agent_run_id"),
                                "prompt": row.get("prompt") or "",
                                "score": score,
                                "reward": float(row.get("reward") or 0.0),
                                "evaluationTime": evaluation_time,
                                "status": task_status,
                                "round": f"{best_round_season}/{best_round_in_season}",
                                "useCase": use_case_name,
                            }
                        )

                if by_website_with_use_cases:
                    for website_entry in by_website_with_use_cases.values():
                        website_tasks = int(website_entry.get("tasks") or 0)
                        if website_tasks > 0:
                            website_entry["averageDuration"] = float(website_entry["averageDuration"] or 0.0) / float(website_tasks)
                        use_cases = []
                        for use_case_entry in website_entry.pop("_use_case_map", {}).values():
                            use_case_tasks = int(use_case_entry.get("tasks") or 0)
                            if use_case_tasks > 0:
                                use_case_entry["averageDuration"] = float(use_case_entry["averageDuration"] or 0.0) / float(use_case_tasks)
                            use_case_entry["taskDetails"] = sorted(
                                use_case_entry.get("taskDetails") or [],
                                key=lambda item: (
                                    str(item.get("evaluationId") or ""),
                                    str(item.get("taskId") or ""),
                                ),
                            )
                            use_cases.append(use_case_entry)
                        use_cases.sort(key=lambda item: (-int(item.get("tasks") or 0), str(item.get("useCase") or "")))
                        website_entry["useCases"] = use_cases
                    performance_by_website_best_round = sorted(
                        by_website_with_use_cases.values(),
                        key=lambda item: (-int(item.get("tasks") or 0), str(item.get("website") or "")),
                    )
                else:
                    for row in best_round_detail.get("performanceByWebsite") or []:
                        tasks_received = int(row.get("tasks_received") or row.get("tasks") or 0)
                        tasks_success = int(row.get("tasks_success") or row.get("successful") or 0)
                        performance_by_website_best_round.append(
                            {
                                "website": row.get("website") or "unknown",
                                "tasks": tasks_received,
                                "successful": tasks_success,
                                "failed": max(tasks_received - tasks_success, 0),
                                "averageDuration": float(row.get("averageDuration") or 0.0),
                                "useCases": [],
                            }
                        )
            except Exception:
                # Keep endpoint resilient if best-round details are unavailable
                performance_by_website_best_round = []

        return {
            "miner": {
                "uid": miner_uid,
                "name": (miner_profile["name"] if miner_profile else f"miner {miner_uid}"),
                "hotkey": (miner_profile["miner_hotkey"] if miner_profile else None),
                "image": (miner_profile["image_url"] if miner_profile and miner_profile.get("image_url") else f"/miners/{miner_uid % 100}.svg"),
            },
            "summary": {
                "totalRounds": len(rounds_history),
                "roundsWon": rounds_won,
                "roundsLost": len(rounds_history) - rounds_won,
                "roundsParticipated": len(rounds_history),
                "totalTasks": total_tasks,
                "totalTasksSuccessful": total_success,
                "totalTasksFailed": max(total_tasks - total_success, 0),
                "overallSuccessRate": (total_success / total_tasks) if total_tasks > 0 else 0.0,
                "averageDuration": sum(x["post_consensus_avg_eval_time"] for x in rounds_history) / len(rounds_history),
                "bestReward": best_score,
                "bestRewardRound": best_score_round,
                "bestRank": season_rank_value if season_rank_value is not None else best,
                "bestRankRound": season_rank_round or best_rank_round,
                "averageReward": sum(x["post_consensus_avg_reward"] for x in rounds_history) / len(rounds_history),
                "totalAlphaEarned": total_alpha_earned,
                "totalTaoEarned": total_tao_earned,
                "distinctGithubUrls": int(distinct_github_urls or 0),
            },
            # Keep legacy key for compatibility + explicit clearer alias.
            "performanceByWebsite": performance_by_website_best_round,
            "performanceByWebsiteBestRound": performance_by_website_best_round,
            "roundsHistory": rounds_history,
        }

    async def list_round_agent_runs(self, round_identifier: str, limit: int, skip: int) -> List[Dict[str, Any]]:
        ref = await self._resolve_round_identifier(round_identifier)
        round_id = int(ref["round_id"])
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT mer.agent_run_id, mer.validator_round_id, mer.round_validator_id, mer.miner_uid, mer.miner_hotkey,
                           mer.started_at, mer.ended_at, mer.elapsed_sec,
                           mer.average_score, mer.average_execution_time, mer.average_reward,
                           mer.total_tasks, mer.success_tasks, mer.failed_tasks,
                           mer.zero_reason
                    FROM miner_evaluation_runs mer
                    WHERE mer.round_validator_id IN (
                      SELECT rv.round_validator_id FROM round_validators rv WHERE rv.round_id = :rid
                    )
                    ORDER BY mer.started_at DESC NULLS LAST
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    {"rid": round_id, "limit": limit, "offset": skip},
                )
            )
            .mappings()
            .all()
        )
        return [self._row_to_agent_eval_run(r) for r in rows]

    async def get_agent_run_by_id(self, agent_run_id: str) -> Dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT mer.agent_run_id, mer.validator_round_id, mer.round_validator_id, mer.miner_uid, mer.miner_hotkey,
                           mer.started_at, mer.ended_at, mer.elapsed_sec,
                           mer.average_score, mer.average_execution_time, mer.average_reward,
                           mer.total_tasks, mer.success_tasks, mer.failed_tasks,
                           mer.zero_reason
                    FROM miner_evaluation_runs mer
                    WHERE mer.agent_run_id = :run_id
                    LIMIT 1
                    """
                    ),
                    {"run_id": agent_run_id},
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise ValueError(f"Agent run {agent_run_id} not found")
        return self._row_to_agent_eval_run(row)

    def _row_to_agent_eval_run(self, row: Any) -> Dict[str, Any]:
        started = float(row["started_at"] or 0.0)
        ended = float(row["ended_at"]) if row["ended_at"] is not None else None
        return {
            "agent_run_id": row["agent_run_id"],
            "validator_round_id": row["validator_round_id"] or f"round_validator_{int(row['round_validator_id'])}",
            "miner_uid": int(row["miner_uid"]) if row["miner_uid"] is not None else None,
            "miner_hotkey": row["miner_hotkey"],
            "started_at": started,
            "ended_at": ended,
            "elapsed_sec": float(row["elapsed_sec"] or 0.0),
            "average_score": float(row["average_score"] or 0.0),
            "average_execution_time": float(row["average_execution_time"] or 0.0),
            "average_reward": float(row["average_reward"] or 0.0),
            "total_tasks": int(row["total_tasks"] or 0),
            "success_tasks": int(row["success_tasks"] or 0),
            "failed_tasks": int(row["failed_tasks"] or 0),
            "metadata": {},
            "zero_reason": row["zero_reason"],
            "tasks": [],
            "task_solutions": [],
            "evaluations": [],
        }

    async def get_agent_performance_metrics(
        self,
        agent_id: str,
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> Dict[str, Any]:
        uid = int(str(agent_id).replace("agent-", ""))
        where = "WHERE miner_uid = :uid"
        params: Dict[str, Any] = {"uid": uid}
        if start_date is not None:
            where += " AND started_at >= :start_ts"
            params["start_ts"] = start_date.timestamp()
        if end_date is not None:
            where += " AND started_at <= :end_ts"
            params["end_ts"] = end_date.timestamp()
        rows = (
            (
                await self.session.execute(
                    text(
                        f"""
                    SELECT agent_run_id, started_at, average_reward, average_execution_time, total_tasks, success_tasks, failed_tasks
                    FROM miner_evaluation_runs
                    {where}
                    ORDER BY started_at ASC
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        total_runs = len(rows)
        successful_runs = sum(1 for r in rows if int(r["success_tasks"] or 0) > 0)
        failed_runs = total_runs - successful_runs
        total_tasks = sum(int(r["total_tasks"] or 0) for r in rows)
        completed_tasks = sum(int(r["success_tasks"] or 0) for r in rows)
        avg_response = (sum(float(r["average_execution_time"] or 0.0) for r in rows) / total_runs) if total_runs > 0 else 0.0
        scores = [float(r["average_reward"] or 0.0) for r in rows]
        current_score = scores[-1] if scores else 0.0
        worst_score = min(scores) if scores else 0.0
        trend: List[Dict[str, Any]] = []
        for i, r in enumerate(rows, start=1):
            tasks = int(r["total_tasks"] or 0)
            succ = int(r["success_tasks"] or 0)
            trend.append(
                {
                    "round": i,
                    "reward": float(r["average_reward"] or 0.0),
                    "responseTime": float(r["average_execution_time"] or 0.0),
                    "successRate": (succ / tasks) if tasks > 0 else 0.0,
                }
            )
        return {
            "agentId": str(agent_id),
            "timeRange": {
                "start": start_date.isoformat() if start_date else "",
                "end": end_date.isoformat() if end_date else "",
            },
            "totalRuns": total_runs,
            "successfulRuns": successful_runs,
            "failedRuns": failed_runs,
            "successRate": (successful_runs / total_runs) if total_runs > 0 else 0.0,
            "currentReward": current_score,
            "worstReward": worst_score,
            "averageResponseTime": avg_response,
            "totalTasks": total_tasks,
            "completedTasks": completed_tasks,
            "taskCompletionRate": (completed_tasks / total_tasks) if total_tasks > 0 else 0.0,
            "scoreDistribution": {
                "excellent": sum(1 for x in scores if x >= 0.9),
                "good": sum(1 for x in scores if 0.7 <= x < 0.9),
                "average": sum(1 for x in scores if 0.5 <= x < 0.7),
                "poor": sum(1 for x in scores if x < 0.5),
            },
            "performanceTrend": trend,
        }

    async def list_agent_runs_for_agent(self, agent_id: str, page: int, limit: int) -> Dict[str, Any]:
        uid = int(str(agent_id).replace("agent-", ""))
        offset = (page - 1) * limit
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                           mer.agent_run_id, mer.started_at, mer.ended_at, mer.elapsed_sec,
                           mer.total_tasks, mer.success_tasks, mer.failed_tasks,
                           mer.average_reward, mer.average_score, mer.average_execution_time,
                           mer.round_validator_id, mer.zero_reason,
                           rv.validator_uid, rv.name AS validator_name, rv.image_url AS validator_image,
                           websites.websites_count,
                           run_cost.avg_cost_per_task
                    FROM miner_evaluation_runs mer
                    LEFT JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                    LEFT JOIN LATERAL (
                      SELECT COUNT(DISTINCT t.web_project_id) AS websites_count
                      FROM evaluations e
                      JOIN tasks t ON t.task_id = e.task_id
                      WHERE e.agent_run_id = mer.agent_run_id
                    ) websites ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT AVG(task_cost) AS avg_cost_per_task
                      FROM (
                        SELECT e.evaluation_id, COALESCE(SUM(lu.cost), 0.0) AS task_cost
                        FROM evaluations e
                        LEFT JOIN evaluation_llm_usage lu ON lu.evaluation_id = e.evaluation_id
                        WHERE e.agent_run_id = mer.agent_run_id
                        GROUP BY e.evaluation_id
                      ) run_eval_costs
                    ) run_cost ON TRUE
                    WHERE mer.miner_uid = :uid
                    ORDER BY mer.started_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    {"uid": uid, "limit": limit, "offset": offset},
                )
            )
            .mappings()
            .all()
        )
        total = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM miner_evaluation_runs WHERE miner_uid=:uid"),
                {"uid": uid},
            )
        ).scalar_one()
        runs = []
        for r in rows:
            rv = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          COALESCE(s.season_number, rv.season_number) AS season_number,
                          COALESCE(rr.round_number_in_season, rv.round_number_in_season) AS round_number_in_season,
                          rv.validator_uid
                        FROM round_validators rv
                        LEFT JOIN rounds rr ON rr.round_id = rv.round_id
                        LEFT JOIN seasons s ON s.season_id = rr.season_id
                        WHERE rv.round_validator_id = :rvid
                        LIMIT 1
                        """
                        ),
                        {"rvid": int(r["round_validator_id"]) if r["round_validator_id"] is not None else -1},
                    )
                )
                .mappings()
                .first()
            )
            round_id = None
            if rv and rv.get("season_number") is not None and rv.get("round_number_in_season") is not None:
                round_id = int(rv["season_number"]) * 10000 + int(rv["round_number_in_season"])
            if round_id is None:
                # Avoid surfacing synthetic "Round 0" cards for orphan/shadow rows.
                continue
            total_tasks = int(r["total_tasks"] or 0)
            success_tasks = int(r["success_tasks"] or 0)
            status = self._derive_agent_run_status(
                ended_at=r["ended_at"],
                zero_reason=r["zero_reason"],
                total_tasks=total_tasks,
                successful_tasks=success_tasks,
            )
            runs.append(
                {
                    "runId": r["agent_run_id"],
                    "agentId": str(agent_id),
                    "roundId": round_id,
                    "validatorId": f"validator-{int(rv['validator_uid'])}" if rv else "validator-0",
                    "startTime": datetime.fromtimestamp(float(r["started_at"] or 0.0), tz=timezone.utc),
                    "endTime": datetime.fromtimestamp(float(r["ended_at"]), tz=timezone.utc) if r["ended_at"] is not None else None,
                    "status": status,
                    "totalTasks": total_tasks,
                    "completedTasks": success_tasks,
                    "successfulTasks": success_tasks,
                    "failedTasks": int(r["failed_tasks"] or 0),
                    "reward": float(r["average_reward"] or 0.0),
                    "averageReward": float(r["average_reward"] or 0.0),
                    "averageScore": float(r["average_score"] or 0.0),
                    "overallReward": float(r["average_reward"] or 0.0),
                    "averageEvaluationTime": float(r["average_execution_time"] or 0.0),
                    "avgCostPerTask": (float(r["avg_cost_per_task"]) if r["avg_cost_per_task"] is not None else None),
                    "websitesCount": int(r["websites_count"] or 0),
                    "validatorName": r["validator_name"] or "Validator",
                    "validatorImage": r["validator_image"] or "/validators/Other.png",
                    "duration": int(float(r["elapsed_sec"] or 0.0)),
                    "tasks": [],
                    "metadata": {},
                }
            )
        return {
            "runs": runs,
            "total": int(total or 0),
            "page": page,
            "limit": limit,
            "availableRounds": sorted(list({int(run["roundId"]) for run in runs if run.get("roundId") is not None}), reverse=True),
            "selectedRound": None,
        }

    async def get_agent_activity_feed(
        self,
        agent_id: str,
        limit: int,
        offset: int,
        activity_type: Optional[str],
        since: Optional[datetime],
    ) -> Dict[str, Any]:
        uid = int(str(agent_id).replace("agent-", ""))
        where = "WHERE miner_uid = :uid"
        params: Dict[str, Any] = {"uid": uid, "limit": limit, "offset": offset}
        if since is not None:
            where += " AND started_at >= :since_ts"
            params["since_ts"] = since.timestamp()
        rows = (
            (
                await self.session.execute(
                    text(
                        f"""
                    SELECT agent_run_id, started_at, ended_at, total_tasks, success_tasks, zero_reason
                    FROM miner_evaluation_runs
                    {where}
                    ORDER BY started_at DESC
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        activities: List[Dict[str, Any]] = []
        for r in rows:
            start_dt = datetime.fromtimestamp(float(r["started_at"] or 0.0), tz=timezone.utc)
            run_started = {
                "id": f"{r['agent_run_id']}:start",
                "type": "run_started",
                "agentId": str(agent_id),
                "agentName": f"miner {uid}",
                "message": f"Run {r['agent_run_id']} started",
                "timestamp": start_dt,
                "metadata": {"runId": r["agent_run_id"]},
            }
            if activity_type is None or activity_type == "run_started":
                activities.append(run_started)
            if r["ended_at"] is not None:
                end_dt = datetime.fromtimestamp(float(r["ended_at"]), tz=timezone.utc)
                is_failed = int(r["success_tasks"] or 0) == 0 and int(r["total_tasks"] or 0) > 0
                evt_type = "run_failed" if is_failed else "run_completed"
                if r["zero_reason"] == "task_timeout":
                    evt_type = "run_failed"
                if activity_type is None or activity_type == evt_type:
                    activities.append(
                        {
                            "id": f"{r['agent_run_id']}:end",
                            "type": evt_type,
                            "agentId": str(agent_id),
                            "agentName": f"miner {uid}",
                            "message": f"Run {r['agent_run_id']} {evt_type.replace('_', ' ')}",
                            "timestamp": end_dt,
                            "metadata": {"runId": r["agent_run_id"]},
                        }
                    )
        activities = sorted(activities, key=lambda x: x["timestamp"], reverse=True)
        return {"activities": activities, "total": len(activities)}

    async def get_agent_runs_by_round(self, agent_id: str, season: Optional[int]) -> Dict[str, Any]:
        """
        Return all rounds where the agent participated, grouped by round.
        Each round contains:
        - consensus: stake-weighted aggregate (reward, score, time, tasks, rank)
        - validators: each validator's individual run metrics for this agent
        Used exclusively by the "Runs" tab on the agent page.
        """
        uid = int(str(agent_id).replace("agent-", ""))

        # Resolve season: default to the latest available season
        if season is None:
            season = await self.get_latest_season_number()
        if season is None:
            return {"agent_uid": uid, "season": None, "rounds": []}

        # Fetch all round_validator_miners rows for this miner in the requested season,
        # including the validator stake (weight) for the consensus aggregation.
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                            rv.round_id,
                            s.season_number,
                            r.round_number_in_season,
                            rv.round_validator_id,
                            rv.validator_uid,
                            rv.name          AS validator_name,
                            rv.image_url     AS validator_image,
                            rv.validator_hotkey,
                            rv.stake,
                            rvm.weight,
                            rvm.post_consensus_rank,
                            rvm.post_consensus_avg_reward,
                            rvm.post_consensus_avg_eval_score,
                            rvm.post_consensus_avg_eval_time,
                            rvm.post_consensus_avg_eval_cost,
                            rvm.post_consensus_tasks_received,
                            rvm.post_consensus_tasks_success,
                            mer.agent_run_id,
                            mer.started_at        AS run_started_at,
                            mer.ended_at          AS run_ended_at,
                            mer.average_reward    AS run_reward,
                            mer.average_score     AS run_score,
                            mer.average_execution_time AS run_time,
                            mer.total_tasks       AS run_total_tasks,
                            mer.success_tasks     AS run_success_tasks,
                            mer.failed_tasks      AS run_failed_tasks,
                            mer.elapsed_sec       AS run_elapsed_sec,
                            mer.zero_reason       AS run_zero_reason,
                            (
                                SELECT AVG(sub_cost.task_cost)
                                FROM (
                                    SELECT e.evaluation_id, COALESCE(SUM(lu.cost), 0.0) AS task_cost
                                    FROM evaluations e
                                    LEFT JOIN evaluation_llm_usage lu ON lu.evaluation_id = e.evaluation_id
                                    WHERE e.agent_run_id = mer.agent_run_id
                                      AND mer.agent_run_id IS NOT NULL
                                    GROUP BY e.evaluation_id
                                ) sub_cost
                            ) AS run_avg_cost,
                            (
                                SELECT COUNT(DISTINCT t.web_project_id)
                                FROM evaluations e
                                JOIN tasks t ON t.task_id = e.task_id
                                WHERE e.agent_run_id = mer.agent_run_id
                                  AND mer.agent_run_id IS NOT NULL
                            ) AS run_websites_count,
                            (
                                SELECT COUNT(DISTINCT t2.web_project_id)
                                FROM tasks t2
                                JOIN round_validators rv2 ON rv2.round_validator_id = t2.round_validator_id
                                WHERE rv2.round_id::text = rv.round_id::text
                            ) AS round_websites_count
                        FROM round_validator_miners rvm
                        JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                        JOIN rounds r ON r.round_id = rv.round_id
                        JOIN seasons s ON s.season_id = r.season_id
                        LEFT JOIN LATERAL (
                            SELECT *
                            FROM miner_evaluation_runs
                            WHERE round_validator_id = rvm.round_validator_id
                              AND miner_uid = rvm.miner_uid
                            ORDER BY started_at DESC NULLS LAST
                            LIMIT 1
                        ) mer ON TRUE
                        WHERE rvm.miner_uid = :uid
                          AND s.season_number = :season
                        ORDER BY r.round_number_in_season DESC, rv.validator_uid ASC
                        """
                    ),
                    {"uid": uid, "season": season},
                )
            )
            .mappings()
            .all()
        )

        # Group by round_id
        rounds_map: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            rid = int(r["round_id"])
            if rid not in rounds_map:
                rounds_map[rid] = {
                    "round_id": rid,
                    "round_key": f"{int(r['season_number'])}/{int(r['round_number_in_season'])}",
                    "round_label": f"Season {int(r['season_number'])} · Round {int(r['round_number_in_season'])}",
                    "season": int(r["season_number"]),
                    "round_in_season": int(r["round_number_in_season"]),
                    "validators_count": 0,
                    "websites_count": 0,
                    "_validators_raw": [],
                }
            rounds_map[rid]["_validators_raw"].append(dict(r))

        # Compute validators_count from already-fetched rows.
        # websites_count comes from the round-level subquery (total distinct web_project_ids
        # across ALL validators in the round, not just the miner's own run).
        for rid, rd in rounds_map.items():
            vrows = rd["_validators_raw"]
            rd["validators_count"] = len({vr["validator_uid"] for vr in vrows})
            # round_websites_count is the same for all rows in a round; take the first non-null
            rd["websites_count"] = next((int(vr["round_websites_count"]) for vr in vrows if vr.get("round_websites_count") is not None), 0)

        # Build final output per round — only include rounds where at least one
        # validator has a real evaluation run. Rounds without runs are "reused"
        # rounds (same GitHub URL) and should only appear in Historical, not here.
        rounds_out: List[Dict[str, Any]] = []
        for rid, rd in sorted(rounds_map.items(), key=lambda x: x[1]["round_in_season"], reverse=True):
            has_real_run = any(vr.get("agent_run_id") is not None for vr in rd["_validators_raw"])
            if not has_real_run:
                continue
            validator_rows = rd.pop("_validators_raw")

            # Build per-validator entries and collect values for consensus.
            # We compute stake-weighted averages of the ACTUAL run values (run_reward,
            # run_score) so both reward and score are consistently weighted by stake.
            # The stored post_consensus_avg_eval_score is a simple average so we
            # intentionally ignore it for the consensus display.
            validators_out: List[Dict[str, Any]] = []
            weighted_reward_sum = 0.0
            weighted_score_sum = 0.0
            stake_sum_reward = 0.0
            stake_sum_score = 0.0
            post_consensus_rank: Optional[int] = None
            post_consensus_time: Optional[float] = None
            post_consensus_tasks_received = 0
            post_consensus_tasks_success = 0
            post_consensus_avg_cost: Optional[float] = None

            for vr in validator_rows:
                stake = float(vr["stake"] or 0.0)
                run_rew = vr["run_reward"]
                run_score = vr["run_score"]

                # Stake-weighted reward and score from actual per-validator run values
                if run_rew is not None and stake > 0:
                    weighted_reward_sum += float(run_rew) * stake
                    stake_sum_reward += stake
                if run_score is not None and stake > 0:
                    weighted_score_sum += float(run_score) * stake
                    stake_sum_score += stake

                # Take the best (lowest) rank for fallback display
                rk = vr["post_consensus_rank"]
                if rk is not None:
                    if post_consensus_rank is None or int(rk) < post_consensus_rank:
                        post_consensus_rank = int(rk)
                        post_consensus_time = float(vr["post_consensus_avg_eval_time"] or 0.0)
                        post_consensus_tasks_received = int(vr["post_consensus_tasks_received"] or 0)
                        post_consensus_tasks_success = int(vr["post_consensus_tasks_success"] or 0)
                        post_consensus_avg_cost = float(vr["post_consensus_avg_eval_cost"]) if vr["post_consensus_avg_eval_cost"] is not None else None

                run_total = int(vr["run_total_tasks"] or 0)
                run_success = int(vr["run_success_tasks"] or 0)
                run_status = self._derive_agent_run_status(
                    ended_at=vr["run_ended_at"],
                    zero_reason=vr["run_zero_reason"],
                    total_tasks=run_total,
                    successful_tasks=run_success,
                )

                validators_out.append(
                    {
                        "validator_uid": int(vr["validator_uid"]),
                        "validator_name": vr["validator_name"] or f"Validator {int(vr['validator_uid'])}",
                        "validator_hotkey": vr["validator_hotkey"],
                        "validator_image": vr["validator_image"] or "/validators/Other.png",
                        "stake": float(vr["stake"] or 0.0),
                        "weight": float(vr["weight"] or 0.0),
                        "post_consensus_rank": int(vr["post_consensus_rank"]) if vr["post_consensus_rank"] is not None else None,
                        "post_consensus_reward": float(vr["post_consensus_avg_reward"] or 0.0) if vr["post_consensus_avg_reward"] is not None else None,
                        "post_consensus_score": float(vr["post_consensus_avg_eval_score"] or 0.0) if vr["post_consensus_avg_eval_score"] is not None else None,
                        "post_consensus_time": float(vr["post_consensus_avg_eval_time"] or 0.0) if vr["post_consensus_avg_eval_time"] is not None else None,
                        "post_consensus_tasks_received": int(vr["post_consensus_tasks_received"] or 0),
                        "post_consensus_tasks_success": int(vr["post_consensus_tasks_success"] or 0),
                        "run_id": vr["agent_run_id"],
                        "run_status": run_status if vr["agent_run_id"] else None,
                        "run_reward": float(vr["run_reward"] or 0.0) if vr["run_reward"] is not None else None,
                        "run_score": float(vr["run_score"] or 0.0) if vr["run_score"] is not None else None,
                        "run_time": float(vr["run_time"] or 0.0) if vr["run_time"] is not None else None,
                        "run_total_tasks": run_total,
                        "run_success_tasks": run_success,
                        "run_failed_tasks": int(vr["run_failed_tasks"] or 0),
                        "run_elapsed_sec": float(vr["run_elapsed_sec"] or 0.0) if vr["run_elapsed_sec"] is not None else None,
                        "run_avg_cost": float(vr["run_avg_cost"]) if vr["run_avg_cost"] is not None else None,
                        "run_websites_count": int(vr["run_websites_count"] or 0),
                        "run_started_at": datetime.fromtimestamp(float(vr["run_started_at"] or 0.0), tz=timezone.utc).isoformat() if vr["run_started_at"] else None,
                        "run_ended_at": datetime.fromtimestamp(float(vr["run_ended_at"]), tz=timezone.utc).isoformat() if vr["run_ended_at"] else None,
                    }
                )

            consensus_reward = weighted_reward_sum / stake_sum_reward if stake_sum_reward > 0 else None
            consensus_score = weighted_score_sum / stake_sum_score if stake_sum_score > 0 else None
            post_consensus_available = post_consensus_rank is not None or consensus_reward is not None

            rounds_out.append(
                {
                    "round_id": rd["round_id"],
                    "round_key": rd["round_key"],
                    "round_label": rd["round_label"],
                    "season": rd["season"],
                    "round_in_season": rd["round_in_season"],
                    "validators_count": rd["validators_count"],
                    "websites_count": rd["websites_count"],
                    "post_consensus_available": post_consensus_available,
                    "consensus": {
                        "rank": post_consensus_rank,
                        "reward": consensus_reward,
                        "score": consensus_score,
                        "time": post_consensus_time,
                        "tasks_received": post_consensus_tasks_received,
                        "tasks_success": post_consensus_tasks_success,
                        "avg_cost": post_consensus_avg_cost,
                    },
                    "validators": validators_out,
                }
            )

        return {
            "agent_uid": uid,
            "season": season,
            "rounds": rounds_out,
        }

    async def list_agents_catalog(
        self,
        page: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        search: Optional[str],
    ) -> Dict[str, Any]:
        latest = (
            await self.session.execute(
                text(
                    """
                    SELECT r.round_id
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    ORDER BY s.season_number DESC, r.round_number_in_season DESC
                    LIMIT 1
                    """
                )
            )
        ).scalar_one_or_none()
        if latest is None:
            return {"agents": [], "total": 0, "page": page, "limit": limit}
        where = "WHERE rvm.round_id = :rid"
        params: Dict[str, Any] = {"rid": int(latest)}
        if search:
            where += " AND (LOWER(COALESCE(rvm.name,'')) LIKE :q OR CAST(rvm.miner_uid AS TEXT) LIKE :q)"
            params["q"] = f"%{search.lower()}%"
        rows = (
            (
                await self.session.execute(
                    text(
                        f"""
                    WITH ranked AS (
                      SELECT DISTINCT ON (rvm.miner_uid)
                        rvm.miner_uid, rvm.name, rvm.image_url, rvm.is_sota,
                        rvm.best_local_rank, rvm.best_local_reward
                      FROM round_validator_miners rvm
                      {where}
                      ORDER BY rvm.miner_uid, rvm.best_local_rank ASC NULLS LAST, rvm.best_local_reward DESC NULLS LAST
                    )
                    SELECT * FROM ranked
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        agents = [
            {
                "uid": int(r["miner_uid"]),
                "name": r["name"] or f"miner {int(r['miner_uid'])}",
                "ranking": int(r["best_local_rank"] or 9999),
                "reward": float(r["best_local_reward"] or 0.0),
                "isSota": bool(r["is_sota"]),
                "imageUrl": r["image_url"] or f"/miners/{int(r['miner_uid']) % 100}.svg",
                "provider": "autoppia",
            }
            for r in rows
        ]
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "averageReward": lambda a: float(a["reward"]),
            "reward": lambda a: float(a["reward"]),
            "averageScore": lambda a: float(a["reward"]),
            "score": lambda a: float(a["reward"]),
            "ranking": lambda a: int(a["ranking"]),
            "name": lambda a: str(a["name"]).lower(),
        }
        sort_key = key_map.get(sort_by, key_map["reward"])
        agents = sorted(agents, key=sort_key, reverse=reverse)
        total = len(agents)
        start = (page - 1) * limit
        return {"agents": agents[start : start + limit], "total": total, "page": page, "limit": limit}

    async def list_agent_runs_catalog(
        self,
        page: int,
        limit: int,
        round_id: Optional[str],
        validator_id: Optional[str],
        agent_id: Optional[str],
        query: Optional[str],
        status: Optional[str],
        start_date: Optional[datetime],
        end_date: Optional[datetime],
        include_unfinished: bool,
        sort_by: str,
        sort_order: str,
    ) -> Dict[str, Any]:
        where = ["1=1"]
        params: Dict[str, Any] = {}
        # Catalog should only list runs linked to a canonical round row.
        # Shadow/orphan runs (rv.round_id IS NULL) create "Round 0" artifacts in UI.
        where.append("rv.round_id IS NOT NULL")
        if not include_unfinished:
            # Hide in-progress rounds by default: they often carry provisional ranks/scores.
            where.append("LOWER(COALESCE(rr.status, '')) IN ('finished', 'completed', 'evaluating_finished')")
        if agent_id:
            uid = int(str(agent_id).replace("agent-", ""))
            where.append("mer.miner_uid = :uid")
            params["uid"] = uid
        if validator_id:
            vuid = int(str(validator_id).replace("validator-", ""))
            where.append("rv.validator_uid = :vuid")
            params["vuid"] = vuid
        if round_id:
            ref = await self._resolve_round_identifier(round_id)
            where.append("rv.round_id = :rid")
            params["rid"] = int(ref["round_id"])
        if query:
            where.append("(LOWER(COALESCE(rvm.name,'')) LIKE :q OR LOWER(COALESCE(mer.agent_run_id,'')) LIKE :q)")
            params["q"] = f"%{query.lower()}%"
        if start_date:
            where.append("mer.started_at >= :start_ts")
            params["start_ts"] = start_date.timestamp()
        if end_date:
            where.append("mer.started_at <= :end_ts")
            params["end_ts"] = end_date.timestamp()

        rows = (
            (
                await self.session.execute(
                    text(
                        f"""
                    SELECT
                      mer.agent_run_id, mer.miner_uid, mer.miner_hotkey, mer.started_at, mer.ended_at,
                      mer.total_tasks, mer.success_tasks, mer.failed_tasks,
                      mer.average_reward, mer.average_score, mer.average_execution_time, mer.elapsed_sec,
                      mer.zero_reason,
                      run_cost.avg_cost_per_task,
                      websites.websites_count,
                      rv.validator_uid, rv.name AS validator_name, rv.image_url AS validator_image, rv.round_id,
                      rr.round_number_in_season, s.season_number,
                      rvm.name AS miner_name, rvm.image_url AS miner_image,
                      rvm.best_local_rank
                    FROM miner_evaluation_runs mer
                    LEFT JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                    LEFT JOIN rounds rr ON rr.round_id = rv.round_id
                    LEFT JOIN seasons s ON s.season_id = rr.season_id
                    LEFT JOIN round_validator_miners rvm
                      ON rvm.round_validator_id = mer.round_validator_id AND rvm.miner_uid = mer.miner_uid
                    LEFT JOIN LATERAL (
                      SELECT AVG(task_cost) AS avg_cost_per_task
                      FROM (
                        SELECT e.evaluation_id, COALESCE(SUM(lu.cost), 0.0) AS task_cost
                        FROM evaluations e
                        LEFT JOIN evaluation_llm_usage lu ON lu.evaluation_id = e.evaluation_id
                        WHERE e.agent_run_id = mer.agent_run_id
                        GROUP BY e.evaluation_id
                      ) run_eval_costs
                    ) run_cost ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT COUNT(DISTINCT t.web_project_id) AS websites_count
                      FROM evaluations e
                      JOIN tasks t ON t.task_id = e.task_id
                      WHERE e.agent_run_id = mer.agent_run_id
                    ) websites ON TRUE
                    WHERE {" AND ".join(where)}
                    """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        runs: List[Dict[str, Any]] = []
        for r in rows:
            total_tasks = int(r["total_tasks"] or 0)
            successful = int(r["success_tasks"] or 0)
            run_status = self._derive_agent_run_status(
                ended_at=r["ended_at"],
                zero_reason=r["zero_reason"],
                total_tasks=total_tasks,
                successful_tasks=successful,
            )
            if status and run_status != status:
                continue
            round_encoded = 0
            season_num = int(r["season_number"]) if r["season_number"] is not None else None
            round_in_season = int(r["round_number_in_season"]) if r["round_number_in_season"] is not None else None
            if season_num is not None and round_in_season is not None:
                round_encoded = season_num * 10000 + round_in_season
            runs.append(
                {
                    "runId": r["agent_run_id"],
                    "agentId": f"agent-{int(r['miner_uid'])}" if r["miner_uid"] is not None else "agent-0",
                    "agentUid": int(r["miner_uid"]) if r["miner_uid"] is not None else None,
                    "agentHotkey": r["miner_hotkey"],
                    "agentName": r["miner_name"] or (f"miner {int(r['miner_uid'])}" if r["miner_uid"] is not None else "miner"),
                    "agentImage": r["miner_image"] or (f"/miners/{int(r['miner_uid']) % 100}.svg" if r["miner_uid"] is not None else "/miners/0.svg"),
                    "roundId": round_encoded,
                    "season": season_num,
                    "round": round_in_season,
                    "roundKey": f"{season_num}/{round_in_season}" if season_num is not None and round_in_season is not None else None,
                    "validatorId": f"validator-{int(r['validator_uid'])}" if r["validator_uid"] is not None else "validator-0",
                    "validatorName": r["validator_name"] or "Validator",
                    "validatorImage": r["validator_image"] or "/validators/Other.png",
                    "status": run_status,
                    "startTime": datetime.fromtimestamp(float(r["started_at"] or 0.0), tz=timezone.utc).isoformat(),
                    "endTime": datetime.fromtimestamp(float(r["ended_at"]), tz=timezone.utc).isoformat() if r["ended_at"] is not None else None,
                    "totalTasks": total_tasks,
                    "completedTasks": successful,
                    "successfulTasks": successful,
                    "failedTasks": int(r["failed_tasks"] or 0),
                    "averageReward": float(r["average_reward"] or 0.0),
                    "averageScore": float(r["average_score"] or 0.0),
                    "averageCost": (float(r["avg_cost_per_task"]) if r["avg_cost_per_task"] is not None else None),
                    "averageEvaluationTime": float(r["average_execution_time"] or 0.0),
                    "websitesCount": int(r["websites_count"] or 0),
                    "zeroReason": r["zero_reason"],
                }
            )
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "startTime": lambda x: x["startTime"],
            "reward": lambda x: float(x.get("averageReward") or 0.0),
            "score": lambda x: float(x.get("averageReward") or 0.0),
            "duration": lambda x: float(x.get("averageEvaluationTime") or 0.0),
            "ranking": lambda x: int(x.get("ranking") or 9999),
        }
        sort_key = key_map.get(sort_by, key_map["startTime"])
        runs = sorted(runs, key=sort_key, reverse=reverse)
        total = len(runs)
        offset = (page - 1) * limit
        paged = runs[offset : offset + limit]
        facets = {
            "validators": [],
            "rounds": [],
            "agents": [],
            "statuses": [],
        }
        return {"runs": paged, "total": total, "page": page, "limit": limit, "facets": facets}

    async def _run_complete_payload(self, run_id: str) -> Dict[str, Any]:
        run = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      mer.agent_run_id, mer.miner_uid, mer.miner_hotkey, mer.started_at, mer.ended_at,
                      mer.total_tasks, mer.success_tasks, mer.failed_tasks,
                      mer.average_reward, mer.average_execution_time, mer.elapsed_sec,
                      mer.zero_reason,
                      rv.round_validator_id, rv.validator_uid, rv.validator_hotkey,
                      rv.name AS validator_name, rv.image_url AS validator_image, rv.round_id,
                      rv.started_at AS vr_started_at, rv.finished_at AS vr_finished_at,
                      rr.round_number_in_season, rr.status AS round_status, s.season_number,
                      rvm.name AS miner_name, rvm.image_url AS miner_image
                    FROM miner_evaluation_runs mer
                    LEFT JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                    LEFT JOIN rounds rr ON rr.round_id = rv.round_id
                    LEFT JOIN seasons s ON s.season_id = rr.season_id
                    LEFT JOIN round_validator_miners rvm ON rvm.round_validator_id = mer.round_validator_id AND rvm.miner_uid = mer.miner_uid
                    WHERE mer.agent_run_id = :run_id
                    LIMIT 1
                    """
                    ),
                    {"run_id": run_id},
                )
            )
            .mappings()
            .first()
        )
        if not run:
            raise ValueError("Agent run not found")
        source_run_info: Optional[Dict[str, Any]] = None

        def _as_epoch(value: Any) -> Optional[int]:
            if value is None:
                return None
            if isinstance(value, datetime):
                return int(value.timestamp())
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

        evaluations = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT e.evaluation_id, e.task_id, e.evaluation_score, e.evaluation_time, e.reward, e.zero_reason,
                           e.created_at, e.updated_at,
                           t.web_project_id, t.prompt, t.use_case
                    FROM evaluations e
                    LEFT JOIN tasks t ON t.task_id = e.task_id
                    WHERE e.agent_run_id = :run_id
                    ORDER BY e.created_at ASC NULLS LAST, e.evaluation_id ASC
                    """
                    ),
                    {"run_id": run_id},
                )
            )
            .mappings()
            .all()
        )
        eval_items: List[Dict[str, Any]] = []
        for e in evaluations:
            use_case = e["use_case"]
            use_case_name = ""
            if isinstance(use_case, dict):
                use_case_name = str(use_case.get("name") or use_case.get("use_case") or "")
            elif use_case is not None:
                use_case_name = str(use_case)
            start_ts = e["created_at"].isoformat() if e["created_at"] else datetime.now(timezone.utc).isoformat()
            end_ts = e["updated_at"].isoformat() if e["updated_at"] else None
            score = float(e["evaluation_score"] or 0.0)
            eval_items.append(
                {
                    "evaluationId": e["evaluation_id"],
                    "taskId": e["task_id"],
                    "website": str(e["web_project_id"] or "unknown"),
                    "useCase": use_case_name or "UNKNOWN",
                    "prompt": e["prompt"] or "",
                    "status": "completed" if score > 0 else "failed",
                    "eval_score": score,
                    "eval_time": float(e["evaluation_time"] or 0.0),
                    "reward": float(e["reward"] or 0.0),
                    "startTime": start_ts,
                    "endTime": end_ts,
                    "zeroReason": e["zero_reason"],
                }
            )

        by_website: Dict[str, Dict[str, Any]] = {}
        for item in eval_items:
            site = item["website"]
            entry = by_website.setdefault(
                site,
                {
                    "website": site,
                    "tasks": 0,
                    "successful": 0,
                    "failed": 0,
                    "averageScore": 0.0,
                    "averageDuration": 0.0,
                },
            )
            entry["tasks"] += 1
            if float(item["eval_score"] or 0.0) > 0:
                entry["successful"] += 1
            else:
                entry["failed"] += 1
            entry["averageScore"] += float(item["eval_score"] or 0.0)
            entry["averageDuration"] += float(item["eval_time"] or 0.0)
        performance = []
        for site, entry in by_website.items():
            tasks = int(entry["tasks"])
            performance.append(
                {
                    "website": site,
                    "tasks": tasks,
                    "successful": int(entry["successful"]),
                    "failed": int(entry["failed"]),
                    "averageScore": (float(entry["averageScore"]) / tasks) if tasks > 0 else 0.0,
                    "averageDuration": (float(entry["averageDuration"]) / tasks) if tasks > 0 else 0.0,
                }
            )

        total_tasks = int(run["total_tasks"] or 0)
        successful_tasks = int(run["success_tasks"] or 0)
        failed_tasks = int(run["failed_tasks"] or max(total_tasks - successful_tasks, 0))
        run_status = "completed"
        if run["ended_at"] is None:
            run_status = "running"
        elif str(run["zero_reason"] or "") == "task_timeout":
            run_status = "failed"
        elif successful_tasks == 0 and total_tasks > 0:
            run_status = "failed"
        round_id = 0
        if run["season_number"] is not None and run["round_number_in_season"] is not None:
            round_id = int(run["season_number"]) * 10000 + int(run["round_number_in_season"])

        run_data = {
            "runId": run["agent_run_id"],
            "agentId": f"agent-{int(run['miner_uid'])}" if run["miner_uid"] is not None else "agent-0",
            "agentUid": int(run["miner_uid"]) if run["miner_uid"] is not None else None,
            "agentHotkey": run["miner_hotkey"],
            "agentName": run["miner_name"] or (f"miner {int(run['miner_uid'])}" if run["miner_uid"] is not None else "miner"),
            "agentImage": run["miner_image"] or (f"/miners/{int(run['miner_uid']) % 100}.svg" if run["miner_uid"] is not None else "/miners/0.svg"),
            "roundId": round_id,
            "season_number": int(run["season_number"]) if run["season_number"] is not None else None,
            "round_number_in_season": int(run["round_number_in_season"]) if run["round_number_in_season"] is not None else None,
            "validatorRoundId": str(run["round_validator_id"]) if run["round_validator_id"] is not None else None,
            "roundNumber": int(run["round_number_in_season"]) if run["round_number_in_season"] is not None else round_id,
            "validatorId": f"validator-{int(run['validator_uid'])}" if run["validator_uid"] is not None else "validator-0",
            "validatorName": run["validator_name"] or "Validator",
            "validatorImage": run["validator_image"] or "/validators/Other.png",
            "startTime": datetime.fromtimestamp(float(run["started_at"] or 0.0), tz=timezone.utc).isoformat(),
            "endTime": datetime.fromtimestamp(float(run["ended_at"]), tz=timezone.utc).isoformat() if run["ended_at"] is not None else "",
            "status": run_status,
            "totalTasks": total_tasks,
            "completedTasks": successful_tasks,
            "successfulTasks": successful_tasks,
            "failedTasks": failed_tasks,
            "reward": float(run["average_reward"] or 0.0),
            "duration": int(float(run["elapsed_sec"] or 0.0)),
            "overallReward": float(run["average_reward"] or 0.0),
            "averageEvaluationTime": float(run["average_execution_time"] or 0.0),
            "totalWebsites": len(performance),
            "websites": [],
            "tasks": [],
            "metadata": {},
            "zeroReason": run["zero_reason"],
        }
        personas = {
            "round": {
                "id": round_id,
                "name": f"Season {int(run['season_number'] or 0)} Round {int(run['round_number_in_season'] or 0)}",
                "status": "completed" if str(run["round_status"] or "finished") in ("finished", "completed") else "active",
                "startTime": run_data["startTime"],
                "endTime": run_data["endTime"] or None,
                "roundId": (f"{int(run['season_number'])}/{int(run['round_number_in_season'])}" if run["season_number"] is not None and run["round_number_in_season"] is not None else None),
                "season_number": int(run["season_number"]) if run["season_number"] is not None else None,
                "round_number_in_season": int(run["round_number_in_season"]) if run["round_number_in_season"] is not None else None,
                "season": int(run["season_number"]) if run["season_number"] is not None else None,
                "round": int(run["round_number_in_season"]) if run["round_number_in_season"] is not None else None,
                "validatorRoundId": str(run["round_validator_id"]) if run["round_validator_id"] is not None else None,
                "roundNumber": int(run["round_number_in_season"]) if run["round_number_in_season"] is not None else round_id,
                "startEpoch": _as_epoch(run.get("vr_started_at")),
                "endEpoch": _as_epoch(run.get("vr_finished_at")),
            },
            "validator": {
                "id": run_data["validatorId"],
                "name": run_data["validatorName"],
                "image": run_data["validatorImage"],
                "description": "",
                "website": "",
                "github": "",
            },
            "agent": {
                "id": run_data["agentId"],
                "uid": run_data["agentUid"],
                "hotkey": run_data["agentHotkey"],
                "name": run_data["agentName"],
                "type": "autoppia",
                "image": run_data["agentImage"],
                "description": "",
            },
        }
        statistics = {
            "totalTasks": total_tasks,
            "websites": len(performance),
            "avg_reward": float(run["average_reward"] or 0.0),
            "avg_time": float(run["average_execution_time"] or 0.0),
            "successfulTasks": successful_tasks,
            "failedTasks": failed_tasks,
            "performanceByWebsite": performance,
        }
        summary = {
            "runId": run_data["runId"],
            "agentId": run_data["agentId"],
            "agentUid": run_data["agentUid"],
            "agentHotkey": run_data["agentHotkey"],
            "agentName": run_data["agentName"],
            "roundId": run_data["roundId"],
            "validatorId": run_data["validatorId"],
            "startTime": run_data["startTime"],
            "endTime": run_data["endTime"] or None,
            "status": run_data["status"],
            "overallReward": run_data["overallReward"],
            "totalTasks": run_data["totalTasks"],
            "successfulTasks": run_data["successfulTasks"],
            "failedTasks": run_data["failedTasks"],
            "duration": run_data["duration"],
            "topPerformingWebsite": (
                {
                    "website": performance[0]["website"],
                    "averageEvalScore": performance[0]["averageScore"],
                    "tasks": performance[0]["tasks"],
                }
                if performance
                else {"website": "N/A", "averageEvalScore": 0.0, "tasks": 0}
            ),
            "topPerformingUseCase": {"useCase": "N/A", "averageEvalScore": 0.0, "tasks": 0},
            "recentActivity": [],
        }
        timeline = [
            {
                "timestamp": run_data["startTime"],
                "type": "run_started",
                "message": f"Run {run_data['runId']} started",
                "taskId": None,
                "metadata": {},
            }
        ]
        for item in eval_items:
            timeline.append(
                {
                    "timestamp": item["startTime"],
                    "type": "task_completed" if item["status"] == "completed" else "task_failed",
                    "message": f"{item['website']} {item['useCase']} {item['status']}",
                    "taskId": item["taskId"],
                    "metadata": {},
                }
            )
        if run_data["endTime"]:
            timeline.append(
                {
                    "timestamp": run_data["endTime"],
                    "type": "run_completed" if run_data["status"] == "completed" else "run_failed",
                    "message": f"Run {run_data['runId']} ended",
                    "taskId": None,
                    "metadata": {},
                }
            )
        logs_rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT task_id, created_at, payload_ref, payload_size
                    FROM task_execution_logs
                    WHERE agent_run_id = :run_id
                    ORDER BY created_at ASC
                    LIMIT 1000
                    """
                    ),
                    {"run_id": run_id},
                )
            )
            .mappings()
            .all()
        )
        logs = [
            {
                "timestamp": (row["created_at"] or datetime.now(timezone.utc)).isoformat(),
                "level": "info",
                "message": f"Task log uploaded for task {row['task_id']}",
                "metadata": {
                    "taskId": row["task_id"],
                    "payloadRef": row["payload_ref"],
                    "payloadUrl": build_public_url(row["payload_ref"]) if row.get("payload_ref") else None,
                    "payloadSize": int(row["payload_size"] or 0),
                },
            }
            for row in logs_rows
        ]
        metrics = {
            "cpu": [{"timestamp": run_data["startTime"], "value": 0.0}],
            "memory": [{"timestamp": run_data["startTime"], "value": 0.0}],
            "network": [{"timestamp": run_data["startTime"], "value": 0.0}],
            "duration": run_data["duration"],
            "peakCpu": 0.0,
            "peakMemory": 0.0,
            "totalNetworkTraffic": 0,
        }
        info = {
            "agentRunId": run_data["runId"],
            "round": personas["round"],
            "validator": personas["validator"],
            "miner": personas["agent"],
            "zeroReason": run_data.get("zeroReason"),
            "isReused": run_data.get("isReused"),
            "reusedFromAgentRunId": run_data.get("reusedFromAgentRunId"),
            "reusedFrom": source_run_info,
        }
        return {
            "run": run_data,
            "personas": personas,
            "statistics": statistics,
            "summary": summary,
            "tasks": {"tasks": eval_items, "total": len(eval_items), "page": 1, "limit": len(eval_items) or 1},
            "timeline": timeline,
            "logs": {"entries": logs, "total": len(logs)},
            "metrics": metrics,
            "complete": {"statistics": statistics, "evaluations": eval_items, "info": info},
        }

    async def get_agent_run_complete_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["complete"]

    async def get_agent_run_detail_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["run"]

    async def get_agent_run_personas_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["personas"]

    async def get_agent_run_statistics_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["statistics"]

    async def get_agent_run_summary_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["summary"]

    async def get_agent_run_tasks_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["tasks"]

    async def get_agent_run_timeline_data(self, run_id: str) -> List[Dict[str, Any]]:
        payload = await self._run_complete_payload(run_id)
        return payload["timeline"]

    async def get_agent_run_logs_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["logs"]

    async def get_agent_run_metrics_data(self, run_id: str) -> Dict[str, Any]:
        payload = await self._run_complete_payload(run_id)
        return payload["metrics"]

    async def compare_agent_runs_data(self, run_ids: List[str]) -> Dict[str, Any]:
        complete = []
        for rid in run_ids:
            try:
                payload = await self._run_complete_payload(rid)
                run = payload["run"]
                complete.append(run)
            except ValueError:
                continue
        if not complete:
            return {"bestReward": "", "fastest": "", "mostTasks": "", "bestSuccessRate": "", "runs": []}
        best_reward = max(complete, key=lambda x: float(x.get("overallReward") or 0.0))
        fastest = min(complete, key=lambda x: float(x.get("duration") or 0.0))
        most_tasks = max(complete, key=lambda x: int(x.get("totalTasks") or 0))
        best_success = max(
            complete,
            key=lambda x: (float(x.get("successfulTasks") or 0.0) / float(x.get("totalTasks") or 1.0)) if int(x.get("totalTasks") or 0) > 0 else 0.0,
        )
        return {
            "bestReward": best_reward.get("runId", ""),
            "fastest": fastest.get("runId", ""),
            "mostTasks": most_tasks.get("runId", ""),
            "bestSuccessRate": best_success.get("runId", ""),
            "runs": complete,
        }
