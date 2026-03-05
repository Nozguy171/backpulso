from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db
from datetime import datetime
from .extensions import db
from sqlalchemy import Numeric
import secrets

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(150), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    plan = db.Column(db.String(50), nullable=False, default="starter")
    collaborator_limit = db.Column(db.Integer, nullable=False, default=1)
    users = db.relationship("User", backref="tenant", lazy=True)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)

    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(30), nullable=False, default="leader")

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Prospect(db.Model):
    __tablename__ = "prospects"

    __table_args__ = (
        db.Index("ix_prospects_tenant_estado", "tenant_id", "estado"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    venta_monto_sin_iva = db.Column(Numeric(12, 2), nullable=True)
    venta_fecha = db.Column(db.DateTime, nullable=True)
    nombre = db.Column(db.String(200), nullable=False)
    numero = db.Column(db.String(50), nullable=False)
    observaciones = db.Column(db.Text, nullable=True)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rechazo_motivo = db.Column(db.Text, nullable=True)
    rechazo_at = db.Column(db.DateTime, nullable=True)
    rechazo_count = db.Column(db.Integer, nullable=False, default=0)
    recomendado_por_id = db.Column(db.Integer, db.ForeignKey("prospects.id"), nullable=True)
    recomendado_por = db.relationship(
        "Prospect",
        remote_side=[id],
        backref="recomendados",
        lazy=True,
    )

    estado = db.Column(db.String(50), nullable=False, default="pendiente", index=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ProspectHistory(db.Model):
    __tablename__ = "prospect_history"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    prospect_id = db.Column(db.Integer, db.ForeignKey("prospects.id"), nullable=False)

    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    effective_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    accion = db.Column(db.String(100), nullable=False)
    de_estado = db.Column(db.String(50), nullable=True)
    a_estado = db.Column(db.String(50), nullable=True)
    detalle = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    prospect = db.relationship("Prospect", backref="historial", lazy=True)
    actor_user = db.relationship("User", foreign_keys=[actor_user_id], lazy=True)
    effective_user = db.relationship("User", foreign_keys=[effective_user_id], lazy=True)


class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    prospect_id = db.Column(db.Integer, db.ForeignKey("prospects.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    fecha_hora = db.Column(db.DateTime, nullable=False)
    ubicacion = db.Column(db.String(255), nullable=False)
    observaciones = db.Column(db.Text, nullable=True)

    estado = db.Column(db.String(50), default="programada", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    prospect = db.relationship("Prospect", lazy="joined")
    created_by_user = db.relationship("User", lazy="joined")

class CallReminder(db.Model):
    __tablename__ = "call_reminders"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    prospect_id = db.Column(db.Integer, db.ForeignKey("prospects.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    fecha_hora = db.Column(db.DateTime, nullable=False)
    observaciones = db.Column(db.Text, nullable=True)

    estado = db.Column(db.String(50), default="pendiente", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
class InviteLink(db.Model):
    __tablename__ = "invite_links"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    token = db.Column(db.String(80), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)

    max_uses = db.Column(db.Integer, nullable=False, default=999999)  # por si luego quieres 1 solo uso
    uses = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)