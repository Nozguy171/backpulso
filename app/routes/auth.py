from flask import Blueprint, request
from flask_jwt_extended import create_access_token
from sqlalchemy.exc import IntegrityError

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

    tenant = Tenant(name=tenant_name, slug=slug)
    user = User(email=email, tenant=tenant, is_admin=True)
    user.set_password(password)

    db.session.add(tenant)
    db.session.add(user)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return {"message": "Error creando la cuenta"}, 500

    # 🔴 ANTES: identity era un dict -> truena "Subject must be a string"
    # ✅ AHORA: identity es SOLO el id (string) y el resto va en additional_claims
    access_token = create_access_token(
        identity=str(user.id),
        additional_claims={
            "tenant_id": tenant.id,
            "is_admin": True,
        },
    )

    return {
        "message": "Cuenta creada correctamente",
        "access_token": access_token,
        "user": {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "is_admin": user.is_admin,
        },
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
        identity=str(user.id),  # 👈 string
        additional_claims={
            "tenant_id": user.tenant_id,
            "is_admin": user.is_admin,
        },
    )

    return {
        "message": "Login exitoso",
        "access_token": access_token,
        "user": {
            "id": user.id,
            "email": user.email,
            "tenant_id": user.tenant_id,
            "is_admin": user.is_admin,
        },
    }, 200
