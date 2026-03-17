from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from app.config import settings
from app.utils.images import resolve_validator_image


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
        count = int(value or 0)
        if count > 0:
            return count

        fallback = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT
                          COUNT(*) AS validator_count,
                          MAX((config->'round'->>'tasks_per_season')::int) AS tasks_per_validator
                        FROM round_validators
                        WHERE round_id = :rid
                          AND config IS NOT NULL
                          AND config->'round'->>'tasks_per_season' IS NOT NULL
                        """
                    ),
                    {"rid": round_id},
                )
            )
            .mappings()
            .first()
        )
        if not fallback:
            return 0

        validator_count = int(fallback.get("validator_count") or 0)
        tasks_per_validator = int(fallback.get("tasks_per_validator") or 0)
        return validator_count * tasks_per_validator

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _post_consensus_summary(post_consensus_json: Any) -> Dict[str, Any]:
        if not isinstance(post_consensus_json, dict):
            return {}
        summary = post_consensus_json.get("summary")
        return summary if isinstance(summary, dict) else {}

    @staticmethod
    def _post_consensus_miners(post_consensus_json: Any) -> List[Dict[str, Any]]:
        if not isinstance(post_consensus_json, dict):
            return []
        miners = post_consensus_json.get("miners")
        return [miner for miner in miners if isinstance(miner, dict)] if isinstance(miners, list) else []

    @staticmethod
    def _json_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @classmethod
    def _downloaded_payload_maps(cls, validators_raw: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        by_uid: Dict[int, Dict[str, Any]] = {}
        by_hotkey: Dict[str, Dict[str, Any]] = {}
        for raw_validator in validators_raw:
            ipfs_downloaded = cls._json_dict(raw_validator.get("ipfs_downloaded"))
            payloads = ipfs_downloaded.get("payloads")
            if not isinstance(payloads, list):
                continue
            for payload_entry in payloads:
                if not isinstance(payload_entry, dict):
                    continue
                payload = cls._json_dict(payload_entry.get("payload"))
                if not payload:
                    continue
                validator_uid = cls._coerce_int(payload_entry.get("validator_uid") or payload.get("validator_uid") or payload.get("uid"))
                validator_hotkey = payload_entry.get("validator_hotkey") or payload.get("validator_hotkey") or payload.get("hk")
                if validator_uid is not None and validator_uid not in by_uid:
                    by_uid[validator_uid] = payload
                if isinstance(validator_hotkey, str) and validator_hotkey and validator_hotkey not in by_hotkey:
                    by_hotkey[validator_hotkey] = payload
        return by_uid, by_hotkey

    @classmethod
    def _resolve_validator_round_payload(
        cls,
        validator_row: Dict[str, Any],
        *,
        downloaded_payloads_by_uid: Dict[int, Dict[str, Any]],
        downloaded_payloads_by_hotkey: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        ipfs_uploaded = cls._json_dict(validator_row.get("ipfs_uploaded"))
        payload = cls._json_dict(ipfs_uploaded.get("payload"))
        if payload:
            return payload

        validator_uid = cls._coerce_int(validator_row.get("validator_uid"))
        if validator_uid is not None and validator_uid in downloaded_payloads_by_uid:
            return downloaded_payloads_by_uid[validator_uid]

        validator_hotkey = validator_row.get("validator_hotkey")
        if isinstance(validator_hotkey, str) and validator_hotkey:
            return downloaded_payloads_by_hotkey.get(validator_hotkey, {})

        return {}

    @classmethod
    def _local_miners_from_ipfs_payload(cls, payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
        miners = payload.get("miners")
        if not isinstance(miners, list):
            return {}

        parsed: Dict[int, Dict[str, Any]] = {}
        for miner in miners:
            if not isinstance(miner, dict):
                continue
            miner_uid = cls._coerce_int(miner.get("uid"))
            if miner_uid is None:
                continue
            best_run = cls._json_dict(miner.get("best_run"))
            current_run = cls._json_dict(miner.get("current_run"))
            best_or_current = best_run or current_run
            parsed[miner_uid] = {
                "uid": miner_uid,
                "name": miner.get("miner_name") or f"Miner {miner_uid}",
                "hotkey": miner.get("hotkey"),
                "github_url": best_or_current.get("github_url"),
                "local_avg_reward": cls._coerce_float(current_run.get("reward")) if current_run else None,
                "local_avg_eval_score": cls._coerce_float(current_run.get("score")) if current_run else None,
                "local_avg_eval_time": cls._coerce_float(current_run.get("time")) if current_run else None,
                "local_avg_eval_cost": cls._coerce_float(current_run.get("cost")) if current_run else None,
                "best_local_reward": cls._coerce_float(best_or_current.get("reward")) or 0.0,
                "best_local_eval_score": cls._coerce_float(best_or_current.get("score")) or 0.0,
                "best_local_eval_time": cls._coerce_float(best_or_current.get("time")) or 0.0,
                "best_local_eval_cost": cls._coerce_float(best_or_current.get("cost")),
            }
        return parsed

    @staticmethod
    def _competition_sort_key(miner: Dict[str, Any]) -> Tuple[float, float, int]:
        best_reward = float(miner.get("best_local_reward") or 0.0)
        local_reward = float(miner.get("local_avg_reward") or 0.0)
        uid = int(miner.get("uid") or 0)
        return (-best_reward, -local_reward, uid)

    @classmethod
    def _miner_identity_from_post_consensus(cls, miner: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[float]]:
        best_run = miner.get("best_run_consensus")
        current_run = miner.get("current_run_consensus")
        best_run = best_run if isinstance(best_run, dict) else {}
        current_run = current_run if isinstance(current_run, dict) else {}
        hotkey = miner.get("hotkey")
        github_url = miner.get("github_url") or best_run.get("github_url") or current_run.get("github_url")
        reward = cls._coerce_float(best_run.get("reward"))
        return hotkey, github_url, reward

    @classmethod
    def _pick_candidate_from_post_consensus(cls, post_consensus_json: Any, leader_before_uid: Optional[int]) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[float]]:
        miners = cls._post_consensus_miners(post_consensus_json)
        ranked: List[Tuple[float, float, int, Dict[str, Any]]] = []
        for miner in miners:
            uid = cls._coerce_int(miner.get("uid"))
            if uid is None or uid == leader_before_uid:
                continue
            best_run = miner.get("best_run_consensus")
            best_run = best_run if isinstance(best_run, dict) else {}
            reward = cls._coerce_float(best_run.get("reward"))
            if reward is None:
                continue
            score = cls._coerce_float(best_run.get("score")) or 0.0
            ranked.append((reward, score, uid, miner))
        if not ranked:
            return None, None, None, None
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        _, _, uid, miner = ranked[0]
        hotkey, github_url, reward = cls._miner_identity_from_post_consensus(miner)
        return uid, hotkey, github_url, reward

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

    async def get_available_agents_seasons(self) -> List[int]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                        SELECT DISTINCT s.season_number
                        FROM seasons s
                        JOIN rounds r ON r.season_id = s.season_id
                        WHERE lower(COALESCE(r.status, '')) IN ('finished', 'evaluating_finished')
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
        available_seasons = await self.get_available_agents_seasons()
        latest_season = available_seasons[0] if available_seasons else None
        if season_ref == "latest":
            season = latest_season
        else:
            season = int(season_ref)
            if season not in available_seasons:
                season = latest_season
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
        available_seasons = await self.get_available_agents_seasons()
        latest_season = available_seasons[0] if available_seasons else None
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
        entries = []
        for r in rows:
            round_id = int(r["round_id"])
            total_tasks = await self._count_round_tasks(round_id)
            entries.append(
                self._round_row_to_payload(
                    r,
                    is_current=(current_round_id is not None and round_id == current_round_id),
                    total_tasks=total_tasks,
                )
            )
        return entries, int(total or 0)

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
        round_id = int(row["round_id"])
        total_tasks = await self._count_round_tasks(round_id)
        return self._round_row_to_payload(
            row,
            is_current=True,
            current_block=current_block,
            total_tasks=total_tasks,
        )

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
        round_id = int(row["round_id"])
        total_tasks = await self._count_round_tasks(round_id)
        payload = self._round_row_to_payload(row, total_tasks=total_tasks)
        validators = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT round_validator_id, validator_uid, validator_hotkey, name, started_at, finished_at, config
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
            tasks_count = int(tasks or 0)
            if tasks_count == 0:
                config = self._json_dict(v.get("config"))
                tasks_count = self._coerce_int(config.get("round", {}).get("tasks_per_season")) or 0
            validator_rounds.append(
                {
                    "validatorRoundId": f"validator_round_{round_id}_{int(v['validator_uid'])}",
                    "validatorUid": int(v["validator_uid"]),
                    "validatorName": v["name"],
                    "validatorHotkey": v["validator_hotkey"],
                    "status": "finished",
                    "startTime": v["started_at"].isoformat() if v["started_at"] else None,
                    "endTime": v["finished_at"].isoformat() if v["finished_at"] else None,
                    "totalTasks": tasks_count,
                    "completedTasks": tasks_count,
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
                          (post_consensus_json->'summary'->'leader_before_round'->>'score')::DOUBLE PRECISION AS leader_before_eval_score,
                          (post_consensus_json->'summary'->'leader_before_round'->>'time')::DOUBLE PRECISION  AS leader_before_eval_time,
                          (post_consensus_json->'summary'->'leader_before_round'->>'cost')::DOUBLE PRECISION  AS leader_before_eval_cost,
                          (post_consensus_json->'summary'->'leader_after_round'->>'score')::DOUBLE PRECISION  AS leader_after_eval_score_json,
                          (post_consensus_json->'summary'->'leader_after_round'->>'time')::DOUBLE PRECISION   AS leader_after_eval_time_json,
                          (post_consensus_json->'summary'->'leader_after_round'->>'cost')::DOUBLE PRECISION   AS leader_after_eval_cost_json,
                          post_consensus_json
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
        post_consensus_json = row.get("post_consensus_json")
        summary = self._post_consensus_summary(post_consensus_json)

        leader_before_uid = self._coerce_int(row.get("leader_before_miner_uid"))
        candidate_uid = self._coerce_int(row.get("candidate_miner_uid"))
        leader_after_uid = self._coerce_int(row.get("leader_after_miner_uid"))

        leader_before_hotkey = row.get("leader_before_miner_hotkey")
        leader_before_github_url = row.get("leader_before_github_url")
        leader_before_reward = self._coerce_float(row.get("leader_before_reward"))
        candidate_hotkey = row.get("candidate_miner_hotkey")
        candidate_github_url = row.get("candidate_github_url")
        candidate_reward = self._coerce_float(row.get("candidate_reward"))
        leader_after_hotkey = row.get("leader_after_miner_hotkey")
        leader_after_github_url = row.get("leader_after_github_url")
        leader_after_reward = self._coerce_float(row.get("leader_after_reward"))

        summary_leader_before = summary.get("leader_before_round")
        if leader_before_uid is None and isinstance(summary_leader_before, dict):
            leader_before_uid = self._coerce_int(summary_leader_before.get("uid"))
            leader_before_hotkey = leader_before_hotkey or summary_leader_before.get("hotkey")
            leader_before_github_url = leader_before_github_url or summary_leader_before.get("github_url")
            leader_before_reward = leader_before_reward if leader_before_reward is not None else self._coerce_float(summary_leader_before.get("reward"))

        summary_candidate = summary.get("candidate_this_round")
        if candidate_uid is None and isinstance(summary_candidate, dict):
            candidate_uid = self._coerce_int(summary_candidate.get("uid"))
            candidate_hotkey = candidate_hotkey or summary_candidate.get("hotkey")
            candidate_github_url = candidate_github_url or summary_candidate.get("github_url")
            candidate_reward = candidate_reward if candidate_reward is not None else self._coerce_float(summary_candidate.get("reward"))

        # A challenger cannot be the same miner as the reigning leader.
        # If the stored summary is corrupted in that way, discard it and
        # recalculate the candidate from the post-consensus miner list.
        if leader_before_uid is not None and candidate_uid is not None and int(candidate_uid) == int(leader_before_uid):
            candidate_uid = None
            candidate_hotkey = None
            candidate_github_url = None
            candidate_reward = None

        summary_leader_after = summary.get("leader_after_round")
        if leader_after_uid is None and isinstance(summary_leader_after, dict):
            leader_after_uid = self._coerce_int(summary_leader_after.get("uid"))
            leader_after_hotkey = leader_after_hotkey or summary_leader_after.get("hotkey")
            leader_after_github_url = leader_after_github_url or summary_leader_after.get("github_url")
            leader_after_reward = leader_after_reward if leader_after_reward is not None else self._coerce_float(summary_leader_after.get("reward"))

        if candidate_uid is None:
            (
                candidate_uid,
                candidate_hotkey,
                candidate_github_url,
                candidate_reward,
            ) = self._pick_candidate_from_post_consensus(post_consensus_json, leader_before_uid)

        leader_before = await self._resolve_miner_identity(
            miner_uid=leader_before_uid,
            round_id=round_id,
            hotkey=leader_before_hotkey,
            github_url=leader_before_github_url,
        )
        candidate = await self._resolve_miner_identity(
            miner_uid=candidate_uid,
            round_id=round_id,
            hotkey=candidate_hotkey,
            github_url=candidate_github_url,
        )
        leader_after = await self._resolve_miner_identity(
            miner_uid=leader_after_uid,
            round_id=round_id,
            hotkey=leader_after_hotkey,
            github_url=leader_after_github_url,
        )

        if leader_before is not None:
            leader_before["reward"] = float(leader_before_reward or 0.0)
            _lb_score = row.get("leader_before_eval_score")
            _lb_time = row.get("leader_before_eval_time")
            _lb_cost = row.get("leader_before_eval_cost")
            if _lb_score is not None:
                leader_before["score"] = float(_lb_score)
            if _lb_time is not None:
                leader_before["time"] = float(_lb_time)
            if _lb_cost is not None:
                leader_before["cost"] = float(_lb_cost)
        if candidate is not None:
            candidate["reward"] = float(candidate_reward or 0.0)
            _cand_score = self._coerce_float((summary_candidate or {}).get("score")) if isinstance(summary_candidate, dict) else None
            _cand_time = self._coerce_float((summary_candidate or {}).get("time")) if isinstance(summary_candidate, dict) else None
            _cand_cost = self._coerce_float((summary_candidate or {}).get("cost")) if isinstance(summary_candidate, dict) else None
            if _cand_score is not None:
                candidate["score"] = _cand_score
            if _cand_time is not None:
                candidate["time"] = _cand_time
            if _cand_cost is not None:
                candidate["cost"] = _cand_cost
        if leader_after is not None:
            leader_after["reward"] = float(leader_after_reward or 0.0)
            _la_score = row.get("leader_after_eval_score") or row.get("leader_after_eval_score_json")
            _la_time = row.get("leader_after_eval_time") or row.get("leader_after_eval_time_json")
            _la_cost = row.get("leader_after_eval_cost") or row.get("leader_after_eval_cost_json")
            if _la_score is not None:
                leader_after["score"] = float(_la_score)
            if _la_time is not None:
                leader_after["time"] = float(_la_time)
            if _la_cost is not None:
                leader_after["cost"] = float(_la_cost)

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
                "post_consensus_json": row.get("post_consensus_json"),
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
                          validator_round_id,
                          validator_uid,
                          validator_hotkey,
                          name,
                          image_url,
                          version,
                          stake,
                          vtrust,
                          started_at,
                          finished_at,
                          ipfs_uploaded,
                          ipfs_downloaded,
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
        downloaded_payloads_by_uid, downloaded_payloads_by_hotkey = self._downloaded_payload_maps([dict(row) for row in validators_raw])

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
            validator_round_id = str(validator.get("validator_round_id") or "")

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
                              post_consensus_tasks_received
                            FROM round_validator_miners
                            WHERE round_validator_id = :rvid
                            """
                        ),
                        {"rvid": round_validator_id},
                    )
                )
                .mappings()
                .all()
            )

            fallback_local_runs: Dict[int, Dict[str, Any]] = {}
            if validator_round_id:
                fallback_run_rows = (
                    (
                        await self.session.execute(
                            text(
                                """
                                SELECT
                                  mer.miner_uid,
                                  mer.average_reward,
                                  mer.average_score,
                                  mer.average_execution_time,
                                  mer.total_tasks,
                                  mer.success_tasks,
                                  (
                                    SELECT AVG(eval_total_cost)
                                    FROM (
                                      SELECT e.evaluation_id, SUM(COALESCE(elu.cost, 0)) AS eval_total_cost
                                      FROM evaluations e
                                      JOIN evaluation_llm_usage elu ON elu.evaluation_id = e.evaluation_id
                                      WHERE e.validator_round_id = :vrid
                                        AND e.miner_uid = mer.miner_uid
                                      GROUP BY e.evaluation_id
                                    ) c
                                  ) AS average_cost
                                FROM miner_evaluation_runs mer
                                WHERE mer.validator_round_id = :vrid
                                """
                            ),
                            {"vrid": validator_round_id},
                        )
                    )
                    .mappings()
                    .all()
                )
                fallback_local_runs = {
                    int(row["miner_uid"]): {
                        "reward": float(row["average_reward"] or 0.0),
                        "score": float(row["average_score"] or 0.0),
                        "time": float(row["average_execution_time"] or 0.0),
                        "cost": (float(row["average_cost"]) if row.get("average_cost") is not None else None),
                        "tasks_received": int(row["total_tasks"] or 0),
                        "tasks_success": int(row["success_tasks"] or 0),
                    }
                    for row in fallback_run_rows
                    if row.get("miner_uid") is not None
                }

            payload_for_validator = self._resolve_validator_round_payload(
                validator,
                downloaded_payloads_by_uid=downloaded_payloads_by_uid,
                downloaded_payloads_by_hotkey=downloaded_payloads_by_hotkey,
            )
            fallback_ipfs_miners = self._local_miners_from_ipfs_payload(payload_for_validator)

            miners_by_uid = {int(item["miner_uid"]): dict(item) for item in miners if item.get("miner_uid") is not None}
            burn_uid = int(settings.BURN_UID)
            all_miner_uids = {int(uid) for uid in (set(miners_by_uid) | set(fallback_local_runs) | set(fallback_ipfs_miners)) if int(uid) != burn_uid}

            competition_miners: List[Dict[str, Any]] = []
            for miner_uid in sorted(all_miner_uids):
                item = miners_by_uid.get(miner_uid) or {}
                fallback_local = fallback_local_runs.get(miner_uid) or {}
                fallback_ipfs = fallback_ipfs_miners.get(miner_uid) or {}
                local_avg_reward = float(item["local_avg_reward"]) if item.get("local_avg_reward") is not None else fallback_local.get("reward", fallback_ipfs.get("local_avg_reward"))
                local_avg_eval_score = float(item["local_avg_eval_score"]) if item.get("local_avg_eval_score") is not None else fallback_local.get("score", fallback_ipfs.get("local_avg_eval_score"))
                local_avg_eval_time = float(item["local_avg_eval_time"]) if item.get("local_avg_eval_time") is not None else fallback_local.get("time", fallback_ipfs.get("local_avg_eval_time"))
                local_avg_eval_cost = float(item["local_avg_eval_cost"]) if item.get("local_avg_eval_cost") is not None else fallback_local.get("cost", fallback_ipfs.get("local_avg_eval_cost"))
                best_local_reward = (
                    float(item["best_local_reward"])
                    if item.get("best_local_reward") is not None
                    else float(fallback_ipfs.get("best_local_reward") if fallback_ipfs.get("best_local_reward") is not None else (fallback_local.get("reward") or 0.0))
                )
                best_local_eval_score = (
                    float(item["best_local_eval_score"])
                    if item.get("best_local_eval_score") is not None
                    else float(fallback_ipfs.get("best_local_eval_score") if fallback_ipfs.get("best_local_eval_score") is not None else (fallback_local.get("score") or 0.0))
                )
                best_local_eval_time = (
                    float(item["best_local_eval_time"])
                    if item.get("best_local_eval_time") is not None
                    else float(fallback_ipfs.get("best_local_eval_time") if fallback_ipfs.get("best_local_eval_time") is not None else (fallback_local.get("time") or 0.0))
                )
                best_local_eval_cost = (
                    float(item["best_local_eval_cost"])
                    if item.get("best_local_eval_cost") is not None
                    else (fallback_ipfs.get("best_local_eval_cost") if fallback_ipfs.get("best_local_eval_cost") is not None else fallback_local.get("cost"))
                )
                miner_entry = {
                    "uid": miner_uid,
                    "name": item.get("name") or fallback_ipfs.get("name") or f"Miner {miner_uid}",
                    "hotkey": item.get("miner_hotkey") or fallback_ipfs.get("hotkey"),
                    "github_url": item.get("github_url") or fallback_ipfs.get("github_url"),
                    "image": item.get("image_url") or f"/miners/{miner_uid % 100}.svg",
                    "competition_rank": (int(item["best_local_rank"]) if item.get("best_local_rank") is not None else None),
                    "local_avg_reward": local_avg_reward,
                    "local_avg_eval_score": local_avg_eval_score,
                    "local_avg_eval_time": local_avg_eval_time,
                    "local_avg_eval_cost": local_avg_eval_cost,
                    "best_local_reward": best_local_reward,
                    "best_local_eval_score": best_local_eval_score,
                    "best_local_eval_time": best_local_eval_time,
                    "best_local_eval_cost": best_local_eval_cost,
                }
                has_signal = miner_entry["best_local_reward"] > 0.0 or (miner_entry["local_avg_reward"] or 0.0) > 0.0 or bool(miner_entry["github_url"]) or bool(miner_entry["hotkey"])
                if has_signal:
                    competition_miners.append(miner_entry)

            competition_miners.sort(key=self._competition_sort_key)

            winner = competition_miners[0] if competition_miners else None

            validators.append(
                {
                    "validator_uid": int(validator["validator_uid"]),
                    "validator_name": validator.get("name") or f"Validator {int(validator['validator_uid'])}",
                    "validator_hotkey": validator.get("validator_hotkey"),
                    "validator_image": resolve_validator_image(
                        validator.get("name"),
                        validator.get("image_url"),
                    ),
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

    def _round_row_to_payload(self, row: Any, *, is_current: bool = False, current_block: Optional[int] = None, total_tasks: int = 0) -> Dict[str, Any]:
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
            "totalTasks": total_tasks,
            "completedTasks": total_tasks if status_l in ("finished", "completed", "evaluating_finished") else 0,
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
                        miner_uid, name, github_url, miner_hotkey, is_sota,
                        post_consensus_rank, post_consensus_avg_reward, post_consensus_avg_eval_time,
                        post_consensus_tasks_received, post_consensus_tasks_success
                      FROM round_validator_miners
                      WHERE round_id = :rid
                        AND COALESCE(post_consensus_tasks_received, 0) > 0
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
                "name": (r["name"] or "").strip() or f"Miner {int(r['miner_uid'])}",
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
        round_view = await self.get_round_validators_view(int(ref["season_number"]), int(ref["round_number_in_season"]))
        validators = []
        for validator in round_view.get("validators", []):
            competition_state = validator.get("competition_state") or {}
            miners = [miner for miner in (competition_state.get("miners") or []) if isinstance(miner, dict)]
            avg_reward = sum(float(miner.get("best_local_reward") or miner.get("local_avg_reward") or 0.0) for miner in miners) / len(miners) if miners else 0.0
            validators.append(
                {
                    "id": f"validator-{int(validator['validator_uid'])}",
                    "name": validator["validator_name"] or f"validator {int(validator['validator_uid'])}",
                    "hotkey": validator["validator_hotkey"] or "",
                    "icon": "/validators/Other.png",
                    "status": "active",
                    "totalTasks": int(validator.get("tasks_total") or 0),
                    "completedTasks": int(validator.get("tasks_total") or 0),
                    "totalMiners": int(competition_state.get("miners_participated") or 0),
                    "activeMiners": int(competition_state.get("miners_participated") or 0),
                    "averageReward": float(avg_reward or 0.0),
                    "topReward": float(competition_state.get("top_reward") or 0.0),
                    "weight": 1,
                    "trust": float(validator.get("vtrust") or 0.0),
                    "version": str(validator.get("version") or ""),
                    "stake": int(validator.get("stake") or 0),
                    "emission": 0,
                    "lastSeen": (
                        datetime.fromisoformat(validator["finished_at"])
                        if validator.get("finished_at")
                        else (datetime.fromisoformat(validator["started_at"]) if validator.get("started_at") else datetime.now(timezone.utc))
                    ).isoformat(),
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
