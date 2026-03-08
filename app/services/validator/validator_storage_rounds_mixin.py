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
        round_number_in_season: int,
    ) -> None:
        """
        Link shadow round_validators rows (round_id IS NULL) to canonical round_id
        once the canonical round exists.
        """
        await self.session.execute(
            text(
                """
                WITH canonical AS (
                    SELECT r.round_id, r.start_block, r.end_block, r.start_epoch, r.end_epoch
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
                    start_block = COALESCE(rv.start_block, c.start_block),
                    end_block = COALESCE(rv.end_block, c.end_block),
                    start_epoch = COALESCE(rv.start_epoch, c.start_epoch),
                    end_epoch = COALESCE(rv.end_epoch, c.end_epoch),
                    updated_at = NOW()
                FROM canonical c
                WHERE rv.round_id IS NULL
                  AND COALESCE(rv.pending_round_link, FALSE) = TRUE
                  AND rv.season_number = :season_number
                  AND rv.round_number_in_season = :round_number_in_season
                """
            ),
            {
                "season_number": int(season_number),
                "round_number_in_season": int(round_number_in_season),
            },
        )
        # Backfill per-miner rows that were persisted before canonical linking.
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
                  AND rv.round_number_in_season = :round_number_in_season
                """
            ),
            {
                "season_number": int(season_number),
                "round_number_in_season": int(round_number_in_season),
            },
        )

    async def _sync_round_validator_miner_reuse_flags(
        self,
        *,
        validator_round_id: str,
        miner_uid: Optional[int] = None,
    ) -> None:
        """Keep round_validator_miners reuse flags aligned with miner_evaluation_runs."""
        sql_base = """
            UPDATE round_validator_miners rvm
            SET
                is_reused = COALESCE(mer.is_reused, FALSE),
                reused_from_agent_run_id = CASE
                    WHEN COALESCE(mer.is_reused, FALSE) THEN mer.reused_from_agent_run_id
                    ELSE NULL
                END,
                reused_from_round_id = CASE
                    WHEN COALESCE(mer.is_reused, FALSE) AND mer.reused_from_agent_run_id IS NOT NULL THEN (
                        SELECT rv_src.round_id
                        FROM miner_evaluation_runs mer_src
                        JOIN round_validators rv_src
                            ON rv_src.validator_round_id = mer_src.validator_round_id
                        WHERE mer_src.agent_run_id = mer.reused_from_agent_run_id
                        LIMIT 1
                    )
                    ELSE NULL
                END,
                updated_at = NOW()
            FROM round_validators rv, miner_evaluation_runs mer
            WHERE rv.round_validator_id = rvm.round_validator_id
              AND rv.validator_round_id = :validator_round_id
              AND mer.validator_round_id = rv.validator_round_id
              AND mer.miner_uid = rvm.miner_uid
        """
        params: Dict[str, Any] = {"validator_round_id": validator_round_id}
        if miner_uid is not None:
            sql_base += "\n  AND rvm.miner_uid = :miner_uid\n"
            params["miner_uid"] = int(miner_uid)

        await self.session.execute(text(sql_base), params)

    async def _set_round_validator_miner_reuse_state(
        self,
        *,
        validator_round_id: str,
        miner_uid: int,
        is_reused: bool,
        reused_from_agent_run_id: Optional[str] = None,
    ) -> None:
        reused_from_round_id = None
        if is_reused and reused_from_agent_run_id:
            reused_from_round_id = await self.session.scalar(
                text(
                    """
                    SELECT rv_src.round_id
                    FROM miner_evaluation_runs mer_src
                    JOIN round_validators rv_src
                      ON rv_src.validator_round_id = mer_src.validator_round_id
                    WHERE mer_src.agent_run_id = :source_run_id
                    LIMIT 1
                    """
                ),
                {"source_run_id": str(reused_from_agent_run_id)},
            )

        await self.session.execute(
            text(
                """
                UPDATE round_validator_miners rvm
                SET
                  is_reused = :is_reused,
                  reused_from_agent_run_id = :reused_from_agent_run_id,
                  reused_from_round_id = :reused_from_round_id,
                  updated_at = NOW()
                FROM round_validators rv
                WHERE rv.round_validator_id = rvm.round_validator_id
                  AND rv.validator_round_id = :validator_round_id
                  AND rvm.miner_uid = :miner_uid
                """
            ),
            {
                "validator_round_id": validator_round_id,
                "miner_uid": int(miner_uid),
                "is_reused": bool(is_reused),
                "reused_from_agent_run_id": str(reused_from_agent_run_id) if is_reused and reused_from_agent_run_id else None,
                "reused_from_round_id": int(reused_from_round_id) if reused_from_round_id is not None else None,
            },
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
        resolved_reuse_source = None
        requested_reuse_id = kwargs.get("reused_from_agent_run_id")
        if kwargs.get("is_reused") and requested_reuse_id:
            resolved_reuse_source = await self._resolve_reused_source_run(str(requested_reuse_id))
            if resolved_reuse_source is None:
                logger.warning(
                    "start_agent_run: reused_from_agent_run_id=%s not found for validator_round_id=%s miner_uid=%s; downgrading to non-reused run to avoid FK failure.",
                    requested_reuse_id,
                    validator_round_id,
                    kwargs.get("miner_uid"),
                )
                kwargs["is_reused"] = False
                kwargs["reused_from_agent_run_id"] = None
            else:
                # Canonicalize chain reuse to the original source run id.
                kwargs["reused_from_agent_run_id"] = resolved_reuse_source.agent_run_id

        is_reused = bool(kwargs.get("is_reused"))
        reused_from_id = kwargs.get("reused_from_agent_run_id")
        if is_reused and reused_from_id:
            source = resolved_reuse_source or await self._resolve_reused_source_run(str(reused_from_id))
            canonical_source_id = source.agent_run_id if source is not None else str(reused_from_id)
            await self._set_round_validator_miner_reuse_state(
                validator_round_id=validator_round_id,
                miner_uid=int(kwargs.get("miner_uid")),
                is_reused=True,
                reused_from_agent_run_id=canonical_source_id,
            )
            logger.debug(
                "start_agent_run: skipped synthetic reused run creation for miner_uid=%s in validator_round_id=%s; source=%s",
                kwargs.get("miner_uid"),
                validator_round_id,
                canonical_source_id,
            )
            return None

        row = AgentEvaluationRunORM(**kwargs)
        self.session.add(row)
        await self.session.flush()

        # Keep round-level miner reuse flags in sync for UI/analytics consistency.
        if row.miner_uid is not None:
            await self._set_round_validator_miner_reuse_state(
                validator_round_id=validator_round_id,
                miner_uid=int(row.miner_uid),
                is_reused=False,
            )

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

        post_summary = merged.get("evaluation_post_consensus")
        if isinstance(post_summary, dict):
            # Remove noisy internal key from post-consensus summary shown in UI payloads.
            post_summary.pop("schema_version", None)
            merged["evaluation_post_consensus"] = post_summary

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
            post_summary_json = merged.get("evaluation_post_consensus")
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
                    "post_consensus_json": json.dumps(post_summary_json) if post_summary_json is not None else None,
                    "ipfs_uploaded": json.dumps(merged.get("ipfs_uploaded")) if merged.get("ipfs_uploaded") is not None else None,
                    "ipfs_downloaded": json.dumps(merged.get("ipfs_downloaded")) if merged.get("ipfs_downloaded") is not None else None,
                    "s3_logs_url": resolved_s3_logs_url,
                    "validator_state": json.dumps(validator_state) if validator_state is not None else None,
                    "validator_iwap_prev_round_json": json.dumps(validator_iwap_prev_round_json) if validator_iwap_prev_round_json is not None else None,
                },
            )
        except Exception:
            logger.exception("finish_round: failed to synchronize summaries into round_validators")

        rank_map: Dict[str, Optional[int]] = {}
        weight_map: Dict[str, Optional[float]] = {}
        zero_reason_map: Dict[str, Optional[str]] = {}
        is_reused_map: Dict[str, bool] = {}
        reused_from_map: Dict[str, Optional[str]] = {}
        agent_runs_by_id: Dict[str, Dict[str, Any]] = {}
        if agent_runs:
            for agent_run_data in agent_runs:
                agent_run_id = agent_run_data.get("agent_run_id")
                if not agent_run_id:
                    continue
                rank_map[agent_run_id] = agent_run_data.get("rank")
                weight_map[agent_run_id] = agent_run_data.get("weight")
                zero_reason_map[agent_run_id] = agent_run_data.get("zero_reason")
                is_reused_map[agent_run_id] = bool(agent_run_data.get("is_reused", False))
                reused_from_map[agent_run_id] = agent_run_data.get("reused_from_agent_run_id")
                agent_runs_by_id[agent_run_id] = agent_run_data

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
            is_reused = is_reused_map.get(run_row.agent_run_id, getattr(run_row, "is_reused", False))
            # Do NOT set ended_at/elapsed_sec here: agent runs are per-miner and already closed
            # - Reused: closed at start_agent_run (ended_at=started_at, elapsed_sec=0)
            # - Evaluated: closed in add_evaluation when we received the last evaluation

            if not is_reused:
                metrics = self._compute_agent_run_stats(run_row)
                run_row.total_tasks = metrics["total_tasks"]
                run_row.success_tasks = metrics["success_tasks"]
                run_row.failed_tasks = metrics["failed_tasks"]
                run_row.average_score = metrics["average_score"]
                run_row.average_execution_time = metrics["average_execution_time"]
                run_row.average_reward = metrics["average_reward"]
            else:
                # Reused runs: source run is truth. Never overwrite with payload 0/0/0 — rounds are sequential, source always has metrics.
                payload_data = agent_runs_by_id.get(run_row.agent_run_id) or {}
                source_id = reused_from_map.get(run_row.agent_run_id) or getattr(run_row, "reused_from_agent_run_id", None)
                source_run = await self._resolve_reused_source_run(source_id) if source_id else None
                if source_run is not None:
                    run_row.reused_from_agent_run_id = source_run.agent_run_id

                payload_attempted = payload_data.get("tasks_attempted")
                source_total = (getattr(source_run, "total_tasks", None) or 0) if source_run else 0
                # Prefer source when payload would zero out (validator sometimes sends 0 for reused runs)
                use_source_metrics = source_run is not None and source_total > 0 and (payload_attempted is None or int(payload_attempted or 0) == 0)

                if use_source_metrics:
                    run_row.total_tasks = int(source_run.total_tasks or 0)
                    run_row.success_tasks = int(getattr(source_run, "success_tasks", 0) or 0)
                    run_row.failed_tasks = int(getattr(source_run, "failed_tasks", 0) or 0)
                    run_row.average_score = source_run.average_score
                    run_row.average_execution_time = getattr(source_run, "average_execution_time", None)
                    run_row.average_reward = getattr(source_run, "average_reward", None)
                    if getattr(source_run, "zero_reason", None):
                        run_row.zero_reason = source_run.zero_reason
                else:
                    if payload_attempted is not None:
                        run_row.total_tasks = int(payload_attempted)
                    elif source_run is not None:
                        run_row.total_tasks = int(source_run.total_tasks or 0)
                    if payload_data.get("tasks_completed") is not None:
                        run_row.success_tasks = int(payload_data["tasks_completed"])
                    elif source_run is not None:
                        run_row.success_tasks = int(getattr(source_run, "success_tasks", 0) or 0)
                    if payload_data.get("tasks_failed") is not None:
                        run_row.failed_tasks = int(payload_data["tasks_failed"])
                    elif source_run is not None:
                        run_row.failed_tasks = int(getattr(source_run, "failed_tasks", 0) or 0)
                    if payload_data.get("avg_reward") is not None:
                        run_row.average_reward = float(payload_data["avg_reward"])
                    elif source_run is not None and getattr(source_run, "average_reward", None) is not None:
                        run_row.average_reward = float(source_run.average_reward)
                    if payload_data.get("avg_evaluation_time") is not None:
                        payload_avg_eval_time = float(payload_data["avg_evaluation_time"])
                        if payload_avg_eval_time > 0.0:
                            run_row.average_execution_time = payload_avg_eval_time
                        elif source_run is not None and getattr(source_run, "average_execution_time", None) is not None:
                            run_row.average_execution_time = float(source_run.average_execution_time)
                        else:
                            run_row.average_execution_time = payload_avg_eval_time
                    elif source_run is not None and getattr(source_run, "average_execution_time", None) is not None:
                        run_row.average_execution_time = float(source_run.average_execution_time)
                    total = getattr(run_row, "total_tasks", 0) or 0
                    success = getattr(run_row, "success_tasks", 0) or 0
                    run_row.average_score = (success / total) if total else (float(source_run.average_score) if source_run and getattr(source_run, "average_score", None) is not None else 0.0)

            if run_row.agent_run_id in zero_reason_map:
                run_row.zero_reason = zero_reason_map[run_row.agent_run_id]
            if run_row.agent_run_id in is_reused_map:
                run_row.is_reused = is_reused_map[run_row.agent_run_id]
            if run_row.agent_run_id in reused_from_map:
                # Do not downgrade an already-resolved root source to an intermediate reused run.
                if not getattr(run_row, "reused_from_agent_run_id", None):
                    candidate_id = reused_from_map[run_row.agent_run_id]
                    if candidate_id:
                        # Validate FK before assigning: the source run must exist in the DB.
                        # The validator may reference a run that was purged on restart, or a
                        # cross-validator run that was never committed to this DB.
                        candidate_run = await self._get_agent_run_row(str(candidate_id))
                        if candidate_run is not None:
                            run_row.reused_from_agent_run_id = candidate_run.agent_run_id
                        else:
                            # Source not in DB: try to find the best existing run for this miner.
                            fallback = await self._find_best_source_run_for_miner(
                                miner_uid=run_row.miner_uid,
                                exclude_validator_round_id=validator_round_id,
                            )
                            run_row.reused_from_agent_run_id = fallback.agent_run_id if fallback else None
                            if fallback:
                                logger.info(
                                    "finish_round: source run %s not found for miner_uid=%s; using fallback %s",
                                    candidate_id,
                                    run_row.miner_uid,
                                    fallback.agent_run_id,
                                )
                            else:
                                logger.warning(
                                    "finish_round: source run %s not found and no fallback for miner_uid=%s; setting reused_from=NULL",
                                    candidate_id,
                                    run_row.miner_uid,
                                )

            # If run has effective score 0 and no zero_reason: for reused runs use source run's zero_reason, else derive from evaluations
            if run_row.zero_reason is None and self._run_has_zero_score(run_row):
                source_id = getattr(run_row, "reused_from_agent_run_id", None)
                if source_id:
                    source_run = await self._get_agent_run_row(source_id)
                    if source_run and getattr(source_run, "zero_reason", None):
                        run_row.zero_reason = source_run.zero_reason
                if run_row.zero_reason is None:
                    run_row.zero_reason = self._derive_run_zero_reason_from_evaluations(run_row)

            # rank and weight removed from agent_evaluation_runs
            # They are now stored in validator_round_summary_miners and updated there

        # Cascade: runs that reuse a run we just updated may have been processed in a
        # finish_round that ran before this round (e.g. round 5 before round 4). Now that
        # this run has total_tasks/failed_tasks/average_execution_time, copy them to all
        # runs that have reused_from_agent_run_id = this run.
        for run_row in run_rows:
            source_id = getattr(run_row, "agent_run_id", None)
            if not source_id:
                continue
            has_stats = (getattr(run_row, "total_tasks", None) or 0) > 0 or getattr(run_row, "average_execution_time", None) is not None
            if not has_stats:
                continue
            stmt_reused = select(AgentEvaluationRunORM).where(
                AgentEvaluationRunORM.reused_from_agent_run_id == source_id,
            )
            reused_rows_result = await self.session.scalars(stmt_reused)
            for reused_row in reused_rows_result:
                if (getattr(reused_row, "total_tasks", None) or 0) == 0 and getattr(reused_row, "average_execution_time", None) is None:
                    reused_row.total_tasks = run_row.total_tasks or 0
                    reused_row.success_tasks = run_row.success_tasks or 0
                    reused_row.failed_tasks = run_row.failed_tasks or 0
                    reused_row.average_score = run_row.average_score
                    reused_row.average_execution_time = run_row.average_execution_time
                    reused_row.average_reward = run_row.average_reward
                    if getattr(run_row, "zero_reason", None) and getattr(reused_row, "zero_reason", None) is None:
                        reused_row.zero_reason = run_row.zero_reason
                    logger.debug(
                        "finish_round: cascaded stats from source run %s to reused run %s",
                        source_id,
                        reused_row.agent_run_id,
                    )

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
        # Ensure summary rows carry reuse provenance from agent runs.
        await self._sync_round_validator_miner_reuse_flags(
            validator_round_id=validator_round_id,
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
