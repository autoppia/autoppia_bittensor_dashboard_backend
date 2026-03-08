from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import defer, selectinload

from app.db.models import (
    AgentEvaluationRunORM,
    EvaluationORM,
    TaskSolutionORM,
    ValidatorRoundORM,
    ValidatorRoundValidatorORM,
)
from app.models.core import AgentEvaluationRun, ValidatorRound, ValidatorRoundMiner, ValidatorRoundSubmissionRequest, ValidatorRoundValidator
from app.services.validator.validator_storage_common import DuplicateIdentifierError, PersistenceResult, RoundConflictError

logger = logging.getLogger(__name__)


class ValidatorStorageRoundsMixin:
    async def upsert_shadow_round_start(
        self,
        *,
        validator_round: ValidatorRound,
        validator_snapshot: ValidatorRoundValidator,
    ) -> None:
        """
        Persist a non-authoritative validator round "shadow" record.

        This path is used when a non-main validator is blocked by main-validator
        authority/grace to open the canonical round. We still store validator-local
        round state so it can later be linked to the canonical round row.
        """
        started_at_sql = "to_timestamp(:started_at)" if validator_round.started_at else "NULL"
        normalized_config = self._apply_runtime_config_defaults(validator_snapshot.config)
        await self.session.execute(
            text(
                f"""
                INSERT INTO round_validators (
                    round_id,
                    season_number,
                    round_number_in_season,
                    start_block,
                    end_block,
                    start_epoch,
                    end_epoch,
                    pending_round_link,
                    is_main_validator,
                    validator_uid,
                    validator_hotkey,
                    validator_coldkey,
                    validator_round_id,
                    name,
                    image_url,
                    version,
                    stake,
                    vtrust,
                    config,
                    started_at,
                    updated_at
                )
                VALUES (
                    NULL,
                    :season_number,
                    :round_number_in_season,
                    :start_block,
                    :end_block,
                    :start_epoch,
                    :end_epoch,
                    TRUE,
                    FALSE,
                    :validator_uid,
                    :validator_hotkey,
                    :validator_coldkey,
                    :validator_round_id,
                    :name,
                    :image_url,
                    :version,
                    :stake,
                    :vtrust,
                    CAST(:config AS JSONB),
                    {started_at_sql},
                    NOW()
                )
                ON CONFLICT (validator_round_id) DO UPDATE SET
                    season_number = COALESCE(EXCLUDED.season_number, round_validators.season_number),
                    round_number_in_season = COALESCE(EXCLUDED.round_number_in_season, round_validators.round_number_in_season),
                    start_block = COALESCE(EXCLUDED.start_block, round_validators.start_block),
                    end_block = COALESCE(EXCLUDED.end_block, round_validators.end_block),
                    start_epoch = COALESCE(EXCLUDED.start_epoch, round_validators.start_epoch),
                    end_epoch = COALESCE(EXCLUDED.end_epoch, round_validators.end_epoch),
                    pending_round_link = CASE
                        WHEN round_validators.round_id IS NULL THEN TRUE
                        ELSE round_validators.pending_round_link
                    END,
                    validator_uid = COALESCE(EXCLUDED.validator_uid, round_validators.validator_uid),
                    validator_hotkey = COALESCE(EXCLUDED.validator_hotkey, round_validators.validator_hotkey),
                    validator_coldkey = COALESCE(EXCLUDED.validator_coldkey, round_validators.validator_coldkey),
                    name = COALESCE(EXCLUDED.name, round_validators.name),
                    image_url = COALESCE(EXCLUDED.image_url, round_validators.image_url),
                    version = COALESCE(EXCLUDED.version, round_validators.version),
                    stake = COALESCE(EXCLUDED.stake, round_validators.stake),
                    vtrust = COALESCE(EXCLUDED.vtrust, round_validators.vtrust),
                    config = COALESCE(EXCLUDED.config, round_validators.config),
                    started_at = COALESCE(EXCLUDED.started_at, round_validators.started_at),
                    updated_at = NOW()
                """
            ),
            {
                "season_number": int(validator_round.season_number),
                "round_number_in_season": int(validator_round.round_number_in_season),
                "start_block": int(getattr(validator_round, "start_block", 0) or 0),
                "end_block": int(getattr(validator_round, "end_block", 0) or 0) if getattr(validator_round, "end_block", None) is not None else None,
                "start_epoch": int(getattr(validator_round, "start_epoch", 0) or 0),
                "end_epoch": int(getattr(validator_round, "end_epoch", 0) or 0) if getattr(validator_round, "end_epoch", None) is not None else None,
                "validator_uid": int(validator_snapshot.validator_uid),
                "validator_hotkey": validator_snapshot.validator_hotkey,
                "validator_coldkey": validator_snapshot.validator_coldkey,
                "validator_round_id": validator_round.validator_round_id,
                "name": validator_snapshot.name,
                "image_url": validator_snapshot.image_url,
                "version": validator_snapshot.version,
                "stake": validator_snapshot.stake,
                "vtrust": validator_snapshot.vtrust,
                "config": json.dumps(normalized_config) if normalized_config is not None else None,
                "started_at": float(validator_round.started_at) if validator_round.started_at is not None else None,
            },
        )

    async def _link_pending_shadow_round_validators(
        self,
        *,
        season_number: int,
        round_number_in_season: int = 0,  # kept for call-site compatibility, not used in queries
    ) -> None:
        """
        Link shadow round_validators rows (round_id IS NULL) to canonical round_id
        once the canonical round exists.

        Processes ALL pending shadow rows for the season (not just the current round)
        so that rounds where the main validator was temporarily down get retroactively
        linked when the next round starts.
        """
        # Link shadow rows for the current round (primary case).
        await self.session.execute(
            text(
                """
                UPDATE round_validators rv
                SET
                    round_id = r.round_id,
                    pending_round_link = FALSE,
                    start_block = COALESCE(rv.start_block, r.start_block),
                    end_block = COALESCE(rv.end_block, r.end_block),
                    start_epoch = COALESCE(rv.start_epoch, r.start_epoch),
                    end_epoch = COALESCE(rv.end_epoch, r.end_epoch),
                    updated_at = NOW()
                FROM rounds r
                JOIN seasons s ON s.season_id = r.season_id
                WHERE rv.round_id IS NULL
                  AND COALESCE(rv.pending_round_link, FALSE) = TRUE
                  AND rv.season_number = :season_number
                  AND s.season_number = :season_number
                  AND r.round_number_in_season = rv.round_number_in_season
                """
            ),
            {"season_number": int(season_number)},
        )
        # Backfill per-miner rows that were persisted before canonical linking,
        # covering all rounds in the season that were just linked or previously missed.
        await self.session.execute(
            text(
                """
                UPDATE round_validator_miners rvm
                SET
                    round_id = rv.round_id,
                    updated_at = NOW()
                FROM round_validators rv
                WHERE rv.round_validator_id = rvm.round_validator_id
                  AND rv.round_id IS NOT NULL
                  AND rvm.round_id IS NULL
                  AND rv.season_number = :season_number
                """
            ),
            {"season_number": int(season_number)},
        )

    async def start_round(
        self,
        *,
        validator_round: ValidatorRound,
        validator_snapshot: ValidatorRoundValidator,
    ) -> ValidatorRoundORM:
        """Create a new validator round and store the initial snapshot."""
        validator_snapshot.config = self._apply_runtime_config_defaults(validator_snapshot.config)
        await self._assert_start_round_authority_and_state(
            validator_round,
            validator_snapshot.stake,
            validator_snapshot.version,
            validator_snapshot.config,
        )
        # Check for existing round with same season and round_in_season for this validator
        await self._purge_round_for_validator_season_and_round(validator_round.validator_uid, validator_round.season_number, validator_round.round_number_in_season)
        await self._ensure_unique_season_round(
            validator_round.validator_uid,
            validator_round.season_number,
            validator_round.round_number_in_season,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        if existing_round is not None:
            raise DuplicateIdentifierError(f"validator_round_id {validator_round.validator_round_id} is already registered")

        round_kwargs = await self._validator_round_kwargs(validator_round)

        round_row = ValidatorRoundORM(**round_kwargs)
        self.session.add(round_row)
        await self.session.flush()
        # Defensive normalization: compatibility view/trigger path can end with null epochs in some
        # start_round flows. Ensure start/end epoch are always populated from round boundaries.
        try:
            from app.services.round_calc import block_to_epoch

            if getattr(round_row, "start_epoch", None) in (None, 0):
                round_row.start_epoch = int(block_to_epoch(round_row.start_block))
            if getattr(round_row, "end_epoch", None) is None:
                end_block = round_row.end_block or round_row.start_block
                round_row.end_epoch = int(block_to_epoch(end_block))
            await self.session.flush()
        except Exception:
            # Keep start resilient; finish_round also has epoch backfill.
            pass

        await self._upsert_validator_snapshot(round_row, validator_snapshot)
        await self._link_pending_shadow_round_validators(
            season_number=int(validator_round.season_number),
            round_number_in_season=int(validator_round.round_number_in_season),
        )

        return round_row

    async def start_agent_run(
        self,
        *,
        validator_round_id: str,
        agent_run: AgentEvaluationRun,
        miner_snapshot: ValidatorRoundMiner,
    ) -> Optional[AgentEvaluationRunORM]:
        """Persist the beginning of an agent evaluation run."""
        round_row = await self._ensure_round_exists(validator_round_id)

        # Check if agent_run_id already exists (idempotency by ID)
        existing_run = await self._get_agent_run_row(agent_run.agent_run_id)
        if existing_run:
            if existing_run.validator_round_id == validator_round_id:
                # Same agent_run_id for same round - idempotent, return existing
                return existing_run
            else:
                raise DuplicateIdentifierError(f"agent_run_id {agent_run.agent_run_id} is already registered for a different round")

        # CRITICAL: Check if there's already an agent_run for this miner in this round
        # An agent run should be unique per (validator_round_id, miner_uid)
        if agent_run.miner_uid is not None:
            from app.db.models import AgentEvaluationRunORM

            stmt_existing = (
                select(AgentEvaluationRunORM)
                .where(
                    AgentEvaluationRunORM.validator_round_id == validator_round_id,
                    AgentEvaluationRunORM.miner_uid == agent_run.miner_uid,
                )
                .limit(1)
            )
            result_existing = await self.session.execute(stmt_existing)
            existing_for_miner = result_existing.scalar_one_or_none()

            if existing_for_miner:
                # There's already an agent_run for this miner in this round
                # Return the existing one instead of creating a duplicate
                logger.warning(
                    f"Agent run already exists for miner_uid={agent_run.miner_uid} in validator_round_id={validator_round_id}. "
                    f"Existing agent_run_id={existing_for_miner.agent_run_id}, requested agent_run_id={agent_run.agent_run_id}. "
                    f"Returning existing agent run (idempotent)."
                )
                return existing_for_miner

        await self._upsert_miner_snapshot(round_row, miner_snapshot)

        kwargs = self._agent_run_kwargs(agent_run)
        row = AgentEvaluationRunORM(**kwargs)
        self.session.add(row)
        await self.session.flush()

        return row

    async def finish_round(
        self,
        *,
        validator_round_id: str,
        status: str,
        ended_at: float,
        agent_runs: Optional[List[Dict[str, Any]]] = None,
        round_metadata: Optional[Dict[str, Any]] = None,
        validator_summary: Optional[Dict[str, Any]] = None,
        local_evaluation: Optional[Dict[str, Any]] = None,
        post_consensus_evaluation: Optional[Dict[str, Any]] = None,
        ipfs_uploaded: Optional[Dict[str, Any]] = None,
        ipfs_downloaded: Optional[Dict[str, Any]] = None,
        s3_logs_url: Optional[str] = None,
        validator_state: Optional[Dict[str, Any]] = None,
        validator_iwap_prev_round_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a validator round as completed."""
        round_row = await self._ensure_round_exists(validator_round_id)
        authoritative_finish = True
        authority_conflict_reason: Optional[str] = None
        snapshot = getattr(round_row, "validator_snapshot", None)
        if snapshot is not None:
            try:
                await self._assert_finish_round_authority_and_state(
                    round_row,
                    int(snapshot.validator_uid),
                    snapshot.validator_hotkey,
                    snapshot.stake,
                )
            except RoundConflictError as exc:
                # Important: a non-main validator may be disallowed to close the GLOBAL round
                # during main-validator grace windows, but it should still be able to persist
                # its own validator_round artifacts (logs, summaries, run states).
                authoritative_finish = False
                authority_conflict_reason = str(exc)
                logger.warning(
                    "finish_round non-authoritative for validator_round_id=%s: %s. Persisting validator-local data only.",
                    validator_round_id,
                    authority_conflict_reason,
                )

        # If round metadata was provided by the validator, persist boundary fields.
        # Only authoritative finishes are allowed to mutate canonical boundaries/config.
        # Non-authoritative finishes still persist validator-local artifacts.
        if round_metadata and isinstance(round_metadata, dict):
            # Keep summary round_number coherent with persisted round id fields.
            # Some validators can compute round_number from a later block during finish.
            try:
                persisted_round_in_season = int(getattr(round_row, "round_number_in_season", 0) or 0)
                if persisted_round_in_season > 0:
                    round_metadata["round_number"] = persisted_round_in_season
            except Exception:
                pass

            def _as_pos_int(value: Any) -> Optional[int]:
                try:
                    if value is None:
                        return None
                    parsed = int(value)
                    return parsed if parsed > 0 else None
                except Exception:
                    return None

            def _as_pos_float(value: Any) -> Optional[float]:
                try:
                    if value is None:
                        return None
                    parsed = float(value)
                    return parsed if parsed > 0 else None
                except Exception:
                    return None

            incoming_start_block = _as_pos_int(round_metadata.get("start_block"))
            incoming_end_block = _as_pos_int(round_metadata.get("end_block"))
            incoming_start_epoch = _as_pos_int(round_metadata.get("start_epoch"))
            incoming_end_epoch = _as_pos_int(round_metadata.get("end_epoch"))
            incoming_started_at = _as_pos_float(round_metadata.get("started_at"))
            incoming_ended_at = _as_pos_float(round_metadata.get("ended_at"))

            if incoming_start_block and incoming_end_block and incoming_end_block < incoming_start_block:
                logger.warning(
                    "finish_round ignored invalid round_metadata boundaries for %s (start_block=%s, end_block=%s)",
                    validator_round_id,
                    incoming_start_block,
                    incoming_end_block,
                )
                incoming_end_block = None

            if incoming_start_epoch and incoming_end_epoch and incoming_end_epoch < incoming_start_epoch:
                logger.warning(
                    "finish_round ignored invalid epoch boundaries for %s (start_epoch=%s, end_epoch=%s)",
                    validator_round_id,
                    incoming_start_epoch,
                    incoming_end_epoch,
                )
                incoming_end_epoch = None

            if incoming_started_at and incoming_ended_at and incoming_ended_at < incoming_started_at:
                logger.warning(
                    "finish_round ignored invalid time boundaries for %s (started_at=%s, ended_at=%s)",
                    validator_round_id,
                    incoming_started_at,
                    incoming_ended_at,
                )
                incoming_ended_at = None

            if authoritative_finish:
                if incoming_start_block is not None and (getattr(round_row, "start_block", None) in (None, 0)):
                    round_row.start_block = incoming_start_block
                if incoming_end_block is not None and (getattr(round_row, "end_block", None) in (None, 0)):
                    round_row.end_block = incoming_end_block
                if incoming_start_epoch is not None and (getattr(round_row, "start_epoch", None) in (None, 0)):
                    round_row.start_epoch = incoming_start_epoch
                if incoming_end_epoch is not None and (getattr(round_row, "end_epoch", None) in (None, 0)):
                    round_row.end_epoch = incoming_end_epoch
                if incoming_started_at is not None and (getattr(round_row, "started_at", None) in (None, 0)):
                    round_row.started_at = incoming_started_at
                if incoming_ended_at is not None and (getattr(round_row, "ended_at", None) in (None, 0)):
                    round_row.ended_at = incoming_ended_at

            # Main validator can persist round/season config so backend uses it instead of .env
            if authoritative_finish and snapshot is not None:
                try:
                    from app.services.round_config_service import upsert_config_season_round

                    rse = round_metadata.get("round_size_epochs")
                    sse = round_metadata.get("season_size_epochs")
                    msb = round_metadata.get("minimum_start_block")
                    bpe = round_metadata.get("blocks_per_epoch", 360)
                    if rse is not None and sse is not None and msb is not None:
                        await upsert_config_season_round(
                            self.session,
                            validator_uid=int(snapshot.validator_uid),
                            round_size_epochs=float(rse),
                            season_size_epochs=float(sse),
                            minimum_start_block=int(msb),
                            blocks_per_epoch=int(bpe) if bpe is not None else 360,
                        )
                except Exception:
                    pass
        # Ensure start/end epoch are populated even when testing overrides bypassed chain-boundary fill
        try:
            if getattr(round_row, "start_epoch", None) is None or getattr(round_row, "end_epoch", None) is None:
                # Calculate epochs from start_block
                from app.services.round_calc import block_to_epoch

                if getattr(round_row, "start_epoch", None) is None:
                    round_row.start_epoch = int(block_to_epoch(round_row.start_block))
                if getattr(round_row, "end_epoch", None) is None:
                    round_row.end_epoch = int(block_to_epoch(round_row.end_block or round_row.start_block))
        except Exception:
            # If boundary computation fails, proceed without blocking finish
            pass

        # Normalize status to match ValidatorRound literal type
        normalized_status = status.lower()
        if normalized_status in {"completed", "complete"}:
            normalized_status = "finished"
        elif normalized_status not in {
            "active",
            "finished",
            "pending",
            "evaluating_finished",
        }:
            normalized_status = "finished"

        round_row.status = normalized_status

        # validator_summary: keep round/s3/ipfs/evaluation summaries and handshake diagnostics
        from app.config import settings
        from app.services.subnet_utils import get_price

        emission_info = None
        try:
            alpha_price = get_price(netuid=settings.VALIDATOR_NETUID)
            if alpha_price <= 0:
                alpha_price = float(settings.SUBNET_PRICE_FALLBACK)
        except Exception:
            alpha_price = float(settings.SUBNET_PRICE_FALLBACK)

        if round_metadata and isinstance(round_metadata, dict):
            emission_info = round_metadata.get("emission", {})
        if not emission_info and post_consensus_evaluation:
            emission_info = (post_consensus_evaluation or {}).get("emission", {})
        if emission_info:
            emission_info = dict(emission_info)
            emission_info["alpha_price"] = float(alpha_price)

        round_with_emission = None
        if round_metadata:
            round_with_emission = dict(round_metadata)
            if emission_info:
                round_with_emission["emission"] = emission_info
        elif emission_info:
            round_with_emission = {"emission": emission_info}

        vs = validator_summary or {}
        # IMPORTANT: keep the full post-consensus object canonical and separate
        # from the local validator snapshot/IPFS payloads.
        post_summary_payload = post_consensus_evaluation if isinstance(post_consensus_evaluation, dict) else None

        merged = {
            "round": round_with_emission or vs.get("round"),
            "ipfs_uploaded": ipfs_uploaded or vs.get("ipfs_uploaded"),
            "ipfs_downloaded": ipfs_downloaded or vs.get("ipfs_downloaded"),
            "evaluation_post_consensus": post_summary_payload if post_summary_payload is not None else vs.get("evaluation_post_consensus"),
            "handshake_results": vs.get("handshake_results"),
        }
        merged["finish_authority"] = {
            "authoritative": authoritative_finish,
            "reason": authority_conflict_reason,
        }

        post_consensus_json = merged.get("evaluation_post_consensus")
        if isinstance(post_consensus_json, dict):
            # Remove noisy internal key from post-consensus summary shown in UI payloads.
            post_consensus_json.pop("schema_version", None)
            merged["evaluation_post_consensus"] = post_consensus_json

        resolved_s3_logs_url = (s3_logs_url or "").strip() or None
        if resolved_s3_logs_url is None:
            summary_s3_url = vs.get("s3_logs_url")
            if isinstance(summary_s3_url, str) and summary_s3_url.strip():
                resolved_s3_logs_url = summary_s3_url.strip()

        round_row.validator_summary = merged
        if resolved_s3_logs_url:
            round_row.s3_logs_url = resolved_s3_logs_url
            merged["s3_logs_url"] = resolved_s3_logs_url

        round_row.ended_at = ended_at
        # Keep canonical round_validators table aligned with finish payloads.
        # round_validators stores only the canonical post-consensus JSON now.
        try:
            post_consensus_json = merged.get("evaluation_post_consensus")
            await self.session.execute(
                text(
                    """
                    UPDATE round_validators
                    SET
                        post_consensus_json = COALESCE(CAST(:post_consensus_json AS JSONB), post_consensus_json),
                        ipfs_uploaded = COALESCE(CAST(:ipfs_uploaded AS JSONB), ipfs_uploaded),
                        ipfs_downloaded = COALESCE(CAST(:ipfs_downloaded AS JSONB), ipfs_downloaded),
                        s3_logs_url = COALESCE(:s3_logs_url, s3_logs_url),
                        validator_state = COALESCE(CAST(:validator_state AS JSONB), validator_state),
                        validator_iwap_prev_round_json = COALESCE(CAST(:validator_iwap_prev_round_json AS JSONB), validator_iwap_prev_round_json),
                        updated_at = NOW()
                    WHERE validator_round_id = :validator_round_id
                    """
                ),
                {
                    "validator_round_id": validator_round_id,
                    "post_consensus_json": json.dumps(post_consensus_json) if post_consensus_json is not None else None,
                    "ipfs_uploaded": json.dumps(merged.get("ipfs_uploaded")) if merged.get("ipfs_uploaded") is not None else None,
                    "ipfs_downloaded": json.dumps(merged.get("ipfs_downloaded")) if merged.get("ipfs_downloaded") is not None else None,
                    "s3_logs_url": resolved_s3_logs_url,
                    "validator_state": json.dumps(validator_state) if validator_state is not None else None,
                    "validator_iwap_prev_round_json": json.dumps(validator_iwap_prev_round_json) if validator_iwap_prev_round_json is not None else None,
                },
            )
        except Exception:
            logger.exception("finish_round: failed to synchronize summaries into round_validators")

        zero_reason_map: Dict[str, Optional[str]] = {}
        if agent_runs:
            for agent_run_data in agent_runs:
                agent_run_id = agent_run_data.get("agent_run_id")
                if not agent_run_id:
                    continue
                zero_reason_map[agent_run_id] = agent_run_data.get("zero_reason")

        stmt_runs = (
            select(AgentEvaluationRunORM)
            .options(
                selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(AgentEvaluationRunORM.evaluations)
                .options(
                    defer(EvaluationORM.gif_recording),
                    defer(EvaluationORM.extra_info),
                )
                .selectinload(EvaluationORM.execution_history_record),
            )
            .where(AgentEvaluationRunORM.validator_round_id == validator_round_id)
        )
        run_rows_result = await self.session.scalars(stmt_runs)
        run_rows = list(run_rows_result)

        for run_row in run_rows:
            metrics = self._compute_agent_run_stats(run_row)
            run_row.total_tasks = metrics["total_tasks"]
            run_row.success_tasks = metrics["success_tasks"]
            run_row.failed_tasks = metrics["failed_tasks"]
            run_row.average_score = metrics["average_score"]
            run_row.average_execution_time = metrics["average_execution_time"]
            run_row.average_reward = metrics["average_reward"]

            if run_row.agent_run_id in zero_reason_map:
                run_row.zero_reason = zero_reason_map[run_row.agent_run_id]
            if run_row.zero_reason is None and self._run_has_zero_score(run_row):
                run_row.zero_reason = self._derive_run_zero_reason_from_evaluations(run_row)

            # rank and weight removed from agent_evaluation_runs
            # They are now stored in validator_round_summary_miners and updated there

        canonical_round_id = await self.session.scalar(
            text("SELECT round_id FROM round_validators WHERE validator_round_id = :validator_round_id LIMIT 1"),
            {"validator_round_id": validator_round_id},
        )
        is_shadow_only_round = canonical_round_id is None
        if is_shadow_only_round:
            logger.info(
                "finish_round: shadow-only validator_round_id=%s (no canonical round_id yet); skipping round_summary materialization and keeping validator-local JSON persisted.",
                validator_round_id,
            )
            return

        # Populate validator_round_summary_miners table
        await self._populate_round_summary(
            validator_round_id=validator_round_id,
            local_evaluation=local_evaluation,
            post_consensus_evaluation=post_consensus_evaluation,
            subnet_price=alpha_price,
        )
        await self._enrich_validator_summary_post_consensus_from_db(round_row)
        try:
            await self._sync_round_validators_post_consensus_json(round_row)
        except Exception:
            logger.exception("finish_round: failed to sync enriched post-consensus summary into round_validators")
        if authoritative_finish:
            try:
                await self._upsert_round_summary_from_validator_summary(round_row)
            except Exception:
                logger.exception("finish_round: failed to upsert round_summary from summary tables")
        else:
            logger.info(
                "finish_round: skipped round_summary upsert for non-authoritative validator_round_id=%s",
                validator_round_id,
            )

    async def submit_round(self, payload: ValidatorRoundSubmissionRequest) -> PersistenceResult:
        """Persist the entire round submission payload."""
        self._assert_unique_payload(payload)

        validator_round = payload.validator_round
        await self._ensure_unique_round_number(
            validator_round.validator_uid,
            validator_round.round_number,
            exclude_round_id=None,
        )

        existing_round = await self._get_round_row(validator_round.validator_round_id)
        round_kwargs = await self._validator_round_kwargs(validator_round)

        if existing_round:
            for key, value in round_kwargs.items():
                setattr(existing_round, key, value)
            round_row = existing_round
        else:
            round_row = ValidatorRoundORM(**round_kwargs)
            self.session.add(round_row)
        await self.session.flush()

        # Snapshots (1:1 relationship - only one snapshot per round)
        validator_snapshot_ids: List[int] = []
        if payload.validator_snapshots:
            # Take the first snapshot (should only be one)
            snapshot = payload.validator_snapshots[0]
            row = await self._upsert_validator_snapshot(
                round_row,
                snapshot,
            )
            validator_snapshot_ids.append(row.id)

        miner_snapshot_ids: List[int] = []
        for snapshot in payload.miner_snapshots:
            row = await self._upsert_miner_snapshot(round_row, snapshot)
            miner_snapshot_ids.append(row.id)

        # Agent runs
        agent_run_ids: List[str] = []
        for agent_run in payload.agent_evaluation_runs:
            kwargs = self._agent_run_kwargs(agent_run)
            stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == agent_run.agent_run_id)
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(f"agent_run_id {agent_run.agent_run_id} is provided multiple times")
            self.session.add(AgentEvaluationRunORM(**kwargs))
            agent_run_ids.append(agent_run.agent_run_id)

        # Tasks
        await self.add_tasks(round_row.validator_round_id, payload.tasks)
        task_ids = [task.task_id for task in payload.tasks]

        # Task solutions
        task_solution_ids: List[str] = []
        for solution in payload.task_solutions:
            kwargs = self._task_solution_kwargs(solution)
            stmt = select(TaskSolutionORM).where(TaskSolutionORM.solution_id == solution.solution_id)
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(f"task_solution_id {solution.solution_id} is provided multiple times")
            self.session.add(TaskSolutionORM(**kwargs))
            task_solution_ids.append(solution.solution_id)

        # Evaluations
        evaluation_ids: List[str] = []
        evaluation_rows: Dict[str, EvaluationORM] = {}
        execution_histories: List[tuple[EvaluationORM, list]] = []  # Store for later creation

        for evaluation in payload.evaluations:
            kwargs = self._evaluation_kwargs(evaluation)

            # Ensure miner_uid and miner_hotkey are set from agent_run if not in evaluation model
            if not kwargs.get("miner_uid") or not kwargs.get("miner_hotkey"):
                # Find the agent_run to get miner info
                agent_run_stmt = select(AgentEvaluationRunORM).where(AgentEvaluationRunORM.agent_run_id == evaluation.agent_run_id)
                agent_run_row = await self.session.scalar(agent_run_stmt)
                if agent_run_row:
                    if not kwargs.get("miner_uid") and agent_run_row.miner_uid is not None:
                        kwargs["miner_uid"] = agent_run_row.miner_uid
                    if not kwargs.get("miner_hotkey") and agent_run_row.miner_hotkey:
                        kwargs["miner_hotkey"] = agent_run_row.miner_hotkey

            # Separate execution_history to store in related table
            execution_history_data = kwargs.pop("execution_history", [])

            stmt = select(EvaluationORM).where(EvaluationORM.evaluation_id == evaluation.evaluation_id)
            existing = await self.session.scalar(stmt)
            if existing:
                raise DuplicateIdentifierError(f"evaluation_id {evaluation.evaluation_id} is provided multiple times")
            evaluation_row = EvaluationORM(**kwargs)
            self.session.add(evaluation_row)
            evaluation_ids.append(evaluation.evaluation_id)
            evaluation_rows[evaluation.evaluation_id] = evaluation_row

            if execution_history_data:
                execution_histories.append((evaluation_row, execution_history_data))

        await self.session.flush()

        # Persist per-model/provider LLM usage (after flush so eval rows exist; subnet can send llm_* scalars)
        for evaluation in payload.evaluations:
            eval_row = evaluation_rows.get(evaluation.evaluation_id)
            if eval_row is not None:
                await self._sync_llm_usage(eval_row, self._llm_usage_from_evaluation(evaluation))

        # Create execution_history records after flush (so we have evaluation.id)
        if execution_histories:
            from app.db.models import EvaluationExecutionHistoryORM

            for evaluation_row, execution_history_data in execution_histories:
                execution_history_row = EvaluationExecutionHistoryORM(
                    evaluation_id=evaluation_row.evaluation_id,
                    execution_history=execution_history_data,
                )
                self.session.add(execution_history_row)

        # 🔍 CRITICAL: Update agent_run stats after adding all evaluations in batch
        # This ensures average_score is NEVER NULL if there are evaluations
        # This is especially important for submit_round which adds multiple evaluations at once
        if agent_run_ids:
            from sqlalchemy.orm import selectinload

            stmt_runs = select(AgentEvaluationRunORM).options(selectinload(AgentEvaluationRunORM.evaluations)).where(AgentEvaluationRunORM.agent_run_id.in_(agent_run_ids))
            run_rows_result = await self.session.scalars(stmt_runs)
            run_rows = list(run_rows_result)

            for run_row in run_rows:
                metrics = self._compute_agent_run_stats(run_row)
                run_row.total_tasks = metrics["total_tasks"]
                run_row.success_tasks = metrics["success_tasks"]
                run_row.failed_tasks = metrics["failed_tasks"]
                run_row.average_score = metrics["average_score"]
                run_row.average_execution_time = metrics["average_execution_time"]
                run_row.average_reward = metrics["average_reward"]

        saved = {
            "validator_round": round_row.validator_round_id,
            "validator_snapshots": validator_snapshot_ids,
            "miner_snapshots": miner_snapshot_ids,
            "agent_evaluation_runs": agent_run_ids,
            "tasks": task_ids,
            "task_solutions": task_solution_ids,
            "evaluations": evaluation_ids,
        }
        # Get validator_uid from snapshot (1:1 relationship)
        validator_uid = payload.validator_snapshots[0].validator_uid if payload.validator_snapshots else None
        if validator_uid is None:
            raise ValueError("No validator snapshot provided")
        return PersistenceResult(
            validator_uid=validator_uid,
            saved_entities=saved,
        )

    async def ensure_round_exists_or_create_minimal_for_round_log(
        self,
        validator_round_id: str,
        season: Optional[int],
        round_in_season: Optional[int],
        validator_uid: Optional[int],
        validator_hotkey: Optional[str],
        *,
        owner_hotkey_from_request: Optional[str] = None,
    ) -> ValidatorRoundORM:
        """
        Return the round row, creating a minimal round + validator snapshot if the round
        does not exist (e.g. after IWAP reset). Allows round-log upload to succeed and
        finish_round to update the row later.
        """
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        round_row = await self.session.scalar(stmt)
        if round_row is not None:
            return round_row
        uid = int(validator_uid) if validator_uid is not None else 0
        hotkey = (validator_hotkey or owner_hotkey_from_request or "").strip()
        if not hotkey:
            raise ValueError("Cannot create minimal round: validator_hotkey or owner_hotkey_from_request required")
        round_row = ValidatorRoundORM(
            validator_round_id=validator_round_id,
            season_number=season,
            round_number_in_season=round_in_season,
            start_block=0,
            end_block=None,
            start_epoch=0,
            end_epoch=None,
            started_at=0.0,
            ended_at=None,
            n_tasks=0,
            status="active",
        )
        self.session.add(round_row)
        await self.session.flush()
        snapshot = ValidatorRoundValidatorORM(
            validator_round_id=validator_round_id,
            validator_uid=uid,
            validator_hotkey=hotkey,
            validator_coldkey=None,
            name=None,
            stake=None,
            vtrust=None,
            image_url=None,
            version=None,
            config=None,
        )
        self.session.add(snapshot)
        await self.session.flush()
        stmt = select(ValidatorRoundORM).options(selectinload(ValidatorRoundORM.validator_snapshot)).where(ValidatorRoundORM.validator_round_id == validator_round_id)
        round_row = await self.session.scalar(stmt)
        assert round_row is not None
        logger.info("Created minimal validator round %s for round-log upload (e.g. after IWAP reset)", validator_round_id)
        return round_row

    async def ensure_unique_round_number(
        self,
        validator_uid: int,
        round_number: Optional[int],
        *,
        exclude_round_id: Optional[str] = None,
    ) -> None:
        """DEPRECATED: Public wrapper to guard against duplicate round numbers."""
        await self._ensure_unique_round_number(validator_uid, round_number, exclude_round_id=exclude_round_id)
