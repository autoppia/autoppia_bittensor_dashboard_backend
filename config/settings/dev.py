import os
from dotenv import load_dotenv
load_dotenv()

from .base import *

DEBUG = True

SECRET_KEY = os.environ["SECRET_KEY"]

MONGO_CONNECTION_URI = os.environ["MONGO_CONNECTION_URI"]