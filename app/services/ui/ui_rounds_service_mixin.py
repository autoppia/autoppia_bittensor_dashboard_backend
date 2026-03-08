from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.config import settings


class UIRoundsServiceMixin:
    async def _get_main_validator_uid(self) -> int:
        row = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT main_validator_uid
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
        if row and row.get("main_validator_uid") is not None:
            return int(row["main_validator_uid"])
        if settings.MAIN_VALIDATOR_UID is not None:
            return int(settings.MAIN_VALIDATOR_UID)
        return 83

    async def _count_round_validators(self, round_id: int) -> int:
        value = (
            await self.session.execute(
                text("SELECT COUNT(DISTINCT validator_uid) FROM round_validators WHERE round_id = :rid"),
                {"rid": round_id},
            )
        ).scalar_one_or_none()
        return int(value or 0)

    async def _count_round_tasks(self, round_id: int) -> int:
        value = (
            await self.session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM tasks
                    WHERE round_validator_id IN (
                      SELECT round_validator_id FROM round_validators WHERE round_id = :rid
                    )
                    """
                ),
                {"rid": round_id},
            )
        ).scalar_one_or_none()
        return int(value or 0)

    async def _resolve_miner_identity(
        self,
        *,
        miner_uid: Optional[int],
        round_id: int,
        hotkey: Optional[str] = None,
        github_url: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if miner_uid is None:
            return None

        row = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          name,
                          miner_hotkey,
                          github_url,
                          image_url
                        FROM round_validator_miners
                        WHERE miner_uid = :miner_uid
                          AND round_id <= :round_id
                        ORDER BY
                          CASE WHEN round_id = :round_id THEN 0 ELSE 1 END,
                          round_id DESC
                        LIMIT 1
                        """
                    ),
                    {"miner_uid": miner_uid, "round_id": round_id},
                )
            )
            .mappings()
            .first()
        )
        row = dict(row) if row else {}
        return {
            "uid": int(miner_uid),
            "name": row.get("name") or f"Miner {int(miner_uid)}",
            "hotkey": hotkey or row.get("miner_hotkey"),
            "github_url": github_url or row.get("github_url"),
            "image": row.get("image_url") or f"/miners/{int(miner_uid) % 100}.svg",
        }

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

    async def get_available_seasons(self) -> List[int]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT DISTINCT s.season_number
                        FROM seasons s
                        JOIN rounds r ON r.season_id = s.season_id
                        ORDER BY s.season_number DESC
                        """
                    )
                )
            )
            .mappings()
            .all()
        )
        return [int(row["season_number"]) for row in rows if row.get("season_number") is not None]

    async def get_round_miners(self, season: int, round_in_season: int) -> Dict[str, Any]:
        ref = await self._round_ref(season, round_in_season)
        if not ref:
            return {"round": f"{season}/{round_in_season}", "miners": []}
        round_id = int(ref["round_id"])
        main_validator_uid = await self._get_main_validator_uid()
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      round_validator_miners.miner_uid AS uid,
                      COALESCE(round_validator_miners.name, 'miner ' || round_validator_miners.miner_uid::text) AS name,
                      round_validator_miners.post_consensus_avg_reward,
                      round_validator_miners.post_consensus_rank,
                      round_validator_miners.best_local_reward,
                      COALESCE(
                        round_validator_miners.best_local_tasks_received,
                        round_validator_miners.post_consensus_tasks_received,
                        round_validator_miners.local_tasks_received,
                        0
                      ) AS tasks_received
                    FROM round_validator_miners
                    JOIN round_validators rv ON rv.round_validator_id = round_validator_miners.round_validator_id
                    WHERE round_validator_miners.round_id = :round_id
                      AND rv.validator_uid = :main_validator_uid
                      AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                      AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                      AND COALESCE(
                        best_local_tasks_received,
                        post_consensus_tasks_received,
                        local_tasks_received,
                        0
                      ) > 0
                    ORDER BY post_consensus_rank ASC NULLS LAST, post_consensus_avg_reward DESC NULLS LAST
                    """
                    ),
                    {"round_id": round_id, "main_validator_uid": main_validator_uid},
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
                    "best_local_round_reward": float(r["best_local_reward"] or reward),
                    "post_consensus_rank": rank or 9999,
                }
        miners = sorted(dedup.values(), key=lambda x: (x["post_consensus_rank"], -x["post_consensus_avg_reward"]))
        return {"round": f"{season}/{round_in_season}", "miners": miners}

    async def get_season_miners(self, season: int) -> Dict[str, Any]:
        main_validator_uid = await self._get_main_validator_uid()
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
                        COALESCE(rvm.post_consensus_avg_reward, 0) AS best_reward,
                        COALESCE(rvm.post_consensus_rank, 9999) AS best_rank,
                        r.round_number_in_season AS round_number
                      FROM round_validator_miners rvm
                      JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                      JOIN rounds r ON r.round_id = rvm.round_id
                      JOIN seasons s ON s.season_id = r.season_id
                      WHERE s.season_number = :season
                        AND rv.validator_uid = :main_validator_uid
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
                        name,
                        image,
                        best_reward,
                        best_rank,
                        round_number
                      FROM season_rows
                      ORDER BY uid, best_reward DESC, best_rank ASC, round_number ASC
                    ),
                    ranked AS (
                      SELECT
                        uid,
                        name,
                        image,
                        best_reward,
                        round_number,
                        ROW_NUMBER() OVER (
                          ORDER BY best_reward DESC, best_rank ASC, uid ASC
                        ) AS season_rank
                      FROM best_rows
                    )
                    SELECT
                      uid,
                      name,
                      image,
                      best_reward,
                      round_number,
                      season_rank
                    FROM ranked
                    ORDER BY season_rank ASC, uid ASC
                    """
                    ),
                    {"season": season, "main_validator_uid": main_validator_uid},
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
                "post_consensus_avg_reward": float(r["best_reward"] or 0.0),
                "best_reward_in_season": float(r["best_reward"] or 0.0),
                "best_local_round_reward": float(r["best_reward"] or 0.0),
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

    async def get_agents_season_rank(self, season_ref: str) -> Dict[str, Any]:
        available_seasons = await self.get_available_seasons()
        latest_season = available_seasons[0] if available_seasons else None
        if season_ref == "latest":
            season = latest_season
        else:
            season = int(season_ref)
        if season is None:
            return {
                "season": None,
                "latestSeason": None,
                "availableSeasons": [],
                "season_leader_uid": None,
                "season_leader_reward": None,
                "miners": [],
            }
        season_payload = await self.get_season_miners(season)
        return {
            "season": season,
            "latestSeason": latest_season,
            "availableSeasons": available_seasons,
            "season_leader_uid": season_payload.get("season_leader_uid"),
            "season_leader_reward": season_payload.get("season_leader_reward"),
            "miners": season_payload.get("miners") or [],
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
        main_validator_uid = await self._get_main_validator_uid()
        miner_hotkey = (
            await self.session.execute(
                text(
                    """
                    SELECT miner_hotkey
                    FROM round_validator_miners
                    JOIN round_validators rv ON rv.round_validator_id = round_validator_miners.round_validator_id
                    JOIN rounds r ON r.round_id = round_validator_miners.round_id
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season
                      AND rv.validator_uid = :main_validator_uid
                      AND miner_uid = :uid
                    LIMIT 1
                    """
                ),
                {
                    "season": latest_season,
                    "uid": int(top_miner["uid"]),
                    "main_validator_uid": main_validator_uid,
                },
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

    async def get_round_status_view(self, season: int, round_in_season: int, current_block: Optional[int]) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(f"{season}/{round_in_season}")
        round_id = int(ref["round_id"])
        progress = await self.get_round_progress_data(f"{season}/{round_in_season}", current_block)
        validators_count = await self._count_round_validators(round_id)
        tasks_total = await self._count_round_tasks(round_id)
        status = str(ref["status"] or "finished")
        completed_tasks = tasks_total if status.lower() in ("finished", "completed", "evaluating_finished") else 0
        return {
            "round_id": round_id,
            "round_key": f"{season}/{round_in_season}",
            "season": season,
            "round_in_season": round_in_season,
            "status": status,
            "start_block": int(ref["start_block"] or 0),
            "current_block": int(progress["currentBlock"] or 0),
            "end_block": int(ref["end_block"] or 0),
            "blocks_remaining": int(progress["blocksRemaining"] or 0),
            "progress": float(progress["progress"] or 0.0),
            "started_at": ref["started_at"].isoformat() if ref["started_at"] else None,
            "ended_at": ref["ended_at"].isoformat() if ref["ended_at"] else None,
            "validators_count": validators_count,
            "tasks_total": tasks_total,
            "completed_tasks": completed_tasks,
            "previous_round": progress["previousRound"],
            "next_round": progress["nextRound"],
        }

    async def get_round_season_summary_view(self, season: int, round_in_season: int) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(f"{season}/{round_in_season}")
        round_id = int(ref["round_id"])
        row = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
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
                          summary_json,
                          post_consensus_summary
                        FROM round_summary
                        WHERE round_id = :rid
                        LIMIT 1
                        """
                    ),
                    {"rid": round_id},
                )
            )
            .mappings()
            .first()
        )
        if not row:
            return {
                "round_id": round_id,
                "round_key": f"{season}/{round_in_season}",
                "season": season,
                "round_in_season": round_in_season,
                "available": False,
                "summary": None,
            }

        row = dict(row)

        leader_before_uid = int(row["leader_before_miner_uid"]) if row["leader_before_miner_uid"] is not None else None
        candidate_uid = int(row["candidate_miner_uid"]) if row["candidate_miner_uid"] is not None else None
        leader_after_uid = int(row["leader_after_miner_uid"]) if row["leader_after_miner_uid"] is not None else None

        leader_before = await self._resolve_miner_identity(
            miner_uid=leader_before_uid,
            round_id=round_id,
            hotkey=row.get("leader_before_miner_hotkey"),
            github_url=row.get("leader_before_github_url"),
        )
        candidate = await self._resolve_miner_identity(
            miner_uid=candidate_uid,
            round_id=round_id,
            hotkey=row.get("candidate_miner_hotkey"),
            github_url=row.get("candidate_github_url"),
        )
        leader_after = await self._resolve_miner_identity(
            miner_uid=leader_after_uid,
            round_id=round_id,
            hotkey=row.get("leader_after_miner_hotkey"),
            github_url=row.get("leader_after_github_url"),
        )

        if leader_before is not None:
            leader_before["reward"] = float(row.get("leader_before_reward") or 0.0)
        if candidate is not None:
            candidate["reward"] = float(row.get("candidate_reward") or 0.0)
        if leader_after is not None:
            leader_after["reward"] = float(row.get("leader_after_reward") or 0.0)

        avg_eval_score = float(row.get("avg_eval_score") or 0.0)
        avg_eval_time = float(row.get("avg_eval_time") or 0.0)
        avg_eval_cost = float(row["avg_eval_cost"]) if row.get("avg_eval_cost") is not None else None
        leader_after_eval_score = float(row["leader_after_eval_score"]) if row.get("leader_after_eval_score") is not None else None
        leader_after_eval_time = float(row["leader_after_eval_time"]) if row.get("leader_after_eval_time") is not None else None
        leader_after_eval_cost = float(row["leader_after_eval_cost"]) if row.get("leader_after_eval_cost") is not None else None

        return {
            "round_id": round_id,
            "round_key": f"{season}/{round_in_season}",
            "season": season,
            "round_in_season": round_in_season,
            "available": True,
            "summary": {
                "leader_before": leader_before,
                "candidate": candidate,
                "leader_after": leader_after,
                "required_improvement_pct": float(row.get("required_improvement_pct") or 0.05),
                "required_reward_to_dethrone": (float(row["required_reward_to_dethrone"]) if row.get("required_reward_to_dethrone") is not None else None),
                "dethroned": bool(row.get("dethroned")),
                "validators_count": int(row.get("validators_count") or 0),
                "miners_evaluated": int(row.get("miners_evaluated") or 0),
                "tasks_evaluated": int(row.get("tasks_evaluated") or 0),
                "tasks_success": int(row.get("tasks_success") or 0),
                "avg_reward": float(row.get("avg_reward") or 0.0),
                "avg_eval_score": avg_eval_score,
                "avg_eval_time": avg_eval_time,
                "avg_eval_cost": avg_eval_cost,
                "leader_after_eval_score": leader_after_eval_score,
                "leader_after_eval_time": leader_after_eval_time,
                "leader_after_eval_cost": leader_after_eval_cost,
                "raw_summary": row.get("summary_json"),
                "post_consensus_summary": row.get("post_consensus_summary"),
            },
        }

    async def get_round_validators_view(self, season: int, round_in_season: int) -> Dict[str, Any]:
        ref = await self._resolve_round_identifier(f"{season}/{round_in_season}")
        round_id = int(ref["round_id"])
        validators_raw = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          round_validator_id,
                          validator_uid,
                          validator_hotkey,
                          name,
                          version,
                          stake,
                          vtrust,
                          started_at,
                          finished_at,
                          ipfs_uploaded,
                          ipfs_downloaded,
                          local_summary_json,
                          post_consensus_json
                        FROM round_validators
                        WHERE round_id = :rid
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
        for raw_validator in validators_raw:
            validator = dict(raw_validator)
            round_validator_id = int(validator["round_validator_id"])

            tasks_total = (
                await self.session.execute(
                    text("SELECT COUNT(*) FROM tasks WHERE round_validator_id = :rvid"),
                    {"rvid": round_validator_id},
                )
            ).scalar_one_or_none()
            tasks_total = int(tasks_total or 0)

            miners = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT
                              miner_uid,
                              name,
                              miner_hotkey,
                              github_url,
                              image_url,
                              local_avg_reward,
                              local_avg_eval_score,
                              local_avg_eval_time,
                              local_avg_eval_cost,
                              best_local_rank,
                              best_local_reward,
                              best_local_eval_score,
                              best_local_eval_time,
                              best_local_eval_cost,
                              is_reused
                            FROM round_validator_miners
                            WHERE round_validator_id = :rvid
                              AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                              AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                            ORDER BY
                              best_local_reward DESC NULLS LAST,
                              best_local_rank ASC NULLS LAST,
                              miner_uid ASC
                            """
                        ),
                        {"rvid": round_validator_id},
                    )
                )
                .mappings()
                .all()
            )

            competition_miners: List[Dict[str, Any]] = []
            for item in miners:
                miner_uid = int(item["miner_uid"])
                competition_miners.append(
                    {
                        "uid": miner_uid,
                        "name": item.get("name") or f"Miner {miner_uid}",
                        "hotkey": item.get("miner_hotkey"),
                        "github_url": item.get("github_url"),
                        "image": item.get("image_url") or f"/miners/{miner_uid % 100}.svg",
                        "competition_rank": (int(item["best_local_rank"]) if item.get("best_local_rank") is not None else None),
                        "local_avg_reward": (float(item["local_avg_reward"]) if item.get("local_avg_reward") is not None else None),
                        "local_avg_eval_score": (float(item["local_avg_eval_score"]) if item.get("local_avg_eval_score") is not None else None),
                        "local_avg_eval_time": (float(item["local_avg_eval_time"]) if item.get("local_avg_eval_time") is not None else None),
                        "local_avg_eval_cost": (float(item["local_avg_eval_cost"]) if item.get("local_avg_eval_cost") is not None else None),
                        "best_local_reward": float(item.get("best_local_reward") or 0.0),
                        "best_local_eval_score": float(item.get("best_local_eval_score") or 0.0),
                        "best_local_eval_time": float(item.get("best_local_eval_time") or 0.0),
                        "best_local_eval_cost": (float(item["best_local_eval_cost"]) if item.get("best_local_eval_cost") is not None else None),
                        "is_reused": bool(item.get("is_reused")),
                    }
                )

            winner = competition_miners[0] if competition_miners else None

            validators.append(
                {
                    "validator_uid": int(validator["validator_uid"]),
                    "validator_name": validator.get("name") or f"Validator {int(validator['validator_uid'])}",
                    "validator_hotkey": validator.get("validator_hotkey"),
                    "validator_image": "/validators/Other.png",
                    "version": str(validator.get("version") or ""),
                    "stake": float(validator.get("stake") or 0.0),
                    "vtrust": float(validator.get("vtrust") or 0.0),
                    "started_at": validator["started_at"].isoformat() if validator.get("started_at") else None,
                    "finished_at": validator["finished_at"].isoformat() if validator.get("finished_at") else None,
                    "tasks_total": tasks_total,
                    "competition_basis": "best_local",
                    "competition_state": {
                        "winner": winner,
                        "top_reward": float(winner["best_local_reward"]) if winner else 0.0,
                        "miners_participated": len(competition_miners),
                        "tasks_evaluated": tasks_total,
                        "miners": competition_miners,
                    },
                    "ipfs": {
                        "uploaded": validator.get("ipfs_uploaded"),
                        "downloaded": validator.get("ipfs_downloaded"),
                    },
                    "consensus": {
                        "pre_consensus": validator.get("local_summary_json"),
                        "post_consensus": validator.get("post_consensus_json"),
                    },
                }
            )

        return {
            "round_id": round_id,
            "round_key": f"{season}/{round_in_season}",
            "season": season,
            "round_in_season": round_in_season,
            "validators": validators,
        }

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
                    text("SELECT leader_after_miner_uid, leader_after_reward FROM round_summary WHERE round_id = :rid LIMIT 1"),
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
            "winnerAverageReward": float(winner["leader_after_reward"] or 0.0) if winner else float(agg["top_reward"] or 0.0),
            "winnerMinerUid": int(winner["leader_after_miner_uid"]) if winner and winner["leader_after_miner_uid"] is not None else None,
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
                text("SELECT leader_after_reward FROM round_summary WHERE round_id=:rid LIMIT 1"),
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
