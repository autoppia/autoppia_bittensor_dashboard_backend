from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import logging
from sqlalchemy import select, func, cast, Float, Integer
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
from app.services.redis_cache import redis_cache
from app.db.models import MinerAggregatesMV


logger = logging.getLogger(__name__)


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
        # Primary path: materialized view (PostgreSQL)
        try:
            mv_response = await self._list_miners_from_materialized_view(
                page=page,
                limit=limit,
                is_sota=is_sota,
                search=search,
                round_number=round_number,
            )
            if mv_response is not None:
                return mv_response
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to query miner_aggregates_mv, falling back to Redis/SQL path: %s",
                exc,
                exc_info=True,
            )

        # Fallback 1: Redis snapshot (equivalent to Redis fallback in NestJS)
        snapshot = redis_cache.get("AGGREGATES:agents:v1")
        if isinstance(snapshot, dict) and snapshot:
            lowered = search.lower() if search else None
            items: List[MinerListItem] = []

            def pick_score(entry: Dict[str, any]) -> Tuple[float, Optional[int]]:
                # Prefer round-specific metrics when requested
                if round_number is not None and round_number > 0:
                    rnd = str(round_number)
                    per = entry.get("rounds", {}).get(rnd)
                    if per:
                        return float(per.get("avgScore", 0.0)), per.get("rank")
                # Fallback to global
                return float(entry.get("avgScore", 0.0)), entry.get("currentRank")

            filtered: List[Tuple[MinerListItem, float, Optional[int]]] = []
            for entry in snapshot.values():
                if not isinstance(entry, dict):
                    continue
                if is_sota is not None and bool(entry.get("isSota")) != is_sota:
                    continue
                uid = int(entry.get("uid") or -1)
                name = str(entry.get("name") or f"agent-{uid if uid >= 0 else 'unknown'}")
                if lowered and lowered not in name.lower() and lowered not in str(uid):
                    continue
                score, rank = pick_score(entry)
                image_url = str(entry.get("imageUrl") or "")
                item = MinerListItem(
                    uid=uid if uid is not None else -1,
                    name=name,
                    ranking=rank or 0,
                    score=round(float(score), 4),
                    isSota=bool(entry.get("isSota")),
                    imageUrl=image_url,
                )
                filtered.append((item, score, rank))

            # Sort: if rank available, asc by rank; else desc by score
            def sort_key(t: Tuple[MinerListItem, float, Optional[int]]):
                _, sc, rk = t
                return (0, rk) if rk is not None and rk > 0 else (1, -sc)

            filtered.sort(key=sort_key)
            items_only = [t[0] for t in filtered]
            ranked_items = self._apply_rankings(items_only, start_rank=1)
            total = len(items_only)
            start = (page - 1) * limit
            end = start + limit
            paginated = ranked_items[start:end]
            return MinimalMinerListResponse(
                miners=paginated,
                total=total,
                page=page,
                limit=limit,
                round=round_number if round_number and round_number > 0 else None,
            )

        # Fallback 2: SQL aggregate path (last resort, similar to direct DB in NestJS)
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
            ranked_items = self._apply_rankings(items, start_rank=1)

            total = len(ranked_items)
            start = (page - 1) * limit
            end = start + limit
            paginated = ranked_items[start:end]

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

    async def _list_miners_from_materialized_view(
        self,
        *,
        page: int,
        limit: int,
        is_sota: bool | None,
        search: str | None,
        round_number: Optional[int],
    ) -> Optional[MinimalMinerListResponse]:
        """
        Query the `miner_aggregates_mv` materialized view.

        Behaviour mirrors the NestJS implementation:
        - If `round_number` is provided, use round-specific metrics from JSONB `rounds`
          and rank by that round's average score.
        - Otherwise, use global `avg_score` ordering.
        """
        if page < 1 or limit < 1:
            return None

        offset = (page - 1) * limit

        if round_number is not None and round_number > 0:
            return await self._list_miners_from_mv_by_round(
                page=page,
                limit=limit,
                offset=offset,
                is_sota=is_sota,
                search=search,
                round_number=round_number,
            )

        # Global query (no round filter) – use ORM/select for clarity
        stmt = select(MinerAggregatesMV)

        conditions = []
        if is_sota is not None:
            conditions.append(MinerAggregatesMV.is_sota.is_(is_sota))

        # Name / UID search (case-insensitive name, numeric UID match)
        if search:
            lowered = search.lower()
            search_filters = [func.lower(MinerAggregatesMV.name).contains(lowered)]
            if search.isdigit():
                try:
                    uid_val = int(search)
                    search_filters.append(MinerAggregatesMV.uid == uid_val)
                except ValueError:
                    pass
            conditions.append(func.or_(*search_filters))

        if conditions:
            stmt = stmt.where(*conditions)

        stmt = stmt.order_by(MinerAggregatesMV.avg_score.desc())
        stmt = stmt.offset(offset).limit(limit)

        count_stmt = select(func.count()).select_from(MinerAggregatesMV)
        if conditions:
            count_stmt = count_stmt.where(*conditions)

        result, total = await self.session.execute(stmt), await self.session.execute(
            count_stmt
        )
        rows = list(result.scalars().all())
        total_count = int(total.scalar() or 0)

        if not rows:
            return MinimalMinerListResponse(
                miners=[],
                total=0,
                page=page,
                limit=limit,
                round=None,
            )

        items: List[MinerListItem] = []
        for row in rows:
            # Use materialized view global metrics
            score = float(getattr(row, "avg_score", 0.0) or 0.0)
            current_rank = int(getattr(row, "current_rank", 0) or 0)
            items.append(
                MinerListItem(
                    uid=row.uid,
                    name=row.name or f"agent-{row.uid}",
                    ranking=current_rank,
                    score=round(score, 4),
                    isSota=bool(row.is_sota),
                    imageUrl=row.image_url or "",
                )
            )

        # Apply sequential rankings (1, 2, 3, ...) as in NestJS MinerListItemEntity.applyRankings
        ranked_items = self._apply_rankings(items, start_rank=1)

        return MinimalMinerListResponse(
            miners=ranked_items,
            total=total_count,
            page=page,
            limit=limit,
            round=None,
        )

    async def _list_miners_from_mv_by_round(
        self,
        *,
        page: int,
        limit: int,
        offset: int,
        is_sota: bool | None,
        search: str | None,
        round_number: int,
    ) -> Optional[MinimalMinerListResponse]:
        """
        Round-specific query against `miner_aggregates_mv`, using JSONB `rounds`.

        We approximate the NestJS raw SQL behaviour:
        - Filter only miners that have data for the requested round.
        - Order by that round's `avgScore` (desc).
        - Use per-round `rank` when available; otherwise fall back to sequential rank.
        """
        # Build base query with JSONB access
        round_key = str(round_number)

        # `rounds` contains per-round aggregates keyed by round_number as string.
        has_round = MinerAggregatesMV.rounds.has_key(round_key)  # type: ignore[attr-defined]

        # Extract per-round average score and rank from JSONB (as text → cast to proper types)
        round_avg_score_col = cast(
            MinerAggregatesMV.rounds[round_key]["avgScore"].astext,  # type: ignore[index]
            Float,
        ).label("round_avg_score")
        round_rank_col = cast(
            MinerAggregatesMV.rounds[round_key]["rank"].astext,  # type: ignore[index]
            Integer,
        ).label("round_rank")

        stmt = (
            select(
                MinerAggregatesMV,
                round_avg_score_col,
                round_rank_col,
            )
            .where(has_round)
        )

        conditions = []

        if is_sota is not None:
            conditions.append(MinerAggregatesMV.is_sota.is_(is_sota))

        if search:
            lowered = search.lower()
            search_filters = [func.lower(MinerAggregatesMV.name).contains(lowered)]
            if search.isdigit():
                try:
                    uid_val = int(search)
                    search_filters.append(MinerAggregatesMV.uid == uid_val)
                except ValueError:
                    pass
            conditions.append(func.or_(*search_filters))

        if conditions:
            stmt = stmt.where(*conditions)

        # Order by round-specific average score (desc), falling back to 0.0 when NULL
        stmt = stmt.order_by(func.coalesce(round_avg_score_col, 0.0).desc())
        stmt = stmt.offset(offset).limit(limit)

        count_stmt = select(func.count()).select_from(MinerAggregatesMV).where(has_round)
        if conditions:
            count_stmt = count_stmt.where(*conditions)

        result = await self.session.execute(stmt)
        total_result = await self.session.execute(count_stmt)

        rows = list(result.all())
        total_count = int(total_result.scalar() or 0)

        if not rows:
            return MinimalMinerListResponse(
                miners=[],
                total=0,
                page=page,
                limit=limit,
                round=round_number,
            )

        items: List[MinerListItem] = []
        for row, round_avg_score, round_rank in rows:
            mv_row: MinerAggregatesMV = row
            score_val = float(round_avg_score or 0.0)
            rank_val = int(round_rank or 0)
            items.append(
                MinerListItem(
                    uid=mv_row.uid,
                    name=mv_row.name or f"agent-{mv_row.uid}",
                    ranking=rank_val,
                    score=round(score_val, 4),
                    isSota=bool(mv_row.is_sota),
                    imageUrl=mv_row.image_url or "",
                )
            )

        ranked_items = self._apply_rankings(items, start_rank=1)

        return MinimalMinerListResponse(
            miners=ranked_items,
            total=total_count,
            page=page,
            limit=limit,
            round=round_number,
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

    @staticmethod
    def _apply_rankings(
        items: Sequence[MinerListItem],
        start_rank: int = 1,
    ) -> List[MinerListItem]:
        """Return a new list with sequential rankings applied."""
        ranked: List[MinerListItem] = []
        current_rank = start_rank
        for item in items:
            ranked.append(item.model_copy(update={"ranking": current_rank}))
            current_rank += 1
        return ranked
