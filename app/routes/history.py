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

    q = (request.args.get("q") or "").strip()
    limit = int(request.args.get("limit") or 50)

    Actor = aliased(User)
    Effective = aliased(User)

    actor_user_id = request.args.get("actor_user_id")
    effective_user_id = request.args.get("effective_user_id")

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
            "created_at": h.created_at.isoformat(),
            "prospect": {
                "id": p.id,
                "nombre": p.nombre,
                "numero": p.numero,
                "forma_obtencion_tipo": p.forma_obtencion_tipo,
                "forma_obtencion": p.forma_obtencion,
            },

            "user": effective_payload,

            "actor": actor_payload,
            "effective": effective_payload,
        })

    return {"historial": historial}, 200

@history_bp.get("/prospects")
@jwt_required()
def listar_prospectos_historial():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    q = (request.args.get("q") or "").strip()
    limit = int(request.args.get("limit") or 2000)

    query = (
        Prospect.query
        .filter(Prospect.tenant_id == tenant_id)
        .order_by(Prospect.created_at.desc())
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Prospect.nombre.ilike(like),
                Prospect.numero.ilike(like),
            )
        )

    rows = query.limit(limit).all()

    return {
        "prospectos": [
            {
                "id": p.id,
                "nombre": p.nombre,
                "numero": p.numero,
                "estado": p.estado,
                "observaciones": p.observaciones,
                "forma_obtencion_tipo": p.forma_obtencion_tipo,
                "forma_obtencion": p.forma_obtencion,
            }
            for p in rows
        ]
    }, 200

@history_bp.get("/prospects/<int:prospect_id>")
@jwt_required()
def ver_prospecto_historial_global(prospect_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    prospect = Prospect.query.filter_by(id=prospect_id, tenant_id=tenant_id).first()
    if not prospect:
        return {"message": "Prospecto no encontrado"}, 404

    Actor = aliased(User)
    Effective = aliased(User)

    rows = (
        db.session.query(ProspectHistory, Actor, Effective)
        .filter(
            ProspectHistory.tenant_id == tenant_id,
            ProspectHistory.prospect_id == prospect_id,
        )
        .outerjoin(Actor, Actor.id == ProspectHistory.actor_user_id)
        .outerjoin(Effective, Effective.id == ProspectHistory.effective_user_id)
        .order_by(ProspectHistory.created_at.desc())
        .all()
    )

    historial = []
    for (h, actor_u, eff_u) in rows:
        actor_payload = {"id": h.actor_user_id, "email": actor_u.email if actor_u else None}
        effective_payload = {"id": h.effective_user_id, "email": eff_u.email if eff_u else None}

        historial.append({
            "id": h.id,
            "accion": h.accion,
            "created_at": h.created_at.isoformat(),
            "de_estado": h.de_estado,
            "a_estado": h.a_estado,
            "detalle": h.detalle,
            "user": effective_payload,
            "actor": actor_payload,
            "effective": effective_payload,
        })

    return {
        "prospect": {
            "id": prospect.id,
            "nombre": prospect.nombre,
            "numero": prospect.numero,
            "observaciones": prospect.observaciones,
            "estado": prospect.estado,
            "created_at": prospect.created_at.isoformat(),
            "forma_obtencion_tipo": prospect.forma_obtencion_tipo,
            "forma_obtencion": prospect.forma_obtencion,
        },
        "historial": historial,
    }, 200