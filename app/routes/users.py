from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from ..models import User, Tenant
users_bp = Blueprint("users", __name__)

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
        }
    }, 200

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
                "created_at": u.created_at.isoformat() + "Z",
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