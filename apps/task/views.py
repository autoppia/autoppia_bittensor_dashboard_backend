# apps/task/views.py

from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from datetime import datetime, timezone, timedelta
from apps.database.mongo_service import MongoService


class TaskViewSet(viewsets.ViewSet):
    """
    API endpoints to list, create (single & bulk), and filter task logs.
    Uses the database defined by settings.MONGO_TASK_DB_NAME.
    """

    db = MongoService.db(settings.MONGO_DB_NAME)

    def create(self, request):
        """POST /tasks/  → Insert a single task record."""
        task_data = self._extract_task_data(request.data)
        insert_result = self._insert_task_in_db(task_data)
        if not insert_result.acknowledged:
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
        Recibe {"tasks": [ {...}, {...}, ... ]}
        Inserta todos los registros en un solo batch y devuelve un informe por cada uno.
        """
        tasks = request.data.get("tasks", [])
        results = []
        for idx, payload in enumerate(tasks):
            try:
                task_data = self._extract_task_data(payload)
                ins = self._insert_task_in_db(task_data)
                if not ins.acknowledged:
                    raise RuntimeError("insert failed")
                self._upsert_metric(task_data)
                results.append(
                    {"index": idx, "task_id": payload.get("task_id"), "status": "ok"}
                )
            except Exception as e:
                results.append(
                    {
                        "index": idx,
                        "task_id": payload.get("task_id"),
                        "status": "error",
                        "detail": str(e),
                    }
                )
        return Response({"results": results}, status=status.HTTP_207_MULTI_STATUS)

    @action(detail=False, url_path="filtered", methods=["get"])
    def filtered_tasks(self, request):
        """GET /tasks/filtered/?period=...&websites=...  → Filtered aggregated metrics."""
        period = request.GET.get("period", "All")
        websites = [w for w in request.GET.get("websites", "").split(",") if w]
        pipeline = self._build_filtered_pipeline(period, websites)
        tasks = self.db["tasks"].aggregate(pipeline)
        return Response(list(tasks))

    # —— Private helper methods —— #

    def _extract_task_data(self, data):
        """Normalize incoming payload (dict) into the stored document."""
        now_ts = datetime.now(timezone.utc).timestamp()
        return {
            "validator_uid": data.get("validator_uid"),
            "miner_uid": data.get("miner_uid"),
            "miner_hotkey": data.get("miner_hotkey"),
            "task_id": data.get("task_id"),
            "success": data.get("success"),
            "score": data.get("score"),
            "duration": data.get("duration"),
            "website": data.get("website"),
            "created_at": now_ts,
        }

    def _insert_task_in_db(self, task_data):
        return self.db["tasks"].insert_one(task_data)

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

        # Recalculate averages and success rate
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
        """Update existing metric or create a new one."""
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
