import os
from datetime import timedelta

class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        # fallback local por si un día lo corres fuera de docker
        "postgresql://pulso_user:pulso_pass@localhost:5445/pulso_crm_db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-pulso")
    DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=8)
