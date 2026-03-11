from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.config import settings
from app.db.models import AgentEvaluationRunORM, ValidatorRoundORM, ValidatorRoundSummaryORM, ValidatorRoundValidatorORM

logger = logging.getLogger(__name__)


class ValidatorStorageSummaryMixin:
    @staticmethod
    def _to_json_dict(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    @staticmethod
    def _summary_block(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return dict(summary)
        legacy_summary_keys = {
            "season",
            "round",
            "percentage_to_dethrone",
            "dethroned",
            "leader_before_round",
            "candidate_this_round",
            "leader_after_round",
        }
        if legacy_summary_keys.intersection(payload.keys()):
            return dict(payload)
        return {}

    @classmethod
    def _normalize_post_consensus_payload(cls, raw: Any) -> Dict[str, Any]:
        payload = cls._to_json_dict(raw)
        if not payload:
            return {}

        nested = payload.get("evaluation_post_consensus")
        if isinstance(nested, dict):
            nested_payload = dict(nested)
            nested_summary = cls._summary_block(nested_payload)
            if isinstance(nested_payload.get("miners"), list) or nested_summary:
                payload = nested_payload

        payload.pop("schema_version", None)
        return payload

    @staticmethod
    def _summary_snapshot(summary: Dict[str, Any], key: str) -> Dict[str, Any]:
        if not isinstance(summary, dict):
            return {}
        value = summary.get(key)
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _apply_leadership_to_summary(
        *,
        summary: Dict[str, Any],
        reigning_uid_before_round: Optional[int],
        reigning_reward_before_round: Optional[float],
        top_candidate_uid: Optional[int],
        top_candidate_reward: Optional[float],
        required_improvement_pct: float,
        dethroned: bool,
        leader_uid_after_round: Optional[int],
        leader_reward_after_round: Optional[float],
        top_candidate_eval_score: Optional[float] = None,
        top_candidate_time: Optional[float] = None,
        top_candidate_cost: Optional[float] = None,
        leader_after_eval_score: Optional[float] = None,
        leader_after_time: Optional[float] = None,
        leader_after_cost: Optional[float] = None,
        leader_after_weight: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload = dict(summary or {})
        summary_block = dict(payload.get("summary") or {})
        leader_before = dict(summary_block.get("leader_before_round") or {})
        candidate = dict(summary_block.get("candidate_this_round") or {})
        leader_after = dict(summary_block.get("leader_after_round") or {})

        if reigning_uid_before_round is None:
            leader_before = None
        else:
            leader_before["uid"] = int(reigning_uid_before_round)
            leader_before["reward"] = float(reigning_reward_before_round or 0.0)

        if top_candidate_uid is None:
            candidate = None
        else:
            candidate["uid"] = int(top_candidate_uid)
            candidate["reward"] = float(top_candidate_reward or 0.0)
            if top_candidate_eval_score is not None:
                candidate["score"] = float(top_candidate_eval_score)
            if top_candidate_time is not None:
                candidate["time"] = float(top_candidate_time)
            if top_candidate_cost is not None:
                candidate["cost"] = float(top_candidate_cost)

        if leader_uid_after_round is None:
            leader_after = None
        else:
            leader_after["uid"] = int(leader_uid_after_round)
            leader_after["reward"] = float(leader_reward_after_round or 0.0)
            if leader_after_eval_score is not None:
                leader_after["score"] = float(leader_after_eval_score)
            if leader_after_time is not None:
                leader_after["time"] = float(leader_after_time)
            if leader_after_cost is not None:
                leader_after["cost"] = float(leader_after_cost)
            if leader_after_weight is not None:
                leader_after["weight"] = float(leader_after_weight)

        summary_block["percentage_to_dethrone"] = float(required_improvement_pct)
        summary_block["dethroned"] = bool(dethroned)
        summary_block["leader_before_round"] = leader_before
        summary_block["candidate_this_round"] = candidate
        summary_block["leader_after_round"] = leader_after
        payload["summary"] = summary_block
        return payload

    async def _recompute_and_persist_season_leadership(self, season_id: int) -> None:
        season_rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      rs.round_id,
                      rs.candidate_miner_uid,
                      rs.candidate_reward,
                      rs.required_improvement_pct,
                      rs.post_consensus_json
                    FROM round_summary rs
                    JOIN rounds r ON r.round_id = rs.round_id
                    WHERE r.season_id = :season_id
                    ORDER BY COALESCE(r.round_number_in_season, 2147483647), rs.round_id
                    """
                    ),
                    {"season_id": season_id},
                )
            )
            .mappings()
            .all()
        )
        if not season_rows:
            return

        leader_uid: Optional[int] = None
        leader_reward: Optional[float] = None
        leader_score: Optional[float] = None
        leader_time: Optional[float] = None
        leader_cost: Optional[float] = None
        default_required_improvement_pct = 0.05

        for row in season_rows:
            round_id = int(row["round_id"])
            post_payload = self._to_json_dict(row.get("post_consensus_json"))
            summary_block = self._summary_block(post_payload)
            required_improvement_pct = float(summary_block.get("percentage_to_dethrone") or row["required_improvement_pct"] or default_required_improvement_pct)
            leader_before = self._summary_snapshot(summary_block, "leader_before_round")
            candidate = self._summary_snapshot(summary_block, "candidate_this_round")
            leader_after = self._summary_snapshot(summary_block, "leader_after_round")

            reigning_uid_before_round = int(leader_before.get("uid")) if leader_before.get("uid") is not None else leader_uid
            reigning_reward_before_round = float(leader_before.get("reward")) if leader_before.get("reward") is not None else leader_reward
            top_candidate_uid = int(candidate.get("uid")) if candidate.get("uid") is not None else None
            top_candidate_reward = float(candidate.get("reward")) if candidate.get("reward") is not None else None
            dethroned = bool(summary_block.get("dethroned", False))

            if leader_after:
                leader_uid = int(leader_after.get("uid")) if leader_after.get("uid") is not None else leader_uid
                leader_reward = float(leader_after.get("reward")) if leader_after.get("reward") is not None else leader_reward
                leader_score = float(leader_after.get("score")) if leader_after.get("score") is not None else leader_score
                leader_time = float(leader_after.get("time")) if leader_after.get("time") is not None else leader_time
                leader_cost = float(leader_after.get("cost")) if leader_after.get("cost") is not None else leader_cost
            else:
                leader_uid = reigning_uid_before_round
                leader_reward = reigning_reward_before_round

            post_payload = self._apply_leadership_to_summary(
                summary=post_payload,
                reigning_uid_before_round=reigning_uid_before_round,
                reigning_reward_before_round=reigning_reward_before_round,
                top_candidate_uid=top_candidate_uid,
                top_candidate_reward=top_candidate_reward,
                required_improvement_pct=required_improvement_pct,
                dethroned=dethroned,
                leader_uid_after_round=leader_uid,
                leader_reward_after_round=leader_reward,
                top_candidate_eval_score=float(candidate.get("score")) if candidate.get("score") is not None else None,
                top_candidate_time=float(candidate.get("time")) if candidate.get("time") is not None else None,
                top_candidate_cost=float(candidate.get("cost")) if candidate.get("cost") is not None else None,
                leader_after_eval_score=leader_score,
                leader_after_time=leader_time,
                leader_after_cost=leader_cost,
                leader_after_weight=float(leader_after.get("weight")) if leader_after.get("weight") is not None else None,
            )

            await self.session.execute(
                text(
                    """
                    UPDATE round_summary
                    SET
                      leader_before_miner_uid = :leader_before_miner_uid,
                      leader_before_reward = :leader_before_reward,
                      candidate_miner_uid = :candidate_miner_uid,
                      candidate_reward = :candidate_reward,
                      leader_after_miner_uid = :leader_after_miner_uid,
                      leader_after_reward = :leader_after_reward,
                      required_improvement_pct = :required_improvement_pct,
                      required_reward_to_dethrone = :required_reward_to_dethrone,
                      dethroned = :dethroned,
                      post_consensus_json = CAST(:post_consensus_json AS JSONB),
                      updated_at = NOW()
                    WHERE round_id = :round_id
                    """
                ),
                {
                    "round_id": round_id,
                    "leader_before_miner_uid": reigning_uid_before_round,
                    "leader_before_reward": reigning_reward_before_round,
                    "candidate_miner_uid": top_candidate_uid,
                    "candidate_reward": top_candidate_reward,
                    "leader_after_miner_uid": leader_uid,
                    "leader_after_reward": leader_reward,
                    "required_improvement_pct": required_improvement_pct,
                    "required_reward_to_dethrone": (float(reigning_reward_before_round) * (1.0 + required_improvement_pct)) if reigning_reward_before_round is not None else None,
                    "dethroned": dethroned,
                    "post_consensus_json": json.dumps(post_payload),
                },
            )

        leader_repo = None
        if leader_uid is not None:
            leader_repo = await self.session.scalar(
                text(
                    """
                    SELECT rs.leader_after_github_url
                    FROM round_summary rs
                    JOIN rounds r ON r.round_id = rs.round_id
                    WHERE r.season_id = :season_id
                      AND rs.leader_after_github_url IS NOT NULL
                    ORDER BY COALESCE(r.round_number_in_season, 2147483647) DESC, rs.round_summary_id DESC
                    LIMIT 1
                    """
                ),
                {"season_id": season_id, "leader_uid": leader_uid},
            )

        await self.session.execute(
            text(
                """
                UPDATE seasons
                SET
                  leader_miner_uid = :leader_uid,
                  leader_reward = :leader_score,
                  leader_github_url = :leader_repo,
                  updated_at = NOW()
                WHERE season_id = :season_id
                """
            ),
            {
                "season_id": season_id,
                "leader_uid": leader_uid,
                "leader_score": leader_reward,
                "leader_repo": leader_repo,
            },
        )

    async def _sync_season_bounds_from_rounds(self, season_id: int) -> None:
        await self.session.execute(
            text(
                """
                WITH bounds AS (
                    SELECT
                        MIN(start_block) AS min_start_block,
                        MAX(end_block) AS max_end_block,
                        MAX(planned_start_block) AS max_planned_start_block,
                        MAX(planned_end_block) AS max_planned_end_block,
                        BOOL_OR(LOWER(COALESCE(status, '')) = 'active') AS has_active_round,
                        MAX(ended_at) AS max_ended_at
                    FROM rounds
                    WHERE season_id = :season_id
                )
                UPDATE seasons s
                SET
                    start_block = COALESCE(bounds.min_start_block, s.start_block),
                    end_block = COALESCE(bounds.max_end_block, bounds.max_planned_end_block, s.end_block),
                    end_at = CASE
                        WHEN bounds.has_active_round THEN NULL
                        ELSE COALESCE(bounds.max_ended_at, s.end_at)
                    END,
                    status = CASE WHEN bounds.has_active_round THEN 'active' ELSE 'finished' END,
                    updated_at = NOW()
                FROM bounds
                WHERE s.season_id = :season_id
                """
            ),
            {"season_id": season_id},
        )

    async def _enrich_validator_summary_post_consensus_from_db(self, round_row: ValidatorRoundORM) -> None:
        """Enrich validator_summary.evaluation_post_consensus with DB-derived metrics.

        Source of truth is validator_round_summary_miners. This makes the summary explicit,
        debuggable, and consistent with APIs that read from round summary rows.
        """
        round_id = getattr(round_row, "round_id", None)
        stmt_rows = select(ValidatorRoundSummaryORM).where(ValidatorRoundSummaryORM.validator_round_id == round_row.validator_round_id).order_by(ValidatorRoundSummaryORM.miner_uid.asc())
        summary_rows = list(await self.session.scalars(stmt_rows))
        if not summary_rows:
            return

        burn_uid = int(settings.BURN_UID)
        validators_count = 0
        if round_id is not None:
            validators_count = int(
                await self.session.scalar(
                    text("SELECT COUNT(*) FROM round_validators WHERE round_id = :round_id"),
                    {"round_id": int(round_id)},
                )
                or 0
            )
        if validators_count <= 0:
            validators_count = int(
                await self.session.scalar(
                    select(func.count(func.distinct(ValidatorRoundValidatorORM.validator_uid))).where(ValidatorRoundValidatorORM.validator_round_id == round_row.validator_round_id)
                )
                or 0
            )

        validator_summary = dict(round_row.validator_summary or {})
        current_post = self._normalize_post_consensus_payload(validator_summary.get("evaluation_post_consensus"))
        if current_post:
            enriched_post = dict(current_post)
        elif validator_summary.get("evaluation_post_consensus") is None:
            enriched_post = {}
        else:
            enriched_post = {"raw": validator_summary.get("evaluation_post_consensus")}

        existing_miners_by_uid: Dict[int, Dict[str, Any]] = {}
        for miner_payload in enriched_post.get("miners", []) if isinstance(enriched_post.get("miners"), list) else []:
            if not isinstance(miner_payload, dict):
                continue
            try:
                miner_uid = int(miner_payload.get("uid", miner_payload.get("miner_uid")))
            except Exception:
                continue
            existing_miners_by_uid[miner_uid] = dict(miner_payload)

        canonical_miners_payload: List[Dict[str, Any]] = []
        for miner_uid, miner_payload in existing_miners_by_uid.items():
            best_run = miner_payload.get("best_run_consensus")
            if not isinstance(best_run, dict):
                continue
            if miner_uid == burn_uid:
                continue
            canonical_miners_payload.append(
                {
                    "miner_uid": miner_uid,
                    "post_consensus_rank": (int(best_run.get("rank")) if best_run.get("rank") is not None else None),
                    "post_consensus_avg_reward": float(best_run.get("reward", 0.0) or 0.0),
                    "post_consensus_avg_eval_score": float(best_run.get("score", 0.0) or 0.0),
                    "post_consensus_avg_eval_time": float(best_run.get("time", 0.0) or 0.0),
                    "post_consensus_tasks_received": int(best_run.get("tasks_received", 0) or 0),
                    "post_consensus_tasks_success": int(best_run.get("tasks_success", 0) or 0),
                    "weight": (float(best_run.get("weight")) if best_run.get("weight") is not None else None),
                }
            )

        miners_payload: List[Dict[str, Any]] = canonical_miners_payload if canonical_miners_payload else []
        if not miners_payload:
            for row in summary_rows:
                miners_payload.append(
                    {
                        "miner_uid": int(row.miner_uid),
                        "miner_hotkey": row.miner_hotkey,
                        "post_consensus_rank": row.post_consensus_rank,
                        "post_consensus_avg_reward": row.post_consensus_avg_reward,
                        "post_consensus_avg_eval_score": row.post_consensus_avg_eval_score,
                        "post_consensus_avg_eval_time": row.post_consensus_avg_eval_time,
                        "post_consensus_tasks_received": row.post_consensus_tasks_received,
                        "post_consensus_tasks_success": row.post_consensus_tasks_success,
                        "weight": row.weight,
                    }
                )

        tasks_evaluated_total = 0
        tasks_success_total = 0
        rewards: List[float] = []
        eval_scores: List[float] = []
        eval_times: List[float] = []
        for row in miners_payload:
            post_tasks_received = int(row.get("post_consensus_tasks_received") or 0)
            post_tasks_success = int(row.get("post_consensus_tasks_success") or 0)
            if int(row.get("miner_uid") or -1) == burn_uid:
                continue
            tasks_evaluated_total += post_tasks_received
            tasks_success_total += post_tasks_success
            rewards.append(float(row.get("post_consensus_avg_reward") or 0.0))
            eval_scores.append(float(row.get("post_consensus_avg_eval_score") or 0.0))
            eval_times.append(float(row.get("post_consensus_avg_eval_time") or 0.0))

        competitive_miners_payload = [m for m in miners_payload if int(m.get("miner_uid") or -1) != burn_uid]
        winner = next((m for m in competitive_miners_payload if m.get("post_consensus_rank") == 1), None)
        if winner is None:
            winner = max(
                competitive_miners_payload,
                key=lambda m: (
                    float(m.get("post_consensus_avg_reward") or 0.0),
                    float(m.get("post_consensus_avg_eval_score") or 0.0),
                    -int(m.get("miner_uid") or 0),
                ),
                default=None,
            )

        db_rollup = {
            "validator_round_id": round_row.validator_round_id,
            "season_number": round_row.season_number,
            "round_number_in_season": round_row.round_number_in_season,
            "validators_count": validators_count,
            "miners_evaluated": len(competitive_miners_payload),
            "tasks_evaluated": tasks_evaluated_total,
            "tasks_success": tasks_success_total,
            "avg_reward": (sum(rewards) / len(rewards)) if rewards else 0.0,
            "avg_eval_score": (sum(eval_scores) / len(eval_scores)) if eval_scores else 0.0,
            "avg_eval_time": (sum(eval_times) / len(eval_times)) if eval_times else 0.0,
            "single_validator_mode": validators_count == 1,
            "winner": {
                "miner_uid": winner.get("miner_uid"),
                "post_consensus_rank": winner.get("post_consensus_rank"),
                "post_consensus_avg_reward": winner.get("post_consensus_avg_reward"),
            }
            if winner
            else None,
        }

        normalized_miners_payload: List[Dict[str, Any]] = []
        summary_rows_by_uid = {int(row.miner_uid): row for row in summary_rows if row.miner_uid is not None}
        all_miner_uids = sorted(set(summary_rows_by_uid.keys()) | set(existing_miners_by_uid.keys()))
        for miner_uid in all_miner_uids:
            row = summary_rows_by_uid.get(miner_uid)
            base_payload = dict(existing_miners_by_uid.get(miner_uid, {}))
            best_run = dict(base_payload.get("best_run_consensus") or {})
            current_run = base_payload.get("current_run_consensus")

            if best_run.get("reward") is None and row is not None:
                best_run["reward"] = float(row.post_consensus_avg_reward or 0.0)
            if best_run.get("score") is None and row is not None and row.post_consensus_avg_eval_score is not None:
                best_run["score"] = float(row.post_consensus_avg_eval_score)
            if best_run.get("time") is None and row is not None and row.post_consensus_avg_eval_time is not None:
                best_run["time"] = float(row.post_consensus_avg_eval_time)
            if best_run.get("cost") is None and row is not None and row.post_consensus_avg_eval_cost is not None:
                best_run["cost"] = float(row.post_consensus_avg_eval_cost)
            if int(best_run.get("tasks_received", 0) or 0) <= 0 and row is not None:
                best_run["tasks_received"] = int(row.post_consensus_tasks_received or 0)
            if int(best_run.get("tasks_success", 0) or 0) <= 0 and row is not None:
                best_run["tasks_success"] = int(row.post_consensus_tasks_success or 0)
            if best_run.get("rank") is None and row is not None and row.post_consensus_rank is not None:
                best_run["rank"] = int(row.post_consensus_rank)
            if best_run.get("weight") is None and row is not None and row.weight is not None:
                best_run["weight"] = float(row.weight)

            normalized_payload = dict(base_payload)
            normalized_payload["uid"] = miner_uid
            normalized_payload["hotkey"] = base_payload.get("hotkey") or base_payload.get("miner_hotkey") or (row.miner_hotkey if row is not None else None)
            normalized_payload["best_run_consensus"] = best_run
            normalized_payload["current_run_consensus"] = current_run if isinstance(current_run, dict) else None
            normalized_payload.pop("miner_uid", None)
            normalized_payload.pop("miner_hotkey", None)

            normalized_miners_payload.append(normalized_payload)

        enriched_post["miners"] = normalized_miners_payload
        enriched_post["validators_participated"] = validators_count
        enriched_post["miners_evaluated"] = db_rollup["miners_evaluated"]
        enriched_post["tasks_evaluated"] = db_rollup["tasks_evaluated"]
        enriched_post["tasks_success"] = db_rollup["tasks_success"]
        enriched_post.pop("schema_version", None)

        def _to_int(value: Any) -> Optional[int]:
            try:
                if value is None:
                    return None
                return int(value)
            except Exception:
                return None

        def _to_float(value: Any) -> Optional[float]:
            try:
                if value is None:
                    return None
                return float(value)
            except Exception:
                return None

        summary_block = self._summary_block(enriched_post)
        leader_before_payload = summary_block.get("leader_before_round") if isinstance(summary_block.get("leader_before_round"), dict) else {}
        leader_before_uid = _to_int(leader_before_payload.get("uid"))
        leader_before_reward = _to_float(leader_before_payload.get("reward"))
        if int(round_row.round_number_in_season or 0) == 1:
            leader_before_uid = None
            leader_before_reward = None
        required_improvement_pct = _to_float(summary_block.get("percentage_to_dethrone")) or 0.05
        summary_leader_after = self._summary_snapshot(summary_block, "leader_after_round")
        summary_candidate = self._summary_snapshot(summary_block, "candidate_this_round")
        winner_uid = _to_int(summary_leader_after.get("uid"))
        if winner_uid is None and isinstance(winner, dict):
            winner_uid = _to_int(winner.get("miner_uid"))
        winner_row = next((row for row in summary_rows if int(row.miner_uid or -1) == int(winner_uid)), None) if winner_uid is not None else None

        candidate_row = None
        candidate_uid_from_summary = _to_int(summary_candidate.get("uid"))
        if candidate_uid_from_summary is not None:
            candidate_row = next((row for row in summary_rows if int(row.miner_uid or -1) == candidate_uid_from_summary), None)
        elif leader_before_uid is not None:
            candidate_row = max(
                (row for row in summary_rows if int(row.miner_uid or -1) != burn_uid and int(row.miner_uid or -1) != leader_before_uid),
                key=lambda row: (
                    float(row.post_consensus_avg_reward or 0.0),
                    float(row.post_consensus_avg_eval_score or 0.0),
                    -int(row.miner_uid or 0),
                ),
                default=None,
            )
        else:
            candidate_row = winner_row

        candidate_uid = int(candidate_row.miner_uid) if candidate_row is not None else None
        candidate_reward = float(candidate_row.post_consensus_avg_reward or 0.0) if candidate_row is not None else None
        candidate_eval_score = float(candidate_row.post_consensus_avg_eval_score or 0.0) if candidate_row is not None and candidate_row.post_consensus_avg_eval_score is not None else None
        candidate_time = float(candidate_row.post_consensus_avg_eval_time or 0.0) if candidate_row is not None and candidate_row.post_consensus_avg_eval_time is not None else None
        candidate_cost = float(candidate_row.post_consensus_avg_eval_cost) if candidate_row is not None and candidate_row.post_consensus_avg_eval_cost is not None else None

        leader_after_uid = int(winner_row.miner_uid) if winner_row is not None else _to_int(summary_leader_after.get("uid"))
        leader_after_reward = float(winner_row.post_consensus_avg_reward or 0.0) if winner_row is not None else _to_float(summary_leader_after.get("reward"))
        leader_after_eval_score = (
            float(winner_row.post_consensus_avg_eval_score or 0.0) if winner_row is not None and winner_row.post_consensus_avg_eval_score is not None else _to_float(summary_leader_after.get("score"))
        )
        leader_after_time = (
            float(winner_row.post_consensus_avg_eval_time or 0.0) if winner_row is not None and winner_row.post_consensus_avg_eval_time is not None else _to_float(summary_leader_after.get("time"))
        )
        leader_after_cost = (
            float(winner_row.post_consensus_avg_eval_cost) if winner_row is not None and winner_row.post_consensus_avg_eval_cost is not None else _to_float(summary_leader_after.get("cost"))
        )
        leader_after_weight = float(winner_row.weight or 0.0) if winner_row is not None and winner_row.weight is not None else _to_float(summary_leader_after.get("weight"))

        dethroned_value = bool(summary_block.get("dethroned", False))

        enriched_post = self._apply_leadership_to_summary(
            summary=enriched_post,
            reigning_uid_before_round=leader_before_uid,
            reigning_reward_before_round=leader_before_reward,
            top_candidate_uid=candidate_uid,
            top_candidate_reward=candidate_reward,
            required_improvement_pct=required_improvement_pct,
            dethroned=dethroned_value,
            leader_uid_after_round=leader_after_uid,
            leader_reward_after_round=leader_after_reward,
            top_candidate_eval_score=candidate_eval_score,
            top_candidate_time=candidate_time,
            top_candidate_cost=candidate_cost,
            leader_after_eval_score=leader_after_eval_score,
            leader_after_time=leader_after_time,
            leader_after_cost=leader_after_cost,
            leader_after_weight=leader_after_weight,
        )

        # Keep round-level consensus decision as first-class columns in validator_rounds.
        round_row.leader_before_uid = leader_before_uid
        round_row.leader_before_reward = leader_before_reward
        round_row.candidate_uid = candidate_uid
        round_row.candidate_reward = candidate_reward
        round_row.leader_after_uid = leader_after_uid
        round_row.leader_after_reward = leader_after_reward
        round_row.required_improvement_pct = required_improvement_pct
        round_row.dethroned = dethroned_value

        validator_summary["evaluation_post_consensus"] = enriched_post
        round_row.validator_summary = validator_summary

    async def _sync_round_validators_post_consensus_json(self, round_row: ValidatorRoundORM) -> None:
        """Mirror enriched post-consensus summary into round_validators JSON columns."""
        validator_summary = dict(getattr(round_row, "validator_summary", {}) or {})
        post_summary = self._normalize_post_consensus_payload(validator_summary.get("evaluation_post_consensus"))
        if not isinstance(post_summary, dict) or not post_summary:
            return
        await self.session.execute(
            text(
                """
                UPDATE round_validators
                SET
                    post_consensus_json = CAST(:post_consensus_json AS JSONB),
                    updated_at = NOW()
                WHERE validator_round_id = :validator_round_id
                """
            ),
            {
                "validator_round_id": round_row.validator_round_id,
                "post_consensus_json": json.dumps(post_summary),
            },
        )

    async def _upsert_round_summary_from_validator_summary(self, round_row: ValidatorRoundORM) -> None:
        """Populate round_summary with leadership snapshot + rollups from validator_round_summary_miners."""
        stmt_rows = select(ValidatorRoundSummaryORM).where(ValidatorRoundSummaryORM.validator_round_id == round_row.validator_round_id).order_by(ValidatorRoundSummaryORM.miner_uid.asc())
        summary_rows = list(await self.session.scalars(stmt_rows))

        row_ctx = await self.session.execute(
            text(
                """
                SELECT
                    rv.round_id,
                    r.season_id,
                    r.round_number_in_season,
                    rv.round_validator_id,
                    rv.validator_uid,
                    COALESCE(rv.is_main_validator, FALSE) AS is_main_validator,
                    rv.post_consensus_json
                FROM round_validators rv
                JOIN rounds r ON r.round_id = rv.round_id
                WHERE rv.validator_round_id = :validator_round_id
                LIMIT 1
                """
            ),
            {"validator_round_id": round_row.validator_round_id},
        )
        ctx = row_ctx.first()
        if ctx is None:
            return

        round_id = int(ctx.round_id)
        season_id = int(ctx.season_id) if ctx.season_id is not None else None
        round_number_in_season = int(ctx.round_number_in_season) if ctx.round_number_in_season is not None else None
        source_round_validator_id = int(ctx.round_validator_id)
        source_validator_uid = int(ctx.validator_uid) if ctx.validator_uid is not None else None
        source_is_main_validator = bool(ctx.is_main_validator)
        burn_uid = int(settings.BURN_UID)
        post_payload = self._normalize_post_consensus_payload(ctx.post_consensus_json)
        if not post_payload:
            validator_summary = dict(getattr(round_row, "validator_summary", {}) or {})
            post_payload = self._normalize_post_consensus_payload(validator_summary.get("evaluation_post_consensus"))

        payload_miners: List[Dict[str, Any]] = []
        for miner_data in post_payload.get("miners", []) if isinstance(post_payload.get("miners"), list) else []:
            if not isinstance(miner_data, dict):
                continue
            try:
                miner_uid = int(miner_data.get("uid", miner_data.get("miner_uid")))
            except Exception:
                continue
            best_run = miner_data.get("best_run_consensus") if isinstance(miner_data.get("best_run_consensus"), dict) else {}
            payload_miners.append(
                {
                    "uid": miner_uid,
                    "reward": float(best_run.get("reward", 0.0) or 0.0),
                    "score": float(best_run.get("score", 0.0) or 0.0),
                    "time": float(best_run.get("time", 0.0) or 0.0),
                    "cost": (float(best_run.get("cost")) if best_run.get("cost") is not None else None),
                    "tasks_received": int(best_run.get("tasks_received", 0) or 0),
                    "tasks_success": int(best_run.get("tasks_success", 0) or 0),
                    "rank": (int(best_run.get("rank")) if best_run.get("rank") is not None else None),
                    "weight": (float(best_run.get("weight")) if best_run.get("weight") is not None else None),
                }
            )

        competitive_rows = [row for row in payload_miners if int(row["uid"]) != burn_uid]

        # Override tasks_received in competitive_rows with the post-consensus combined value
        # from round_validator_miners (which aggregates across all validators), so that
        # tasks_evaluated reflects the full multi-validator evaluation count (e.g. 80 = 2×40)
        # rather than just one validator's local view (e.g. 40).
        if competitive_rows and summary_rows:
            summary_tasks_by_uid = {int(sr.miner_uid): int(sr.post_consensus_tasks_received or 0) for sr in summary_rows if sr.miner_uid is not None}
            summary_success_by_uid = {int(sr.miner_uid): int(sr.post_consensus_tasks_success or 0) for sr in summary_rows if sr.miner_uid is not None}
            for row in competitive_rows:
                uid = int(row["uid"])
                if uid in summary_tasks_by_uid and summary_tasks_by_uid[uid] > row["tasks_received"]:
                    row["tasks_received"] = summary_tasks_by_uid[uid]
                    row["tasks_success"] = summary_success_by_uid.get(uid, row["tasks_success"])

        # Fallback for reused rounds: if the post_consensus payload had no miners
        # (e.g. because round_validators.post_consensus_json is NULL and
        # validator_summary.evaluation_post_consensus was not yet enriched),
        # rebuild the competitive list directly from the already-populated
        # validator_round_summary_miners rows (round_validator_miners).
        if not competitive_rows:
            for sr in summary_rows:
                if sr.miner_uid is None or int(sr.miner_uid) == burn_uid:
                    continue
                competitive_rows.append(
                    {
                        "uid": int(sr.miner_uid),
                        "reward": float(sr.post_consensus_avg_reward or 0.0),
                        "score": float(sr.post_consensus_avg_eval_score or 0.0),
                        "time": float(sr.post_consensus_avg_eval_time or 0.0),
                        "cost": float(sr.post_consensus_avg_eval_cost) if sr.post_consensus_avg_eval_cost is not None else None,
                        "tasks_received": int(sr.post_consensus_tasks_received or 0),
                        "tasks_success": int(sr.post_consensus_tasks_success or 0),
                        "rank": int(sr.post_consensus_rank) if sr.post_consensus_rank is not None else None,
                        "weight": float(sr.weight) if sr.weight is not None else None,
                    }
                )
        if not competitive_rows:
            return

        winner_row = next((row for row in competitive_rows if row.get("rank") == 1), None)
        if winner_row is None:
            winner_row = max(
                competitive_rows,
                key=lambda row: (
                    float(row.get("reward") or 0.0),
                    float(row.get("score") or 0.0),
                    -int(row.get("uid") or 0),
                ),
                default=None,
            )

        rewards = [float(row.get("reward") or 0.0) for row in competitive_rows]
        tasks_evaluated = sum(int(row.get("tasks_received") or 0) for row in competitive_rows)
        tasks_success = sum(int(row.get("tasks_success") or 0) for row in competitive_rows)

        # True averages across ALL competitive miners
        all_eval_scores = [float(row.get("score") or 0.0) for row in competitive_rows]
        all_eval_times = [float(row.get("time") or 0.0) for row in competitive_rows if float(row.get("time") or 0.0) > 0]
        all_eval_costs = [float(row.get("cost")) for row in competitive_rows if row.get("cost") is not None and float(row.get("cost") or 0.0) > 0]
        avg_all_eval_score = (sum(all_eval_scores) / len(all_eval_scores)) if all_eval_scores else 0.0
        avg_all_eval_time = (sum(all_eval_times) / len(all_eval_times)) if all_eval_times else 0.0
        avg_all_eval_cost = (sum(all_eval_costs) / len(all_eval_costs)) if all_eval_costs else None

        validators_count = int(
            await self.session.scalar(
                text("SELECT COUNT(*) FROM round_validators WHERE round_id = :round_id"),
                {"round_id": round_id},
            )
            or 0
        )

        summary_block = self._summary_block(post_payload)
        leader_before_summary = self._summary_snapshot(summary_block, "leader_before_round")
        candidate_summary = self._summary_snapshot(summary_block, "candidate_this_round")
        leader_after_summary = self._summary_snapshot(summary_block, "leader_after_round")

        required_improvement_pct = (
            float(round_row.required_improvement_pct) if getattr(round_row, "required_improvement_pct", None) is not None else float(summary_block.get("percentage_to_dethrone", 0.05) or 0.05)
        )
        dethroned = bool(round_row.dethroned) if getattr(round_row, "dethroned", None) is not None else bool(summary_block.get("dethroned", False))

        async def _lookup_round_miner_snapshot(miner_uid: Optional[int]) -> tuple[Optional[str], Optional[str]]:
            if miner_uid is None:
                return None, None
            snap = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT miner_hotkey, github_url
                            FROM round_validator_miners
                            WHERE round_id = :round_id
                              AND miner_uid = :miner_uid
                            ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
                            LIMIT 1
                            """
                        ),
                        {"round_id": round_id, "miner_uid": int(miner_uid)},
                    )
                )
                .mappings()
                .first()
            )
            if not snap:
                return None, None
            return snap.get("miner_hotkey"), snap.get("github_url")

        leader_before_uid = int(round_row.leader_before_uid) if getattr(round_row, "leader_before_uid", None) is not None else None
        if leader_before_uid is None and leader_before_summary.get("uid") is not None:
            leader_before_uid = int(leader_before_summary.get("uid"))
        leader_before_reward = float(round_row.leader_before_reward) if getattr(round_row, "leader_before_reward", None) is not None else None
        if leader_before_reward is None and leader_before_summary.get("reward") is not None:
            leader_before_reward = float(leader_before_summary.get("reward"))
        if leader_before_uid is None and season_id is not None and round_number_in_season is not None and round_number_in_season > 1:
            previous_round = (
                (
                    await self.session.execute(
                        text(
                            """
                            SELECT
                                rs.leader_after_miner_uid AS uid,
                                rs.leader_after_reward AS reward
                            FROM round_summary rs
                            JOIN rounds r ON r.round_id = rs.round_id
                            WHERE r.season_id = :season_id
                              AND r.round_number_in_season < :round_number_in_season
                              AND rs.leader_after_miner_uid IS NOT NULL
                            ORDER BY r.round_number_in_season DESC, rs.round_id DESC
                            LIMIT 1
                            """
                        ),
                        {
                            "season_id": season_id,
                            "round_number_in_season": round_number_in_season,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if previous_round:
                if previous_round.get("uid") is not None:
                    leader_before_uid = int(previous_round.get("uid"))
                if leader_before_reward is None and previous_round.get("reward") is not None:
                    leader_before_reward = float(previous_round.get("reward"))
        candidate_row = None
        candidate_uid_from_summary = int(candidate_summary.get("uid")) if candidate_summary.get("uid") is not None else None
        if candidate_uid_from_summary is not None:
            candidate_row = next((row for row in competitive_rows if int(row.get("uid") or -1) == candidate_uid_from_summary), None)
        elif leader_before_uid is not None:
            candidate_row = max(
                (row for row in competitive_rows if int(row.get("uid") or -1) != int(leader_before_uid)),
                key=lambda row: (
                    float(row.get("reward") or 0.0),
                    float(row.get("score") or 0.0),
                    -int(row.get("uid") or 0),
                ),
                default=None,
            )
        else:
            candidate_row = winner_row

        candidate_uid = int(candidate_row.get("uid")) if candidate_row is not None else None
        leader_after_uid = int(leader_after_summary.get("uid")) if leader_after_summary.get("uid") is not None else None
        if leader_after_uid is None and winner_row is not None:
            leader_after_uid = int(winner_row.get("uid"))
        leader_before_hotkey, leader_before_github_url = await _lookup_round_miner_snapshot(leader_before_uid)
        candidate_hotkey, candidate_github_url = await _lookup_round_miner_snapshot(candidate_uid)
        leader_after_hotkey, leader_after_github_url = await _lookup_round_miner_snapshot(leader_after_uid)
        candidate_reward = (
            float(candidate_row.get("reward", 0.0) or 0.0) if candidate_row is not None else (float(candidate_summary.get("reward")) if candidate_summary.get("reward") is not None else None)
        )
        leader_after_row = (
            next(
                (row for row in competitive_rows if int(row.get("uid") or -1) == int(leader_after_uid)),
                None,
            )
            if leader_after_uid is not None
            else None
        )
        leader_after_reward = (
            float(leader_after_row.get("reward", 0.0) or 0.0)
            if leader_after_row is not None
            else (float(leader_after_summary.get("reward")) if leader_after_summary.get("reward") is not None else None)
        )
        leader_eval_score = float(leader_after_row.get("score", 0.0) or 0.0) if leader_after_row is not None else float(leader_after_summary.get("score") or 0.0)
        leader_eval_time = float(leader_after_row.get("time", 0.0) or 0.0) if leader_after_row is not None else float(leader_after_summary.get("time") or 0.0)
        leader_eval_cost = (
            float(leader_after_row.get("cost"))
            if leader_after_row is not None and leader_after_row.get("cost") is not None
            else (float(leader_after_summary.get("cost")) if leader_after_summary.get("cost") is not None else None)
        )

        await self.session.execute(
            text(
                """
                INSERT INTO round_summary (
                    round_id,
                    source_round_validator_id,
                    source_validator_uid,
                    source_is_main_validator,
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
                    post_consensus_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    :round_id,
                    :source_round_validator_id,
                    :source_validator_uid,
                    :source_is_main_validator,
                    :leader_before_miner_uid,
                    :leader_before_miner_hotkey,
                    :leader_before_github_url,
                    :leader_before_reward,
                    :candidate_miner_uid,
                    :candidate_miner_hotkey,
                    :candidate_github_url,
                    :candidate_reward,
                    :leader_after_miner_uid,
                    :leader_after_miner_hotkey,
                    :leader_after_github_url,
                    :leader_after_reward,
                    :required_improvement_pct,
                    :required_reward_to_dethrone,
                    :dethroned,
                    :validators_count,
                    :miners_evaluated,
                    :tasks_evaluated,
                    :tasks_success,
                    :avg_reward,
                    :avg_eval_score,
                    :avg_eval_time,
                    :avg_eval_cost,
                    :leader_after_eval_score,
                    :leader_after_eval_time,
                    :leader_after_eval_cost,
                    CAST(:post_consensus_json AS JSONB),
                    NOW(),
                    NOW()
                )
                ON CONFLICT (round_id) DO UPDATE SET
                    source_round_validator_id = EXCLUDED.source_round_validator_id,
                    source_validator_uid = EXCLUDED.source_validator_uid,
                    source_is_main_validator = EXCLUDED.source_is_main_validator,
                    leader_before_miner_uid = EXCLUDED.leader_before_miner_uid,
                    leader_before_miner_hotkey = EXCLUDED.leader_before_miner_hotkey,
                    leader_before_github_url = EXCLUDED.leader_before_github_url,
                    leader_before_reward = EXCLUDED.leader_before_reward,
                    candidate_miner_uid = EXCLUDED.candidate_miner_uid,
                    candidate_miner_hotkey = EXCLUDED.candidate_miner_hotkey,
                    candidate_github_url = EXCLUDED.candidate_github_url,
                    candidate_reward = EXCLUDED.candidate_reward,
                    leader_after_miner_uid = EXCLUDED.leader_after_miner_uid,
                    leader_after_miner_hotkey = EXCLUDED.leader_after_miner_hotkey,
                    leader_after_github_url = EXCLUDED.leader_after_github_url,
                    leader_after_reward = EXCLUDED.leader_after_reward,
                    required_improvement_pct = EXCLUDED.required_improvement_pct,
                    required_reward_to_dethrone = EXCLUDED.required_reward_to_dethrone,
                    dethroned = EXCLUDED.dethroned,
                    validators_count = EXCLUDED.validators_count,
                    miners_evaluated = EXCLUDED.miners_evaluated,
                    tasks_evaluated = EXCLUDED.tasks_evaluated,
                    tasks_success = EXCLUDED.tasks_success,
                    avg_reward = EXCLUDED.avg_reward,
                    avg_eval_score = EXCLUDED.avg_eval_score,
                    avg_eval_time = EXCLUDED.avg_eval_time,
                    avg_eval_cost = EXCLUDED.avg_eval_cost,
                    leader_after_eval_score = EXCLUDED.leader_after_eval_score,
                    leader_after_eval_time = EXCLUDED.leader_after_eval_time,
                    leader_after_eval_cost = EXCLUDED.leader_after_eval_cost,
                    post_consensus_json = COALESCE(EXCLUDED.post_consensus_json, round_summary.post_consensus_json),
                    updated_at = NOW()
                WHERE NOT COALESCE(round_summary.source_is_main_validator, FALSE)
                   OR COALESCE(EXCLUDED.source_is_main_validator, FALSE)
                """
            ),
            {
                "round_id": round_id,
                "source_round_validator_id": source_round_validator_id,
                "source_validator_uid": source_validator_uid,
                "source_is_main_validator": source_is_main_validator,
                "leader_before_miner_uid": leader_before_uid,
                "leader_before_miner_hotkey": leader_before_hotkey,
                "leader_before_github_url": leader_before_github_url,
                "leader_before_reward": leader_before_reward,
                "candidate_miner_uid": candidate_uid,
                "candidate_miner_hotkey": candidate_hotkey,
                "candidate_github_url": candidate_github_url,
                "candidate_reward": candidate_reward,
                "leader_after_miner_uid": leader_after_uid,
                "leader_after_miner_hotkey": leader_after_hotkey,
                "leader_after_github_url": leader_after_github_url,
                "leader_after_reward": leader_after_reward,
                "required_improvement_pct": required_improvement_pct,
                "required_reward_to_dethrone": (leader_before_reward * (1.0 + required_improvement_pct)) if leader_before_reward is not None else None,
                "dethroned": dethroned,
                "validators_count": validators_count,
                "miners_evaluated": len(competitive_rows),
                "tasks_evaluated": tasks_evaluated,
                "tasks_success": tasks_success,
                "avg_reward": (sum(rewards) / len(rewards)) if rewards else 0.0,
                "avg_eval_score": avg_all_eval_score,
                "avg_eval_time": avg_all_eval_time,
                "avg_eval_cost": avg_all_eval_cost,
                "leader_after_eval_score": leader_eval_score,
                "leader_after_eval_time": leader_eval_time,
                "leader_after_eval_cost": leader_eval_cost,
                "post_consensus_json": json.dumps(post_payload),
            },
        )

        season_id = await self.session.scalar(text("SELECT season_id FROM rounds WHERE round_id = :round_id LIMIT 1"), {"round_id": round_id})
        if season_id is not None:
            await self._recompute_and_persist_season_leadership(int(season_id))
            await self._sync_season_bounds_from_rounds(int(season_id))

    async def _populate_round_summary(
        self,
        *,
        validator_round_id: str,
        local_evaluation: Optional[Dict[str, Any]] = None,
        post_consensus_evaluation: Optional[Dict[str, Any]] = None,
        subnet_price: Optional[float] = None,
    ) -> None:
        """Populate validator_round_summary_miners table from local_evaluation and post_consensus_evaluation.

        If no evaluation data is provided, creates basic summary records from agent_runs.
        """
        # Build a map of miner_uid -> summary data
        summary_map: Dict[int, Dict[str, Any]] = {}
        run_metrics_map: Dict[int, Dict[str, Any]] = {}

        # Always read local metrics from persisted agent runs as the source of truth.
        stmt_runs = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.validator_round_id == validator_round_id).where(AgentEvaluationRunORM.miner_uid.isnot(None))
        run_rows = await self.session.scalars(stmt_runs)
        for run_row in run_rows:
            if run_row.miner_uid is None:
                continue
            miner_uid = int(run_row.miner_uid)
            run_metrics_map[miner_uid] = {
                "miner_uid": miner_uid,
                "miner_hotkey": run_row.miner_hotkey,
                "local_avg_reward": run_row.average_reward,
                "local_avg_eval_score": run_row.average_score,
                "local_avg_eval_time": run_row.average_execution_time,
                "local_tasks_received": run_row.total_tasks,
                "local_tasks_success": run_row.success_tasks,
                "local_avg_eval_cost": None,  # populated below from llm_usage or reused source
            }
            summary_map.setdefault(miner_uid, {}).update(run_metrics_map[miner_uid])

        # Compute local_avg_eval_cost from evaluation_llm_usage for miners with stored evaluations.
        try:
            cost_result = await self.session.execute(
                text(
                    """
                    SELECT e.miner_uid,
                           AVG(agg.eval_total_cost) AS avg_cost
                    FROM (
                        SELECT elu.evaluation_id,
                               SUM(COALESCE(elu.cost, 0)) AS eval_total_cost
                        FROM evaluation_llm_usage elu
                        GROUP BY elu.evaluation_id
                    ) agg
                    JOIN evaluations e ON e.evaluation_id = agg.evaluation_id
                    WHERE e.validator_round_id = :validator_round_id
                      AND e.miner_uid IS NOT NULL
                      AND agg.eval_total_cost > 0
                    GROUP BY e.miner_uid
                    """
                ),
                {"validator_round_id": validator_round_id},
            )
            for cost_row in cost_result.mappings():
                uid = cost_row.get("miner_uid")
                avg_cost = cost_row.get("avg_cost")
                if uid is not None and avg_cost is not None:
                    uid_int = int(uid)
                    if uid_int in run_metrics_map:
                        run_metrics_map[uid_int]["local_avg_eval_cost"] = float(avg_cost)
                    if uid_int in summary_map:
                        summary_map[uid_int]["local_avg_eval_cost"] = float(avg_cost)
        except Exception:
            pass  # Non-critical: cost data stays NULL for this round

        # Process local_evaluation
        if local_evaluation and isinstance(local_evaluation, dict):
            local_miners = local_evaluation.get("miners", [])
            for miner_data in local_miners:
                if not isinstance(miner_data, dict):
                    continue
                miner_uid = miner_data.get("miner_uid")
                if miner_uid is None:
                    continue

                summary_map.setdefault(miner_uid, {})["miner_uid"] = int(miner_uid)
                miner_hotkey = miner_data.get("miner_hotkey") or run_metrics_map.get(int(miner_uid), {}).get("miner_hotkey")
                if miner_hotkey is not None:
                    summary_map[miner_uid]["miner_hotkey"] = miner_hotkey
                current_run = miner_data.get("current_run")
                if not isinstance(current_run, dict):
                    current_run = None

                if current_run is not None:
                    summary_map[miner_uid]["local_avg_reward"] = current_run.get("reward")
                    summary_map[miner_uid]["local_avg_eval_score"] = current_run.get("score")
                    summary_map[miner_uid]["local_avg_eval_time"] = current_run.get("time")
                    summary_map[miner_uid]["local_avg_eval_cost"] = current_run.get("cost")
                    summary_map[miner_uid]["local_tasks_received"] = current_run.get("tasks_received")
                    summary_map[miner_uid]["local_tasks_success"] = current_run.get("tasks_success")
                else:
                    # No current_run means no local execution happened in this round.
                    # Keep the round-local fields empty instead of leaking
                    # best historical values or persisted run history.
                    summary_map[miner_uid]["local_avg_reward"] = None
                    summary_map[miner_uid]["local_avg_eval_score"] = None
                    summary_map[miner_uid]["local_avg_eval_time"] = None
                    summary_map[miner_uid]["local_avg_eval_cost"] = None
                    summary_map[miner_uid]["local_tasks_received"] = None
                    summary_map[miner_uid]["local_tasks_success"] = None

        # Process post_consensus_evaluation
        if post_consensus_evaluation and isinstance(post_consensus_evaluation, dict):
            post_consensus_miners = post_consensus_evaluation.get("miners", [])
            for miner_data in post_consensus_miners:
                if not isinstance(miner_data, dict):
                    continue
                miner_uid = miner_data.get("miner_uid", miner_data.get("uid"))
                if miner_uid is None:
                    continue

                summary_map.setdefault(miner_uid, {})["miner_uid"] = int(miner_uid)
                # Update miner_hotkey if not already set or if post_consensus has it
                if "miner_hotkey" not in summary_map[miner_uid] or summary_map[miner_uid]["miner_hotkey"] is None:
                    summary_map[miner_uid]["miner_hotkey"] = miner_data.get("miner_hotkey", miner_data.get("hotkey"))

                best_run_consensus = miner_data.get("best_run_consensus") if isinstance(miner_data.get("best_run_consensus"), dict) else None
                if best_run_consensus is not None:
                    summary_map[miner_uid]["post_consensus_rank"] = best_run_consensus.get("rank")
                    summary_map[miner_uid]["post_consensus_avg_reward"] = best_run_consensus.get("reward")
                    summary_map[miner_uid]["post_consensus_avg_eval_score"] = best_run_consensus.get("score")
                    summary_map[miner_uid]["post_consensus_avg_eval_time"] = best_run_consensus.get("time")
                    summary_map[miner_uid]["post_consensus_avg_eval_cost"] = best_run_consensus.get("cost")
                    summary_map[miner_uid]["post_consensus_tasks_received"] = best_run_consensus.get("tasks_received")
                    summary_map[miner_uid]["post_consensus_tasks_success"] = best_run_consensus.get("tasks_success")
                    summary_map[miner_uid]["weight"] = best_run_consensus.get("weight")
                else:
                    summary_map[miner_uid]["post_consensus_rank"] = miner_data.get("rank")
                    summary_map[miner_uid]["post_consensus_avg_reward"] = miner_data.get("consensus_reward")
                    summary_map[miner_uid]["post_consensus_avg_eval_score"] = miner_data.get("avg_eval_score")
                    summary_map[miner_uid]["post_consensus_avg_eval_time"] = miner_data.get("avg_eval_time")
                    summary_map[miner_uid]["post_consensus_avg_eval_cost"] = miner_data.get("avg_cost")
                    summary_map[miner_uid]["post_consensus_tasks_received"] = miner_data.get("tasks_sent")
                    summary_map[miner_uid]["post_consensus_tasks_success"] = miner_data.get("tasks_success")
                    summary_map[miner_uid]["weight"] = miner_data.get("weight")
                # Add subnet_price to all miners in this round
                if subnet_price is not None:
                    summary_map[miner_uid]["subnet_price"] = float(subnet_price)

        # Consistency rule:
        # If this round only has one validator, post-consensus metrics should not
        # contradict local metrics. In reused/timeout flows, post-consensus payloads
        # can contain zeros (tasks_sent=0, avg_eval_time=0) even though local metrics
        # are valid. Normalize post-consensus from local in that case.
        validators_count_in_round = await self.session.scalar(
            select(func.count(func.distinct(ValidatorRoundValidatorORM.validator_uid))).where(ValidatorRoundValidatorORM.validator_round_id == validator_round_id)
        )
        if int(validators_count_in_round or 0) == 1:
            for _miner_uid, data in summary_map.items():
                local_tasks = int(data.get("local_tasks_received") or 0)
                post_tasks = int(data.get("post_consensus_tasks_received") or 0)
                if local_tasks <= 0:
                    continue
                if post_tasks <= 0:
                    data["post_consensus_tasks_received"] = local_tasks
                    data["post_consensus_tasks_success"] = int(data.get("local_tasks_success") or 0)
                if float(data.get("post_consensus_avg_eval_time") or 0.0) <= 0.0:
                    data["post_consensus_avg_eval_time"] = data.get("local_avg_eval_time")
                if float(data.get("post_consensus_avg_eval_cost") or 0.0) <= 0.0:
                    data["post_consensus_avg_eval_cost"] = data.get("local_avg_eval_cost")
                if float(data.get("post_consensus_avg_eval_score") or 0.0) <= 0.0:
                    data["post_consensus_avg_eval_score"] = data.get("local_avg_eval_score")
                if float(data.get("post_consensus_avg_reward") or 0.0) <= 0.0:
                    data["post_consensus_avg_reward"] = data.get("local_avg_reward")

        # Upsert summary records
        for miner_uid, summary_data in summary_map.items():
            stmt = select(ValidatorRoundSummaryORM).where(
                ValidatorRoundSummaryORM.validator_round_id == validator_round_id,
                ValidatorRoundSummaryORM.miner_uid == miner_uid,
            )
            existing = await self.session.scalar(stmt)

            # Ensure subnet_price is set for all records (use provided value or keep existing)
            if subnet_price is not None and "subnet_price" not in summary_data:
                summary_data["subnet_price"] = float(subnet_price)

            if existing:
                # Update existing record
                for key, value in summary_data.items():
                    if key != "miner_uid":  # Don't update the primary key
                        setattr(existing, key, value)
                # Update subnet_price if not in summary_data but provided
                if subnet_price is not None and existing.subnet_price is None:
                    existing.subnet_price = float(subnet_price)
            else:
                # Create new record
                new_summary = ValidatorRoundSummaryORM(validator_round_id=validator_round_id, **summary_data)
                self.session.add(new_summary)

        # Write local_avg_eval_cost directly to round_validator_miners.
        # The compat trigger that maps validator_round_summary_miners → round_validator_miners
        # predates the cost column and does not propagate it. We use a direct SQL UPDATE.
        cost_by_miner = {int(uid): data["local_avg_eval_cost"] for uid, data in summary_map.items() if data.get("local_avg_eval_cost") is not None and float(data["local_avg_eval_cost"] or 0) > 0}
        if cost_by_miner:
            try:
                for uid_int, cost_val in cost_by_miner.items():
                    await self.session.execute(
                        text(
                            """
                            UPDATE round_validator_miners rvm
                            SET local_avg_eval_cost = :cost, updated_at = NOW()
                            FROM round_validators rv
                            WHERE rv.round_validator_id = rvm.round_validator_id
                              AND rv.validator_round_id = :validator_round_id
                              AND rvm.miner_uid = :miner_uid
                              AND (rvm.local_avg_eval_cost IS NULL OR rvm.local_avg_eval_cost = 0)
                            """
                        ),
                        {
                            "validator_round_id": validator_round_id,
                            "miner_uid": uid_int,
                            "cost": float(cost_val),
                        },
                    )
            except Exception:
                pass  # Non-critical: cost write failure should not block round finalization

        # Canonicalize post-consensus metrics across all validators in the same round.
        # Reward/rank/weight come from consensus payloads and may contain tiny per-validator
        # drifts; non-reward metrics must be globally consistent for round-level traceability.
        # We derive a canonical post-consensus view per miner using:
        # - rank/reward/weight aggregated from post-consensus fields
        # - eval/time/tasks aggregated from local execution metrics
        # Then write that same canonical payload to every validator_round_id in this round.
        await self.session.execute(
            text(
                """
                WITH target_round AS (
                    SELECT rv.round_id
                    FROM round_validators rv
                    WHERE rv.validator_round_id = :validator_round_id
                    LIMIT 1
                ),
                target_validator_rounds AS (
                    SELECT rv.validator_round_id
                    FROM round_validators rv
                    JOIN target_round tr ON tr.round_id = rv.round_id
                ),
                effective_per_validator_miner AS (
                    SELECT
                        vrs.miner_uid,
                        vrs.post_consensus_rank,
                        vrs.post_consensus_avg_reward,
                        vrs.post_consensus_avg_eval_score,
                        vrs.weight,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN COALESCE(vrs.local_tasks_received, 0)
                            ELSE COALESCE(vrs.post_consensus_tasks_received, 0)
                        END AS effective_tasks_received,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN COALESCE(vrs.local_tasks_success, 0)
                            ELSE COALESCE(vrs.post_consensus_tasks_success, 0)
                        END AS effective_tasks_success,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN vrs.local_avg_eval_time
                            ELSE vrs.post_consensus_avg_eval_time
                        END AS effective_eval_time,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN vrs.local_avg_eval_cost
                            ELSE vrs.post_consensus_avg_eval_cost
                        END AS effective_eval_cost
                    FROM validator_round_summary_miners vrs
                    JOIN target_validator_rounds tvr ON tvr.validator_round_id = vrs.validator_round_id
                ),
                canonical_per_miner AS (
                    SELECT
                        epvm.miner_uid,
                        MIN(epvm.post_consensus_rank) FILTER (WHERE epvm.post_consensus_rank IS NOT NULL) AS canonical_rank,
                        AVG(epvm.post_consensus_avg_reward) FILTER (WHERE epvm.post_consensus_avg_reward IS NOT NULL) AS canonical_reward,
                        CASE
                            WHEN SUM(epvm.weight) FILTER (WHERE epvm.post_consensus_avg_eval_score IS NOT NULL AND epvm.weight IS NOT NULL) > 0
                            THEN SUM(epvm.post_consensus_avg_eval_score * epvm.weight) FILTER (WHERE epvm.post_consensus_avg_eval_score IS NOT NULL AND epvm.weight IS NOT NULL)
                                 / SUM(epvm.weight) FILTER (WHERE epvm.post_consensus_avg_eval_score IS NOT NULL AND epvm.weight IS NOT NULL)
                            ELSE AVG(epvm.post_consensus_avg_eval_score) FILTER (WHERE epvm.post_consensus_avg_eval_score IS NOT NULL)
                        END AS canonical_eval_score,
                        AVG(epvm.weight) FILTER (WHERE epvm.weight IS NOT NULL) AS canonical_weight,
                        SUM(COALESCE(epvm.effective_tasks_received, 0))::INTEGER AS canonical_tasks_received,
                        SUM(COALESCE(epvm.effective_tasks_success, 0))::INTEGER AS canonical_tasks_success,
                        CASE
                            WHEN SUM(COALESCE(epvm.effective_tasks_received, 0)) > 0
                            THEN SUM(COALESCE(epvm.effective_eval_time, 0) * COALESCE(epvm.effective_tasks_received, 0))::DOUBLE PRECISION
                                 / SUM(COALESCE(epvm.effective_tasks_received, 0))::DOUBLE PRECISION
                            ELSE NULL
                        END AS canonical_eval_time,
                        CASE
                            WHEN SUM(COALESCE(epvm.effective_tasks_received, 0)) FILTER (WHERE epvm.effective_eval_cost IS NOT NULL) > 0
                            THEN SUM((COALESCE(epvm.effective_eval_cost, 0) * COALESCE(epvm.effective_tasks_received, 0))::DOUBLE PRECISION)
                                 FILTER (WHERE epvm.effective_eval_cost IS NOT NULL)
                                 / SUM((COALESCE(epvm.effective_tasks_received, 0))::DOUBLE PRECISION)
                                   FILTER (WHERE epvm.effective_eval_cost IS NOT NULL)
                            ELSE NULL
                        END AS canonical_eval_cost
                    FROM effective_per_validator_miner epvm
                    GROUP BY epvm.miner_uid
                )
                UPDATE validator_round_summary_miners vrs
                SET
                    post_consensus_rank = COALESCE(cpm.canonical_rank, vrs.post_consensus_rank),
                    post_consensus_avg_reward = COALESCE(cpm.canonical_reward, vrs.post_consensus_avg_reward),
                    post_consensus_avg_eval_score = COALESCE(cpm.canonical_eval_score, vrs.post_consensus_avg_eval_score),
                    post_consensus_avg_eval_time = COALESCE(cpm.canonical_eval_time, vrs.post_consensus_avg_eval_time),
                    post_consensus_avg_eval_cost = COALESCE(cpm.canonical_eval_cost, vrs.post_consensus_avg_eval_cost),
                    post_consensus_tasks_received = COALESCE(cpm.canonical_tasks_received, vrs.post_consensus_tasks_received),
                    post_consensus_tasks_success = COALESCE(cpm.canonical_tasks_success, vrs.post_consensus_tasks_success),
                    weight = COALESCE(cpm.canonical_weight, vrs.weight),
                    updated_at = NOW()
                FROM canonical_per_miner cpm
                WHERE vrs.validator_round_id IN (SELECT validator_round_id FROM target_validator_rounds)
                  AND vrs.miner_uid = cpm.miner_uid
                """
            ),
            {"validator_round_id": validator_round_id},
        )

        # Materialize best_local_* as the best LOCAL historical mark achieved by
        # this validator for each miner up to and including the current round.
        await self.session.execute(
            text(
                """
                WITH target_rows AS (
                    SELECT
                        rvm.id AS target_id,
                        rvm.miner_uid,
                        rv.validator_uid,
                        rv.season_number,
                        rv.round_number_in_season
                    FROM round_validator_miners rvm
                    JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                    WHERE rv.validator_round_id = :validator_round_id
                ),
                history_rows AS (
                    SELECT
                        tr.target_id,
                        COALESCE(hist.local_rank, 9999) AS best_local_rank,
                        COALESCE(hist.local_avg_reward, 0) AS best_local_reward,
                        COALESCE(hist.local_avg_eval_score, 0) AS best_local_eval_score,
                        COALESCE(hist.local_avg_eval_time, 0) AS best_local_eval_time,
                        COALESCE(hist.local_tasks_received, 0) AS best_local_tasks_received,
                        COALESCE(hist.local_tasks_success, 0) AS best_local_tasks_success,
                        COALESCE(hist.local_avg_eval_cost, 0) AS best_local_eval_cost,
                        ROW_NUMBER() OVER (
                            PARTITION BY tr.target_id
                            ORDER BY
                                COALESCE(hist.local_avg_reward, 0) DESC,
                                COALESCE(hist.local_rank, 9999) ASC,
                                rvh.round_number_in_season ASC,
                                hist.id ASC
                        ) AS rn
                    FROM target_rows tr
                    JOIN round_validators rvh
                      ON rvh.validator_uid = tr.validator_uid
                     AND rvh.season_number = tr.season_number
                     AND rvh.round_number_in_season <= tr.round_number_in_season
                    JOIN (
                        SELECT
                            rvm.id,
                            rvm.round_validator_id,
                            rvm.miner_uid,
                            rvm.local_avg_reward,
                            rvm.local_avg_eval_score,
                            rvm.local_avg_eval_time,
                            rvm.local_avg_eval_cost,
                            rvm.local_tasks_received,
                            rvm.local_tasks_success,
                            ROW_NUMBER() OVER (
                                PARTITION BY rvm.round_validator_id
                                ORDER BY
                                    COALESCE(rvm.local_avg_reward, 0) DESC,
                                    COALESCE(rvm.local_avg_eval_score, 0) DESC,
                                    COALESCE(rvm.miner_uid, 2147483647) ASC
                            ) AS local_rank
                        FROM round_validator_miners rvm
                        WHERE COALESCE(rvm.local_tasks_received, 0) > 0
                    ) hist
                      ON hist.round_validator_id = rvh.round_validator_id
                     AND hist.miner_uid = tr.miner_uid
                    WHERE rvh.validator_uid = tr.validator_uid
                )
                UPDATE round_validator_miners rvm
                SET
                    best_local_rank = hr.best_local_rank,
                    best_local_reward = hr.best_local_reward,
                    best_local_eval_score = hr.best_local_eval_score,
                    best_local_eval_time = hr.best_local_eval_time,
                    best_local_tasks_received = hr.best_local_tasks_received,
                    best_local_tasks_success = hr.best_local_tasks_success,
                    best_local_eval_cost = hr.best_local_eval_cost,
                    updated_at = NOW()
                FROM history_rows hr
                WHERE rvm.id = hr.target_id
                  AND hr.rn = 1
                """
            ),
            {"validator_round_id": validator_round_id},
        )

        # Backfill miner identity for reused rounds from the latest known identity
        # of the same validator/miner within the season.
        await self.session.execute(
            text(
                """
                WITH target_rows AS (
                    SELECT
                        rvm.id AS target_id,
                        rvm.miner_uid,
                        rv.validator_uid,
                        rv.season_number,
                        rv.round_number_in_season
                    FROM round_validator_miners rvm
                    JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                    WHERE rv.validator_round_id = :validator_round_id
                ),
                identity_rows AS (
                    SELECT
                        tr.target_id,
                        hist.name,
                        hist.github_url,
                        hist.image_url,
                        ROW_NUMBER() OVER (
                            PARTITION BY tr.target_id
                            ORDER BY rvh.round_number_in_season DESC, hist.updated_at DESC NULLS LAST, hist.id DESC
                        ) AS rn
                    FROM target_rows tr
                    JOIN round_validators rvh
                      ON rvh.validator_uid = tr.validator_uid
                     AND rvh.season_number = tr.season_number
                     AND rvh.round_number_in_season <= tr.round_number_in_season
                    JOIN round_validator_miners hist
                      ON hist.round_validator_id = rvh.round_validator_id
                     AND hist.miner_uid = tr.miner_uid
                    WHERE hist.name IS NOT NULL
                       OR hist.github_url IS NOT NULL
                       OR hist.image_url IS NOT NULL
                )
                UPDATE round_validator_miners rvm
                SET
                    name = COALESCE(rvm.name, ir.name),
                    github_url = COALESCE(rvm.github_url, ir.github_url),
                    image_url = COALESCE(rvm.image_url, ir.image_url),
                    updated_at = NOW()
                FROM identity_rows ir
                WHERE rvm.id = ir.target_id
                  AND ir.rn = 1
                """
            ),
            {"validator_round_id": validator_round_id},
        )
