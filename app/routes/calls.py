# app/routes/calls.py
from datetime import datetime, timedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt
from sqlalchemy import and_, func
from flask_jwt_extended import get_jwt_identity
from ..extensions import db
from ..models import CallReminder, Prospect, User
from ..utils.visibility import get_visible_user_id

calls_bp = Blueprint("calls", __name__)

def _fmt_dt(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")

def _call_to_dict(c: CallReminder, p: Prospect = None, u: User = None):
    return {
        "id": c.id,
        "fecha_hora": c.fecha_hora.isoformat() + "Z",
        "observaciones": c.observaciones,
        "estado": c.estado,
        "estado_detalle": c.estado_detalle,
        "resolved_at": c.resolved_at.isoformat() + "Z" if c.resolved_at else None,
        "updated_at": c.updated_at.isoformat() + "Z" if getattr(c, "updated_at", None) else None,
        "created_at": c.created_at.isoformat() + "Z" if getattr(c, "created_at", None) else None,
        "prospect": {
            "id": p.id,
            "nombre": p.nombre,
            "numero": p.numero,
        } if p else None,
        "user": {
            "id": c.created_by_user_id,
            "email": u.email,
        } if u else {"id": c.created_by_user_id},
    }

@calls_bp.get("/")
@jwt_required()
def listar_llamadas():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    day = request.args.get("day")        # "2026-01-13"
    from_ = request.args.get("from")     # "YYYY-MM-DD" o ISO
    to_ = request.args.get("to")         # "YYYY-MM-DD" o ISO
    limit = int(request.args.get("limit") or 200)
    estado = request.args.get("estado")  # pendiente/hecha/cancelada etc (si lo usas)

    q = (
        db.session.query(CallReminder, Prospect, User)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .outerjoin(User, User.id == CallReminder.created_by_user_id)
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
        .order_by(CallReminder.fecha_hora.asc())
    )

    if estado:
        q = q.filter(CallReminder.estado == estado)

    if day:
        d = datetime.fromisoformat(day).date()
        start = datetime.combine(d, datetime.min.time())
        end = start + timedelta(days=1)
        q = q.filter(and_(CallReminder.fecha_hora >= start, CallReminder.fecha_hora < end))
    else:
        if from_:
            if len(from_) == 10:
                d = datetime.fromisoformat(from_).date()
                from_dt = datetime.combine(d, datetime.min.time())
            else:
                from_dt = datetime.fromisoformat(from_.replace("Z", ""))
            q = q.filter(CallReminder.fecha_hora >= from_dt)

        if to_:
            if len(to_) == 10:
                d = datetime.fromisoformat(to_).date()
                to_dt = datetime.combine(d, datetime.min.time()) + timedelta(days=1)
            else:
                to_dt = datetime.fromisoformat(to_.replace("Z", ""))
            q = q.filter(CallReminder.fecha_hora < to_dt)

    rows = q.limit(limit).all()
    return {
        "llamadas": [_call_to_dict(c, p, u) for (c, p, u) in rows]
    }, 200


@calls_bp.get("/days")
@jwt_required()
def dias_con_llamadas():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))
    estado = request.args.get("estado")
    from_ = request.args.get("from")
    to_ = request.args.get("to")

    q = (
        db.session.query(
            func.date(CallReminder.fecha_hora).label("day"),
            func.count(CallReminder.id).label("count"),
        )
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
        .group_by(func.date(CallReminder.fecha_hora))
        .order_by(func.date(CallReminder.fecha_hora).asc())
    )
    if estado:
        q = q.filter(CallReminder.estado == estado)
    if from_:
        d = datetime.fromisoformat(from_).date()
        q = q.filter(CallReminder.fecha_hora >= datetime.combine(d, datetime.min.time()))
    if to_:
        d = datetime.fromisoformat(to_).date()
        q = q.filter(CallReminder.fecha_hora < datetime.combine(d, datetime.min.time()) + timedelta(days=1))

    rows = q.all()
    return {"days": [{"day": str(r.day), "count": int(r.count)} for r in rows]}, 200


@calls_bp.post("/<int:call_id>/reagendar")
@jwt_required()
def reagendar_llamada(call_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    actor_user_id = int(get_jwt_identity())
    visible_user_id = get_visible_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    fecha = (data.get("fecha") or "").strip()
    hora = (data.get("hora") or "").strip()
    obs = (data.get("observaciones") or "").strip() or None

    if not fecha or not hora:
        return {"message": "fecha y hora son obligatorias"}, 400

    row = (
        db.session.query(CallReminder, Prospect, User)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .outerjoin(User, User.id == CallReminder.created_by_user_id)
        .filter(CallReminder.id == call_id, CallReminder.tenant_id == tenant_id)
        .first()
    )
    if not row:
        return {"message": "Llamada no encontrada"}, 404

    call, prospect, created_by_user = row

    if prospect.assigned_to_user_id != visible_user_id:
        return {"message": "Llamada no encontrada"}, 404

    fecha_hora = datetime.fromisoformat(f"{fecha}T{hora}")

    call.estado = "reagendada"
    call.estado_detalle = f"Reagendada para {_fmt_dt(fecha_hora)}"
    call.resolved_at = datetime.utcnow()

    new_call = CallReminder(
        tenant_id=tenant_id,
        prospect_id=call.prospect_id,
        created_by_user_id=visible_user_id,
        fecha_hora=fecha_hora,
        observaciones=obs,
        estado="pendiente",
        estado_detalle=None,
        resolved_at=None,
    )
    db.session.add(new_call)
    db.session.commit()

    return {
        "ok": True,
        "old_call": _call_to_dict(call, prospect, created_by_user),
        "new_call": _call_to_dict(new_call, prospect, None),
    }, 200
@calls_bp.post("/<int:call_id>/cancelar")
@jwt_required()
def cancelar_llamada(call_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    actor_user_id = int(get_jwt_identity())
    visible_user_id = get_visible_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    motivo = (data.get("motivo") or "").strip() or None

    row = (
        db.session.query(CallReminder, Prospect, User)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .outerjoin(User, User.id == CallReminder.created_by_user_id)
        .filter(CallReminder.id == call_id, CallReminder.tenant_id == tenant_id)
        .first()
    )
    if not row:
        return {"message": "Llamada no encontrada"}, 404

    call, prospect, created_by_user = row

    if prospect.assigned_to_user_id != visible_user_id:
        return {"message": "Llamada no encontrada"}, 404

    call.estado = "cancelada"
    call.estado_detalle = motivo or "Llamada cancelada"
    call.resolved_at = datetime.utcnow()

    db.session.commit()
    return {"ok": True, "call": _call_to_dict(call, prospect, created_by_user)}, 200
@calls_bp.post("/<int:call_id>/marcar-hecha")
@jwt_required()
def marcar_llamada_hecha(call_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    actor_user_id = int(get_jwt_identity())
    visible_user_id = get_visible_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    obs = (data.get("observaciones") or "").strip() or None

    row = (
        db.session.query(CallReminder, Prospect, User)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .outerjoin(User, User.id == CallReminder.created_by_user_id)
        .filter(CallReminder.id == call_id, CallReminder.tenant_id == tenant_id)
        .first()
    )
    if not row:
        return {"message": "Llamada no encontrada"}, 404

    call, prospect, created_by_user = row

    if prospect.assigned_to_user_id != visible_user_id:
        return {"message": "Llamada no encontrada"}, 404

    call.estado = "hecha"
    call.estado_detalle = obs or "Llamada realizada"
    call.resolved_at = datetime.utcnow()

    db.session.commit()
    return {"ok": True, "call": _call_to_dict(call, prospect, created_by_user)}, 200