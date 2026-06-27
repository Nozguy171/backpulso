from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from ..models import User, Tenant
from ..extensions import db
users_bp = Blueprint("users", __name__)

ALLOWED_THEMES = {"royal-emerald", "royal-amethyst", "royal-sapphire", "royal-ivory"}

def _require_leader_or_admin():
    role = (get_jwt() or {}).get("role")
    if role not in ("leader", "admin"):
        return {"message": "Esta sección es solo para líder/admin"}, 403
    return None

@users_bp.get("/")
@jwt_required()
def listar_usuarios():
    tenant_id = get_jwt().get("tenant_id")
    users = User.query.filter_by(tenant_id=tenant_id).order_by(User.email.asc()).all()
    return {"users": [{"id": u.id, "email": u.email, "role": u.role} for u in users]}, 200

@users_bp.get("/me")
@jwt_required()
def me():
    user_id = int(get_jwt_identity())
    u = User.query.get(user_id)
    if not u:
        return {"message": "Usuario no encontrado"}, 404

    return {
        "user": {
            "id": u.id,
            "email": u.email,
            "tenant_id": u.tenant_id,
            "role": u.role,
            "theme": u.theme or "royal-sapphire",
            "is_platform_admin": bool(u.is_platform_admin),
        }
    }, 200

@users_bp.patch("/me/settings")
@jwt_required()
def update_me_settings():
    user_id = int(get_jwt_identity())
    u = User.query.get(user_id)
    if not u:
        return {"message": "Usuario no encontrado"}, 404

    data = request.get_json() or {}
    theme = data.get("theme")
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    confirm_password = data.get("confirm_password") or ""

    if theme is not None:
        if theme not in ALLOWED_THEMES:
            return {"message": "Estilo inválido"}, 400
        u.theme = theme

    if new_password or confirm_password or current_password:
        if not current_password or not u.check_password(current_password):
            return {"message": "Contraseña actual inválida"}, 400
        if not new_password or new_password != confirm_password:
            return {"message": "Las contraseñas no coinciden"}, 400
        u.set_password(new_password)

    db.session.commit()
    return {"ok": True, "user": {"id": u.id, "email": u.email, "role": u.role, "theme": u.theme}}, 200

@users_bp.get("/limits")
@jwt_required()
def limits():
    err = _require_leader_or_admin()
    if err: return err

    tenant_id = get_jwt().get("tenant_id")
    tenant = Tenant.query.get(tenant_id)
    if not tenant:
        return {"message": "Tenant inválido"}, 400

    current_collabs = User.query.filter_by(tenant_id=tenant_id, role="collaborator").count()
    return {
        "limits": {
            "plan": tenant.plan,
            "collaborator_limit": tenant.collaborator_limit,
            "collaborators_used": current_collabs,
            "collaborators_remaining": max(0, tenant.collaborator_limit - current_collabs),
        }
    }, 200

@users_bp.get("/collaborators")
@jwt_required()
def collaborators():
    err = _require_leader_or_admin()
    if err: return err

    tenant_id = get_jwt().get("tenant_id")
    limit = int(request.args.get("limit", "500"))

    collabs = (
        User.query
        .filter_by(tenant_id=tenant_id, role="collaborator")
        .order_by(User.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "colaboradores": [
            {
                "id": u.id,
                "email": u.email,
                "role": u.role,
                "created_at": u.created_at.isoformat(),
            }
            for u in collabs
        ]
    }, 200

@users_bp.post("/acting-as")
@jwt_required()
def acting_as():
    err = _require_leader_or_admin()
    if err: return err

    tenant_id = get_jwt().get("tenant_id")
    data = request.get_json() or {}
    target_id = int(data.get("user_id") or 0)

    if not target_id:
        return {"message": "user_id requerido"}, 400

    u = User.query.get(target_id)
    if not u or u.tenant_id != tenant_id:
        return {"message": "Usuario inválido"}, 404


    return {
        "ok": True,
        "acting_as_user_id": u.id,
        "email": u.email,
        "role": u.role,
    }, 200
