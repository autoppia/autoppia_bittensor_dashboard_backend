from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.core import (
    AgentEvaluationRun,
    Evaluation,
    Miner,
    Task,
    TaskSolution,
    Validator,
    ValidatorRound,
    ValidatorRoundMiner,
    ValidatorRoundValidator,
)
from app.utils.images import resolve_agent_image, sanitize_miner_image


def _resolve_miner_snapshot_image(snapshot: ValidatorRoundMiner) -> None:
    """
    Normalize miner snapshot images so we persist full URLs and apply fallbacks.
    """
    sanitized = sanitize_miner_image(snapshot.image_url)
    info = SimpleNamespace(
        agent_image=sanitized or "",
        is_sota=bool(snapshot.is_sota),
        agent_name=(snapshot.agent_name or "").strip(),
        hotkey=snapshot.miner_hotkey,
        uid=snapshot.miner_uid,
    )
    resolved = resolve_agent_image(info, existing=sanitized or None)
    if not resolved:
        resolved = resolve_agent_image(info)
    snapshot.image_url = resolved


class StartRoundRequest(BaseModel):
    validator_identity: Validator
    validator_round: ValidatorRound
    validator_snapshot: ValidatorRoundValidator

    @field_validator("validator_round")
    @classmethod
    def _ensure_round(cls, round_model: ValidatorRound) -> ValidatorRound:
        # Validate season and round fields are present
        if round_model.season_number is None:
            raise ValueError("season_number is required to start a validator round")
        if round_model.round_number_in_season is None:
            raise ValueError("round_number_in_season is required to start a validator round")

        # Validate that the season and round match the start_block
        from app.services.round_calc import compute_season_number

        expected_season = compute_season_number(round_model.start_block)
        if round_model.season_number != expected_season:
            raise ValueError(f"season_number mismatch: got {round_model.season_number}, expected {expected_season} for start_block {round_model.start_block}")

        return round_model


class SetTasksRequest(BaseModel):
    tasks: list[Task] = Field(default_factory=list)


class StartAgentRunRequest(BaseModel):
    agent_run: AgentEvaluationRun
    miner_identity: Miner
    miner_snapshot: ValidatorRoundMiner


class AddEvaluationRequest(BaseModel):
    task: Task
    task_solution: TaskSolution
    evaluation: Evaluation
    evaluation_result: Dict[str, Any] | None = None


class FinishRoundAgentRun(BaseModel):
    agent_run_id: str
    rank: int | None = None
    weight: float | None = None
    # FASE 1: Nuevos campos opcionales
    miner_name: str | None = None
    avg_reward: float | None = None  # Average reward (evaluation_score + time_score)
    avg_evaluation_time: float | None = None
    tasks_attempted: int | None = None
    tasks_completed: int | None = None
    tasks_failed: int | None = None
    zero_reason: str | None = None  # Reason for score 0 (e.g. over_cost_limit, deploy_failed, all_tasks_failed)
    is_reused: bool = False  # Same (repo, commit) already evaluated this season
    reused_from_agent_run_id: str | None = None  # Source agent_run_id when is_reused


class RoundMetadata(BaseModel):
    """Round timing and metadata."""

    round_number: int
    started_at: float
    ended_at: float
    start_block: int
    end_block: int
    start_epoch: float
    end_epoch: float
    tasks_total: int
    tasks_completed: int
    miners_responded_handshake: int  # miners that answered the round handshake
    miners_evaluated: int  # miners that had at least one task evaluated


class FinishRoundRequest(BaseModel):
    status: str = Field(default="finished", description="Final status for the round")
    ended_at: float | None = Field(default=None, description="Epoch timestamp when the round finished")
    agent_runs: list[FinishRoundAgentRun] = Field(default_factory=list)
    round_metadata: RoundMetadata | None = Field(default=None, alias="round")
    validator_summary: Dict[str, Any] | None = None
    local_evaluation: Dict[str, Any] | None = None
    post_consensus_evaluation: Dict[str, Any] | None = None
    # IPFS data
    ipfs_uploaded: Dict[str, Any] | None = None
    ipfs_downloaded: Dict[str, Any] | None = None
    s3_logs_url: str | None = None
    validator_state: Dict[str, Any] | None = None
    validator_iwap_prev_round_json: Dict[str, Any] | None = None


class ValidatorRoundLogUploadRequest(BaseModel):
    validator_round_id: str = Field(..., description="Validator round ID that owns this log")
    season: Optional[int] = Field(None, description="Season number")
    round_in_season: Optional[int] = Field(None, description="Round number inside season")
    validator_uid: Optional[int] = Field(None, description="Validator UID")
    validator_hotkey: Optional[str] = Field(None, description="Validator hotkey")
    content: str = Field(..., description="Raw validator round log payload")


class ValidatorRoundLogUploadResponse(BaseModel):
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
