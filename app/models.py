from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db
from datetime import datetime
from .extensions import db

class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(150), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="tenant", lazy=True)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)

    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Prospect(db.Model):
    __tablename__ = "prospects"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    nombre = db.Column(db.String(200), nullable=False)
    numero = db.Column(db.String(50), nullable=False)
    observaciones = db.Column(db.Text, nullable=True)

    # prospecto que lo recomendó (opcional)
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
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # ejemplo: create_prospect, sin_respuesta, agendar_cita, programar_llamada, rechazado, observaciones
    accion = db.Column(db.String(100), nullable=False)
    de_estado = db.Column(db.String(50), nullable=True)
    a_estado = db.Column(db.String(50), nullable=True)

    detalle = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    prospect = db.relationship("Prospect", backref="historial", lazy=True)


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