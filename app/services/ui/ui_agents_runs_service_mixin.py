from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from sqlalchemy import text


class UIAgentsRunsServiceMixin:
    async def get_agent_detail(self, miner_uid: int, season: Optional[int], round_in_season: Optional[int]) -> Dict[str, Any]:
        if season is None or round_in_season is None:
            latest = await self.get_latest_round_top_miner()
            if latest:
                season = latest["season"]
                round_in_season = latest["round"]
        if season is None or round_in_season is None:
            raise ValueError(f"Agent {miner_uid} not found")

        ref = await self._round_ref(season, round_in_season)
        if not ref:
            raise ValueError(f"Agent {miner_uid} not found")
        round_id = int(ref["round_id"])

        miner_rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT *
                    FROM round_validator_miners
                    WHERE round_id = :round_id AND miner_uid = :miner_uid
                    ORDER BY round_validator_id ASC
                    """
                    ),
                    {"round_id": round_id, "miner_uid": miner_uid},
                )
            )
            .mappings()
            .all()
        )
        if not miner_rows:
            raise ValueError(f"Agent {miner_uid} not found")

        first = miner_rows[0]
        validators = []
        for row in miner_rows:
            vr = (
                (
                    await self.session.execute(
                        text("SELECT validator_uid, validator_hotkey, name FROM round_validators WHERE round_validator_id = :id"),
                        {"id": int(row["round_validator_id"])},
                    )
                )
                .mappings()
                .first()
            )
            if vr:
                validators.append({"uid": int(vr["validator_uid"]), "hotkey": vr["validator_hotkey"], "name": vr["name"]})

        total_tasks = int(sum(int(r["local_tasks_received"] or 0) for r in miner_rows))
        success_tasks = int(sum(int(r["local_tasks_success"] or 0) for r in miner_rows))
        failed_tasks = max(total_tasks - success_tasks, 0)
        avg_time = (sum(float(r["local_avg_eval_time"] or 0.0) for r in miner_rows) / len(miner_rows)) if miner_rows else 0.0
        score = float(first["effective_reward"] or first["post_consensus_avg_reward"] or first["local_avg_reward"] or 0.0)
        rank = int(first["effective_rank"] or first["post_consensus_rank"] or first["local_rank"] or 0)

        runs_count = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM miner_evaluation_runs WHERE miner_uid = :uid"),
                {"uid": miner_uid},
            )
        ).scalar_one()
        success_runs = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM miner_evaluation_runs WHERE miner_uid = :uid AND success_tasks > 0"),
                {"uid": miner_uid},
            )
        ).scalar_one()
        rounds_participated = (
            await self.session.execute(
                text("SELECT COUNT(DISTINCT round_id) FROM round_validator_miners WHERE miner_uid = :uid"),
                {"uid": miner_uid},
            )
        ).scalar_one()
        rounds_won = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM round_outcomes WHERE winner_miner_uid = :uid"),
                {"uid": miner_uid},
            )
        ).scalar_one()

        run_ctx = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      mer.agent_run_id,
                      mer.is_reused,
                      mer.reused_from_agent_run_id,
                      mer.zero_reason
                    FROM miner_evaluation_runs mer
                    WHERE mer.miner_uid = :uid
                      AND mer.round_validator_id IN (
                        SELECT rv.round_validator_id
                        FROM round_validators rv
                        WHERE rv.round_id = :round_id
                      )
                    ORDER BY mer.started_at DESC NULLS LAST, mer.created_at DESC NULLS LAST
                    LIMIT 1
                    """
                    ),
                    {"uid": miner_uid, "round_id": round_id},
                )
            )
            .mappings()
            .first()
        )
        is_reused = bool(run_ctx["is_reused"]) if run_ctx else False
        run_agent_run_id = run_ctx["agent_run_id"] if run_ctx else None
        reused_from_agent_run_id = run_ctx["reused_from_agent_run_id"] if run_ctx else None
        zero_reason = run_ctx["zero_reason"] if run_ctx else None
        source_agent_run_id = reused_from_agent_run_id or run_agent_run_id

        reused_from_round: Optional[str] = None
        if reused_from_agent_run_id:
            source_round = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT s.season_number, r.round_number_in_season
                        FROM miner_evaluation_runs mer
                        JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                        JOIN rounds r ON r.round_id = rv.round_id
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE mer.agent_run_id = :agent_run_id
                        LIMIT 1
                        """
                        ),
                        {"agent_run_id": reused_from_agent_run_id},
                    )
                )
                .mappings()
                .first()
            )
            if source_round:
                reused_from_round = f"{int(source_round['season_number'])}/{int(source_round['round_number_in_season'])}"

        performance_by_website: List[Dict[str, Any]] = []

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

        if source_agent_run_id:
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
                          e.evaluation_time
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
            by_website: Dict[str, Dict[str, Any]] = {}
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
            for entry in by_website.values():
                tasks_received = int(entry["tasks_received"] or 0)
                tasks_success = int(entry["tasks_success"] or 0)
                entry["success_rate"] = (tasks_success / tasks_received) if tasks_received > 0 else 0.0
                performance_by_website.append(entry)
            performance_by_website.sort(key=lambda x: x["tasks_received"], reverse=True)

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
                "currentScore": score,
                "currentTopScore": score,
                "currentRank": rank,
                "bestRankEver": rank,
                "bestRankRoundId": season * 10000 + round_in_season,
                "roundsParticipated": int(rounds_participated or 0),
                "roundsWon": int(rounds_won or 0),
                "alphaWonInPrizes": 0.0,
                "taoWonInPrizes": 0.0,
                "bestRoundScore": score,
                "bestRoundId": season * 10000 + round_in_season,
                "averageResponseTime": round(avg_time, 2),
                "totalTasks": total_tasks,
                "completedTasks": success_tasks,
                "lastSeen": None,
                "createdAt": None,
                "updatedAt": None,
            },
            "scoreRoundData": [],
            "availableRounds": [season * 10000 + round_in_season],
            "performanceByWebsite": performance_by_website,
            "avg_cost_per_task": None,
            "is_reused": is_reused,
            "reused_from_agent_run_id": reused_from_agent_run_id,
            "reused_from_round": reused_from_round,
            "zero_reason": zero_reason,
            "roundMetrics": {
                "roundId": season * 10000 + round_in_season,
                "score": score,
                "topScore": score,
                "rank": rank,
                "totalRuns": len(miner_rows),
                "totalValidators": len(validators),
                "validatorUids": [v["uid"] for v in validators if v["uid"] is not None],
                "validators": validators,
                "totalTasks": total_tasks,
                "completedTasks": success_tasks,
                "failedTasks": failed_tasks,
                "successRate": (success_tasks / total_tasks) if total_tasks > 0 else 0.0,
                "averageResponseTime": round(avg_time, 2),
                "performanceByWebsite": performance_by_website,
                "avgCostPerTask": None,
                "isReused": is_reused,
                "reusedFromAgentRunId": reused_from_agent_run_id,
                "reusedFromRound": reused_from_round,
                "zeroReason": zero_reason,
            },
        }

    async def get_miner_historical(self, miner_uid: int, season: Optional[int]) -> Dict[str, Any]:
        where = "WHERE miner_uid = :uid"
        params: Dict[str, Any] = {"uid": miner_uid}
        if season is not None:
            where += " AND round_id IN (SELECT r.round_id FROM rounds r JOIN seasons s ON s.season_id=r.season_id WHERE s.season_number=:season)"
            params["season"] = season
        rows = (
            (
                await self.session.execute(
                    text(
                        f"""
                    SELECT round_id, post_consensus_rank, post_consensus_avg_reward,
                           post_consensus_avg_eval_score, post_consensus_avg_eval_time,
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
                        text("SELECT s.season_number, ro.round_number_in_season FROM rounds ro JOIN seasons s ON s.season_id=ro.season_id WHERE ro.round_id=:rid"),
                        {"rid": int(r["round_id"])},
                    )
                )
                .mappings()
                .first()
            )
            rounds_history.append(
                {
                    "round": f"{int(sr['season_number'])}/{int(sr['round_number_in_season'])}" if sr else str(int(r["round_id"])),
                    "post_consensus_rank": r["post_consensus_rank"],
                    "post_consensus_avg_reward": float(r["post_consensus_avg_reward"] or 0.0),
                    "post_consensus_avg_eval_score": float(r["post_consensus_avg_eval_score"] or 0.0),
                    "post_consensus_avg_eval_time": float(r["post_consensus_avg_eval_time"] or 0.0),
                    "tasks_received": int(r["post_consensus_tasks_received"] or 0),
                    "tasks_success": int(r["post_consensus_tasks_success"] or 0),
                    "tasks_failed": max(int(r["post_consensus_tasks_received"] or 0) - int(r["post_consensus_tasks_success"] or 0), 0),
                    "is_winner": (r["post_consensus_rank"] == 1),
                    "validators_count": 1,
                    "subnet_price": float(r["subnet_price"] or 0.0),
                    "weight": float(r["weight"] or 0.0),
                }
            )

        best = min([x["post_consensus_rank"] for x in rounds_history if x["post_consensus_rank"] is not None] or [None], default=None)
        best_score = max([x["post_consensus_avg_reward"] for x in rounds_history] or [0.0])
        total_tasks = sum(x["tasks_received"] for x in rounds_history)
        total_success = sum(x["tasks_success"] for x in rounds_history)
        rounds_won = sum(1 for x in rounds_history if x["is_winner"])

        return {
            "miner": {
                "uid": miner_uid,
                "name": f"miner {miner_uid}",
                "hotkey": None,
                "image": f"/miners/{miner_uid % 100}.svg",
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
                "bestScore": best_score,
                "bestScoreRound": rounds_history[0]["round"],
                "bestRank": best,
                "bestRankRound": rounds_history[0]["round"],
                "averageScore": sum(x["post_consensus_avg_reward"] for x in rounds_history) / len(rounds_history),
                "totalAlphaEarned": 0.0,
                "totalTaoEarned": 0.0,
                "distinctGithubUrls": 0,
            },
            "performanceByWebsite": [],
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
                           mer.meta, mer.is_reused, mer.reused_from_agent_run_id, mer.zero_reason
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
                           mer.meta, mer.is_reused, mer.reused_from_agent_run_id, mer.zero_reason
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
            "metadata": row["meta"] or {},
            "is_reused": bool(row["is_reused"]),
            "reused_from_agent_run_id": row["reused_from_agent_run_id"],
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
                    "score": float(r["average_reward"] or 0.0),
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
            "currentScore": current_score,
            "worstScore": worst_score,
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
                    SELECT mer.agent_run_id, mer.started_at, mer.ended_at, mer.elapsed_sec,
                           mer.total_tasks, mer.success_tasks, mer.average_reward,
                           mer.round_validator_id, mer.zero_reason
                    FROM miner_evaluation_runs mer
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
                        SELECT s.season_number, rr.round_number_in_season, rv.validator_uid
                        FROM round_validators rv
                        JOIN rounds rr ON rr.round_id = rv.round_id
                        JOIN seasons s ON s.season_id = rr.season_id
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
            round_id = (int(rv["season_number"]) * 10000 + int(rv["round_number_in_season"])) if rv else 0
            total_tasks = int(r["total_tasks"] or 0)
            success_tasks = int(r["success_tasks"] or 0)
            status = "completed"
            if r["ended_at"] is None:
                status = "running"
            elif r["zero_reason"] == "task_timeout":
                status = "timeout"
            elif success_tasks == 0 and total_tasks > 0:
                status = "failed"
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
                    "score": float(r["average_reward"] or 0.0),
                    "duration": int(float(r["elapsed_sec"] or 0.0)),
                    "ranking": None,
                    "tasks": [],
                    "metadata": {},
                }
            )
        return {
            "runs": runs,
            "total": int(total or 0),
            "page": page,
            "limit": limit,
            "availableRounds": sorted(list({int(run["roundId"]) for run in runs if int(run["roundId"]) > 0}), reverse=True),
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
                        rvm.effective_rank, rvm.effective_reward
                      FROM round_validator_miners rvm
                      {where}
                      ORDER BY rvm.miner_uid, rvm.effective_rank ASC NULLS LAST, rvm.effective_reward DESC NULLS LAST
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
                "ranking": int(r["effective_rank"] or 9999),
                "score": float(r["effective_reward"] or 0.0),
                "isSota": bool(r["is_sota"]),
                "imageUrl": r["image_url"] or f"/miners/{int(r['miner_uid']) % 100}.svg",
                "provider": "autoppia",
            }
            for r in rows
        ]
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "averageScore": lambda a: float(a["score"]),
            "score": lambda a: float(a["score"]),
            "ranking": lambda a: int(a["ranking"]),
            "name": lambda a: str(a["name"]).lower(),
        }
        sort_key = key_map.get(sort_by, key_map["score"])
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
        sort_by: str,
        sort_order: str,
    ) -> Dict[str, Any]:
        where = ["1=1"]
        params: Dict[str, Any] = {}
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
                      mer.average_reward, mer.average_execution_time, mer.elapsed_sec,
                      mer.zero_reason, mer.is_reused, mer.reused_from_agent_run_id,
                      rv.validator_uid, rv.name AS validator_name, rv.image_url AS validator_image, rv.round_id,
                      rr.round_number_in_season, s.season_number,
                      rvm.name AS miner_name, rvm.image_url AS miner_image,
                      rvm.effective_rank
                    FROM miner_evaluation_runs mer
                    LEFT JOIN round_validators rv ON rv.round_validator_id = mer.round_validator_id
                    LEFT JOIN rounds rr ON rr.round_id = rv.round_id
                    LEFT JOIN seasons s ON s.season_id = rr.season_id
                    LEFT JOIN round_validator_miners rvm
                      ON rvm.round_validator_id = mer.round_validator_id AND rvm.miner_uid = mer.miner_uid
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
            run_status = "completed"
            if r["ended_at"] is None:
                run_status = "running"
            elif str(r["zero_reason"] or "") == "task_timeout":
                run_status = "timeout"
            elif successful == 0 and total_tasks > 0:
                run_status = "failed"
            if status and run_status != status:
                continue
            round_encoded = 0
            if r["season_number"] is not None and r["round_number_in_season"] is not None:
                round_encoded = int(r["season_number"]) * 10000 + int(r["round_number_in_season"])
            runs.append(
                {
                    "runId": r["agent_run_id"],
                    "agentId": f"agent-{int(r['miner_uid'])}" if r["miner_uid"] is not None else "agent-0",
                    "agentUid": int(r["miner_uid"]) if r["miner_uid"] is not None else None,
                    "agentHotkey": r["miner_hotkey"],
                    "agentName": r["miner_name"] or (f"miner {int(r['miner_uid'])}" if r["miner_uid"] is not None else "miner"),
                    "agentImage": r["miner_image"] or (f"/miners/{int(r['miner_uid']) % 100}.svg" if r["miner_uid"] is not None else "/miners/0.svg"),
                    "roundId": round_encoded,
                    "validatorId": f"validator-{int(r['validator_uid'])}" if r["validator_uid"] is not None else "validator-0",
                    "validatorName": r["validator_name"] or "Validator",
                    "validatorImage": r["validator_image"] or "/validators/Other.png",
                    "status": run_status,
                    "startTime": datetime.fromtimestamp(float(r["started_at"] or 0.0), tz=timezone.utc).isoformat(),
                    "endTime": datetime.fromtimestamp(float(r["ended_at"]), tz=timezone.utc).isoformat() if r["ended_at"] is not None else None,
                    "totalTasks": total_tasks,
                    "completedTasks": successful,
                    "successfulTasks": successful,
                    "overallScore": float(r["average_reward"] or 0.0),
                    "successRate": (successful / total_tasks) if total_tasks > 0 else 0.0,
                    "ranking": int(r["effective_rank"] or 9999),
                    "averageEvaluationTime": float(r["average_execution_time"] or 0.0),
                    "zeroReason": r["zero_reason"],
                    "isReused": bool(r["is_reused"]),
                    "reusedFromAgentRunId": r["reused_from_agent_run_id"],
                }
            )
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "startTime": lambda x: x["startTime"],
            "score": lambda x: float(x["overallScore"]),
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
                      mer.zero_reason, mer.is_reused, mer.reused_from_agent_run_id,
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
            "score": float(run["average_reward"] or 0.0),
            "ranking": 1,
            "duration": int(float(run["elapsed_sec"] or 0.0)),
            "overallScore": float(run["average_reward"] or 0.0),
            "averageEvaluationTime": float(run["average_execution_time"] or 0.0),
            "totalWebsites": len(performance),
            "websites": [],
            "tasks": [],
            "metadata": {},
            "zeroReason": run["zero_reason"],
            "isReused": bool(run["is_reused"]),
            "reusedFromAgentRunId": run["reused_from_agent_run_id"],
        }
        personas = {
            "round": {
                "id": round_id,
                "name": f"Season {int(run['season_number'] or 0)} Round {int(run['round_number_in_season'] or 0)}",
                "status": "completed" if str(run["round_status"] or "finished") in ("finished", "completed") else "active",
                "startTime": run_data["startTime"],
                "endTime": run_data["endTime"] or None,
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
            "avg_score": float(run["average_reward"] or 0.0),
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
            "overallScore": run_data["overallScore"],
            "totalTasks": run_data["totalTasks"],
            "successfulTasks": run_data["successfulTasks"],
            "failedTasks": run_data["failedTasks"],
            "duration": run_data["duration"],
            "ranking": run_data["ranking"],
            "topPerformingWebsite": (
                {
                    "website": performance[0]["website"],
                    "score": performance[0]["averageScore"],
                    "tasks": performance[0]["tasks"],
                }
                if performance
                else {"website": "N/A", "score": 0.0, "tasks": 0}
            ),
            "topPerformingUseCase": {"useCase": "N/A", "score": 0.0, "tasks": 0},
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
                    SELECT created_at, log_level, message, payload
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
                "level": str(row["log_level"] or "info").lower(),
                "message": row["message"] or "",
                "metadata": row["payload"] or {},
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
            "reusedFrom": None,
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
            return {"bestScore": "", "fastest": "", "mostTasks": "", "bestSuccessRate": "", "runs": []}
        best_score = max(complete, key=lambda x: float(x.get("overallScore") or 0.0))
        fastest = min(complete, key=lambda x: float(x.get("duration") or 0.0))
        most_tasks = max(complete, key=lambda x: int(x.get("totalTasks") or 0))
        best_success = max(
            complete,
            key=lambda x: (float(x.get("successfulTasks") or 0.0) / float(x.get("totalTasks") or 1.0)) if int(x.get("totalTasks") or 0) > 0 else 0.0,
        )
        return {
            "bestScore": best_score.get("runId", ""),
            "fastest": fastest.get("runId", ""),
            "mostTasks": most_tasks.get("runId", ""),
            "bestSuccessRate": best_success.get("runId", ""),
            "runs": complete,
        }
