from datetime import datetime, timedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

from ..extensions import db
from ..models import InviteLink, User, Tenant

invites_bp = Blueprint("invites", __name__)

def _require_leader():
    claims = get_jwt()
    if claims.get("role") != "leader":
        return {"message": "Solo líder"}, 403
    return None

@invites_bp.post("/")
@jwt_required()
def create_invite():
    err = _require_leader()
    if err: return err

    leader_id = int(get_jwt_identity())
    tenant_id = get_jwt().get("tenant_id")

    tenant = Tenant.query.get(tenant_id)
    if not tenant:
        return {"message": "Tenant inválido"}, 400

    current_collabs = User.query.filter_by(tenant_id=tenant_id, role="collaborator").count()

    remaining = tenant.collaborator_limit - current_collabs
    if remaining <= 0:
        return {"message": "Límite de colaboradores alcanzado"}, 409

    token = InviteLink.new_token()
    inv = InviteLink(
        tenant_id=tenant_id,
        created_by_user_id=leader_id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=1),
        max_uses=remaining,   # ✅ aquí
        uses=0,
    )
    db.session.add(inv)
    db.session.commit()

    return {
        "invite": {
            "token": inv.token,
            "expires_at": inv.expires_at.isoformat() + "Z",
            "max_uses": inv.max_uses,
            "uses": inv.uses,
        }
    }, 201

@invites_bp.get("/<string:token>")
def validate_invite(token: str):
    inv = InviteLink.query.filter_by(token=token).first()
    if not inv:
        return {"valid": False, "message": "Invitación inválida"}, 404
    if datetime.utcnow() > inv.expires_at:
        return {"valid": False, "message": "Invitación expirada"}, 410
    if inv.uses >= inv.max_uses:
        return {"valid": False, "message": "Invitación ya no disponible"}, 409

    return {"valid": True, "expires_at": inv.expires_at.isoformat() + "Z"}, 200