import os
import time
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db

app = create_app()

def init_db_with_retry(max_retries: int = 20, delay_seconds: int = 2):
    attempt = 1
    while attempt <= max_retries:
        try:
            print(f"[INIT-DB] Intento {attempt}/{max_retries} de crear tablas...")
            with app.app_context():
                db.create_all()
            print("[INIT-DB] Tablas creadas correctamente ✅")
            return
        except OperationalError as e:
            print(f"[INIT-DB] DB no está lista todavía ({e}). Esperando {delay_seconds}s...")
            time.sleep(delay_seconds)
            attempt += 1

    raise RuntimeError("[INIT-DB] No se pudo conectar a la DB después de varios intentos 💀")

try:
    init_db_with_retry()
except Exception as e:
    print(str(e))
    # no matamos el proceso aquí; gunicorn podría reiniciar