# app/routes/appointments.py
from datetime import datetime, date, timedelta
from flask import Blueprint, request
from sqlalchemy import and_
from sqlalchemy import func
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from ..extensions import db
from ..models import Appointment, Prospect, User
from ..utils.visibility import get_visible_user_id

appointments_bp = Blueprint("appointments", __name__)

def _appt_to_dict(a: Appointment):
    return {
        "id": a.id,
        "fecha_hora": a.fecha_hora.isoformat() + "Z",
        "ubicacion": a.ubicacion,
        "observaciones": a.observaciones,
        "estado": a.estado,
        "estado_detalle": a.estado_detalle,
        "resolved_at": a.resolved_at.isoformat() + "Z" if a.resolved_at else None,
        "created_at": a.created_at.isoformat() + "Z" if getattr(a, "created_at", None) else None,
        "prospect": {
            "id": a.prospect.id,
            "nombre": a.prospect.nombre,
            "numero": a.prospect.numero,
        } if a.prospect else None,
        "user": {
            "id": a.created_by_user_id,
            "email": a.created_by_user.email,
        } if getattr(a, "created_by_user", None) else {"id": a.created_by_user_id},
    }
@appointments_bp.get("/")
@jwt_required()
def listar_citas():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))
    # filtros
    day = request.args.get("day")        # "2026-01-13"
    from_ = request.args.get("from")     # ISO o "YYYY-MM-DD"
    to_ = request.args.get("to")         # ISO o "YYYY-MM-DD"
    limit = int(request.args.get("limit") or 200)
    estado = request.args.get("estado")  # opcional: programada/cancelada/etc

    q = (
        Appointment.query
        .filter(Appointment.tenant_id == tenant_id)
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .outerjoin(User, User.id == Appointment.created_by_user_id)
        .order_by(Appointment.fecha_hora.asc())
    )
    prospect_id = request.args.get("prospect_id")
    q = q.filter(Prospect.assigned_to_user_id == visible_user_id)
    if estado:
        q = q.filter(Appointment.estado == estado)
    if prospect_id:
        q = q.filter(Appointment.prospect_id == int(prospect_id))
    # day tiene prioridad
    if day:
        d = datetime.fromisoformat(day).date()
        start = datetime.combine(d, datetime.min.time())
        end = start + timedelta(days=1)
        q = q.filter(and_(Appointment.fecha_hora >= start, Appointment.fecha_hora < end))
    else:
        if from_:
            # acepta YYYY-MM-DD o ISO
            if len(from_) == 10:
                d = datetime.fromisoformat(from_).date()
                from_dt = datetime.combine(d, datetime.min.time())
            else:
                from_dt = datetime.fromisoformat(from_.replace("Z", ""))
            q = q.filter(Appointment.fecha_hora >= from_dt)

        if to_:
            if len(to_) == 10:
                d = datetime.fromisoformat(to_).date()
                to_dt = datetime.combine(d, datetime.min.time()) + timedelta(days=1)
            else:
                to_dt = datetime.fromisoformat(to_.replace("Z", ""))
            q = q.filter(Appointment.fecha_hora < to_dt)

    rows = q.limit(limit).all()
    return {"citas": [_appt_to_dict(a) for a in rows]}, 200


@appointments_bp.get("/days")
@jwt_required()
def dias_con_citas():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    from_ = request.args.get("from")
    to_ = request.args.get("to")
    estado = request.args.get("estado")

    q = (
        db.session.query(
            func.date(Appointment.fecha_hora).label("day"),
            func.count(Appointment.id).label("count"),
        )
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .filter(Appointment.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
    )

    if estado:
        q = q.filter(Appointment.estado == estado)

    q = q.group_by(func.date(Appointment.fecha_hora)).order_by(func.date(Appointment.fecha_hora).asc())

    if from_:
        d = datetime.fromisoformat(from_).date()
        q = q.filter(Appointment.fecha_hora >= datetime.combine(d, datetime.min.time()))
    if to_:
        d = datetime.fromisoformat(to_).date()
        q = q.filter(Appointment.fecha_hora < datetime.combine(d, datetime.min.time()) + timedelta(days=1))

    rows = q.all()

    return {
        "days": [{"day": str(r.day), "count": int(r.count)} for r in rows]
    }, 200