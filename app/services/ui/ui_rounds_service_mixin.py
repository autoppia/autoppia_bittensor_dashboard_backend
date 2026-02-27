from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text


class UIRoundsServiceMixin:
    async def _round_ref(self, season: int, round_in_season: int) -> Optional[Dict[str, Any]]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT r.round_id, r.round_number_in_season, s.season_number
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season AND r.round_number_in_season = :round
                    LIMIT 1
                    """
                    ),
                    {"season": season, "round": round_in_season},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def get_available_rounds(self) -> List[str]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT s.season_number, r.round_number_in_season
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    ORDER BY s.season_number DESC, r.round_number_in_season DESC
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        return [f"{int(r['season_number'])}/{int(r['round_number_in_season'])}" for r in rows]

    async def get_round_miners(self, season: int, round_in_season: int) -> Dict[str, Any]:
        ref = await self._round_ref(season, round_in_season)
        if not ref:
            return {"round": f"{season}/{round_in_season}", "miners": []}
        round_id = int(ref["round_id"])
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      miner_uid AS uid,
                      COALESCE(name, 'miner ' || miner_uid::text) AS name,
                      post_consensus_avg_reward,
                      post_consensus_rank,
                      effective_reward
                    FROM round_validator_miners
                    WHERE round_id = :round_id
                    ORDER BY post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                    """
                    ),
                    {"round_id": round_id},
                )
            )
            .mappings()
            .all()
        )

        dedup: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            uid = int(r["uid"])
            score = float(r["post_consensus_avg_reward"] or 0.0)
            rank = int(r["post_consensus_rank"]) if r["post_consensus_rank"] is not None else None
            current = dedup.get(uid)
            if current is None or (rank is not None and (current["post_consensus_rank"] is None or rank < current["post_consensus_rank"])):
                dedup[uid] = {
                    "uid": uid,
                    "name": r["name"],
                    "image": f"/miners/{uid % 100}.svg",
                    "post_consensus_avg_reward": score,
                    "round_score": score,
                    "best_score_in_season": score,
                    "effective_round_score": float(r["effective_reward"] or score),
                    "post_consensus_rank": rank or 9999,
                }
        miners = sorted(dedup.values(), key=lambda x: (x["post_consensus_rank"], -x["post_consensus_avg_reward"]))
        return {"round": f"{season}/{round_in_season}", "miners": miners}

    async def get_latest_round_top_miner(self) -> Optional[Dict[str, Any]]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT s.season_number, r.round_number_in_season, ro.winner_miner_uid
                    FROM round_outcomes ro
                    JOIN rounds r ON r.round_id = ro.round_id
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
        if not row:
            return None
        miner_hotkey = (
            await self.session.execute(
                text(
                    """
                    SELECT miner_hotkey
                    FROM round_validator_miners
                    WHERE round_id = (
                      SELECT r.round_id
                      FROM rounds r JOIN seasons s ON s.season_id = r.season_id
                      WHERE s.season_number = :season AND r.round_number_in_season = :round
                      LIMIT 1
                    ) AND miner_uid = :uid
                    LIMIT 1
                    """
                ),
                {"season": int(row["season_number"]), "round": int(row["round_number_in_season"]), "uid": int(row["winner_miner_uid"])},
            )
        ).scalar_one_or_none()
        return {
            "season": int(row["season_number"]),
            "round": int(row["round_number_in_season"]),
            "miner_uid": int(row["winner_miner_uid"]),
            "miner_hotkey": miner_hotkey,
        }

    async def get_rounds_list(self, page: int, limit: int) -> Tuple[List[Dict[str, Any]], int]:
        offset = (page - 1) * limit
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      r.round_id,
                      s.season_number,
                      r.round_number_in_season,
                      r.start_block,
                      r.end_block,
                      r.started_at,
                      r.ended_at,
                      r.status
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    ORDER BY s.season_number DESC, r.round_number_in_season DESC
                    LIMIT :limit OFFSET :offset
                    """
                    ),
                    {"limit": limit, "offset": offset},
                )
            )
            .mappings()
            .all()
        )
        total = (await self.session.execute(text("SELECT COUNT(*) FROM rounds"))).scalar_one()
        return [self._round_row_to_payload(r) for r in rows], int(total or 0)

    async def get_current_round(self) -> Optional[Dict[str, Any]]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      r.round_id,
                      s.season_number,
                      r.round_number_in_season,
                      r.start_block,
                      r.end_block,
                      r.started_at,
                      r.ended_at,
                      r.status
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
        return self._round_row_to_payload(row) if row else None

    async def get_round_detail(self, season: int, round_in_season: int) -> Dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      r.round_id,
                      s.season_number,
                      r.round_number_in_season,
                      r.start_block,
                      r.end_block,
                      r.started_at,
                      r.ended_at,
                      r.status
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season AND r.round_number_in_season = :round
                    LIMIT 1
                    """
                    ),
                    {"season": season, "round": round_in_season},
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise ValueError(f"Round {season}/{round_in_season} not found")
        payload = self._round_row_to_payload(row)
        round_id = int(row["round_id"])
        validators = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT round_validator_id, validator_uid, validator_hotkey, name, started_at, finished_at
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
        validator_rounds = []
        for v in validators:
            tasks = (
                await self.session.execute(
                    text("SELECT COUNT(*) FROM tasks WHERE round_validator_id = :rvid"),
                    {"rvid": int(v["round_validator_id"])},
                )
            ).scalar_one()
            validator_rounds.append(
                {
                    "validatorRoundId": f"validator_round_{round_id}_{int(v['validator_uid'])}",
                    "validatorUid": int(v["validator_uid"]),
                    "validatorName": v["name"],
                    "validatorHotkey": v["validator_hotkey"],
                    "status": "finished",
                    "startTime": v["started_at"].isoformat() if v["started_at"] else None,
                    "endTime": v["finished_at"].isoformat() if v["finished_at"] else None,
                    "totalTasks": int(tasks or 0),
                    "completedTasks": int(tasks or 0),
                    "icon": "/validators/Other.png",
                    "agentEvaluationRuns": None,
                    "roundData": None,
                }
            )
        payload["validatorRounds"] = validator_rounds
        payload["validatorRoundCount"] = len(validator_rounds)
        return payload

    async def get_round_detail_by_round_id(self, round_id: int) -> Dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT s.season_number, r.round_number_in_season
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE r.round_id = :round_id
                    LIMIT 1
                    """
                    ),
                    {"round_id": round_id},
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise ValueError(f"Round {round_id} not found")
        return await self.get_round_detail(int(row["season_number"]), int(row["round_number_in_season"]))

    def _round_row_to_payload(self, row: Any) -> Dict[str, Any]:
        season = int(row["season_number"])
        round_in_season = int(row["round_number_in_season"])
        round_id = int(row["round_id"])
        start_block = int(row["start_block"] or 0)
        end_block = int(row["end_block"] or start_block)
        status = str(row["status"] or "finished")
        return {
            "id": season * 10000 + round_in_season,
            "round": round_in_season,
            "roundNumber": round_in_season,
            "roundKey": f"{season}/{round_in_season}",
            "season": season,
            "roundInSeason": round_in_season,
            "startBlock": start_block,
            "endBlock": end_block,
            "current": False,
            "startTime": row["started_at"].isoformat() if row["started_at"] else None,
            "endTime": row["ended_at"].isoformat() if row["ended_at"] else None,
            "status": status,
            "totalTasks": 0,
            "completedTasks": 0,
            "currentBlock": end_block,
            "blocksRemaining": 0,
            "progress": 1.0 if status in ("finished", "completed") else 0.0,
            "roundIdRaw": round_id,
        }

    async def _resolve_round_identifier(self, round_identifier: str) -> Dict[str, Any]:
        raw = str(round_identifier).strip()
        if "/" in raw:
            season_s, round_s = raw.split("/", 1)
            season = int(season_s)
            round_in_season = int(round_s)
            row = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          r.round_id,
                          s.season_number,
                          r.round_number_in_season,
                          r.start_block,
                          r.end_block,
                          r.started_at,
                          r.ended_at,
                          r.status
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE s.season_number = :season AND r.round_number_in_season = :round
                        LIMIT 1
                        """
                        ),
                        {"season": season, "round": round_in_season},
                    )
                )
                .mappings()
                .first()
            )
            if not row:
                raise ValueError(f"Round {raw} not found")
            return dict(row)

        numeric = int(raw)
        by_encoded = None
        if numeric >= 10000 and (numeric % 10000) > 0:
            by_encoded = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          r.round_id,
                          s.season_number,
                          r.round_number_in_season,
                          r.start_block,
                          r.end_block,
                          r.started_at,
                          r.ended_at,
                          r.status
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE s.season_number = :season AND r.round_number_in_season = :round
                        LIMIT 1
                        """
                        ),
                        {"season": numeric // 10000, "round": numeric % 10000},
                    )
                )
                .mappings()
                .first()
            )
        if by_encoded:
            return dict(by_encoded)

        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      r.round_id,
                      s.season_number,
                      r.round_number_in_season,
                      r.start_block,
                      r.end_block,
                      r.started_at,
                      r.ended_at,
                      r.status
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE r.round_id = :round_id
                    LIMIT 1
                    """
                    ),
                    {"round_id": numeric},
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise ValueError(f"Round {raw} not found")
        return dict(row)

    async def get_round_statistics(self, round_identifier: str) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(round_identifier)
        round_id = int(ref["round_id"])
        encoded_id = int(ref["season_number"]) * 10000 + int(ref["round_number_in_season"])
        total_miners = (
            await self.session.execute(
                text("SELECT COUNT(DISTINCT miner_uid) FROM round_validator_miners WHERE round_id = :rid"),
                {"rid": round_id},
            )
        ).scalar_one()
        total_validators = (
            await self.session.execute(
                text("SELECT COUNT(DISTINCT validator_uid) FROM round_validators WHERE round_id = :rid"),
                {"rid": round_id},
            )
        ).scalar_one()
        total_tasks = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM tasks WHERE round_validator_id IN (SELECT round_validator_id FROM round_validators WHERE round_id=:rid)"),
                {"rid": round_id},
            )
        ).scalar_one()
        winner = (
            (
                await self.session.execute(
                    text("SELECT winner_miner_uid, winner_score FROM round_outcomes WHERE round_id = :rid LIMIT 1"),
                    {"rid": round_id},
                )
            )
            .mappings()
            .first()
        )
        agg = (
            (
                await self.session.execute(
                    text(
                        """
                    WITH ranked AS (
                      SELECT DISTINCT ON (miner_uid)
                        miner_uid,
                        post_consensus_avg_reward,
                        post_consensus_avg_eval_time,
                        post_consensus_tasks_received,
                        post_consensus_tasks_success
                      FROM round_validator_miners
                      WHERE round_id = :rid
                      ORDER BY miner_uid, post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                    )
                    SELECT
                      COALESCE(AVG(post_consensus_avg_reward), 0) AS avg_reward,
                      COALESCE(MAX(post_consensus_avg_reward), 0) AS top_reward,
                      COALESCE(AVG(post_consensus_avg_eval_time), 0) AS avg_time,
                      COALESCE(SUM(post_consensus_tasks_received), 0) AS tasks_received,
                      COALESCE(SUM(post_consensus_tasks_success), 0) AS tasks_success
                    FROM ranked
                    """
                    ),
                    {"rid": round_id},
                )
            )
            .mappings()
            .first()
        )
        tasks_received = float(agg["tasks_received"] or 0.0)
        tasks_success = float(agg["tasks_success"] or 0.0)
        return {
            "roundId": encoded_id,
            "totalMiners": int(total_miners or 0),
            "activeMiners": int(total_miners or 0),
            "totalTasks": int(total_tasks or 0),
            "completedTasks": int(total_tasks or 0),
            "totalValidators": int(total_validators or 0),
            "averageTasksPerValidator": (float(total_tasks or 0) / float(total_validators or 1)) if int(total_validators or 0) > 0 else 0.0,
            "averageScore": float(agg["avg_reward"] or 0.0),
            "winnerAverageScore": float(winner["winner_score"] or 0.0) if winner else float(agg["top_reward"] or 0.0),
            "winnerMinerUid": int(winner["winner_miner_uid"]) if winner and winner["winner_miner_uid"] is not None else None,
            "validatorAverageTopScore": float(agg["top_reward"] or 0.0),
            "topScore": float(agg["top_reward"] or 0.0),
            "successRate": (tasks_success / tasks_received) if tasks_received > 0 else 0.0,
            "averageDuration": float(agg["avg_time"] or 0.0),
            "totalStake": 0,
            "totalEmission": 0,
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
        }

    async def get_round_miners_data(
        self,
        round_identifier: str,
        page: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        success: Optional[bool],
        min_score: Optional[float],
        max_score: Optional[float],
    ) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(round_identifier)
        round_id = int(ref["round_id"])
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    WITH ranked AS (
                      SELECT DISTINCT ON (miner_uid)
                        miner_uid, name, miner_hotkey, is_sota,
                        post_consensus_rank, post_consensus_avg_reward, post_consensus_avg_eval_time,
                        post_consensus_tasks_received, post_consensus_tasks_success
                      FROM round_validator_miners
                      WHERE round_id = :rid
                      ORDER BY miner_uid, post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                    )
                    SELECT * FROM ranked
                    """
                    ),
                    {"rid": round_id},
                )
            )
            .mappings()
            .all()
        )
        miners = []
        for r in rows:
            tasks_total = int(r["post_consensus_tasks_received"] or 0)
            tasks_ok = int(r["post_consensus_tasks_success"] or 0)
            score = float(r["post_consensus_avg_reward"] or 0.0)
            item = {
                "uid": int(r["miner_uid"]),
                "name": r["name"] or f"miner {int(r['miner_uid'])}",
                "hotkey": r["miner_hotkey"],
                "success": tasks_ok > 0,
                "score": score,
                "duration": float(r["post_consensus_avg_eval_time"] or 0.0),
                "ranking": int(r["post_consensus_rank"] or 9999),
                "tasksCompleted": tasks_ok,
                "tasksTotal": tasks_total,
                "stake": 0,
                "emission": 0,
                "lastSeen": datetime.now(timezone.utc).isoformat(),
                "validatorId": f"round-{round_id}",
                "isSota": bool(r["is_sota"]),
                "provider": "autoppia",
                "imageUrl": f"/miners/{int(r['miner_uid']) % 100}.svg",
            }
            miners.append(item)
        if success is not None:
            miners = [m for m in miners if bool(m["success"]) is success]
        if min_score is not None:
            miners = [m for m in miners if float(m["score"]) >= float(min_score)]
        if max_score is not None:
            miners = [m for m in miners if float(m["score"]) <= float(max_score)]
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "uid": lambda m: m["uid"],
            "duration": lambda m: m["duration"],
            "ranking": lambda m: m["ranking"],
            "score": lambda m: m["score"],
        }
        sort_key = key_map.get(sort_by, key_map["score"])
        miners = sorted(miners, key=sort_key, reverse=reverse)
        total = len(miners)
        start = (page - 1) * limit
        end = start + limit
        return {"miners": miners[start:end], "total": total, "page": page, "limit": limit}

    async def get_round_validators_data(self, round_identifier: str) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(round_identifier)
        round_id = int(ref["round_id"])
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      rv.round_validator_id,
                      rv.validator_uid,
                      rv.validator_hotkey,
                      rv.name,
                      rv.version,
                      rv.stake,
                      rv.vtrust,
                      rv.started_at,
                      rv.finished_at,
                      COALESCE(COUNT(DISTINCT t.task_id), 0) AS total_tasks,
                      COALESCE(COUNT(DISTINCT rvm.miner_uid), 0) AS total_miners,
                      COALESCE(AVG(rvm.local_avg_reward), 0) AS avg_score,
                      COALESCE(MAX(rvm.local_avg_reward), 0) AS top_score
                    FROM round_validators rv
                    LEFT JOIN tasks t ON t.round_validator_id = rv.round_validator_id
                    LEFT JOIN round_validator_miners rvm ON rvm.round_validator_id = rv.round_validator_id
                    WHERE rv.round_id = :rid
                    GROUP BY rv.round_validator_id, rv.validator_uid, rv.validator_hotkey, rv.name, rv.version, rv.stake, rv.vtrust, rv.started_at, rv.finished_at
                    ORDER BY rv.validator_uid ASC
                    """
                    ),
                    {"rid": round_id},
                )
            )
            .mappings()
            .all()
        )
        validators = []
        for r in rows:
            validators.append(
                {
                    "id": f"validator-{int(r['validator_uid'])}",
                    "name": r["name"] or f"validator {int(r['validator_uid'])}",
                    "hotkey": r["validator_hotkey"] or "",
                    "icon": "/validators/Other.png",
                    "status": "active",
                    "totalTasks": int(r["total_tasks"] or 0),
                    "completedTasks": int(r["total_tasks"] or 0),
                    "totalMiners": int(r["total_miners"] or 0),
                    "activeMiners": int(r["total_miners"] or 0),
                    "averageScore": float(r["avg_score"] or 0.0),
                    "topScore": float(r["top_score"] or 0.0),
                    "weight": 1,
                    "trust": float(r["vtrust"] or 0.0),
                    "version": str(r["version"] or ""),
                    "stake": int(r["stake"] or 0),
                    "emission": 0,
                    "lastSeen": (r["finished_at"] or r["started_at"] or datetime.now(timezone.utc)).isoformat(),
                    "uptime": 1.0,
                }
            )
        return {"validators": validators, "total": len(validators)}

    async def get_round_progress_data(self, round_identifier: str, current_block: Optional[int]) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(round_identifier)
        season = int(ref["season_number"])
        round_in_season = int(ref["round_number_in_season"])
        start_block = int(ref["start_block"] or 0)
        end_block = int(ref["end_block"] or start_block)
        status = str(ref["status"] or "finished")
        if current_block is None:
            now_block = end_block if status in ("finished", "completed") else start_block
        else:
            now_block = int(current_block)
        blocks_remaining = max(end_block - now_block, 0)
        span = max(end_block - start_block, 0)
        progress = 1.0 if span == 0 else max(0.0, min(1.0, float(now_block - start_block) / float(span)))
        row_prev = (
            await self.session.execute(
                text(
                    """
                    SELECT r2.round_number_in_season
                    FROM rounds r
                    JOIN rounds r2 ON r2.season_id = r.season_id
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season AND r.round_number_in_season = :round AND r2.round_number_in_season < r.round_number_in_season
                    ORDER BY r2.round_number_in_season DESC
                    LIMIT 1
                    """
                ),
                {"season": season, "round": round_in_season},
            )
        ).scalar_one_or_none()
        row_next = (
            await self.session.execute(
                text(
                    """
                    SELECT r2.round_number_in_season
                    FROM rounds r
                    JOIN rounds r2 ON r2.season_id = r.season_id
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season AND r.round_number_in_season = :round AND r2.round_number_in_season > r.round_number_in_season
                    ORDER BY r2.round_number_in_season ASC
                    LIMIT 1
                    """
                ),
                {"season": season, "round": round_in_season},
            )
        ).scalar_one_or_none()
        return {
            "roundId": season * 10000 + round_in_season,
            "season": season,
            "roundInSeason": round_in_season,
            "currentBlock": now_block,
            "startBlock": start_block,
            "endBlock": end_block,
            "blocksRemaining": blocks_remaining,
            "progress": progress,
            "estimatedTimeRemaining": {"days": 0, "hours": 0, "minutes": 0, "seconds": 0},
            "lastUpdated": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "nextRound": f"{season}/{int(row_next)}" if row_next is not None else None,
            "previousRound": f"{season}/{int(row_prev)}" if row_prev is not None else None,
        }

    async def get_round_summary_data(self, round_identifier: str) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(round_identifier)
        round_id = int(ref["round_id"])
        season = int(ref["season_number"])
        round_in_season = int(ref["round_number_in_season"])
        miners = (
            await self.session.execute(
                text("SELECT COUNT(DISTINCT miner_uid) FROM round_validator_miners WHERE round_id=:rid"),
                {"rid": round_id},
            )
        ).scalar_one()
        winner = (
            await self.session.execute(
                text("SELECT winner_score FROM round_outcomes WHERE round_id=:rid LIMIT 1"),
                {"rid": round_id},
            )
        ).scalar_one_or_none()
        avg = (
            await self.session.execute(
                text("SELECT COALESCE(AVG(post_consensus_avg_reward),0) FROM round_validator_miners WHERE round_id=:rid"),
                {"rid": round_id},
            )
        ).scalar_one()
        return {
            "roundId": season * 10000 + round_in_season,
            "status": str(ref["status"] or "finished"),
            "progress": 1.0 if str(ref["status"] or "finished") in ("finished", "completed") else 0.0,
            "totalMiners": int(miners or 0),
            "averageScore": float(avg or 0.0),
            "topScore": float(winner or 0.0),
            "timeRemaining": "0s",
        }

    async def get_round_with_validators(self, season: int, round_in_season: int) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(f"{season}/{round_in_season}")
        round_id = int(ref["round_id"])
        outcome = (
            (
                await self.session.execute(
                    text("SELECT winner_miner_uid, winner_score, post_consensus_summary FROM round_outcomes WHERE round_id=:rid LIMIT 1"),
                    {"rid": round_id},
                )
            )
            .mappings()
            .first()
        )
        winner_uid = int(outcome["winner_miner_uid"]) if outcome and outcome["winner_miner_uid"] is not None else None
        winner_row = None
        if winner_uid is not None:
            winner_row = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT name, miner_hotkey, github_url,
                               post_consensus_avg_reward, post_consensus_avg_eval_score, post_consensus_avg_eval_time
                        FROM round_validator_miners
                        WHERE round_id=:rid AND miner_uid=:uid
                        ORDER BY post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                        LIMIT 1
                        """
                        ),
                        {"rid": round_id, "uid": winner_uid},
                    )
                )
                .mappings()
                .first()
            )
        miners_evaluated = (
            await self.session.execute(
                text("SELECT COUNT(DISTINCT miner_uid) FROM round_validator_miners WHERE round_id=:rid"),
                {"rid": round_id},
            )
        ).scalar_one()
        tasks_evaluated = (
            await self.session.execute(
                text("SELECT COUNT(*) FROM tasks WHERE round_validator_id IN (SELECT round_validator_id FROM round_validators WHERE round_id=:rid)"),
                {"rid": round_id},
            )
        ).scalar_one()

        validators_raw = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT round_validator_id, validator_uid, validator_hotkey, name, ipfs_uploaded, ipfs_downloaded, local_summary_json, post_consensus_json
                    FROM round_validators
                    WHERE round_id=:rid
                    ORDER BY validator_uid ASC
                    """
                    ),
                    {"rid": round_id},
                )
            )
            .mappings()
            .all()
        )
        validators: List[Dict[str, Any]] = []
        for vr in validators_raw:
            rvid = int(vr["round_validator_id"])
            local_stats = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          COALESCE(MAX(local_avg_reward), 0) AS local_top_reward,
                          COALESCE(AVG(local_avg_eval_time), 0) AS local_avg_eval_time,
                          COALESCE(COUNT(DISTINCT miner_uid), 0) AS local_miners
                        FROM round_validator_miners
                        WHERE round_validator_id=:rvid
                        """
                        ),
                        {"rvid": rvid},
                    )
                )
                .mappings()
                .first()
            )
            local_tasks = (
                await self.session.execute(
                    text("SELECT COUNT(*) FROM tasks WHERE round_validator_id=:rvid"),
                    {"rvid": rvid},
                )
            ).scalar_one()
            local_winner = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT miner_uid, name, miner_hotkey
                        FROM round_validator_miners
                        WHERE round_validator_id=:rvid
                        ORDER BY local_rank ASC NULLS LAST, local_avg_reward DESC NULLS LAST
                        LIMIT 1
                        """
                        ),
                        {"rvid": rvid},
                    )
                )
                .mappings()
                .first()
            )
            local_miners = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT miner_uid, name, miner_hotkey, local_rank, local_avg_reward, local_avg_eval_score, local_avg_eval_time
                        FROM round_validator_miners
                        WHERE round_validator_id=:rvid
                        ORDER BY local_rank ASC NULLS LAST, local_avg_reward DESC NULLS LAST
                        """
                        ),
                        {"rvid": rvid},
                    )
                )
                .mappings()
                .all()
            )
            validators.append(
                {
                    "validator_uid": int(vr["validator_uid"]),
                    "validator_name": vr["name"] or f"validator {int(vr['validator_uid'])}",
                    "validator_hotkey": vr["validator_hotkey"],
                    "winner": (
                        {
                            "uid": int(local_winner["miner_uid"]),
                            "name": local_winner["name"] or f"miner {int(local_winner['miner_uid'])}",
                            "image": f"/miners/{int(local_winner['miner_uid']) % 100}.svg",
                            "hotkey": local_winner["miner_hotkey"],
                        }
                        if local_winner
                        else None
                    ),
                    "topScore": float(local_stats["local_top_reward"] or 0.0),
                    "local_avg_winner_score": float(local_stats["local_top_reward"] or 0.0),
                    "local_avg_eval_time": float(local_stats["local_avg_eval_time"] or 0.0),
                    "local_miners_evaluated": int(local_stats["local_miners"] or 0),
                    "local_tasks_evaluated": int(local_tasks or 0),
                    "miners": [
                        {
                            "uid": int(m["miner_uid"]),
                            "name": m["name"] or f"miner {int(m['miner_uid'])}",
                            "hotkey": m["miner_hotkey"],
                            "image": f"/miners/{int(m['miner_uid']) % 100}.svg",
                            "local_rank": int(m["local_rank"]) if m["local_rank"] is not None else None,
                            "local_avg_reward": float(m["local_avg_reward"] or 0.0),
                            "local_avg_eval_score": float(m["local_avg_eval_score"] or 0.0),
                            "local_avg_eval_time": float(m["local_avg_eval_time"] or 0.0),
                        }
                        for m in local_miners
                    ],
                    "ipfs_uploaded": vr["ipfs_uploaded"],
                    "ipfs_downloaded": vr["ipfs_downloaded"],
                    "consensus_summary": vr["local_summary_json"],
                    "post_consensus_evaluation": vr["post_consensus_json"],
                }
            )

        return {
            "round_number": season * 10000 + round_in_season,
            "season": season,
            "round_in_season": round_in_season,
            "post_consensus_summary": {
                "winner": (
                    {
                        "uid": winner_uid,
                        "name": (winner_row["name"] if winner_row else f"miner {winner_uid}"),
                        "image": f"/miners/{winner_uid % 100}.svg",
                        "hotkey": winner_row["miner_hotkey"] if winner_row else None,
                        "github_url": winner_row["github_url"] if winner_row else None,
                        "avg_reward": float(winner_row["post_consensus_avg_reward"] or 0.0) if winner_row else float(outcome["winner_score"] or 0.0),
                        "avg_eval_score": float(winner_row["post_consensus_avg_eval_score"] or 0.0) if winner_row else 0.0,
                        "avg_eval_time": float(winner_row["post_consensus_avg_eval_time"] or 0.0) if winner_row else 0.0,
                    }
                    if winner_uid is not None
                    else None
                ),
                "miners_evaluated": int(miners_evaluated or 0),
                "tasks_evaluated": int(tasks_evaluated or 0),
                "raw_summary": outcome["post_consensus_summary"] if outcome else None,
            },
            "validators": validators,
        }
