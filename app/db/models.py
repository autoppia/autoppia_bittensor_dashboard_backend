from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Always use PostgreSQL JSONB (PostgreSQL is required)
try:
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB  # type: ignore
except ImportError:
    raise ImportError("PostgreSQL dialect not available - install asyncpg")


def _select_json_type():
    """Always return JSONB for PostgreSQL."""
    return _PG_JSONB


JSON = _select_json_type()


def utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    """Reusable timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Validator rounds and snapshots
# ---------------------------------------------------------------------------


class ValidatorRoundORM(TimestampMixin, Base):
    """Canonical representation of a validator_round."""

    __tablename__ = "validator_rounds"
    __table_args__ = (
        Index("ix_validator_rounds_status", "status"),
        Index("ix_validator_rounds_season_round", "season_number", "round_number_in_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    season_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    round_number_in_season: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    start_block: Mapped[int] = mapped_column(Integer, nullable=False)
    end_block: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    end_epoch: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    started_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    n_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    validator_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    s3_logs_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Consensus decision snapshot (round-level, queryable source of truth).
    # Legacy SQL columns still use winner/reigning/candidate *_score names, but
    # the ORM exposes reward semantics because this layer no longer models
    # evaluation score here; it models leadership reward.
    leader_after_uid: Mapped[Optional[int]] = mapped_column("winner_uid", Integer, nullable=True, index=True)
    leader_after_reward: Mapped[Optional[float]] = mapped_column("winner_score", Float, nullable=True)
    leader_before_uid: Mapped[Optional[int]] = mapped_column("reigning_uid_before_round", Integer, nullable=True, index=True)
    leader_before_reward: Mapped[Optional[float]] = mapped_column("reigning_score_before_round", Float, nullable=True)
    candidate_uid: Mapped[Optional[int]] = mapped_column("top_candidate_uid", Integer, nullable=True, index=True)
    candidate_reward: Mapped[Optional[float]] = mapped_column("top_candidate_score", Float, nullable=True)
    required_improvement_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dethroned: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, index=True)

    validator_snapshot: Mapped["ValidatorRoundValidatorORM"] = relationship(
        back_populates="validator_round",
        uselist=False,  # 1:1 relationship
        cascade="all, delete-orphan",
    )
    miner_snapshots: Mapped[list["ValidatorRoundMinerORM"]] = relationship(
        back_populates="validator_round",
        cascade="all, delete-orphan",
    )
    agent_runs: Mapped[list["AgentEvaluationRunORM"]] = relationship(
        back_populates="validator_round",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list["TaskORM"]] = relationship(
        back_populates="validator_round",
        cascade="all, delete-orphan",
    )
    evaluations: Mapped[list["EvaluationORM"]] = relationship(
        back_populates="validator_round",
        cascade="all, delete-orphan",
    )
    round_summaries: Mapped[list["ValidatorRoundSummaryORM"]] = relationship(
        back_populates="validator_round",
        cascade="all, delete-orphan",
    )

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy JSON payloads."""
        legacy_status = self.status or ""
        if legacy_status == "active":
            legacy_status = "in_progress"
        meta = dict(self.validator_summary or {})
        if self.s3_logs_url:
            meta["s3_logs_url"] = self.s3_logs_url
        return {
            "status": legacy_status,
            "meta": meta,
            "validator_summary": self.validator_summary,
            "s3_logs_url": self.s3_logs_url,
        }


class ValidatorRoundValidatorORM(TimestampMixin, Base):
    """Snapshot of validator information for a given validator_round."""

    __tablename__ = "validator_round_validators"
    __table_args__ = (
        UniqueConstraint(
            "validator_round_id",
            name="uq_round_validator_round_id",  # 1:1 relationship
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # 1:1 relationship
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    validator_coldkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vtrust: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Validator configuration used during this round
    config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True, default=None)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(
        back_populates="validator_snapshot",
        uselist=False,
    )


class ValidatorRoundMinerORM(TimestampMixin, Base):
    """Snapshot of miner information for a given validator_round."""

    __tablename__ = "validator_round_miners"
    __table_args__ = (
        UniqueConstraint(
            "validator_round_id",
            "miner_uid",
            "miner_hotkey",
            name="uq_round_miner_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    miner_coldkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    github_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_sota: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(back_populates="miner_snapshots")


class ValidatorRoundSummaryORM(TimestampMixin, Base):
    """Summary of miner performance in a validator round (local and post-consensus)."""

    __tablename__ = "validator_round_summary_miners"
    __table_args__ = (
        UniqueConstraint(
            "validator_round_id",
            "miner_uid",
            name="uq_round_summary_round_miner",
        ),
        Index("ix_round_summary_miners_round", "validator_round_id"),
        Index("ix_round_summary_miners_miner", "miner_uid"),
        Index("ix_round_summary_miners_consensus_rank", "validator_round_id", "post_consensus_rank"),
        # Index for ORDER BY post_consensus_avg_reward DESC queries (top miner)
        Index("ix_round_summary_miners_consensus_reward", "post_consensus_avg_reward"),
        Index("ix_round_summary_miners_round_reward", "validator_round_id", "post_consensus_avg_reward"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    miner_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    # Local evaluation (pre-consensus, from this validator)
    local_avg_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    local_avg_eval_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    local_avg_eval_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    local_avg_eval_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    local_tasks_received: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    local_tasks_success: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Post-consensus evaluation (aggregated from all validators)
    post_consensus_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    post_consensus_avg_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_consensus_avg_eval_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_consensus_avg_eval_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_consensus_avg_eval_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_consensus_tasks_received: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    post_consensus_tasks_success: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    subnet_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="Subnet price (alpha to TAO rate) at the time of this round")

    validator_round: Mapped["ValidatorRoundORM"] = relationship(back_populates="round_summaries")


# ---------------------------------------------------------------------------
# Agent evaluation runs
# ---------------------------------------------------------------------------


class AgentEvaluationRunORM(TimestampMixin, Base):
    """Execution record for a real agent evaluation within a validator_round."""

    __tablename__ = "miner_evaluation_runs"
    __table_args__ = (
        UniqueConstraint("agent_run_id", name="uq_agent_run_id"),
        Index("ix_agent_run_round", "validator_round_id", "agent_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # validator_uid and validator_hotkey removed - obtain via validator_round.validator_snapshot

    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    # is_sota and version removed - obtain via validator_round.miner_snapshots

    started_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    average_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_execution_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # rank and weight removed - obtain via validator_round_summary_miners
    # Reason for score 0 when applicable (e.g. over_cost_limit, deploy_failed, task_failed)
    zero_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(back_populates="agent_runs")
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(back_populates="agent_run", cascade="all, delete-orphan")
    evaluations: Mapped[list["EvaluationORM"]] = relationship(back_populates="agent_run", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Tasks and solutions
# ---------------------------------------------------------------------------


class TaskORM(TimestampMixin, Base):
    """Task prompt shared with miners in a validator_round."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_task_id"),
        Index("ix_task_round", "validator_round_id", "task_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    web_project_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    web_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    specifications: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tests: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    use_case: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(back_populates="tasks")
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    evaluations: Mapped[list["EvaluationORM"]] = relationship(back_populates="task", cascade="all, delete-orphan")

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy task payloads."""
        return {
            "task_id": self.task_id,
            "validator_round_id": self.validator_round_id,
            "web_project_id": self.web_project_id,
            "web_version": self.web_version,
            "url": self.url,
            "prompt": self.prompt,
            "specifications": dict(self.specifications or {}),
            "tests": list(self.tests or []),
            "use_case": dict(self.use_case or {}),
        }


class TaskSolutionORM(TimestampMixin, Base):
    """Task solution submitted by an agent."""

    __tablename__ = "task_solutions"
    __table_args__ = (
        UniqueConstraint("solution_id", name="uq_solution_id"),
        Index("ix_solution_task", "task_id", "solution_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    solution_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True)
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("miner_evaluation_runs.agent_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    task: Mapped["TaskORM"] = relationship(back_populates="task_solutions")
    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(back_populates="task_solutions")
    evaluations: Mapped[list["EvaluationORM"]] = relationship(back_populates="task_solution", cascade="all, delete-orphan")

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy task solution payloads."""
        return {
            "solution_id": self.solution_id,
            "task_id": self.task_id,
            "agent_run_id": self.agent_run_id,
            "validator_round_id": self.validator_round_id,
            "validator_uid": self.validator_uid,
            "validator_hotkey": self.validator_hotkey,
            "miner_uid": self.miner_uid,
            "miner_hotkey": self.miner_hotkey,
            "actions": list(self.actions or []),
        }


# ---------------------------------------------------------------------------
# Evaluations
# ---------------------------------------------------------------------------


class EvaluationORM(TimestampMixin, Base):
    """Evaluation of a task and its solution with detailed artefacts."""

    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("evaluation_id", name="uq_evaluation_id"),
        Index("ix_evaluation_round", "validator_round_id", "evaluation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("miner_evaluation_runs.agent_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True)
    task_solution_id: Mapped[str] = mapped_column(
        ForeignKey("task_solutions.solution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    evaluation_score: Mapped[float] = mapped_column("evaluation_score", Float, nullable=False, default=0.0)  # Pure evaluation score (tests/actions only, 0-1)
    reward: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # Final task reward used by consensus
    evaluation_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gif_recording: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_info: Mapped[dict[str, Any]] = mapped_column("extra_info", JSON, nullable=False, default=dict)  # e.g. timeout flag; was "meta"
    # Reason for score 0 at evaluation level (e.g. task_timeout, tests_failed)
    zero_reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # LLM usage: only in evaluation_llm_usage table (relationship llm_usage), not duplicated here

    validator_round: Mapped["ValidatorRoundORM"] = relationship(back_populates="evaluations")
    task: Mapped["TaskORM"] = relationship(back_populates="evaluations")
    task_solution: Mapped["TaskSolutionORM"] = relationship(back_populates="evaluations")
    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(back_populates="evaluations")
    execution_history_record: Mapped[Optional["EvaluationExecutionHistoryORM"]] = relationship(
        back_populates="evaluation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    llm_usage: Mapped[list["EvaluationLLMUsageORM"]] = relationship(
        back_populates="evaluation",
        cascade="all, delete-orphan",
    )

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy JSON payloads."""
        out: dict[str, Any] = {
            "evaluation_id": self.evaluation_id,
            "evaluation_score": self.evaluation_score,
            "reward": self.reward,
            "evaluation_time": self.evaluation_time,
            "metadata": dict(self.extra_info or {}),
        }
        if self.zero_reason is not None:
            out["zero_reason"] = self.zero_reason
        return out

    @property
    def execution_history(self) -> list[Any]:
        """Access execution_history from related table (safe for lazy loading)."""
        from sqlalchemy import inspect

        # Check if the relationship is loaded without triggering lazy load
        state = inspect(self)
        if "execution_history_record" in state.unloaded:
            # Relationship not loaded, return empty list without triggering I/O
            return []
        # Relationship is loaded, safe to access
        if self.execution_history_record:
            return self.execution_history_record.execution_history
        return []


class EvaluationExecutionHistoryORM(TimestampMixin, Base):
    """Execution history for evaluations (separate table for performance)."""

    __tablename__ = "evaluations_execution_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.evaluation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_history: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    evaluation: Mapped["EvaluationORM"] = relationship(back_populates="execution_history_record")


class EvaluationLLMUsageORM(TimestampMixin, Base):
    """Per-model/provider LLM usage details for an evaluation."""

    __tablename__ = "evaluation_llm_usage"
    __table_args__ = (Index("ix_eval_llm_usage_eval_id", "evaluation_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.evaluation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    evaluation: Mapped["EvaluationORM"] = relationship(back_populates="llm_usage")


# ---------------------------------------------------------------------------
# Task execution logs (backoffice)
# ---------------------------------------------------------------------------


class TaskExecutionLogORM(TimestampMixin, Base):
    """S3-backed execution log metadata for a task/agent_run pair."""

    __tablename__ = "task_execution_logs"
    __table_args__ = (
        UniqueConstraint("task_id", "agent_run_id", name="uq_task_execution_log"),
        Index("ix_task_execution_logs_round", "validator_round_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("miner_evaluation_runs.agent_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    season: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    round_in_season: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    payload_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Optimization tables (Materialized Views)
# ---------------------------------------------------------------------------


__all__ = [
    "ValidatorRoundORM",
    "ValidatorRoundValidatorORM",
    "ValidatorRoundMinerORM",
    "AgentEvaluationRunORM",
    "TaskORM",
    "TaskSolutionORM",
    "EvaluationORM",
    "EvaluationExecutionHistoryORM",
    "EvaluationLLMUsageORM",
    "TaskExecutionLogORM",
    "RoundORM",
]

# Backwards compatibility aliases (must be after __all__ for export)
RoundORM = ValidatorRoundORM
