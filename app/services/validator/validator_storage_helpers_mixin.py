from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, select, text
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationLLMUsageORM,
    EvaluationORM,
    TaskORM,
    ValidatorRoundMinerORM,
    ValidatorRoundORM,
    ValidatorRoundValidatorORM,
)
from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
    Task,
    TaskSolution,
    ValidatorRound,
    ValidatorRoundMiner,
    ValidatorRoundSubmissionRequest,
    ValidatorRoundValidator,
)
from app.services.validator.validator_storage_common import (
    DuplicateIdentifierError,
    RoundConflictError,
    _action_dump,
    _agent_run_meta_for_storage,
    _clean_meta_dict,
    _non_empty_dict,
    _optional_dump,
)

logger = logging.getLogger(__name__)


class ValidatorStorageHelpersMixin:
    async def _close_stale_active_seasons_for_incoming(self, incoming_season_number: int) -> None:
        """
        Close older active seasons that no longer have active rounds.

        This prevents false conflicts like:
        "Cannot start season N: season N-1 is still active"
        when season N-1 already finished all its rounds but its season row
        remained active due missing finalize transition.
        """
        if incoming_season_number <= 0:
            return
        await self.session.execute(
            text(
                """
                WITH stale AS (
                    SELECT
                        s.season_id,
                        s.season_number,
                        COALESCE(
                            (
                                SELECT MAX(r.ended_at)
                                FROM rounds r
                                WHERE r.season_id = s.season_id
                                  AND r.ended_at IS NOT NULL
                            ),
                            NOW()
                        ) AS inferred_end_at
                    FROM seasons s
                    WHERE LOWER(COALESCE(s.status, '')) = 'active'
                      AND s.season_number < :incoming_season_number
                      AND NOT EXISTS (
                          SELECT 1
                          FROM rounds r
                          WHERE r.season_id = s.season_id
                            AND LOWER(COALESCE(r.status, '')) = 'active'
                      )
                )
                UPDATE seasons s
                SET
                    status = 'finished',
                    end_at = COALESCE(s.end_at, stale.inferred_end_at),
                    updated_at = NOW()
                FROM stale
                WHERE s.season_id = stale.season_id
                """
            ),
            {"incoming_season_number": int(incoming_season_number)},
        )

    async def _get_main_validator_cfg(self) -> tuple[Optional[int], str]:
        row = (
            await self.session.execute(
                text(
                    """
                    SELECT main_validator_uid, main_validator_hotkey
                    FROM app_runtime_config
                    WHERE id = 1
                    LIMIT 1
                    """
                )
            )
        ).first()
        if not row:
            return None, ""
        return row[0], (row[1] or "").strip()

    async def _is_highest_stake_backup(self, validator_uid: int, validator_stake: Optional[float]) -> bool:
        cfg_uid, _ = await self._get_main_validator_cfg()
        sql_text = """
            SELECT rv.validator_uid, MAX(COALESCE(rv.stake, 0)) AS max_stake
            FROM round_validators rv
            WHERE rv.validator_uid IS NOT NULL
        """
        params: dict[str, Any] = {}
        if cfg_uid is not None:
            sql_text += "\n  AND rv.validator_uid <> :main_uid"
            params["main_uid"] = int(cfg_uid)
        sql_text += """
            GROUP BY rv.validator_uid
            ORDER BY MAX(COALESCE(rv.stake, 0)) DESC, rv.validator_uid ASC
        """
        rows = (
            await self.session.execute(
                text(sql_text),
                params,
            )
        ).all()
        if not rows:
            return True
        top_uid = int(rows[0][0])
        top_stake = float(rows[0][1] or 0.0)
        current_stake = float(validator_stake or 0.0)
        if int(validator_uid) == top_uid:
            return True
        return current_stake >= top_stake and int(validator_uid) < top_uid

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _run_has_zero_score(run_row: AgentEvaluationRunORM) -> bool:
        """True if the run has effective score 0 (so it must have a zero_reason)."""
        avg = getattr(run_row, "average_score", None)
        if avg is not None and avg <= 0.0:
            return True
        total = getattr(run_row, "total_tasks", None) or 0
        success = getattr(run_row, "success_tasks", None) or 0
        return total > 0 and success == 0

    @staticmethod
    def _derive_run_zero_reason_from_evaluations(run_row: AgentEvaluationRunORM) -> str:
        """Derive zero_reason for a run with score 0 from its evaluations (never leave 0 without reason)."""
        evaluations = list(getattr(run_row, "evaluations", []) or [])
        zero_reasons: List[str] = []
        for ev in evaluations:
            score = getattr(ev, "evaluation_score", None)
            if score is not None and float(score) <= 0.0:
                reason = getattr(ev, "zero_reason", None)
                if reason:
                    zero_reasons.append(reason)
        if not zero_reasons:
            return "task_failed"
        # Use single most common reason (e.g. all task_timeout -> "task_timeout")
        (reason, _) = Counter(zero_reasons).most_common(1)[0]
        return reason

    def _compute_agent_run_stats(self, run_row: AgentEvaluationRunORM) -> Dict[str, Any]:
        task_solutions = list(getattr(run_row, "task_solutions", []) or [])
        evaluations = list(getattr(run_row, "evaluations", []) or [])

        # CRITICAL: total_tasks should be the number of evaluations, because each task has one evaluation
        # (even if the miner didn't respond, we create an evaluation with score 0.0)
        # So total_tasks = len(evaluations) is correct
        total_tasks = len(evaluations)

        # If no evaluations yet, fall back to counting unique task_ids from solutions
        if total_tasks == 0:
            task_ids = {solution.task_id for solution in task_solutions if solution.task_id}
            total_tasks = len(task_ids) if task_ids else (run_row.total_tasks or 0)

        total_tasks = int(total_tasks or 0)

        scores: List[float] = []
        for eval_obj in evaluations:
            value = self._to_float(getattr(eval_obj, "evaluation_score", None)) or self._to_float(getattr(eval_obj, "eval_score", None))
            # 🔍 CRITICAL: If no evaluation_score found, default to 0.0 (task failed)
            # This ensures average_score is NEVER None if there are evaluations
            # Each task should have an evaluation with evaluation_score (0.0 or 1.0)
            if value is not None:
                scores.append(value)
            else:
                # Evaluation exists but no score - treat as failed (0.0)
                # This should never happen if task_flow.py is working correctly,
                # but we handle it defensively
                scores.append(0.0)

        # 🔍 CRITICAL: average_score should NEVER be None if there are evaluations
        # If there are evaluations, we should always have scores (even if all are 0.0)
        # If there are no evaluations, average_score can be None (round not finished yet)
        if total_tasks > 0:
            # We have tasks (evaluations), so we must have scores
            average_score = sum(scores) / len(scores) if scores else 0.0
        else:
            # No tasks yet - average_score is None (round not finished)
            average_score = None

        # Binary scoring: evaluation_score is 1.0 if at least one test passed, 0.0 otherwise
        # success_tasks counts evaluations with evaluation_score > 0.0 (i.e., == 1.0)
        success_tasks = sum(1 for score in scores if score > 0.0)
        if success_tasks > total_tasks:
            success_tasks = total_tasks
        failed_tasks = max(total_tasks - success_tasks, 0)

        evaluation_times: List[float] = []
        for eval_obj in evaluations:
            value = self._to_float(getattr(eval_obj, "evaluation_time", None))
            if value is not None and value >= 0.0:
                evaluation_times.append(value)
        average_execution_time = sum(evaluation_times) / len(evaluation_times) if evaluation_times else None

        reward_values: List[float] = []
        for eval_obj in evaluations:
            reward_candidate: Any = getattr(eval_obj, "reward", None)
            if reward_candidate is None:
                meta = getattr(eval_obj, "meta", {}) or {}
                if isinstance(meta, dict):
                    reward_candidate = meta.get("reward") or meta.get("total_reward") or meta.get("final_reward")
            if reward_candidate is None:
                reward_candidate = getattr(eval_obj, "evaluation_score", None) or getattr(eval_obj, "eval_score", None)
            value = self._to_float(reward_candidate)
            if value is not None:
                reward_values.append(value)

        average_reward = (sum(reward_values) / len(reward_values)) if reward_values and len(reward_values) > 0 else None

        return {
            "total_tasks": total_tasks,
            "success_tasks": success_tasks,
            "failed_tasks": failed_tasks,
            "average_score": average_score,
            "average_execution_time": average_execution_time,
            "average_reward": average_reward,
        }

    async def _get_round_row(self, validator_round_id: str) -> Optional[ValidatorRoundORM]:
        # Load with eager loading for validator_snapshot (1:1 relationship)
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        return await self.session.scalar(stmt)

    async def _is_main_validator_identity(self, validator_uid: int, validator_hotkey: Optional[str]) -> bool:
        cfg_uid, cfg_hotkey = await self._get_main_validator_cfg()
        payload_hotkey = (validator_hotkey or "").strip()
        if cfg_uid is None and not cfg_hotkey:
            return True
        if cfg_uid is not None and int(validator_uid) == int(cfg_uid):
            return True
        if cfg_hotkey and payload_hotkey and payload_hotkey == cfg_hotkey:
            return True
        return False

    async def _assert_start_round_authority_and_state(
        self,
        validator_round: ValidatorRound,
        validator_stake: Optional[float],
        validator_version: Optional[str] = None,
        validator_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        is_main = await self._is_main_validator_identity(
            validator_round.validator_uid,
            validator_round.validator_hotkey,
        )
        if is_main:
            # Reconcile stale previous seasons before validating conflicts.
            await self._close_stale_active_seasons_for_incoming(int(validator_round.season_number))
            active_season = (
                await self.session.execute(
                    text(
                        """
                        SELECT s.season_number
                        FROM seasons s
                        WHERE LOWER(COALESCE(s.status, '')) = 'active'
                          AND s.season_number <> :season_number
                        LIMIT 1
                        """
                    ),
                    {"season_number": int(validator_round.season_number)},
                )
            ).first()
            if active_season:
                raise RoundConflictError(f"Cannot start season {validator_round.season_number}: season {int(active_season[0])} is still active")
            active_round = (
                await self.session.execute(
                    text(
                        """
                        SELECT r.round_number_in_season
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE s.season_number = :season_number
                          AND LOWER(COALESCE(r.status, '')) = 'active'
                          AND r.round_number_in_season <> :round_number_in_season
                        LIMIT 1
                        """
                    ),
                    {
                        "season_number": int(validator_round.season_number),
                        "round_number_in_season": int(validator_round.round_number_in_season),
                    },
                )
            ).first()
            if active_round:
                raise RoundConflictError(f"Cannot start a new round while another round is active (season={validator_round.season_number}, active_round={int(active_round[0])})")
            return
        existing_round = (
            await self.session.execute(
                text(
                    """
                    SELECT r.round_id, LOWER(COALESCE(r.status, '')) AS round_status
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    WHERE s.season_number = :season_number
                      AND r.round_number_in_season = :round_number_in_season
                    LIMIT 1
                    """
                ),
                {
                    "season_number": int(validator_round.season_number),
                    "round_number_in_season": int(validator_round.round_number_in_season),
                },
            )
        ).first()
        if not existing_round:
            from app.services.chain_state import get_current_block

            current_block = get_current_block()
            if current_block is None:
                raise RoundConflictError("Chain state unavailable: non-main validator cannot trigger fallback start")
            planned_start_block = int(getattr(validator_round, "start_block", 0) or 0)
            grace_blocks = int(getattr(settings, "MAIN_VALIDATOR_START_GRACE_BLOCKS", 25))
            if current_block <= (planned_start_block + grace_blocks):
                raise RoundConflictError(
                    "Only main validator can open a new season/round before fallback grace elapses "
                    f"(current_block={current_block}, planned_start_block={planned_start_block}, grace_blocks={grace_blocks})"
                )
            has_other_active = (
                await self.session.execute(
                    text(
                        """
                        SELECT 1
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE s.season_number = :season_number
                          AND LOWER(COALESCE(r.status, '')) = 'active'
                        LIMIT 1
                        """
                    ),
                    {"season_number": int(validator_round.season_number)},
                )
            ).first()
            if has_other_active:
                raise RoundConflictError(f"Cannot fallback-start season={validator_round.season_number}, round={validator_round.round_number_in_season}: another round is already active")
            is_top_backup = await self._is_highest_stake_backup(
                int(validator_round.validator_uid),
                validator_stake,
            )
            if not is_top_backup:
                raise RoundConflictError("Fallback start denied: validator is not the highest-stake backup")
            return
        round_status = (existing_round[1] or "").strip()
        if round_status != "active":
            raise RoundConflictError(
                f"Cannot attach validator run to a non-active round (season={validator_round.season_number}, round={validator_round.round_number_in_season}, status={round_status or 'unknown'})"
            )
        await self._assert_runtime_alignment_with_main(
            season_number=int(validator_round.season_number),
            round_number_in_season=int(validator_round.round_number_in_season),
            validator_uid=int(validator_round.validator_uid),
            validator_hotkey=(validator_round.validator_hotkey or "").strip() or None,
            validator_version=validator_version,
            validator_config=validator_config,
        )

    @staticmethod
    def _normalize_runtime_config_for_compare(config: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(config, dict) or not config:
            return None
        # Remove obvious identity/noise keys that can differ per validator process.
        ignored_keys = {
            "validator_uid",
            "validator_hotkey",
            "validator_coldkey",
            "wallet",
            "hotkey",
            "coldkey",
        }
        cleaned = {k: v for k, v in config.items() if k not in ignored_keys}
        if not cleaned:
            return None
        return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _apply_runtime_config_defaults(self, config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(config, dict):
            return config
        normalized = dict(config)
        timing = normalized.get("timing")
        if isinstance(timing, dict):
            timing_normalized = dict(timing)
        else:
            timing_normalized = {}
        # Keep backend and validator defaults aligned for late-start skip window.
        timing_normalized.setdefault(
            "skip_round_if_started_after_fraction",
            float(getattr(settings, "VALIDATOR_SKIP_ROUND_STARTED_AFTER_FRACTION_DEFAULT", 0.6)),
        )
        normalized["timing"] = timing_normalized
        return normalized

    def _extract_runtime_guardrails(self, config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(config, dict):
            return {}
        round_cfg = config.get("round") if isinstance(config.get("round"), dict) else {}
        return {
            "season_number": self._coerce_int(round_cfg.get("season_number", config.get("season_number"))),
            "round_number_in_season": self._coerce_int(round_cfg.get("round_number_in_season", round_cfg.get("round_number", config.get("round_number_in_season")))),
            "start_block": self._coerce_int(round_cfg.get("start_block", config.get("start_block"))),
            "end_block": self._coerce_int(round_cfg.get("end_block", config.get("end_block"))),
            "start_epoch": self._coerce_int(round_cfg.get("start_epoch", config.get("start_epoch"))),
            "end_epoch": self._coerce_int(round_cfg.get("end_epoch", config.get("end_epoch"))),
        }

    def _extract_skip_round_fraction(self, config: Optional[Dict[str, Any]]) -> Optional[float]:
        default_fraction = float(getattr(settings, "VALIDATOR_SKIP_ROUND_STARTED_AFTER_FRACTION_DEFAULT", 0.6))
        if not isinstance(config, dict):
            return default_fraction
        timing_cfg = config.get("timing") if isinstance(config.get("timing"), dict) else {}
        value = timing_cfg.get("skip_round_if_started_after_fraction")
        parsed = self._coerce_float(value)
        return parsed if parsed is not None else default_fraction

    async def _assert_runtime_alignment_with_main(
        self,
        *,
        season_number: int,
        round_number_in_season: int,
        validator_uid: int,
        validator_hotkey: Optional[str],
        validator_version: Optional[str],
        validator_config: Optional[Dict[str, Any]],
    ) -> None:
        # Only applies to non-main validators once the canonical round is active.
        is_main = await self._is_main_validator_identity(validator_uid, validator_hotkey)
        if is_main:
            return

        row = (
            await self.session.execute(
                text(
                    """
                    SELECT
                        rv.validator_uid,
                        rv.validator_hotkey,
                        rv.version,
                        rv.config
                    FROM round_validators rv
                    WHERE rv.season_number = :season_number
                      AND rv.round_number_in_season = :round_number_in_season
                      AND COALESCE(rv.is_main_validator, FALSE) = TRUE
                    ORDER BY rv.updated_at DESC
                    LIMIT 1
                    """
                ),
                {
                    "season_number": int(season_number),
                    "round_number_in_season": int(round_number_in_season),
                },
            )
        ).first()
        if not row:
            return

        main_uid = int(row[0]) if row[0] is not None else None
        main_hotkey = (row[1] or "").strip() or None
        main_version = (row[2] or "").strip() or None
        main_config = self._apply_runtime_config_defaults(row[3] if isinstance(row[3], dict) else None)

        incoming_version = (validator_version or "").strip() or None
        if main_version and incoming_version and main_version != incoming_version:
            raise RoundConflictError(
                f"Validator runtime mismatch with main validator: version mismatch (incoming={incoming_version}, main={main_version}, main_uid={main_uid}, main_hotkey={main_hotkey})"
            )

        incoming_config = self._apply_runtime_config_defaults(validator_config)
        main_guardrails = self._extract_runtime_guardrails(main_config)
        incoming_guardrails = self._extract_runtime_guardrails(incoming_config)
        for key in ("season_number", "round_number_in_season", "start_block", "end_block", "start_epoch", "end_epoch"):
            main_val = main_guardrails.get(key)
            incoming_val = incoming_guardrails.get(key)
            if main_val is None or incoming_val is None:
                continue
            if main_val != incoming_val:
                raise RoundConflictError(
                    "Validator runtime mismatch with main validator: "
                    f"critical field mismatch ({key}, incoming={incoming_val}, main={main_val}, "
                    f"season={season_number}, round={round_number_in_season}, main_uid={main_uid}, main_hotkey={main_hotkey})"
                )

        main_skip_fraction = self._extract_skip_round_fraction(main_config)
        incoming_skip_fraction = self._extract_skip_round_fraction(incoming_config)
        if main_skip_fraction is not None and incoming_skip_fraction is not None and abs(main_skip_fraction - incoming_skip_fraction) > 1e-9:
            logger.warning(
                "Validator runtime drift (non-blocking): skip_round_if_started_after_fraction incoming=%s main=%s (season=%s round=%s validator_uid=%s main_uid=%s)",
                incoming_skip_fraction,
                main_skip_fraction,
                season_number,
                round_number_in_season,
                validator_uid,
                main_uid,
            )

        normalized_main_cfg = self._normalize_runtime_config_for_compare(main_config)
        normalized_incoming_cfg = self._normalize_runtime_config_for_compare(incoming_config)
        if normalized_main_cfg and normalized_incoming_cfg and normalized_main_cfg != normalized_incoming_cfg:
            logger.warning(
                "Validator runtime drift (non-blocking): non-critical config mismatch (season=%s round=%s validator_uid=%s main_uid=%s main_hotkey=%s)",
                season_number,
                round_number_in_season,
                validator_uid,
                main_uid,
                main_hotkey,
            )

    async def _assert_finish_round_authority_and_state(
        self,
        round_row: ValidatorRoundORM,
        validator_uid: int,
        validator_hotkey: Optional[str],
        validator_stake: Optional[float],
    ) -> None:
        is_main = await self._is_main_validator_identity(validator_uid, validator_hotkey)
        if is_main:
            return
        from app.services.chain_state import get_current_block

        current_block = get_current_block()
        if current_block is None:
            raise RoundConflictError("Chain state unavailable: non-main validator cannot trigger fallback finish")
        planned_end_block = 0
        planned_end_result = await self.session.execute(
            text(
                """
                SELECT COALESCE(r.planned_end_block, r.end_block, :fallback_end_block, :fallback_start_block) AS planned_end_block
                FROM rounds r
                JOIN round_validators rv ON rv.round_id = r.round_id
                WHERE rv.validator_round_id = :validator_round_id
                LIMIT 1
                """
            ),
            {
                "validator_round_id": str(round_row.validator_round_id),
                "fallback_end_block": int(getattr(round_row, "end_block", 0) or 0),
                "fallback_start_block": int(getattr(round_row, "start_block", 0) or 0),
            },
        )
        row = planned_end_result.first()
        if row and row[0] is not None:
            planned_end_block = int(row[0])
        if planned_end_block <= 0:
            planned_end_block = int(getattr(round_row, "end_block", 0) or getattr(round_row, "start_block", 0) or 0)
        grace_blocks = int(getattr(settings, "MAIN_VALIDATOR_FINISH_GRACE_BLOCKS", 25))
        if current_block <= (planned_end_block + grace_blocks):
            raise RoundConflictError(
                f"Fallback finish denied: main validator still within finish grace (current_block={current_block}, planned_end_block={planned_end_block}, grace_blocks={grace_blocks})"
            )
        is_top_backup = await self._is_highest_stake_backup(int(validator_uid), validator_stake)
        if not is_top_backup:
            raise RoundConflictError("Fallback finish denied: validator is not the highest-stake backup")

    async def _purge_round_for_validator_season_and_round(self, validator_uid: int, season_number: int, round_number_in_season: int) -> None:
        """Delete any existing round for this validator, season and round_in_season (and cascade children)."""
        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.season_number == season_number,
                ValidatorRoundORM.round_number_in_season == round_number_in_season,
            )
        )
        rows = list(await self.session.scalars(stmt))
        if not rows:
            return
        for row in rows:
            await self.session.delete(row)
        await self.session.flush()

    async def _get_agent_run_row(self, agent_run_id: str) -> Optional[AgentEvaluationRunORM]:
        stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == agent_run_id)
        return await self.session.scalar(stmt)

    async def _resolve_reused_source_run(self, source_run_id: Optional[str]) -> Optional[AgentEvaluationRunORM]:
        """Follow reused_from chain and return the root source run."""
        if not source_run_id:
            return None
        visited: set[str] = set()
        current_id: Optional[str] = source_run_id
        root: Optional[AgentEvaluationRunORM] = None
        while current_id and current_id not in visited:
            visited.add(current_id)
            row = await self._get_agent_run_row(current_id)
            if row is None:
                break
            root = row
            current_id = getattr(row, "reused_from_agent_run_id", None)
        return root

    async def _propagate_source_metrics_to_reused_runs(self, source_run: AgentEvaluationRunORM) -> None:
        """When a source run gets its metrics (e.g. after evaluations), copy them to any runs that reuse from it.
        This fixes runs created before the source had data (e.g. round N+1 reused run created before round N evaluations arrived).
        """
        total = getattr(source_run, "total_tasks", None) or 0
        if total <= 0:
            return
        stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.reused_from_agent_run_id == source_run.agent_run_id)
        result = await self.session.scalars(stmt)
        updated = False
        for row in result:
            if (getattr(row, "total_tasks", None) or 0) <= 0:
                row.total_tasks = int(total)
                row.success_tasks = int(getattr(source_run, "success_tasks", 0) or 0)
                row.failed_tasks = int(getattr(source_run, "failed_tasks", 0) or 0)
                if getattr(source_run, "average_score", None) is not None:
                    row.average_score = source_run.average_score
                if getattr(source_run, "average_execution_time", None) is not None:
                    row.average_execution_time = source_run.average_execution_time
                if getattr(source_run, "average_reward", None) is not None:
                    row.average_reward = source_run.average_reward
                if getattr(source_run, "zero_reason", None):
                    row.zero_reason = source_run.zero_reason
                updated = True
        if updated:
            await self.session.flush()

    async def _get_task_row(self, task_id: str) -> Optional[TaskORM]:
        stmt = select(TaskORM).where(TaskORM.task_id == task_id)
        return await self.session.scalar(stmt)

    async def _ensure_round_exists(self, validator_round_id: str) -> ValidatorRoundORM:
        # Load with eager loading for validator_snapshot (1:1 relationship)
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        round_row = await self.session.scalar(stmt)
        if not round_row:
            raise ValueError(f"Validator round {validator_round_id} not found")
        return round_row

    async def _ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: Optional[int],
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """DEPRECATED: Use _ensure_unique_season_round instead."""
        if round_number is None:
            return

        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.round_number == round_number,
            )
        )
        if exclude_round_id is not None:
            stmt = stmt.where(ValidatorRoundORM.validator_round_id != exclude_round_id)
        existing = await self.session.scalar(stmt)
        if existing:
            raise RoundConflictError(f"Validator {validator_uid} already has a round with number {round_number}")

    async def _ensure_unique_season_round(
        self,
        validator_uid: int,
        season_number: int,
        round_number_in_season: int,
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """Ensure no existing round for this validator with same season and round_in_season."""
        stmt = (
            select(ValidatorRoundORM)
            .join(
                ValidatorRoundValidatorORM,
                ValidatorRoundORM.validator_round_id == ValidatorRoundValidatorORM.validator_round_id,
            )
            .where(
                ValidatorRoundValidatorORM.validator_uid == validator_uid,
                ValidatorRoundORM.season_number == season_number,
                ValidatorRoundORM.round_number_in_season == round_number_in_season,
            )
        )
        if exclude_round_id is not None:
            stmt = stmt.where(ValidatorRoundORM.validator_round_id != exclude_round_id)
        existing = await self.session.scalar(stmt)
        if existing:
            raise RoundConflictError(f"Validator {validator_uid} already has a round for season {season_number}, round {round_number_in_season}")

    async def _upsert_validator_snapshot(
        self,
        round_row: ValidatorRoundORM,
        snapshot: ValidatorRoundValidator,
    ) -> ValidatorRoundValidatorORM:
        # 1:1 relationship - only one snapshot per round
        stmt = select(ValidatorRoundValidatorORM).where(
            ValidatorRoundValidatorORM.validator_round_id == round_row.validator_round_id,
        )
        existing = await self.session.scalar(stmt)
        kwargs = {
            "validator_round_id": round_row.validator_round_id,
            "validator_uid": snapshot.validator_uid,
            "validator_hotkey": snapshot.validator_hotkey,
            "validator_coldkey": snapshot.validator_coldkey,
            "name": snapshot.name,
            "stake": snapshot.stake,
            "vtrust": snapshot.vtrust,
            "image_url": snapshot.image_url,
            "version": snapshot.version,
            "config": self._apply_runtime_config_defaults(snapshot.config),  # Include validator configuration
        }
        if existing:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing
        row = ValidatorRoundValidatorORM(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    async def _upsert_miner_snapshot(
        self,
        round_row: ValidatorRoundORM,
        snapshot: ValidatorRoundMiner,
    ) -> ValidatorRoundMinerORM:
        # Shadow-mode start can create round_validators with round_id=NULL until canonical
        # round linking completes. Ensure link exists before writing miner snapshots because
        # compat layer inserts into round_validator_miners where round_id is NOT NULL.
        rv_round_id = await self.session.scalar(
            text(
                """
                SELECT round_id
                FROM round_validators
                WHERE validator_round_id = :validator_round_id
                LIMIT 1
                """
            ),
            {"validator_round_id": round_row.validator_round_id},
        )
        if rv_round_id is None:
            await self.session.execute(
                text(
                    """
                    WITH canonical AS (
                        SELECT r.round_id
                        FROM rounds r
                        JOIN seasons s ON s.season_id = r.season_id
                        WHERE s.season_number = :season_number
                          AND r.round_number_in_season = :round_number_in_season
                        LIMIT 1
                    )
                    UPDATE round_validators rv
                    SET
                        round_id = c.round_id,
                        pending_round_link = FALSE,
                        updated_at = NOW()
                    FROM canonical c
                    WHERE rv.validator_round_id = :validator_round_id
                      AND rv.round_id IS NULL
                    """
                ),
                {
                    "validator_round_id": round_row.validator_round_id,
                    "season_number": int(round_row.season_number),
                    "round_number_in_season": int(round_row.round_number_in_season),
                },
            )
            rv_round_id = await self.session.scalar(
                text(
                    """
                    SELECT round_id
                    FROM round_validators
                    WHERE validator_round_id = :validator_round_id
                    LIMIT 1
                    """
                ),
                {"validator_round_id": round_row.validator_round_id},
            )
        if rv_round_id is None:
            # Non-main validators may arrive slightly before the canonical round row is
            # created/linked. Do not block run persistence; keep miner snapshot writes
            # and backfill round_id later when canonical linking completes.
            logger.warning(
                "Canonical round not linked yet for validator_round_id=%s; persisting miner snapshot with deferred round_id link.",
                round_row.validator_round_id,
            )

        stmt = select(ValidatorRoundMinerORM).where(
            ValidatorRoundMinerORM.validator_round_id == round_row.validator_round_id,
            ValidatorRoundMinerORM.miner_uid == snapshot.miner_uid,
            ValidatorRoundMinerORM.miner_hotkey == snapshot.miner_hotkey,
        )
        existing = await self.session.scalar(stmt)
        kwargs = {
            "validator_round_id": round_row.validator_round_id,
            "miner_uid": snapshot.miner_uid,
            "miner_hotkey": snapshot.miner_hotkey,
            "miner_coldkey": snapshot.miner_coldkey,
            "name": snapshot.agent_name,
            "image_url": snapshot.image_url,
            "github_url": snapshot.github_url,
            "is_sota": snapshot.is_sota,
            "version": snapshot.version if hasattr(snapshot, "version") else None,
        }
        if existing:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing
        row = ValidatorRoundMinerORM(**kwargs)
        self.session.add(row)
        await self.session.flush()
        return row

    def _validator_round_kwargs(self, model: ValidatorRound) -> Dict[str, Any]:
        # validator_uid, validator_hotkey, validator_coldkey moved to ValidatorRoundValidatorORM
        # metadata/round summary is stored in validator_summary at finish_round, not at start
        start_block = int(model.start_block or 0)
        end_block = int(model.end_block) if getattr(model, "end_block", None) not in (None, 0) else None
        if end_block is None and start_block > 0:
            try:
                from app.config import settings

                round_blocks = int(settings.ROUND_SIZE_EPOCHS * settings.BLOCKS_PER_EPOCH)
                if round_blocks > 0:
                    end_block = start_block + round_blocks
            except Exception:
                end_block = None

        start_epoch = int(model.start_epoch or 0)
        end_epoch = int(model.end_epoch) if getattr(model, "end_epoch", None) not in (None, 0) else None
        if end_epoch is None and end_block is not None:
            try:
                from app.services.round_calc import block_to_epoch

                end_epoch = int(block_to_epoch(int(end_block)))
            except Exception:
                end_epoch = None

        return {
            "validator_round_id": model.validator_round_id,
            "season_number": model.season_number,
            "round_number_in_season": model.round_number_in_season,
            "start_block": start_block,
            "end_block": end_block,
            "start_epoch": start_epoch,
            "end_epoch": end_epoch,
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "n_tasks": model.n_tasks,
            "status": model.status,
        }

    def _agent_run_kwargs(
        self,
        model: AgentEvaluationRun,
    ) -> Dict[str, Any]:
        return {
            "agent_run_id": model.agent_run_id,
            "validator_round_id": model.validator_round_id,
            # validator_uid and validator_hotkey removed - obtain via validator_round.validator_snapshot
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            # is_sota and version removed - obtain via validator_round.miner_snapshots
            "started_at": model.started_at,
            "ended_at": model.ended_at,
            "elapsed_sec": model.elapsed_sec,
            "average_score": model.average_score,
            "average_execution_time": model.average_execution_time,
            "average_reward": model.average_reward,
            # total_reward removed - no longer stored in agent_evaluation_runs
            "total_tasks": model.total_tasks,
            "success_tasks": getattr(model, "success_tasks", getattr(model, "completed_tasks", 0)),
            "failed_tasks": model.failed_tasks,
            # rank and weight removed - obtain via validator_round_summary_miners
            "meta": _agent_run_meta_for_storage(model),
            "is_reused": getattr(model, "is_reused", False),
            "reused_from_agent_run_id": getattr(model, "reused_from_agent_run_id", None),
            "zero_reason": getattr(model, "zero_reason", None),
        }

    def _task_kwargs(self, model: Task) -> Dict[str, Any]:
        return {
            "task_id": model.task_id,
            "validator_round_id": model.validator_round_id,
            "web_project_id": model.web_project_id,
            "web_version": model.web_version,
            "url": model.url,
            "prompt": model.prompt,
            "specifications": _non_empty_dict(model.specifications),
            "tests": [test.model_dump(mode="json", exclude_none=True) for test in model.tests],
            "use_case": (model.use_case if isinstance(model.use_case, dict) else _optional_dump(model.use_case)),
        }

    def _task_solution_kwargs(
        self,
        model: TaskSolution,
    ) -> Dict[str, Any]:
        return {
            "solution_id": model.solution_id,
            "task_id": model.task_id,
            "agent_run_id": model.agent_run_id,
            "validator_round_id": model.validator_round_id,
            "validator_uid": model.validator_uid,
            "validator_hotkey": model.validator_hotkey,
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            "actions": _action_dump(model.actions),
        }

    def _evaluation_kwargs(
        self,
        model: Evaluation,
    ) -> Dict[str, Any]:
        # Normalize evaluation_score and reward (accept eval_score from legacy payloads)
        eval_score_val = getattr(model, "evaluation_score", None) or getattr(model, "eval_score", None)
        if eval_score_val is None:
            eval_score_val = 0.0
        try:
            eval_score_val = float(eval_score_val)
        except Exception:
            eval_score_val = 0.0

        reward_val = getattr(model, "reward", 0.0)
        try:
            reward_val = float(reward_val)
        except Exception:
            reward_val = 0.0

        # Preserve reward provided by validator (it already includes score/time/cost shaping).
        # Only fall back to EVAL_SCORE_WEIGHT when reward is missing/invalid for solved tasks.
        # If evaluation_score == 0, reward must be 0.
        if eval_score_val > 0.0:
            eval_score_weight = float(settings.EVAL_SCORE_WEIGHT)
            if reward_val <= 0.0:
                reward_val = eval_score_weight
        else:
            reward_val = 0.0

        zero_reason = getattr(model, "zero_reason", None)
        if zero_reason is None and eval_score_val <= 0.0:
            meta = getattr(model, "metadata", None) or {}
            if meta.get("timeout") is True:
                zero_reason = "task_timeout"

        return {
            "evaluation_id": model.evaluation_id,
            "validator_round_id": model.validator_round_id,
            "task_id": model.task_id,
            "task_solution_id": model.task_solution_id,
            "agent_run_id": model.agent_run_id,
            "validator_uid": model.validator_uid,
            "validator_hotkey": model.validator_hotkey,
            "miner_uid": model.miner_uid,
            "miner_hotkey": model.miner_hotkey,
            "evaluation_score": eval_score_val,
            "reward": reward_val,
            "evaluation_time": model.evaluation_time,
            "execution_history": list(model.execution_history),
            "gif_recording": model.gif_recording,
            "extra_info": _clean_meta_dict(model.metadata),
            "zero_reason": zero_reason,
        }

    def _llm_usage_from_evaluation(self, evaluation: Evaluation) -> Any:
        """Return llm_usage list from evaluation (single source of truth)."""
        usage = getattr(evaluation, "llm_usage", None)
        if usage and isinstance(usage, list) and len(usage) > 0:
            return usage
        return None

    def _normalize_llm_usage(self, usage: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not usage:
            return rows
        for item in usage:
            if hasattr(item, "model_dump"):
                raw = item.model_dump()
            elif isinstance(item, dict):
                raw = item
            else:
                continue
            provider = raw.get("provider")
            model = raw.get("model")
            tokens = raw.get("tokens")
            cost = raw.get("cost")
            if provider is None and model is None and tokens is None and cost is None:
                continue
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "tokens": tokens,
                    "cost": cost,
                }
            )
        return rows

    async def _sync_llm_usage(
        self,
        evaluation_row: EvaluationORM,
        usage: Any,
    ) -> None:
        usage_rows = self._normalize_llm_usage(usage)
        if not usage_rows:
            return
        await self.session.execute(delete(EvaluationLLMUsageORM).where(EvaluationLLMUsageORM.evaluation_id == evaluation_row.evaluation_id))
        for row in usage_rows:
            self.session.add(
                EvaluationLLMUsageORM(
                    evaluation_id=evaluation_row.evaluation_id,
                    provider=row.get("provider"),
                    model=row.get("model"),
                    tokens=row.get("tokens"),
                    cost=row.get("cost"),
                )
            )

    @staticmethod
    def _assert_unique(sequence: Iterable[str], name: str) -> None:
        seen: set[str] = set()
        for value in sequence:
            if value in seen:
                raise DuplicateIdentifierError(f"Duplicate {name}: {value}")
            seen.add(value)

    def _assert_unique_payload(self, payload: ValidatorRoundSubmissionRequest) -> None:
        self._assert_unique(
            [payload.validator_round.validator_round_id],
            "validator_round_id",
        )
        self._assert_unique(
            [run.agent_run_id for run in payload.agent_evaluation_runs],
            "agent_run_id",
        )
        self._assert_unique(
            [task.task_id for task in payload.tasks],
            "task_id",
        )
        self._assert_unique(
            [solution.solution_id for solution in payload.task_solutions],
            "task_solution_id",
        )
        self._assert_unique(
            [evaluation.evaluation_id for evaluation in payload.evaluations],
            "evaluation_id",
        )
