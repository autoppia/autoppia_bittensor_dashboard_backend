from datetime import datetime, timezone
from typing import Dict, Any
import logging, traceback

from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.database.mongo_service import MongoService

logger = logging.getLogger(__name__)


class ValidatorRunViewSet(viewsets.ViewSet):
    db = MongoService.db(settings.MONGO_DB_NAME)

    # POST /validator-runs/info/
    @action(detail=False, methods=["post"], url_path="info")
    def upsert_info(self, request):
        body: Dict[str, Any] = request.data or {}
        validator_id = body.get("validator_id")
        if validator_id is None:
            return Response(
                {"error": "validator_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Nunca enviamos _id → lo autogenera Mongo
        now = datetime.now(timezone.utc)

        # Campos que permitimos actualizar:
        allowed = {
            "validator_id": validator_id,
            "address": body.get("address"),
            "hotkey": body.get("hotkey"),
            "coldkey": body.get("coldkey"),
            "version": body.get("version"),
            "llm": body.get("llm"),
            "evaluator": body.get("evaluator"),
            "operator": body.get("operator"),
            "demo_webs": body.get("demo_webs"),
            "updated_at": now,
        }

        try:
            res = self.db["validator_info"].update_one(
                {"validator_id": validator_id},
                {
                    # setOnInsert solo se aplica si NO existe → crea created_at
                    "$setOnInsert": {"created_at": now},
                    "$set": allowed,
                },
                upsert=True,
            )
            return Response(
                {
                    "ok": True,
                    "validator_id": validator_id,
                    "upserted_id": str(res.upserted_id) if res.upserted_id else None,
                    "matched": res.matched_count,
                    "modified": res.modified_count,
                }
            )
        except Exception:
            logger.error(f"[upsert_info] exception:\n{traceback.format_exc()}")
            return Response(
                {"error": "internal error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # POST /validator-runs/events/
    @action(detail=False, methods=["post"], url_path="events")
    def create_event(self, request):
        body: Dict[str, Any] = request.data or {}
        validator_id = body.get("validator_id")
        if validator_id is None:
            return Response(
                {"error": "validator_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        doc = {
            "validator_id": validator_id,
            "forward_seq": body.get("forward_seq"),
            "phase": body.get("phase") or "INFO",
            "message": body.get("message") or "",
            "run_id": body.get("run_id"),
            "extra": body.get("extra") or {},
            # sólo created_at; los eventos son append-only
            "created_at": datetime.now(timezone.utc),
        }

        try:
            ins = self.db["validator_events"].insert_one(doc)
            return Response({"ok": True, "inserted_id": str(ins.inserted_id)})
        except Exception:
            logger.error(f"[create_event] exception:\n{traceback.format_exc()}")
            return Response(
                {"error": "internal error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # GET /validator-runs/
    def list(self, request):
        projection = {
            "_id": 0,
            "validator_id": 1,
            "address": 1,
            "hotkey": 1,
            "coldkey": 1,
            "version": 1,
            "llm": 1,
            "evaluator": 1,
            "operator": 1,
            "demo_webs": 1,
            "created_at": 1,
            "updated_at": 1,
        }
        docs = self.db["validator_info"].find({}, projection).sort("validator_id", 1)
        return Response(list(docs))

    # GET /validator-runs/{validator_id}/
    def retrieve(self, request, pk=None):
        doc = self.db["validator_info"].find_one({"validator_id": int(pk)}, {"_id": 0})
        return Response(doc)

    # GET /validator-runs/{validator_id}/events/?limit=N
    @action(detail=True, methods=["get"], url_path="events")
    def list_events(self, request, pk=None):
        limit = int(request.GET.get("limit", 100))
        cur = (
            self.db["validator_events"]
            .find({"validator_id": int(pk)}, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        return Response(list(cur))
