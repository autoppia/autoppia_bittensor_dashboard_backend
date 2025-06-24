from dataclasses import dataclass, asdict
from typing import Dict, Any
from datetime import datetime, timezone, timedelta

from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.database.mongo_service import MongoService
import logging
import traceback

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
#  DATA CLASS
# ────────────────────────────────────────────────────────────────────────


@dataclass
class LeaderboardTaskRecord:
    """Single task execution reported by a miner and validated by a validator."""

    # Identifiers
    validator_uid: int
    miner_uid: int
    miner_hotkey: str
    miner_coldkey: str

    # Task data
    task_id: str
    task_prompt: str
    website: str
    use_case: str  # NEW – category / benchmark use-case
    actions: Dict[str, Any]  # JSON with miner steps / answer

    # Results
    success: bool = False
    score: float = 0.0
    duration: float = 0.0  # seconds

    # ——— factory & serializer ———
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LeaderboardTaskRecord":
        """Converts inbound JSON into a typed dataclass instance."""
        return cls(
            validator_uid=data["validator_uid"],
            miner_uid=data["miner_uid"],
            miner_hotkey=data["miner_hotkey"],
            miner_coldkey=data["miner_coldkey"],
            task_id=data["task_id"],
            task_prompt=data["task_prompt"],
            website=data["website"],
            use_case=data.get("use_case", ""),
            actions=data.get("actions", {}),
            success=data.get("success", False),
            score=data.get("score", 0.0),
            duration=data.get("duration", 0.0),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to plain dict and append a UTC creation timestamp."""
        d = asdict(self)
        d["created_at"] = datetime.now(timezone.utc).timestamp()
        return d


# ────────────────────────────────────────────────────────────────────────
#  VIEWSET
# ────────────────────────────────────────────────────────────────────────


class TaskViewSet(viewsets.ViewSet):
    """
    Endpoints for:
    • Inserting task logs (single & bulk)
    • Querying filtered aggregates (per-task granularity)

    Each stored document now contains:
    - miner_coldkey
    - use_case
    - actions
    """

    db = MongoService.db(settings.MONGO_DB_NAME)

    # ──────────────────────────── CREATE (single) ────────────────────────────
    def create(self, request):
        record = LeaderboardTaskRecord.from_dict(request.data)
        task_data = record.to_dict()

        ins = self.db["tasks"].insert_one(task_data)
        if not ins.acknowledged:
            return Response(
                {"error": "Failed to insert task into database"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        self._upsert_metric(task_data)
        return Response(
            {"message": "Task logged successfully"}, status=status.HTTP_201_CREATED
        )

    # ──────────────────────────── CREATE (bulk) ─────────────────────────────
    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk_create(self, request):
        logger.info(f"[bulk_create] payload received: {request.data!r}")
        results = []
        try:
            for idx, item in enumerate(request.data or []):
                record = LeaderboardTaskRecord.from_dict(item)
                task_data = record.to_dict()

                ins = self.db["tasks"].insert_one(task_data)
                if not ins.acknowledged:
                    raise RuntimeError("insert failed")

                self._upsert_metric(task_data)
                results.append(
                    {"index": idx, "task_id": record.task_id, "status": "ok"}
                )

            return Response({"results": results}, status=status.HTTP_207_MULTI_STATUS)
        except Exception:
            logger.error(f"[bulk_create] exception:\n{traceback.format_exc()}")
            return Response(
                {"error": "Internal server error during bulk insert"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ──────────────────────────── FILTERED LIST ─────────────────────────────
    @action(detail=False, methods=["get"], url_path="filtered")
    def filtered_tasks(self, request):
        """GET /tasks/filtered/?period=...&websites=... → aggregated stats."""
        period = request.GET.get("period", "All")  # All | Day | Week | Month
        websites = [w for w in request.GET.get("websites", "").split(",") if w]
        pipeline = self._build_filtered_pipeline(period, websites)
        return Response(list(self.db["tasks"].aggregate(pipeline)))

    # ──────────────────────────── METRIC HELPERS ───────────────────────────
    def _fetch_metric_from_db(self, miner_uid):
        return self.db["metrics"].find_one({"miner_uid": miner_uid})

    def _update_metric_in_db(self, metric: Dict[str, Any], task_data: Dict[str, Any]):
        vid = str(task_data["validator_uid"])
        key = f"validator_{vid}"
        score = task_data["score"]
        duration = task_data["duration"]

        if key in metric["tasks_per_validator"]:
            count = metric["tasks_per_validator"][key]
            total_score = metric["scores_per_validator"][key] * count + score
            total_duration = metric["durations_per_validator"][key] * count + duration
            metric["tasks_per_validator"][key] += 1
            metric["scores_per_validator"][key] = round(
                total_score / metric["tasks_per_validator"][key], 3
            )
            metric["durations_per_validator"][key] = round(
                total_duration / metric["tasks_per_validator"][key]
            )
        else:
            metric["tasks_per_validator"][key] = 1
            metric["scores_per_validator"][key] = score
            metric["durations_per_validator"][key] = duration

        metric["score_avg"] = round(
            sum(metric["scores_per_validator"].values())
            / len(metric["scores_per_validator"]),
            3,
        )
        metric["duration_avg"] = round(
            sum(metric["durations_per_validator"].values())
            / len(metric["durations_per_validator"])
        )
        metric["successful_tasks"] += 1 if task_data["success"] else 0
        metric["total_tasks"] += 1
        metric["success_rate"] = round(
            metric["successful_tasks"] / metric["total_tasks"], 3
        )

        return self.db["metrics"].replace_one(
            {"miner_uid": task_data["miner_uid"]}, metric
        )

    def _create_metric_in_db(self, metric, task_data):
        vid = str(task_data["validator_uid"])
        key = f"validator_{vid}"
        new_metric = {
            "miner_uid": task_data["miner_uid"],
            "miner_hotkey": task_data["miner_hotkey"],
            "miner_coldkey": task_data[
                "miner_coldkey"
            ],  # stored but not exposed in /metrics/
            "tasks_per_validator": {key: 1},
            "scores_per_validator": {key: task_data["score"]},
            "durations_per_validator": {key: task_data["duration"]},
            "successful_tasks": 1 if task_data["success"] else 0,
            "total_tasks": 1,
            "success_rate": 1 if task_data["success"] else 0,
        }
        if metric:
            return self.db["metrics"].replace_one(
                {"miner_uid": task_data["miner_uid"]}, new_metric
            )
        return self.db["metrics"].insert_one(new_metric)

    def _upsert_metric(self, task_data):
        metric = self._fetch_metric_from_db(task_data["miner_uid"])
        if metric and metric.get("miner_hotkey") == task_data["miner_hotkey"]:
            self._update_metric_in_db(metric, task_data)
        else:
            self._create_metric_in_db(metric, task_data)

    # ──────────────────────────── PIPELINE BUILDER ───────────────────────────
    def _build_filtered_pipeline(self, period: str, websites):
        """Mongo aggregation pipeline for /tasks/filtered/ endpoint."""
        now = datetime.now(timezone.utc)
        if period == "Day":
            start = now - timedelta(days=1)
        elif period == "Week":
            start = now - timedelta(days=7)
        elif period == "Month":
            start = now - timedelta(days=30)
        else:
            start = None

        match: Dict[str, Any] = {}
        if start:
            match["created_at"] = {"$gte": start.timestamp()}
        if websites:
            match["website"] = {"$in": websites}

        return [
            {"$match": match},
            # First level – per miner/validator
            {
                "$group": {
                    "_id": {
                        "miner_uid": "$miner_uid",
                        "miner_hotkey": "$miner_hotkey",
                        "miner_coldkey": "$miner_coldkey",
                        "validator_uid": "$validator_uid",
                    },
                    "score": {"$avg": "$score"},
                    "duration": {"$avg": "$duration"},
                }
            },
            # Second level – per miner (aggregate validators)
            {
                "$group": {
                    "_id": {
                        "miner_uid": "$_id.miner_uid",
                        "miner_hotkey": "$_id.miner_hotkey",
                        "miner_coldkey": "$_id.miner_coldkey",
                    },
                    "scores_per_validator": {
                        "$push": {
                            "k": {"$toString": "$_id.validator_uid"},
                            "v": "$score",
                        }
                    },
                    "durations_per_validator": {
                        "$push": {
                            "k": {"$toString": "$_id.validator_uid"},
                            "v": "$duration",
                        }
                    },
                    "score_avg": {"$avg": "$score"},
                    "duration_avg": {"$avg": "$duration"},
                }
            },
            # Convert arrays ➜ objects
            {
                "$addFields": {
                    "scores_per_validator": {"$arrayToObject": "$scores_per_validator"},
                    "durations_per_validator": {
                        "$arrayToObject": "$durations_per_validator"
                    },
                }
            },
            # Final projection and sorting
            {
                "$project": {
                    "_id": 0,
                    "miner_uid": "$_id.miner_uid",
                    "miner_hotkey": "$_id.miner_hotkey",
                    "miner_coldkey": "$_id.miner_coldkey",
                    "scores_per_validator": 1,
                    "durations_per_validator": 1,
                    "score_avg": {"$round": ["$score_avg", 3]},
                    "duration_avg": {"$round": ["$duration_avg", 0]},
                }
            },
            {"$sort": {"miner_uid": 1}},
        ]
