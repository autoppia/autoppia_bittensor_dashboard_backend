from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ui.miners import (
    Miner,
    MinerDetailResponse,
    MinerListResponse,
    MinerStatus,
)
from app.models.ui.miners import Pagination
from app.services.agents_service import AgentsService, AgentAggregate

logger = logging.getLogger(__name__)


def _ts_to_iso(ts: Optional[float]) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()


class MinersService:
    """SQL-backed service for miners."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.agents_service = AgentsService(session)

    async def list_miners(
        self,
        page: int,
        limit: int,
        is_sota: Optional[bool] = None,
        status: Optional[MinerStatus] = None,
        sort_by: str = "averageScore",
        sort_order: str = "desc",
        search: Optional[str] = None,
    ) -> MinerListResponse:
        aggregates = await self.agents_service._aggregate_agents()  # type: ignore[attr-defined]
        miners = [self._aggregate_to_miner(agg) for agg in aggregates.values()]

        if is_sota is not None:
            miners = [miner for miner in miners if miner.isSota == is_sota]

        if status:
            miners = [miner for miner in miners if miner.status == status]

        if search:
            lowered = search.lower()
            miners = [
                miner
                for miner in miners
                if lowered in miner.name.lower()
                or lowered in miner.hotkey.lower()
                or lowered in miner.id.lower()
            ]

        miners = self._sort_miners(miners, sort_by, sort_order)

        total = len(miners)
        start = (page - 1) * limit
        end = start + limit
        paginated = miners[start:end]
        total_pages = max(1, (total + limit - 1) // limit)

        pagination = Pagination(page=page, limit=limit, total=total, totalPages=total_pages)
        return MinerListResponse(miners=paginated, pagination=pagination)

    async def get_miner(self, uid: int) -> MinerDetailResponse:
        aggregates = await self.agents_service._aggregate_agents()  # type: ignore[attr-defined]
        for aggregate in aggregates.values():
            if aggregate.uid == uid:
                return MinerDetailResponse(miner=self._aggregate_to_miner(aggregate))
        raise ValueError(f"Miner {uid} not found")

    def _aggregate_to_miner(self, aggregate: AgentAggregate) -> Miner:
        miner_info = aggregate.miner
        name = miner_info.agent_name if miner_info and miner_info.agent_name else aggregate.agent_id
        hotkey = miner_info.hotkey if miner_info and miner_info.hotkey else ""
        image_url = miner_info.agent_image if miner_info and miner_info.agent_image else ""
        github = miner_info.github if miner_info else None
        description = miner_info.description if miner_info else ""
        average_score = (
            aggregate.total_score / aggregate.total_runs if aggregate.total_runs else 0.0
        )
        success_rate = (
            (aggregate.successful_runs / aggregate.total_runs) * 100 if aggregate.total_runs else 0.0
        )
        best_score = aggregate.best_score
        average_duration = (
            sum(aggregate.durations) / len(aggregate.durations) if aggregate.durations else 0.0
        )

        last_seen_iso = _ts_to_iso(aggregate.last_seen)
        created_iso = _ts_to_iso(
            aggregate.first_seen if aggregate.first_seen != float("inf") else aggregate.last_seen
        )

        return Miner(
            id=str(aggregate.uid) if aggregate.uid is not None else aggregate.agent_id,
            uid=aggregate.uid or -1,
            name=name,
            hotkey=hotkey,
            imageUrl=image_url,
            githubUrl=github,
            taostatsUrl=f"https://taostats.io/miner/{aggregate.uid}" if aggregate.uid is not None else "",
            isSota=aggregate.is_sota,
            status=MinerStatus.ACTIVE,
            description=description,
            totalRuns=aggregate.total_runs,
            successfulRuns=aggregate.successful_runs,
            averageScore=average_score,
            bestScore=best_score,
            successRate=success_rate,
            averageDuration=average_duration,
            totalTasks=aggregate.total_tasks,
            completedTasks=aggregate.completed_tasks,
            lastSeen=last_seen_iso,
            createdAt=created_iso,
            updatedAt=last_seen_iso,
        )

    def _sort_miners(self, miners: List[Miner], sort_by: str, sort_order: str) -> List[Miner]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(miners, key=lambda miner: getattr(miner, sort_by), reverse=reverse)
        except AttributeError:
            return miners
