# apps/metric/views.py

from django.conf import settings
from rest_framework import viewsets
from rest_framework.response import Response
from apps.database.mongo_service import MongoService


class MetricViewSet(viewsets.ViewSet):
    """
    API endpoints to list and retrieve aggregated metrics.
    Uses the database defined by settings.MONGO_METRIC_DB_NAME.
    """

    db = MongoService.db(settings.MONGO_DB_NAME)

    def list(self, request):
        """Return all miner metrics."""
        projection = {
            "miner_uid": 1,
            "miner_hotkey": 1,
            "scores_per_validator": 1,
            "durations_per_validator": 1,
            "score_avg": 1,
            "duration_avg": 1,
            "successful_tasks": 1,
            "total_tasks": 1,
            "success_rate": 1,
            "_id": 0,
        }
        metrics = self.db["metrics"].find({}, projection).sort("miner_uid", 1)
        return Response(list(metrics))

    def retrieve(self, request, pk=None):
        """Return the metric document for a single miner_uid."""
        metric = self.db["metrics"].find_one({"miner_uid": pk}, {"_id": 0})
        return Response(metric)
