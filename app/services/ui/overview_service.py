from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvaluationRunORM, EvaluationResultORM, RoundORM, TaskORM
from app.models.core import ValidatorRound
from app.models.ui.overview import (
    ActivityMetadata,
    LeaderboardEntry,
    NetworkStatus,
    OverviewMetrics,
    PerformanceTrend,
    RecentActivity,
    RoundInfo,
    SubnetStatistics,
    ValidatorInfo,
)
from app.services.ui.rounds_service import AgentRunContext, RoundRecord, RoundsService
from app.config import settings
from app.utils.images import resolve_validator_image

logger = logging.getLogger(__name__)


class ValidatorState(Enum):
    NOT_STARTED = "not_started"
    STARTING = "starting"
    SENDING_TASKS = "sending_tasks"
    EVALUATING = "evaluating"
    WAITING = "waiting"
    OFFLINE = "offline"
    FINISHED = "finished"


STATUS_DISPLAY: Dict[ValidatorState, str] = {
    ValidatorState.NOT_STARTED: "Not Started",
    ValidatorState.STARTING: "Starting",
    ValidatorState.SENDING_TASKS: "Sending Tasks",
    ValidatorState.EVALUATING: "Evaluating",
    ValidatorState.WAITING: "Waiting",
    ValidatorState.OFFLINE: "Offline",
    ValidatorState.FINISHED: "Finished",
}

STATUS_DEFAULT_TASK: Dict[ValidatorState, str] = {
    ValidatorState.NOT_STARTED: "Awaiting round start",
    ValidatorState.STARTING: "Validator connected – awaiting task upload",
    ValidatorState.SENDING_TASKS: "Distributing tasks to agent runs",
    ValidatorState.EVALUATING: "Evaluating miner submissions",
    ValidatorState.WAITING: "Awaiting next action",
    ValidatorState.OFFLINE: "No activity detected recently",
    ValidatorState.FINISHED: "Round completed",
}


@dataclass(frozen=True)
class ValidatorStatusInfo:
    """Normalized validator activity status for overview displays."""

    state: ValidatorState
    label: str
    default_task: str
    requires_prompt: bool

    @classmethod
    def from_state(cls, state: ValidatorState) -> "ValidatorStatusInfo":
        return cls(
            state=state,
            label=STATUS_DISPLAY[state],
            default_task=STATUS_DEFAULT_TASK[state],
            requires_prompt=state
            not in {ValidatorState.NOT_STARTED, ValidatorState.FINISHED},
        )


def _round_id_to_int(round_id: str) -> int:
    if not round_id:
        return 0
    suffix = round_id
    if round_id.startswith("round_"):
        suffix = round_id.split("round_", 1)[1]
    elif "_" in round_id:
        suffix = round_id.split("_", 1)[1]
    digits: list[str] = []
    for char in suffix:
        if char.isdigit():
            digits.append(char)
        else:
            break
    if not digits:
        return 0
    try:
        return int("".join(digits))
    except ValueError:
        return 0


def _timestamp(value: Optional[float]) -> str:
    if value is None:
        value = datetime.now(timezone.utc).timestamp()
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()


class OverviewService:
    """Compute overview metrics from SQL-backed data."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.rounds_service = RoundsService(session)

    async def overview_metrics(self) -> OverviewMetrics:
        records_with_contexts = await self._recent_round_records(
            limit=50, include_details=True
        )
        if not records_with_contexts:
            now_iso = datetime.now(timezone.utc).isoformat()
            return OverviewMetrics(
                topScore=0.0,
                totalWebsites=0,
                totalValidators=0,
                totalMiners=0,
                currentRound=0,
                metricsRound=0,
                subnetVersion="1.0.0",
                lastUpdated=now_iso,
            )

        def _round_number(record: RoundRecord) -> int:
            model = record.model
            return model.round_number or _round_id_to_int(model.validator_round_id)

        # Group records by round so we can easily pick the latest completed set.
        round_records_by_number: Dict[
            int, List[Tuple[RoundRecord, List[AgentRunContext]]]
        ] = {}
        completed_round_numbers: set[int] = set()
        for record, contexts in records_with_contexts:
            number = _round_number(record)
            if number:
                round_records_by_number.setdefault(number, []).append(
                    (record, contexts)
                )
                if record.model.ended_at:
                    completed_round_numbers.add(number)

        latest_completed_round = (
            max(completed_round_numbers) if completed_round_numbers else None
        )

        try:
            current_round_overview = (
                await self.rounds_service.get_current_round_overview()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to resolve current round overview: %s", exc)
            current_round_overview = None

        def _resolve_round_number(payload: Optional[Dict[str, Any]]) -> int:
            if not payload:
                return 0
            candidate = (
                payload.get("round") or payload.get("roundNumber") or payload.get("id")
            )
            if isinstance(candidate, int):
                return candidate
            if isinstance(candidate, float):
                return int(candidate)
            if isinstance(candidate, str):
                parsed = _round_id_to_int(candidate)
                if parsed:
                    return parsed
                if candidate.isdigit():
                    return int(candidate)
            return 0

        current_round_value = _resolve_round_number(current_round_overview)
        if current_round_value <= 0:
            current_round_candidates = [
                _round_number(record)
                for record, _ in records_with_contexts
                if _round_number(record)
            ]
            if current_round_candidates:
                current_round_value = max(current_round_candidates)

        preferred_previous_round: Optional[int] = None
        if current_round_value > 0:
            preferred_previous_round = max(current_round_value - 1, 0)

        candidate_round_numbers: List[int] = []
        if preferred_previous_round and preferred_previous_round > 0:
            candidate_round_numbers.append(preferred_previous_round)
        if (
            latest_completed_round is not None
            and latest_completed_round not in candidate_round_numbers
        ):
            candidate_round_numbers.append(latest_completed_round)

        for number in sorted(completed_round_numbers, reverse=True):
            if number not in candidate_round_numbers:
                candidate_round_numbers.append(number)

        for number in sorted(round_records_by_number.keys(), reverse=True):
            if number not in candidate_round_numbers:
                candidate_round_numbers.append(number)

        target_records: List[Tuple[RoundRecord, List[AgentRunContext]]] = []
        metrics_round_number = 0
        for number in candidate_round_numbers:
            candidates = round_records_by_number.get(number)
            if not candidates:
                continue
            completed_candidates = [
                (record, contexts)
                for record, contexts in candidates
                if record.model.ended_at
            ]
            selected_records = completed_candidates or candidates
            if not selected_records:
                continue
            target_records = selected_records
            metrics_round_number = number
            break

        if not target_records:
            target_records = [records_with_contexts[0]]
            metrics_round_number = _round_number(target_records[0][0])

        top_score = 0.0
        validators: set[int] = set()
        miners: set[str] = set()
        version_candidates: List[str] = []
        unique_websites: set[str] = set()
        miner_score_tracker: Dict[str, List[float]] = {}

        for record, contexts in target_records:
            round_obj = record.model
            if round_obj.validator_uid is not None:
                validators.add(round_obj.validator_uid)

            for validator_snapshot in getattr(round_obj, "validators", []) or []:
                if getattr(validator_snapshot, "uid", None) is not None:
                    validators.add(validator_snapshot.uid)
                version = getattr(validator_snapshot, "version", None)
                if version:
                    version_candidates.append(str(version))

            validator_info = getattr(round_obj, "validator_info", None)
            if validator_info and validator_info.version:
                version_candidates.append(str(validator_info.version))

            seen_tasks: set[str] = set()
            for ctx in contexts:
                miner_identifier = None
                if ctx.run.miner_uid is not None:
                    miner_identifier = f"uid:{ctx.run.miner_uid}"
                elif ctx.run.agent_run_id:
                    miner_identifier = f"run:{ctx.run.agent_run_id}"
                if miner_identifier:
                    miners.add(miner_identifier)

                score = self.rounds_service._context_score(ctx)
                if miner_identifier:
                    tracker = miner_score_tracker.setdefault(miner_identifier, [])
                    tracker.append(score)

                for task in ctx.tasks or []:
                    if task.task_id in seen_tasks:
                        continue
                    seen_tasks.add(task.task_id)
                    host = urlparse(task.url).netloc or task.url
                    if host:
                        unique_websites.add(host.lower())

            if not contexts and round_obj.winners:
                round_top = max(
                    winner.get("score", 0.0) for winner in round_obj.winners
                )
                top_score = max(top_score, round(round_top, 6))

        top_miner_uid = None
        top_miner_name = None

        if miner_score_tracker:
            # Calculate average score for each miner and find the top one
            miner_averages = {
                identifier: sum(scores) / len(scores)
                for identifier, scores in miner_score_tracker.items()
                if scores
            }
            if miner_averages:
                top_score = max(miner_averages.values())
                # Find the miner with the top score
                for identifier, avg_score in miner_averages.items():
                    if avg_score == top_score:
                        # Extract UID from identifier (format: "uid:80" or "run:...")
                        if identifier.startswith("uid:"):
                            try:
                                top_miner_uid = int(identifier.split(":", 1)[1])
                            except (ValueError, IndexError):
                                pass
                        # Try to find miner name from contexts
                        for record, contexts in target_records:
                            for ctx in contexts:
                                if ctx.run.miner_uid == top_miner_uid:
                                    miner_info = getattr(ctx.run, "miner_info", None)
                                    if miner_info and miner_info.agent_name:
                                        top_miner_name = miner_info.agent_name
                                        break
                            if top_miner_name:
                                break
                        break
        else:
            # If we have no context data, fall back to best single score captured earlier.
            top_score = max(top_score, 0.0)

        subnet_version = version_candidates[0] if version_candidates else "1.0.0"
        total_websites = (
            len(unique_websites) if unique_websites else await self._total_websites()
        )

        display_metrics_round_number = int(metrics_round_number or 0)
        if display_metrics_round_number < 0:
            display_metrics_round_number = 0

        return OverviewMetrics(
            topScore=round(top_score, 3),
            topMinerUid=top_miner_uid,
            topMinerName=top_miner_name,
            totalWebsites=total_websites,
            totalValidators=len(validators),
            totalMiners=len(miners),
            currentRound=current_round_value,
            metricsRound=display_metrics_round_number,
            subnetVersion=subnet_version,
            lastUpdated=datetime.now(timezone.utc).isoformat(),
        )

    async def validators_list(
        self,
        page: int,
        limit: int,
        status: Optional[str],
        sort_by: str,
        sort_order: str,
    ) -> Tuple[List[Dict[str, Any]], int]:
        validators = await self._aggregate_validators()

        entries = list(validators.values())

        if status:
            entries = [entry for entry in entries if entry["status"] == status]

        reverse = sort_order.lower() == "desc"
        try:
            entries.sort(key=lambda item: item.get(sort_by), reverse=reverse)
        except Exception:  # noqa: BLE001
            pass

        total = len(entries)
        start = (page - 1) * limit
        end = start + limit
        return entries[start:end], total

    async def validator_detail(self, validator_id: str) -> Dict[str, Any]:
        validators = await self._aggregate_validators()
        validator = validators.get(validator_id)
        if not validator:
            raise ValueError(f"Validator {validator_id} not found")
        return validator

    async def validators_filter(self) -> List[Dict[str, Any]]:
        validators = await self._aggregate_validators()
        items: List[Dict[str, Any]] = []
        for identifier, data in validators.items():
            items.append(
                {
                    "id": identifier,
                    "name": data.get("name", identifier),
                    "hotkey": data.get("hotkey"),
                    "icon": data.get("icon"),
                    "status": data.get("status"),
                }
            )
        items.sort(key=lambda item: item["name"])
        return items

    async def current_round(self) -> Optional[RoundInfo]:
        rounds = await self._recent_rounds(limit=1)
        if not rounds:
            return None
        return self._round_to_info(rounds[0], current=True)

    async def rounds_list(
        self, page: int, limit: int, status: Optional[str]
    ) -> Tuple[List[RoundInfo], Optional[RoundInfo], int]:
        rounds = await self._recent_rounds(limit=100)
        active_rounds = [round_obj for round_obj in rounds if not round_obj.ended_at]
        completed_rounds = [round_obj for round_obj in rounds if round_obj.ended_at]

        completed_rounds.sort(key=lambda r: r.started_at or 0, reverse=True)

        round_infos = [
            self._round_to_info(round_obj, current=False)
            for round_obj in completed_rounds
        ]

        if status:
            round_infos = [info for info in round_infos if info.status == status]

        total = len(round_infos)
        start = (page - 1) * limit
        end = start + limit
        paginated = round_infos[start:end]

        current_round_obj: Optional[ValidatorRound] = None
        if active_rounds:
            active_rounds.sort(key=lambda r: r.started_at or 0, reverse=True)
            current_round_obj = active_rounds[0]
        elif completed_rounds:
            current_round_obj = completed_rounds[0]

        current_round = (
            self._round_to_info(current_round_obj, current=True)
            if current_round_obj
            else None
        )
        return paginated, current_round, total

    async def round_detail(self, identifier: str) -> RoundInfo:
        if identifier.isdigit():
            validator_round_id = f"round_{identifier.zfill(3)}"
        else:
            validator_round_id = identifier

        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
            .where(RoundORM.validator_round_id == validator_round_id)
        )
        row = await self.session.scalar(stmt)
        if not row:
            raise ValueError(f"Round {identifier} not found")
        round_obj = self.rounds_service._deserialize_round(row)
        return self._round_to_info(round_obj, current=round_obj.ended_at is None)

    async def leaderboard(
        self,
        time_range: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Tuple[List[LeaderboardEntry], Dict[str, str]]:
        normalized_range = (time_range or "").strip().lower()
        range_limits = {
            "7d": 7,
            "15d": 15,
            "30d": 30,
        }

        derived_limit: Optional[int] = None
        unlimited = False

        if normalized_range == "all":
            unlimited = True
        elif normalized_range in range_limits:
            derived_limit = range_limits[normalized_range]
        elif normalized_range.endswith("d"):
            try:
                parsed_days = int(normalized_range[:-1])
                if parsed_days > 0:
                    derived_limit = parsed_days
            except ValueError:
                derived_limit = None

        if derived_limit is None and not unlimited:
            # Default to a sensible window when no explicit range is provided.
            derived_limit = 30

        if limit is not None:
            # When an explicit limit is provided, it takes precedence and disables the "all" flag.
            unlimited = False
            derived_limit = min(limit, derived_limit) if derived_limit else limit

        fetch_limit = 0
        if not unlimited:
            # Fetch a wider window than requested so we can collapse multiple validator rounds
            # for the same logical day into a single aggregated round.
            fetch_limit = max((derived_limit or 30) * 5, derived_limit or 1)

        records_with_contexts = await self._recent_round_records(
            limit=fetch_limit or 0,
            include_details=False,
        )
        if not records_with_contexts:
            now_iso = datetime.now(timezone.utc).isoformat()
            return [], {"start": now_iso, "end": now_iso}

        def _scores_for_provider(
            contexts: List[AgentRunContext],
            provider_tokens: List[str],
        ) -> List[float]:
            scores: List[float] = []
            for ctx in contexts:
                miner_info = ctx.run.miner_info
                provider = ""
                if miner_info:
                    provider = str(
                        miner_info.provider or miner_info.agent_name or ""
                    ).lower()
                if provider and any(token in provider for token in provider_tokens):
                    scores.append(self.rounds_service._context_score(ctx))
            return scores

        total_rounds = len(records_with_contexts)
        entries: List[LeaderboardEntry] = []
        for idx, (record, contexts) in enumerate(records_with_contexts):
            round_obj = record.model

            # Only include finished rounds
            if round_obj.status != "finished":
                continue

            run_scores = [self.rounds_service._context_score(ctx) for ctx in contexts]

            average_score = (
                round_obj.average_score
                if round_obj.average_score is not None
                else (sum(run_scores) / len(run_scores) if run_scores else 0.0)
            )

            # Find the winner (highest score)
            winner_uid: Optional[int] = None
            winner_name: Optional[str] = None
            if contexts:
                winner_ctx = max(
                    contexts,
                    key=lambda ctx: self.rounds_service._context_score(ctx),
                )
                winner_uid = winner_ctx.run.miner_uid
                miner_info = getattr(winner_ctx.run, "miner_info", None)
                if miner_info and miner_info.agent_name:
                    winner_name = miner_info.agent_name

            openai_scores = _scores_for_provider(contexts, ["openai"])
            anthropic_scores = _scores_for_provider(contexts, ["anthropic"])
            browser_scores = _scores_for_provider(contexts, ["browser"])

            def _avg(values: List[float]) -> Optional[float]:
                if not values:
                    return None
                return round(sum(values) / len(values), 3)

            timestamp = datetime.fromtimestamp(
                round_obj.started_at or datetime.now(timezone.utc).timestamp(),
                tz=timezone.utc,
            ).isoformat()

            round_number = round_obj.round_number or _round_id_to_int(
                round_obj.validator_round_id
            )
            if not round_number or round_number <= 0:
                round_number = total_rounds - idx

            entries.append(
                LeaderboardEntry(
                    round=round_number,
                    subnet36=round(average_score, 3),
                    winnerUid=winner_uid,
                    winnerName=winner_name,
                    openai_cua=_avg(openai_scores),
                    anthropic_cua=_avg(anthropic_scores),
                    browser_use=_avg(browser_scores),
                    timestamp=timestamp,
                )
            )

        grouped_entries: Dict[int, List[LeaderboardEntry]] = defaultdict(list)
        for entry in entries:
            grouped_entries[entry.round].append(entry)

        aggregated_entries: List[LeaderboardEntry] = []
        for round_number, round_entries in grouped_entries.items():
            latest_timestamp = max(entry.timestamp for entry in round_entries)
            subnet36_values = [entry.subnet36 for entry in round_entries]
            openai_values = [
                value
                for value in (entry.openai_cua for entry in round_entries)
                if value is not None
            ]
            anthropic_values = [
                value
                for value in (entry.anthropic_cua for entry in round_entries)
                if value is not None
            ]
            browser_values = [
                value
                for value in (entry.browser_use for entry in round_entries)
                if value is not None
            ]

            # Get winner from the entry with highest score
            winner_entry = max(round_entries, key=lambda e: e.subnet36, default=None)
            winner_uid = winner_entry.winnerUid if winner_entry else None
            winner_name = winner_entry.winnerName if winner_entry else None

            aggregated_entries.append(
                LeaderboardEntry(
                    round=round_number,
                    subnet36=round(max(subnet36_values), 3) if subnet36_values else 0.0,
                    winnerUid=winner_uid,
                    winnerName=winner_name,
                    openai_cua=round(max(openai_values), 3) if openai_values else None,
                    anthropic_cua=(
                        round(max(anthropic_values), 3) if anthropic_values else None
                    ),
                    browser_use=(
                        round(max(browser_values), 3) if browser_values else None
                    ),
                    timestamp=latest_timestamp,
                )
            )

        aggregated_entries.sort(key=lambda entry: entry.timestamp, reverse=True)

        if not unlimited and derived_limit:
            aggregated_entries = aggregated_entries[:derived_limit]

        if not aggregated_entries:
            now_iso = datetime.now(timezone.utc).isoformat()
            return [], {"start": now_iso, "end": now_iso}

        start = min(entry.timestamp for entry in aggregated_entries)
        end = max(entry.timestamp for entry in aggregated_entries)
        return aggregated_entries, {"start": start, "end": end}

    async def statistics(self) -> SubnetStatistics:
        validators = await self._aggregate_validators()

        def _to_float(value: Any) -> float:
            if value is None:
                return 0.0
            if isinstance(value, (int, float)):
                return float(value)
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        total_stake = int(
            sum(_to_float(entry.get("stake")) for entry in validators.values())
        )
        total_emission = int(
            sum(_to_float(entry.get("emission")) for entry in validators.values())
        )
        total_runs = await self._total_runs()
        success_total = int(total_runs * 0.85)

        average_trust = (
            sum(_to_float(entry.get("trust")) for entry in validators.values())
            / len(validators)
            if validators
            else 0.0
        )
        average_uptime = (
            sum(_to_float(entry.get("uptime", 0.0)) for entry in validators.values())
            / len(validators)
            if validators
            else 0.0
        )

        average_score = await self._average_score()
        total_tasks_completed = sum(
            entry["completedTasks"] for entry in validators.values()
        )

        return SubnetStatistics(
            totalStake=total_stake,
            totalEmission=total_emission,
            averageTrust=round(average_trust, 3),
            networkUptime=round(average_uptime, 2),
            activeValidators=len(validators),
            registeredMiners=len(validators),  # Approximate
            totalTasksCompleted=total_tasks_completed,
            averageTaskScore=round(average_score, 3),
            lastUpdated=datetime.now(timezone.utc).isoformat(),
        )

    async def network_status(self) -> NetworkStatus:
        validators = await self._aggregate_validators()
        rounds = await self._recent_rounds(limit=5)
        now = datetime.now(timezone.utc)
        network_latency_samples = [
            round_obj.elapsed_sec for round_obj in rounds if round_obj.elapsed_sec
        ]
        average_latency = (
            int(sum(network_latency_samples) / len(network_latency_samples))
            if network_latency_samples
            else 0
        )

        # Simplified policy: Always show healthy/live except when last round was > 6 hours ago.
        # If there are no rounds yet, treat as healthy.
        if not rounds:
            return NetworkStatus(
                status="healthy",
                message="Awaiting first round",
                lastChecked=now.isoformat(),
                activeValidators=len(validators),
                networkLatency=average_latency,
            )

        last_round = max(
            rounds,
            key=lambda r: (r.ended_at or r.started_at or 0),
        )
        last_activity_ts = last_round.ended_at or last_round.started_at
        last_activity_dt = (
            datetime.fromtimestamp(last_activity_ts, tz=timezone.utc)
            if last_activity_ts
            else None
        )

        # If timestamp missing, still treat as healthy per simplified policy
        if not last_activity_ts:
            status = "healthy"
            message = "Awaiting next round"
        else:
            delta = max(now.timestamp() - last_activity_ts, 0.0)
            delta_hours = delta / 3600

            def _humanize(seconds: float) -> str:
                total_seconds = int(max(seconds, 0))
                days, remainder = divmod(total_seconds, 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes, _ = divmod(remainder, 60)
                parts: List[str] = []
                if days:
                    parts.append(f"{days} day{'s' if days != 1 else ''}")
                if hours:
                    parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes and not days:
                    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
                if not parts:
                    parts.append("just now")
                return " ".join(parts)

            human_delta = _humanize(delta)
            timestamp_label = (
                last_activity_dt.strftime("%Y-%m-%d %H:%M UTC")
                if last_activity_dt
                else "unknown time"
            )
            round_label = _round_id_to_int(last_round.validator_round_id)

            if delta_hours > 6:
                status = "degraded"
                message = (
                    f"No round activity for {human_delta} — last known round #{round_label} "
                    f"completed at {timestamp_label}"
                )
            else:
                status = "healthy"
                message = (
                    f"Awaiting next round — last recorded round #{round_label} finished {human_delta} ago "
                    f"({timestamp_label})"
                )

        return NetworkStatus(
            status=status,
            message=message,
            lastChecked=now.isoformat(),
            activeValidators=len(validators),
            networkLatency=average_latency,
        )

    async def recent_activity(self, limit: int) -> List[RecentActivity]:
        rounds = await self._recent_rounds(limit=limit)
        activities: List[RecentActivity] = []
        for round_obj in rounds:
            if not round_obj.winners:
                continue
            winner = round_obj.winners[0]
            miner_uid = winner.get("miner_uid", 0)
            activity = RecentActivity(
                id=f"round_{round_obj.validator_round_id}_winner",
                type="task_completed",
                message=f"Miner {miner_uid} completed top task in round {round_obj.validator_round_id}",
                timestamp=_timestamp(round_obj.ended_at or round_obj.started_at),
                metadata=ActivityMetadata(
                    validatorId=str(winner.get("validator_uid")),
                    taskId=str(winner.get("task_id")),
                    score=float(winner.get("score", 0.0)),
                    roundId=round_obj.validator_round_id,
                ),
            )
            activities.append(activity)
        return activities[:limit]

    async def performance_trends(self, days: int) -> List[PerformanceTrend]:
        rounds = await self._recent_rounds(limit=days)
        trends: List[PerformanceTrend] = []
        for round_obj in rounds:
            average_score = 0.0
            if round_obj.winners:
                scores = [winner.get("score", 0.0) for winner in round_obj.winners]
                average_score = sum(scores) / len(scores) if scores else 0.0
            trend = PerformanceTrend(
                date=datetime.fromtimestamp(
                    round_obj.started_at or datetime.now(timezone.utc).timestamp(),
                    tz=timezone.utc,
                ).strftime("%Y-%m-%d"),
                averageScore=round(average_score, 3),
                totalTasks=round_obj.n_tasks,
                activeValidators=len(round_obj.validators),
            )
            trends.append(trend)
        trends.sort(key=lambda t: t.date)
        return trends

    async def _recent_round_records(
        self,
        limit: int = 20,
        include_details: bool = False,
    ) -> List[Tuple[RoundRecord, List[AgentRunContext]]]:
        stmt = (
            select(RoundORM)
            .options(
                selectinload(RoundORM.validator_snapshots),
                selectinload(RoundORM.miner_snapshots),
            )
            .order_by(RoundORM.id.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = await self.session.scalars(stmt)
        records: List[Tuple[RoundRecord, List[AgentRunContext]]] = []
        for row in rows:
            try:
                round_model = self.rounds_service._deserialize_round(row)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to parse round %s: %s", row.validator_round_id, exc
                )
                continue

            if round_model.started_at is None:
                logger.debug(
                    "Skipping round %s due to missing started_at timestamp",
                    row.validator_round_id,
                )
                continue

            record = RoundRecord(row=row, model=round_model)
            contexts: List[AgentRunContext] = []
            try:
                contexts = await self.rounds_service.list_agent_run_contexts(
                    validator_round_id=row.validator_round_id,
                    include_details=include_details,
                    limit=None,
                    skip=0,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to load agent run contexts for %s: %s",
                    row.validator_round_id,
                    exc,
                )
            if contexts:
                self.rounds_service._recalculate_round_from_contexts(record, contexts)
            records.append((record, contexts))
        return records

    async def _recent_rounds(self, limit: int = 20) -> List[ValidatorRound]:
        records = await self._recent_round_records(limit=limit, include_details=False)
        return [record.model for record, _ in records]

    async def _total_websites(self) -> int:
        stmt = select(TaskORM)
        rows = await self.session.scalars(stmt)
        urls = set()
        for row in rows:
            data = row.data or {}
            url = data.get("url")
            if url:
                urls.add(url)
        return len(urls)

    async def _total_runs(self) -> int:
        stmt = select(AgentEvaluationRunORM.agent_run_id)
        rows = await self.session.scalars(stmt)
        return len(list(rows))

    def _derive_validator_status(
        self,
        current_record: Optional[RoundRecord],
        *,
        total_runs: int,
        successful_runs: int,
        has_scores: bool,
        seconds_since_activity: Optional[float],
    ) -> ValidatorStatusInfo:
        if current_record is None:
            return ValidatorStatusInfo.from_state(ValidatorState.NOT_STARTED)

        validator_round = current_record.model
        if validator_round.ended_at:
            return ValidatorStatusInfo.from_state(ValidatorState.FINISHED)

        state = ValidatorState.STARTING
        if total_runs > 0:
            state = ValidatorState.SENDING_TASKS
        if successful_runs > 0 or has_scores:
            state = ValidatorState.EVALUATING

        if seconds_since_activity is not None:
            if seconds_since_activity > 86400:
                state = ValidatorState.OFFLINE
            elif seconds_since_activity > 3600 and total_runs == 0:
                state = ValidatorState.WAITING

        return ValidatorStatusInfo.from_state(state)

    async def _average_score(self) -> float:
        stmt = select(EvaluationResultORM)
        rows = await self.session.scalars(stmt)
        scores = []
        for row in rows:
            data = row.data or {}
            score = data.get("final_score")
            if score is not None:
                try:
                    scores.append(float(score))
                except (TypeError, ValueError):
                    continue
        return sum(scores) / len(scores) if scores else 0.0

    async def _latest_evaluated_task_meta(
        self, validator_round_id: str
    ) -> Optional[Dict[str, Optional[str]]]:
        """Fetch latest evaluated task prompt + website + use case for a validator round.

        Returns a dict with keys: prompt, website, useCase.
        """
        stmt = (
            select(
                TaskORM.prompt,
                TaskORM.url,
                TaskORM.relevant_data,
                TaskORM.use_case,
            )
            .join(EvaluationResultORM, EvaluationResultORM.task_id == TaskORM.task_id)
            .where(EvaluationResultORM.validator_round_id == validator_round_id)
            .order_by(
                EvaluationResultORM.created_at.desc(), EvaluationResultORM.id.desc()
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if not row:
            return None
        prompt, url, relevant_data, use_case = row
        # Derive website: prefer relevant_data.website, fallback to url
        website = None
        if isinstance(relevant_data, dict):
            website = relevant_data.get("website") or None
        if not website:
            website = url
        # Derive use case name
        use_case_name = None
        if isinstance(use_case, dict):
            use_case_name = use_case.get("name") or None
        elif isinstance(use_case, str):
            use_case_name = use_case
        return {
            "prompt": prompt or None,
            "website": website or None,
            "useCase": use_case_name or None,
        }

    async def _aggregate_validators(self) -> Dict[str, Dict[str, Any]]:
        records_with_contexts = await self._recent_round_records(
            limit=200, include_details=False
        )
        aggregates: Dict[str, Dict[str, Any]] = {}

        # Determine the current (latest) round number present in DB
        round_numbers: List[int] = []
        for record, _ in records_with_contexts:
            num = record.model.round_number or _round_id_to_int(
                record.model.validator_round_id
            )
            if num:
                round_numbers.append(num)
        round_numbers = sorted(set(round_numbers), reverse=True)
        max_round_number = round_numbers[0] if round_numbers else 0

        if max_round_number == 0 and records_with_contexts:
            fallback_record = records_with_contexts[0][0]
            max_round_number = fallback_record.model.round_number or _round_id_to_int(
                fallback_record.model.validator_round_id
            )

        # Build helper maps:
        # - current_round_entries: entries for the latest round (used for live status)
        # - last_entry_by_uid: last participation for each validator (used for last seen round info)
        current_round_entries: Dict[int, Tuple[RoundRecord, List[AgentRunContext]]] = {}
        last_entry_by_uid: Dict[int, Tuple[RoundRecord, List[AgentRunContext]]] = {}
        for record, contexts in records_with_contexts:
            round_number = record.model.round_number or _round_id_to_int(
                record.model.validator_round_id
            )
            validator_uid = record.model.validator_uid or record.validator_uid
            if validator_uid is None:
                continue
            # Track most recent record per validator overall
            prev_last = last_entry_by_uid.get(validator_uid)
            prev_ts = (
                (prev_last[0].model.ended_at or prev_last[0].model.started_at or 0.0)
                if prev_last
                else -1
            )
            curr_ts = record.model.ended_at or record.model.started_at or 0.0
            if prev_last is None or curr_ts >= prev_ts:
                last_entry_by_uid[validator_uid] = (record, contexts)
            # Track latest record for the current round only
            if round_number == max_round_number:
                existing = current_round_entries.get(validator_uid)
                if existing is None or (record.model.started_at or 0.0) > (
                    existing[0].model.started_at or 0.0
                ):
                    current_round_entries[validator_uid] = (record, contexts)

        now_ts = datetime.now(timezone.utc).timestamp()
        meta_cache: Dict[str, Optional[Dict[str, Optional[str]]]] = {}

        # Limit expected validators to those who participated in recent rounds instead of a static directory.
        # Include validators from the current round and the last few completed rounds (configurable).
        lookback = settings.OVERVIEW_VALIDATORS_LOOKBACK_ROUNDS or 0
        if lookback < 0:
            lookback = 0
        recent_round_numbers = set(round_numbers[:lookback]) if round_numbers else set()
        recent_participant_uids: set[int] = set()
        for record, _ in records_with_contexts:
            rn = record.model.round_number or _round_id_to_int(
                record.model.validator_round_id
            )
            uid = record.model.validator_uid or record.validator_uid
            if uid is None:
                continue
            if rn in recent_round_numbers:
                recent_participant_uids.add(uid)

        known_validator_uids = (
            set(current_round_entries.keys()) | recent_participant_uids
        )

        for validator_uid in sorted(known_validator_uids):
            entry = current_round_entries.get(validator_uid)
            last_entry = last_entry_by_uid.get(validator_uid)

            current_record = entry[0] if entry else None
            current_contexts = entry[1] if entry else []

            display_record = current_record or (last_entry[0] if last_entry else None)
            display_round = display_record.model if display_record else None
            validator_info = (
                getattr(display_round, "validator_info", None)
                if display_round
                else None
            )

            contexts_flat = current_contexts

            # Use last participation for display of last seen round when not currently running
            if last_entry is not None:
                round_number = last_entry[0].model.round_number or _round_id_to_int(
                    last_entry[0].model.validator_round_id
                )
            else:
                round_number = max_round_number or None

            display_name = (
                validator_info.name if validator_info and validator_info.name else None
            )
            if not display_name and display_round:
                display_name = (
                    getattr(display_round, "metadata", {}).get("validator_name") or None
                )
            if not display_name:
                display_name = f"Validator {validator_uid}"

            hotkey_candidates = []
            if validator_info and validator_info.hotkey:
                hotkey_candidates.append(validator_info.hotkey)
            if display_round and display_round.validator_hotkey:
                hotkey_candidates.append(display_round.validator_hotkey)
            if current_record and current_record.model.validator_hotkey:
                hotkey_candidates.append(current_record.model.validator_hotkey)
            hotkey = next(
                (candidate for candidate in hotkey_candidates if candidate), None
            )

            existing_icon = (
                getattr(validator_info, "image_url", None) if validator_info else None
            )
            icon = resolve_validator_image(display_name, existing=existing_icon)

            stake_value: float = 0.0
            if validator_info and validator_info.stake is not None:
                try:
                    stake_value = float(validator_info.stake)
                except (TypeError, ValueError):
                    stake_value = 0.0

            trust_value: float = 0.0
            if validator_info and validator_info.vtrust is not None:
                try:
                    trust_value = float(validator_info.vtrust)
                except (TypeError, ValueError):
                    trust_value = 0.0

            version = None
            if validator_info and validator_info.version:
                try:
                    version = int(str(validator_info.version).split(".")[0])
                except ValueError:
                    version = None

            if display_round:
                total_tasks = display_round.n_tasks or 0
                completed_tasks = self.rounds_service._estimate_completed_tasks(
                    display_round
                )
                validator_round_id = display_round.validator_round_id
            else:
                total_tasks = 0
                completed_tasks = 0
                validator_round_id = None

            total_runs = len(contexts_flat)
            successful_runs = len(
                [
                    ctx
                    for ctx in contexts_flat
                    if self.rounds_service._context_score(ctx) >= 0.5
                ]
            )
            has_scores = any(
                self.rounds_service._context_score(ctx) > 0.0 for ctx in contexts_flat
            )

            last_activity_candidates: List[float] = []
            # Prefer current round activity; otherwise fall back to last participation
            source_entry = entry or last_entry
            if source_entry:
                record_ref, ctx_list = source_entry
                round_model = record_ref.model
                if round_model.ended_at:
                    last_activity_candidates.append(round_model.ended_at)
                if round_model.started_at:
                    last_activity_candidates.append(round_model.started_at)
                for context in ctx_list:
                    if context.run.ended_at:
                        last_activity_candidates.append(context.run.ended_at)
                    elif context.run.started_at:
                        last_activity_candidates.append(context.run.started_at)

            last_activity_ts = (
                max(last_activity_candidates) if last_activity_candidates else None
            )
            seconds_since_activity = (
                max(0.0, now_ts - last_activity_ts)
                if last_activity_ts is not None
                else None
            )

            validator_round = current_record.model if current_record else None

            status_info = self._derive_validator_status(
                current_record,
                total_runs=total_runs,
                successful_runs=successful_runs,
                has_scores=has_scores,
                seconds_since_activity=seconds_since_activity,
            )
            status = status_info.label
            current_task = status_info.default_task

            cache_key = validator_round.validator_round_id if validator_round else None
            current_website: Optional[str] = None
            current_use_case: Optional[str] = None
            if cache_key and status_info.requires_prompt:
                if cache_key not in meta_cache:
                    meta_cache[cache_key] = await self._latest_evaluated_task_meta(
                        cache_key
                    )
                meta = meta_cache.get(cache_key)
                if meta:
                    if meta.get("prompt"):
                        current_task = meta.get("prompt") or current_task
                    current_website = meta.get("website") or None
                    current_use_case = meta.get("useCase") or None

            uptime = round(
                min(
                    100.0,
                    (completed_tasks / total_tasks * 100.0) if total_tasks else 0.0,
                ),
                1,
            )

            emission_value: Optional[int] = None
            if isinstance(stake_value, (int, float)):
                emission_value = int(float(stake_value) * 0.05)

            key = f"validator-{validator_uid}"
            aggregates[key] = {
                "id": key,
                "name": display_name,
                "hotkey": hotkey,
                "icon": icon,
                "currentTask": current_task,
                "currentWebsite": current_website,
                "currentUseCase": current_use_case,
                "status": status,
                "statusCode": status_info.state.value,
                "totalTasks": int(total_tasks),
                "weight": stake_value,
                "trust": trust_value,
                "version": version,
                "lastSeen": _timestamp(last_activity_ts),
                "stake": stake_value,
                "emission": emission_value,
                "uptime": uptime,
                "completedTasks": int(completed_tasks),
                "validatorRoundId": validator_round_id,
                "roundNumber": round_number,
                "validatorUid": validator_uid,
            }

        return aggregates

    def _round_to_info(self, round_obj: ValidatorRound, current: bool) -> RoundInfo:
        total_tasks = round_obj.n_tasks
        completed_tasks = len(round_obj.winners or [])
        average_score = 0.0
        top_score = 0.0
        if round_obj.winners:
            scores = [winner.get("score", 0.0) for winner in round_obj.winners]
            if scores:
                average_score = sum(scores) / len(scores)
                top_score = max(scores)

        derived_status = round_obj.status or ("active" if current else "finished")
        if round_obj.ended_at:
            derived_status = "finished"
        elif not round_obj.ended_at and current:
            derived_status = "active"

        return RoundInfo(
            id=int(
                round_obj.round_number or _round_id_to_int(round_obj.validator_round_id)
            ),
            startBlock=round_obj.start_block,
            endBlock=round_obj.end_block or round_obj.start_block + 360,
            current=current,
            startTime=_timestamp(round_obj.started_at),
            endTime=_timestamp(round_obj.ended_at) if round_obj.ended_at else None,
            status=derived_status,
            totalTasks=total_tasks,
            completedTasks=completed_tasks,
            averageScore=round(average_score, 3),
            topScore=round(top_score, 3),
        )
