from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text


class UIOverviewServiceMixin:
    async def get_overview_metrics(self) -> Dict[str, Any]:
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
                "topMinerUid": None,
                "topMinerName": None,
                "topReward": 0.0,
                "totalWebsites": 14,
                "totalValidators": 0,
                "totalMiners": 0,
                "tasksPerValidator": 0,
                "totalTasksPerValidator": 0,
                "minerList": [],
                "currentRound": 0,
                "currentSeason": None,
                "currentRoundInSeason": None,
                "metricsRound": 0,
                "metricsSeason": None,
                "metricsRoundInSeason": None,
                "subnetVersion": "12.2.0",
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            }

        metrics_source = latest_finished or latest_any
        metrics_season = int(metrics_source["season_number"])
        metrics_round_in_season = int(metrics_source["round_number_in_season"])
        metrics_round_id = int(metrics_source["round_id"])

        current_season = int(latest_any["season_number"])
        current_round_in_season = int(latest_any["round_number_in_season"])

        winner = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT ro.winner_miner_uid, rvm.name, ro.winner_score
                    FROM round_outcomes ro
                    LEFT JOIN round_validator_miners rvm
                      ON rvm.round_id = ro.round_id
                     AND rvm.miner_uid = ro.winner_miner_uid
                    WHERE ro.round_id = :round_id
                    LIMIT 1
                    """
                    ),
                    {"round_id": metrics_round_id},
                )
            )
            .mappings()
            .first()
        )
        if not winner:
            winner = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT ro.winner_miner_uid, rvm.name, ro.winner_score
                        FROM round_outcomes ro
                        JOIN rounds rr ON rr.round_id = ro.round_id
                        JOIN seasons ss ON ss.season_id = rr.season_id
                        LEFT JOIN round_validator_miners rvm
                          ON rvm.round_id = ro.round_id
                         AND rvm.miner_uid = ro.winner_miner_uid
                        ORDER BY ss.season_number DESC, rr.round_number_in_season DESC
                        LIMIT 1
                        """
                        )
                    )
                )
                .mappings()
                .first()
            )

        total_validators = (await self.session.execute(text("SELECT COUNT(DISTINCT validator_uid) FROM round_validators WHERE round_id=:rid"), {"rid": metrics_round_id})).scalar_one()
        total_miners_active = (
            await self.session.execute(
                text(
                    """
                    WITH ranked AS (
                      SELECT DISTINCT ON (miner_uid)
                        miner_uid,
                        COALESCE(
                          effective_tasks_received,
                          post_consensus_tasks_received,
                          local_tasks_received,
                          0
                        ) AS tasks_received
                      FROM round_validator_miners
                      WHERE round_id = :rid
                      ORDER BY miner_uid, post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                    )
                    SELECT COUNT(*) FROM ranked WHERE tasks_received > 0
                    """
                ),
                {"rid": metrics_round_id},
            )
        ).scalar_one()
        outcome_counts = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT validators_count, miners_evaluated
                        FROM round_outcomes
                        WHERE round_id = :rid
                        LIMIT 1
                        """
                    ),
                    {"rid": metrics_round_id},
                )
            )
            .mappings()
            .first()
        )
        miners = (
            (
                await self.session.execute(
                    text(
                        """
                        WITH ranked AS (
                          SELECT DISTINCT ON (miner_uid)
                            miner_uid AS uid,
                            COALESCE(name, 'miner '||miner_uid::text) AS name,
                            COALESCE(
                              effective_tasks_received,
                              post_consensus_tasks_received,
                              local_tasks_received,
                              0
                            ) AS tasks_received
                          FROM round_validator_miners
                          WHERE round_id = :rid
                          ORDER BY miner_uid, post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                        )
                        SELECT uid, name
                        FROM ranked
                        WHERE tasks_received > 0
                        ORDER BY uid
                        """
                    ),
                    {"rid": metrics_round_id},
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
        final_total_validators = int((outcome_counts or {}).get("validators_count") or total_validators or 0)
        final_total_miners = int((outcome_counts or {}).get("miners_evaluated") or total_miners_active or 0)

        return {
            "topMinerUid": int(winner["winner_miner_uid"]) if winner and winner["winner_miner_uid"] is not None else None,
            "topMinerName": winner["name"] if winner else None,
            "topReward": float(winner["winner_score"] or 0.0) if winner else 0.0,
            "totalWebsites": 14,
            "totalValidators": final_total_validators,
            "totalMiners": final_total_miners,
            "tasksPerValidator": int(tasks_per_validator or 0),
            "totalTasksPerValidator": int(tasks_per_validator or 0),
            "minerList": [dict(m) for m in miners],
            "currentRound": int((current_season * 10000) + current_round_in_season),
            "currentSeason": current_season,
            "currentRoundInSeason": current_round_in_season,
            "metricsRound": metrics_round_in_season,
            "metricsSeason": metrics_season,
            "metricsRoundInSeason": metrics_round_in_season,
            "subnetVersion": "12.2.0",
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
                "stake": float(r["stake"] or 0.0),
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
                    SELECT s.season_number, r.round_number_in_season, r.ended_at,
                           ro.winner_miner_uid, ro.winner_score
                    FROM round_outcomes ro
                    JOIN rounds r ON r.round_id = ro.round_id
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
                    "subnet36": float(r["winner_score"] or 0.0),
                    "post_consensus_reward": float(r["winner_score"] or 0.0),
                    "winnerUid": int(r["winner_miner_uid"]) if r["winner_miner_uid"] is not None else None,
                    "winnerName": None,
                    "timestamp": (r["ended_at"] or datetime.now(timezone.utc)).isoformat(),
                    "post_consensus_eval_score": float(r["winner_score"] or 0.0),
                    "post_consensus_eval_time": 0.0,
                    "score": float(r["winner_score"] or 0.0),
                    "time": 0.0,
                }
            )
        return entries, "all"

    async def get_overview_statistics(self) -> Dict[str, Any]:
        total_validators = (await self.session.execute(text("SELECT COUNT(DISTINCT validator_uid) FROM round_validators"))).scalar_one()
        total_miners = (await self.session.execute(text("SELECT COUNT(DISTINCT miner_uid) FROM round_validator_miners"))).scalar_one()
        total_tasks = (await self.session.execute(text("SELECT COUNT(*) FROM tasks"))).scalar_one()
        avg_score = (await self.session.execute(text("SELECT COALESCE(AVG(post_consensus_avg_reward),0) FROM round_validator_miners"))).scalar_one()
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
