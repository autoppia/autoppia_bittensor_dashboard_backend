import os

from .base import *

DEBUG = False

SECRET_KEY = os.environ["SECRET_KEY"]

MONGO_CONNECTION_URI = os.environ["MONGO_CONNECTION_URI"]
