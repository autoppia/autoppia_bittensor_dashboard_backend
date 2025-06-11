# apps/task/views.py

from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from datetime import datetime, timezone, timedelta

from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.database.mongo_service import MongoService


@dataclass
class LeaderboardTaskRecord:
    validator_uid: int
    miner_uid: int
    miner_hotkey: str
    task_id: str
    website: str
    success: bool = False
    score: float = 0.0
    duration: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LeaderboardTaskRecord":
        return cls(
            validator_uid=data["validator_uid"],
            miner_uid=data["miner_uid"],
            miner_hotkey=data["miner_hotkey"],
            task_id=data["task_id"],
            website=data["website"],
            success=data.get("success", False),
            score=data.get("score", 0.0),
            duration=data.get("duration", 0.0),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = datetime.now(timezone.utc).timestamp()
        return d


class TaskViewSet(viewsets.ViewSet):
    """
    API endpoints to create (single & bulk) and filter task logs.
    Uses the database defined by settings.MONGO_TASK_DB_NAME.
    """

    db = MongoService.db(settings.MONGO_DB_NAME)

    def create(self, request):
        """POST /tasks/ → Insert a single task record."""
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

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk_create(self, request):
        """
        POST /tasks/bulk/
        Recibe siempre un array JSON de LeaderboardTaskRecord:
          [
            {validator_uid:…, miner_uid:…, …},
            {…},
            …
          ]
        Inserta todos los registros en un batch y devuelve un informe por cada uno.
        """
        results = []
        for idx, item in enumerate(request.data or []):
            try:
                record = LeaderboardTaskRecord.from_dict(item)
                task_data = record.to_dict()

                ins = self.db["tasks"].insert_one(task_data)
                if not ins.acknowledged:
                    raise RuntimeError("insert failed")

                self._upsert_metric(task_data)
                results.append(
                    {"index": idx, "task_id": record.task_id, "status": "ok"}
                )
            except Exception as e:
                results.append(
                    {
                        "index": idx,
                        "task_id": item.get("task_id"),
                        "status": "error",
                        "detail": str(e),
                    }
                )

        return Response({"results": results}, status=status.HTTP_207_MULTI_STATUS)

    @action(detail=False, methods=["get"], url_path="filtered")
    def filtered_tasks(self, request):
        """GET /tasks/filtered/?period=...&websites=... → Filtered aggregated metrics."""
        period = request.GET.get("period", "All")
        websites = [w for w in request.GET.get("websites", "").split(",") if w]
        pipeline = self._build_filtered_pipeline(period, websites)
        tasks = self.db["tasks"].aggregate(pipeline)
        return Response(list(tasks))

    # ——— Private helpers ——— #

    def _fetch_metric_from_db(self, miner_uid):
        return self.db["metrics"].find_one({"miner_uid": miner_uid})

    def _update_metric_in_db(self, metric, task_data):
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

    def _build_filtered_pipeline(self, period, websites):
        now = datetime.now(timezone.utc)
        if period == "Day":
            start = now - timedelta(days=1)
        elif period == "Week":
            start = now - timedelta(days=7)
        elif period == "Month":
            start = now - timedelta(days=30)
        else:
            start = None

        match = {}
        if start:
            match["created_at"] = {"$gte": start.timestamp()}
        if websites:
            match["website"] = {"$in": websites}

        return [
            {"$match": match},
            {
                "$group": {
                    "_id": {
                        "miner_uid": "$miner_uid",
                        "miner_hotkey": "$miner_hotkey",
                        "validator_uid": "$validator_uid",
                    },
                    "score": {"$avg": "$score"},
                    "duration": {"$avg": "$duration"},
                }
            },
            {
                "$group": {
                    "_id": {
                        "miner_uid": "$_id.miner_uid",
                        "miner_hotkey": "$_id.miner_hotkey",
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
            {
                "$addFields": {
                    "scores_per_validator": {"$arrayToObject": "$scores_per_validator"},
                    "durations_per_validator": {
                        "$arrayToObject": "$durations_per_validator"
                    },
                }
            },
            {
                "$project": {
                    "miner_uid": "$_id.miner_uid",
                    "miner_hotkey": "$_id.miner_hotkey",
                    "scores_per_validator": 1,
                    "durations_per_validator": 1,
                    "score_avg": {"$round": ["$score_avg", 3]},
                    "duration_avg": {"$round": ["$duration_avg", 0]},
                    "_id": 0,
                }
            },
            {"$sort": {"miner_uid": 1}},
        ]
