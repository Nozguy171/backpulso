# app/routes/calls.py
from datetime import datetime, timedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt
from sqlalchemy import and_, func
from flask_jwt_extended import get_jwt_identity
from ..extensions import db
from ..models import CallReminder, Prospect, User

calls_bp = Blueprint("calls", __name__)

def _call_to_dict(c: CallReminder, p: Prospect = None, u: User = None):
    return {
        "id": c.id,
        "fecha_hora": c.fecha_hora.isoformat() + "Z",
        "observaciones": c.observaciones,
        "estado": c.estado,
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
    estado = request.args.get("estado")
    from_ = request.args.get("from")  # "2026-01-01"
    to_ = request.args.get("to")      # "2026-02-01"

    q = (
        db.session.query(
            func.date(CallReminder.fecha_hora).label("day"),
            func.count(CallReminder.id).label("count"),
        )
        .filter(CallReminder.tenant_id == tenant_id)
        .group_by(func.date(CallReminder.fecha_hora))
        .order_by(func.date(CallReminder.fecha_hora).asc())
    )
    q = q.filter(CallReminder.tenant_id == tenant_id)
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

def _get_effective_user_id_for_calls(claims, actor_user_id: int) -> int:
    # misma lógica que prospects.py (acting-as)
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")

    acting_as = request.headers.get("X-Acting-As-User")
    if not acting_as or role != "leader":
        return actor_user_id

    try:
        acting_as_id = int(acting_as)
    except Exception:
        return actor_user_id

    u = User.query.filter_by(id=acting_as_id, tenant_id=tenant_id).first()
    return acting_as_id if u else actor_user_id


@calls_bp.post("/<int:call_id>/reagendar")
@jwt_required()
def reagendar_llamada(call_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    actor_user_id = int(get_jwt_identity())
    effective_user_id = _get_effective_user_id_for_calls(claims, actor_user_id)

    data = request.get_json() or {}
    fecha = (data.get("fecha") or "").strip()  # YYYY-MM-DD
    hora = (data.get("hora") or "").strip()    # HH:MM
    obs = (data.get("observaciones") or "").strip() or None

    if not fecha or not hora:
        return {"message": "fecha y hora son obligatorias"}, 400

    row = (
        db.session.query(CallReminder, Prospect)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.id == call_id, CallReminder.tenant_id == tenant_id)
        .first()
    )
    if not row:
        return {"message": "Llamada no encontrada"}, 404

    call, prospect = row

    # permisos: colaborador solo si el prospecto es suyo
    if role == "collaborator" and prospect.assigned_to_user_id != effective_user_id:
        return {"message": "No tienes permiso"}, 403

    # marcamos la actual como reagendada (para que ya no salga en pendientes)
    if call.estado == "pendiente":
        call.estado = "reagendada"
    else:
        # si ya no está pendiente igual puedes reagendar, pero no tiene sentido duplicar
        call.estado = "reagendada"

    # creamos la nueva llamada
    fecha_hora = datetime.fromisoformat(f"{fecha}T{hora}")
    new_call = CallReminder(
        tenant_id=tenant_id,
        prospect_id=call.prospect_id,
        created_by_user_id=effective_user_id,
        fecha_hora=fecha_hora,
        observaciones=obs,
        estado="pendiente",
    )
    db.session.add(new_call)
    db.session.commit()

    return {
        "ok": True,
        "old_call_id": call.id,
        "new_call": _call_to_dict(new_call, prospect, None),
    }, 200


@calls_bp.post("/<int:call_id>/cancelar")
@jwt_required()
def cancelar_llamada(call_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    actor_user_id = int(get_jwt_identity())
    effective_user_id = _get_effective_user_id_for_calls(claims, actor_user_id)

    data = request.get_json() or {}
    motivo = (data.get("motivo") or "").strip() or None

    row = (
        db.session.query(CallReminder, Prospect)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.id == call_id, CallReminder.tenant_id == tenant_id)
        .first()
    )
    if not row:
        return {"message": "Llamada no encontrada"}, 404

    call, prospect = row

    if role == "collaborator" and prospect.assigned_to_user_id != effective_user_id:
        return {"message": "No tienes permiso"}, 403

    call.estado = "cancelada"
    if motivo:
        call.observaciones = (call.observaciones or "").strip()
        call.observaciones = (call.observaciones + ("\n" if call.observaciones else "") + f"Cancelada: {motivo}")

    db.session.commit()
    return {"ok": True, "call": _call_to_dict(call, prospect, None)}, 200


@calls_bp.post("/<int:call_id>/marcar-hecha")
@jwt_required()
def marcar_llamada_hecha(call_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")
    actor_user_id = int(get_jwt_identity())
    effective_user_id = _get_effective_user_id_for_calls(claims, actor_user_id)

    data = request.get_json() or {}
    obs = (data.get("observaciones") or "").strip() or None

    row = (
        db.session.query(CallReminder, Prospect)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.id == call_id, CallReminder.tenant_id == tenant_id)
        .first()
    )
    if not row:
        return {"message": "Llamada no encontrada"}, 404

    call, prospect = row

    if role == "collaborator" and prospect.assigned_to_user_id != effective_user_id:
        return {"message": "No tienes permiso"}, 403

    call.estado = "hecha"
    if obs:
        call.observaciones = (call.observaciones or "").strip()
        call.observaciones = (call.observaciones + ("\n" if call.observaciones else "") + obs)

    db.session.commit()
    return {"ok": True, "call": _call_to_dict(call, prospect, None)}, 200