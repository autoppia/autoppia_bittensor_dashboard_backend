from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ui.subnets import (
    MinerRosterEntry,
    MinerSnapshot,
    SubnetTimelineResponse,
    TimelineMeta,
    TimelineMetaQuery,
    TimelineRound,
)

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


def _iso_timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=0).isoformat()


class SubnetTimelineService:
    """Build subnet timeline responses sourced from new DB schema tables."""

    def __init__(self, session: AsyncSession):
        self.session = session

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
        requested_rounds = self._resolve_round_count(rounds=rounds, seconds_back=seconds_back, available=len(available_rounds))
        selected = self._select_rounds(available_rounds, end_round_number=target_end, count=requested_rounds)
        if not selected:
            raise ValueError("No rounds matched the requested window")

        roster_size = self._resolve_roster_size(miners)
        timeline, roster = await self._build_timeline_and_roster(selected, roster_size)

        durations = []
        for idx in range(1, len(timeline)):
            t1 = datetime.fromisoformat(timeline[idx - 1].timestamp.replace("Z", "+00:00")).timestamp()
            t2 = datetime.fromisoformat(timeline[idx].timestamp.replace("Z", "+00:00")).timestamp()
            durations.append(max(t2 - t1, 0))
        average_duration = int(round(mean(durations))) if durations else FALLBACK_ROUND_DURATION

        meta = TimelineMeta(
            subnet_id=subnet_id,
            start_round=timeline[0].round,
            end_round=timeline[-1].round,
            round_count=len(timeline),
            round_duration_seconds=max(1, average_duration),
            generated_at=datetime.now(timezone.utc).isoformat(),
            query=TimelineMetaQuery(
                rounds=rounds,
                end_round=end_round,
                seconds_back=seconds_back,
                miners=miners,
            ),
            inferred_round_count=len(selected),
        )
        return SubnetTimelineResponse(subnet_id=subnet_id, roster=roster, timeline=timeline, meta=meta)

    async def _available_rounds(self) -> List[Tuple[int, int, int, float]]:
        rows = (
            (
                await self.session.execute(
                    text(
                        """
                    SELECT
                      r.round_id,
                      s.season_number,
                      r.round_number_in_season,
                      EXTRACT(EPOCH FROM COALESCE(r.ended_at, r.started_at, NOW())) AS ts
                    FROM rounds r
                    JOIN seasons s ON s.season_id = r.season_id
                    ORDER BY s.season_number ASC, r.round_number_in_season ASC
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        result: List[Tuple[int, int, int, float]] = []
        for row in rows:
            season = int(row["season_number"])
            round_in = int(row["round_number_in_season"])
            encoded = season * 10000 + round_in
            result.append((encoded, int(row["round_id"]), round_in, float(row["ts"] or datetime.now(timezone.utc).timestamp())))
        return result

    @staticmethod
    def _resolve_end_round(end_round: Optional[int], available_rounds: Sequence[Tuple[int, int, int, float]]) -> int:
        max_available = available_rounds[-1][0]
        if end_round is None:
            return max_available
        return min(max(end_round, 1), max_available)

    @staticmethod
    def _resolve_round_count(*, rounds: Optional[int], seconds_back: Optional[int], available: int) -> int:
        if rounds is not None:
            count = max(1, min(rounds, MAX_ROUND_COUNT))
        elif seconds_back is not None:
            inferred = max(1, seconds_back // FALLBACK_ROUND_DURATION)
            count = min(inferred, MAX_ROUND_COUNT)
        else:
            count = DEFAULT_ROUND_COUNT
        return min(count, available)

    @staticmethod
    def _select_rounds(
        rounds: Sequence[Tuple[int, int, int, float]],
        *,
        end_round_number: int,
        count: int,
    ) -> List[Tuple[int, int, int, float]]:
        eligible = [entry for entry in rounds if entry[0] <= end_round_number]
        if not eligible:
            return []
        return eligible[-count:]

    @staticmethod
    def _resolve_roster_size(miners: Optional[int]) -> int:
        if miners is None:
            return DEFAULT_ROSTER_SIZE
        return max(1, min(miners, MAX_ROSTER_SIZE))

    async def _build_timeline_and_roster(
        self,
        selected_rounds: Sequence[Tuple[int, int, int, float]],
        roster_size: int,
    ) -> Tuple[List[TimelineRound], List[MinerRosterEntry]]:
        per_round: Dict[int, List[Dict[str, object]]] = {}
        miner_presence: Dict[str, int] = defaultdict(int)
        miner_best_reward: Dict[str, float] = defaultdict(float)
        miner_names: Dict[str, str] = {}

        for encoded, round_id, _round_in, _ts in selected_rounds:
            rows = (
                (
                    await self.session.execute(
                        text(
                            """
                        WITH ranked AS (
                          SELECT DISTINCT ON (miner_uid)
                            miner_uid,
                            COALESCE(name, 'Miner ' || miner_uid::text) AS name,
                            COALESCE(best_local_rank, post_consensus_rank, 9999) AS rank,
                            COALESCE(best_local_reward, post_consensus_avg_reward, 0) AS reward
                          FROM round_validator_miners
                          WHERE round_id = :rid
                            AND NULLIF(TRIM(COALESCE(name, '')), '') IS NOT NULL
                            AND NULLIF(TRIM(COALESCE(github_url, '')), '') IS NOT NULL
                          ORDER BY miner_uid, COALESCE(best_local_rank, post_consensus_rank, 9999) ASC, COALESCE(best_local_reward, post_consensus_avg_reward, 0) DESC
                        )
                        SELECT miner_uid, name, rank, reward
                        FROM ranked
                        ORDER BY rank ASC, reward DESC
                        """
                        ),
                        {"rid": round_id},
                    )
                )
                .mappings()
                .all()
            )
            miners_for_round: List[Dict[str, object]] = []
            for row in rows:
                miner_uid = int(row["miner_uid"])
                miner_id = f"miner-{miner_uid}"
                reward_pct = max(0.0, min(float(row["reward"] or 0.0) * 100.0, 100.0))
                miners_for_round.append(
                    {
                        "miner_id": miner_id,
                        "name": str(row["name"]),
                        "rank": int(row["rank"] or 9999),
                        "reward": reward_pct,
                    }
                )
                miner_presence[miner_id] += 1
                miner_best_reward[miner_id] = max(miner_best_reward[miner_id], reward_pct)
                miner_names[miner_id] = str(row["name"])
            per_round[encoded] = miners_for_round

        ordered_miners = sorted(
            miner_presence.keys(),
            key=lambda mid: (-miner_presence[mid], -miner_best_reward[mid], int(mid.split("-")[-1])),
        )[:roster_size]

        roster: List[MinerRosterEntry] = []
        for idx, miner_id in enumerate(ordered_miners):
            uid = miner_id.split("-")[-1]
            roster.append(
                MinerRosterEntry(
                    miner_id=miner_id,
                    display_name=miner_names.get(miner_id, f"Miner {uid}"),
                    color_hex=COLOR_PALETTE[idx % len(COLOR_PALETTE)],
                    avatar_url=f"https://placehold.co/96x96/png?text=M{uid}",
                    order=idx,
                )
            )

        prev_rank: Dict[str, Optional[int]] = {r.miner_id: None for r in roster}
        prev_reward: Dict[str, float] = {r.miner_id: 0.0 for r in roster}
        timeline: List[TimelineRound] = []
        for encoded, _round_id, _round_in, ts in selected_rounds:
            round_rows = per_round.get(encoded, [])
            by_id = {str(row["miner_id"]): row for row in round_rows}
            snapshots: List[MinerSnapshot] = []
            for roster_entry in roster:
                row = by_id.get(roster_entry.miner_id)
                current_rank = int(row["rank"]) if row else max(1, len(round_rows) + 1)
                current_reward = float(row["reward"]) if row else 0.0
                previous_rank = prev_rank.get(roster_entry.miner_id)
                previous_reward = prev_reward.get(roster_entry.miner_id, 0.0)
                rank_change = 0 if previous_rank is None else previous_rank - current_rank
                reward_change = current_reward - previous_reward
                snapshots.append(
                    MinerSnapshot(
                        miner_id=roster_entry.miner_id,
                        reward=current_reward,
                        rank=current_rank,
                        rank_change=rank_change,
                        reward_change=reward_change,
                        previous_rank=previous_rank,
                    )
                )
                prev_rank[roster_entry.miner_id] = current_rank
                prev_reward[roster_entry.miner_id] = current_reward
            timeline.append(TimelineRound(round=encoded, timestamp=_iso_timestamp(ts), snapshots=snapshots))

        return timeline, roster
