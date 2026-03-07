from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text


class UIRoundsServiceMixin:
    async def get_latest_season_number(self) -> Optional[int]:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT s.season_number
                    FROM seasons s
                    JOIN rounds r ON r.season_id = s.season_id
                    ORDER BY s.season_number DESC
                    LIMIT 1
                    """
                    )
                )
            )
            .mappings()
            .first()
        )
        return int(row["season_number"]) if row and row["season_number"] is not None else None

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
                      effective_reward,
                      COALESCE(
                        effective_tasks_received,
                        post_consensus_tasks_received,
                        local_tasks_received,
                        0
                      ) AS tasks_received
                    FROM round_validator_miners
                    WHERE round_id = :round_id
                      AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                      AND COALESCE(
                        effective_tasks_received,
                        post_consensus_tasks_received,
                        local_tasks_received,
                        0
                      ) > 0
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
            reward = float(r["post_consensus_avg_reward"] or 0.0)
            rank = int(r["post_consensus_rank"]) if r["post_consensus_rank"] is not None else None
            current = dedup.get(uid)
            if current is None or (rank is not None and (current["post_consensus_rank"] is None or rank < current["post_consensus_rank"])):
                dedup[uid] = {
                    "uid": uid,
                    "name": r["name"],
                    "image": f"/miners/{uid % 100}.svg",
                    "post_consensus_avg_reward": reward,
                    "tasks_received": int(r["tasks_received"] or 0),
                    "round_reward": reward,
                    "best_reward_in_season": reward,
                    "effective_round_reward": float(r["effective_reward"] or reward),
                    "post_consensus_rank": rank or 9999,
                }
        miners = sorted(dedup.values(), key=lambda x: (x["post_consensus_rank"], -x["post_consensus_avg_reward"]))
        return {"round": f"{season}/{round_in_season}", "miners": miners}

    async def get_season_miners(self, season: int) -> Dict[str, Any]:
        season_row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT leader_miner_uid, leader_reward
                    FROM seasons
                    WHERE season_number = :season
                    LIMIT 1
                    """
                    ),
                    {"season": season},
                )
            )
            .mappings()
            .first()
        )
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    WITH season_rows AS (
                      SELECT
                        rvm.miner_uid AS uid,
                        COALESCE(NULLIF(TRIM(COALESCE(rvm.name, '')), ''), 'miner ' || rvm.miner_uid::text) AS name,
                        rvm.image_url AS image,
                        COALESCE(rvm.effective_reward, rvm.post_consensus_avg_reward, rvm.local_avg_reward, 0) AS effective_reward,
                        COALESCE(rvm.effective_rank, rvm.post_consensus_rank, rvm.local_rank, 9999) AS effective_rank,
                        r.round_number_in_season AS round_number
                      FROM round_validator_miners rvm
                      JOIN rounds r ON r.round_id = rvm.round_id
                      JOIN seasons s ON s.season_id = r.season_id
                      WHERE s.season_number = :season
                        AND NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                        AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                        AND COALESCE(
                          rvm.effective_tasks_received,
                          rvm.post_consensus_tasks_received,
                          rvm.local_tasks_received,
                          0
                        ) > 0
                    ),
                    best_rows AS (
                      SELECT DISTINCT ON (uid)
                        uid,
                        name,
                        image,
                        effective_reward,
                        effective_rank,
                        round_number
                      FROM season_rows
                      ORDER BY uid, effective_reward DESC, effective_rank ASC, round_number ASC
                    ),
                    ranked AS (
                      SELECT
                        uid,
                        name,
                        image,
                        effective_reward,
                        round_number,
                        ROW_NUMBER() OVER (
                          ORDER BY effective_reward DESC, effective_rank ASC, uid ASC
                        ) AS season_rank
                      FROM best_rows
                    )
                    SELECT
                      uid,
                      name,
                      image,
                      effective_reward,
                      round_number,
                      season_rank
                    FROM ranked
                    ORDER BY season_rank ASC, uid ASC
                    """
                    ),
                    {"season": season},
                )
            )
            .mappings()
            .all()
        )

        miners = [
            {
                "uid": int(r["uid"]),
                "name": r["name"],
                "image": r["image"] or f"/miners/{int(r['uid']) % 100}.svg",
                "post_consensus_avg_reward": float(r["effective_reward"] or 0.0),
                "best_reward_in_season": float(r["effective_reward"] or 0.0),
                "effective_round_reward": float(r["effective_reward"] or 0.0),
                "best_round_in_season": int(r["round_number"]) if r["round_number"] is not None else None,
                "post_consensus_rank": int(r["season_rank"] or 9999),
                "is_reigning_leader": (season_row is not None and season_row["leader_miner_uid"] is not None and int(season_row["leader_miner_uid"]) == int(r["uid"])),
            }
            for r in rows
        ]
        return {
            "round": f"season/{season}",
            "season": season,
            "season_leader_uid": (int(season_row["leader_miner_uid"]) if season_row and season_row["leader_miner_uid"] is not None else None),
            "season_leader_reward": (float(season_row["leader_reward"]) if season_row and season_row["leader_reward"] is not None else None),
            "miners": miners,
        }

    async def get_latest_round_top_miner(self) -> Optional[Dict[str, Any]]:
        latest_season = await self.get_latest_season_number()
        if latest_season is None:
            return None

        season_miners = await self.get_season_miners(latest_season)
        miners = season_miners.get("miners") or []
        if not miners:
            return None

        top_miner = miners[0]
        miner_hotkey = (
            await self.session.execute(
                text(
                    """
                    SELECT miner_hotkey
                    FROM round_validator_miners
                    JOIN rounds r ON r.round_id = round_validator_miners.round_id
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season
                      AND miner_uid = :uid
                    LIMIT 1
                    """
                ),
                {"season": latest_season, "uid": int(top_miner["uid"])},
            )
        ).scalar_one_or_none()
        return {
            "season": latest_season,
            "round": None,
            "miner_uid": int(top_miner["uid"]),
            "miner_hotkey": miner_hotkey,
        }

    async def get_rounds_list(self, page: int, limit: int) -> Tuple[List[Dict[str, Any]], int]:
        offset = (page - 1) * limit
        current_row = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT r.round_id
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    ORDER BY
                      CASE WHEN LOWER(COALESCE(r.status, '')) = 'active' THEN 0 ELSE 1 END,
                      s.season_number DESC,
                      r.round_number_in_season DESC
                    LIMIT 1
                    """
                    )
                )
            )
            .mappings()
            .first()
        )
        current_round_id = int(current_row["round_id"]) if current_row else None
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
        return [self._round_row_to_payload(r, is_current=(current_round_id is not None and int(r["round_id"]) == current_round_id)) for r in rows], int(total or 0)

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
                    ORDER BY
                      CASE WHEN LOWER(COALESCE(r.status, '')) = 'active' THEN 0 ELSE 1 END,
                      s.season_number DESC,
                      r.round_number_in_season DESC
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

        # Use chain block when available for consistent "current/progress" values.
        current_block = None
        try:
            from app.services.chain_state import get_current_block

            current_block = get_current_block()
        except Exception:
            current_block = None
        return self._round_row_to_payload(row, is_current=True, current_block=current_block)

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

    def _round_row_to_payload(self, row: Any, *, is_current: bool = False, current_block: Optional[int] = None) -> Dict[str, Any]:
        season = int(row["season_number"])
        round_in_season = int(row["round_number_in_season"])
        round_id = int(row["round_id"])
        start_block = int(row["start_block"] or 0)
        end_block = int(row["end_block"] or start_block)
        if end_block < start_block:
            end_block = start_block
        status = str(row["status"] or "finished")
        status_l = status.lower()
        active_like = status_l == "active"
        if current_block is None:
            effective_current_block = end_block if not active_like else start_block
        else:
            effective_current_block = int(current_block)
        if active_like:
            blocks_remaining = max(end_block - effective_current_block, 0)
            span = max(end_block - start_block, 1)
            progress = min(max((effective_current_block - start_block) / span, 0.0), 1.0)
        else:
            blocks_remaining = 0
            progress = 1.0 if status_l in ("finished", "completed", "evaluating_finished") else 0.0

        return {
            "id": season * 10000 + round_in_season,
            "round": round_in_season,
            "roundNumber": round_in_season,
            "roundKey": f"{season}/{round_in_season}",
            "season": season,
            "roundInSeason": round_in_season,
            "startBlock": start_block,
            "endBlock": end_block,
            "current": bool(is_current),
            "startTime": row["started_at"].isoformat() if row["started_at"] else None,
            "endTime": row["ended_at"].isoformat() if row["ended_at"] else None,
            "status": status,
            "totalTasks": 0,
            "completedTasks": 0,
            "currentBlock": effective_current_block,
            "blocksRemaining": blocks_remaining,
            "progress": progress,
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
                text(
                    """
                    SELECT COUNT(DISTINCT miner_uid)
                    FROM round_validator_miners
                    WHERE round_id = :rid
                      AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                    """
                ),
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
                        AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                        AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
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
            "averageReward": float(agg["avg_reward"] or 0.0),
            "winnerAverageReward": float(winner["winner_score"] or 0.0) if winner else float(agg["top_reward"] or 0.0),
            "winnerMinerUid": int(winner["winner_miner_uid"]) if winner and winner["winner_miner_uid"] is not None else None,
            "validatorAverageTopReward": float(agg["top_reward"] or 0.0),
            "topReward": float(agg["top_reward"] or 0.0),
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
                        AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                        AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
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
            reward = float(r["post_consensus_avg_reward"] or 0.0)
            item = {
                "uid": int(r["miner_uid"]),
                "name": r["name"] or f"miner {int(r['miner_uid'])}",
                "hotkey": r["miner_hotkey"],
                "success": tasks_ok > 0,
                "reward": reward,
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
            miners = [m for m in miners if float(m["reward"]) >= float(min_score)]
        if max_score is not None:
            miners = [m for m in miners if float(m["reward"]) <= float(max_score)]
        reverse = str(sort_order).lower() != "asc"
        key_map = {
            "uid": lambda m: m["uid"],
            "duration": lambda m: m["duration"],
            "ranking": lambda m: m["ranking"],
            "reward": lambda m: m["reward"],
            "score": lambda m: m["reward"],
        }
        sort_key = key_map.get(sort_by, key_map["reward"])
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
                      COALESCE(
                        COUNT(
                          DISTINCT CASE
                            WHEN NULLIF(TRIM(COALESCE(rvm.name, '')), '') IS NOT NULL
                              AND NULLIF(TRIM(COALESCE(rvm.github_url, '')), '') IS NOT NULL
                            THEN rvm.miner_uid
                          END
                        ),
                        0
                      ) AS total_miners,
                      COALESCE(AVG(rvm.local_avg_reward), 0) AS avg_reward,
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
                    "averageReward": float(r["avg_reward"] or 0.0),
                    "topReward": float(r["top_score"] or 0.0),
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
                text(
                    """
                    SELECT COUNT(DISTINCT miner_uid)
                    FROM round_validator_miners
                    WHERE round_id=:rid
                      AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                    """
                ),
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
                text(
                    """
                    SELECT COALESCE(AVG(post_consensus_avg_reward),0)
                    FROM round_validator_miners
                    WHERE round_id=:rid
                      AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                    """
                ),
                {"rid": round_id},
            )
        ).scalar_one()
        return {
            "roundId": season * 10000 + round_in_season,
            "status": str(ref["status"] or "finished"),
            "progress": 1.0 if str(ref["status"] or "finished") in ("finished", "completed") else 0.0,
            "totalMiners": int(miners or 0),
            "averageReward": float(avg or 0.0),
            "topReward": float(winner or 0.0),
            "timeRemaining": "0s",
        }

    async def get_round_with_validators(self, season: int, round_in_season: int) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(f"{season}/{round_in_season}")
        round_id = int(ref["round_id"])
        outcome = (
            (
                await self.session.execute(
                    text("""
                        SELECT winner_miner_uid, winner_score, post_consensus_summary,
                               dethroned, reigning_miner_uid_before_round, reigning_score_before_round
                        FROM round_outcomes WHERE round_id=:rid LIMIT 1
                    """),
                    {"rid": round_id},
                )
            )
            .mappings()
            .first()
        )
        outcome = dict(outcome) if outcome else {}
        winner_uid_raw = outcome.get("winner_miner_uid")
        winner_uid = int(winner_uid_raw) if winner_uid_raw is not None else None
        dethroned = bool(outcome.get("dethroned")) if outcome.get("dethroned") is not None else False
        reigning_uid_raw = outcome.get("reigning_miner_uid_before_round")
        reigning_uid = int(reigning_uid_raw) if reigning_uid_raw is not None else None

        # Season leader = winner only if they dethroned the reigning champion (or no reigning champion exists).
        # Otherwise the reigning champion IS still the season leader, even if they didn't win this round.
        if winner_uid is not None:
            season_leader_uid = winner_uid if (dethroned or reigning_uid is None) else reigning_uid
        elif reigning_uid is not None:
            # Round has no winner yet (e.g. round still processing) but we know who was reigning
            season_leader_uid = reigning_uid
        else:
            # No outcome at all - look at the most recent finished round in this season to find who was reigning
            prev_reigning = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT ro.winner_miner_uid, ro.reigning_miner_uid_before_round, ro.dethroned
                            FROM round_outcomes ro
                            JOIN rounds r ON r.round_id = ro.round_id
                            WHERE r.season_id = (SELECT season_id FROM rounds WHERE round_id = :rid)
                              AND ro.round_id < :rid
                              AND ro.winner_miner_uid IS NOT NULL
                            ORDER BY ro.round_id DESC
                            LIMIT 1
                            """
                        ),
                        {"rid": round_id},
                    )
                )
                .mappings()
                .first()
            )
            if prev_reigning:
                prev_dethroned = bool(prev_reigning.get("dethroned"))
                prev_reigning_uid = prev_reigning.get("reigning_miner_uid_before_round")
                prev_winner = prev_reigning.get("winner_miner_uid")
                season_leader_uid = (
                    int(prev_winner)
                    if (prev_dethroned or prev_reigning_uid is None)
                    else int(prev_reigning_uid)
                    if prev_reigning_uid is not None
                    else int(prev_winner)
                    if prev_winner is not None
                    else None
                )
            else:
                season_leader_uid = None

        # Season leader score: use reigning_score_before_round when the season leader IS the reigning champion,
        # or winner_score when the winner is also the season leader (they dethroned the previous champion).
        reigning_score = outcome.get("reigning_score_before_round")
        if season_leader_uid is not None and season_leader_uid == winner_uid:
            season_leader_score = outcome.get("winner_score")
        elif reigning_score is not None:
            season_leader_score = reigning_score
        else:
            season_leader_score = outcome.get("winner_score")

        # Fetch the season leader's identity + best performance metrics.
        # We look across ALL rounds up to the current one (not just the current round),
        # prioritising the round where they had the highest reward — this ensures we get
        # real data even when the season leader sat out the current round.
        winner_row = None
        if season_leader_uid is not None:
            winner_row = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT rvm.name, rvm.miner_hotkey, rvm.github_url,
                                   rvm.post_consensus_avg_reward, rvm.post_consensus_avg_eval_score,
                                   rvm.post_consensus_avg_eval_time, rvm.post_consensus_avg_eval_cost,
                                   rvm.local_avg_eval_cost,
                                   rvm.effective_reward, rvm.effective_eval_score, rvm.effective_eval_time,
                                   COALESCE(rvm.effective_eval_cost, rvm.post_consensus_avg_eval_cost, rvm.local_avg_eval_cost) AS effective_eval_cost
                            FROM round_validator_miners rvm
                            WHERE rvm.miner_uid = :uid
                              AND rvm.round_id <= :rid
                              AND COALESCE(rvm.effective_reward, rvm.post_consensus_avg_reward) > 0
                            ORDER BY COALESCE(rvm.effective_reward, rvm.post_consensus_avg_reward) DESC NULLS LAST
                            LIMIT 1
                            """
                        ),
                        {"uid": season_leader_uid, "rid": round_id},
                    )
                )
                .mappings()
                .first()
            )
            winner_row = dict(winner_row) if winner_row is not None else None
            # If still no row with real metrics, fall back to any row with just identity info
            if winner_row is None:
                winner_row = (
                    (
                        await self.session.execute(
                            text(
                                """
                                SELECT rvm.name, rvm.miner_hotkey, rvm.github_url,
                                       NULL::double precision AS post_consensus_avg_reward,
                                       NULL::double precision AS post_consensus_avg_eval_time,
                                       NULL::double precision AS post_consensus_avg_eval_cost,
                                       NULL::double precision AS local_avg_eval_cost,
                                       NULL::double precision AS effective_reward,
                                       NULL::double precision AS effective_eval_time,
                                       NULL::double precision AS effective_eval_cost
                                FROM round_validator_miners rvm
                                WHERE rvm.miner_uid = :uid
                                  AND rvm.round_id <= :rid
                                  AND rvm.name IS NOT NULL
                                ORDER BY rvm.round_id DESC
                                LIMIT 1
                                """
                            ),
                            {"uid": season_leader_uid, "rid": round_id},
                        )
                    )
                    .mappings()
                    .first()
                )
                winner_row = dict(winner_row) if winner_row is not None else None

        # Save the original round winner UID (may differ from season_leader_uid when not dethroned)
        round_winner_uid = int(outcome.get("winner_miner_uid")) if outcome.get("winner_miner_uid") is not None else None
        round_winner_score = float(outcome.get("winner_score")) if outcome.get("winner_score") is not None else None

        # Fetch round winner's name for the leadership rule display (only if different from season leader)
        round_winner_name: str | None = None
        if round_winner_uid is not None and round_winner_uid != season_leader_uid:
            rw_name_row = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT name FROM round_validator_miners
                            WHERE round_id = :rid AND miner_uid = :uid AND name IS NOT NULL
                            LIMIT 1
                            """
                        ),
                        {"rid": round_id, "uid": round_winner_uid},
                    )
                )
                .mappings()
                .first()
            )
            round_winner_name = rw_name_row.get("name") if rw_name_row else None

        # Keep winner_uid pointing to the season leader for the response dict below
        winner_uid = season_leader_uid
        try:
            miners_evaluated = (
                await self.session.execute(
                    text(
                        """
                        SELECT COUNT(DISTINCT miner_uid)
                        FROM round_validator_miners
                        WHERE round_id=:rid
                          AND name IS NOT NULL
                          AND github_url IS NOT NULL
                        """
                    ),
                    {"rid": round_id},
                )
            ).scalar_one_or_none()
            miners_evaluated = int(miners_evaluated or 0)
        except Exception:
            miners_evaluated = 0
        try:
            tasks_evaluated = (
                await self.session.execute(
                    text("SELECT COUNT(*) FROM tasks WHERE round_validator_id IN (SELECT round_validator_id FROM round_validators WHERE round_id=:rid)"),
                    {"rid": round_id},
                )
            ).scalar_one_or_none()
            tasks_evaluated = int(tasks_evaluated or 0)
        except Exception:
            tasks_evaluated = 0

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
            vr = dict(vr) if vr is not None else {}
            rvid = int(vr.get("round_validator_id", 0))
            if rvid <= 0:
                continue
            local_stats = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT
                          COALESCE(MAX(COALESCE(effective_reward, local_avg_reward)), 0) AS local_top_reward,
                          COALESCE(AVG(COALESCE(effective_eval_time, local_avg_eval_time)), 0) AS local_avg_eval_time,
                          COALESCE(COUNT(DISTINCT miner_uid), 0) AS local_miners
                        FROM round_validator_miners
                        WHERE round_validator_id=:rvid
                          AND name IS NOT NULL
                          AND github_url IS NOT NULL
                        """
                        ),
                        {"rvid": rvid},
                    )
                )
                .mappings()
                .first()
            )
            try:
                local_tasks = (
                    await self.session.execute(
                        text("SELECT COUNT(*) FROM tasks WHERE round_validator_id=:rvid"),
                        {"rvid": rvid},
                    )
                ).scalar_one_or_none()
                local_tasks = int(local_tasks or 0)
            except Exception:
                local_tasks = 0
            local_winner = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT miner_uid, name, miner_hotkey
                        FROM round_validator_miners
                        WHERE round_validator_id=:rvid
                          AND name IS NOT NULL
                          AND github_url IS NOT NULL
                        ORDER BY COALESCE(effective_rank, local_rank) ASC NULLS LAST,
                                 COALESCE(effective_reward, local_avg_reward) DESC NULLS LAST
                        LIMIT 1
                        """
                        ),
                        {"rvid": rvid},
                    )
                )
                .mappings()
                .first()
            )
            local_winner = dict(local_winner) if local_winner is not None else None
            local_miners = (
                (
                    await self.session.execute(
                        text(
                            """
                        SELECT miner_uid, name, miner_hotkey, local_rank, local_avg_reward, local_avg_eval_score, local_avg_eval_time,
                               effective_rank, effective_reward, effective_eval_score, effective_eval_time
                        FROM round_validator_miners
                        WHERE round_validator_id=:rvid
                          AND name IS NOT NULL
                          AND github_url IS NOT NULL
                        ORDER BY COALESCE(effective_rank, local_rank) ASC NULLS LAST,
                                 COALESCE(effective_reward, local_avg_reward) DESC NULLS LAST
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
                    "validator_uid": int(vr.get("validator_uid", 0)),
                    "validator_name": (vr.get("name") or f"validator {int(vr.get('validator_uid', 0))}"),
                    "validator_hotkey": vr.get("validator_hotkey", ""),
                    "winner": (
                        {
                            "uid": int(local_winner.get("miner_uid", 0)),
                            "name": local_winner.get("name") or f"miner {int(local_winner.get('miner_uid', 0))}",
                            "image": f"/miners/{int(local_winner.get('miner_uid', 0)) % 100}.svg",
                            "hotkey": local_winner.get("miner_hotkey"),
                        }
                        if local_winner
                        else None
                    ),
                    "topReward": float(local_stats.get("local_top_reward") or 0.0),
                    "local_avg_winner_reward": float(local_stats.get("local_top_reward") or 0.0),
                    "local_avg_eval_time": float(local_stats.get("local_avg_eval_time") or 0.0),
                    "local_miners_evaluated": int(local_stats.get("local_miners") or 0),
                    "local_tasks_evaluated": int(local_tasks or 0),
                    "miners": [
                        {
                            "uid": int(m.get("miner_uid", 0)),
                            "name": m.get("name") or f"miner {int(m.get('miner_uid', 0))}",
                            "hotkey": m.get("miner_hotkey"),
                            "image": f"/miners/{int(m.get('miner_uid', 0)) % 100}.svg",
                            "local_rank": int(m.get("effective_rank") or m.get("local_rank")) if (m.get("effective_rank") is not None or m.get("local_rank") is not None) else None,
                            "local_avg_reward": float(m.get("effective_reward") or m.get("local_avg_reward") or 0.0),
                            "local_avg_eval_score": float(m.get("effective_eval_score") or m.get("local_avg_eval_score") or 0.0),
                            "local_avg_eval_time": float(m.get("effective_eval_time") or m.get("local_avg_eval_time") or 0.0),
                        }
                        for m in (local_miners or [])
                    ],
                    "ipfs_uploaded": vr.get("ipfs_uploaded"),
                    "ipfs_downloaded": vr.get("ipfs_downloaded"),
                    # Keep both key families for UI compatibility.
                    "consensus_summary": vr.get("local_summary_json"),
                    "post_consensus_evaluation": vr.get("post_consensus_json"),
                    "evaluation_pre_consensus": vr.get("local_summary_json"),
                    "evaluation_post_consensus": vr.get("post_consensus_json"),
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
                        "name": (winner_row.get("name") or f"Miner {winner_uid}") if winner_row else f"Miner {winner_uid}",
                        "image": f"/miners/{winner_uid % 100}.svg",
                        "hotkey": (winner_row.get("miner_hotkey") or None) if winner_row else None,
                        "github_url": (winner_row.get("github_url") or None) if winner_row else None,
                        # season_leader_score is the authoritative score for the season leader
                        # (reigning_score_before_round when they're the reigning champ,
                        #  or winner_score when they dethroned the previous champion).
                        "avg_reward": (
                            next(
                                (
                                    float(v)
                                    for v in [
                                        season_leader_score,
                                        winner_row.get("effective_reward") if winner_row else None,
                                        winner_row.get("post_consensus_avg_reward") if winner_row else None,
                                    ]
                                    if v is not None and float(v) > 0
                                ),
                                0.0,
                            )
                        ),
                        "avg_eval_score": (
                            next(
                                (
                                    float(v)
                                    for v in [
                                        winner_row.get("effective_eval_score") if winner_row else None,
                                        winner_row.get("post_consensus_avg_eval_score") if winner_row else None,
                                    ]
                                    if v is not None and float(v) > 0
                                ),
                                0.0,
                            )
                        ),
                        "avg_eval_time": (
                            next(
                                (
                                    float(v)
                                    for v in [
                                        winner_row.get("effective_eval_time") if winner_row else None,
                                        winner_row.get("post_consensus_avg_eval_time") if winner_row else None,
                                    ]
                                    if v is not None and float(v) > 0
                                ),
                                0.0,
                            )
                        ),
                        "avg_eval_cost": (
                            next(
                                (
                                    float(v)
                                    for v in [
                                        winner_row.get("effective_eval_cost") if winner_row else None,
                                        winner_row.get("post_consensus_avg_eval_cost") if winner_row else None,
                                        winner_row.get("local_avg_eval_cost") if winner_row else None,
                                    ]
                                    if v is not None and float(v) > 0
                                ),
                                None,
                            )
                        ),
                    }
                    if winner_uid is not None
                    else None
                ),
                "miners_evaluated": int(miners_evaluated or 0),
                "tasks_evaluated": int(tasks_evaluated or 0),
                "raw_summary": outcome.get("post_consensus_summary"),
                "leadership_rule": {
                    "required_improvement_pct": float(outcome.get("required_improvement_pct") or 0.05),
                    "reigning_uid": reigning_uid,
                    "reigning_name": (winner_row.get("name") if winner_row else None) or (f"Miner {reigning_uid}" if reigning_uid else None),
                    "reigning_score": float(outcome.get("reigning_score_before_round")) if outcome.get("reigning_score_before_round") is not None else None,
                    "challenger_uid": round_winner_uid if round_winner_uid != season_leader_uid else None,
                    "challenger_name": round_winner_name or (f"Miner {round_winner_uid}" if round_winner_uid else None),
                    "challenger_score": round_winner_score,
                    "dethroned": dethroned,
                    "season_leader_uid": season_leader_uid,
                }
                if (reigning_uid is not None or round_winner_uid is not None)
                else None,
            },
            "validators": validators,
        }
