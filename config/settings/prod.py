import os

from .base import *

DEBUG = False

SECRET_KEY = os.environ["SECRET_KEY"]

MONGO_CONNECTION_URI = os.environ["MONGO_CONNECTION_URI"]

MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "autoppia")
