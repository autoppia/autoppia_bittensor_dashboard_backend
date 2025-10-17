from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.data import get_validator_metadata
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
from app.utils.images import resolve_validator_image

logger = logging.getLogger(__name__)


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

    async def overview_metrics(self) -> OverviewMetrics:
        rounds = await self._recent_rounds()
        if not rounds:
            now_iso = datetime.now(timezone.utc).isoformat()
            return OverviewMetrics(
                topScore=0.0,
                totalWebsites=0,
                totalValidators=0,
                totalMiners=0,
                currentRound=0,
                subnetVersion="1.0.0",
                lastUpdated=now_iso,
            )

        top_score = 0.0
        validators: set[int] = set()
        miners: set[int] = set()
        current_round = max(_round_id_to_int(round.validator_round_id) for round in rounds)
        subnet_version = "1.0.0"

        active_rounds: List[ValidatorRound] = []
        completed_rounds: List[ValidatorRound] = []
        for round_obj in rounds:
            if round_obj.ended_at:
                completed_rounds.append(round_obj)
            else:
                active_rounds.append(round_obj)
            validators.update(validator.uid for validator in round_obj.validators)
            miners.update(miner.uid for miner in round_obj.miners if miner.uid is not None)
            if round_obj.winners:
                round_top = max(winner.get("score", 0.0) for winner in round_obj.winners)
                top_score = max(top_score, round_top)
            if round_obj.validators and round_obj.validators[0].version:
                subnet_version = round_obj.validators[0].version

        total_websites = await self._total_websites()

        return OverviewMetrics(
            topScore=round(top_score, 3),
            totalWebsites=total_websites,
            totalValidators=len(validators),
            totalMiners=len(miners),
            currentRound=current_round,
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

    async def rounds_list(self, page: int, limit: int, status: Optional[str]) -> Tuple[List[RoundInfo], Optional[RoundInfo], int]:
        rounds = await self._recent_rounds(limit=100)
        active_rounds = [round_obj for round_obj in rounds if not round_obj.ended_at]
        completed_rounds = [round_obj for round_obj in rounds if round_obj.ended_at]

        completed_rounds.sort(key=lambda r: r.started_at or 0, reverse=True)

        round_infos = [self._round_to_info(round_obj, current=False) for round_obj in completed_rounds]

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

        current_round = self._round_to_info(current_round_obj, current=True) if current_round_obj else None
        return paginated, current_round, total

    async def round_detail(self, identifier: str) -> RoundInfo:
        if identifier.isdigit():
            validator_round_id = f"round_{identifier.zfill(3)}"
        else:
            validator_round_id = identifier

        stmt = select(RoundORM).where(RoundORM.validator_round_id == validator_round_id)
        row = await self.session.scalar(stmt)
        if not row:
            raise ValueError(f"Round {identifier} not found")
        data = dict(row.data or {})
        data.setdefault("validator_round_id", row.validator_round_id)
        return self._round_to_info(ValidatorRound(**data), current=False)

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

        rounds = await self._recent_rounds(limit=0 if unlimited else (derived_limit or 30))
        if not rounds:
            now_iso = datetime.now(timezone.utc).isoformat()
            return [], {"start": now_iso, "end": now_iso}

        round_ids = [round_obj.validator_round_id for round_obj in rounds]
        stmt = select(AgentEvaluationRunORM).where(
            AgentEvaluationRunORM.validator_round_id.in_(round_ids)
        )
        run_rows = await self.session.scalars(stmt)

        runs_by_round: Dict[str, List[AgentEvaluationRunORM]] = defaultdict(list)
        for run_row in run_rows:
            runs_by_round[run_row.validator_round_id].append(run_row)

        def _scores_for_provider(
            run_list: List[AgentEvaluationRunORM], provider_tokens: List[str]
        ) -> List[float]:
            scores: List[float] = []
            for row in run_list:
                data = row.data or {}
                miner_info = data.get("miner_info") or {}
                provider = str(miner_info.get("provider") or miner_info.get("agent_name") or "").lower()
                if provider and any(token in provider for token in provider_tokens):
                    score = data.get("avg_eval_score") or data.get("average_score")
                    if score is not None:
                        try:
                            scores.append(float(score))
                        except (TypeError, ValueError):
                            continue
            return scores

        total_rounds = len(rounds)
        entries: List[LeaderboardEntry] = []
        timestamps: List[str] = []
        for idx, round_obj in enumerate(rounds):
            round_runs = runs_by_round.get(round_obj.validator_round_id, [])
            run_scores: List[float] = []
            for row in round_runs:
                data = row.data or {}
                score = data.get("avg_eval_score") or data.get("average_score")
                if score is not None:
                    try:
                        run_scores.append(float(score))
                    except (TypeError, ValueError):
                        continue

            average_score = (
                round_obj.average_score
                if round_obj.average_score is not None
                else (sum(run_scores) / len(run_scores) if run_scores else 0.0)
            )

            openai_scores = _scores_for_provider(round_runs, ["openai"])
            anthropic_scores = _scores_for_provider(round_runs, ["anthropic"])
            browser_scores = _scores_for_provider(round_runs, ["browser"])

            def _avg(values: List[float]) -> float:
                return round(sum(values) / len(values), 3) if values else 0.0

            timestamp = datetime.fromtimestamp(
                round_obj.started_at or datetime.now(timezone.utc).timestamp(),
                tz=timezone.utc,
            ).isoformat()
            timestamps.append(timestamp)

            round_number = round_obj.round_number or _round_id_to_int(round_obj.validator_round_id)
            if not round_number or round_number <= 0:
                round_number = total_rounds - idx

            entries.append(
                LeaderboardEntry(
                    round=round_number,
                    subnet36=round(average_score, 3),
                    openai_cua=_avg(openai_scores) or round(average_score, 3),
                    anthropic_cua=_avg(anthropic_scores) or round(average_score, 3),
                    browser_use=_avg(browser_scores) or round(average_score, 3),
                    timestamp=timestamp,
                )
            )

        entries.sort(key=lambda entry: entry.timestamp, reverse=True)
        start = min(timestamps)
        end = max(timestamps)
        return entries, {"start": start, "end": end}

    async def statistics(self) -> SubnetStatistics:
        validators = await self._aggregate_validators()
        total_stake = sum(int(entry["stake"]) for entry in validators.values())
        total_emission = sum(int(entry["emission"]) for entry in validators.values())
        total_runs = await self._total_runs()
        success_total = int(total_runs * 0.85)

        average_trust = (
            sum(float(entry["trust"]) for entry in validators.values()) / len(validators)
            if validators
            else 0.0
        )
        average_uptime = (
            sum(float(entry.get("uptime", 0.0)) for entry in validators.values()) / len(validators)
            if validators
            else 0.0
        )

        average_score = await self._average_score()
        total_tasks_completed = sum(entry["completedTasks"] for entry in validators.values())

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

        if not rounds:
            return NetworkStatus(
                status="down",
                message="No round activity recorded yet",
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

        if not last_activity_ts:
            status = "degraded"
            message = "Latest round is missing timing data"
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
                last_activity_dt.strftime("%Y-%m-%d %H:%M UTC") if last_activity_dt else "unknown time"
            )
            round_label = _round_id_to_int(last_round.validator_round_id)

            if delta <= 900:
                status = "healthy"
                message = "Rounds are completing normally"
            elif delta <= 3600:
                status = "degraded"
                message = f"Last round completed {human_delta} ago ({timestamp_label})"
            elif len(validators) == 0:
                status = "down"
                message = "No active validators detected"
            else:
                if delta_hours < 24:
                    status = "healthy"
                    message = (
                        f"Awaiting next round — last recorded round #{round_label} finished {human_delta} ago "
                        f"({timestamp_label})"
                    )
                else:
                    status = "degraded"
                    message = (
                        f"No rounds recorded in the past {human_delta} — last known round #{round_label} "
                        f"completed at {timestamp_label}"
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
                date=datetime.fromtimestamp(round_obj.started_at or datetime.now(timezone.utc).timestamp(), tz=timezone.utc).strftime("%Y-%m-%d"),
                averageScore=round(average_score, 3),
                totalTasks=round_obj.n_tasks,
                activeValidators=len(round_obj.validators),
            )
            trends.append(trend)
        trends.sort(key=lambda t: t.date)
        return trends

    async def _recent_rounds(self, limit: int = 20) -> List[ValidatorRound]:
        stmt = select(RoundORM).order_by(RoundORM.id.desc())
        if limit:
            stmt = stmt.limit(limit)
        rows = await self.session.scalars(stmt)
        rounds: List[ValidatorRound] = []
        for row in rows:
            data = dict(row.data or {})
            data.setdefault("validator_round_id", row.validator_round_id)
            if data.get("started_at") is None:
                logger.debug(
                    "Skipping round %s due to missing started_at timestamp",
                    row.validator_round_id,
                )
                continue
            try:
                rounds.append(ValidatorRound(**data))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse round %s: %s", row.validator_round_id, exc)
        return rounds

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

    async def _count_tasks(self, validator_round_id: str) -> int:
        stmt = select(func.count(TaskORM.id)).where(TaskORM.validator_round_id == validator_round_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _count_agent_runs(self, validator_round_id: str) -> int:
        stmt = select(func.count(AgentEvaluationRunORM.id)).where(
            AgentEvaluationRunORM.validator_round_id == validator_round_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _count_evaluations(self, validator_round_id: str) -> int:
        stmt = select(func.count(EvaluationResultORM.id)).where(
            EvaluationResultORM.validator_round_id == validator_round_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _aggregate_validators(self) -> Dict[str, Dict[str, Any]]:
        rounds = await self._recent_rounds(limit=100)
        aggregates: Dict[str, Dict[str, Any]] = {}
        validator_rounds: Dict[str, List[ValidatorRound]] = defaultdict(list)

        for round_obj in rounds:
            for validator in round_obj.validators:
                key = f"validator_{validator.uid}"
                validator_rounds[key].append(round_obj)

        for key, round_list in validator_rounds.items():
            if not round_list:
                continue
            first_round = round_list[0]
            validator_meta = None
            validator_uid = int(key.split("_", 1)[1])

            for validator in first_round.validators:
                if validator.uid == validator_uid:
                    validator_meta = validator
                    break

            metadata = get_validator_metadata(validator_uid)
            total_tasks = sum(round_obj.n_tasks for round_obj in round_list)
            completed_tasks = sum(len(round_obj.winners or []) for round_obj in round_list)
            stake_from_round = validator_meta.stake if validator_meta else None
            weight = (
                stake_from_round
                if stake_from_round not in (None, 0, 0.0)
                else float(metadata.get("stake") or 0.0)
            )
            trust_from_round = validator_meta.vtrust if validator_meta else None
            trust = (
                trust_from_round
                if trust_from_round not in (None, 0, 0.0)
                else float(metadata.get("vtrust") or 0.0)
            )
            version = validator_meta.version if validator_meta and validator_meta.version else "1.0.0"
            last_round = max(round_list, key=lambda r: r.ended_at or r.started_at or 0)
            last_seen = _timestamp(last_round.ended_at or last_round.started_at)
            validator_round_id = getattr(last_round, "validator_round_id", None)

            now_ts = datetime.now(timezone.utc).timestamp()
            completion_rate = (completed_tasks / total_tasks) if total_tasks else 0.0
            last_activity_ts = last_round.ended_at or last_round.started_at
            seconds_since_activity = (
                max(0.0, now_ts - last_activity_ts) if last_activity_ts else None
            )

            tasks_recorded = await self._count_tasks(validator_round_id) if validator_round_id else 0
            agent_runs_recorded = await self._count_agent_runs(validator_round_id) if validator_round_id else 0
            evaluations_recorded = await self._count_evaluations(validator_round_id) if validator_round_id else 0

            status = "Finished"
            round_state = (last_round.status or "").lower()
            if not last_round.ended_at and round_state != "completed":
                status = "Starting"
                if tasks_recorded > 0:
                    status = "Sending Tasks"
                if agent_runs_recorded > 0:
                    status = "Evaluating"
                if seconds_since_activity is not None and seconds_since_activity > 86400:
                    status = "Offline"
                elif (
                    seconds_since_activity is not None
                    and seconds_since_activity > 3600
                    and tasks_recorded == 0
                ):
                    status = "Waiting"

            current_task = {
                "Starting": "Validator connected – awaiting task upload",
                "Sending Tasks": "Distributing tasks to agent runs",
                "Evaluating": "Evaluating miner submissions",
                "Waiting": "Awaiting next action",
                "Offline": "No activity detected recently",
                "Finished": "Round completed",
            }.get(status, "Validator activity")

            display_name = metadata.get("name") or (validator_meta.name if validator_meta else f"Validator {validator_uid}")
            existing_icon = metadata.get("image")
            icon = resolve_validator_image(display_name, existing=existing_icon)

            aggregates[key] = {
                "id": key,
                "name": display_name,
                "hotkey": metadata.get("hotkey") or (validator_meta.hotkey if validator_meta else ""),
                "icon": icon,
                "currentTask": current_task,
                "status": status,
                "totalTasks": total_tasks,
                "weight": weight,
                "trust": trust,
                "version": int(version.split(".")[0]) if version else 1,
                "lastSeen": last_seen,
                "stake": int(weight),
                "emission": int(weight * 0.05),
                "uptime": round(min(100.0, completion_rate * 100), 1) if total_tasks else 0.0,
                "completedTasks": completed_tasks,
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

        derived_status = round_obj.status or ("active" if current else "completed")
        if round_obj.ended_at:
            derived_status = "finished"
        elif not round_obj.ended_at and current:
            derived_status = "active"

        return RoundInfo(
            id=int(round_obj.round_number or _round_id_to_int(round_obj.validator_round_id)),
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
