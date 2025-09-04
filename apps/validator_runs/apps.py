from django.apps import AppConfig
from django.conf import settings
from apps.database.mongo_service import MongoService


class ValidatorRunsConfig(AppConfig):
    name = "apps.validator_runs"

    def ready(self):
        # Crear índices al arrancar el server (idempotente)
        db = MongoService.db(settings.MONGO_DB_NAME)
        info = db["validator_runs"]
        events = db["validator_run_events"]

        # validator_info: unique por validator_id
        info.create_index("validator_id", unique=True)
        info.create_index("updated_at")
        info.create_index("created_at")

        # validator_events: consultas rápidas para dashboard
        events.create_index([("validator_id", 1), ("ts", -1)])
        events.create_index([("forward_seq", 1), ("ts", 1)])
        events.create_index([("phase", 1), ("ts", -1)])
        # TTL opcional (p. ej. 30 días) ← SOLO si ts es datetime real, no string:
        # events.create_index("ts", expireAfterSeconds=30*24*3600)
