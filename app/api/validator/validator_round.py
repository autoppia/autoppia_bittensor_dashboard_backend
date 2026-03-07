"""Progressive validator round ingestion endpoints aligned with normalized models."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.validator.validator_round_handlers_auth import validator_auth_check
from app.api.validator.validator_round_handlers_evaluations import add_evaluation, add_evaluations_batch
from app.api.validator.validator_round_handlers_lifecycle import finish_round, get_runtime_config, set_tasks, start_agent_run, start_round, sync_runtime_config
from app.api.validator.validator_round_handlers_logs import upload_round_log
from app.services.validator.validator_auth import require_validator_auth

router = APIRouter(prefix="/api/v1/validator-rounds", tags=["validator-rounds"])

router.post("/auth-check", dependencies=[Depends(require_validator_auth)])(validator_auth_check)
router.get("/runtime-config", dependencies=[Depends(require_validator_auth)])(get_runtime_config)
router.post("/runtime-config", dependencies=[Depends(require_validator_auth)])(sync_runtime_config)
router.post("/start", dependencies=[Depends(require_validator_auth)])(start_round)
router.post("/{validator_round_id}/tasks", dependencies=[Depends(require_validator_auth)])(set_tasks)
router.post("/{validator_round_id}/agent-runs", dependencies=[Depends(require_validator_auth)])(start_agent_run)
router.post("/{validator_round_id}/agent-runs/start", dependencies=[Depends(require_validator_auth)])(start_agent_run)
router.post("/{validator_round_id}/agent-runs/{agent_run_id}/evaluations/batch", dependencies=[Depends(require_validator_auth)])(add_evaluations_batch)
router.post("/{validator_round_id}/evaluations/batch", dependencies=[Depends(require_validator_auth)])(add_evaluations_batch)
router.post("/{validator_round_id}/agent-runs/{agent_run_id}/evaluations", dependencies=[Depends(require_validator_auth)])(add_evaluation)
router.post("/{validator_round_id}/evaluations", dependencies=[Depends(require_validator_auth)])(add_evaluation)
router.post("/{validator_round_id}/finish", dependencies=[Depends(require_validator_auth)])(finish_round)
router.post("/{validator_round_id}/round-log", response_model_exclude_none=True, dependencies=[Depends(require_validator_auth)])(upload_round_log)
router.post("/logs/upload", response_model_exclude_none=True, dependencies=[Depends(require_validator_auth)])(upload_round_log)

__all__ = ["router"]
