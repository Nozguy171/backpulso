import os
import time

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from .extensions import db


MIGRATIONS = (
    "ALTER TABLE prospects ADD COLUMN IF NOT EXISTS numero_encuesta VARCHAR(80)",
    "ALTER TABLE prospects ALTER COLUMN numero_encuesta DROP NOT NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS theme VARCHAR(40) NOT NULL DEFAULT 'royal-sapphire'",
    "ALTER TABLE users ALTER COLUMN theme SET DEFAULT 'royal-sapphire'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS ubicacion_lat DOUBLE PRECISION",
    "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS ubicacion_lng DOUBLE PRECISION",
    "ALTER TABLE tenants DROP COLUMN IF EXISTS status",
    "ALTER TABLE prospect_sales ADD COLUMN IF NOT EXISTS sold_by_user_id INTEGER",
    "ALTER TABLE prospect_sales ADD COLUMN IF NOT EXISTS effective_user_id INTEGER",
    "UPDATE prospect_sales SET sold_by_user_id = created_by_user_id WHERE sold_by_user_id IS NULL",
    "UPDATE prospect_sales SET effective_user_id = sold_by_user_id WHERE effective_user_id IS NULL",
    "ALTER TABLE prospect_sales ALTER COLUMN sold_by_user_id SET NOT NULL",
    "ALTER TABLE prospect_sales ALTER COLUMN effective_user_id SET NOT NULL",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'prospect_sales_sold_by_user_id_fkey') THEN
            ALTER TABLE prospect_sales ADD CONSTRAINT prospect_sales_sold_by_user_id_fkey FOREIGN KEY (sold_by_user_id) REFERENCES users(id);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'prospect_sales_effective_user_id_fkey') THEN
            ALTER TABLE prospect_sales ADD CONSTRAINT prospect_sales_effective_user_id_fkey FOREIGN KEY (effective_user_id) REFERENCES users(id);
        END IF;
    END $$
    """,
    "CREATE INDEX IF NOT EXISTS ix_prospect_sales_sold_by_user_id ON prospect_sales (sold_by_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_prospect_sales_effective_user_id ON prospect_sales (effective_user_id)",
    """
    CREATE TABLE IF NOT EXISTS admin_audit (
        id SERIAL PRIMARY KEY,
        admin_user_id INTEGER NOT NULL REFERENCES users(id),
        action VARCHAR(100) NOT NULL,
        target_type VARCHAR(50) NOT NULL,
        target_id INTEGER NOT NULL,
        tenant_id INTEGER REFERENCES tenants(id),
        details TEXT,
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_admin_audit_created_at ON admin_audit (created_at)",
    """
    CREATE TABLE IF NOT EXISTS prospect_documents (
        id SERIAL PRIMARY KEY,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        prospect_id INTEGER NOT NULL REFERENCES prospects(id),
        uploaded_by_user_id INTEGER NOT NULL REFERENCES users(id),
        doc_type VARCHAR(60) NOT NULL,
        original_filename VARCHAR(255) NOT NULL,
        stored_path VARCHAR(500) NOT NULL,
        mime_type VARCHAR(120),
        size_bytes INTEGER NOT NULL DEFAULT 0,
        deleted_at TIMESTAMP WITHOUT TIME ZONE,
        deleted_by_user_id INTEGER REFERENCES users(id),
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_prospect_documents_tenant_id ON prospect_documents (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_prospect_documents_prospect_id ON prospect_documents (prospect_id)",
    "CREATE INDEX IF NOT EXISTS ix_prospect_documents_doc_type ON prospect_documents (doc_type)",
    "CREATE INDEX IF NOT EXISTS ix_prospect_documents_created_at ON prospect_documents (created_at)",
    """
    CREATE TABLE IF NOT EXISTS document_templates (
        id SERIAL PRIMARY KEY,
        tenant_id INTEGER NOT NULL REFERENCES tenants(id),
        uploaded_by_user_id INTEGER NOT NULL REFERENCES users(id),
        doc_type VARCHAR(60) NOT NULL DEFAULT 'formato',
        name VARCHAR(180) NOT NULL DEFAULT 'Formato',
        description TEXT,
        original_filename VARCHAR(255) NOT NULL,
        stored_path VARCHAR(500) NOT NULL,
        mime_type VARCHAR(120),
        size_bytes INTEGER NOT NULL DEFAULT 0,
        deleted_at TIMESTAMP WITHOUT TIME ZONE,
        deleted_by_user_id INTEGER REFERENCES users(id),
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "ALTER TABLE document_templates ALTER COLUMN doc_type SET DEFAULT 'formato'",
    "ALTER TABLE document_templates ADD COLUMN IF NOT EXISTS name VARCHAR(180)",
    "ALTER TABLE document_templates ADD COLUMN IF NOT EXISTS description TEXT",
    "UPDATE document_templates SET name = original_filename WHERE name IS NULL OR name = ''",
    "ALTER TABLE document_templates ALTER COLUMN name SET NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_document_templates_tenant_id ON document_templates (tenant_id)",
    "CREATE INDEX IF NOT EXISTS ix_document_templates_doc_type ON document_templates (doc_type)",
    "CREATE INDEX IF NOT EXISTS ix_document_templates_created_at ON document_templates (created_at)",
    """
    UPDATE users
    SET theme = 'royal-sapphire'
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
            db.create_all()
            for sql in MIGRATIONS:
                db.session.execute(text(sql))
            admin_login = (os.getenv("PULSO_ADMIN_LOGIN") or "").strip().lower() if os.getenv("PULSO_ADMIN_ENABLED", "0") == "1" else ""
            if admin_login:
                db.session.execute(
                    text("""
                        UPDATE users SET is_platform_admin = TRUE
                        WHERE lower(username) = :login
                           OR lower(email) = :login
                           OR numero_telefonico = :login
                    """),
                    {"login": admin_login},
                )
            db.session.commit()
            print("[MIGRATIONS] ok")
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()
