from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    RoundORM,
    ValidatorRoundMinersScoreORM,
    ValidatorRoundMinerORM,
)
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
        # If round_number is specified, query directly from validator_round_miners_score
        # This bypasses Redis cache to ensure we get the correct score_consensus values
        if round_number is not None and round_number > 0:
            return await self._list_miners_from_scores(
                round_number=round_number,
                page=page,
                limit=limit,
                is_sota=is_sota,
                search=search,
            )

        # Fast path: try Redis snapshot first (only when no specific round is requested)
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

        # Fallback to SQL aggregate path
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

    @rollback_on_error
    async def _list_miners_from_scores(
        self,
        round_number: int,
        page: int,
        limit: int,
        is_sota: Optional[bool],
        search: Optional[str],
    ) -> MinimalMinerListResponse:
        """List miners for a specific round using score_consensus from validator_round_miners_score."""
        # Query miners with scores for this round
        stmt = (
            select(
                ValidatorRoundMinersScoreORM.miner_uid,
                ValidatorRoundMinersScoreORM.score_consensus,
                ValidatorRoundMinersScoreORM.rank_consensus,
                ValidatorRoundMinerORM.name,
                ValidatorRoundMinerORM.image_url,
                ValidatorRoundMinerORM.is_sota,
            )
            .select_from(
                ValidatorRoundMinersScoreORM.__table__.join(
                    RoundORM.__table__,
                    ValidatorRoundMinersScoreORM.validator_round_id
                    == RoundORM.validator_round_id,
                ).outerjoin(
                    ValidatorRoundMinerORM.__table__,
                    (RoundORM.validator_round_id == ValidatorRoundMinerORM.validator_round_id)
                    & (
                        ValidatorRoundMinersScoreORM.miner_uid
                        == ValidatorRoundMinerORM.miner_uid
                    ),
                )
            )
            .where(RoundORM.round_number == round_number)
            .order_by(
                ValidatorRoundMinersScoreORM.rank_consensus.asc().nulls_last(),
                ValidatorRoundMinersScoreORM.score_consensus.desc(),
            )
        )

        result = await self.session.execute(stmt)
        rows = result.all()

        # Build items from query results
        items: List[MinerListItem] = []
        lowered = search.lower() if search else None

        for row in rows:
            miner_uid = row.miner_uid
            score_consensus = float(row.score_consensus) if row.score_consensus else 0.0
            rank_consensus = row.rank_consensus
            name = row.name or f"Miner {miner_uid}"
            image_url = row.image_url or ""
            is_sota_value = row.is_sota if row.is_sota is not None else False

            # Apply filters
            if is_sota is not None and is_sota_value != is_sota:
                continue

            if lowered:
                if (
                    lowered not in name.lower()
                    and lowered not in str(miner_uid)
                ):
                    continue

            items.append(
                MinerListItem(
                    uid=miner_uid,
                    name=name,
                    ranking=rank_consensus or 0,
                    score=round(score_consensus, 4),
                    isSota=is_sota_value,
                    imageUrl=image_url,
                )
            )

        # Apply rankings (in case rank_consensus is None for some)
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
            round=round_number,
        )

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
