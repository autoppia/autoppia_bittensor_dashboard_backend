from django.conf import settings
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from pymongo import MongoClient

class MetricViewSet(viewsets.ViewSet):
    mongo_connection_uri = settings.MONGO_CONNECTION_URI
    mongo_client = MongoClient(mongo_connection_uri)
    mongo_database = mongo_client["autoppia"]

    def list(self, request):
        try:
            metrics = self.mongo_database["metrics"].find()
            return Response(list(metrics))
        except Exception as e:
            return Response({"error": str(e)}, status=500)

    def retrieve(self, request, pk=None):
        try:
            metric = self.mongo_database["metrics"].find_one({"miner_uid": pk})
            return Response(metric)
        except Exception as e:
            return Response({"error": str(e)}, status=500)

