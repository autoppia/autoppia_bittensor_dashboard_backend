from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AgentEvaluationRunORM, RoundORM
from app.models.core import AgentEvaluationRun, MinerInfo, Round
from app.models.ui.subnets import (
    MinerRosterEntry,
    MinerSnapshot,
    SubnetTimelineResponse,
    TimelineMeta,
    TimelineMetaQuery,
    TimelineRound,
)
from app.services.rounds_service import AgentRunContext, RoundsService

DEFAULT_ROUND_COUNT = 90
MAX_ROUND_COUNT = 500
DEFAULT_ROSTER_SIZE = 8
MAX_ROSTER_SIZE = 32
FALLBACK_ROUND_DURATION = 60

COLOR_PALETTE = [
    "#4F46E5",
    "#7C3AED",
    "#0EA5E9",
    "#10B981",
    "#F97316",
    "#EF4444",
    "#14B8A6",
    "#F59E0B",
    "#8B5CF6",
    "#3B82F6",
]


@dataclass(slots=True)
class _RoundSnapshot:
    number: int
    identifier: str
    round_model: Round
    agent_contexts: List[AgentRunContext]

    @property
    def timestamp(self) -> float:
        reference = self.round_model.ended_at or self.round_model.started_at
        if reference is None:
            return datetime.now(timezone.utc).timestamp()
        return reference

    @property
    def duration(self) -> Optional[float]:
        if self.round_model.started_at is None or self.round_model.ended_at is None:
            return None
        return max(self.round_model.ended_at - self.round_model.started_at, 0.0)


def _round_number(validator_round_id: str) -> Optional[int]:
    if "_" in validator_round_id:
        _, suffix = validator_round_id.split("_", 1)
    else:
        suffix = validator_round_id
    try:
        return int(suffix)
    except ValueError:
        return None


def _iso_timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat()


def _score_from_run(context: AgentRunContext) -> float:
    """Return the average evaluation score for an agent run (0-1 range)."""
    if context.evaluation_results:
        return sum(result.final_score for result in context.evaluation_results) / len(
            context.evaluation_results
        )
    if context.run.avg_eval_score is not None:
        return context.run.avg_eval_score
    return 0.0


def _miner_identifier(run: AgentEvaluationRun) -> str:
    if run.miner_uid is not None:
        return f"miner-{run.miner_uid}"
    return run.agent_run_id


def _miner_display_name(run: AgentEvaluationRun) -> str:
    info = run.miner_info
    if info and info.agent_name:
        return info.agent_name
    if run.miner_uid is not None:
        return f"Miner {run.miner_uid}"
    return run.agent_run_id


def _miner_avatar(info: Optional[MinerInfo], display_name: str, identifier: str) -> str:
    if info and info.agent_image:
        return info.agent_image
    safe = display_name.replace(" ", "+")
    return f"https://placehold.co/256x256?text={safe or identifier}"


class SubnetTimelineService:
    """Build subnet timeline responses sourced entirely from persisted round data."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    async def build_timeline(
        self,
        subnet_id: str,
        *,
        rounds: Optional[int] = None,
        end_round: Optional[int] = None,
        seconds_back: Optional[int] = None,
        miners: Optional[int] = None,
    ) -> SubnetTimelineResponse:
        available_rounds = await self._available_rounds()
        if not available_rounds:
            raise ValueError("No rounds available for timeline generation")

        target_end = self._resolve_end_round(end_round, available_rounds)
        requested_rounds = self._resolve_round_count(
            rounds=rounds,
            seconds_back=seconds_back,
            available=len(available_rounds),
        )

        selected_ids = self._select_round_ids(
            available_rounds,
            end_round_number=target_end,
            count=requested_rounds,
        )
        if not selected_ids:
            raise ValueError("No rounds matched the requested window")

        round_snapshots = await self._load_round_snapshots(selected_ids)
        if not round_snapshots:
            raise ValueError("No persisted round snapshots available")

        roster_size = self._resolve_roster_size(miners, len(round_snapshots))

        timeline, roster = self._build_timeline(round_snapshots, roster_size)

        durations = [snapshot.duration for snapshot in round_snapshots if snapshot.duration]
        average_duration = int(round(mean(durations))) if durations else FALLBACK_ROUND_DURATION

        meta = TimelineMeta(
            subnet_id=subnet_id,
            start_round=round_snapshots[0].number,
            end_round=round_snapshots[-1].number,
            round_count=len(timeline),
            round_duration_seconds=max(1, average_duration),
            generated_at=datetime.now(timezone.utc).isoformat(),
            query=TimelineMetaQuery(
                rounds=rounds,
                end_round=end_round,
                seconds_back=seconds_back,
                miners=miners,
            ),
            inferred_round_count=len(selected_ids),
        )

        return SubnetTimelineResponse(
            subnet_id=subnet_id,
            roster=roster,
            timeline=timeline,
            meta=meta,
        )

    async def _available_rounds(self) -> List[Tuple[int, str]]:
        stmt = select(RoundORM.validator_round_id)
        results = await self.session.scalars(stmt)
        entries: List[Tuple[int, str]] = []
        for identifier in results:
            number = _round_number(identifier)
            if number is not None:
                entries.append((number, identifier))
        entries.sort(key=lambda item: item[0])
        return entries

    @staticmethod
    def _resolve_end_round(
        end_round: Optional[int],
        available_rounds: Sequence[Tuple[int, str]],
    ) -> int:
        max_available = available_rounds[-1][0]
        if end_round is None:
            return max_available
        return min(max(end_round, 1), max_available)

    @staticmethod
    def _resolve_round_count(
        *,
        rounds: Optional[int],
        seconds_back: Optional[int],
        available: int,
    ) -> int:
        if rounds is not None:
            count = max(1, min(rounds, MAX_ROUND_COUNT))
        elif seconds_back is not None:
            inferred = max(1, seconds_back // FALLBACK_ROUND_DURATION)
            count = min(inferred, MAX_ROUND_COUNT)
        else:
            count = DEFAULT_ROUND_COUNT
        return min(count, available)

    @staticmethod
    def _select_round_ids(
        rounds: Sequence[Tuple[int, str]],
        *,
        end_round_number: int,
        count: int,
    ) -> List[Tuple[int, str]]:
        eligible = [entry for entry in rounds if entry[0] <= end_round_number]
        if not eligible:
            return []
        return eligible[-count:]

    async def _load_round_snapshots(
        self,
        selected: Sequence[Tuple[int, str]],
    ) -> List[_RoundSnapshot]:
        identifiers = [identifier for _, identifier in selected]
        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.agent_runs)
                .selectinload(AgentEvaluationRunORM.tasks),
                selectinload(RoundORM.agent_runs)
                .selectinload(AgentEvaluationRunORM.task_solutions),
                selectinload(RoundORM.agent_runs)
                .selectinload(AgentEvaluationRunORM.evaluation_results),
            )
            .where(RoundORM.validator_round_id.in_(identifiers))
        )
        rows = await self.session.scalars(stmt)
        by_identifier: Dict[str, RoundORM] = {row.validator_round_id: row for row in rows}

        snapshots: List[_RoundSnapshot] = []
        for number, identifier in selected:
            round_row = by_identifier.get(identifier)
            if round_row is None:
                continue
            round_model = self.rounds_service._deserialize_round(round_row)  # type: ignore[attr-defined]
            agent_contexts = [
                self.rounds_service._build_agent_run_context(run_row, parent_round_row=round_row)  # type: ignore[attr-defined]
                for run_row in round_row.agent_runs
            ]
            snapshots.append(
                _RoundSnapshot(
                    number=number,
                    identifier=identifier,
                    round_model=round_model,
                    agent_contexts=agent_contexts,
                )
            )
        snapshots.sort(key=lambda snapshot: snapshot.number)
        return snapshots

    @staticmethod
    def _resolve_roster_size(
        miners: Optional[int],
        available_snapshots: int,
    ) -> int:
        requested = miners if miners is not None else DEFAULT_ROSTER_SIZE
        return max(1, min(requested, MAX_ROSTER_SIZE))

    def _build_timeline(
        self,
        round_snapshots: Sequence[_RoundSnapshot],
        roster_size: int,
    ) -> Tuple[List[TimelineRound], List[MinerRosterEntry]]:
        miner_stats: Dict[str, Dict[str, object]] = defaultdict(
            lambda: {
                "display_name": "",
                "image": "",
                "scores": [],
                "info": None,
            }
        )

        timeline: List[TimelineRound] = []
        previous_snapshots: Dict[str, MinerSnapshot] = {}

        for snapshot in round_snapshots:
            run_scores: List[Tuple[AgentRunContext, float]] = []
            for context in snapshot.agent_contexts:
                score = max(0.0, min(1.0, _score_from_run(context)))
                run_scores.append((context, score))

                identifier = _miner_identifier(context.run)
                metrics = miner_stats[identifier]
                metrics["display_name"] = _miner_display_name(context.run)
                metrics["info"] = context.run.miner_info
                metrics["scores"].append(score)
                if context.run.miner_info and context.run.miner_info.agent_image:
                    metrics["image"] = context.run.miner_info.agent_image

            if not run_scores:
                continue

            run_scores.sort(
                key=lambda item: (
                    -item[1],
                    item[0].run.miner_uid if item[0].run.miner_uid is not None else float("inf"),
                    item[0].run.agent_run_id,
                )
            )

            snapshots: List[MinerSnapshot] = []
            for rank, (context, score) in enumerate(run_scores, start=1):
                identifier = _miner_identifier(context.run)
                percent_score = round(score * 100, 2)
                previous = previous_snapshots.get(identifier)
                if previous:
                    score_change = round(percent_score - previous.score, 2)
                    rank_change = previous.rank - rank
                    previous_rank = previous.rank
                else:
                    score_change = 0.0
                    rank_change = 0
                    previous_rank = None

                snapshot_model = MinerSnapshot(
                    miner_id=identifier,
                    score=percent_score,
                    rank=rank,
                    rank_change=rank_change,
                    score_change=score_change,
                    previous_rank=previous_rank,
                )
                snapshots.append(snapshot_model)
                previous_snapshots[identifier] = snapshot_model

            timeline.append(
                TimelineRound(
                    round=snapshot.number,
                    timestamp=_iso_timestamp(snapshot.timestamp),
                    snapshots=snapshots,
                )
            )

        roster = self._build_roster(miner_stats, roster_size)
        return timeline, roster

    def _build_roster(
        self,
        miner_stats: Dict[str, Dict[str, object]],
        roster_size: int,
    ) -> List[MinerRosterEntry]:
        aggregates: List[Tuple[float, str, Dict[str, object]]] = []
        for identifier, metrics in miner_stats.items():
            scores: Iterable[float] = metrics["scores"]  # type: ignore[assignment]
            if not scores:
                continue
            average_score = mean(scores)
            aggregates.append((average_score, identifier, metrics))

        aggregates.sort(key=lambda item: item[0], reverse=True)
        selected = aggregates[:roster_size]

        roster: List[MinerRosterEntry] = []
        for index, (avg_score, identifier, metrics) in enumerate(selected):
            info: Optional[MinerInfo] = metrics["info"]  # type: ignore[assignment]
            display_name = metrics["display_name"] or (info.agent_name if info else identifier)  # type: ignore[assignment]
            image = metrics["image"]  # type: ignore[assignment]
            avatar_url = image if image else _miner_avatar(info, display_name, identifier)
            color = COLOR_PALETTE[index % len(COLOR_PALETTE)]

            roster.append(
                MinerRosterEntry(
                    miner_id=identifier,
                    display_name=display_name,
                    color_hex=color,
                    avatar_url=avatar_url,
                    order=index,
                )
            )
        return roster
