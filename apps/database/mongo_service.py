# apps/common/mongo_service.py

from django.conf import settings
from pymongo import MongoClient


class MongoService:
    """
    Servicio singleton MongoClient + acceso a bases dinámicas.
    """

    _client = None

    @classmethod
    def client(cls):
        if cls._client is None:
            cls._client = MongoClient(settings.MONGO_CONNECTION_URI)
        return cls._client

    @classmethod
    def db(cls, db_name: str = settings.MONGO_DB_NAME):
        """
        Si no se pasa db_name, usa el que haya en settings por defecto.
        """
        name = db_name
        return cls.client()[name]
