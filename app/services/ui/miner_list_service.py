from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ui.miner_list import (
    MinerDetail,
    MinerDetailResponse,
    MinerListItem,
    MinerListResponse as MinimalMinerListResponse,
)
from app.services.ui.agents_service import AgentAggregate, RoundAgentSnapshot
from app.services.ui.miners_service import MinersService
from app.utils.images import resolve_agent_image
from app.services.service_utils import rollback_on_error


class MinerListService:
    """Minimal miner list/details service backed by SQL aggregates."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.miners_service = MinersService(session)

    @rollback_on_error
    async def list_miners(
        self,
        page: int,
        limit: int,
        is_sota: bool | None = None,
        search: str | None = None,
        round_number: Optional[int] = None,
    ) -> MinimalMinerListResponse:
        aggregates = await self.miners_service.agents_service._aggregate_agents()  # type: ignore[attr-defined]
        round_candidates = self._collect_round_candidates(aggregates)
        snapshot_cache: Dict[int, List[RoundAgentSnapshot]] = {}

        async def load_snapshots(candidate_round: int) -> List[RoundAgentSnapshot]:
            if candidate_round in snapshot_cache:
                return snapshot_cache[candidate_round]
            snapshots = await self.miners_service.agents_service.build_round_snapshots(
                candidate_round,
                aggregates,
            )
            snapshot_cache[candidate_round] = snapshots
            return snapshots

        def has_real_miners(snapshots: Sequence[RoundAgentSnapshot]) -> bool:
            return any(
                snapshot.aggregate.uid is not None and not snapshot.aggregate.is_sota
                for snapshot in snapshots
            )

        async def resolve_round(preferred_rounds: Iterable[int]) -> Tuple[Optional[int], List[RoundAgentSnapshot]]:
            tried: List[int] = []
            for candidate in preferred_rounds:
                tried.append(candidate)
                snapshots = await load_snapshots(candidate)
                if snapshots and has_real_miners(snapshots):
                    return candidate, snapshots
            for candidate in tried:
                snapshots = await load_snapshots(candidate)
                if snapshots:
                    return candidate, snapshots
            return None, []

        preferred_rounds: List[int]
        if round_number is not None and round_number > 0:
            preferred_rounds = [round_number]
            preferred_rounds.extend(
                candidate
                for candidate in round_candidates
                if candidate < round_number and candidate not in preferred_rounds
            )
        else:
            preferred_rounds = round_candidates

        resolved_round, snapshots = await resolve_round(preferred_rounds)

        if resolved_round is not None and snapshots:
            items = self._build_items_from_snapshots(
                snapshots=snapshots,
                is_sota=is_sota,
                search=search,
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
                round=resolved_round,
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
            round=None,
        )

    @rollback_on_error
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

    @staticmethod
    def _collect_round_candidates(aggregates: Dict[str, AgentAggregate]) -> List[int]:
        rounds: set[int] = set()
        for aggregate in aggregates.values():
            for round_id in getattr(aggregate, "rounds", set()):
                if isinstance(round_id, int) and round_id > 0:
                    rounds.add(round_id)
        return sorted(rounds, reverse=True)

    def _build_items_from_snapshots(
        self,
        snapshots: Sequence[RoundAgentSnapshot],
        is_sota: Optional[bool],
        search: Optional[str],
    ) -> List[MinerListItem]:
        items: List[MinerListItem] = []
        lowered = search.lower() if search else None

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
            hotkey = miner_info.hotkey if miner_info and miner_info.hotkey else ""
            uid_value = aggregate.uid if aggregate.uid is not None else -1

            if lowered:
                identifier_candidates = [
                    name.lower(),
                    hotkey.lower(),
                    aggregate.agent_id.lower(),
                    str(uid_value),
                ]
                if not any(lowered in candidate for candidate in identifier_candidates):
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

        return items
