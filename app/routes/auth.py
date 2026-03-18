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
    nombre_completo = (data.get("email") or "").strip()
    username = (data.get("username") or "").strip().lower()
    numero_telefonico = (data.get("numero_telefonico") or "").strip()
    password = data.get("password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not nombre_completo:
        return {"message": "El nombre completo es obligatorio"}, 400

    if not username:
        return {"message": "El username es obligatorio"}, 400

    if not numero_telefonico:
        return {"message": "El número telefónico es obligatorio"}, 400

    if not numero_telefonico.isdigit() or len(numero_telefonico) != 10:
        return {"message": "El número telefónico debe tener exactamente 10 dígitos"}, 400

    if not password or not confirm_password:
        return {"message": "Contraseña y confirmación obligatorias"}, 400

    if password != confirm_password:
        return {"message": "Las contraseñas no coinciden"}, 400

    if User.query.filter_by(username=username).first():
        return {"message": "Ese username ya está en uso"}, 409

    if User.query.filter_by(numero_telefonico=numero_telefonico).first():
        return {"message": "Ese número telefónico ya está en uso"}, 409

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

    db.session.add(tenant)
    db.session.flush()

    user = User(
        tenant_id=tenant.id,
        email=email,
        username=username,
        numero_telefonico=numero_telefonico,
        role="leader",
    )

    user.set_password(password)

    db.session.add(user)
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

    login = (data.get("login") or "").strip()
    password = data.get("password") or ""

    if not login or not password:
        return {"message": "Usuario y contraseña son obligatorios"}, 400

    user = User.query.filter(
        (User.username == login.lower()) | (User.numero_telefonico == login)
    ).first()

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
    username = (data.get("username") or "").strip().lower()
    numero_telefonico = (data.get("numero_telefonico") or "").strip()
    token = (data.get("token") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not username:
        return {"message": "El username es obligatorio"}, 400

    if not numero_telefonico:
        return {"message": "El número telefónico es obligatorio"}, 400

    if not numero_telefonico.isdigit() or len(numero_telefonico) != 10:
        return {"message": "El número telefónico debe tener exactamente 10 dígitos"}, 400


    if not token or not email or not password or not confirm_password:
        return {"message": "Faltan campos"}, 400
    if password != confirm_password:
        return {"message": "Las contraseñas no coinciden"}, 400
    if User.query.filter_by(email=email).first():
        return {"message": "Ya existe una cuenta con ese correo"}, 400

    inv = InviteLink.query.filter_by(token=token).first()
    if not inv:
        return {"message": "Invitación inválida"}, 404
    if datetime.now() > inv.expires_at:
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

    user = User(
        tenant_id=tenant.id,
        email=email,
        username=username,
        numero_telefonico=numero_telefonico,
        role="collaborator",
    )
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