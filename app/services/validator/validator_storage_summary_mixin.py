from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, text

from app.db.models import AgentEvaluationRunORM, ValidatorRoundORM, ValidatorRoundSummaryORM, ValidatorRoundValidatorORM

logger = logging.getLogger(__name__)


class ValidatorStorageSummaryMixin:
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

        miners_payload: List[Dict[str, Any]] = []
        tasks_evaluated_total = 0
        tasks_success_total = 0
        rewards: List[float] = []
        eval_scores: List[float] = []
        eval_times: List[float] = []

        for row in summary_rows:
            post_tasks_received = int(row.post_consensus_tasks_received or 0)
            post_tasks_success = int(row.post_consensus_tasks_success or 0)
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
                }
            )

        winner = next((m for m in miners_payload if m.get("post_consensus_rank") == 1), None)
        if winner is None:
            winner = max(
                miners_payload,
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
            "miners_evaluated": len(miners_payload),
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

        enriched_post["miners"] = miners_payload
        # Avoid noisy internal fields in post-consensus payload.
        enriched_post.pop("schema_version", None)
        winner_uid = db_rollup.get("winner", {}).get("miner_uid") if isinstance(db_rollup.get("winner"), dict) else None
        round_summary = enriched_post.get("round_summary") if isinstance(enriched_post.get("round_summary"), dict) else {}
        winner_obj = round_summary.get("winner") if isinstance(round_summary.get("winner"), dict) else {}
        if winner_uid is not None:
            winner_obj["miner_uid"] = int(winner_uid)
        round_summary["winner"] = winner_obj
        enriched_post["round_summary"] = round_summary

        decision_obj = round_summary.get("decision") if isinstance(round_summary.get("decision"), dict) else {}
        season_summary = enriched_post.get("season_summary") if isinstance(enriched_post.get("season_summary"), dict) else {}
        if winner_uid is not None:
            season_summary["current_winner_uid"] = int(winner_uid)
        if "dethroned" not in season_summary:
            season_summary["dethroned"] = bool(decision_obj.get("dethroned", False))
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
        round_row.winner_uid = _to_int(winner_obj.get("miner_uid") or winner_obj.get("uid"))
        round_row.winner_score = _to_float(winner_obj.get("score"))
        round_row.reigning_uid_before_round = _to_int(decision_obj.get("reigning_uid_before_round"))
        round_row.reigning_score_before_round = _to_float(decision_obj.get("reigning_score_before_round"))
        round_row.top_candidate_uid = _to_int(decision_obj.get("top_candidate_uid"))
        round_row.top_candidate_score = _to_float(decision_obj.get("top_candidate_score"))
        round_row.required_improvement_pct = _to_float(season_summary.get("required_improvement_pct", decision_obj.get("required_improvement_pct")))
        dethroned_value = season_summary.get("dethroned", decision_obj.get("dethroned"))
        round_row.dethroned = bool(dethroned_value) if dethroned_value is not None else None

        # Convenience top-level aliases to avoid ambiguity in consumers.
        enriched_post.setdefault("validators_count", db_rollup["validators_count"])
        enriched_post.setdefault("miners_evaluated", db_rollup["miners_evaluated"])
        enriched_post.setdefault("tasks_evaluated", db_rollup["tasks_evaluated"])
        enriched_post.setdefault("tasks_success", db_rollup["tasks_success"])
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
                    post_consensus_summary = CAST(:post_consensus_json AS JSONB),
                    updated_at = NOW()
                WHERE validator_round_id = :validator_round_id
                """
            ),
            {
                "validator_round_id": round_row.validator_round_id,
                "post_consensus_json": json.dumps(post_summary),
            },
        )

    async def _upsert_round_outcome_from_summary(self, round_row: ValidatorRoundORM) -> None:
        """Populate round_outcomes with winner + rollups from validator_round_summary_miners."""
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
        source_round_validator_id = int(ctx.round_validator_id)
        winner_row = next((row for row in summary_rows if int(row.post_consensus_rank or 0) == 1), None)
        if winner_row is None:
            winner_row = max(
                summary_rows,
                key=lambda row: (
                    float(row.post_consensus_avg_reward or 0.0),
                    float(row.post_consensus_avg_eval_score or 0.0),
                    -int(row.miner_uid or 0),
                ),
            )

        rewards = [float(row.post_consensus_avg_reward) for row in summary_rows if row.post_consensus_avg_reward is not None]
        eval_scores = [float(row.post_consensus_avg_eval_score) for row in summary_rows if row.post_consensus_avg_eval_score is not None]
        eval_times = [float(row.post_consensus_avg_eval_time) for row in summary_rows if row.post_consensus_avg_eval_time is not None]
        tasks_evaluated = sum(int(row.post_consensus_tasks_received or 0) for row in summary_rows)
        tasks_success = sum(int(row.post_consensus_tasks_success or 0) for row in summary_rows)

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

        await self.session.execute(
            text(
                """
                INSERT INTO round_outcomes (
                    round_id,
                    winner_miner_uid,
                    winner_score,
                    reigning_miner_uid_before_round,
                    reigning_score_before_round,
                    top_candidate_miner_uid,
                    top_candidate_score,
                    required_improvement_pct,
                    dethroned,
                    validators_count,
                    miners_evaluated,
                    tasks_evaluated,
                    tasks_success,
                    avg_reward,
                    avg_eval_score,
                    avg_eval_time,
                    computed_at,
                    summary_json,
                    post_consensus_summary,
                    source_round_validator_id,
                    created_at,
                    updated_at
                )
                VALUES (
                    :round_id,
                    :winner_miner_uid,
                    :winner_score,
                    :reigning_miner_uid_before_round,
                    :reigning_score_before_round,
                    :top_candidate_miner_uid,
                    :top_candidate_score,
                    :required_improvement_pct,
                    :dethroned,
                    :validators_count,
                    :miners_evaluated,
                    :tasks_evaluated,
                    :tasks_success,
                    :avg_reward,
                    :avg_eval_score,
                    :avg_eval_time,
                    NOW(),
                    CAST(:summary_json AS JSONB),
                    CAST(:post_consensus_summary AS JSONB),
                    :source_round_validator_id,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (round_id) DO UPDATE SET
                    winner_miner_uid = EXCLUDED.winner_miner_uid,
                    winner_score = EXCLUDED.winner_score,
                    reigning_miner_uid_before_round = EXCLUDED.reigning_miner_uid_before_round,
                    reigning_score_before_round = EXCLUDED.reigning_score_before_round,
                    top_candidate_miner_uid = EXCLUDED.top_candidate_miner_uid,
                    top_candidate_score = EXCLUDED.top_candidate_score,
                    required_improvement_pct = EXCLUDED.required_improvement_pct,
                    dethroned = EXCLUDED.dethroned,
                    validators_count = EXCLUDED.validators_count,
                    miners_evaluated = EXCLUDED.miners_evaluated,
                    tasks_evaluated = EXCLUDED.tasks_evaluated,
                    tasks_success = EXCLUDED.tasks_success,
                    avg_reward = EXCLUDED.avg_reward,
                    avg_eval_score = EXCLUDED.avg_eval_score,
                    avg_eval_time = EXCLUDED.avg_eval_time,
                    computed_at = NOW(),
                    summary_json = COALESCE(EXCLUDED.summary_json, round_outcomes.summary_json),
                    post_consensus_summary = COALESCE(EXCLUDED.post_consensus_summary, round_outcomes.post_consensus_summary),
                    source_round_validator_id = EXCLUDED.source_round_validator_id,
                    updated_at = NOW()
                """
            ),
            {
                "round_id": round_id,
                "winner_miner_uid": int(getattr(winner_row, "miner_uid", 0) or 0),
                "winner_score": float(getattr(winner_row, "post_consensus_avg_reward", 0.0) or 0.0),
                "reigning_miner_uid_before_round": (int(round_row.reigning_uid_before_round) if getattr(round_row, "reigning_uid_before_round", None) is not None else None),
                "reigning_score_before_round": (float(round_row.reigning_score_before_round) if getattr(round_row, "reigning_score_before_round", None) is not None else None),
                "top_candidate_miner_uid": (int(round_row.top_candidate_uid) if getattr(round_row, "top_candidate_uid", None) is not None else None),
                "top_candidate_score": (float(round_row.top_candidate_score) if getattr(round_row, "top_candidate_score", None) is not None else None),
                "required_improvement_pct": required_improvement_pct,
                "dethroned": dethroned,
                "validators_count": validators_count,
                "miners_evaluated": len(summary_rows),
                "tasks_evaluated": tasks_evaluated,
                "tasks_success": tasks_success,
                "avg_reward": (sum(rewards) / len(rewards)) if rewards else 0.0,
                "avg_eval_score": (sum(eval_scores) / len(eval_scores)) if eval_scores else 0.0,
                "avg_eval_time": (sum(eval_times) / len(eval_times)) if eval_times else 0.0,
                "summary_json": json.dumps(post_summary),
                "post_consensus_summary": json.dumps(post_summary),
                "source_round_validator_id": source_round_validator_id,
            },
        )

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
            }
            summary_map.setdefault(miner_uid, {}).update(run_metrics_map[miner_uid])

        # If no evaluation data provided, create basic summaries from agent_runs
        if not local_evaluation and not post_consensus_evaluation:
            # When only runs are available, also derive deterministic local ranking.
            ranked_miners = sorted(
                run_metrics_map.items(),
                key=lambda kv: (
                    -float(kv[1].get("local_avg_reward") or 0.0),
                    -float(kv[1].get("local_avg_eval_score") or 0.0),
                    int(kv[0]),
                ),
            )
            for index, (miner_uid, _) in enumerate(ranked_miners, start=1):
                summary_map.setdefault(miner_uid, {})["local_rank"] = index

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

                local_rank = miner_data.get("rank")
                if local_rank is not None:
                    summary_map[miner_uid]["local_rank"] = local_rank

                # Local metrics must match persisted local execution (agent runs).
                # Only fall back to payload when run metrics are not available.
                summary_map[miner_uid]["local_avg_reward"] = run_metrics.get("local_avg_reward") if run_metrics.get("local_avg_reward") is not None else miner_data.get("avg_reward")
                summary_map[miner_uid]["local_avg_eval_score"] = run_metrics.get("local_avg_eval_score") if run_metrics.get("local_avg_eval_score") is not None else miner_data.get("avg_eval_score")
                summary_map[miner_uid]["local_avg_eval_time"] = run_metrics.get("local_avg_eval_time") if run_metrics.get("local_avg_eval_time") is not None else miner_data.get("avg_evaluation_time")
                summary_map[miner_uid]["local_tasks_received"] = run_metrics.get("local_tasks_received") if run_metrics.get("local_tasks_received") is not None else miner_data.get("tasks_attempted")
                summary_map[miner_uid]["local_tasks_success"] = run_metrics.get("local_tasks_success") if run_metrics.get("local_tasks_success") is not None else miner_data.get("tasks_completed")

        # If some local ranks are still missing but local metrics exist, derive from local_avg_reward.
        missing_rank_uids = [uid for uid, data in summary_map.items() if data.get("local_rank") is None and data.get("local_avg_reward") is not None]
        if missing_rank_uids:
            ranked_miners = sorted(
                missing_rank_uids,
                key=lambda uid: (
                    -float(summary_map[uid].get("local_avg_reward") or 0.0),
                    -float(summary_map[uid].get("local_avg_eval_score") or 0.0),
                    int(uid),
                ),
            )
            used_ranks = {int(data["local_rank"]) for data in summary_map.values() if data.get("local_rank") is not None}
            next_rank = 1
            for uid in ranked_miners:
                while next_rank in used_ranks:
                    next_rank += 1
                summary_map[uid]["local_rank"] = next_rank
                used_ranks.add(next_rank)
                next_rank += 1

        # Process post_consensus_evaluation
        if post_consensus_evaluation and isinstance(post_consensus_evaluation, dict):
            post_consensus_miners = post_consensus_evaluation.get("miners", [])
            for miner_data in post_consensus_miners:
                if not isinstance(miner_data, dict):
                    continue
                miner_uid = miner_data.get("miner_uid")
                if miner_uid is None:
                    continue

                summary_map.setdefault(miner_uid, {})["miner_uid"] = int(miner_uid)
                # Update miner_hotkey if not already set or if post_consensus has it
                if "miner_hotkey" not in summary_map[miner_uid] or summary_map[miner_uid]["miner_hotkey"] is None:
                    summary_map[miner_uid]["miner_hotkey"] = miner_data.get("miner_hotkey")

                summary_map[miner_uid]["post_consensus_rank"] = miner_data.get("rank")
                summary_map[miner_uid]["post_consensus_avg_reward"] = miner_data.get("consensus_reward")
                summary_map[miner_uid]["post_consensus_avg_eval_score"] = miner_data.get("avg_eval_score")
                summary_map[miner_uid]["post_consensus_avg_eval_time"] = miner_data.get("avg_eval_time")
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
