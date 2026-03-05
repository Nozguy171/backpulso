from flask import Blueprint, request
from flask_jwt_extended import create_access_token
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from ..models import InviteLink
from ..extensions import db
from ..models import User, Tenant

auth_bp = Blueprint("auth", __name__)


def slugify_from_email(email: str) -> str:
    return email.split("@")[0].replace(".", "-").replace("_", "-").lower()

@auth_bp.post("/signup")
def signup():
    data = request.get_json() or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not email or not password or not confirm_password:
        return {"message": "Faltan campos"}, 400
    if password != confirm_password:
        return {"message": "Las contraseñas no coinciden"}, 400
    if User.query.filter_by(email=email).first():
        return {"message": "Ya existe una cuenta con ese correo"}, 400

    tenant_name = email.split("@")[0]
    slug = slugify_from_email(email)

    PLAN_LIMITS = {
        "starter": 1,
        "pro": 5,
        "team": 20,
    }
    plan = "pro"  # por ahora
    tenant = Tenant(
        name=tenant_name,
        slug=slug,
        plan=plan,
        collaborator_limit=PLAN_LIMITS.get(plan, 1),
    )
    user = User(email=email, tenant=tenant, role="leader")
    user.set_password(password)

    db.session.add_all([tenant, user])
    db.session.commit()

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "tenant_id": tenant.id,
            "role": user.role,
        },
    )

    return {
        "message": "Cuenta creada correctamente",
        "access_token": access_token,
        "user": {"id": user.id, "email": user.email, "tenant_id": user.tenant_id, "role": user.role},
    }, 201

@auth_bp.post("/login")
def login():
    data = request.get_json() or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return {"message": "Correo y contraseña obligatorios"}, 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return {"message": "Credenciales inválidas"}, 401

    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "tenant_id": user.tenant_id,
            "role": user.role,
        },
    )

    return {
        "message": "Login exitoso",
        "access_token": access_token,
        "user": {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "role": user.role,
        },
    }, 200


@auth_bp.post("/signup-collaborator")
def signup_collaborator():
    data = request.get_json() or {}

    token = (data.get("token") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not token or not email or not password or not confirm_password:
        return {"message": "Faltan campos"}, 400
    if password != confirm_password:
        return {"message": "Las contraseñas no coinciden"}, 400
    if User.query.filter_by(email=email).first():
        return {"message": "Ya existe una cuenta con ese correo"}, 400

    inv = InviteLink.query.filter_by(token=token).first()
    if not inv:
        return {"message": "Invitación inválida"}, 404
    if datetime.utcnow() > inv.expires_at:
        return {"message": "Invitación expirada"}, 410
    if inv.uses >= inv.max_uses:
        return {"message": "Invitación ya no disponible"}, 409

    tenant = Tenant.query.get(inv.tenant_id)
    if not tenant:
        return {"message": "Tenant inválido"}, 400

    # ✅ validar límite (solo cuenta colaboradores, no líderes)
    current_collabs = User.query.filter_by(tenant_id=tenant.id, role="collaborator").count()
    if current_collabs >= tenant.collaborator_limit:
        return {"message": "Límite de colaboradores alcanzado"}, 409

    user = User(email=email, tenant_id=tenant.id, role="collaborator")
    user.set_password(password)

    inv.uses += 1

    db.session.add(user)
    db.session.commit()

    # opcional: login automático
    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={"tenant_id": user.tenant_id, "role": user.role},
    )

    return {
        "message": "Colaborador creado correctamente",
        "access_token": access_token,
        "user": {"id": user.id, "email": user.email, "tenant_id": user.tenant_id, "role": user.role},
    }, 201