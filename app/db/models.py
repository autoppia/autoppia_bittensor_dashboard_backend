from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

# Always use PostgreSQL JSONB (PostgreSQL is required)
try:
    from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB  # type: ignore
except ImportError:
    raise ImportError("PostgreSQL dialect not available - install asyncpg")

from app.config import settings as _settings


def _select_json_type():
    """Always return JSONB for PostgreSQL."""
    return _PG_JSONB


JSON = _select_json_type()
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    """Reusable timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Identity tables
# ---------------------------------------------------------------------------


class ValidatorORM(TimestampMixin, Base):
    """Immutable validator identity."""

    __tablename__ = "validators"
    __table_args__ = (
        UniqueConstraint("uid", "hotkey", name="uq_validator_uid_hotkey"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    hotkey: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    coldkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    validator_rounds: Mapped[list["ValidatorRoundORM"]] = relationship(
        back_populates="validator",
        cascade="all, delete-orphan",
    )
    snapshots: Mapped[list["ValidatorRoundValidatorORM"]] = relationship(
        back_populates="validator",
        cascade="all, delete-orphan",
    )
    agent_runs: Mapped[list["AgentEvaluationRunORM"]] = relationship(
        back_populates="validator",
        cascade="all, delete-orphan",
    )


class MinerORM(TimestampMixin, Base):
    """Immutable miner identity."""

    __tablename__ = "miners"
    __table_args__ = (UniqueConstraint("uid", "hotkey", name="uq_miner_uid_hotkey"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    hotkey: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    coldkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    snapshots: Mapped[list["ValidatorRoundMinerORM"]] = relationship(
        back_populates="miner",
        cascade="all, delete-orphan",
    )
    agent_runs: Mapped[list["AgentEvaluationRunORM"]] = relationship(
        back_populates="miner",
        cascade="all, delete-orphan",
    )
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(
        back_populates="miner",
        cascade="all, delete-orphan",
    )
    evaluations: Mapped[list["EvaluationORM"]] = relationship(
        back_populates="miner",
        cascade="all, delete-orphan",
    )
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="miner",
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# Validator rounds and snapshots
# ---------------------------------------------------------------------------


class ValidatorRoundORM(TimestampMixin, Base):
    """Canonical representation of a validator_round."""

    __tablename__ = "validator_rounds"
    __table_args__ = (
        UniqueConstraint(
            "validator_uid",
            "round_number",
            name="uq_validator_round_uid_number",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True
    )
    validator_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("validators.id", ondelete="SET NULL"), nullable=True
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    validator_coldkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    round_number: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )

    start_block: Mapped[int] = mapped_column(Integer, nullable=False)
    end_block: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    end_epoch: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    started_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    max_epochs: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    max_blocks: Mapped[int] = mapped_column(Integer, nullable=False, default=360)
    n_tasks: Mapped[int] = mapped_column(Integer, nullable=False)
    n_miners: Mapped[int] = mapped_column(Integer, nullable=False)
    n_winners: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    average_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    top_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    validator: Mapped[Optional[ValidatorORM]] = relationship(
        back_populates="validator_rounds"
    )
    validator_snapshots: Mapped[list["ValidatorRoundValidatorORM"]] = relationship(
        back_populates="validator_round",
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
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="validator_round",
        cascade="all, delete-orphan",
    )

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy JSON payloads."""
        legacy_status = self.status or ""
        if legacy_status == "active":
            legacy_status = "in_progress"
        return {
            "status": legacy_status,
            "summary": dict(self.summary or {}),
            "meta": dict(self.meta or {}),
        }


class ValidatorRoundValidatorORM(TimestampMixin, Base):
    """Snapshot of validator information for a given validator_round."""

    __tablename__ = "validator_round_validators"
    __table_args__ = (
        UniqueConstraint(
            "validator_round_id",
            "validator_uid",
            "validator_hotkey",
            name="uq_round_validator_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
    )
    validator_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("validators.id", ondelete="SET NULL"), nullable=True
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vtrust: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(
        back_populates="validator_snapshots"
    )
    validator: Mapped[Optional[ValidatorORM]] = relationship(back_populates="snapshots")


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
    miner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("miners.id", ondelete="SET NULL"), nullable=True
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    miner_coldkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    agent_name: Mapped[str] = mapped_column(String(256), nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    github_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_sota: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_seen_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(
        back_populates="miner_snapshots"
    )
    miner: Mapped[Optional[MinerORM]] = relationship(back_populates="snapshots")


# ---------------------------------------------------------------------------
# Agent evaluation runs
# ---------------------------------------------------------------------------


class AgentEvaluationRunORM(TimestampMixin, Base):
    """Execution record for an agent within a validator_round."""

    __tablename__ = "agent_evaluation_runs"
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
    validator_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("validators.id", ondelete="SET NULL"), nullable=True
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    miner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("miners.id", ondelete="SET NULL"), nullable=True
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )

    is_sota: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    started_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ended_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    average_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_execution_time: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    average_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(
        back_populates="agent_runs"
    )
    validator: Mapped[Optional[ValidatorORM]] = relationship(
        back_populates="agent_runs"
    )
    miner: Mapped[Optional[MinerORM]] = relationship(back_populates="agent_runs")
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list["EvaluationORM"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )


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
    is_web_real: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    web_project_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    specifications: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    tests: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    relevant_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    use_case: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(back_populates="tasks")
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    evaluations: Mapped[list["EvaluationORM"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy task payloads."""
        return {
            "task_id": self.task_id,
            "validator_round_id": self.validator_round_id,
            "is_web_real": self.is_web_real,
            "web_project_id": self.web_project_id,
            "url": self.url,
            "prompt": self.prompt,
            "specifications": dict(self.specifications or {}),
            "tests": list(self.tests or []),
            "relevant_data": dict(self.relevant_data or {}),
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
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_evaluation_runs.agent_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    miner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("miners.id", ondelete="SET NULL"), nullable=True
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )

    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    web_agent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    task: Mapped["TaskORM"] = relationship(back_populates="task_solutions")
    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(
        back_populates="task_solutions"
    )
    miner: Mapped[Optional[MinerORM]] = relationship(back_populates="task_solutions")
    evaluations: Mapped[list["EvaluationORM"]] = relationship(
        back_populates="task_solution", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="task_solution", cascade="all, delete-orphan"
    )

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
            "web_agent_id": self.web_agent_id,
        }


# ---------------------------------------------------------------------------
# Evaluations and results
# ---------------------------------------------------------------------------


class EvaluationORM(TimestampMixin, Base):
    """Evaluation record linking a task, solution, and agent run."""

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
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_solution_id: Mapped[str] = mapped_column(
        ForeignKey("task_solutions.solution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_evaluation_runs.agent_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    validator_hotkey: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )

    miner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("miners.id", ondelete="SET NULL"), nullable=True
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    miner_hotkey: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )

    final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evaluation_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    validator_round: Mapped["ValidatorRoundORM"] = relationship(
        back_populates="evaluations"
    )
    task: Mapped["TaskORM"] = relationship(back_populates="evaluations")
    task_solution: Mapped["TaskSolutionORM"] = relationship(
        back_populates="evaluations"
    )
    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(
        back_populates="evaluations"
    )
    miner: Mapped[Optional[MinerORM]] = relationship(back_populates="evaluations")
    results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )


class EvaluationResultORM(TimestampMixin, Base):
    """Detailed artefact produced for an evaluation."""

    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("result_id", name="uq_evaluation_result_id"),
        Index("ix_eval_result_round", "validator_round_id", "result_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.evaluation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    validator_round_id: Mapped[str] = mapped_column(
        ForeignKey("validator_rounds.validator_round_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_evaluation_runs.agent_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_solution_id: Mapped[str] = mapped_column(
        ForeignKey("task_solutions.solution_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    miner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("miners.id", ondelete="SET NULL"), nullable=True
    )
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    validator_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    final_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    test_results_matrix: Mapped[list[list[dict[str, Any]]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    execution_history: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    feedback: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    web_agent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evaluation_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stats: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    gif_recording: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    evaluation: Mapped["EvaluationORM"] = relationship(back_populates="results")

    @property
    def data(self) -> dict[str, Any]:
        """Backwards-compatible accessor for legacy JSON payloads."""
        return {
            "result_id": self.result_id,
            "evaluation_id": self.evaluation_id,
            "final_score": self.final_score,
            "raw_score": self.raw_score,
            "evaluation_time": self.evaluation_time,
            "meta": dict(self.meta or {}),
        }

    validator_round: Mapped["ValidatorRoundORM"] = relationship(
        back_populates="evaluation_results"
    )
    task: Mapped["TaskORM"] = relationship(back_populates="evaluation_results")
    task_solution: Mapped["TaskSolutionORM"] = relationship(
        back_populates="evaluation_results"
    )
    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(
        back_populates="evaluation_results"
    )
    miner: Mapped[Optional[MinerORM]] = relationship(
        back_populates="evaluation_results"
    )


# ---------------------------------------------------------------------------
# Optimization tables (Materialized Views)
# ---------------------------------------------------------------------------


class RoundSnapshotORM(TimestampMixin, Base):
    __tablename__ = "round_snapshots"
    round_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    data_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        CheckConstraint("round_number > 0", name="ck_round_snapshots_positive_number"),
    )


class AgentStatsORM(TimestampMixin, Base):
    __tablename__ = "agent_stats"
    uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotkey: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_sota: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    best_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    worst_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    successful_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    best_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    recent_rounds: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    first_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_round_number: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )
    __table_args__ = (
        CheckConstraint("total_rounds >= 0", name="ck_agent_stats_positive_rounds"),
        CheckConstraint("total_runs >= 0", name="ck_agent_stats_positive_runs"),
        CheckConstraint(
            "avg_score >= 0.0 AND avg_score <= 1.0", name="ck_agent_stats_score_range"
        ),
        CheckConstraint(
            "best_score >= 0.0 AND best_score <= 1.0",
            name="ck_agent_stats_best_score_range",
        ),
        Index("idx_agent_stats_avg_score", "avg_score", postgresql_using="btree"),
    )


class MinerAggregatesMV(Base):
    """
    Read-only ORM mapping for materialized view `miner_aggregates_mv`.

    This view is maintained in PostgreSQL and provides pre-aggregated miner
    statistics for fast leaderboard-style queries.
    """

    __tablename__ = "miner_aggregates_mv"

    # Core identity / display fields
    uid: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    hotkey: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_sota: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    image_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    github_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Aggregated performance metrics
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    best_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_response_time: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Global ranking / latest round snapshot
    current_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latest_round_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latest_round_score: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    # Activity / status metadata
    last_seen: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="inactive")

    # JSONB with per-round aggregates:
    # {
    #   "<round_number>": { "avgScore": float, "rank": int, "totalRuns": int },
    #   ...
    # }
    rounds: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


__all__ = [
    "RoundSnapshotORM",
    "AgentStatsORM",
    "ValidatorORM",
    "MinerORM",
    "ValidatorRoundORM",
    "ValidatorRoundValidatorORM",
    "ValidatorRoundMinerORM",
    "AgentEvaluationRunORM",
    "TaskORM",
    "TaskSolutionORM",
    "EvaluationORM",
    "EvaluationResultORM",
    "RoundORM",
    "MinerAggregatesMV",
]

# Backwards compatibility alias
RoundORM = ValidatorRoundORM
