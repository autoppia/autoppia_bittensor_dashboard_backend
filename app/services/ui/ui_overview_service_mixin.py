from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.services.metagraph_service import MetagraphError, get_validator_data


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

        metrics_source = latest_finished or latest_any
        metrics_season = int(metrics_source["season_number"])
        metrics_round_id = int(metrics_source["round_id"])

        current_season = int(latest_any["season_number"])
        current_round_in_season = int(latest_any["round_number_in_season"])

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
            "leader": leader_payload,
            "season": current_season,
            "round": current_round_in_season,
            "totalMiners": len(miners),
            "tasksPerValidator": int(tasks_per_validator or 0),
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
            status_label = "Evaluating" if is_active else "Waiting"
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
        return {
            "status": "healthy",
            "message": "Network operational",
            "lastChecked": datetime.now(timezone.utc).isoformat(),
            "activeValidators": len(validators),
            "networkLatency": 0,
        }

    async def get_overview_recent_activity(self, limit: int) -> List[Dict[str, Any]]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT rr.round_id, rr.started_at, rr.ended_at, s.season_number, rr.round_number_in_season
                    FROM rounds rr
                    JOIN seasons s ON s.season_id = rr.season_id
                    ORDER BY rr.ended_at DESC NULLS LAST, rr.started_at DESC NULLS LAST
                    LIMIT :lim
                    """
                    ),
                    {"lim": max(limit, 1)},
                )
            )
            .mappings()
            .all()
        )
        acts: List[Dict[str, Any]] = []
        for r in rows:
            if r["started_at"]:
                acts.append(
                    {
                        "id": f"round-{int(r['round_id'])}-start",
                        "type": "round_started",
                        "message": f"Season {int(r['season_number'])} Round {int(r['round_number_in_season'])} started",
                        "timestamp": r["started_at"].isoformat(),
                        "metadata": {"roundId": int(r["round_id"])},
                    }
                )
            if r["ended_at"]:
                acts.append(
                    {
                        "id": f"round-{int(r['round_id'])}-end",
                        "type": "round_ended",
                        "message": f"Season {int(r['season_number'])} Round {int(r['round_number_in_season'])} ended",
                        "timestamp": r["ended_at"].isoformat(),
                        "metadata": {"roundId": int(r["round_id"])},
                    }
                )
        acts = sorted(acts, key=lambda a: a["timestamp"], reverse=True)
        return acts[:limit]

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
