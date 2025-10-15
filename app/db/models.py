from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    """Reusable timestamp columns."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class RoundORM(TimestampMixin, Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validator_round_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    validator_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    agent_runs: Mapped[list["AgentEvaluationRunORM"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )


class AgentEvaluationRunORM(TimestampMixin, Base):
    __tablename__ = "agent_evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    validator_round_id: Mapped[str] = mapped_column(String(128), index=True)
    validator_uid: Mapped[int] = mapped_column(Integer)
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_sota: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    round: Mapped["RoundORM"] = relationship(back_populates="agent_runs")
    tasks: Mapped[list["TaskORM"]] = relationship(back_populates="agent_run", cascade="all, delete-orphan")
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )


class TaskORM(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    validator_round_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_run_id: Mapped[Optional[str]] = mapped_column(
        String(128), ForeignKey("agent_evaluation_runs.agent_run_id", ondelete="SET NULL"), nullable=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    agent_run: Mapped[Optional["AgentEvaluationRunORM"]] = relationship(back_populates="tasks")
    task_solutions: Mapped[list["TaskSolutionORM"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskSolutionORM(TimestampMixin, Base):
    __tablename__ = "task_solutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    solution_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(String(128), ForeignKey("tasks.task_id", ondelete="CASCADE"))
    agent_run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agent_evaluation_runs.agent_run_id", ondelete="CASCADE")
    )
    validator_round_id: Mapped[str] = mapped_column(String(128), index=True)
    validator_uid: Mapped[int] = mapped_column(Integer)
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(back_populates="task_solutions")
    task: Mapped["TaskORM"] = relationship(back_populates="task_solutions")
    evaluation_results: Mapped[list["EvaluationResultORM"]] = relationship(
        back_populates="task_solution", cascade="all, delete-orphan"
    )


class EvaluationResultORM(TimestampMixin, Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    task_id: Mapped[str] = mapped_column(String(128), ForeignKey("tasks.task_id", ondelete="CASCADE"))
    task_solution_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("task_solutions.solution_id", ondelete="CASCADE")
    )
    agent_run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("agent_evaluation_runs.agent_run_id", ondelete="CASCADE")
    )
    validator_round_id: Mapped[str] = mapped_column(String(128), index=True)
    validator_uid: Mapped[int] = mapped_column(Integer)
    miner_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    agent_run: Mapped["AgentEvaluationRunORM"] = relationship(back_populates="evaluation_results")
    task_solution: Mapped["TaskSolutionORM"] = relationship(back_populates="evaluation_results")
    task: Mapped["TaskORM"] = relationship(back_populates="evaluation_results")


__all__ = [
    "RoundORM",
    "AgentEvaluationRunORM",
    "TaskORM",
    "TaskSolutionORM",
    "EvaluationResultORM",
]
