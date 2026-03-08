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
    def _apply_leadership_to_summary(
        *,
        summary: Dict[str, Any],
        winner_uid: Optional[int],
        winner_reward: Optional[float],
        reigning_uid_before_round: Optional[int],
        reigning_reward_before_round: Optional[float],
        top_candidate_uid: Optional[int],
        top_candidate_reward: Optional[float],
        required_improvement_pct: float,
        dethroned: bool,
        leader_uid_after_round: Optional[int],
        leader_reward_after_round: Optional[float],
    ) -> Dict[str, Any]:
        payload = dict(summary or {})
        round_summary = payload.get("round_summary") if isinstance(payload.get("round_summary"), dict) else {}
        decision = round_summary.get("decision") if isinstance(round_summary.get("decision"), dict) else {}
        winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
        season_summary = payload.get("season_summary") if isinstance(payload.get("season_summary"), dict) else {}

        winner_obj.pop("score", None)
        winner_obj["miner_uid"] = winner_uid
        winner_obj["reward"] = winner_reward
        round_summary["winner"] = winner_obj
        round_summary.pop("miner_scores", None)

        decision.pop("reigning_score_before_round", None)
        decision.pop("top_candidate_score", None)
        decision["reigning_uid_before_round"] = reigning_uid_before_round
        decision["reigning_reward_before_round"] = reigning_reward_before_round
        decision["top_candidate_uid"] = top_candidate_uid
        decision["top_candidate_reward"] = top_candidate_reward
        decision["required_improvement_pct"] = required_improvement_pct
        decision["dethroned"] = dethroned
        round_summary["decision"] = decision
        payload["round_summary"] = round_summary

        season_summary.pop("winner_before_round_score", None)
        season_summary.pop("candidate_score", None)
        season_summary.pop("winner_after_round_score", None)
        season_summary.pop("current_winner_score", None)
        season_summary["required_improvement_pct"] = required_improvement_pct
        season_summary["winner_before_round_uid"] = reigning_uid_before_round
        season_summary["winner_before_round_reward"] = reigning_reward_before_round
        season_summary["candidate_uid"] = top_candidate_uid
        season_summary["candidate_reward"] = top_candidate_reward
        season_summary["winner_after_round_uid"] = leader_uid_after_round
        season_summary["winner_after_round_reward"] = leader_reward_after_round
        season_summary["current_winner_uid"] = leader_uid_after_round
        season_summary["current_winner_reward"] = leader_reward_after_round
        season_summary["dethroned"] = dethroned
        if dethroned:
            season_summary["round_result"] = "dethroned"
        elif leader_uid_after_round is not None:
            season_summary["round_result"] = "retained"
        else:
            season_summary["round_result"] = "no_winner"
        payload["season_summary"] = season_summary
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
                      rs.post_consensus_summary
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
        default_required_improvement_pct = 0.05

        for row in season_rows:
            round_id = int(row["round_id"])
            winner_uid = int(row["candidate_miner_uid"]) if row["candidate_miner_uid"] is not None else None
            winner_reward = float(row["candidate_reward"]) if row["candidate_reward"] is not None else None
            required_improvement_pct = float(row["required_improvement_pct"] or default_required_improvement_pct)

            reigning_uid_before_round = leader_uid
            reigning_reward_before_round = leader_reward
            top_candidate_uid = winner_uid
            top_candidate_reward = winner_reward
            dethroned = False

            if winner_uid is not None and winner_reward is not None:
                if leader_uid is None or leader_reward is None:
                    leader_uid = winner_uid
                    leader_reward = winner_reward
                elif winner_uid == leader_uid:
                    leader_reward = max(float(leader_reward), float(winner_reward))
                else:
                    dethrone_threshold = float(leader_reward) * (1.0 + required_improvement_pct)
                    if float(winner_reward) >= dethrone_threshold:
                        dethroned = True
                        leader_uid = winner_uid
                        leader_reward = winner_reward

            post_payload = self._apply_leadership_to_summary(
                summary=self._to_json_dict(row.get("post_consensus_summary")),
                winner_uid=winner_uid,
                winner_reward=winner_reward,
                reigning_uid_before_round=reigning_uid_before_round,
                reigning_reward_before_round=reigning_reward_before_round,
                top_candidate_uid=top_candidate_uid,
                top_candidate_reward=top_candidate_reward,
                required_improvement_pct=required_improvement_pct,
                dethroned=dethroned,
                leader_uid_after_round=leader_uid,
                leader_reward_after_round=leader_reward,
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
                      post_consensus_summary = CAST(:post_consensus_summary AS JSONB),
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
                    "post_consensus_summary": json.dumps(post_payload),
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

    async def _get_handshake_participant_uids(self, validator_round_id: str) -> set[int]:
        """Return miner UIDs that participated in handshake with valid required fields.

        Business round metrics (winner/miners_evaluated/rollups) must be based on
        this set, not on the full metagraph consensus vector.
        """
        rows = await self.session.execute(
            text(
                """
                SELECT DISTINCT rvm.miner_uid
                FROM round_validator_miners rvm
                JOIN round_validators rv ON rv.round_validator_id = rvm.round_validator_id
                WHERE rv.validator_round_id = :validator_round_id
                  AND rvm.name IS NOT NULL
                  AND rvm.github_url IS NOT NULL
                """
            ),
            {"validator_round_id": validator_round_id},
        )
        return {int(r.miner_uid) for r in rows if r.miner_uid is not None}

    async def _enrich_validator_summary_post_consensus_from_db(self, round_row: ValidatorRoundORM) -> None:
        """Enrich validator_summary.evaluation_post_consensus with DB-derived metrics.

        Source of truth is validator_round_summary_miners. This makes the summary explicit,
        debuggable, and consistent with APIs that read from round summary rows.
        """
        stmt_rows = select(ValidatorRoundSummaryORM).where(ValidatorRoundSummaryORM.validator_round_id == round_row.validator_round_id).order_by(ValidatorRoundSummaryORM.miner_uid.asc())
        summary_rows = list(await self.session.scalars(stmt_rows))
        if not summary_rows:
            return

        validators_count = int(
            await self.session.scalar(select(func.count(func.distinct(ValidatorRoundValidatorORM.validator_uid))).where(ValidatorRoundValidatorORM.validator_round_id == round_row.validator_round_id))
            or 0
        )

        handshake_uids = await self._get_handshake_participant_uids(round_row.validator_round_id)

        miners_payload: List[Dict[str, Any]] = []
        tasks_evaluated_total = 0
        tasks_success_total = 0
        rewards: List[float] = []
        eval_scores: List[float] = []
        eval_times: List[float] = []

        burn_uid = int(settings.BURN_UID)

        for row in summary_rows:
            post_tasks_received = int(row.post_consensus_tasks_received or 0)
            post_tasks_success = int(row.post_consensus_tasks_success or 0)
            is_burn_row = int(row.miner_uid or -1) == burn_uid
            is_handshake_participant = int(row.miner_uid or -1) in handshake_uids
            if not is_burn_row and is_handshake_participant:
                tasks_evaluated_total += post_tasks_received
                tasks_success_total += post_tasks_success
                if row.post_consensus_avg_reward is not None:
                    rewards.append(float(row.post_consensus_avg_reward))
                if row.post_consensus_avg_eval_score is not None:
                    eval_scores.append(float(row.post_consensus_avg_eval_score))
                if row.post_consensus_avg_eval_time is not None:
                    eval_times.append(float(row.post_consensus_avg_eval_time))

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
                    "is_handshake_participant": is_handshake_participant,
                }
            )

        competitive_miners_payload = [m for m in miners_payload if int(m.get("miner_uid") or -1) != burn_uid and bool(m.get("is_handshake_participant"))]
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

        validator_summary = dict(round_row.validator_summary or {})
        current_post = validator_summary.get("evaluation_post_consensus")
        if isinstance(current_post, dict):
            enriched_post = dict(current_post)
        elif current_post is None:
            enriched_post = {}
        else:
            enriched_post = {"raw": current_post}

        # Keep business-facing summary focused on active handshake participants.
        enriched_post["miners"] = competitive_miners_payload
        # Avoid noisy internal fields in post-consensus payload.
        enriched_post.pop("schema_version", None)
        winner_uid = db_rollup.get("winner", {}).get("miner_uid") if isinstance(db_rollup.get("winner"), dict) else None
        round_summary = enriched_post.get("round_summary") if isinstance(enriched_post.get("round_summary"), dict) else {}
        winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
        winner_obj.pop("score", None)
        if winner_uid is not None:
            winner_obj["miner_uid"] = int(winner_uid)
        else:
            winner_obj.pop("miner_uid", None)
            winner_obj.pop("uid", None)
            winner_obj["reason"] = "no_handshake_participants"
        round_summary["winner"] = winner_obj
        round_summary.pop("miner_scores", None)
        enriched_post["round_summary"] = round_summary

        decision_obj = round_summary.get("decision") if isinstance(round_summary.get("decision"), dict) else {}
        season_summary = enriched_post.get("season_summary") if isinstance(enriched_post.get("season_summary"), dict) else {}
        decision_obj.pop("reigning_score_before_round", None)
        decision_obj.pop("top_candidate_score", None)
        decision_obj.pop("required_score_to_dethrone", None)
        season_summary.pop("winner_before_round_score", None)
        season_summary.pop("candidate_score", None)
        season_summary.pop("winner_after_round_score", None)
        season_summary.pop("current_winner_score", None)
        season_summary.pop("dethrone_threshold_score", None)
        if winner_uid is not None:
            season_summary["current_winner_uid"] = int(winner_uid)
        else:
            season_summary.pop("current_winner_uid", None)
        if "dethroned" not in season_summary:
            season_summary["dethroned"] = bool(decision_obj.get("dethroned", False))

        # Human-readable transition block: before -> candidate -> after.
        reigning_uid_before = decision_obj.get("reigning_uid_before_round")
        reigning_reward_before = decision_obj.get("reigning_reward_before_round", decision_obj.get("reigning_score_before_round"))
        top_candidate_uid = decision_obj.get("top_candidate_uid")
        top_candidate_reward = decision_obj.get("top_candidate_reward", decision_obj.get("top_candidate_score"))
        required_improvement_pct = season_summary.get(
            "required_improvement_pct",
            decision_obj.get("required_improvement_pct"),
        )
        winner_after_uid = winner_obj.get("miner_uid") or winner_obj.get("uid")
        winner_after_reward = winner_obj.get("reward", winner_obj.get("score"))

        try:
            req_pct_f = float(required_improvement_pct) if required_improvement_pct is not None else None
        except Exception:
            req_pct_f = None
        try:
            reigning_reward_f = float(reigning_reward_before) if reigning_reward_before is not None else None
        except Exception:
            reigning_reward_f = None
        try:
            candidate_reward_f = float(top_candidate_reward) if top_candidate_reward is not None else None
        except Exception:
            candidate_reward_f = None

        ranked_competitors: list[tuple[float, float, int, dict[str, Any]]] = []
        for miner in competitive_miners_payload:
            if not isinstance(miner, dict):
                continue
            try:
                miner_uid = int(miner.get("miner_uid", miner.get("uid")))
            except Exception:
                continue
            best_run = miner.get("best_run_consensus") if isinstance(miner.get("best_run_consensus"), dict) else miner
            try:
                reward = float(best_run.get("reward", miner.get("consensus_reward", 0.0)) or 0.0)
                score = float(best_run.get("score", miner.get("avg_eval_score", 0.0)) or 0.0)
            except Exception:
                continue
            ranked_competitors.append((reward, score, -miner_uid, miner))
        ranked_competitors.sort(reverse=True)

        challenger_payload = None
        if reigning_uid_before is not None:
            try:
                reigning_uid_before_i = int(reigning_uid_before)
            except Exception:
                reigning_uid_before_i = None
            for _reward, _score, _neg_uid, miner in ranked_competitors:
                try:
                    miner_uid = int(miner.get("miner_uid", miner.get("uid")))
                except Exception:
                    continue
                if reigning_uid_before_i is not None and miner_uid == reigning_uid_before_i:
                    continue
                challenger_payload = miner
                break
        elif ranked_competitors:
            challenger_payload = ranked_competitors[0][3]

        if challenger_payload is not None:
            challenger_best = challenger_payload.get("best_run_consensus") if isinstance(challenger_payload.get("best_run_consensus"), dict) else challenger_payload
            try:
                top_candidate_uid = int(challenger_payload.get("miner_uid", challenger_payload.get("uid")))
            except Exception:
                top_candidate_uid = top_candidate_uid
            try:
                top_candidate_reward = float(challenger_best.get("reward", challenger_payload.get("consensus_reward")))
                candidate_reward_f = float(top_candidate_reward)
            except Exception:
                pass
        elif reigning_uid_before is not None:
            top_candidate_uid = None
            top_candidate_reward = None
            candidate_reward_f = None

        dethrone_threshold = reigning_reward_f * (1.0 + req_pct_f) if reigning_reward_f is not None and req_pct_f is not None else None
        candidate_met_threshold = candidate_reward_f >= dethrone_threshold if candidate_reward_f is not None and dethrone_threshold is not None else None

        season_summary["winner_before_round_uid"] = reigning_uid_before
        season_summary["winner_before_round_reward"] = reigning_reward_before
        season_summary["candidate_uid"] = top_candidate_uid
        season_summary["candidate_reward"] = top_candidate_reward
        season_summary["winner_after_round_uid"] = winner_after_uid
        season_summary["winner_after_round_reward"] = winner_after_reward
        season_summary["current_winner_reward"] = winner_after_reward
        season_summary["dethrone_threshold_reward"] = dethrone_threshold
        season_summary["candidate_met_threshold"] = candidate_met_threshold
        if season_summary.get("dethroned") is True:
            season_summary["round_result"] = "dethroned"
        elif winner_after_uid is not None:
            season_summary["round_result"] = "retained"
        else:
            season_summary["round_result"] = "no_winner"
        enriched_post["season_summary"] = season_summary

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

        # Keep round-level consensus decision as first-class columns in validator_rounds.
        winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
        round_row.leader_after_uid = _to_int(winner_obj.get("miner_uid") or winner_obj.get("uid"))
        round_row.leader_after_reward = _to_float(winner_obj.get("reward", winner_obj.get("score")))
        round_row.leader_before_uid = _to_int(decision_obj.get("reigning_uid_before_round"))
        round_row.leader_before_reward = _to_float(decision_obj.get("reigning_reward_before_round", decision_obj.get("reigning_score_before_round")))
        round_row.candidate_uid = _to_int(top_candidate_uid)
        round_row.candidate_reward = _to_float(top_candidate_reward)
        round_row.required_improvement_pct = _to_float(season_summary.get("required_improvement_pct", decision_obj.get("required_improvement_pct")))
        dethroned_value = season_summary.get("dethroned", decision_obj.get("dethroned"))
        round_row.dethroned = bool(dethroned_value) if dethroned_value is not None else None

        # Convenience top-level aliases to avoid ambiguity in consumers.
        # Always overwrite these rollups with DB-derived business metrics.
        enriched_post["validators_count"] = db_rollup["validators_count"]
        enriched_post["miners_evaluated"] = db_rollup["miners_evaluated"]
        enriched_post["tasks_evaluated"] = db_rollup["tasks_evaluated"]
        enriched_post["tasks_success"] = db_rollup["tasks_success"]
        # winner is already represented in round_summary; avoid duplicated winner blocks here.
        enriched_post.pop("winner", None)

        validator_summary["evaluation_post_consensus"] = enriched_post
        round_row.validator_summary = validator_summary

    async def _sync_round_validators_post_consensus_json(self, round_row: ValidatorRoundORM) -> None:
        """Mirror enriched post-consensus summary into round_validators JSON columns."""
        validator_summary = dict(getattr(round_row, "validator_summary", {}) or {})
        post_summary = validator_summary.get("evaluation_post_consensus")
        if not isinstance(post_summary, dict):
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
        if not summary_rows:
            return

        row_ctx = await self.session.execute(
            text(
                """
                SELECT rv.round_id, rv.round_validator_id
                FROM round_validators rv
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
        burn_uid = int(settings.BURN_UID)
        handshake_uids = await self._get_handshake_participant_uids(round_row.validator_round_id)
        competitive_rows = [row for row in summary_rows if int(row.miner_uid or -1) != burn_uid and int(row.miner_uid or -1) in handshake_uids]
        winner_row = next((row for row in competitive_rows if int(row.post_consensus_rank or 0) == 1), None)
        if winner_row is None:
            winner_row = max(
                competitive_rows,
                key=lambda row: (
                    float(row.post_consensus_avg_reward or 0.0),
                    float(row.post_consensus_avg_eval_score or 0.0),
                    -int(row.miner_uid or 0),
                ),
                default=None,
            )

        rewards = [float(row.post_consensus_avg_reward) for row in competitive_rows if row.post_consensus_avg_reward is not None]
        tasks_evaluated = sum(int(row.post_consensus_tasks_received or 0) for row in competitive_rows)
        tasks_success = sum(int(row.post_consensus_tasks_success or 0) for row in competitive_rows)

        # True averages across ALL competitive miners
        all_eval_scores = [float(row.post_consensus_avg_eval_score) for row in competitive_rows if row.post_consensus_avg_eval_score is not None]
        all_eval_times = [float(row.post_consensus_avg_eval_time) for row in competitive_rows if row.post_consensus_avg_eval_time is not None and float(row.post_consensus_avg_eval_time) > 0]
        all_eval_costs = [float(row.post_consensus_avg_eval_cost) for row in competitive_rows if row.post_consensus_avg_eval_cost is not None and float(row.post_consensus_avg_eval_cost) > 0]
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

        validator_summary = dict(getattr(round_row, "validator_summary", {}) or {})
        post_summary = validator_summary.get("evaluation_post_consensus")
        if not isinstance(post_summary, dict):
            post_summary = {}
        round_summary = post_summary.get("round_summary") if isinstance(post_summary.get("round_summary"), dict) else {}
        decision = round_summary.get("decision") if isinstance(round_summary.get("decision"), dict) else {}
        season_summary = post_summary.get("season_summary") if isinstance(post_summary.get("season_summary"), dict) else {}

        required_improvement_pct = (
            float(round_row.required_improvement_pct)
            if getattr(round_row, "required_improvement_pct", None) is not None
            else float(season_summary.get("required_improvement_pct", decision.get("required_improvement_pct", 0.05)) or 0.05)
        )
        dethroned = bool(round_row.dethroned) if getattr(round_row, "dethroned", None) is not None else bool(season_summary.get("dethroned", decision.get("dethroned", False)))

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
        candidate_row = None
        if leader_before_uid is not None:
            candidate_row = max(
                (row for row in competitive_rows if int(row.miner_uid or -1) != int(leader_before_uid)),
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
        leader_after_uid = int(winner_row.miner_uid) if winner_row is not None else None
        leader_before_hotkey, leader_before_github_url = await _lookup_round_miner_snapshot(leader_before_uid)
        candidate_hotkey, candidate_github_url = await _lookup_round_miner_snapshot(candidate_uid)
        leader_after_hotkey, leader_after_github_url = await _lookup_round_miner_snapshot(leader_after_uid)
        leader_before_reward = float(round_row.leader_before_reward) if getattr(round_row, "leader_before_reward", None) is not None else None
        candidate_reward = float(getattr(candidate_row, "post_consensus_avg_reward", 0.0) or 0.0) if candidate_row is not None else None
        leader_after_reward = float(getattr(winner_row, "post_consensus_avg_reward", 0.0) or 0.0) if winner_row is not None else None
        leader_eval_score = float(getattr(winner_row, "post_consensus_avg_eval_score", 0.0) or 0.0) if winner_row is not None else 0.0
        leader_eval_time = float(getattr(winner_row, "post_consensus_avg_eval_time", 0.0) or 0.0) if winner_row is not None else 0.0
        leader_eval_cost = float(getattr(winner_row, "post_consensus_avg_eval_cost")) if winner_row is not None and getattr(winner_row, "post_consensus_avg_eval_cost", None) is not None else None

        await self.session.execute(
            text(
                """
                INSERT INTO round_summary (
                    round_id,
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
                    post_consensus_summary,
                    created_at,
                    updated_at
                )
                VALUES (
                    :round_id,
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
                    CAST(:post_consensus_summary AS JSONB),
                    NOW(),
                    NOW()
                )
                ON CONFLICT (round_id) DO UPDATE SET
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
                    post_consensus_summary = COALESCE(EXCLUDED.post_consensus_summary, round_summary.post_consensus_summary),
                    updated_at = NOW()
                """
            ),
            {
                "round_id": round_id,
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
                "post_consensus_summary": json.dumps(post_summary),
            },
        )

        season_id = await self.session.scalar(text("SELECT season_id FROM rounds WHERE round_id = :round_id LIMIT 1"), {"round_id": round_id})
        if season_id is not None:
            await self._recompute_and_persist_season_leadership(int(season_id))

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

        # For reused miners (no evaluations stored), propagate local_avg_eval_cost from the
        # source agent_run's best historical round_validator_miners cost record.
        try:
            reused_cost_result = await self.session.execute(
                text(
                    """
                    SELECT mer.miner_uid,
                           rvm_src.local_avg_eval_cost AS source_cost
                    FROM miner_evaluation_runs mer
                    JOIN miner_evaluation_runs mer_src
                      ON mer_src.agent_run_id = mer.reused_from_agent_run_id
                    JOIN round_validators rv_src
                      ON rv_src.validator_round_id = mer_src.validator_round_id
                    JOIN round_validator_miners rvm_src
                      ON rvm_src.round_validator_id = rv_src.round_validator_id
                      AND rvm_src.miner_uid = mer.miner_uid
                    WHERE mer.validator_round_id = :validator_round_id
                      AND mer.is_reused = TRUE
                      AND mer.reused_from_agent_run_id IS NOT NULL
                      AND rvm_src.local_avg_eval_cost IS NOT NULL
                      AND rvm_src.local_avg_eval_cost > 0
                    ORDER BY rvm_src.local_avg_eval_cost DESC
                    """
                ),
                {"validator_round_id": validator_round_id},
            )
            for reused_row in reused_cost_result.mappings():
                uid = reused_row.get("miner_uid")
                source_cost = reused_row.get("source_cost")
                if uid is not None and source_cost is not None:
                    uid_int = int(uid)
                    # Only fill in if not already set from direct evaluations
                    if run_metrics_map.get(uid_int, {}).get("local_avg_eval_cost") is None:
                        run_metrics_map.setdefault(uid_int, {})["local_avg_eval_cost"] = float(source_cost)
                    if summary_map.get(uid_int, {}).get("local_avg_eval_cost") is None:
                        summary_map.setdefault(uid_int, {})["local_avg_eval_cost"] = float(source_cost)
        except Exception:
            pass  # Non-critical: cost propagation from reused source stays NULL

        # If no evaluation data provided, create basic summaries from agent_runs
        if not local_evaluation and not post_consensus_evaluation:
            pass

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
                run_metrics = run_metrics_map.get(int(miner_uid), {})
                miner_hotkey = miner_data.get("miner_hotkey") or run_metrics.get("miner_hotkey")
                if miner_hotkey is not None:
                    summary_map[miner_uid]["miner_hotkey"] = miner_hotkey
                best_run = miner_data.get("best_run")
                if not isinstance(best_run, dict):
                    best_run = miner_data.get("current_run")
                if not isinstance(best_run, dict):
                    best_run = None

                if best_run is not None:
                    summary_map[miner_uid]["local_avg_reward"] = best_run.get("reward")
                    summary_map[miner_uid]["local_avg_eval_score"] = best_run.get("score")
                    summary_map[miner_uid]["local_avg_eval_time"] = best_run.get("time")
                    summary_map[miner_uid]["local_avg_eval_cost"] = best_run.get("cost")
                    summary_map[miner_uid]["local_tasks_received"] = best_run.get("tasks_received")
                    summary_map[miner_uid]["local_tasks_success"] = best_run.get("tasks_success")
                else:
                    # Legacy fallback for older validators still sending flat local payloads.
                    summary_map[miner_uid]["local_avg_reward"] = run_metrics.get("local_avg_reward") if run_metrics.get("local_avg_reward") is not None else miner_data.get("avg_reward")
                    summary_map[miner_uid]["local_avg_eval_score"] = (
                        run_metrics.get("local_avg_eval_score") if run_metrics.get("local_avg_eval_score") is not None else miner_data.get("avg_eval_score")
                    )
                    summary_map[miner_uid]["local_avg_eval_time"] = (
                        run_metrics.get("local_avg_eval_time") if run_metrics.get("local_avg_eval_time") is not None else miner_data.get("avg_evaluation_time")
                    )
                    summary_map[miner_uid]["local_avg_eval_cost"] = run_metrics.get("local_avg_eval_cost")
                    summary_map[miner_uid]["local_tasks_received"] = (
                        run_metrics.get("local_tasks_received") if run_metrics.get("local_tasks_received") is not None else miner_data.get("tasks_attempted")
                    )
                    summary_map[miner_uid]["local_tasks_success"] = run_metrics.get("local_tasks_success") if run_metrics.get("local_tasks_success") is not None else miner_data.get("tasks_completed")

                summary_map[miner_uid]["is_reused"] = bool(miner_data.get("is_reused", False))
                summary_map[miner_uid]["reused_from_agent_run_id"] = miner_data.get("reused_from_agent_run_id")

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

            if "is_reused" in summary_data:
                await self._set_round_validator_miner_reuse_state(
                    validator_round_id=validator_round_id,
                    miner_uid=int(miner_uid),
                    is_reused=bool(summary_data.get("is_reused", False)),
                    reused_from_agent_run_id=summary_data.get("reused_from_agent_run_id"),
                )

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
                        vrs.weight,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN COALESCE(vrs.local_tasks_received, 0)
                            WHEN COALESCE(vrs.is_reused, FALSE) = TRUE AND COALESCE(vrs.best_local_tasks_received, 0) > 0 THEN COALESCE(vrs.best_local_tasks_received, 0)
                            ELSE 0
                        END AS effective_tasks_received,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN COALESCE(vrs.local_tasks_success, 0)
                            WHEN COALESCE(vrs.is_reused, FALSE) = TRUE AND COALESCE(vrs.best_local_tasks_received, 0) > 0 THEN COALESCE(vrs.best_local_tasks_success, 0)
                            ELSE 0
                        END AS effective_tasks_success,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN vrs.local_avg_eval_time
                            WHEN COALESCE(vrs.is_reused, FALSE) = TRUE AND COALESCE(vrs.best_local_tasks_received, 0) > 0 THEN vrs.best_local_eval_time
                            ELSE NULL
                        END AS effective_eval_time,
                        CASE
                            WHEN COALESCE(vrs.local_tasks_received, 0) > 0 THEN vrs.local_avg_eval_cost
                            WHEN COALESCE(vrs.is_reused, FALSE) = TRUE AND COALESCE(vrs.best_local_tasks_received, 0) > 0 THEN vrs.best_local_eval_cost
                            ELSE NULL
                        END AS effective_eval_cost
                    FROM validator_round_summary_miners vrs
                    JOIN target_validator_rounds tvr ON tvr.validator_round_id = vrs.validator_round_id
                ),
                canonical_per_miner AS (
                    SELECT
                        epvm.miner_uid,
                        MIN(epvm.post_consensus_rank) FILTER (WHERE epvm.post_consensus_rank IS NOT NULL) AS canonical_rank,
                        AVG(epvm.post_consensus_avg_reward) FILTER (WHERE epvm.post_consensus_avg_reward IS NOT NULL) AS canonical_reward,
                        AVG(epvm.weight) FILTER (WHERE epvm.weight IS NOT NULL) AS canonical_weight,
                        SUM(COALESCE(epvm.effective_tasks_received, 0))::INTEGER AS canonical_tasks_received,
                        SUM(COALESCE(epvm.effective_tasks_success, 0))::INTEGER AS canonical_tasks_success,
                        CASE
                            WHEN SUM(COALESCE(epvm.effective_tasks_received, 0)) > 0
                            THEN SUM(COALESCE(epvm.effective_tasks_success, 0))::DOUBLE PRECISION
                                 / SUM(COALESCE(epvm.effective_tasks_received, 0))::DOUBLE PRECISION
                            ELSE NULL
                        END AS canonical_eval_score,
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
                        COALESCE(hist.average_reward, 0) AS best_local_reward,
                        COALESCE(hist.average_score, 0) AS best_local_eval_score,
                        COALESCE(hist.average_execution_time, 0) AS best_local_eval_time,
                        COALESCE(hist.total_tasks, 0) AS best_local_tasks_received,
                        COALESCE(hist.success_tasks, 0) AS best_local_tasks_success,
                        COALESCE(hist.avg_cost_per_task, 0) AS best_local_eval_cost,
                        ROW_NUMBER() OVER (
                            PARTITION BY tr.target_id
                            ORDER BY
                                COALESCE(hist.average_reward, 0) DESC,
                                COALESCE(hist.local_rank, 9999) ASC,
                                rvh.round_number_in_season ASC,
                                hist.agent_run_id ASC
                        ) AS rn
                    FROM target_rows tr
                    JOIN round_validators rvh
                      ON rvh.validator_uid = tr.validator_uid
                     AND rvh.season_number = tr.season_number
                     AND rvh.round_number_in_season <= tr.round_number_in_season
                    JOIN (
                        SELECT
                            mer.agent_run_id,
                            mer.round_validator_id,
                            mer.miner_uid,
                            mer.average_reward,
                            mer.average_score,
                            mer.average_execution_time,
                            mer.total_tasks,
                            mer.success_tasks,
                            COALESCE(run_cost.avg_cost_per_task, 0) AS avg_cost_per_task,
                            ROW_NUMBER() OVER (
                                PARTITION BY mer.round_validator_id
                                ORDER BY
                                    COALESCE(mer.average_reward, 0) DESC,
                                    COALESCE(mer.average_score, 0) DESC,
                                    COALESCE(mer.miner_uid, 2147483647) ASC
                            ) AS local_rank
                        FROM miner_evaluation_runs mer
                        LEFT JOIN (
                            SELECT
                                per_eval.agent_run_id,
                                AVG(per_eval.eval_total_cost) AS avg_cost_per_task
                            FROM (
                                SELECT
                                    e.agent_run_id,
                                    e.evaluation_id,
                                    SUM(COALESCE(elu.cost, 0)) AS eval_total_cost
                                FROM evaluations e
                                JOIN evaluation_llm_usage elu ON elu.evaluation_id = e.evaluation_id
                                GROUP BY e.agent_run_id, e.evaluation_id
                            ) per_eval
                            GROUP BY per_eval.agent_run_id
                        ) run_cost
                          ON run_cost.agent_run_id = mer.agent_run_id
                        WHERE mer.miner_uid IS NOT NULL
                          AND COALESCE(mer.total_tasks, 0) > 0
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
