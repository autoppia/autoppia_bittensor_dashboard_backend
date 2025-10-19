from __future__ import annotations

from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ui.miner_list import (
    MinerDetail,
    MinerDetailResponse,
    MinerListItem,
    MinerListResponse as MinimalMinerListResponse,
)
from app.services.miners_service import MinersService
from app.utils.images import resolve_agent_image


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
        round_number: Optional[int] = None,
    ) -> MinimalMinerListResponse:
        if round_number is not None and round_number > 0:
            snapshots = await self.miners_service.agents_service.build_round_snapshots(round_number)
            items: List[MinerListItem] = []

            for snapshot in snapshots:
                aggregate = snapshot.aggregate

                if is_sota is not None and aggregate.is_sota != is_sota:
                    continue

                miner_info = aggregate.miner
                name = (
                    miner_info.agent_name
                    if miner_info and miner_info.agent_name
                    else aggregate.agent_id
                )
                hotkey = miner_info.hotkey if miner_info else ""
                uid_value = aggregate.uid if aggregate.uid is not None else -1

                if search:
                    lowered = search.lower()
                    if (
                        lowered not in name.lower()
                        and lowered not in hotkey.lower()
                        and lowered not in aggregate.agent_id.lower()
                        and lowered not in str(uid_value)
                    ):
                        continue

                image_url = resolve_agent_image(miner_info)
                items.append(
                    MinerListItem(
                        uid=uid_value,
                        name=name,
                        ranking=snapshot.rank,
                        score=snapshot.average_score,
                        isSota=aggregate.is_sota,
                        imageUrl=image_url,
                    )
                )

            total = len(items)
            start = (page - 1) * limit
            end = start + limit
            paginated = items[start:end]

            return MinimalMinerListResponse(
                miners=paginated,
                total=total,
                page=page,
                limit=limit,
            )

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
            averageResponseTime=miner.averageResponseTime,
            totalTasks=miner.totalTasks,
            completedTasks=miner.completedTasks,
            lastSeen=miner.lastSeen,
            createdAt=miner.createdAt,
            updatedAt=miner.updatedAt,
        )

        return MinerDetailResponse(miner=detail)
