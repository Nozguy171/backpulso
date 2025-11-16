import time
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db

app = create_app()


def init_db_with_retry(max_retries: int = 10, delay_seconds: int = 2):
    """Intenta hacer db.create_all() con reintentos mientras arranca Postgres."""
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

    print("[INIT-DB] No se pudo conectar a la DB después de varios intentos 💀")


if __name__ == "__main__":
    init_db_with_retry()

    app.run(host="0.0.0.0", port=8000, debug=True)
