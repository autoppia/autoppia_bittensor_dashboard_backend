from __future__ import annotations

from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ui.miner_list import (
    MinerDetail,
    MinerDetailResponse,
    MinerListItem,
    MinerListResponse as MinimalMinerListResponse,
)
from app.services.miners_service import MinersService


class MinerListService:
    """Minimal miner list/details service backed by SQL aggregates."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.miners_service = MinersService(session)

    async def list_miners(
        self,
        page: int,
        limit: int,
        is_sota: bool | None = None,
        search: str | None = None,
    ) -> MinimalMinerListResponse:
        # Reuse the full miner aggregation and then project to list items.
        full = await self.miners_service.list_miners(
            page=page,
            limit=limit,
            is_sota=is_sota,
            status=None,
            sort_by="averageScore",
            sort_order="desc",
            search=search,
        )

        start_rank = (page - 1) * limit
        items: List[MinerListItem] = []
        for index, miner in enumerate(full.miners):
            items.append(
                MinerListItem(
                    uid=miner.uid,
                    name=miner.name,
                    ranking=start_rank + index + 1,
                    score=miner.averageScore,
                    isSota=miner.isSota,
                    imageUrl=miner.imageUrl,
                )
            )

        return MinimalMinerListResponse(
            miners=items,
            total=full.pagination.total,
            page=full.pagination.page,
            limit=full.pagination.limit,
        )

    async def get_miner_detail(self, uid: int) -> MinerDetailResponse:
        full_detail = await self.miners_service.get_miner(uid)
        miner = full_detail.miner

        detail = MinerDetail(
            uid=miner.uid,
            name=miner.name,
            hotkey=miner.hotkey,
            imageUrl=miner.imageUrl,
            githubUrl=miner.githubUrl,
            taostatsUrl=miner.taostatsUrl,
            isSota=miner.isSota,
            status=miner.status.value if hasattr(miner.status, "value") else str(miner.status),
            description=miner.description,
            totalRuns=miner.totalRuns,
            successfulRuns=miner.successfulRuns,
            averageScore=miner.averageScore,
            bestScore=miner.bestScore,
            successRate=miner.successRate,
            averageDuration=miner.averageDuration,
            totalTasks=miner.totalTasks,
            completedTasks=miner.completedTasks,
            lastSeen=miner.lastSeen,
            createdAt=miner.createdAt,
            updatedAt=miner.updatedAt,
        )

        return MinerDetailResponse(miner=detail)
