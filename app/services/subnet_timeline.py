from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Sequence

from app.models.ui.subnets import (
    MinerRosterEntry,
    MinerSnapshot,
    SubnetTimelineResponse,
    TimelineMeta,
    TimelineMetaQuery,
    TimelineRound,
)

ROUND_DURATION_SECONDS = 60
DEFAULT_ROUND_COUNT = 90
MAX_ROUND_COUNT = 500
DEFAULT_ROSTER_SIZE = 8
MAX_ROSTER_SIZE = 32

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

FALLBACK_DISPLAY_NAMES = [
    "Neuron Forge",
    "TensorWave",
    "Flux Dynamics",
    "Synapse Labs",
    "Signal Bridge",
    "Aurora Stack",
    "Pulse Metrics",
    "Orbit Compute",
]

ROUND_EPOCH_REFERENCE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _deterministic_random(parts: Sequence[str]) -> float:
    """
    Produce a deterministic pseudo-random float between 0 and 1.

    Uses SHA-256 of the joined parts and converts the leading bytes into a
    normalized float.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _build_miner_roster(subnet_id: str, roster_size: int) -> List[MinerRosterEntry]:
    roster: List[MinerRosterEntry] = []
    for index in range(roster_size):
        seed = f"{subnet_id}:{index}"
        miner_id = f"miner_{hashlib.md5(seed.encode('utf-8')).hexdigest()[:8]}"

        name_base = FALLBACK_DISPLAY_NAMES[index % len(FALLBACK_DISPLAY_NAMES)]
        name_suffix = index // len(FALLBACK_DISPLAY_NAMES)
        display_name = f"{name_base} {name_suffix + 1}" if name_suffix else name_base

        roster.append(
            MinerRosterEntry(
                miner_id=miner_id,
                display_name=display_name,
                color_hex=COLOR_PALETTE[index % len(COLOR_PALETTE)],
                avatar_url=f"https://api.dicebear.com/7.x/identicon/svg?seed={miner_id}",
                order=index,
            )
        )
    return roster


def _generate_timeline(
    *,
    subnet_id: str,
    roster: List[MinerRosterEntry],
    start_round: int,
    end_round: int,
    now: datetime,
) -> List[TimelineRound]:
    timeline: List[TimelineRound] = []
    previous_snapshots: dict[str, MinerSnapshot] = {}

    for round_number in range(start_round, end_round + 1):
        timestamp = now - timedelta(seconds=(end_round - round_number) * ROUND_DURATION_SECONDS)
        baseline_drift = _deterministic_random([subnet_id, "baseline", str(round_number)]) - 0.5

        miner_performances = []
        for miner in roster:
            baseline = 70 + _deterministic_random([subnet_id, miner.miner_id, "baseline"]) * 20
            phase_offset = _deterministic_random([miner.miner_id, "phase"]) * math.tau
            amplitude = 5 + _deterministic_random([miner.miner_id, "amplitude"]) * 10
            progression = round_number - start_round
            trend = (_deterministic_random([miner.miner_id, "trend"]) - 0.5) * 0.4 * progression
            oscillation = math.sin(round_number / 3 + phase_offset) * amplitude
            noise = (_deterministic_random([subnet_id, miner.miner_id, str(round_number), "noise"]) - 0.5) * 4

            raw_score = baseline + oscillation + noise + trend + baseline_drift * 5
            clamped_score = max(0.0, min(100.0, raw_score))
            score = round(clamped_score, 1)

            miner_performances.append((miner, score))

        miner_performances.sort(key=lambda item: (-item[1], item[0].order))

        snapshots: List[MinerSnapshot] = []
        for rank_index, (miner, score) in enumerate(miner_performances, start=1):
            previous_snapshot = previous_snapshots.get(miner.miner_id)
            previous_score = previous_snapshot.score if previous_snapshot else None
            score_change = (score - previous_score) if previous_score is not None else 0.0
            rank_change = (previous_snapshot.rank - rank_index) if previous_snapshot else 0

            snapshots.append(
                MinerSnapshot(
                    miner_id=miner.miner_id,
                    score=score,
                    rank=rank_index,
                    rank_change=rank_change,
                    score_change=round(score_change, 1),
                    previous_rank=previous_snapshot.rank if previous_snapshot else None,
                )
            )

        previous_snapshots = {snapshot.miner_id: snapshot for snapshot in snapshots}
        timeline.append(
            TimelineRound(
                round=round_number,
                timestamp=timestamp.replace(microsecond=0).isoformat(),
                snapshots=snapshots,
            )
        )

    return timeline


def build_subnet_timeline(
    subnet_id: str,
    *,
    rounds: Optional[int] = None,
    end_round: Optional[int] = None,
    seconds_back: Optional[int] = None,
    miners: Optional[int] = None,
    now: Optional[datetime] = None,
) -> SubnetTimelineResponse:
    """
    Build a deterministic mock timeline dataset for a subnet compatible with the UI animation.
    """

    now = now or datetime.now(timezone.utc)
    requested_rounds = rounds
    requested_end_round = end_round
    requested_miners = miners

    roster_size = _clamp(
        miners if miners is not None else DEFAULT_ROSTER_SIZE,
        1,
        MAX_ROSTER_SIZE,
    )
    roster = _build_miner_roster(subnet_id, roster_size)

    if rounds is None and seconds_back is not None:
        derived_rounds = max(1, seconds_back // ROUND_DURATION_SECONDS)
        rounds = derived_rounds

    rounds = _clamp(rounds or DEFAULT_ROUND_COUNT, 1, MAX_ROUND_COUNT)

    if end_round is None:
        elapsed_seconds = int((now - ROUND_EPOCH_REFERENCE).total_seconds())
        rounds_since_epoch = max(0, elapsed_seconds // ROUND_DURATION_SECONDS)
        end_round = max(1, 1000 + rounds_since_epoch)

    start_round = max(1, end_round - rounds + 1)
    inferred_round_count = end_round - start_round + 1

    timeline = _generate_timeline(
        subnet_id=subnet_id,
        roster=roster,
        start_round=start_round,
        end_round=end_round,
        now=now,
    )

    meta = TimelineMeta(
        subnet_id=subnet_id,
        start_round=start_round,
        end_round=end_round,
        round_count=len(timeline),
        round_duration_seconds=ROUND_DURATION_SECONDS,
        generated_at=now.isoformat(),
        query=TimelineMetaQuery(
            rounds=requested_rounds,
            end_round=requested_end_round,
            seconds_back=seconds_back,
            miners=requested_miners,
        ),
        inferred_round_count=inferred_round_count,
    )

    return SubnetTimelineResponse(
        subnet_id=subnet_id,
        roster=roster,
        timeline=timeline,
        meta=meta,
    )
