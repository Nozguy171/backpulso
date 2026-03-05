import os
from datetime import timedelta

class Config:
    SQLALCHEMY_DATABASE_URI = os.environ["DATABASE_URL"]
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=int(os.getenv("JWT_DAYS", "2")))