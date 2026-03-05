from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from sqlalchemy import or_
from sqlalchemy.orm import aliased

from ..models import ProspectHistory, Prospect, User
from ..extensions import db

history_bp = Blueprint("history", __name__)

@history_bp.get("/")
@jwt_required()
def listar_historial_general():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    me = int(get_jwt_identity())

    q = (request.args.get("q") or "").strip()
    limit = int(request.args.get("limit") or 50)

    # ✅ aliases para traer emails sin N+1
    Actor = aliased(User)
    Effective = aliased(User)

    # filtros opcionales (acepta ambos nombres)
    actor_user_id = request.args.get("actor_user_id")
    effective_user_id = request.args.get("effective_user_id")

    # ✅ tu UI manda user_id, lo tratamos como effective_user_id (quién “realizó”)
    user_id = request.args.get("user_id")
    if user_id and not effective_user_id:
        effective_user_id = user_id

    prospect_id = request.args.get("prospect_id")

    query = (
        db.session.query(ProspectHistory, Prospect, Actor, Effective)
        .filter(ProspectHistory.tenant_id == tenant_id)
        .join(Prospect, Prospect.id == ProspectHistory.prospect_id)
        .outerjoin(Actor, Actor.id == ProspectHistory.actor_user_id)
        .outerjoin(Effective, Effective.id == ProspectHistory.effective_user_id)
        .order_by(ProspectHistory.created_at.desc())
    )

    if role == "collaborator":
        query = query.filter(Prospect.assigned_to_user_id == me)

    if actor_user_id:
        query = query.filter(ProspectHistory.actor_user_id == int(actor_user_id))

    if effective_user_id:
        query = query.filter(ProspectHistory.effective_user_id == int(effective_user_id))

    if prospect_id:
        query = query.filter(ProspectHistory.prospect_id == int(prospect_id))

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                ProspectHistory.accion.ilike(like),
                ProspectHistory.detalle.ilike(like),
                Prospect.nombre.ilike(like),
            )
        )

    rows = query.limit(limit).all()

    historial = []
    for (h, p, actor_u, eff_u) in rows:
        actor_payload = {"id": h.actor_user_id, "email": actor_u.email if actor_u else None}
        effective_payload = {"id": h.effective_user_id, "email": eff_u.email if eff_u else None}

        historial.append({
            "id": h.id,
            "accion": h.accion,
            "de_estado": h.de_estado,
            "a_estado": h.a_estado,
            "detalle": h.detalle,
            "created_at": h.created_at.isoformat() + "Z",
            "prospect": {"id": p.id, "nombre": p.nombre},

            "user": effective_payload,

            "actor": actor_payload,
            "effective": effective_payload,
        })

    return {"historial": historial}, 200