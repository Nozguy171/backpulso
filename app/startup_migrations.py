import os
import time

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from .extensions import db


MIGRATIONS = (
    "ALTER TABLE prospects ADD COLUMN IF NOT EXISTS numero_encuesta VARCHAR(80)",
    "ALTER TABLE prospects ALTER COLUMN numero_encuesta DROP NOT NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS theme VARCHAR(40) NOT NULL DEFAULT 'royal-emerald'",
    """
    UPDATE users
    SET theme = 'royal-emerald'
    WHERE theme IS NULL
       OR theme NOT IN ('royal-emerald', 'royal-amethyst', 'royal-sapphire', 'royal-ivory')
    """,
    """
    DELETE FROM call_reminders
    WHERE observaciones = 'Seguimiento mensual (mantenimiento / nuevas citas)'
      AND estado = 'cancelada'
    """,
)


def run_startup_migrations(app):
    if os.getenv("PULSO_AUTO_MIGRATE", "1") == "0":
        return

    max_retries = int(os.getenv("PULSO_MIGRATION_RETRIES", "30"))
    delay_seconds = int(os.getenv("PULSO_MIGRATION_RETRY_SECONDS", "2"))

    for attempt in range(1, max_retries + 1):
        try:
            _run_once(app)
            return
        except SQLAlchemyError as e:
            if attempt == max_retries:
                raise
            print(f"[MIGRATIONS] DB no lista ({attempt}/{max_retries}): {e}")
            time.sleep(delay_seconds)


def _run_once(app):
    with app.app_context():
        try:
            db.session.execute(text("SELECT pg_advisory_xact_lock(80520240619)"))
            for sql in MIGRATIONS:
                db.session.execute(text(sql))
            db.session.commit()
            print("[MIGRATIONS] ok")
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()
